"""She can always leave a voice channel: the <<HANG UP>> sentinel (2026-09-13)."""

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from plugins.discord.hooks import hangup_sentinel
from plugins.discord.sapphire.voice_prompt import (
    HANGUP_INSTRUCTION,
    HANGUP_MARKER_RE,
    build_voice_conversation_context,
)
from plugins.discord.voice.discord_conversation_runner import DiscordConversationRunner


class FakeRunner:
    def __init__(self):
        self.armed = []

    def arm_leave(self, chat_name):
        self.armed.append(chat_name)
        return chat_name == 'discord_111_222'


def _hook(monkeypatch, runner):
    monkeypatch.setattr(hangup_sentinel, '_runtime', lambda: SimpleNamespace(discord_conversation_runner=runner))


def test_hook_arms_the_runner_for_that_voice_chat(monkeypatch):
    runner = FakeRunner()
    _hook(monkeypatch, runner)
    hangup_sentinel.post_chat(SimpleNamespace(response='Bye for now! <<HANG UP>>', chat_name='discord_111_222'))
    assert runner.armed == ['discord_111_222']


def test_hook_ignores_other_chats_and_plain_replies(monkeypatch):
    runner = FakeRunner()
    _hook(monkeypatch, runner)
    hangup_sentinel.post_chat(SimpleNamespace(response='<<HANG UP>>', chat_name='default'))
    hangup_sentinel.post_chat(SimpleNamespace(response='see you later', chat_name='discord_111_222'))
    assert runner.armed == []


def test_hook_bails_loudly_without_a_stamp(monkeypatch, caplog):
    runner = FakeRunner()
    _hook(monkeypatch, runner)
    with caplog.at_level(logging.WARNING):
        hangup_sentinel.post_chat(SimpleNamespace(response='<<HANG UP>>', chat_name=None))
    assert runner.armed == []
    assert 'no chat_name' in caplog.text


def test_marker_tolerates_spacing_and_case():
    assert HANGUP_MARKER_RE.search('bye << hang_up >>')
    assert HANGUP_MARKER_RE.search('<<HANGUP>>')
    assert not HANGUP_MARKER_RE.search('hang up the phone')


def test_prompt_always_carries_the_instruction_even_under_a_custom_template():
    block = build_voice_conversation_context(bot_names=['Remmi'], prompt_template='Be brief, {primary}.')
    assert 'Be brief, Remmi.' in block
    assert HANGUP_INSTRUCTION in block


def _runner_with_session(cues=True):
    playback = MagicMock()
    playback.start.return_value = {'status': 'streaming'}
    settings = SimpleNamespace(voice=SimpleNamespace(
        conversation_core_enabled=True, max_conversation_sessions=2, addressing_mode='bot_name',
        conversation_prompt_template='', llm_provider='', llm_model='', keep_chat_history=False,
        turn_cues_enabled=cues, barge_hold_ms=250,
    ))
    store = MagicMock()
    store.resolve.return_value = settings
    voice_transport = MagicMock()
    runner = DiscordConversationRunner(playback_service=playback, settings_store=store, voice_transport=voice_transport)
    with patch('plugins.discord.voice.discord_conversation_runner._get_system') as get_system, \
            patch.object(runner, '_build_stack') as build_stack:
        get_system.return_value = MagicMock()
        driver = MagicMock()
        driver._spawn = MagicMock()
        build_stack.return_value = (driver, MagicMock(), MagicMock(), MagicMock())
        session = SimpleNamespace(session_id='sess-1', account_name='bot', guild_id='111', channel_id='222',
                                  mode='conversational')
        assert runner.start(session)['status'] == 'active'
    return runner, voice_transport


def test_runner_leaves_after_the_goodbye_drains_with_the_chime():
    runner, voice_transport = _runner_with_session(cues=True)
    left = []
    runner.leave_fn = lambda account, channel: left.append((account, channel))
    chat = runner._sessions['sess-1']['chat_name']

    assert runner.arm_leave(chat) is True
    assert runner.arm_leave('discord_999_999') is False
    import threading
    import time as _t
    # Patch the runner MODULE's `time` name (chime sleep → no-op) rather than
    # time.sleep itself — that patched this test's own poll loop too (flake).
    fake_time = SimpleNamespace(monotonic=_t.monotonic, sleep=lambda s: None)
    with patch('plugins.discord.voice.discord_conversation_runner.time', fake_time), \
            patch('plugins.discord.voice.discord_conversation_source.cue_wav_bytes', return_value=b'RIFFwav'):
        runner._note_reply_end('sess-1')                     # her final words drained
        for t in threading.enumerate():
            if t.name == 'discord-voice-hangup':
                t.join(timeout=3.0)
    assert left == [('bot', '222')]
    voice_transport.play_audio_sync.assert_called_once()
    assert runner._sessions['sess-1']['leave_after_drain'] is False


def test_runner_reply_end_without_the_sentinel_stays():
    runner, voice_transport = _runner_with_session()
    left = []
    runner.leave_fn = lambda account, channel: left.append((account, channel))
    runner._note_reply_end('sess-1')
    import time as _t
    _t.sleep(0.05)
    assert left == []
    voice_transport.play_audio_sync.assert_not_called()
