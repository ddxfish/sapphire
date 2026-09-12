"""Conservative turn-taking for spoken participation."""

from __future__ import annotations

import time


class VoiceTurnTakingService:
    def __init__(self, *, min_silence_seconds: float = 1.0, speak_cooldown_seconds: float = 2.0):
        self.min_silence_seconds = max(0.0, float(min_silence_seconds))
        self.speak_cooldown_seconds = max(0.0, float(speak_cooldown_seconds))
        self._last_human_speech = {}
        self._last_bot_spoke = {}

    def note_speech_activity(self, channel_id: str, *, now: float | None = None) -> None:
        self._last_human_speech[str(channel_id)] = now if now is not None else time.time()

    def note_bot_spoke(self, channel_id: str, *, now: float | None = None) -> None:
        self._last_bot_spoke[str(channel_id)] = now if now is not None else time.time()

    def may_speak(self, channel_id: str, *, now: float | None = None) -> bool:
        now = now if now is not None else time.time()
        channel_id = str(channel_id)
        last_human = self._last_human_speech.get(channel_id, 0.0)
        last_bot = self._last_bot_spoke.get(channel_id, 0.0)
        if now - last_human < self.min_silence_seconds:
            return False
        if now - last_bot < self.speak_cooldown_seconds:
            return False
        return True

    def may_reply_to_utterance(self, channel_id: str, *, now: float | None = None) -> bool:
        """After a completed user utterance, only enforce bot speak cooldown."""
        now = now if now is not None else time.time()
        channel_id = str(channel_id)
        last_bot = self._last_bot_spoke.get(channel_id, 0.0)
        return now - last_bot >= self.speak_cooldown_seconds
