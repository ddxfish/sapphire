"""S6 + filters: opt-in auto-join over covered channels, leave when empty, the deliberate-leave latch."""
import asyncio
from types import SimpleNamespace

from plugins.discord.models.intentions import LeaveVoiceIntention
from plugins.discord.voice.auto_join_service import VoiceAutoJoinService
from plugins.discord.voice.voice_service import VoiceService


class FakeSessions:
    def __init__(self, channels=()):
        self._channels = list(channels)

    def list_active(self, account_name):
        return [SimpleNamespace(channel_id=c) for c in self._channels]


class FakeVoiceService:
    def __init__(self, live=()):
        self.joins, self.leaves, self.ensures = [], [], []
        self.sessions = FakeSessions(live)

    async def ensure_listener_async(self, account_name, channel_id, guild_id=''):
        self.ensures.append(channel_id)
        return {'status': 'listening'}

    async def join_async(self, intention):
        self.joins.append(intention.channel_id)
        return {'status': 'joined', 'channel_id': intention.channel_id}

    async def leave_async(self, intention):
        self.leaves.append((intention.channel_id, intention.reason))
        return {'status': 'left', 'channel_id': intention.channel_id}


class FakeTransport:
    """rows: channel_id -> {human_count, bot_connected} — what list_voice_targets reports."""

    def __init__(self, rows):
        self.rows = rows

    def _list(self, account_name):
        return [dict(channel_id=cid, guild_id='g', guild_name='Server', channel_name=f'vc{cid}', **row)
                for cid, row in self.rows.items()]

    async def list_voice_targets(self, account_name=None):
        return self._list(account_name)

    def list_voice_targets_sync(self, account_name=None):
        return self._list(account_name)


class FakeGate:
    """rules: channel_id -> task; auto: task ids with auto_join on."""

    def __init__(self, rules, auto=()):
        self.rules, self.auto = rules, set(auto)

    def tasks(self, account_name):
        return list(self.rules.values())

    def select(self, account_name, payload):
        return self.rules.get(str(payload.get('channel_id')))

    def allowed(self, account_name, channel_id):
        return self.rules.get(str(channel_id))

    def auto_join(self, task):
        return task['id'] in self.auto


def _rule(tid):
    return {'id': tid, 'name': f'Voice {tid}'}


def _tick(svc, account='alpha'):
    return asyncio.run(svc.tick_async(account))


def test_auto_join_rule_joins_when_humans_are_there_and_any_covered_channel_leaves_when_empty():
    voice = FakeVoiceService()
    transport = FakeTransport({'1': {'human_count': 2, 'bot_connected': False}, '2': {'human_count': 0, 'bot_connected': True},
                               '3': {'human_count': 1, 'bot_connected': False}, '4': {'human_count': 0, 'bot_connected': True},
                               '5': {'human_count': 3, 'bot_connected': False}})
    gate = FakeGate({'1': _rule('a'), '2': _rule('a'), '3': _rule('m'), '4': _rule('m')}, auto=['a'])
    out = _tick(VoiceAutoJoinService(transport=transport, voice_service=voice, gate=gate))
    assert voice.joins == ['1']                                                   # 3 = manual rule: waits for /voice join; 5 = no rule
    assert voice.leaves == [('2', 'auto_join_empty'), ('4', 'auto_join_empty')]   # empty = leave, auto or manual
    assert len(out) == 3


def test_connected_with_humans_keeps_the_listener_alive_and_inspect_reports_it():
    voice = FakeVoiceService()
    transport = FakeTransport({'1': {'human_count': 1, 'bot_connected': True}, '2': {'human_count': 0, 'bot_connected': False},
                               '9': {'human_count': 4, 'bot_connected': False}})
    svc = VoiceAutoJoinService(transport=transport, voice_service=voice, gate=FakeGate({'1': _rule('m'), '2': _rule('a')}, auto=['a']))
    assert _tick(svc) == [] and voice.ensures == ['1'] and voice.joins == []
    report = svc.inspect('alpha')
    assert report['enabled'] and [(r['channel_id'], r['status'], r['auto_join']) for r in report['targets']] == [
        ('1', 'manual', False), ('2', 'watching', True)]                          # 9 is uncovered: not listed


def test_a_deliberate_leave_latches_until_the_channel_empties():
    voice = FakeVoiceService()
    transport = FakeTransport({'1': {'human_count': 2, 'bot_connected': False}})
    svc = VoiceAutoJoinService(transport=transport, voice_service=voice, gate=FakeGate({'1': _rule('a')}, auto=['a']))
    svc.note_leave('alpha', '1', 'hangup_sentinel')
    assert svc.latched('alpha', '1')
    _tick(svc)
    assert voice.joins == []                                       # the trap: no rejoin while they are still there
    assert svc.inspect('alpha')['targets'][0]['latched'] is True
    transport.rows['1'] = {'human_count': 0, 'bot_connected': False}
    _tick(svc)
    assert not svc.latched('alpha', '1')                           # emptied once → the latch clears
    transport.rows['1'] = {'human_count': 1, 'bot_connected': False}
    _tick(svc)
    assert voice.joins == ['1']
    svc.note_leave('alpha', '1', 'auto_join_empty')                # her own leaves never latch
    svc.note_leave('alpha', '1', 'no_voice_task')
    assert not svc.latched('alpha', '1')


def test_voice_service_reports_every_leave_to_the_latch():
    seen = []
    sessions = SimpleNamespace(get=lambda a, c: None, close=lambda sid: None)

    async def disconnect_async(a, c):
        return {'status': 'disconnected'}

    transport = SimpleNamespace(disconnect_sync=lambda a, c: {'status': 'disconnected'}, disconnect_async=disconnect_async)
    svc = VoiceService(voice_transport=transport, sessions=sessions, on_leave=lambda a, c, r: seen.append((a, c, r)))
    svc.leave(LeaveVoiceIntention(intention_type='leave_voice', account_name='alpha', channel_id='1', message_id='', reason='slash_command'))
    asyncio.run(svc.leave_async(LeaveVoiceIntention(intention_type='leave_voice', account_name='alpha', channel_id=2, message_id='',
                                                    reason='hangup_sentinel')))
    assert seen == [('alpha', '1', 'slash_command'), ('alpha', '2', 'hangup_sentinel')]
    svc.on_leave = lambda a, c, r: 1 / 0                           # a broken hook never breaks the leave
    assert svc.leave(LeaveVoiceIntention(intention_type='leave_voice', account_name='alpha', channel_id='3', message_id='', reason='x'))['status'] == 'left'


def test_no_rule_for_a_live_channel_means_leave_and_inspect_says_off():
    voice = FakeVoiceService(live=['5', '6'])
    svc = VoiceAutoJoinService(transport=FakeTransport({}), voice_service=voice, gate=FakeGate({}))
    assert len(_tick(svc)) == 2 and voice.leaves == [('5', 'no_voice_task'), ('6', 'no_voice_task')]
    assert svc.inspect('alpha') == {'enabled': False, 'reason': 'no_voice_task', 'targets': []}
    covered = FakeVoiceService(live=['7'])
    svc2 = VoiceAutoJoinService(transport=FakeTransport({'7': {'human_count': 1, 'bot_connected': True}}), voice_service=covered,
                                gate=FakeGate({'7': _rule('m')}))
    assert _tick(svc2) == [] and covered.leaves == [] and covered.ensures == ['7']
