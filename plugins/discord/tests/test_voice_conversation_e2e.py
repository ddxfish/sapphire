from unittest.mock import MagicMock, patch

from plugins.discord.models.voice import VoiceMode, VoiceSession
from plugins.discord.voice.discord_conversation_runner import DiscordConversationRunner
from plugins.discord.voice.voice_addressing import mentions_bot


def test_addressing_skips_undirected_speech():
    assert not mentions_bot('anyone want pizza?', ['Leona'])
    assert mentions_bot('hey Leona help', ['Leona'])


def test_runner_stop_clears_session():
    playback = MagicMock()
    runner = DiscordConversationRunner(playback_service=playback)
    session = VoiceSession(
        session_id='s1',
        account_name='bot',
        guild_id='g',
        channel_id='c',
        mode=VoiceMode.CONVERSATIONAL,
    )
    with patch('plugins.discord.voice.discord_conversation_runner._get_system') as get_system:
        get_system.return_value = MagicMock()
        with patch.object(runner, '_build_stack') as build_stack:
            build_stack.return_value = (MagicMock(), MagicMock(), MagicMock(), MagicMock())
            runner.start(session)
            assert runner.is_active('s1')
            runner.stop('s1')
            assert not runner.is_active('s1')


def test_listener_skips_batch_conversation_when_core_active():
    from plugins.discord.voice.voice_conversation_service import VoiceConversationService

    execution = MagicMock()
    service = VoiceConversationService(
        voice_execution_service=execution,
        voice_session_repository=MagicMock(),
        settings_store=MagicMock(resolve=MagicMock(return_value=MagicMock(
            voice=MagicMock(conversation_core_enabled=True),
        ))),
    )
    session = VoiceSession('s', 'a', 'g', 'c', mode=VoiceMode.CONVERSATIONAL)
    result = service.handle_transcript(session, {'status': 'transcribed', 'text': 'hi'})
    assert result['reason'] == 'core_conversation_active'
    execution.execute.assert_not_called()
