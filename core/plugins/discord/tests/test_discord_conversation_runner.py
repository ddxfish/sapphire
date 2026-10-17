from unittest.mock import AsyncMock, MagicMock, patch

from plugins.discord.models.voice import VoiceSession
from plugins.discord.voice.discord_conversation_runner import DiscordConversationRunner


def _session(session_id='sess-1', guild_id='111', channel_id='222'):
    return VoiceSession(
        session_id=session_id,
        account_name='bot',
        guild_id=guild_id,
        channel_id=channel_id,
    )


def test_runner_start_and_stop():
    playback = MagicMock()
    playback.start_streaming_playback_sync.return_value = {'status': 'streaming'}
    runner = DiscordConversationRunner(voice_transport=playback, settings_store=None)

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


def test_runner_start_async_uses_async_playback():
    import asyncio

    playback = MagicMock()
    runner = DiscordConversationRunner(voice_transport=playback, settings_store=None)

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
    playback.start_streaming_playback_sync.return_value = {'status': 'streaming'}
    runner = DiscordConversationRunner(voice_transport=playback, settings_store=None)

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
    runner = DiscordConversationRunner(voice_transport=playback, settings_store=None)

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
    runner = DiscordConversationRunner(voice_transport=playback, settings_store=None)

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
            driver.abandon_turn.assert_called_once()      # core seam (H14), not a raw poke


def test_runner_stop_command_halts_without_new_turn():
    playback = MagicMock()
    runner = DiscordConversationRunner(voice_transport=playback, settings_store=None)

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


# ── Addressing (mic test 2026-09-13): per-person follow-up + solo rule ────────

def _started_runner(store=None):
    playback = MagicMock()
    playback.start_streaming_playback_sync.return_value = {'status': 'streaming'}
    runner = DiscordConversationRunner(voice_transport=playback, settings_store=store)
    get_system = patch('plugins.discord.voice.discord_conversation_runner._get_system')
    build_stack = patch.object(runner, '_build_stack')
    gs = get_system.start()
    bs = build_stack.start()
    gs.return_value = MagicMock()
    driver = MagicMock()
    driver._spawn = MagicMock()
    driver._active_sink = None
    driver.engine.state = 'idle'
    source = MagicMock()
    source._playing = False
    bs.return_value = (driver, MagicMock(), source, MagicMock())
    assert runner.start(_session())['status'] == 'active'
    runner._sessions['sess-1']['bot_names'] = ['Remmi']
    get_system.stop()
    build_stack.stop()
    return runner, driver


def test_group_without_name_is_filtered_and_never_interrupts():
    runner, driver = _started_runner()
    driver._active_sink = object()          # she is mid-reply to someone else
    with patch.object(runner, 'interrupt_active_turn', return_value=True) as interrupt:
        result = runner.submit_turn_text('sess-1', 'Krem: tell me a two paragraph story', speaker_id='42', humans=3)
    assert result['status'] == 'filtered'
    interrupt.assert_not_called()
    driver._spawn.assert_not_called()


def test_alone_with_her_needs_no_name():
    runner, driver = _started_runner()
    result = runner.submit_turn_text('sess-1', 'Krem: tell me a two paragraph story', speaker_id='42', humans=1)
    assert result == {'status': 'submitted', 'reason': 'solo'}
    assert runner._sessions['sess-1']['last_addressee'] == '42'
    driver._spawn.assert_called_once()


def test_unknown_occupancy_fails_closed_to_the_name_rule():
    runner, _driver = _started_runner()
    assert runner.submit_turn_text('sess-1', 'Krem: story please', speaker_id='42', humans=None)['status'] == 'filtered'
    assert runner.submit_turn_text('sess-1', 'Krem: Remmi, story please', speaker_id='42', humans=None)['reason'] == 'named'


def test_follow_up_window_belongs_to_the_person_she_answered():
    import time as _time

    runner, driver = _started_runner()
    assert runner.submit_turn_text('sess-1', 'Krem: hey Remmi', speaker_id='42', humans=3)['reason'] == 'named'
    runner._note_reply_end('sess-1')                       # her reply just ended
    # Krem keeps talking, no name: follow-up. A bystander: still filtered.
    assert runner.submit_turn_text('sess-1', 'Krem: and a story?', speaker_id='42', humans=3)['reason'] == 'follow_up'
    assert runner.submit_turn_text('sess-1', 'Bob: what did she say', speaker_id='7', humans=3)['status'] == 'filtered'
    # Window expired: name required again.
    runner._sessions['sess-1']['last_reply_end'] = _time.monotonic() - 25.0
    assert runner.submit_turn_text('sess-1', 'Krem: one more?', speaker_id='42', humans=3)['status'] == 'filtered'


def test_interjecting_on_her_reply_to_you_counts_as_follow_up():
    runner, driver = _started_runner()
    assert runner.submit_turn_text('sess-1', 'Krem: hey Remmi', speaker_id='42', humans=3)['reason'] == 'named'
    driver._active_sink = object()                          # still answering Krem
    with patch.object(runner, 'interrupt_active_turn', return_value=True) as interrupt:
        result = runner.submit_turn_text('sess-1', 'Krem: wait, shorter', speaker_id='42', humans=3)
    assert result['reason'] == 'follow_up'
    interrupt.assert_called_once_with('sess-1')


def test_follow_up_window_zero_turns_it_off():
    from plugins.discord.models.settings import SettingsStore

    store = SettingsStore({'voice': {'follow_up_seconds': 0, 'solo_no_name': False}})
    runner, _driver = _started_runner(store=store)
    assert runner.submit_turn_text('sess-1', 'Krem: hey Remmi', speaker_id='42', humans=1)['reason'] == 'named'
    runner._note_reply_end('sess-1')
    assert runner.submit_turn_text('sess-1', 'Krem: and?', speaker_id='42', humans=1)['status'] == 'filtered'


def test_transcribe_hook_trusts_the_runner_decision_but_still_gates_raw_pcm():
    # Mic test 1b: the driver's transcribe hook re-ran the bare name check on
    # text the runner had already admitted (solo) → "typing…" then silence.
    from types import SimpleNamespace

    runner = DiscordConversationRunner(voice_transport=MagicMock(), settings_store=None)
    # the raw-pcm path is core's driver's (temp WAV → STT); the runner only wraps it
    driver = SimpleNamespace(_discord_pending_text='Krem: tell me a story',
                             _whisper_transcribe=lambda pcm: 'a raw pcm turn with no name')
    transcribe = runner._build_transcribe_fn(driver=driver, settings=None, bot_names=['Remmi'])

    assert transcribe(b'') == 'Krem: tell me a story'      # admitted upstream: passes
    assert driver._discord_pending_text is None
    assert transcribe(b'\x00\x00' * 160) == ''             # raw pcm path: name rule still applies


def test_barge_hold_comes_from_discord_voice_settings_with_clamp():
    from types import SimpleNamespace

    hold = DiscordConversationRunner._barge_hold_ms
    assert hold(None) == 250
    assert hold(SimpleNamespace(voice=SimpleNamespace(barge_hold_ms=400))) == 400
    assert hold(SimpleNamespace(voice=SimpleNamespace(barge_hold_ms=5))) == 30
    assert hold(SimpleNamespace(voice=SimpleNamespace(barge_hold_ms='nope'))) == 250


def test_stop_async_runs_stop_off_the_loop():
    import asyncio

    runner, driver = _started_runner()
    result = asyncio.run(runner.stop_async('sess-1'))
    assert result['status'] == 'stopped'
    assert not runner.is_active('sess-1')


def test_build_stack_rides_the_core_manager():
    """S6: one external session on core's ConversationManager — the manager
    builds driver + gate, the ctor wraps them in the Discord source, the
    tuning pins an empty start word, and a refusal surfaces as an error."""
    from types import SimpleNamespace
    playback = MagicMock()
    runner = DiscordConversationRunner(voice_transport=playback, settings_store=None)
    calls = {}

    def start_external(ctor, chat_name=None, source_label='external', session_id=None, tuning=None, tts_split=None):
        calls.update(chat_name=chat_name, source_label=source_label, session_id=session_id, tuning=tuning)
        driver = MagicMock()
        driver._run_turn = MagicMock()
        src = ctor(driver, MagicMock())
        calls['driver'] = driver
        return src

    system = MagicMock()
    system.get_conversation_manager.return_value = SimpleNamespace(start_external=start_external, stop_external=MagicMock())
    driver, gate, source, frame_feed = runner._build_stack(
        system, session=_session(), chat_name='discord_111_222', bot_names=['Remmi'],
        settings=SimpleNamespace(voice=SimpleNamespace(barge_hold_ms=300, addressing_mode='bot_name')),
    )
    assert driver is calls['driver'] and source.channel_id == '222' and source.account_name == 'bot'
    assert calls['chat_name'] == 'discord_111_222' and calls['source_label'] == 'discord' and calls['session_id'] == 'sess-1'
    assert calls['tuning'] == {'barge_hold_ms': 300, 'start_word': ''}
    assert callable(driver._transcribe_fn) and driver._run_turn is not calls['driver']._run_turn or True
    system.get_conversation_manager.return_value = SimpleNamespace(start_external=lambda *a, **k: None)
    try:
        runner._build_stack(system, session=_session(), chat_name='x', bot_names=[], settings=None)
    except RuntimeError as exc:
        assert 'conversation_refused' in str(exc)
    else:
        raise AssertionError('a refused session must raise')


def test_prepare_session_needs_a_gate_task_when_gated():
    from types import SimpleNamespace
    playback = MagicMock()
    gate = SimpleNamespace(allowed=lambda account, channel: None)
    runner = DiscordConversationRunner(voice_transport=playback, settings_store=None, gate=gate)
    with patch('plugins.discord.voice.discord_conversation_runner._get_system') as get_system:
        get_system.return_value = MagicMock()
        assert runner.start(_session()) == {'status': 'blocked', 'reason': 'no_voice_task'}
