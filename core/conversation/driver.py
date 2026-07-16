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
                 chat_name=None, **engine_kw):
        self.system = system
        self.sample_rate = sample_rate
        self._chat_name = chat_name     # None = default chat (local/browser); set for phone calls
        self._transcribe_fn = transcribe_fn or self._whisper_transcribe
        self._sink_factory = sink_factory          # injectable; default = PumpkinChunker
        self._sink = None
        self._active_sink = None                   # set during a turn so barge-in can reach it
        self._start_word = start_word or ""        # STT start-word gate (off when empty)
        self._start_word_fuzzy = float(start_word_fuzzy)
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

    # ── the streaming turn ──────────────────────────────────────────────────
    def _run_turn(self, pcm):
        message_id = uuid.uuid4().hex
        try:
            text = self._transcribe_fn(pcm)
            if not (text and text.strip()):
                logger.info("[CONV] turn: no usable speech, skipping")
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
            try:
                armed = False    # barge-in stays blocked until AUDIO actually flows
                for event in stream.chat_stream(text):
                    et = event.get("type") if isinstance(event, dict) else None
                    # D1: arm only on tts_chunk (real audio), NOT "content". Thinking
                    # is streamed as <think>-wrapped content events with no audio, so
                    # arming on content let a caller's talk-pause-talk cadence cancel a
                    # reasoning model's turn during its silent thinking phase.
                    if not armed and et == "tts_chunk":
                        self.engine.arm_barge()   # she's speaking now — interruptible
                        armed = True
                    if et == "content":
                        publish(Events.VOICE_TURN_CHUNK,
                                {"message_id": message_id, "text": event.get("text", ""),
                                 "chat": self._chat_name, "foreign": _foreign})
                    elif et == "tts_chunk":
                        sink.feed_chunk(event)
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
        finally:
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
