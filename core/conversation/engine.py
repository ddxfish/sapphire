"""Conversation-mode turn-state machine (v3 Rollout 2a).

PURE LOGIC. Fed `(pcm_frame, is_speech)` decisions one frame at a time, it runs
the conversation turn cycle and emits actions through two callbacks. No audio
device, no VAD model, no LLM live here — the front-door/driver injects those
(Rollout 2b), which keeps this unit-testable and identical across the browser
and local front-doors.

States
------
  IDLE          waiting for the user to start talking
  USER_SPEAKING accumulating the user's utterance until end-of-speech
  RESPONDING    STT/LLM/TTS turn running; watching for a barge-in

Transitions
-----------
  IDLE          --speech onset--------------------------------> USER_SPEAKING
  USER_SPEAKING --silence >= endpoint_silence_ms,
                  speech  >= min_speech_ms---------------------> on_turn(audio); RESPONDING
  USER_SPEAKING --endpoint but speech < min_speech_ms (blip)---> IDLE  (discard)
  RESPONDING    --speech >= barge_hold_ms, barge ARMED---------> on_barge_in(); USER_SPEAKING (new utterance)
  RESPONDING    --turn_finished() (TTS done, no barge)---------> IDLE

Barge arming (2026-07-06): entering RESPONDING DISARMS barge-in; the driver
calls arm_barge() when prose actually starts flowing (first content/tts_chunk).
Speech during LLM preprocessing or thinking is ignored — there's nothing to
interrupt yet, and firing barges there killed turns before she could answer
(a user who talk-pauses-talks in that window cancelled every turn).

Callback contract
-----------------
  on_turn(pcm_bytes)  fired at end-of-utterance. MUST be non-blocking — the
                      driver dispatches STT/LLM/TTS on its own thread and returns
                      immediately, because push_frame runs on the audio path.
  on_barge_in()       fired when the user talks over the response. MUST be quick
                      (set a cancel flag). The engine then captures the new utterance.

  turn_finished()     the driver calls this when the response (TTS) completes with
                      no barge-in, returning the engine to IDLE.
  arm_barge()         the driver calls this when the response becomes interruptible
                      (prose streaming / audio playing).
  begin_response()    external speech (a call greeting) enters RESPONDING from IDLE
                      so user speech over it barges instead of piling a turn.

PCM frames are int16 little-endian bytes; frame duration is derived from length,
so timing is deterministic (no wall clock) — feed known-size frames in tests.
"""

IDLE = "idle"
USER_SPEAKING = "user_speaking"
RESPONDING = "responding"


class ConversationEngine:
    def __init__(self, *, on_turn, on_barge_in, sample_rate=16000,
                 endpoint_silence_ms=700, min_speech_ms=200,
                 barge_hold_ms=90, max_utterance_ms=30000):
        self._on_turn = on_turn
        self._on_barge_in = on_barge_in
        self.sample_rate = sample_rate
        self.endpoint_silence_ms = endpoint_silence_ms
        self.min_speech_ms = min_speech_ms
        self.barge_hold_ms = barge_hold_ms
        self.max_utterance_ms = max_utterance_ms

        self.state = IDLE
        self._buf = []
        self._speech_ms = 0.0
        self._silence_ms = 0.0
        self._utterance_ms = 0.0
        self._barge_ms = 0.0
        self.barge_enabled = False   # armed by the driver once prose flows

    def _frame_ms(self, pcm):
        # int16 LE PCM: 2 bytes/sample
        return 1000.0 * (len(pcm) // 2) / self.sample_rate

    def _start_utterance(self, pcm):
        dur = self._frame_ms(pcm)
        self.state = USER_SPEAKING
        self._buf = [pcm]
        self._speech_ms = dur
        self._silence_ms = 0.0
        self._utterance_ms = dur
        self._barge_ms = 0.0

    def _endpoint(self):
        audio = b"".join(self._buf)
        self._buf = []
        if self._speech_ms >= self.min_speech_ms:
            self.state = RESPONDING
            self._barge_ms = 0.0
            self.barge_enabled = False   # disarmed until the driver arms on first prose
            self._on_turn(audio)
        else:
            self.state = IDLE  # too short — discard the blip

    def push_frame(self, pcm, is_speech):
        """Feed one frame of audio + its VAD decision. Drives all transitions."""
        dur = self._frame_ms(pcm)

        if self.state == IDLE:
            if is_speech:
                self._start_utterance(pcm)
            return

        if self.state == USER_SPEAKING:
            self._buf.append(pcm)
            self._utterance_ms += dur
            if is_speech:
                self._speech_ms += dur
                self._silence_ms = 0.0
            else:
                self._silence_ms += dur
            if (self._silence_ms >= self.endpoint_silence_ms
                    or self._utterance_ms >= self.max_utterance_ms):
                self._endpoint()
            return

        if self.state == RESPONDING:
            if is_speech and self.barge_enabled:
                self._barge_ms += dur
                if self._barge_ms >= self.barge_hold_ms:
                    self._on_barge_in()
                    self._start_utterance(pcm)  # capture the barge-in utterance
            else:
                # Silence OR disarmed (LLM preproc/thinking — nothing to interrupt):
                # the hold timer restarts fresh once prose arms it.
                self._barge_ms = 0.0
            return

    def begin_response(self):
        """Enter RESPONDING for externally-produced speech (a call greeting) so
        concurrent user speech is a barge-in, not a parallel turn. Only valid from
        IDLE — returns False (and does nothing) mid-utterance/mid-turn."""
        if self.state != IDLE:
            return False
        self.state = RESPONDING
        self._barge_ms = 0.0
        self.barge_enabled = False
        return True

    def arm_barge(self):
        """Driver calls this once the response is interruptible (prose streaming /
        audio playing). Until then, speech over a silent RESPONDING state is ignored."""
        self.barge_enabled = True

    def turn_finished(self):
        """Driver calls this when the RESPONDING turn (TTS) completes with no barge-in."""
        if self.state == RESPONDING:
            self.state = IDLE
            self._barge_ms = 0.0

    def reset(self):
        """Return to IDLE and drop any partial utterance (e.g. on mode exit)."""
        self.state = IDLE
        self._buf = []
        self._speech_ms = self._silence_ms = self._utterance_ms = self._barge_ms = 0.0
        self.barge_enabled = False
