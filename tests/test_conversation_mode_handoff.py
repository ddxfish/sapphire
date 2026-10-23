"""Rollout-1 ACCEPTANCE TEST — conversation-mode handoff is fail-safe.

The non-negotiable guarantee: a conversation-mode audio failure must NEVER break
the wakeword pipeline. We inject success / failing / None acquire stubs and assert
the wakeword recorder + detector end in the right state.

This runs on Linux, but the guarantee is pure control flow (no real audio), so a
green here means a Windows/WASAPI conversation-audio failure degrades to "feature
unavailable," never a deaf Sapphire. That's exactly why we can build without
testing Windows first.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sapphire import VoiceChatSystem


def _fake_system():
    """A VoiceChatSystem stand-in carrying only what the handoff touches, with the
    real methods bound to it. wake_detector is a plain mock (NOT a
    NullWakeWordDetector), so the 'wakeword active' guard passes."""
    s = MagicMock()
    s.conversation_mode_enabled = False
    s.conversation_session = None
    s.wake_detector = MagicMock()
    s.wake_word_recorder = MagicMock()
    s.wake_word_recorder.start_recording.return_value = True
    s.enter_conversation_mode = VoiceChatSystem.enter_conversation_mode.__get__(s)
    s.exit_conversation_mode = VoiceChatSystem.exit_conversation_mode.__get__(s)
    s._restore_wakeword = VoiceChatSystem._restore_wakeword.__get__(s)
    return s


def test_failed_acquire_restores_wakeword():
    """Audio init raises (the WASAPI-dragon case) -> wakeword restored, mode off."""
    s = _fake_system()

    def boom():
        raise RuntimeError("WASAPI duplex clock failed")

    ok = s.enter_conversation_mode(boom)
    assert ok is False
    assert s.conversation_mode_enabled is False
    # released the mic...
    s.wake_detector.stop_listening.assert_called()
    s.wake_word_recorder.stop_recording.assert_called()
    # ...then RESTORED it (the fail-safe)
    s.wake_word_recorder.start_recording.assert_called()
    s.wake_detector.start_listening.assert_called()


def test_none_acquire_restores_wakeword():
    """acquire returns None (opened but dead) -> treated as failure, wakeword restored."""
    s = _fake_system()
    ok = s.enter_conversation_mode(lambda: None)
    assert ok is False
    assert s.conversation_mode_enabled is False
    s.wake_word_recorder.start_recording.assert_called()
    s.wake_detector.start_listening.assert_called()


def test_success_suppresses_then_exit_restores():
    """acquire succeeds -> wakeword suppressed; exit -> session closed + wakeword back."""
    s = _fake_system()
    session = MagicMock()

    ok = s.enter_conversation_mode(lambda: session)
    assert ok is True
    assert s.conversation_mode_enabled is True
    assert s.conversation_session is session
    s.wake_detector.stop_listening.assert_called()
    s.wake_word_recorder.stop_recording.assert_called()
    # success path must NOT have restarted the wakeword (it's yielded now)
    s.wake_word_recorder.start_recording.assert_not_called()

    s.exit_conversation_mode()
    assert s.conversation_mode_enabled is False
    assert s.conversation_session is None
    session.close.assert_called()
    # exit restores the wakeword
    s.wake_word_recorder.start_recording.assert_called()
    s.wake_detector.start_listening.assert_called()


def test_enter_when_already_active_is_noop_true():
    s = _fake_system()
    s.conversation_mode_enabled = True
    assert s.enter_conversation_mode(lambda: MagicMock()) is True
    # must not touch the wakeword if already in conversation mode
    s.wake_detector.stop_listening.assert_not_called()


def test_exit_when_inactive_is_noop():
    s = _fake_system()
    s.exit_conversation_mode()  # should not raise / not touch wakeword
    s.wake_word_recorder.start_recording.assert_not_called()
