from plugins.discord.models.settings import SettingsOverlay, SettingsStore
from plugins.discord.models.voice import VoiceMode, VoiceSession
from plugins.discord.voice.voice_listener_service import VoiceListenerService


class FakeTransport:
    def __init__(self):
        self.started = []
        self.stopped = []
        self.playback_stops = []

    def start_listening_sync(self, account_name, channel_id, *, on_utterance, loop=None, **kwargs):
        self.started.append((account_name, channel_id, on_utterance, kwargs))
        return {'status': 'listening'}

    def stop_listening_sync(self, account_name, channel_id):
        self.stopped.append((account_name, channel_id))
        return {'status': 'stopped'}

    def stop_playback_sync(self, account_name, channel_id):
        self.playback_stops.append((account_name, channel_id))
        return {'status': 'stopped'}


class FakeAsyncTransport(FakeTransport):
    async def start_listening_async(self, account_name, channel_id, *, on_utterance, loop=None, **kwargs):
        self.started.append((account_name, channel_id, on_utterance, kwargs))
        return {'status': 'listening'}

    async def stop_listening_async(self, account_name, channel_id):
        self.stopped.append((account_name, channel_id))
        return {'status': 'stopped'}


class FakePerception:
    def __init__(self):
        self.calls = []

    def process_audio(self, session_id, *, audio_bytes, speaker_id='', speaker_name='', guild_id='', **kwargs):
        self.calls.append((session_id, speaker_id, speaker_name, audio_bytes))
        return {'status': 'transcribed', 'text': 'hello'}


def _session(mode=VoiceMode.TRANSCRIBE_ONLY):
    return VoiceSession(
        session_id='sess1',
        account_name='alpha',
        guild_id='g1',
        channel_id='vc1',
        mode=mode,
    )


def _voice_store(*, enabled: bool = True) -> SettingsStore:
    store = SettingsStore()
    store.global_overlay = SettingsOverlay.from_dict({'voice': {'enabled': enabled}})
    return store


def test_listener_starts_for_transcribe_mode():
    transport = FakeTransport()
    service = VoiceListenerService(
        voice_transport=transport,
        voice_perception_service=FakePerception(),
        settings_store=_voice_store(enabled=True),
    )
    result = service.start(_session())
    assert result['status'] == 'listening'
    assert transport.started


def test_listener_starts_async_for_transcribe_mode():
    import asyncio

    async def run():
        transport = FakeAsyncTransport()
        service = VoiceListenerService(
            voice_transport=transport,
            voice_perception_service=FakePerception(),
            settings_store=_voice_store(enabled=True),
        )
        result = await service.start_async(_session())
        assert result['status'] == 'listening'
        assert transport.started

    asyncio.run(run())


def test_listener_async_starts_conversation_runner():
    import asyncio
    from plugins.discord.models.settings import SettingsOverlay, SettingsStore

    class FakeRunner:
        def __init__(self):
            self.started_async = []

        async def start_async(self, session):
            self.started_async.append(session.session_id)
            return {'status': 'active'}

        def is_active(self, session_id):
            return session_id in {item for item in self.started_async}

        def frame_feed_for(self, session_id):
            class Feed:
                def push_stereo_pcm(self, pcm):
                    del pcm

            return Feed()

    async def run():
        store = SettingsStore()
        store.global_overlay = SettingsOverlay.from_dict(
            {
                'voice': {
                    'enabled': True,
                    'mode': VoiceMode.CONVERSATIONAL.value,
                    'speaking_enabled': True,
                    'conversation_core_enabled': True,
                }
            }
        )
        transport = FakeAsyncTransport()
        runner = FakeRunner()
        service = VoiceListenerService(
            voice_transport=transport,
            voice_perception_service=FakePerception(),
            conversation_runner=runner,
            settings_store=store,
        )
        session = VoiceSession(
            session_id='sess-conv',
            account_name='alpha',
            guild_id='g1',
            channel_id='vc1',
            mode=VoiceMode.CONVERSATIONAL,
        )
        result = await service.start_async(session)
        assert result['status'] == 'listening'
        assert runner.started_async == ['sess-conv']
        assert 'on_pcm_frame' in transport.started[0][3]

    asyncio.run(run())


def test_listener_skips_when_voice_disabled():
    transport = FakeTransport()
    service = VoiceListenerService(
        voice_transport=transport,
        voice_perception_service=FakePerception(),
        settings_store=_voice_store(enabled=False),
    )
    result = service.start(_session())
    assert result['status'] == 'skipped'
    assert not transport.started


def test_handle_utterance_runs_perception():
    transport = FakeTransport()
    perception = FakePerception()
    service = VoiceListenerService(
        voice_transport=transport,
        voice_perception_service=perception,
        settings_store=SettingsStore(),
    )
    session = _session()
    service._sessions[(session.account_name, session.channel_id)] = session
    service._handle_utterance(session.account_name, session.channel_id, 42, 'Alice', b'wav')
    assert perception.calls[0][0] == 'sess1'
    assert transport.playback_stops


def test_on_pcm_frame_feeds_the_engine_and_never_interrupts_on_its_own():
    # Barge-in the core way (mic test 2026-09-13): frames ride into the engine
    # with no speech hint; core's VAD + arming + hold window decide. The plugin
    # path that cancelled her on one RMS-classified frame is gone.
    from plugins.discord.models.settings import SettingsOverlay

    pushed = []
    interrupt_calls = []
    turn_active = {'value': True}

    class FakeRunner:
        def is_active(self, session_id):
            return session_id == 'sess-conv'

        def is_turn_active(self, session_id):
            return session_id == 'sess-conv' and turn_active['value']

        def interrupt_active_turn(self, session_id):
            interrupt_calls.append(session_id)
            return True

        def frame_feed_for(self, session_id):
            class Feed:
                def push_stereo_pcm(self, pcm, **kwargs):
                    pushed.append((pcm, kwargs))

            return Feed()

    store = SettingsStore()
    store.global_overlay = SettingsOverlay.from_dict(
        {
            'voice': {
                'enabled': True,
                'mode': VoiceMode.CONVERSATIONAL.value,
                'speaking_enabled': True,
                'conversation_core_enabled': True,
            }
        }
    )
    service = VoiceListenerService(
        voice_transport=FakeTransport(),
        voice_perception_service=FakePerception(),
        conversation_runner=FakeRunner(),
        settings_store=store,
    )
    session = VoiceSession(
        session_id='sess-conv',
        account_name='alpha',
        guild_id='g1',
        channel_id='vc1',
        mode=VoiceMode.CONVERSATIONAL,
    )
    on_pcm_frame = service._frame_feed_listen_kwargs(session)['on_pcm_frame']

    on_pcm_frame(42, b'\x00\x01' * 200, 5000.0, True)   # "speech" by RMS
    on_pcm_frame(42, b'\x00\x00' * 200, 0.0, False)     # silence — the engine needs it too
    assert interrupt_calls == []
    assert [kw for _pcm, kw in pushed] == [{}, {}]      # no speech hint: VAD decides

    turn_active['value'] = False
    on_pcm_frame(42, b'\x00\x01' * 200, 5000.0, True)
    assert len(pushed) == 2                              # nothing fed outside a live turn


def test_submit_conversation_turn_passes_speaker_and_live_occupancy():
    from plugins.discord.models.settings import SettingsOverlay

    calls = []

    class FakeRunner:
        def is_active(self, session_id):
            return True

        def submit_turn_text(self, session_id, text, **kwargs):
            calls.append((session_id, text, kwargs))
            return {'status': 'submitted'}

    class FakeDiscordTransport:
        def get_voice_channel_state_sync(self, account_name, channel_id):
            return {'status': 'ok', 'human_count': 1, 'bot_connected': True}

    transport = FakeTransport()
    transport.discord_transport = FakeDiscordTransport()
    store = SettingsStore()
    store.global_overlay = SettingsOverlay.from_dict(
        {'voice': {'enabled': True, 'mode': VoiceMode.CONVERSATIONAL.value, 'speaking_enabled': True}}
    )
    service = VoiceListenerService(
        voice_transport=transport,
        voice_perception_service=FakePerception(),
        conversation_runner=FakeRunner(),
        settings_store=store,
    )
    session = VoiceSession(session_id='s1', account_name='alpha', guild_id='g1', channel_id='vc1',
                           mode=VoiceMode.CONVERSATIONAL)
    result = service._submit_conversation_turn(session, 'story please', speaker_name='Krem', speaker_id='42')
    assert result['status'] == 'submitted'
    assert calls[0][2] == {'speaker_id': '42', 'humans': 1}
    assert calls[0][1].startswith('Krem:')


def test_merge_timer_is_armed_on_the_loop_from_a_worker_thread():
    # The legacy (non-core) lane merges utterances with a loop timer from a
    # pool thread — bookkeeping must hop to the loop (call_later is not
    # thread-safe).
    from plugins.discord.models.settings import SettingsOverlay

    class Loop:
        def __init__(self):
            self.hops = 0
            self.armed = []

        def call_soon_threadsafe(self, fn, *args):
            self.hops += 1
            fn(*args)

        def call_later(self, delay, cb):
            self.armed.append(delay)
            return type('H', (), {'cancel': lambda self_: None})()

    store = SettingsStore()
    store.global_overlay = SettingsOverlay.from_dict(
        {'voice': {'enabled': True, 'mode': VoiceMode.CONVERSATIONAL.value, 'speaking_enabled': True,
                   'conversation_core_enabled': False}}
    )
    service = VoiceListenerService(voice_transport=FakeTransport(), voice_perception_service=FakePerception(),
                                   settings_store=store)
    loop = Loop()
    session = VoiceSession(session_id='s1', account_name='alpha', guild_id='g1', channel_id='vc1',
                           mode=VoiceMode.CONVERSATIONAL)
    service._sessions[('alpha', 'vc1')] = session
    session._utterance_loop = loop
    import io
    import wave

    def _wav():
        buf = io.BytesIO()
        with wave.open(buf, 'wb') as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(b'\x00\x01' * 160)
        return buf.getvalue()

    service._handle_utterance('alpha', 'vc1', 42, 'Krem', _wav())
    service._handle_utterance('alpha', 'vc1', 42, 'Krem', _wav())
    assert loop.hops == 2
    assert len(loop.armed) == 2
    assert ('alpha', 'vc1', 42) in service._merge_pending
