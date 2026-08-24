from unittest.mock import AsyncMock, MagicMock, patch

from plugins.discord.models.voice import VoiceMode, VoiceSession
from plugins.discord.voice.discord_conversation_runner import DiscordConversationRunner


def _session(session_id='sess-1', guild_id='111', channel_id='222'):
    return VoiceSession(
        session_id=session_id,
        account_name='bot',
        guild_id=guild_id,
        channel_id=channel_id,
        mode=VoiceMode.CONVERSATIONAL,
    )


def test_runner_start_and_stop():
    playback = MagicMock()
    playback.start.return_value = {'status': 'streaming'}
    runner = DiscordConversationRunner(playback_service=playback, settings_store=None)

    with patch('plugins.discord.voice.discord_conversation_runner._get_system') as get_system:
        system = MagicMock()
        system.llm_chat.create_chat.return_value = True
        get_system.return_value = system

        with patch.object(runner, '_build_stack') as build_stack:
            driver = MagicMock()
            gate = MagicMock()
            source = MagicMock()
            frame_feed = MagicMock()
            build_stack.return_value = (driver, gate, source, frame_feed)

            result = runner.start(_session())
            assert result['status'] == 'active'
            assert runner.is_active('sess-1')
            source.start.assert_called_once_with()

            stop = runner.stop('sess-1')
            assert stop['status'] == 'stopped'
            assert not runner.is_active('sess-1')
            source.close.assert_called_once()
            driver.reset.assert_called_once()


def test_runner_slot_cap():
    playback = MagicMock()
    settings = MagicMock()
    settings.voice.conversation_core_enabled = True
    settings.voice.max_conversation_sessions = 1
    store = MagicMock()
    store.resolve.return_value = settings
    runner = DiscordConversationRunner(playback_service=playback, settings_store=store)

    with patch('plugins.discord.voice.discord_conversation_runner._get_system') as get_system:
        get_system.return_value = MagicMock()
        with patch.object(runner, '_build_stack') as build_stack:
            build_stack.return_value = (MagicMock(), MagicMock(), MagicMock(), MagicMock())
            assert runner.start(_session())['status'] == 'active'
            assert runner.start(_session('sess-2', '333', '444'))['status'] == 'error'


def test_runner_start_async_uses_async_playback():
    import asyncio

    playback = MagicMock()
    runner = DiscordConversationRunner(playback_service=playback, settings_store=None)

    async def run():
        with patch('plugins.discord.voice.discord_conversation_runner._get_system') as get_system:
            system = MagicMock()
            system.llm_chat.create_chat.return_value = True
            get_system.return_value = system

            with patch.object(runner, '_build_stack') as build_stack:
                driver = MagicMock()
                gate = MagicMock()
                source = MagicMock()
                source.start_playback_async = AsyncMock(return_value={'status': 'streaming'})
                frame_feed = MagicMock()
                build_stack.return_value = (driver, gate, source, frame_feed)

                result = await runner.start_async(_session())
                assert result['status'] == 'active'
                source.start_playback_async.assert_awaited_once()
                source.start.assert_called_once_with(start_playback=False)

    asyncio.run(run())


def test_runner_submit_turn_text_uses_pending_text():
    playback = MagicMock()
    playback.start.return_value = {'status': 'streaming'}
    runner = DiscordConversationRunner(playback_service=playback, settings_store=None)

    with patch('plugins.discord.voice.discord_conversation_runner._get_system') as get_system:
        system = MagicMock()
        system.llm_chat.create_chat.return_value = True
        get_system.return_value = system

        with patch.object(runner, '_build_stack') as build_stack:
            driver = MagicMock()
            driver._transcribe_fn = MagicMock()
            driver._spawn = MagicMock()
            gate = MagicMock()
            source = MagicMock()
            frame_feed = MagicMock()
            build_stack.return_value = (driver, gate, source, frame_feed)

            assert runner.start(_session())['status'] == 'active'
            runner._sessions['sess-1']['bot_names'] = ['Remmi']

            submit = runner.submit_turn_text('sess-1', 'Hey Remmi, hello')
            assert submit['status'] == 'submitted'
            assert driver._discord_pending_text == 'Hey Remmi, hello'
            driver._spawn.assert_called_once()


def test_runner_interrupt_skips_when_idle():
    playback = MagicMock()
    runner = DiscordConversationRunner(playback_service=playback, settings_store=None)

    with patch('plugins.discord.voice.discord_conversation_runner._get_system') as get_system:
        get_system.return_value = MagicMock()
        with patch.object(runner, '_build_stack') as build_stack:
            driver = MagicMock()
            driver._chat_name = 'discord_111_222'
            driver._active_sink = None
            driver.engine.state = 'IDLE'
            source = MagicMock()
            source._playing = False
            build_stack.return_value = (driver, MagicMock(), source, MagicMock())

            assert runner.start(_session())['status'] == 'active'
            assert runner.interrupt_active_turn('sess-1') is False

            driver.system.cancel_generation.assert_not_called()
            source.interrupt_playback.assert_not_called()


def test_runner_interrupt_active_turn_cancels_responding():
    playback = MagicMock()
    runner = DiscordConversationRunner(playback_service=playback, settings_store=None)

    with patch('plugins.discord.voice.discord_conversation_runner._get_system') as get_system:
        get_system.return_value = MagicMock()
        with patch.object(runner, '_build_stack') as build_stack:
            driver = MagicMock()
            driver._chat_name = 'discord_111_222'
            driver._active_sink = MagicMock()
            driver.engine.state = 'IDLE'
            source = MagicMock()
            build_stack.return_value = (driver, MagicMock(), source, MagicMock())

            assert runner.start(_session())['status'] == 'active'
            assert runner.interrupt_active_turn('sess-1') is True

            driver.system.cancel_generation.assert_called_once_with(chat_name='discord_111_222')
            source.interrupt_playback.assert_called_once()
            driver.engine.turn_finished.assert_called_once()


def test_runner_stop_command_halts_without_new_turn():
    playback = MagicMock()
    runner = DiscordConversationRunner(playback_service=playback, settings_store=None)

    with patch('plugins.discord.voice.discord_conversation_runner._get_system') as get_system:
        get_system.return_value = MagicMock()
        with patch.object(runner, '_build_stack') as build_stack:
            driver = MagicMock()
            driver._spawn = MagicMock()
            build_stack.return_value = (driver, MagicMock(), MagicMock(), MagicMock())

            assert runner.start(_session())['status'] == 'active'
            runner._sessions['sess-1']['bot_names'] = ['Remmi']

            with patch.object(runner, 'interrupt_active_turn', return_value=True) as interrupt:
                result = runner.submit_turn_text('sess-1', 'Remi, stop talking please')

            assert result['status'] == 'stopped'
            interrupt.assert_called_once_with('sess-1')
            driver._spawn.assert_not_called()
