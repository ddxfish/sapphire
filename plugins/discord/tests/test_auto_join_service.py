from plugins.discord.models.intentions import JoinVoiceIntention, LeaveVoiceIntention
from plugins.discord.voice.auto_join_service import VoiceAutoJoinService
import asyncio


class FakeVoiceService:
    def __init__(self):
        self.joins = []
        self.leaves = []

    def join(self, intention):
        self.joins.append(intention)
        return {'status': 'joined', 'channel_id': intention.channel_id}

    async def join_async(self, intention):
        self.joins.append(intention)
        return {'status': 'joined', 'channel_id': intention.channel_id}

    def leave(self, intention):
        self.leaves.append(intention)
        return {'status': 'left', 'channel_id': intention.channel_id}

    async def leave_async(self, intention):
        self.leaves.append(intention)
        return {'status': 'left', 'channel_id': intention.channel_id}


class FakeTransport:
    def __init__(self, states):
        self._states = states

    def get_voice_channel_state_sync(self, account_name, channel_id):
        return self._states.get((account_name, channel_id), {'status': 'error'})

    async def get_voice_channel_state_async(self, account_name, channel_id):
        return self.get_voice_channel_state_sync(account_name, channel_id)


class FakeSettings:
    def __init__(self, *, enabled=True, emergency_disabled=False, join_targets=None):
        self.voice = type('V', (), {
            'enabled': enabled,
            'emergency_disabled': emergency_disabled,
            'join_targets': join_targets or [],
        })()


class FakeSettingsStore:
    def __init__(self, settings):
        self._settings = settings

    def resolve(self, **kwargs):
        return self._settings


def test_auto_join_joins_when_humans_present():
    transport = FakeTransport({
        ('alpha', 'vc1'): {
            'status': 'ok',
            'guild_id': 'g1',
            'human_count': 2,
            'bot_connected': False,
        },
    })
    voice = FakeVoiceService()
    service = VoiceAutoJoinService(
        transport=transport,
        voice_service=voice,
        settings_store=FakeSettingsStore(FakeSettings(join_targets=['alpha:vc1'])),
    )

    results = service.tick('alpha')

    assert len(results) == 1
    assert len(voice.joins) == 1
    assert voice.joins[0].channel_id == 'vc1'
    assert voice.joins[0].reason == 'auto_join'
    assert voice.leaves == []


def test_auto_join_leaves_when_empty():
    transport = FakeTransport({
        ('alpha', 'vc1'): {
            'status': 'ok',
            'guild_id': 'g1',
            'human_count': 0,
            'bot_connected': True,
        },
    })
    voice = FakeVoiceService()
    service = VoiceAutoJoinService(
        transport=transport,
        voice_service=voice,
        settings_store=FakeSettingsStore(FakeSettings(join_targets=['alpha:vc1'])),
    )

    results = service.tick('alpha')

    assert len(results) == 1
    assert voice.leaves[0].reason == 'auto_join_empty'
    assert voice.joins == []


def test_auto_join_skips_when_already_connected_with_humans():
    transport = FakeTransport({
        ('alpha', 'vc1'): {
            'status': 'ok',
            'guild_id': 'g1',
            'human_count': 2,
            'bot_connected': True,
        },
    })
    voice = FakeVoiceService()
    service = VoiceAutoJoinService(
        transport=transport,
        voice_service=voice,
        settings_store=FakeSettingsStore(FakeSettings(join_targets=['alpha:vc1'])),
    )

    assert service.tick('alpha') == []
    assert voice.joins == []
    assert voice.leaves == []


def test_auto_join_disabled_when_voice_off():
    transport = FakeTransport({
        ('alpha', 'vc1'): {
            'status': 'ok',
            'guild_id': 'g1',
            'human_count': 2,
            'bot_connected': False,
        },
    })
    voice = FakeVoiceService()
    service = VoiceAutoJoinService(
        transport=transport,
        voice_service=voice,
        settings_store=FakeSettingsStore(FakeSettings(enabled=False, join_targets=['alpha:vc1'])),
    )

    assert service.tick('alpha') == []
    assert voice.joins == []


def test_auto_join_ignores_other_accounts():
    transport = FakeTransport({
        ('beta', 'vc1'): {
            'status': 'ok',
            'guild_id': 'g1',
            'human_count': 2,
            'bot_connected': False,
        },
    })
    voice = FakeVoiceService()
    service = VoiceAutoJoinService(
        transport=transport,
        voice_service=voice,
        settings_store=FakeSettingsStore(FakeSettings(join_targets=['beta:vc1'])),
    )

    assert service.tick('alpha') == []
    assert voice.joins == []


def test_auto_join_inspect_reports_target_state():
    transport = FakeTransport({
        ('alpha', 'vc1'): {
            'status': 'ok',
            'guild_id': 'g1',
            'channel_name': 'Lounge',
            'human_count': 1,
            'member_count': 1,
            'bot_connected': False,
        },
    })
    service = VoiceAutoJoinService(
        transport=transport,
        voice_service=FakeVoiceService(),
        settings_store=FakeSettingsStore(FakeSettings(join_targets=['alpha:vc1'])),
    )

    status = service.inspect('alpha')

    assert status['enabled'] is True
    assert status['targets'][0]['human_count'] == 1
    assert status['targets'][0]['status'] == 'watching'


def test_auto_join_async_joins_when_humans_present():
    transport = FakeTransport({
        ('alpha', 'vc1'): {
            'status': 'ok',
            'guild_id': 'g1',
            'human_count': 1,
            'bot_connected': False,
        },
    })
    voice = FakeVoiceService()
    service = VoiceAutoJoinService(
        transport=transport,
        voice_service=voice,
        settings_store=FakeSettingsStore(FakeSettings(join_targets=['alpha:vc1'])),
    )

    results = asyncio.run(service.tick_async('alpha'))

    assert len(results) == 1
    assert voice.joins[0].channel_id == 'vc1'


class FakeSleepService:
    def __init__(self, *, sleeping=False):
        self._sleeping = sleeping

    def voice_blocked_for_sleep(self, account_name, settings, **kwargs):
        return self._sleeping


def test_auto_join_skips_join_while_sleeping():
    transport = FakeTransport({
        ('alpha', 'vc1'): {
            'status': 'ok',
            'guild_id': 'g1',
            'human_count': 2,
            'bot_connected': False,
        },
    })
    voice = FakeVoiceService()
    service = VoiceAutoJoinService(
        transport=transport,
        voice_service=voice,
        settings_store=FakeSettingsStore(FakeSettings(join_targets=['alpha:vc1'])),
        sleep_service=FakeSleepService(sleeping=True),
    )

    results = service.tick('alpha')

    assert results == []
    assert voice.joins == []
    assert voice.leaves == []


def test_auto_join_leaves_voice_while_sleeping():
    transport = FakeTransport({
        ('alpha', 'vc1'): {
            'status': 'ok',
            'guild_id': 'g1',
            'human_count': 2,
            'bot_connected': True,
        },
    })
    voice = FakeVoiceService()
    service = VoiceAutoJoinService(
        transport=transport,
        voice_service=voice,
        settings_store=FakeSettingsStore(FakeSettings(join_targets=['alpha:vc1'])),
        sleep_service=FakeSleepService(sleeping=True),
    )

    results = service.tick('alpha')

    assert len(results) == 1
    assert voice.joins == []
    assert len(voice.leaves) == 1
    assert voice.leaves[0].channel_id == 'vc1'
