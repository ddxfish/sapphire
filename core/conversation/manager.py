"""Conversation-mode manager (v3 Rollout 2b) — "true speech mode".

Ties the driver + VAD gate + front-door to the fail-safe handoff. `start_local`
enters true speech mode using the local mic (headphone tier); `stop` exits and
restores wakeword. One manager per system; lives on the VoiceChatSystem.

The engine/gate tunables (VAD threshold, barge-hold, min-speech, endpoint-silence)
are read FRESH from settings on each `start_local`, so changing them in
Settings > Conversation takes effect on the next activation — no restart. The gate
and source_factory are injectable so this is unit-testable without silero or a mic.
"""
import logging
import threading

from core.conversation.driver import ConversationDriver

logger = logging.getLogger(__name__)


class ConversationManager:
    def __init__(self, system, gate=None, source_factory=None):
        self.system = system
        self._injected_gate = gate                 # tests inject; prod builds fresh per start
        self.driver = None                         # the OPERATOR's driver (local/browser)
        self._source_factory = source_factory or self._default_local_source
        # External sessions (phone calls) run OUTSIDE the operator's conversation
        # mode: they never touch conversation_mode_enabled or the wakeword — a call
        # doesn't contend for any local audio device, so the operator keeps their
        # ears (and can run local/browser mode concurrently). session_id -> record.
        self.external = {}
        self._external_lock = threading.Lock()     # slot-cap check races endpoint threads

    def _default_local_source(self, driver, gate):
        import config
        # CONVERSATION_DTLN picks the local audio path: "none" = headphone tier (no echo cancel),
        # "256"/"512" = duplex DTLN AEC for open speakers. OFF (none) by default. If duplex can't load
        # (commonly the onnx models are absent — they live in gitignored user/) it soft-falls-back to
        # the headphone tier so conversation mode never breaks.
        dtln = str(getattr(config, "CONVERSATION_DTLN", "none")).lower()
        if dtln in ("256", "512"):
            src = None
            try:
                from core.conversation.duplex_source import DuplexConversationSource
                delay = float(getattr(config, "CONVERSATION_AEC_DELAY_MS", 0))     # 0 off; <0 auto; >0 manual
                guard = float(getattr(config, "CONVERSATION_BARGE_GUARD_MS", 300))
                floor = float(getattr(config, "CONVERSATION_BARGE_RMS_FLOOR", 0.03))
                src = DuplexConversationSource(driver, gate, dtln_model=dtln, aec_delay_ms=delay,
                                               barge_guard_ms=guard, barge_rms_floor=floor)
                src.start()
                driver.set_sink(src)               # the SAME object is the TTS sink
                logger.info(f"[CONV] using duplex/DTLN-{dtln} audio tier")
                return src
            except Exception as e:
                logger.warning(f"[CONV] duplex/DTLN tier unavailable ({e}); falling back to headphone tier")
                if src is not None:
                    try:
                        src.close()                # release any partial duplex stream before fallback
                    except Exception:
                        pass
        # headphone tier (default, dtln=none): input-only mic, no DTLN, no model dependency
        from core.conversation.local_source import LocalMicSource
        src = LocalMicSource(driver, gate)
        src.start()                                # raises on failure -> handoff restores wakeword
        return src

    def _build_gate(self, tuning=None):
        if self._injected_gate is not None:
            return self._injected_gate
        import config
        from core.conversation.vad import SpeechGate
        t = tuning or {}
        threshold = float(t.get("vad_threshold") or
                          getattr(config, "CONVERSATION_VAD_THRESHOLD", 0.5))
        return SpeechGate(threshold=threshold)

    @property
    def active(self):
        return bool(getattr(self.system, "conversation_mode_enabled", False))

    def _build_driver(self, chat_name=None, tuning=None, tts_split=None):
        """Fresh driver from current settings so tuning applies without restart.
        chat_name targets a specific chat (phone calls); None = default (local/browser).
        `tuning` overrides individual engine knobs per session (a phone profile);
        falsy/absent values inherit the global Settings > Conversation keys.
        `tts_split` forces the TTS pump's split mode for this surface (phone =
        'sentence' so first audio doesn't wait for full generation)."""
        import config
        t = tuning or {}
        kw = {}
        if t.get("max_utterance_ms"):
            kw["max_utterance_ms"] = int(t["max_utterance_ms"])
        return ConversationDriver(
            self.system,
            chat_name=chat_name,
            tts_split=tts_split,
            start_word=str(getattr(config, "CONVERSATION_START_WORD", "")),
            start_word_fuzzy=float(getattr(config, "CONVERSATION_START_WORD_FUZZY", 0.7)),
            endpoint_silence_ms=int(t.get("endpoint_silence_ms") or
                                    getattr(config, "CONVERSATION_ENDPOINT_SILENCE_MS", 700)),
            min_speech_ms=int(t.get("min_speech_ms") or
                              getattr(config, "CONVERSATION_MIN_SPEECH_MS", 200)),
            barge_hold_ms=int(t.get("barge_hold_ms") or
                              getattr(config, "CONVERSATION_BARGE_HOLD_MS", 90)),
            **kw,
        )

    def start_local(self):
        """Enter true speech mode on the local mic. Returns True if active."""
        if self.active:
            return True
        self.driver = self._build_driver()
        gate = self._build_gate()

        def acquire():
            return self._source_factory(self.driver, gate)

        ok = self.system.enter_conversation_mode(acquire)
        logger.info(f"[CONV] start_local -> {'ON' if ok else 'failed (wakeword intact)'}")
        return ok

    def start_browser(self, send_fn):
        """Enter true speech mode fed by a connected browser WS (v3 browser endpoint).

        `send_fn(dict)` must be thread-safe and never raise — the WS route bridges
        it onto its asyncio loop. Returns the BrowserConversationSource (the route
        pumps PCM/control into it) or None if the mode couldn't start.
        """
        if self.active:
            return None
        self.driver = self._build_driver()
        gate = self._build_gate()
        from core.conversation.browser_source import BrowserConversationSource
        src = BrowserConversationSource(self.driver, gate, send_fn)

        def acquire():
            src.start()
            self.driver.set_sink(src)      # source IS the sink (duplex pattern)
            return src

        ok = self.system.enter_conversation_mode_external(acquire, source_label="browser")
        logger.info(f"[CONV] start_browser -> {'ON' if ok else 'failed'}")
        return src if ok else None

    def start_external(self, source_ctor, chat_name=None, source_label="external",
                       session_id=None, tuning=None, tts_split=None):
        """Start an external conversation session (a phone call). Each session gets
        its OWN driver + gate + source — N sessions run concurrently up to the slot
        cap (CONVERSATION_EXTERNAL_SLOTS). `source_ctor(driver, gate)` builds a
        source that is ALSO the TTS sink (duplex pattern) and must be `.start()`-ed
        by the ctor. Returns the source or None (slot cap / build failure).
        `tuning` (optional dict: vad_threshold / endpoint_silence_ms / min_speech_ms /
        barge_hold_ms / max_utterance_ms) overrides the global conversation knobs
        for THIS session only — the caller's audio profile (phone lines are noisier
        than a desk mic). Falsy entries inherit the global settings.

        Unlike the operator path this never touches conversation_mode_enabled or
        the wakeword — a call brings its own transport and contends for nothing
        local. End it with stop_external(session_id)."""
        import config
        import time
        import uuid
        cap = int(getattr(config, "CONVERSATION_EXTERNAL_SLOTS", 2))
        sid = session_id or uuid.uuid4().hex[:12]
        with self._external_lock:
            if len(self.external) >= cap:
                logger.warning(f"[CONV] start_external({source_label}) refused — "
                               f"{len(self.external)}/{cap} slots in use")
                return None
            # A2: serialize per chat. Two live sessions on one chat each seed from a
            # start-of-session snapshot and persist the WHOLE messages column, so the
            # last writer silently erases the other's turns. One live stream per chat
            # name; a second caller targeting an already-live chat is refused here and
            # rejected by the caller like a busy line.
            if chat_name and any(r.get("chat") == chat_name for r in self.external.values()):
                logger.warning(f"[CONV] start_external({source_label}) refused — "
                               f"chat '{chat_name}' already has a live session")
                return None
            driver = self._build_driver(chat_name=chat_name, tuning=tuning,
                                        tts_split=tts_split)
            gate = self._build_gate(tuning=tuning)
            try:
                src = source_ctor(driver, gate)
                driver.set_sink(src)       # source IS the sink (duplex pattern)
            except Exception as e:
                logger.error(f"[CONV] start_external({source_label}) source build failed: {e}")
                return None
            self.external[sid] = {"driver": driver, "src": src, "chat": chat_name,
                                  "label": source_label, "started": time.time()}
        logger.info(f"[CONV] start_external({source_label}) -> ON "
                    f"(session {sid}, {len(self.external)}/{cap} slots)")
        return src

    def stop_external(self, session_id):
        """End one external session: close its source, reset its driver (idempotent)."""
        with self._external_lock:
            rec = self.external.pop(session_id, None)
        if rec is None:
            return
        try:
            close = getattr(rec["src"], "close", None)
            if callable(close):
                close()
        except Exception as e:
            logger.warning(f"[CONV] external session {session_id} close error: {e}")
        rec["driver"].reset()
        logger.info(f"[CONV] external session {session_id} ended "
                    f"({len(self.external)} still active)")

    def external_chats(self):
        """Chat names owned by live external sessions (phone calls). Web-origin
        stop/cancel must leave these chats' streams alone even when the operator
        is viewing one (viewing makes it the ACTIVE chat — chat-scoping alone
        can't tell the surfaces apart)."""
        with self._external_lock:
            return {rec.get("chat") for rec in self.external.values() if rec.get("chat")}

    def stop(self):
        """Exit the OPERATOR's true speech mode and restore wakeword (idempotent).
        External sessions (phone calls) are untouched — they end individually via
        stop_external()."""
        self.system.exit_conversation_mode()
        if self.driver is not None:
            self.driver.reset()
