# core/continuity/executor.py
"""
Continuity Executor - Runs scheduled tasks with proper context isolation.
Switches chat context, applies settings, runs LLM, restores original state.
"""

import copy
import json
import logging
import threading
from contextvars import ContextVar
from datetime import datetime
from typing import Dict, Any
from core.event_bus import publish, Events

logger = logging.getLogger(__name__)


# Module-level ContextVar holding the parsed event_data dict that triggered
# the currently-running daemon/webhook task. Plugins (especially their
# tools) can read this to route results back to the originating request
# without the LLM having to carry routing IDs as tool arguments.
#
# Set INSIDE the task's thread (via _run_foreground/_run_background) so
# ContextVars resolve correctly — ContextVars don't propagate across the
# fresh threading.Thread the scheduler spawns per task.
#
# Empty dict default keeps `.get("anything")` safe in non-event contexts.
current_event_data: ContextVar[dict] = ContextVar("current_event_data", default={})


# Contract for plugin-supplied images on an event payload:
#   payload["images"] = [{"data": "<base64>", "media_type": "image/png"}, ...]
# Plugin authors populate this; core validates strictly and routes through the
# existing tool-image pipeline (DB blob + <<IMG::...>> marker + vision gate).
# Malformed entries are dropped with a log — never stringified into the prompt.
_MAX_EVENT_IMAGES = 8
_MAX_EVENT_IMAGE_BYTES = 10 * 1024 * 1024  # 10MB decoded, per image
_VALID_IMAGE_MEDIA = {"image/png", "image/jpeg", "image/jpg", "image/gif", "image/webp"}


def _extract_event_images(obj) -> list:
    """Pull validated images from an event payload dict.

    Returns a list of {"data": <base64 str>, "media_type": <str>} that passed
    validation, or [] if none/invalid. This is the trust boundary for
    plugin-supplied binary data: a signed plugin can still be buggy, so we
    decode-test every entry and cap count + size before anything reaches the
    LLM context or the chat DB. 2026-06-13.
    """
    import base64
    if not isinstance(obj, dict):
        return []
    raw = obj.get("images")
    if not isinstance(raw, list) or not raw:
        return []
    out = []
    for entry in raw[:_MAX_EVENT_IMAGES]:
        if not isinstance(entry, dict):
            logger.warning("[EVENT-IMG] Dropped non-dict image entry")
            continue
        data = entry.get("data")
        media_type = (entry.get("media_type") or "image/jpeg").lower()
        if not isinstance(data, str) or not data:
            logger.warning("[EVENT-IMG] Dropped image entry with no base64 'data' string")
            continue
        if media_type not in _VALID_IMAGE_MEDIA:
            logger.warning(f"[EVENT-IMG] Dropped image with unsupported media_type {media_type!r}")
            continue
        try:
            decoded = base64.b64decode(data, validate=True)
        except Exception:
            logger.warning("[EVENT-IMG] Dropped image entry — 'data' is not valid base64")
            continue
        if len(decoded) > _MAX_EVENT_IMAGE_BYTES:
            logger.warning(f"[EVENT-IMG] Dropped image — {len(decoded)} bytes exceeds cap")
            continue
        out.append({"data": data, "media_type": media_type})
    if len(raw) > _MAX_EVENT_IMAGES:
        logger.warning(f"[EVENT-IMG] Payload had {len(raw)} images; capped to {_MAX_EVENT_IMAGES}")
    return out


class ContinuityExecutor:
    """Executes continuity tasks with context isolation."""

    def __init__(self, system):
        """
        Args:
            system: VoiceChatSystem instance with llm_chat, tts, etc.
        """
        self.system = system
        # RLock (reentrant) — guards TTS voice snapshot/apply/restore. Non-reentrant
        # Lock caused a self-deadlock this morning when the finally block re-entered
        # via `with self._voice_lock:`. RLock also defends against any future tool
        # that calls back into the executor while a task is running (spawn_agent,
        # nested continuity triggers, future cross-task coordination) — that path
        # would deadlock on re-acquire with a plain Lock. Scout chaos #17/#18.
        self._voice_lock = threading.RLock()

    @staticmethod
    def _format_event_data(event_data: str) -> str:
        """Format raw event JSON into clean text for the AI.

        If the data is JSON with a 'text' field (e.g. messaging daemons),
        present it as a clean message. Otherwise pass through raw.
        """
        try:
            obj = json.loads(event_data) if isinstance(event_data, str) else event_data
        except (json.JSONDecodeError, TypeError):
            return event_data

        if not isinstance(obj, dict):
            return event_data

        # Email format — structured with subject, sender, body
        if obj.get("subject") is not None and obj.get("snippet") is not None:
            from_name = obj.get("from_name", "")
            from_addr = obj.get("from_address", "")
            sender = f"{from_name} <{from_addr}>" if from_name else from_addr
            parts = []
            if sender:
                parts.append(f"From: {sender}")
            parts.append(f"Subject: {obj['subject']}")
            if obj.get("uid"):
                parts.append(f"UID: {obj['uid']}")
            parts.append("")
            parts.append(obj["snippet"])
            return "\n".join(parts)

        text = obj.get("text") or obj.get("content") or None
        if not text:
            # Messaging-shaped payloads (daemon events with routing fields) must
            # never fall through to raw JSON: the payload carries routing
            # metadata — llm_primary/llm_model, attachment URLs, author ids —
            # that would enter the prompt AND persist into chat history as the
            # user turn, where the AI later quotes it as fact ("I'm on <old
            # model>"). Format normally with a placeholder trigger instead.
            if any(obj.get(k) for k in ("channel_id", "chat_id", "account")):
                text = "(message has no text — likely an attachment, sticker, or embed)"
            else:
                # Generic non-message events keep raw-JSON visibility, minus
                # the LLM routing keys, which are never content.
                redacted = {k: v for k, v in obj.items()
                            if k not in ("llm_primary", "llm_model")}
                try:
                    return json.dumps(redacted, ensure_ascii=False, default=str)
                except Exception:
                    return event_data

        # Build clean message from common fields
        sender = obj.get("display_name") or obj.get("first_name") or obj.get("username") or obj.get("sender") or ""
        channel = obj.get("channel_name", "")
        guild = obj.get("guild_name", "")

        parts = []
        # Label channel to match tool param name exactly — AI can copy-paste
        if channel and guild:
            parts.append(f"channel: {channel}")
            parts.append(f"server: {guild}")
        elif channel:
            parts.append(f"channel: {channel}")

        # Include recent chat history if available
        history = obj.get("recent_history", [])
        if history:
            parts.append("Recent chat:")
            for line in history:
                parts.append(f"  {line}")
            parts.append("")  # blank line before the trigger message

        # Current date/time for context
        from datetime import datetime as _dt
        try:
            import config as _cfg
            tz_name = getattr(_cfg, 'USER_TIMEZONE', '')
            if tz_name:
                from zoneinfo import ZoneInfo
                now = _dt.now(ZoneInfo(tz_name))
            else:
                now = _dt.now().astimezone()
        except Exception:
            now = _dt.now()
        parts.append(f"Current time: {now.strftime('%Y-%m-%d %H:%M %Z').strip()}")

        # Include chat_id/account for messaging tools (Telegram, Discord)
        chat_id = obj.get("chat_id")
        channel_id = obj.get("channel_id")
        account = obj.get("account")
        if chat_id:
            parts.append(f"chat_id: {chat_id}")
        if channel_id:
            parts.append(f"channel_id: {channel_id}")
        if account:
            parts.append(f"account: {account}")

        # Discord mention hint — works for sender and anyone in recent history
        author_id = obj.get("author_id")
        if author_id and channel_id:
            parts.append(f"To @mention a Discord user, use <@userid> (e.g. <@{author_id}> for the sender). User IDs appear in [id:...] brackets in the chat history.")

        if chat_id or channel_id or account:
            parts.append("")

        # The trigger message itself — emphasized
        if sender:
            parts.append(f">>> {sender}: {text}")
        else:
            parts.append(f">>> {text}")
        return "\n".join(parts)

    def run(self, task: Dict[str, Any], event_data: str = None,
            progress_callback=None, response_callback=None) -> Dict[str, Any]:
        """
        Execute a continuity task.

        Args:
            task: Task definition dict
            event_data: Optional event payload (for daemon/webhook triggered tasks).
                        When present, initial_message is prepended as instructions.
            progress_callback: Optional callable(iteration, total) for progress updates
            response_callback: Optional callable(response_text) called before TTS

        Returns:
            Result dict with success, responses, errors
        """
        # Plugin-sourced tasks run their handler directly
        source = task.get("source", "")
        if source.startswith("plugin:"):
            return self._run_plugin_task(task, progress_callback, response_callback)

        # Deepcopy to protect against update_task mutating the dict while we
        # read it. Scheduler passes live refs from self._tasks.values(), so
        # a concurrent UI edit of this task would otherwise produce incoherent
        # merged settings (new persona name + old toolset, etc.)
        task = copy.deepcopy(task)

        # For event-triggered tasks, build message from instructions + event data
        _ev_token = None
        if event_data is not None:
            event_display = self._format_event_data(event_data)
            instructions = task.get("initial_message", "").strip()
            if instructions:
                task["initial_message"] = f"{instructions}\n\n{event_display}"
            else:
                task["initial_message"] = event_display

            # Plugin-supplied images (payload["images"]). Validated here at the
            # trust boundary; saved to the chat DB + sent to vision models at the
            # run site. Absent/invalid → [] → behavior identical to text-only. 2026-06-13.
            try:
                _img_obj = json.loads(event_data) if isinstance(event_data, str) else event_data
                event_images = _extract_event_images(_img_obj)
                if event_images:
                    task["_event_images"] = event_images
                    logger.info(f"[EVENT-IMG] {len(event_images)} image(s) attached from event payload")
            except (json.JSONDecodeError, TypeError):
                pass

            # Auto-set plugin scopes from event data (e.g. discord account)
            # Event source → scope key mapping for auto-fill.
            # Extracted to a list so adding a new event-emitting plugin only touches this table,
            # not the surrounding logic. (Future: move this into the scope manifest capability
            # so plugins can self-declare `"event_source_keyword": "discord"`.)
            _EVENT_SOURCE_SCOPE_MAP = [
                ("discord",  "discord_scope"),
                ("telegram", "telegram_scope"),
                ("email",    "email_scope"),
            ]
            try:
                obj = json.loads(event_data) if isinstance(event_data, str) else event_data
                if isinstance(obj, dict) and obj.get("account"):
                    trigger = task.get("trigger_config", {})
                    source = trigger.get("source", "") or trigger.get("event_source", "")
                    for keyword, scope_key in _EVENT_SOURCE_SCOPE_MAP:
                        if keyword in source and not task.get(scope_key):
                            task[scope_key] = obj["account"]
                            # Discord needs channel_id for auto-reply targeting
                            if keyword == "discord" and obj.get("channel_id"):
                                task["_discord_reply_channel_id"] = obj["channel_id"]
                            break
            except (json.JSONDecodeError, TypeError):
                pass

            # Generic plugin context propagation. Set the current event_data
            # in a ContextVar that plugin tools can read without the LLM
            # carrying routing IDs as arguments. Works in this thread + any
            # downstream call frame in the same task; ContextVars don't
            # propagate across new threads, but the task runs in this thread
            # so anything ExecutionContext.run does (LLM, tool calls) sees
            # the set value.
            try:
                event_obj = json.loads(event_data) if isinstance(event_data, str) else (event_data or {})
                if isinstance(event_obj, dict):
                    _ev_token = current_event_data.set(event_obj)
            except (json.JSONDecodeError, TypeError):
                pass

        # Resolve persona defaults into task (task-level fields override persona).
        # `_resolve_persona` raises on malformed-persona / lookup failure (today's
        # change). Build the result dict FIRST so we can return a shaped error
        # if persona resolution explodes — without this, the raise propagates
        # before the result dict exists and the scheduler's outer except has
        # to fabricate state. Witch-hunt 2026-04-21 finding H12.
        result = {
            "success": False,
            "task_id": task.get("id"),
            "task_name": task.get("name"),
            "started_at": datetime.now().isoformat(),
            "responses": [],
            "errors": []
        }
        try:
            try:
                task = self._resolve_persona(task)
            except Exception as e:
                err = f"Persona resolution failed: {e}"
                logger.error(f"[Continuity] {err}", exc_info=True)
                result["errors"].append(err)
                result["completed_at"] = datetime.now().isoformat()
                return result

            chat_target = task.get("chat_target", "").strip()

            # Blank chat_target = ephemeral: isolated, no chat creation, no UI impact
            if not chat_target:
                return self._run_background(task, result, progress_callback, response_callback)

            # Named chat_target = foreground: switches to that chat, runs, restores
            return self._run_foreground(task, result, progress_callback, response_callback)
        finally:
            # Reset the event ContextVar. Harmless on today's fresh-thread-per-task
            # scheduler; load-bearing the day any pooled thread calls run() —
            # without it the next run on that thread inherits a stale event's
            # llm_primary and routing ids.
            if _ev_token is not None:
                current_event_data.reset(_ev_token)
    
    @staticmethod
    def _extract_task_settings(task: Dict[str, Any]) -> Dict[str, Any]:
        """Extract execution settings from a task dict for ExecutionContext.
        Scope keys are pulled dynamically from SCOPE_REGISTRY so new plugin scopes
        propagate to scheduled tasks without code changes.

        Missing scope keys fall back to 'default' for backward compat with
        tasks created before the scope-registry rollout. But: we warn when
        that happens so the silent-default class of bugs is visible in logs.
        A task that SHOULD have been scoped to e.g. 'lookout' but is missing
        the key will still run, but the warning flags the write as going to
        shared memory unintentionally. Scout finding 2026-04-19."""
        from core.chat.function_manager import scope_setting_keys
        settings = {
            "prompt": task.get("prompt", "default"),
            "privacy_required": task.get("privacy_required", False),
            "toolset": task.get("toolset", "none"),
            "provider": task.get("provider", "auto"),
            "model": task.get("model", ""),
            "inject_datetime": task.get("inject_datetime", False),
            "max_tool_rounds": task.get("max_tool_rounds"),
            "max_parallel_tools": task.get("max_parallel_tools"),
            "context_limit": task.get("context_limit"),
            "system_append": task.get("system_append"),
        }
        missing_scopes = []
        for setting_key in scope_setting_keys():
            if setting_key in task:
                settings[setting_key] = task[setting_key]
            else:
                # Pre-2026-05-07 we wrote 'default' here. That defeated the
                # force-None protection in ExecutionContext._build_scopes:
                # by the time it ran, every scope key was already in
                # task_settings, so the `if setting_key not in task_settings`
                # branch was dead code. The "silent-default closed" comment
                # was a lie — only the warning fired, the real protection
                # was bypassed. Now we leave the key UNSET so _build_scopes
                # can force-None it, disabling the scope for this task as
                # designed. Wildcard scout 2026-05-07 finding (verified).
                missing_scopes.append(setting_key)
        if missing_scopes:
            logger.warning(
                f"[Continuity] Task '{task.get('name', '?')}' missing scope keys "
                f"{missing_scopes} — those scopes will be DISABLED for this run "
                f"(force-None at task entry). Edit the task to set explicit scopes "
                f"if writes from this task should land somewhere."
            )
        return settings

    def _run_background(self, task: Dict[str, Any], result: Dict[str, Any],
                        progress_cb=None, response_cb=None) -> Dict[str, Any]:
        """Run task in background mode — fully isolated via ExecutionContext."""
        from core.continuity.execution_context import ExecutionContext

        task_name = task.get("name", "Unknown")
        logger.info(f"[Continuity] Running '{task_name}' in BACKGROUND mode (ExecutionContext)")

        with self._voice_lock:
            original_voice = self._snapshot_voice()
            try:
                self._apply_voice(task)
            except Exception:
                self._restore_voice(original_voice)
                raise

            try:
                task_settings = self._extract_task_settings(task)
                ctx = ExecutionContext(
                    self.system.llm_chat.function_manager,
                    self.system.llm_chat.tool_engine,
                    task_settings
                )

                # Set Discord reply channel for auto-reply targeting
                reply_ch = task.get("_discord_reply_channel_id")
                if reply_ch:
                    try:
                        from plugins.discord.tools.discord_tools import _reply_channel_id
                        _reply_channel_id.set(reply_ch)
                    except ImportError:
                        pass

                tts_enabled = task.get("tts_enabled", True)
                browser_tts = task.get("browser_tts", False)
                msg = task.get("initial_message", "Hello.")
                # Ephemeral path doesn't persist — images are turn-only, shown to
                # the LLM (vision-gated in ctx), no DB save / marker needed.
                _ev_images = task.get("_event_images")

                try:
                    response = ctx.run(msg, images=_ev_images)

                    if response_cb and response:
                        try: response_cb(response)
                        except Exception as _e: logger.error(f"[Continuity] Response callback failed: {_e}")

                    if response:
                        if browser_tts:
                            publish(Events.TTS_SPEAK, {"text": response, "task": task_name})
                        elif tts_enabled and hasattr(self.system, 'tts') and self.system.tts:
                            try:
                                self.system.tts.speak_sync(response)
                            except Exception as tts_err:
                                logger.warning(f"[Continuity] TTS failed: {tts_err}")

                    result["responses"].append({
                        "iteration": 1,
                        "input": msg,
                        "output": response or None
                    })
                except Exception as e:
                    from core.chat.chat import friendly_llm_error
                    friendly = friendly_llm_error(e)
                    error_msg = f"Task failed: {friendly or e}"
                    logger.error(f"[Continuity] {error_msg}", exc_info=True)
                    result["errors"].append(error_msg)
                    publish(Events.CONTINUITY_TASK_ERROR, {
                        "task": task.get("name", "Unknown"),
                        "error": friendly or str(e),
                    })

                if progress_cb:
                    progress_cb(1, 1)

                result["success"] = len(result["errors"]) == 0

            except Exception as e:
                from core.chat.chat import friendly_llm_error
                friendly = friendly_llm_error(e)
                error_msg = f"Background task failed: {friendly or e}"
                logger.error(f"[Continuity] {error_msg}", exc_info=True)
                result["errors"].append(error_msg)
                publish(Events.CONTINUITY_TASK_ERROR, {
                    "task": task.get("name", "Unknown"),
                    "error": friendly or str(e),
                })

            finally:
                self._restore_voice(original_voice)

        result["completed_at"] = datetime.now().isoformat()
        return result

    @staticmethod
    def _save_event_images(images, target_chat, session_manager) -> str:
        """Save plugin event images to the chat DB keyed to target_chat and
        return a marker suffix to append to the user message text.

        Markers (`<<IMG::event:id>>`) match the UI strip pattern, so they're
        stripped before the LLM and rehydrated from the DB by the frontend —
        the same lifecycle as tool images. Bytes live in the DB; history holds
        only the marker. 2026-06-13.
        """
        import base64
        import uuid
        if not images or not hasattr(session_manager, "save_tool_image"):
            return ""
        markers = []
        for img in images:
            try:
                media_type = img.get("media_type", "image/jpeg")
                ext = "png" if "png" in media_type else ("gif" if "gif" in media_type
                      else ("webp" if "webp" in media_type else "jpg"))
                img_id = f"{uuid.uuid4().hex[:12]}.{ext}"
                img_bytes = base64.b64decode(img["data"])
                # Use the SAME `tool:` marker namespace as tool images — the
                # frontend (strips `tool:`), serve route, and orphan-pruner all
                # only recognize `tool:`. A separate `event:` prefix rendered as
                # a broken image AND got pruned as a false orphan. These ARE
                # tool_images-table entries, just sourced from an event. 2026-06-13.
                if session_manager.save_tool_image(img_id, img_bytes, media_type,
                                                   chat_name=target_chat):
                    markers.append(f"<<IMG::tool:{img_id}>>")
                else:
                    # DB write failed — don't append a marker pointing at a blob
                    # that isn't there (would 404 on render). LLM still saw the
                    # image this turn via the images= param; only history skips it.
                    logger.warning(f"[EVENT-IMG] save_tool_image returned False for {img_id} — marker skipped")
            except Exception as e:
                logger.warning(f"[EVENT-IMG] Failed to persist event image: {e}")
        return ("\n" + "\n".join(markers)) if markers else ""

    def _run_foreground(self, task: Dict[str, Any], result: Dict[str, Any],
                        progress_cb=None, response_cb=None) -> Dict[str, Any]:
        """Run task with persistent chat history — no UI switching.

        Voice-lock discipline: the lock is held for the ENTIRE duration of
        the task (snapshot → apply → LLM → TTS → restore). Before 2026-04-19
        the lock was released between apply and restore, which let a second
        concurrent task's `_apply_voice` run over the first's — task A would
        then speak with B's voice during overlap, and if B finished first
        and restored "original" (really A's voice), the user could hear
        half-swapped voices for the rest of A's run. Holding the lock across
        serializes concurrent foreground tasks but keeps TTS correctness.
        """
        from core.continuity.execution_context import ExecutionContext

        session_manager = self.system.llm_chat.session_manager
        original_voice: Dict[str, Any] = {}
        self._voice_lock.acquire()
        try:
            original_voice = self._snapshot_voice()
            self._apply_voice(task)
        except Exception:
            # Apply failed — restore whatever we captured and release BEFORE re-raising.
            try: self._restore_voice(original_voice)
            except Exception: pass
            self._voice_lock.release()
            raise
        target_chat = task.get("chat_target", "").strip()

        try:
            logger.info(f"[Continuity] Running '{task.get('name')}' with chat persistence, chat='{target_chat}'")

            # Find existing chat or create new one
            # Normalize the same way create_chat sanitizes: keep alnum/space/dash/underscore
            normalized = "".join(c for c in target_chat if c.isalnum() or c in (' ', '-', '_')).strip()
            normalized = normalized.replace(' ', '_').lower()
            # Guard: all-non-alnum chat_target (e.g. "!!!") normalizes to empty.
            # Proceeding would create/write to a blank-named chat file — bad
            # on-disk state and session_manager behavior for "" is undefined.
            # Fail the task loudly instead. Chaos scout #15 — 2026-04-20.
            if not normalized:
                raise ValueError(
                    f"chat_target {target_chat!r} normalizes to empty — "
                    "refusing to create/write blank-named chat."
                )
            existing_chats = {c["name"]: c["name"] for c in session_manager.list_chat_files()}
            match = existing_chats.get(normalized)
            if match:
                target_chat = match
            else:
                logger.info(f"[Continuity] Creating new chat: {target_chat}")
                # create_chat now publishes CHAT_CREATED itself (the creation
                # chokepoint), so no explicit publish here.
                session_manager.create_chat(target_chat)
                target_chat = normalized

            # Build ExecutionContext — isolated, no singleton mutation.
            # "Webhook specifies chat name" (trigger_config.chat_from_payload): the
            # named chat answers with ITS OWN settings, not the task's — so the reply
            # runs as if typed in that chat (persona/toolset/scopes come from the chat).
            # Settings are read unconditionally: the privacy stamp below needs them.
            # A read failure propagates — a task that can't see its target chat's
            # settings must not run as if it could (fail-closed, like the gate).
            chat_settings = session_manager.read_chat_settings(target_chat) or {}
            if task.get("trigger_config", {}).get("chat_from_payload"):
                from core.chat.function_manager import scope_setting_keys
                cc = chat_settings
                merged = {**task,
                          "prompt": cc.get("persona") or cc.get("prompt") or "default",
                          "toolset": cc.get("toolset") or cc.get("ability") or "none",
                          "provider": cc.get("llm_primary") or "auto",
                          "model": cc.get("llm_model") or "",
                          **{k: cc[k] for k in scope_setting_keys() if k in cc}}
                task_settings = self._extract_task_settings(merged)
            else:
                task_settings = self._extract_task_settings(task)
            # Privacy rides the CHAT here, not just the prompt (vaulted-chats
            # Phase 0, 2026-08-13). This lane is about to read the target
            # chat's whole transcript into the LLM request — before this, only
            # a privacy_required PROMPT forced local-only, so a cron/agent/
            # daemon task with chat_target=<private chat> + a cloud provider
            # shipped that chat's history out. OR the chat's flag into the
            # same carrier the prompt gate uses; ExecutionContext then gates
            # provider selection AND network tools off it.
            if chat_settings.get("private_chat"):
                task_settings["privacy_required"] = True
            ctx = ExecutionContext(
                self.system.llm_chat.function_manager,
                self.system.llm_chat.tool_engine,
                task_settings
            )

            # Set Discord reply channel for auto-reply targeting
            reply_ch = task.get("_discord_reply_channel_id")
            if reply_ch:
                try:
                    from plugins.discord.tools.discord_tools import _reply_channel_id
                    _reply_channel_id.set(reply_ch)
                except ImportError:
                    # Discord plugin not loaded at all — expected path, quiet.
                    pass
                except Exception as e:
                    # Plugin loaded but discord_tools broken in some other way.
                    # Silently ignoring would auto-reply land nowhere visible
                    # with no diagnostic. Log loudly. Chaos scout #19.
                    logger.warning(
                        f"[Continuity] Discord reply channel set failed "
                        f"(plugin loaded but broken): {e}"
                    )

            tts_enabled = task.get("tts_enabled", True)
            browser_tts = task.get("browser_tts", False)
            msg = task.get("initial_message", "Hello.")

            # Plugin event images: save bytes to the chat DB (keyed to target_chat)
            # and append <<IMG::event:id>> markers to the persisted text. The base64
            # is handed to ctx separately for the LLM turn (vision-gated); history
            # keeps only the marker. 2026-06-13.
            _ev_images = task.get("_event_images")
            if _ev_images:
                msg = msg + self._save_event_images(_ev_images, target_chat, session_manager)

            # Read history from target chat WITHOUT switching active chat.
            # The task's context_limit (when set) governs the history trim
            # too — before this, the global CONTEXT_LIMIT silently capped
            # long-session tasks (the librarian's 128K nights) at read time.
            history_messages = session_manager.read_chat_messages(
                target_chat, provider=task_settings.get("provider"),
                context_limit=task_settings.get("context_limit")
            )

            try:
                # Per-stream brain override: for the duration of the run, any
                # tool that reads/writes "this chat's" state via session_manager
                # (get_self_info, switch_model, reset_chat, set_voice, …)
                # resolves the task's own chat_target — NOT the operator's
                # active web chat. Same primitive the phone-call stream uses
                # (chat_streaming.py:175). Span is ctx.run only; the
                # persistence + TTS below never consult it. 2026-08-06 —
                # closes the daemon↔web state-leak family (a Discord reply
                # reported the web chat's model as its own).
                from core.chat import stream_brain
                _cc = session_manager.read_chat_settings(target_chat)
                _brain_token = stream_brain.set_override({
                    "chat": target_chat,
                    "settings": _cc if isinstance(_cc, dict) else {},
                    "system_prompt": ctx.system_prompt,
                    "tools": None,
                })
                try:
                    # Run through isolated ExecutionContext — no singleton contact
                    response = ctx.run(msg, history_messages=history_messages, images=_ev_images)
                finally:
                    stream_brain.reset_override(_brain_token)

                # If the run ended degraded (context overflow, tool exhaustion,
                # empty LLM, hallucinated tools), the empty assistant message
                # in ctx.new_messages is intentionally blank — Apr-24 fix kept
                # error text out of TTS / Discord / Telegram. But the chat UI
                # then renders a totally empty bubble with no signal to the
                # user. Attach degraded_reason as metadata on the empty asst
                # message so the frontend can render it as an italic system
                # note. Frontend MUST keep this out of any speak/relay paths.
                degraded = getattr(ctx, 'degraded_reason', None)
                if degraded and ctx.new_messages:
                    for _m in ctx.new_messages:
                        if _m.get("role") == "assistant" and not _m.get("content"):
                            _meta = dict(_m.get("metadata") or {})
                            _meta["degraded_reason"] = degraded
                            _m["metadata"] = _meta
                            break

                # Persist the FULL conversation (including tool calls + results)
                # to the target chat. ctx.new_messages has everything generated
                # during this run: user msg, assistant+tool_calls, tool results,
                # and the final assistant response — not just the bookends.
                # Convert wire-format tool results to canonical persistence
                # shape first — Claude / Anthropic-compat providers emit tool
                # results as `role=user` with list-content `tool_result` blocks
                # which the frontend renderer can't display. Without this
                # conversion every Claude-backed scheduled task corrupts its
                # named chat with empty user bubbles. 2026-05-11.
                if hasattr(ctx, 'new_messages') and ctx.new_messages:
                    from core.chat.chat_tool_calling import wire_to_canonical
                    canonical_messages = wire_to_canonical(ctx.new_messages)
                    appended = session_manager.append_messages_to_chat(
                        target_chat, canonical_messages)
                    if appended is False:
                        # Deleted chat or active-stream-wait timeout — the
                        # tool effects already happened; the transcript is
                        # now missing this exchange. Surface it (scout,
                        # 2026-07-20) instead of discarding the bool.
                        logger.warning(
                            f"[Continuity] Transcript append to "
                            f"'{target_chat}' FAILED — the chat is missing "
                            f"this exchange (deleted mid-run or stream-busy).")
                else:
                    # Fallback to simple pair if new_messages not available
                    session_manager.append_to_chat(target_chat, msg, response or "")

                if degraded:
                    # Surface degradation in the RESULT too — callers that
                    # gate work on the run (the librarian's drain stamps)
                    # must be able to tell "model failed" from "model
                    # declined"; success stays True for backward compat.
                    result["degraded"] = degraded
                    publish(Events.CONTINUITY_TASK_ERROR, {
                        "task": task.get("name", "Unknown"),
                        "error": degraded,
                    })

                if response_cb and response:
                    try: response_cb(response)
                    except Exception as _e: logger.error(f"[Continuity] Response callback failed: {_e}")

                if response:
                    if browser_tts:
                        publish(Events.TTS_SPEAK, {"text": response, "task": task.get("name", "")})
                    elif tts_enabled and hasattr(self.system, 'tts') and self.system.tts:
                        try:
                            self.system.tts.speak_sync(response)
                        except Exception as tts_err:
                            logger.warning(f"[Continuity] TTS failed: {tts_err}")

                result["responses"].append({
                    "iteration": 1,
                    "input": msg,
                    "output": response or None
                })
            except Exception as e:
                from core.chat.chat import friendly_llm_error
                friendly = friendly_llm_error(e)
                error_msg = f"Task failed: {friendly or e}"
                logger.error(f"[Continuity] {error_msg}", exc_info=True)
                result["errors"].append(error_msg)
                publish(Events.CONTINUITY_TASK_ERROR, {
                    "task": task.get("name", "Unknown"),
                    "error": friendly or str(e),
                })

            if progress_cb:
                progress_cb(1, 1)

            result["success"] = len(result["errors"]) == 0

        except Exception as e:
            from core.chat.chat import friendly_llm_error
            friendly = friendly_llm_error(e)
            error_msg = f"Persistent chat task failed: {friendly or e}"
            logger.error(f"[Continuity] {error_msg}", exc_info=True)
            result["errors"].append(error_msg)
            publish(Events.CONTINUITY_TASK_ERROR, {
                "task": task.get("name", "Unknown"),
                "error": friendly or str(e),
            })

        finally:
            # Voice lock is already held by this thread from the acquire above —
            # re-acquiring via `with self._voice_lock:` would deadlock against
            # ourselves (threading.Lock is NON-reentrant). Just restore + release.
            try:
                self._restore_voice(original_voice)
            except Exception as _e:
                logger.warning(f"[Continuity] _restore_voice in finally failed: {_e}")
            self._voice_lock.release()

        result["completed_at"] = datetime.now().isoformat()
        return result

    def _run_plugin_task(self, task: Dict[str, Any], progress_cb=None, response_cb=None) -> Dict[str, Any]:
        """Execute a plugin-sourced scheduled task by calling its handler."""
        from pathlib import Path
        import config

        result = {
            "success": False,
            "task_id": task.get("id"),
            "task_name": task.get("name"),
            "started_at": datetime.now().isoformat(),
            "responses": [],
            "errors": []
        }

        plugin_name = task.get("source", "").replace("plugin:", "")
        handler_path = task.get("handler", "")
        plugin_dir = task.get("plugin_dir", "")

        if not handler_path or not plugin_dir:
            result["errors"].append(f"Plugin task missing handler or plugin_dir")
            return result

        full_path = Path(plugin_dir) / handler_path
        if not full_path.exists():
            result["errors"].append(f"Handler not found: {full_path}")
            return result

        try:
            source = full_path.read_text(encoding="utf-8")
            namespace = {"__file__": str(full_path), "__name__": f"plugin_schedule_{plugin_name}"}
            exec(compile(source, str(full_path), "exec"), namespace)

            run_func = namespace.get("run")
            if not run_func or not callable(run_func):
                result["errors"].append(f"No 'run' function in {full_path}")
                return result

            # Build event dict for the handler
            from core.plugin_loader import plugin_loader
            plugin_state = plugin_loader.get_plugin_state(plugin_name)

            event = {
                "system": self.system,
                "config": config,
                "task": task,
                "plugin_state": plugin_state,
            }

            output = run_func(event)
            result["responses"].append({"output": str(output) if output else None})
            result["success"] = True

            if response_cb and output:
                try: response_cb(str(output))
                except Exception as _e: logger.error(f"[Continuity] Response callback failed: {_e}")

        except Exception as e:
            logger.error(f"[Continuity] Plugin task '{task.get('name')}' failed: {e}", exc_info=True)
            result["errors"].append(str(e))
            publish(Events.CONTINUITY_TASK_ERROR, {
                "task": task.get("name", "Unknown"),
                "error": str(e),
            })

        if progress_cb:
            progress_cb(1, 1)

        result["completed_at"] = datetime.now().isoformat()
        return result

    def _resolve_persona(self, task: Dict[str, Any]) -> Dict[str, Any]:
        """If task has a persona, merge persona settings as defaults under task-level overrides."""
        persona_name = task.get("persona", "")
        if not persona_name:
            return task

        try:
            from core.personas import persona_manager
            persona = persona_manager.get(persona_name)
            if not persona:
                logger.warning(f"[Continuity] Persona '{persona_name}' not found, skipping")
                return task

            ps = persona.get("settings", {})
            resolved = dict(task)

            # Persona provides defaults — task-level fields override.
            # Non-scope fields are static; scope fields are pulled dynamically from
            # SCOPE_REGISTRY so new plugin scopes flow through persona inheritance.
            from core.chat.function_manager import scope_setting_keys
            field_map = {
                "prompt": "prompt",
                "toolset": "toolset",
                "voice": "voice",
                "pitch": "pitch",
                "speed": "speed",
                "llm_primary": "provider",
                "llm_model": "model",
                "inject_datetime": "inject_datetime",
            }
            # Scope keys: persona key == task key (e.g. memory_scope → memory_scope)
            scope_keys = set(scope_setting_keys())
            for setting_key in scope_keys:
                field_map[setting_key] = setting_key
            # Sentinel values that mean "no preference, fall through to persona default".
            # For scope keys, 'none' is EXPLICIT opt-out ("disable this scope") and must
            # NOT fall through. 'default' is ALSO removed from the scope-key sentinel
            # list (2026-04-21) — 'default' is a real scope name where user memories
            # live, so treating it as a "please override me" sentinel meant an explicit
            # task_val='default' got silently replaced by the persona's scope. Scout
            # day-ruiner #4. Explicit 'default' now means exactly that: use the
            # default scope. Non-scope fields keep the legacy sentinel list.
            _empty_sentinels = ("", "auto", "none", "default", None)
            _empty_sentinels_scope = ("", "auto", None)  # 'none' AND 'default' excluded
            for persona_key, task_key in field_map.items():
                persona_val = ps.get(persona_key)
                task_val = resolved.get(task_key)
                if not persona_val:
                    continue
                if not task_val:
                    resolved[task_key] = persona_val
                    continue
                sentinels = _empty_sentinels_scope if task_key in scope_keys else _empty_sentinels
                if task_val in sentinels:
                    resolved[task_key] = persona_val

            logger.info(f"[Continuity] Resolved persona '{persona_name}' into task settings")
            return resolved
        except Exception as e:
            # Previously this bare except returned the raw task, which meant
            # scope keys silently fell to registry defaults ('default', a real
            # scope) downstream. That's the silent-default class. Raise loudly
            # so the caller's try/except surfaces the failure to the user
            # instead of routing writes into the wrong scope. Scout chaos #4/#9.
            logger.error(f"[Continuity] Persona resolution failed for '{persona_name}': {e}", exc_info=True)
            raise

    def _snapshot_voice(self) -> Dict[str, Any]:
        """Snapshot current TTS voice/pitch/speed for later restore."""
        tts = getattr(self.system, 'tts', None)
        if not tts:
            return {}
        try:
            return {
                "voice": getattr(tts, 'voice_name', None),
                "pitch": getattr(tts, 'pitch_shift', None),
                "speed": getattr(tts, 'speed', None),
            }
        except Exception:
            return {}

    def _validate_voice(self, voice: str) -> str:
        """Validate voice matches current TTS provider, substitute default if mismatched."""
        from core.tts.utils import validate_voice
        return validate_voice(voice)

    def _restore_voice(self, snapshot: Dict[str, Any]) -> None:
        """Restore TTS voice/pitch/speed from snapshot. Each setter gets its own
        try — a single failure can't short-circuit the others, and we log which
        one failed rather than swallowing the whole restore. Scout chaos #1/#2/#14."""
        if not snapshot:
            return
        tts = getattr(self.system, 'tts', None)
        if not tts:
            return
        for field, setter_name in (("voice", "set_voice"), ("pitch", "set_pitch"), ("speed", "set_speed")):
            val = snapshot.get(field)
            if val is None:
                continue
            try:
                setter = getattr(tts, setter_name)
                if field == "voice":
                    setter(self._validate_voice(val))
                else:
                    setter(val)
            except Exception as e:
                logger.warning(f"[Continuity] Failed to restore TTS {field}={val!r}: {e}")
        logger.debug(f"[Continuity] Restored voice settings: {snapshot}")

    def _apply_voice(self, task: Dict[str, Any]) -> None:
        """Apply voice/pitch/speed settings to TTS. Per-setter try so a
        mid-apply failure doesn't silently leave TTS half-configured — we
        RAISE on any failure so the caller (_run_foreground / _run_background)
        can restore from snapshot and mark the task errored, rather than
        continuing with a mixed voice that then persists across tasks.
        Scout chaos #1/#2/#14 — 2026-04-21."""
        tts = getattr(self.system, 'tts', None)
        if not tts:
            return
        for field, setter_name in (("voice", "set_voice"), ("pitch", "set_pitch"), ("speed", "set_speed")):
            val = task.get(field)
            # "voice" uses truthy check (empty string = unset); numeric fields
            # use is-not-None (0.0 is a legit speed-multiplier on some setups,
            # although unusual).
            if field == "voice":
                if not val:
                    continue
            else:
                if val is None:
                    continue
            try:
                setter = getattr(tts, setter_name)
                if field == "voice":
                    setter(self._validate_voice(val))
                else:
                    setter(val)
            except Exception as e:
                # Re-raise so the caller rolls back to snapshot. Logging here
                # identifies WHICH setter failed — restore path will log its
                # own recovery attempt.
                logger.error(f"[Continuity] TTS apply failed at {field}={val!r}: {e}")
                raise

    # _apply_task_settings removed — ExecutionContext handles all isolation now