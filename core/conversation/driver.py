"""Conversation-mode turn driver (v3 Rollout 2b — STREAMING).

Bridges the pure-logic ConversationEngine to Sapphire's streaming pipeline:
  on_turn(pcm)   -> STT (whisper) -> drive chat_stream():
                      content   -> event bus (VOICE_TURN_CHUNK)  [web UI streams in]
                      tts_chunk -> PumpkinChunker                [local audio streams out]
                    -> wait for audio to finish -> engine.turn_finished()
  on_barge_in()  -> cancel_generation() (halts chat_stream) + sink.stop() (cuts audio)

Routing voice through chat_stream (instead of the blocking chat()) is what makes
the reply stream to the UI AND makes the LLM cancellable for real barge-in.

NOTE: local audio depends on TTS streaming being enabled (chat_stream's pump only
emits tts_chunk events then). With it off, the UI still streams but she's silent —
a fallback (tts.speak final) is a later add. STT/stream/sink are injectable for tests.
"""
import difflib
import logging
import os
import re
import tempfile
import threading
import uuid
import wave

from core.conversation.engine import ConversationEngine
from core.event_bus import publish, Events

logger = logging.getLogger(__name__)


def _norm(s):
    return re.sub(r"[^\w\s]", "", s.lower()).strip()


def match_start_word(text, phrases_csv, threshold=0.7):
    """STT-based start-word gate (no wakeword pause). `phrases_csv` = comma-separated start phrases
    (e.g. "hey sapphire, sapphire"). If `text` fuzzily begins with any phrase, return the remainder
    with that prefix stripped (may be ""). Return None if none match. Empty phrases_csv = feature
    OFF -> returns `text` unchanged so the caller doesn't gate."""
    phrases = [p.strip() for p in (phrases_csv or "").split(",") if p.strip()]
    if not phrases:
        return text                                # feature off
    words = text.split()
    best_ratio, best_strip = 0.0, 0
    for phrase in phrases:
        pn = _norm(phrase)
        base = len(pn.split())
        if base == 0:
            continue
        # Try a few leading-word windows so STT splitting one word into two still matches
        # (e.g. "sapphire" -> "staff fire" makes a 2-word phrase span 3 leading words).
        for k in range(max(1, base - 1), base + 3):
            if k > len(words):
                break
            r = difflib.SequenceMatcher(None, _norm(" ".join(words[:k])), pn).ratio()
            if r > best_ratio:
                best_ratio, best_strip = r, k
    if best_ratio >= threshold:
        return " ".join(words[best_strip:]).strip()  # strip the matched prefix
    return None                                      # no phrase matched -> gate


class ConversationDriver:
    def __init__(self, system, transcribe_fn=None, sink_factory=None,
                 sample_rate=16000, start_word="", start_word_fuzzy=0.7,
                 chat_name=None, tts_split=None, **engine_kw):
        self.system = system
        self.sample_rate = sample_rate
        self._chat_name = chat_name     # None = default chat (local/browser); set for phone calls
        self._tts_split = (tts_split or "").strip().lower() or None   # per-surface pump split mode
        self._cue_fn = None             # optional turn-cue player (v2.9 soundscape)
        self._llm_timeout = 0.0         # >0 arms the one-shot silent regen on first-token timeout
        self._transcribe_fn = transcribe_fn or self._whisper_transcribe
        self._sink_factory = sink_factory          # injectable; default = PumpkinChunker
        self._sink = None
        self._active_sink = None                   # set during a turn so barge-in can reach it
        self._start_word = start_word or ""        # STT start-word gate (off when empty)
        self._start_word_fuzzy = float(start_word_fuzzy)
        self._privacy_gate_logged = False          # one loud log per session, not per turn
        self.engine = ConversationEngine(
            on_turn=self._on_turn,
            on_barge_in=self._on_barge_in,
            sample_rate=sample_rate,
            **engine_kw,
        )

    # ── front-door entry ────────────────────────────────────────────────────
    def push_frame(self, pcm, is_speech):
        self.engine.push_frame(pcm, is_speech)

    def reset(self):
        self.engine.reset()

    def set_sink(self, sink):
        """Use an externally-built sink. The duplex source is its own sink (one stream,
        both directions), so the manager wires it here instead of building a PumpkinChunker."""
        self._sink = sink

    def set_cues(self, fn):
        """Optional turn-cue player: fn(name) with name in {'think','barge','error'}.
        think = still working (fires ~1/s from capture until her first audio),
        barge = user interrupted and the floor is theirs,
        error = the turn failed (canned spoken apology). Wired per-surface
        (phone today; conversation/wakeword modes join in the v2.9 soundscape)."""
        self._cue_fn = fn

    def set_llm_timeout(self, seconds):
        """Per-surface LLM deadline (phone today). The actual read timeout rides
        the call chat's `llm_request_timeout` setting into the provider client;
        this arms the matching one-shot SILENT retry on the stream — the caller
        hears a quick triple think-tick, never an apology, unless the retry
        also dies (then the 'error' cue speaks as usual)."""
        self._llm_timeout = float(seconds or 0)

    def _retry_cue(self):
        for _ in range(3):              # triple-tick: the line's alive, still working
            self._cue("think")

    def _cue(self, name):
        if self._cue_fn is None:
            return
        try:
            self._cue_fn(name)
        except Exception as e:
            logger.debug(f"[CONV] cue '{name}' failed: {e}")

    # ── engine callbacks ────────────────────────────────────────────────────
    def _on_turn(self, pcm):
        self._spawn(self._run_turn, pcm)           # non-blocking: STT/LLM/TTS off the audio path

    def _on_barge_in(self):
        logger.info("[CONV] barge-in fired -> cancelling LLM + cutting audio")
        try:
            # Scope the cancel to THIS conversation's chat — an unscoped cancel
            # kills every live stream in the system (a phone barge-in would
            # cancel a concurrent web-UI reply). None = active chat (local/browser).
            self.system.cancel_generation(chat_name=self._chat_name)
        except Exception as e:
            logger.warning(f"[CONV] barge-in cancel_generation failed: {e}")
        sink = self._active_sink
        if sink is not None:
            try:
                sink.stop()                        # cut local audio now
            except Exception as e:
                logger.warning(f"[CONV] barge-in sink.stop failed: {e}")
        self._cue("barge")                         # floor's yours — she heard you

    # ── the streaming turn ──────────────────────────────────────────────────
    def _run_turn(self, pcm):
        # Voice privacy gate (vault v1.1): a private chat refuses cloud STT
        # (voice in) AND cloud TTS (speech out). Either leak kills the whole
        # turn — a half-voice call is a dead line, not privacy.
        try:
            if self._chat_name:
                _settings = self.system.llm_chat.session_manager.get_settings_for(self._chat_name) or {}
            else:
                _settings = self.system.llm_chat.session_manager.get_chat_settings() or {}
        except Exception:
            _settings = {}
        try:
            from core.voice_privacy import stt_gate_reason, tts_gate_reason
            _gate = stt_gate_reason(_settings) or tts_gate_reason(_settings)
        except Exception:
            _gate = ''
        if _gate:
            if not self._privacy_gate_logged:
                logger.warning(f"[CONV] {_gate} — voice turns disabled for this session")
                self._privacy_gate_logged = True
            return

        message_id = uuid.uuid4().hex
        # Turn cues: a soft think-pulse every second until her audio starts
        # flowing — fills the STT+LLM dead air that reads as a hung line on a
        # phone call. (No capture-tick: it sat too close to the first pulse to
        # read as a separate signal — Krem's live-call verdict 2026-07-15.)
        pulse_stop = threading.Event()
        if self._cue_fn is not None:
            def _pulse():
                while not pulse_stop.wait(1.0):
                    self._cue("think")
            threading.Thread(target=_pulse, daemon=True, name="conv-think-pulse").start()
        try:
            text = self._transcribe_fn(pcm)
            if not (text and text.strip()):
                logger.info("[CONV] turn: no usable speech, skipping")
                return
            # post_stt hook — same contract as the wakeword and /api/stt
            # lanes (H8 class): plugins can correct/translate/normalize the
            # transcription. Conversation mode was the only STT lane that
            # bypassed it. Fires before start-word matching so a correction
            # can rescue a mis-heard start word.
            from core.hooks import hook_runner, HookEvent
            if hook_runner.has_handlers("post_stt"):
                import config as _config
                stt_event = HookEvent(input=text, config=_config,
                                      metadata={"system": self.system})
                hook_runner.fire("post_stt", stt_event)
                text = stt_event.input
                if not (text and text.strip()):
                    logger.info("[CONV] turn: post_stt hook emptied the utterance, skipping")
                    return
            gated = match_start_word(text, self._start_word, self._start_word_fuzzy)
            if gated is None:
                logger.info("[CONV] turn: start word not matched, ignoring utterance")
                return
            text = gated.strip()
            if not text:
                logger.info("[CONV] turn: start word only, nothing to act on")
                return
            logger.info("[CONV] turn: transcribed user utterance -> streaming")

            sink = self._ensure_sink()
            sink.start()
            self._active_sink = sink
            # `foreign`: this turn runs in an explicit non-active chat (a phone
            # call's side chat) — the web UI must NOT render it into whatever
            # chat is being viewed (it streamed in, then vanished on reconcile).
            try:
                _active = self.system.llm_chat.session_manager.get_active_chat_name()
            except Exception:
                _active = None
            _foreign = bool(self._chat_name and self._chat_name != _active)
            publish(Events.VOICE_TURN_START, {"message_id": message_id, "user_text": text,
                                              "chat": self._chat_name, "foreign": _foreign})

            stream, sid, chat = self.system.llm_chat.begin_stream(self._chat_name)
            if self._tts_split:
                # Phone surface: force the sentence-split pump so the first
                # sentence synthesizes while the rest still generates. Inert
                # when TTS streaming is globally off (pump stays disabled).
                stream.tts_split_override = self._tts_split
            if self._llm_timeout > 0:
                # One silent regen if the provider never sends a first token
                # before the read timeout — the caller only hears the tick.
                stream.timeout_retry = 1
                stream.on_timeout_retry = self._retry_cue
            try:
                armed = False    # barge-in stays blocked until AUDIO actually flows
                for event in stream.chat_stream(text):
                    et = event.get("type") if isinstance(event, dict) else None
                    # D1: arm only on tts_chunk (real audio), NOT "content". Thinking
                    # is streamed as <think>-wrapped content events with no audio, so
                    # arming on content let a caller's talk-pause-talk cadence cancel a
                    # reasoning model's turn during its silent thinking phase.
                    if not armed and et == "tts_chunk":
                        pulse_stop.set()          # her voice takes over from the pulse
                        self.engine.arm_barge()   # she's speaking now — interruptible
                        armed = True
                    if et == "content":
                        publish(Events.VOICE_TURN_CHUNK,
                                {"message_id": message_id, "text": event.get("text", ""),
                                 "chat": self._chat_name, "foreign": _foreign})
                    elif et == "tts_chunk":
                        sink.feed_chunk(event)
                    elif et == "error":
                        # chat_stream signals some faults as yielded events, not
                        # raises (e.g. private-prompt block). Surface them like
                        # any other turn failure instead of ending in silence.
                        raise RuntimeError(event.get("text") or "stream error event")
                    if getattr(stream, "cancel_flag", False):
                        break
            finally:
                self.system.llm_chat.end_stream(sid, chat)

            sink.finish()
            self._wait_sink(sink)                  # stay RESPONDING until audio finishes
            publish(Events.VOICE_TURN_END, {"message_id": message_id,
                                            "chat": self._chat_name, "foreign": _foreign})
        except Exception as e:
            logger.error(f"[CONV] streaming turn failed: {e}")
            # Spoken failure cue: a dead provider must not read as a hung line.
            # Surfaces that wired cues (phone) play a canned "sorry, I glitched"
            # — pre-synthesized, so it works even when TTS is the thing that died.
            self._cue("error")
        finally:
            pulse_stop.set()
            self._active_sink = None
            self.engine.turn_finished()            # no-op if a barge-in already moved us on

    # ── external speech (call greetings) ────────────────────────────────────
    def speak_direct(self, audio_bytes, on_begin=None):
        """Play pre-synthesized audio as an engine-aware pseudo-turn. The engine
        holds RESPONDING while it plays (barge armed), so a listener talking over
        it interrupts cleanly instead of spawning a parallel turn. Blocks until
        the audio drains or a barge-in cuts it. Returns False if the engine was
        already mid-turn (audio is skipped — never stomp a live utterance).
        `on_begin` fires once the engine commits, before the first frame plays —
        the caller's hook for recording what is about to be said."""
        import base64
        if not audio_bytes:
            return False
        if not self.engine.begin_response():
            logger.info("[CONV] speak_direct skipped — engine not idle")
            return False
        if on_begin is not None:
            try:
                on_begin()
            except Exception as e:
                logger.warning(f"[CONV] speak_direct on_begin failed: {e}")
        sink = self._ensure_sink()
        sink.start()
        self._active_sink = sink
        self.engine.arm_barge()
        try:
            sink.feed_chunk({"audio_b64": base64.b64encode(audio_bytes).decode()})
            sink.finish()
            self._wait_sink(sink)
        finally:
            self._active_sink = None
            self.engine.turn_finished()    # no-op if a barge-in already moved us on
        return True

    # ── sink ────────────────────────────────────────────────────────────────
    def _ensure_sink(self):
        if self._sink is None:
            if self._sink_factory is not None:
                self._sink = self._sink_factory()
            else:
                from core.tts.pumpkin_chunker import PumpkinChunker
                tts = getattr(self.system, "tts", None)
                self._sink = PumpkinChunker(
                    output_device=getattr(tts, "output_device", None),
                    output_rate=getattr(tts, "output_rate", None) or 48000,
                )
        return self._sink

    def _wait_sink(self, sink, timeout=180):
        waiter = getattr(sink, "wait", None)   # duplex sink: poll-drain (no per-turn worker)
        if callable(waiter):
            waiter(timeout=timeout)
            return
        w = getattr(sink, "_worker", None)     # PumpkinChunker: join the playback worker
        if w is not None and hasattr(w, "join"):
            w.join(timeout=timeout)

    # ── helpers ─────────────────────────────────────────────────────────────
    def _spawn(self, target, *args):
        threading.Thread(target=target, args=args, daemon=True).start()

    def _whisper_transcribe(self, pcm):
        wc = getattr(self.system, "whisper_client", None)
        if wc is None:
            return None
        fd, path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        try:
            with wave.open(path, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(self.sample_rate)
                w.writeframes(pcm)
            return wc.transcribe_file(path)
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass
