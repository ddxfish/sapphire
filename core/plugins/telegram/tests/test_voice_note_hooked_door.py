"""Voice notes go through the hooked TTS door (2026-10-17).

_generate_voice used to call the raw primitive (generate_audio_data), so no
pre_tts plugin ever saw a Telegram voice note. It now calls tts.render with
the producing chat's settings: the same privacy gate as before, plus text
cleanup and pre_tts. A voice note plays on the phone, so render's default
plays_here=False keeps Settings > Voice > Play speech here out of it.

Source-level, like test_session_expiry_event: the daemon needs Telethon.
"""
from pathlib import Path


def test_voice_note_renders_through_the_hooked_door():
    body = (Path(__file__).parent.parent / "daemon.py").read_text()
    start = body.index("def _generate_voice(")
    fn = body[start:body.index("\ndef ", start + 1)]
    assert "system.tts.render(clean, chat_settings=_settings)" in fn
    assert "generate_audio_data" not in fn
    assert "plays_here" not in fn                 # elsewhere: the phone, never muted by the local switch
