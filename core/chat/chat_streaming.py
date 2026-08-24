import json
import logging
import re
import time
from typing import Generator, Union, Dict, Any
import config
from .chat_tool_calling import strip_ui_markers, _extract_tool_images, filter_to_thinking_only, cap_tool_result_text
from .llm_providers import LLMResponse, get_generation_params
from core.event_bus import publish, Events
from core.hooks import hook_runner, HookEvent
from core.metrics import metrics as token_metrics
from core.tts.stream_pump import StreamingTTSPump

logger = logging.getLogger(__name__)


def stamp_private_if_unlocked(session_manager):
    """Vaulted chats — 'messages control the private marker': an operator
    turn in a chat while the vault is OPEN marks that chat private (one-way;
    Chat Manager owns the unmark). Skips: no/locked vault (no private mode),
    managed mode, already-private, and MODE-TAGGED chats (game/story/
    librarian/limbo belong to plugin surfaces — talking in a game while
    unlocked must not vault the game). A stamp failure logs and lets the
    turn run public (systemic-error posture; matches voice_privacy).
    Module-level (ruling RM3, 2026-08-15) so the non-streaming operator door
    (REST /api/chat, wake, body) stamps through the same code."""
    try:
        from core import prompt_vault
        vs = prompt_vault.vault_status()
        if not (vs.get('exists') and vs.get('unlocked')):
            return
        from core.settings_manager import settings as sm_settings
        if sm_settings.is_managed():
            return
        sm = session_manager
        # Name captured BEFORE the write (vault hunt R5 family): if an
        # eviction retargets the active chat mid-stamp, expected_active
        # makes the store refuse rather than mark the landing chat.
        name = sm.get_active_chat_name()
        cur = sm.get_chat_settings()
        if cur.get('private_chat') or cur.get('mode'):
            return
        if sm.update_chat_settings({'private_chat': True},
                                   expected_active=name):
            # No name in the log — it just became a secret (ruling F3).
            logger.info("[VAULT] active chat marked private — spoke while vault open")
            publish(Events.CHAT_SETTINGS_CHANGED,
                    {"chat": name, "settings": {"private_chat": True},
                     "origin": None})
    except Exception as e:
        logger.warning(f"[VAULT] talk-stamp failed — turn runs unstamped: {e}")


class StreamingChat:
    def __init__(self, main_chat):
        self.main_chat = main_chat
        self.tool_engine = main_chat.tool_engine
        self.cancel_flag = False
        self.current_stream = None
        # Voice-mute for the current message (left-button "stop TTS"): drops this
        # stream's TTS without cancelling the LLM. Read by the pump's cancel_check
        # (flush_and_close bails) + set on the pump's _skip_turn (push no-ops). NOT
        # the same as cancel_flag, which stops the LLM — TTS/STT is the left button's
        # domain; the LLM has its own Stop button.
        self.tts_stopped = False
        self.tts_pump = None
        self.ephemeral = False
        self.is_streaming = False
        # Name of the chat currently streaming — lets /api/cancel refuse to
        # cancel a DIFFERENT chat's stream (was global, cross-chat tabs
        # interfered). Full per-request streaming state is H4 architecture
        # work; this is the narrow scoping fix. H5 2026-04-22.
        self.active_chat_name = None
        # A1: the explicit chat this stream targets (None = web/active chat). When
        # set to a non-active chat (a phone call), the turn runs a per-stream brain
        # override so it reads/writes ITS chat, not the UI's active one.
        self.target_chat = None

    def _effective_enabled_tools(self):
        """A1: the tool list for THIS turn — a per-stream override's tools when the
        turn targets a non-active chat, else the global active toolset. An override
        with tools=None means that chat's toolset is 'none' → no tools this turn."""
        try:
            from core.chat.stream_brain import get_override
            o = get_override()
            if o is not None:
                return o.get("tools") or []
        except Exception:
            pass
        return self.main_chat.function_manager.enabled_tools

    def _cleanup_stream(self):
        """Safely close current stream if it exists."""
        if self.current_stream:
            try:
                self.current_stream.close()
                logger.info("[CLEANUP] Stream closed")
            except Exception as e:
                logger.warning(f"[CLEANUP] Stream close warning: {e}")
            finally:
                self.current_stream = None

    def stop_tts(self):
        """Left-button "stop TTS": mute her voice for THIS message without cancelling
        the LLM. Sets tts_stopped (pump's flush_and_close bails within ~100ms via
        cancel_check) + the pump's _skip_turn (push() stops submitting new synth).
        Both are bool writes — safe from the API thread while the stream runs in its
        own thread (no cross-thread list mutation). Idempotent."""
        self.tts_stopped = True
        p = self.tts_pump
        if p is not None:
            try:
                p._skip_turn = True
            except Exception as e:
                logger.warning(f"[STREAMING] stop_tts failed: {e}")

    def _stamp_private_if_unlocked(self):
        """Streaming-lane wrapper — see stamp_private_if_unlocked below."""
        stamp_private_if_unlocked(self.main_chat.session_manager)

    def chat_stream(self, user_input: str, prefill: str = None, skip_user_message: bool = False, images: list = None, files: list = None) -> Generator[Union[str, Dict[str, Any]], None, None]:
        """
        Stream chat responses. Yields typed events:
        - {"type": "stream_started"} immediately when processing begins
        - {"type": "content", "text": "..."} for text content
        - {"type": "thinking", "text": "..."} for thinking content (rendered with tags by UI)
        - {"type": "tool_start", "id": "...", "name": "...", "args": {...}} when tool begins
        - {"type": "tool_end", "id": "...", "result": "...", "error": bool} when tool completes
        - {"type": "iteration_start", "iteration": N} before each LLM call
        - {"type": "reload"} for page reload signal
        - {"type": "final", "text": str, "cancelled": bool, "error": bool}
          terminal event on every clean exit — the definitive turn text for
          blocking consumers (LLMChat.chat). Raise paths emit none; consumers
          catch the raise instead. SSE drops it (clients already streamed
          the content); the phone driver ignores it.
        - str for legacy compatibility (module responses, prefills)

        Args:
            user_input: Text input from user
            prefill: Optional assistant prefill for continue mode
            skip_user_message: Don't add user message to history (continue mode)
            images: Optional list of {"type": "image", "data": "...", "media_type": "..."}
            files: Optional list of {"filename": "...", "text": "..."}
        """
        logger.info(f"[START] [STREAMING START] cancel_flag={self.cancel_flag}, prefill={bool(prefill)}, skip_user={skip_user_message}, images={len(images) if images else 0}, files={len(files) if files else 0}")

        # Publish typing start event. D4: tag the surface so the operator's browser
        # ignores a phone call / background conversation's typing (target_chat set =
        # foreign stream). Untagged, a live call flickered the operator's avatar +
        # Stop button and re-fetched their active chat.
        self.is_streaming = True
        publish(Events.AI_TYPING_START, {"foreign": bool(self.target_chat), "chat": self.target_chat})

        # Immediate feedback that backend received the request
        yield {"type": "stream_started"}

        # Streaming TTS pump — emits per-chunk audio SSE events alongside
        # LLM token content. Inert when TTS_STREAMING_ENABLED is off or
        # provider lacks supports_streaming. Single pump spans the whole
        # turn (across tool iterations); only the final-content iteration
        # actually feeds it text and closes it. Defined here (outside the
        # main try) so the cleanup-finally can always reach it. The
        # cancel_check lambda lets flush_and_close bail mid-drain when
        # the user hits Stop AFTER the LLM is done but synth is still
        # finishing (M7 stop coordination).
        tts_pump = StreamingTTSPump(
            system=self.main_chat.system,
            cancel_check=lambda: self.cancel_flag or self.tts_stopped,
            # Set by the conversation driver for phone calls (sentence-split =
            # first audio at the first sentence, not end-of-generation). None
            # everywhere else — global Settings > TTS behavior unchanged.
            split_override=getattr(self, "tts_split_override", None),
            # Blocking consumer lane (LLMChat.chat, merge 2026-08-17): the
            # door speaks the returned blob itself (process_llm_query →
            # tts.speak), so the pump stays inert — no synth, no tts hooks,
            # no double-speak. Must gate HERE, not via tts_stopped: the
            # generator resets tts_stopped just below.
            disabled=bool(getattr(self, "suppress_tts", False)),
        )
        self.tts_pump = tts_pump   # expose for stop_tts() (left-button voice mute)

        # Prompt-privacy enforcement moved to _select_provider (2026-08-09) —
        # one server-side gate covering ALL doors (web, /api/chat, voice,
        # phone target-chats), target-chat aware. The old pre-flight here
        # checked only this door, read the GLOBAL active prompt (wrong chat
        # for phone streams), and silently self-disabled on ImportError.

        _brain_token = None   # A1: declared before the try so the finally is always safe

        try:
            # H4 counter, moved to the TOP of the try (vault hunt R3 2026-08-15):
            # count this stream BEFORE any setup so (a) a vault lock's eviction
            # defers to end_streaming instead of racing the setup below, and
            # (b) the unconditional end_streaming in the finally can never
            # double-decrement a concurrent stream's count when setup throws.
            # (H4 2026-04-22 history: single bool let two concurrent streams
            # corrupt mid-turn history; counter fix.)
            self.main_chat.session_manager.begin_streaming()
            self.cancel_flag = False
            self.tts_stopped = False
            self.current_stream = None
            self.ephemeral = False
            # begin_stream() stamped the route-accept active chat on us; the
            # gap between that and this generator starting is UNCOUNTED — an
            # eviction may have landed in it.
            _intended_chat = getattr(self, "active_chat_name", None) or None
            try:
                self.active_chat_name = self.main_chat.session_manager.get_active_chat_name()
            except Exception:
                self.active_chat_name = None

            # Vault hunt R3/R4: operator lane — if a lock's eviction moved the
            # world in that uncounted gap, refuse the turn instead of writing
            # the user's private-intent text into the eviction landing chat.
            # Same refusal when the active chat is private in a sealed world
            # (failed-eviction state): its turns can't persist honestly.
            if not getattr(self, "target_chat", None):
                if _intended_chat and self.active_chat_name \
                        and _intended_chat != self.active_chat_name:
                    logger.warning("[VAULT] active chat changed during stream "
                                   "setup — turn refused")
                    _refusal = ("🔒 The vault locked while this message was "
                                "in flight and the active chat changed. "
                                "Please resend.")
                    yield {"type": "content", "text": _refusal}
                    yield {"type": "final", "text": _refusal,
                           "cancelled": False, "error": True}
                    return
                try:
                    from core import prompt_vault as _pv
                    _vs_gate = _pv.vault_status()
                    if _vs_gate.get('exists') and not _vs_gate.get('unlocked') \
                            and self.main_chat.session_manager.get_chat_settings().get('private_chat'):
                        logger.warning("[VAULT] turn refused — active chat is "
                                       "private and the vault is sealed")
                        _refusal = ("🔒 The vault is locked and this chat is "
                                    "private — unlock the vault to continue "
                                    "here.")
                        yield {"type": "content", "text": _refusal}
                        yield {"type": "final", "text": _refusal,
                               "cancelled": False, "error": True}
                        return
                except Exception:
                    pass

            # A1: if this stream has an EXPLICIT target chat (driver/phone — web
            # passes None), install a per-context brain override so the whole turn
            # — messages, provider, persona, tools, scopes — runs in THAT chat from
            # its STORED settings. Unconditional on the target: comparing against
            # the active chat let a phone call inherit the UI session's in-memory
            # brain whenever the user was WATCHING the call's chat (2026-07-03:
            # Alfred greeting, Sapphire brain). Reset in the outer finally.
            _brain_token = None
            _tgt = getattr(self, "target_chat", None)
            if _tgt:
                try:
                    from core.chat import stream_brain
                    from core import prompts as _prompts
                    sess = self.main_chat.session_manager.make_stream_session(_tgt)
                    if sess:
                        _pn = sess["settings"].get("prompt", "default")
                        _pd = _prompts.get_prompt(_pn)
                        if not isinstance(_pd, dict):
                            # Missing name — run on assembled default this turn
                            # (heals when the prompt re-registers), never the
                            # literal "System prompt not loaded."
                            _pd = _prompts.get_prompt("default")
                        sess["system_prompt"] = (_pd.get("content", "") if isinstance(_pd, dict) else "") or ""
                        sess["tools"] = self.main_chat._resolve_toolset_tools(
                            sess["settings"].get("toolset", "all"),
                            sess["settings"].get("extra_toolsets"))
                        _brain_token = stream_brain.set_override(sess)
                        logger.info(f"[A1] stream brain override → chat '{_tgt}' "
                                    f"(provider={sess['settings'].get('llm_primary')}, "
                                    f"tools={len(sess['tools']) if sess['tools'] else 0})")
                except Exception as _e:
                    logger.warning(f"[A1] stream brain override failed for '{_tgt}': {_e}")
                    _brain_token = None
            else:
                # Talk-marks-private (Krem's ruling 2026-08-14): SPEAKING in a
                # chat while the vault is open stamps it private — reading
                # never does; the only unmark is Chat Manager. Operator lanes
                # only: web typing and browser voice both ride target_chat=
                # None, while phone/background streams carry an explicit
                # target and never stamp. Runs BEFORE provider selection so
                # THIS turn already enforces local-only (stamping after would
                # leak the first turn to a cloud provider).
                # An operator turn is USER ACTIVITY for the vault idle clock
                # (Krem's ruling 2026-08-15): chatting in a private chat must
                # not read as "idle" to the auto-lock. Phone/background
                # streams ride the target_chat branch and never touch.
                try:
                    from core import prompt_vault as _pv_touch
                    _pv_touch.touch()
                except Exception:
                    pass
                self._stamp_private_if_unlocked()
            # Spice rail AFTER the A1 override install: before this, a phone
            # turn's spice cadence read the OPERATOR'S chat settings and turn
            # count (the ContextVar wasn't set yet) and could rewrite the
            # global prompt from a foreign stream.
            self.main_chat.refresh_spice_if_needed()

            # Plugin pre_chat hook — can modify input, bypass LLM, or stop propagation
            if hook_runner.has_handlers("pre_chat"):
                hook_event = HookEvent(input=user_input, config=config,
                                       metadata={"system": self.main_chat.system})
                hook_runner.fire("pre_chat", hook_event)
                if hook_event.skip_llm:
                    response = hook_event.response or ""
                    if response:
                        if not hook_event.ephemeral:
                            self.main_chat.session_manager.add_user_message(user_input)
                            self.main_chat.session_manager.add_assistant_final(response)
                        else:
                            self.ephemeral = True
                        yield {"type": "content", "text": response}
                    yield {"type": "final", "text": response,
                           "cancelled": False, "error": False}
                    publish(Events.AI_TYPING_END, {"foreign": bool(self.target_chat), "chat": self.target_chat})
                    self.is_streaming = False
                    return
                user_input = hook_event.input  # may have been mutated

            messages = self.main_chat._build_base_messages(user_input, images=images, files=files)

            if not skip_user_message:
                # Build content list if files or images present, otherwise just text
                if files or images:
                    user_content = []
                    if user_input:
                        user_content.append({"type": "text", "text": user_input})
                    for f in (files or []):
                        user_content.append({
                            "type": "file",
                            "filename": f.get("filename", ""),
                            "text": f.get("text", "")
                        })
                    for img in (images or []):
                        user_content.append({
                            "type": "image",
                            "data": img.get("data", ""),
                            "media_type": img.get("media_type", "image/jpeg")
                        })
                    self.main_chat.session_manager.add_user_message(user_content)
                else:
                    self.main_chat.session_manager.add_user_message(user_input)
            else:
                logger.info("[CONTINUE] Skipping user message addition (continuing from existing)")
            
            # Handle manual continue prefill
            has_prefill = bool(prefill)
            if has_prefill:
                # Strip trailing whitespace - Claude API rejects it
                clean_prefill = prefill.rstrip()
                messages.append({"role": "assistant", "content": clean_prefill})
                logger.info(f"[CONTINUE] Continuing with {len(clean_prefill)} char prefill")
                yield {"type": "content", "text": prefill}  # Show original to user
            
            # Handle forced thinking prefill - disabled when continuing
            force_prefill = None
            if getattr(config, 'FORCE_THINKING', False) and not has_prefill:
                force_prefill = getattr(config, 'THINKING_PREFILL', '<think>')
                # Strip trailing whitespace for Claude compatibility
                messages.append({"role": "assistant", "content": force_prefill.rstrip()})
                logger.info(f"[THINK] Forced thinking prefill: {force_prefill}")
                yield {"type": "content", "text": force_prefill}
            
            # Set scopes for this chat context
            # Reset first to prevent bleed across chats when plugin scopes come and go
            # (a chat saved before a plugin was enabled wouldn't have its scope key in settings,
            # and apply_scopes only sets keys present in the dict → stale value survives).
            from core.chat.function_manager import reset_scopes
            reset_scopes()
            chat_settings = self.main_chat.session_manager.get_chat_settings()
            self.main_chat.function_manager.apply_scopes(chat_settings)
            # Effective chat (matches the get_chat_settings() applied above) — a phone
            # call reads ITS RAG scope, never the operator's active-chat documents.
            chat_name = self.main_chat.session_manager._effective_chat_name()
            self.main_chat.function_manager.set_rag_scope(f"__rag__:{chat_name}")

            # Snapshot scopes as a plain dict — survives across Starlette's
            # per-yield context resets, re-applied in execute_function()
            _scopes = self.main_chat.function_manager.snapshot_scopes()

            # Send only enabled tools - model should only know about active tools
            # Snapshot names too — used to validate tool calls against what LLM actually received
            # Snapshot executors to protect against reload yanking executors mid-chat
            enabled_tools = self._effective_enabled_tools()
            # tools_filter hook (2026-08-24): plugins subtract tools from THIS
            # turn's schema — the game-room scenario fence. Subtract-only by
            # construction (intersection against the resolved list), fail-open
            # on any error. Sits at the ONE seam both lanes cross (active
            # chat + stream-brain override), so _allowed_tool_names below is
            # derived from the FILTERED list — a hallucinated call to a
            # fenced tool is refused like any unknown tool.
            try:
                from core.hooks import hook_runner, HookEvent
                _ev = HookEvent(chat_name=chat_name, tools=list(enabled_tools))
                hook_runner.fire("tools_filter", _ev)
                _kept = {t["function"]["name"] for t in (_ev.tools or [])
                         if isinstance(t, dict) and "function" in t}
                enabled_tools = [t for t in enabled_tools
                                 if t.get("function", {}).get("name") in _kept]
            except Exception as e:
                logger.warning(f"tools_filter hook failed (unfiltered list ships): {e}")
            _allowed_tool_names = {t["function"]["name"] for t in enabled_tools if "function" in t}
            _executor_snapshot = self.main_chat.function_manager.snapshot_executors()

            # Drain dangling-toolset state and surface as SSE notice (parallel
            # to non-streaming chat.py logic). Cleared after consume so it
            # only fires once per detection. getattr is defensive — some tests
            # mock function_manager without this attribute.
            bad_ts = getattr(self.main_chat.function_manager, 'last_dangling_toolset', None)
            if bad_ts:
                self.main_chat.function_manager.last_dangling_toolset = None
                yield {
                    "type": "notice",
                    "message": f"Toolset '{bad_ts}' is missing — tools disabled for this chat. Fix in chat settings.",
                    "severity": "warning",
                }

            provider_key, provider, model_override = self.main_chat._select_provider()

            # Determine effective model (per-chat override or provider default)
            effective_model = model_override if model_override else provider.model

            # Provenance for tool executors (mindpalace metadata). Patches the
            # _scopes snapshot taken above, since the model resolves only here.
            from core.chat.function_manager import set_tool_context
            set_tool_context(_scopes, chat=chat_name,
                             persona=chat_settings.get('prompt'),
                             model=effective_model)

            gen_params = get_generation_params(
                provider_key,
                effective_model,
                {**getattr(config, 'LLM_PROVIDERS', {}), **getattr(config, 'LLM_CUSTOM_PROVIDERS', {})}
            )
            
            # Pass model override to provider if set
            if model_override:
                gen_params['model'] = model_override
            
            # CRITICAL: Disable thinking for continue operations
            # Claude requires thinking blocks with signatures - we can't fake them
            if has_prefill:
                gen_params['disable_thinking'] = True
                logger.info("[CONTINUE] Disabled thinking for continue (can't replay signatures)")

            tool_call_count = 0
            loop_counts = {}  # per-turn per-tool call counts (loop guard); turn-local by design
            # Accumulate token usage across all iterations for final summary
            cumulative_tokens = {"prompt": 0, "completion": 0, "thinking": 0, "total": 0,
                                 "cache_read": 0, "cache_write": 0, "iterations": 0}

            # Bind the per-iteration vars the cancel-save path (below) reads, so a barge-in that
            # sets cancel_flag before iteration 0 reaches their in-loop init can't UnboundLocalError.
            current_content = ""
            current_thinking = ""
            metadata = None

            for iteration in range(config.MAX_TOOL_ITERATIONS):
                if self.cancel_flag:
                    logger.info(f"[STOP] [STREAMING] Cancelled at iteration {iteration + 1}")
                    break
                
                logger.info(f"--- Streaming Iteration {iteration + 1}/{config.MAX_TOOL_ITERATIONS} ---")
                
                # Signal UI that we're starting a new LLM call (useful after tool completion)
                yield {"type": "iteration_start", "iteration": iteration + 1}
                
                # Track content and thinking separately
                current_content = ""
                current_thinking = ""
                thinking_raw = None
                metadata = None
                in_thinking = False
                
                tool_calls = []
                tool_pending_sent = set()  # Track which tool indices got early UI hint
                final_response = None
                first_chunk_time = None  # Track when generation actually starts
                chunk_count = 0  # init BEFORE the try — the except reads it, and creation can raise

                try:
                    logger.info(f"[STREAM] Creating provider stream [{provider.provider_name}] (effective_model={effective_model})")
                    self.current_stream = provider.chat_completion_stream(
                        messages,
                        tools=enabled_tools if enabled_tools else None,
                        generation_params=gen_params
                    )
                    for event in self.current_stream:
                        chunk_count += 1
                        
                        # Start timing from first actual chunk (not stream creation)
                        if first_chunk_time is None:
                            first_chunk_time = time.time()
                        
                        if self.cancel_flag:
                            logger.info(f"[STOP] [STREAMING] Cancelled at chunk {chunk_count}")
                            self._cleanup_stream()
                            break
                        
                        event_type = event.get("type")
                        
                        if event_type == "content":
                            text = event.get("text", "")
                            # Close thinking tag if transitioning from think to prose
                            if in_thinking:
                                yield {"type": "content", "text": "</think>\n\n"}
                                in_thinking = False
                            current_content += text
                            yield {"type": "content", "text": text}
                            # Streaming TTS: push the same text to the chunker.
                            # Returned events are tts_stream_start (first push)
                            # and any tts_chunks whose synth completed in time.
                            for tts_ev in tts_pump.push(text):
                                yield tts_ev

                        elif event_type == "thinking":
                            # Thinking from Claude - emit as content with tags for UI
                            text = event.get("text", "")
                            current_thinking += text

                            # Emit thinking wrapped in tags for UI rendering
                            if not in_thinking:
                                yield {"type": "content", "text": "<think>"}
                                in_thinking = True
                            yield {"type": "content", "text": text}

                        elif event_type == "tool_call":
                            # Close thinking tag if open before tool calls
                            if in_thinking:
                                yield {"type": "content", "text": "</think>\n\n"}
                                in_thinking = False
                            
                            idx = event.get("index", 0)
                            while len(tool_calls) <= idx:
                                tool_calls.append({
                                    "id": "",
                                    "type": "function",
                                    "function": {"name": "", "arguments": ""}
                                })
                            
                            if event.get("id"):
                                tool_calls[idx]["id"] = event["id"]
                            if event.get("name"):
                                tool_calls[idx]["function"]["name"] = event["name"]
                            if event.get("arguments"):
                                tool_calls[idx]["function"]["arguments"] = event["arguments"]

                            # Early UI hint: show accordion as soon as we know the tool name
                            # Only for indices within MAX_PARALLEL_TOOLS — excess tools won't execute
                            tc = tool_calls[idx]
                            if idx not in tool_pending_sent and tc["function"]["name"] and idx < config.MAX_PARALLEL_TOOLS:
                                tool_pending_sent.add(idx)
                                yield {"type": "tool_pending", "name": tc["function"]["name"], "index": idx}
                        
                        elif event_type == "done":
                            # Close thinking tag if still open
                            if in_thinking:
                                yield {"type": "content", "text": "</think>\n\n"}
                                in_thinking = False
                            
                            final_response = event.get("response")
                            # Capture thinking data from done event
                            if event.get("thinking"):
                                current_thinking = event["thinking"]
                            if event.get("thinking_raw"):
                                thinking_raw = event["thinking_raw"]
                            if event.get("metadata"):
                                metadata = event["metadata"]
                    
                    logger.info(f"[STREAM] Stream iteration complete ({chunk_count} chunks)")
                    self._cleanup_stream()
                    
                    if self.cancel_flag:
                        break
                
                except Exception as e:
                    # Silent regen (surface opt-in via stream attrs, set by the
                    # conversation driver for phone calls): a timeout with ZERO
                    # chunks received retries once with a fresh provider stream —
                    # same messages, no error row in history, no spoken apology.
                    _en = type(e).__name__.lower()
                    _timeoutish = "timeout" in _en or "timed out" in str(e).lower()
                    if (_timeoutish and chunk_count == 0 and not self.cancel_flag
                            and int(getattr(self, "timeout_retry", 0) or 0) > 0):
                        self.timeout_retry = int(self.timeout_retry) - 1
                        logger.warning("[STREAMING] first-token timeout — silent retry with a fresh stream")
                        self._cleanup_stream()
                        _cb = getattr(self, "on_timeout_retry", None)
                        if _cb:
                            try:
                                _cb()
                            except Exception:
                                pass
                        continue    # burns one tool-iteration slot; messages unchanged
                    logger.error(f"[ERR] [STREAMING] Iteration {iteration + 1} failed: {e}", exc_info=True)
                    self._cleanup_stream()
                    raise
                
                # Build metadata if not provided by provider
                if not metadata:
                    iteration_end_time = time.time()
                    gen_start = first_chunk_time or iteration_end_time
                    duration = round(iteration_end_time - gen_start, 2)

                    # Try real usage from provider response first, fall back to estimate
                    resp_usage = final_response.usage if final_response and hasattr(final_response, 'usage') else None
                    if resp_usage:
                        content_tokens = resp_usage.get("completion_tokens", 0)
                        prompt_tokens = resp_usage.get("prompt_tokens", 0)
                        total_tokens = resp_usage.get("total_tokens", 0)
                        estimated = False
                    else:
                        content_tokens = len(current_content) // 4 if current_content else 0
                        prompt_tokens = 0
                        total_tokens = content_tokens + (len(current_thinking) // 4 if current_thinking else 0)
                        estimated = True

                    thinking_tokens = len(current_thinking) // 4 if current_thinking else 0

                    metadata = {
                        "provider": provider_key,
                        "model": effective_model,
                        "start_time": time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(gen_start)),
                        "end_time": time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(iteration_end_time)),
                        "duration_seconds": duration,
                        "tokens": {
                            "content": content_tokens,
                            "thinking": thinking_tokens,
                            "prompt": prompt_tokens,
                            "total": total_tokens,
                            "estimated": estimated
                        },
                        "tokens_per_second": round(content_tokens / duration, 1) if duration > 0 else 0
                    }
                    # Forward cache stats from provider
                    if resp_usage:
                        for k in ("cache_read_tokens", "cache_write_tokens"):
                            if resp_usage.get(k):
                                metadata["tokens"][k] = resp_usage[k]
                
                # Accumulate tokens across iterations
                if metadata and metadata.get("tokens"):
                    t = metadata["tokens"]
                    cumulative_tokens["prompt"] += t.get("prompt", 0)
                    cumulative_tokens["completion"] += t.get("content", 0)
                    cumulative_tokens["thinking"] += t.get("thinking", 0)
                    cumulative_tokens["total"] += t.get("total", 0)
                    cumulative_tokens["cache_read"] += t.get("cache_read_tokens", 0)
                    cumulative_tokens["cache_write"] += t.get("cache_write_tokens", 0)
                    cumulative_tokens["iterations"] += 1

                    # Record per-call metrics
                    call_type = "tool_call" if tool_calls else "conversation"
                    estimated = metadata["tokens"].get("estimated", False)
                    try:
                        # effective chat: a phone call's tokens attribute to ITS
                        # chat ('__private__' for private chats — plaintext deposit).
                        chat_name = self.main_chat.session_manager.metrics_chat_label()
                        token_metrics.record(chat_name, provider_key, effective_model,
                                             call_type, metadata, estimated=estimated)
                    except Exception:
                        pass  # Metrics are best-effort

                # Generate fallback IDs for tool calls missing them (GLM, some OpenAI-compat APIs)
                for tc in tool_calls:
                    if tc.get("function", {}).get("name") and not tc.get("id"):
                        tc["id"] = f"call_{iteration}_{tool_calls.index(tc)}"
                        logger.info(f"[TOOL] Generated fallback ID for tool call: {tc['function']['name']}")

                if tool_calls and any(tc.get("id") and tc.get("function", {}).get("name") for tc in tool_calls):
                    logger.info(f"[TOOL] Processing {len(tool_calls)} tool call(s)")
                    
                    tool_calls_to_execute = tool_calls[:config.MAX_PARALLEL_TOOLS]
                    
                    # Combine prefill with current content for history
                    full_content = prefill + current_content if has_prefill else current_content

                    # When the content carries inline <think> reasoning (Qwen/GLM-style),
                    # keep only the thinking and drop the trailing decision-prose ("okay,
                    # I'll generate the image"). Replaying that prose as committed history
                    # nudges the model to repeat the action — the non-streaming path
                    # already filters here; streaming didn't. Gated on inline think tags
                    # so providers whose reasoning lives in thinking_raw (Claude) are
                    # untouched and never get their prose wrapped. Only `content` changes;
                    # thinking_raw/thinking are preserved. 2026-06-14.
                    if "<think" in (full_content or "").lower():
                        stored_content = filter_to_thinking_only(full_content)
                    else:
                        stored_content = full_content

                    # Store message with tool calls - include thinking_raw for Claude
                    # tool cycles, AND `thinking` for DeepSeek-reasoner's required
                    # reasoning_content round-trip on subsequent iterations. Without
                    # the `thinking` field here, the in-memory messages list omits
                    # it, the openai_compat sanitizer's gate at line 427-430 finds
                    # `msg.get('thinking') == None`, and the next API call after
                    # tool execution hits 400 "Missing reasoning_content". 2026-05-14.
                    messages.append({
                        "role": "assistant",
                        "content": stored_content,
                        "tool_calls": tool_calls_to_execute,
                        "thinking_raw": thinking_raw,  # Has signatures for Claude API
                        "thinking": current_thinking if current_thinking else None,
                    })

                    # Save to history with new schema
                    self.main_chat.session_manager.add_assistant_with_tool_calls(
                        content=stored_content,
                        tool_calls=tool_calls_to_execute,
                        thinking=current_thinking if current_thinking else None,
                        thinking_raw=thinking_raw,
                        metadata=metadata
                    )
                    
                    iteration_tool_images = []

                    for tool_call in tool_calls_to_execute:
                        if self.cancel_flag:
                            logger.info(f"[STOP] [STREAMING] Cancelled before tool execution")
                            break

                        if not tool_call.get("id") or not tool_call.get("function", {}).get("name"):
                            continue

                        tool_call_count += 1
                        function_name = tool_call["function"]["name"]
                        tool_call_id = tool_call["id"]
                        # Loop guard: count every attempt this turn (success, throw, or bad JSON).
                        self.main_chat.function_manager.bump_loop_count(loop_counts, function_name)

                        raw_args = tool_call.get("function", {}).get("arguments", "")
                        # Empty string is valid for no-arg tools — Claude/Anthropic
                        # sends arguments='' (not '{}') when a tool has no params.
                        # json.loads('') would fail. Treat empty as {} explicitly.
                        # 2026-05-15.
                        if raw_args == "" or raw_args is None:
                            function_args = {}
                        else:
                            try:
                                function_args = json.loads(raw_args)
                            except json.JSONDecodeError:
                                # Mirror the non-streaming path (chat_tool_calling.py:372-385):
                                # surface the parse error back to the LLM as a tool result
                                # instead of silently calling the tool with empty args.
                                # Smaller/quantized models occasionally emit malformed JSON;
                                # without feedback the LLM can't self-correct and the tool
                                # runs with default args (potentially destructive for tools
                                # with permissive defaults). 2026-05-14.
                                logger.error(
                                    f"[STREAMING] Failed to parse tool arguments for "
                                    f"{function_name}: {raw_args!r}"
                                )
                                error_result = "Error: Invalid JSON arguments."
                                # Loop-warn to the LLM only; history keeps raw error.
                                llm_result = error_result + self.main_chat.function_manager.loop_warn_suffix(function_name, loop_counts)
                                # CRITICAL: also append to messages — without this, the next
                                # LLM call sees a tool_use with no matching tool_result and
                                # Anthropic returns a 400 that kills the whole turn.
                                # 2026-05-15.
                                wrapped_err = provider.format_tool_result(
                                    tool_call_id, function_name, llm_result
                                )
                                messages.append(wrapped_err)
                                self.main_chat.session_manager.add_tool_result(
                                    tool_call_id, function_name, error_result
                                )
                                yield {
                                    "type": "tool_end",
                                    "id": tool_call_id,
                                    "name": function_name,
                                    "result": error_result,
                                    "is_error": True,
                                }
                                continue

                        # Emit typed tool_start event
                        yield {
                            "type": "tool_start",
                            "id": tool_call_id,
                            "name": function_name,
                            "args": function_args
                        }

                        # Publish to event bus for avatar/plugins
                        publish(Events.TOOL_EXECUTING, {"name": function_name})

                        try:
                            function_result = self.main_chat.function_manager.execute_function(function_name, function_args, scopes=_scopes, allowed_tools=_allowed_tool_names, executor_snapshot=_executor_snapshot)
                            result_str, tool_imgs = _extract_tool_images(function_result, self.main_chat.session_manager, provider, function_name)
                            if tool_imgs:
                                iteration_tool_images.extend(tool_imgs)
                                logger.info(f"[TOOL] {function_name} returned {len(tool_imgs)} image(s)")
                            clean_result = strip_ui_markers(result_str)
                            clean_result += self.main_chat.function_manager.loop_warn_suffix(function_name, loop_counts)

                            publish(Events.TOOL_COMPLETE, {"name": function_name, "success": True})

                            # Emit typed tool_end event
                            yield {
                                "type": "tool_end",
                                "id": tool_call_id,
                                "name": function_name,
                                "result": clean_result[:500] if len(clean_result) > 500 else clean_result,
                                "error": False
                            }

                            wrapped_msg = provider.format_tool_result(
                                tool_call_id,
                                function_name,
                                clean_result
                            )
                            messages.append(wrapped_msg)
                            logger.info(f"[OK] [STREAMING] Tool {function_name} executed successfully")

                            self.main_chat.session_manager.add_tool_result(
                                tool_call_id,
                                function_name,
                                result_str,
                                inputs=function_args
                            )

                        except Exception as tool_error:
                            logger.error(f"Tool execution error: {tool_error}", exc_info=True)
                            # Exception strings can embed whole payloads — this
                            # path bypasses _extract_tool_images, so cap here.
                            error_result = cap_tool_result_text(f"Error: {str(tool_error)}", function_name)
                            # Loop-warn to the LLM only; history + SSE keep the raw error.
                            llm_result = error_result + self.main_chat.function_manager.loop_warn_suffix(function_name, loop_counts)

                            publish(Events.TOOL_COMPLETE, {"name": function_name, "success": False})

                            yield {
                                "type": "tool_end",
                                "id": tool_call_id,
                                "name": function_name,
                                "result": error_result,
                                "error": True
                            }

                            wrapped_msg = provider.format_tool_result(
                                tool_call_id,
                                function_name,
                                llm_result
                            )
                            messages.append(wrapped_msg)

                            self.main_chat.session_manager.add_tool_result(
                                tool_call_id,
                                function_name,
                                error_result,
                                inputs=function_args
                            )

                    # Inject tool-returned images for next LLM turn
                    if iteration_tool_images:
                        from .chat import _inject_tool_images
                        _inject_tool_images(messages, iteration_tool_images, provider)

                    # Refresh tools list — tool_load may have added new tools.
                    # Also refresh the allowed_tool_names guard set and the
                    # executor snapshot so newly-loaded tools become callable
                    # on the next iteration. Without this, the LLM would see
                    # new tools in tools= but execute_function would reject
                    # them as "not in active toolset". Mirrors chat.py:680.
                    enabled_tools = self._effective_enabled_tools()
                    _allowed_tool_names = {t["function"]["name"] for t in enabled_tools if "function" in t}
                    _executor_snapshot = self.main_chat.function_manager.snapshot_executors()

                    if self.cancel_flag:
                        break

                    continue

                # Check for text-based tool calls (LM Studio, Qwen, GLM compatibility)
                # Check both content AND thinking - GLM puts tool calls in reasoning_content
                else:
                    function_call_data = None
                    # Check content first
                    if current_content:
                        function_call_data = self.tool_engine.extract_function_call_from_text(current_content)
                    # Also check thinking content (GLM reasoning_content may contain tool calls)
                    if not function_call_data and current_thinking:
                        function_call_data = self.tool_engine.extract_function_call_from_text(current_thinking)
                        if function_call_data:
                            logger.info("[TOOL] Found text-based tool call in thinking/reasoning content")
                    if function_call_data:
                        text_tool_name = function_call_data["function_call"]["name"]
                        logger.info(f"[TOOL] Text-based tool call detected: {text_tool_name}")

                        tool_call_count += 1
                        full_content = prefill + current_content if has_prefill else current_content

                        # Execute text-based tool call (function_manager returns error if not active).
                        # allowed_tools + executor_snapshot were missing here for a year
                        # (validation-bypass drift vs the native tool_calls path above and
                        # the old blocking lane) — closed in the 2026-08-17 merge.
                        _, text_tool_images = self.tool_engine.execute_text_based_tool_call(
                            function_call_data,
                            full_content,
                            messages,
                            self.main_chat.session_manager,
                            provider,
                            scopes=_scopes,
                            allowed_tools=_allowed_tool_names,
                            executor_snapshot=_executor_snapshot,
                            loop_counts=loop_counts
                        )

                        # Inject tool-returned images for next LLM turn
                        if text_tool_images:
                            from .chat import _inject_tool_images
                            _inject_tool_images(messages, text_tool_images, provider)

                        # Emit tool events for UI
                        tool_name = function_call_data["function_call"]["name"]
                        tool_args = function_call_data["function_call"].get("arguments", {})
                        yield {"type": "tool_start", "id": f"text_{iteration}", "name": tool_name, "args": tool_args}

                        # Get the result that was added to messages
                        last_msg = messages[-1] if messages else {}
                        result = last_msg.get("content", "Tool executed")
                        is_error = "Error:" in result or "not currently available" in result

                        yield {"type": "tool_end", "id": f"text_{iteration}", "name": tool_name, "result": result[:500], "error": is_error}

                        logger.info(f"[TOOL] Text-based tool iteration {iteration + 1} completed")
                        continue

                    logger.info(f"[OK] Final response received after {iteration + 1} iteration(s)")

                    full_content = current_content

                    if has_prefill:
                        full_content = prefill + full_content

                    if force_prefill:
                        full_content = force_prefill + full_content

                    # post_llm hook — plugins can mutate response before save + TTS
                    if hook_runner.has_handlers("post_llm"):
                        llm_event = hook_runner.fire("post_llm", HookEvent(
                            input=user_input, response=full_content,
                            config=config, metadata={"system": self.main_chat.system}
                        ))
                        full_content = llm_event.response or full_content

                    # Attach cumulative token stats from all iterations
                    if cumulative_tokens["iterations"] > 1 and metadata:
                        metadata["cumulative_tokens"] = {
                            "prompt": cumulative_tokens["prompt"],
                            "completion": cumulative_tokens["completion"],
                            "thinking": cumulative_tokens["thinking"],
                            "total": cumulative_tokens["total"],
                            "iterations": cumulative_tokens["iterations"]
                        }
                        if cumulative_tokens["cache_read"]:
                            metadata["cumulative_tokens"]["cache_read"] = cumulative_tokens["cache_read"]
                        if cumulative_tokens["cache_write"]:
                            metadata["cumulative_tokens"]["cache_write"] = cumulative_tokens["cache_write"]

                    # Save final response with thinking separated
                    self.main_chat.session_manager.add_assistant_final(
                        content=full_content,
                        thinking=current_thinking if current_thinking else None,
                        metadata=metadata
                    )

                    if hook_runner.has_handlers("post_chat"):
                        hook_runner.fire("post_chat", HookEvent(
                            input=user_input, response=full_content,
                            config=config, metadata={"system": self.main_chat.system}
                        ))

                    # Flush remaining audio chunks (blocks on synth) + tts_stream_end
                    for tts_ev in tts_pump.flush_and_close():
                        yield tts_ev

                    yield {"type": "final", "text": full_content,
                           "cancelled": False, "error": False}
                    return
            
            # If cancelled, KEEP whatever she generated before the interrupt (was:
            # discard the whole turn — barge-in left a hole in the history). Save the
            # partial prose as a TEXT assistant message — never a tool_use, so the
            # tool_use->tool_result contract is untouched. Then fall through. 2026-06-19.
            if self.cancel_flag:
                partial = (current_content or "").strip()
                save_content = ""
                if partial:
                    save_content = prefill + current_content if has_prefill else current_content
                    try:
                        self.main_chat.session_manager.add_assistant_final(
                            content=save_content,
                            thinking=current_thinking if current_thinking else None,
                            metadata=metadata,
                        )
                    except Exception as e:
                        logger.warning(f"[STREAMING] partial save on cancel failed: {e}")
                yield {"type": "final", "text": save_content,
                       "cancelled": True, "error": False}
                return

            # Loop exhausted - force final response
            logger.warning(f"[STREAMING] Exceeded max iterations ({config.MAX_TOOL_ITERATIONS}). Forcing final answer.")
            
            messages.append({
                "role": "user",
                "content": "You've used tools multiple times. Stop using tools now and provide your final answer based on the information you gathered."
            })
            
            try:
                yield {"type": "content", "text": "\n\n"}
                
                final_stream = provider.chat_completion_stream(
                    messages,
                    tools=None,
                    generation_params=gen_params
                )
                
                final_content = ""
                final_thinking = ""
                final_metadata = None
                forced_final_response = None
                in_thinking = False
                final_start_time = time.time()
                
                for event in final_stream:
                    if self.cancel_flag:
                        break
                    
                    event_type = event.get("type")
                    
                    if event_type == "content":
                        chunk = event.get("text", "")
                        if in_thinking:
                            yield {"type": "content", "text": "</think>\n\n"}
                            in_thinking = False
                        final_content += chunk
                        yield {"type": "content", "text": chunk}
                        for tts_ev in tts_pump.push(chunk):
                            yield tts_ev

                    elif event_type == "thinking":
                        text = event.get("text", "")
                        final_thinking += text
                        if not in_thinking:
                            yield {"type": "content", "text": "<think>"}
                            in_thinking = True
                        yield {"type": "content", "text": text}
                    
                    elif event_type == "done":
                        if in_thinking:
                            yield {"type": "content", "text": "</think>\n\n"}
                        if event.get("thinking"):
                            final_thinking = event["thinking"]
                        if event.get("metadata"):
                            final_metadata = event["metadata"]
                        forced_final_response = event.get("response")
                        break

                if not final_metadata:
                    final_end_time = time.time()
                    duration = round(final_end_time - final_start_time, 2)

                    resp_usage = forced_final_response.usage if forced_final_response and hasattr(forced_final_response, 'usage') else None
                    if resp_usage:
                        content_tokens = resp_usage.get("completion_tokens", 0)
                        prompt_tokens = resp_usage.get("prompt_tokens", 0)
                        total_tokens = resp_usage.get("total_tokens", 0)
                        estimated = False
                    else:
                        content_tokens = len(final_content) // 4 if final_content else 0
                        prompt_tokens = 0
                        total_tokens = content_tokens + (len(final_thinking) // 4 if final_thinking else 0)
                        estimated = True

                    thinking_tokens = len(final_thinking) // 4 if final_thinking else 0

                    final_metadata = {
                        "provider": provider_key,
                        "model": effective_model,
                        "start_time": time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(final_start_time)),
                        "end_time": time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(final_end_time)),
                        "duration_seconds": duration,
                        "tokens": {
                            "content": content_tokens,
                            "thinking": thinking_tokens,
                            "prompt": prompt_tokens,
                            "total": total_tokens,
                            "estimated": estimated
                        },
                        "tokens_per_second": round(content_tokens / duration, 1) if duration > 0 else 0
                    }
                    if resp_usage:
                        for k in ("cache_read_tokens", "cache_write_tokens"):
                            if resp_usage.get(k):
                                final_metadata["tokens"][k] = resp_usage[k]
                
                if final_content:
                    full_final = (force_prefill or "") + final_content
                else:
                    # Empty content after tool calls — short placeholder + toast.
                    # Old text ("I used N tools and gathered information") was
                    # a confident lie when tools had silently errored. Mirrors
                    # non-streaming chat.py fix. 2026-05-16.
                    full_final = "(no response)"
                    yield {"type": "content", "text": full_final}
                    yield {
                        "type": "notice",
                        "message": (
                            f"Generation ended without a reply after {tool_call_count} tool call(s). "
                            f"A tool likely errored or isn't in the active toolset — check the logs, or rephrase."
                        ),
                        "severity": "warning",
                    }

                # post_llm hook — plugins can mutate forced-final response
                if hook_runner.has_handlers("post_llm"):
                    llm_event = hook_runner.fire("post_llm", HookEvent(
                        input=user_input, response=full_final,
                        config=config, metadata={"system": self.main_chat.system}
                    ))
                    full_final = llm_event.response or full_final

                self.main_chat.session_manager.add_assistant_final(
                    content=full_final,
                    thinking=final_thinking if final_thinking and final_content else None,
                    metadata=final_metadata
                )
                _post_response = full_final

                if hook_runner.has_handlers("post_chat"):
                    hook_runner.fire("post_chat", HookEvent(
                        input=user_input, response=_post_response,
                        config=config, metadata={"system": self.main_chat.system}
                    ))

                # Flush streaming TTS for the forced-final path too.
                for tts_ev in tts_pump.flush_and_close():
                    yield tts_ev

                _forced_text, _forced_error = full_final, False

            except Exception as final_error:
                logger.error(f"[STREAMING] Forced final response failed: {final_error}")
                error_msg = f"I completed {tool_call_count} tool calls but encountered an error generating the final response."
                yield {"type": "content", "text": error_msg}
                self.main_chat.session_manager.add_assistant_final(error_msg)
                _forced_text, _forced_error = error_msg, True

            yield {"type": "final", "text": _forced_text,
                   "cancelled": False, "error": _forced_error}

        except ConnectionError as e:
            logger.warning(f"[STREAMING] {e}")
            # Save error so history doesn't end with a dangling user message
            self.main_chat.session_manager.add_assistant_final(
                f"[Connection error: {e}]"
            )
            # Synthetic tts_stream_end so the frontend's audio queue can
            # finalize even though we're aborting before flush_and_close.
            # Without this, isStreaming stays true / mute pill orphaned
            # until the next stream. 2026-05-18 herring-table #10.
            #
            # WIRE-ONLY — DO NOT FIRE THE HOOK HERE. Looks tempting to also
            # call _fire_hook("tts_stream_end", ...) but the `finally` block
            # below calls `tts_pump.cancel()` which fires the hook exactly
            # once (cancel() respects `_closed` guard at stream_pump.py:290,
            # so flush_and_close's normal-path hook fire is also single-fire).
            # The wire-vs-hook split is intentional: SSE goes to the browser
            # for UI cleanup; the hook goes to plugins for state finalize.
            # Three scouts independently flagged this as a "double fire" —
            # they were wrong; the wiring is asymmetric on purpose. 2026-05-20.
            if tts_pump._stream_started and not tts_pump._closed:
                yield {
                    "type": "tts_stream_end",
                    "stream_id": tts_pump._stream_id,
                    "chunk_count": tts_pump._chunk_count,
                    "interrupted": True,
                }
            self._cleanup_stream()
            raise
        except Exception as e:
            logger.error(f"[ERR] [STREAMING FATAL] Unhandled error: {e}", exc_info=True)
            # Save error so history doesn't end with a dangling user message
            # (consecutive user messages break Claude's alternating requirement)
            self.main_chat.session_manager.add_assistant_final(
                f"[Error: {type(e).__name__}: {e}]"
            )
            if tts_pump._stream_started and not tts_pump._closed:
                yield {
                    "type": "tts_stream_end",
                    "stream_id": tts_pump._stream_id,
                    "chunk_count": tts_pump._chunk_count,
                    "interrupted": True,
                }
            self._cleanup_stream()
            raise
        
        finally:
            logger.info(f"[CLEANUP] [STREAMING FINALLY] Cleaning up, cancel_flag={self.cancel_flag}")
            # Drop any in-flight TTS synth — no events flow after this point.
            try:
                tts_pump.cancel()
            except Exception as _tts_e:
                logger.warning(f"[CLEANUP] TTS pump cancel failed: {_tts_e!r}")
            # Close any open tool cycle so history isn't left in a broken state
            # (e.g. user hit Stop mid-tool-execution)
            if self.main_chat.session_manager._in_tool_cycle:
                logger.info("[CLEANUP] Closing orphaned tool cycle from cancelled stream")
                # Inject dummy tool_results for any pending tool_calls so LLM history
                # stays valid (providers require tool_result after tool_calls)
                try:
                    msgs = self.main_chat.session_manager._effective_chat().messages
                    for msg in reversed(msgs):
                        if msg.get("role") == "assistant" and msg.get("tool_calls"):
                            existing_results = {m.get("tool_call_id") for m in msgs if m.get("role") == "tool"}
                            for tc in msg["tool_calls"]:
                                tc_id = tc.get("id", "")
                                if tc_id not in existing_results:
                                    self.main_chat.session_manager.add_tool_result(
                                        tc_id, tc.get("function", {}).get("name", "unknown"),
                                        "[Cancelled by user]"
                                    )
                            break
                except Exception as e:
                    logger.warning(f"[CLEANUP] Failed to inject cancel tool results: {e}")
                self.main_chat.session_manager.add_assistant_final(
                    content="[Cancelled during tool execution]"
                )
            self._cleanup_stream()
            self.cancel_flag = False
            self.is_streaming = False
            self.active_chat_name = None
            # A1: drop the per-stream brain override AFTER the cleanup that
            # persists via the effective chat (tool-cycle close above) but
            # BEFORE end_streaming — the 1→0 boundary inside end_streaming
            # runs the vault eviction (set_active_chat → flush/load of the
            # ACTIVE chat) and must not resolve through this stream's
            # override: it would skip the outgoing chat's flush and resync
            # the override chat's store from a trimmed in-memory window
            # (day-ruiner hunt 2026-08-17).
            if _brain_token is not None:
                try:
                    from core.chat import stream_brain
                    stream_brain.reset_override(_brain_token)
                except Exception:
                    pass
            self.main_chat.session_manager.end_streaming()
            publish(Events.AI_TYPING_END, {"foreign": bool(self.target_chat), "chat": self.target_chat})