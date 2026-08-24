import time

from plugins.discord.voice.voice_turn_taking_service import VoiceTurnTakingService


def test_blocks_speaking_during_overlap():
    service = VoiceTurnTakingService(min_silence_seconds=1.0, speak_cooldown_seconds=2.0)
    now = 100.0
    service.note_speech_activity('vc1', now=now)
    service.note_bot_spoke('vc1', now=now)

    assert service.may_speak('vc1', now=now + 0.5) is False


def test_allows_speak_after_silence_and_cooldown():
    service = VoiceTurnTakingService(min_silence_seconds=1.0, speak_cooldown_seconds=2.0)
    now = 100.0
    service.note_speech_activity('vc1', now=now)
    service.note_bot_spoke('vc1', now=now)

    assert service.may_speak('vc1', now=now + 3.5) is True


def test_reply_to_utterance_ignores_recent_human_activity():
    service = VoiceTurnTakingService(min_silence_seconds=1.0, speak_cooldown_seconds=2.0)
    now = 100.0
    service.note_speech_activity('vc1', now=now)
    assert service.may_reply_to_utterance('vc1', now=now + 0.1) is True
    service.note_bot_spoke('vc1', now=now)
    assert service.may_reply_to_utterance('vc1', now=now + 1.0) is False
