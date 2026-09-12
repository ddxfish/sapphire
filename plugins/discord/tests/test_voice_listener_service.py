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


def test_pcm_barge_in_defers_interrupt_off_router_thread(monkeypatch):
    from plugins.discord.models.settings import SettingsOverlay

    submitted = []
    interrupt_calls = []

    class FakeRunner:
        def is_active(self, session_id):
            return session_id == 'sess-conv'

        def is_turn_active(self, session_id):
            return session_id == 'sess-conv'

        def interrupt_active_turn(self, session_id):
            interrupt_calls.append(session_id)
            return True

        def frame_feed_for(self, session_id):
            class Feed:
                def push_stereo_pcm(self, pcm, is_speech=None):
                    del pcm, is_speech

            return Feed()

    def fake_submit(fn, *args, **kwargs):
        submitted.append((fn, args, kwargs))

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
    runner = FakeRunner()
    service = VoiceListenerService(
        voice_transport=FakeTransport(),
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
    monkeypatch.setattr(
        'plugins.discord.voice.voice_listener_service.VOICE_WORKER_POOL.submit',
        fake_submit,
    )
    kwargs = service._frame_feed_listen_kwargs(session)
    on_pcm_frame = kwargs['on_pcm_frame']
    on_pcm_frame(42, b'\x00\x01' * 200, 5000.0, True)
    assert not interrupt_calls
    assert len(submitted) == 1
    assert submitted[0][1][0] == b'\x00\x01' * 200
