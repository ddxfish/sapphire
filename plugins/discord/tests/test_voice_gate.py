"""S6 + filters: the Realtime voice rule — filters pick the channels, auto-join is opt-in, the VC chat inherits its config."""
from core.continuity.scheduler import ContinuityScheduler
from plugins.discord.voice.voice_gate import SOURCE, VoiceGate


class FakeLoader:
    """core's get_enabled_daemon_task door (most-specific-wins) on the real filter semantics."""

    def __init__(self, tasks):
        self.tasks = tasks

    def get_enabled_daemon_task(self, source, account=None, payload=None):
        assert source == SOURCE and payload is not None
        catch_all = None
        for task in self.tasks:
            tc = task['trigger_config']
            if account is not None and tc.get('account') != account:
                continue
            flt = {k: v for k, v in (tc.get('filter') or {}).items() if str(v).strip()}
            if not flt:
                catch_all = catch_all or task
            elif ContinuityScheduler.filter_matches(flt, payload, task['name']):
                return task
        return catch_all

    def tasks_for_source(self, source):
        assert source == SOURCE
        return list(self.tasks)


def _task(tid, account, filt=None, **over):
    task = {'id': tid, 'name': f'Voice {tid}', 'enabled': True, 'provider': 'auto', 'model': '', 'toolset': 'none', 'prompt': '',
            'trigger_config': {'source': SOURCE, 'account': account, 'filter': filt, 'keep_chat_history': False}}
    task.update(over)
    return task


CHANNELS = {'111': {'guild_id': 'g1', 'guild_name': "Krem's Server", 'channel_id': '111', 'channel_name': 'Lounge'},
            '222': {'guild_id': 'g1', 'guild_name': "Krem's Server", 'channel_id': '222', 'channel_name': 'AFK'},
            '333': {'guild_id': 'g2', 'guild_name': 'Other', 'channel_id': '333', 'channel_name': 'Lounge'}}


def _gate(*tasks):
    return VoiceGate(FakeLoader(list(tasks)), describe=lambda account, cid: CHANNELS.get(str(cid)))


def test_no_filter_covers_every_channel_the_bot_sees_per_account():
    gate = _gate(_task('a', 'alpha'), _task('b', 'beta', {'channel_name': 'lounge'}))
    assert gate.allowed('alpha', '111')['id'] == 'a' and gate.allowed('alpha', '333')['id'] == 'a'
    assert gate.allowed('beta', '333')['id'] == 'b' and gate.allowed('beta', '222') is None
    assert gate.allowed('gamma', '111') is None and VoiceGate(None).allowed('alpha', '111') is None
    assert [t['id'] for t in gate.tasks('alpha')] == ['a'] and gate.tasks('gamma') == []


def test_filters_use_the_daemon_semantics_and_the_specific_rule_wins():
    everyone = _task('all', 'alpha', provider='fireworks')
    krem = _task('krem', 'alpha', {'guild_name': "krem's server", 'channel_name_not': 'afk'}, provider='claude')
    gate = _gate(everyone, krem)
    assert gate.allowed('alpha', '111')['id'] == 'krem'       # its filter matches → beats the catch-all
    assert gate.allowed('alpha', '222')['id'] == 'all'        # _not excluded krem → the catch-all covers it
    assert gate.allowed('alpha', '333')['id'] == 'all'
    only = _gate(krem)
    assert only.allowed('alpha', '111')['id'] == 'krem' and only.allowed('alpha', '222') is None and only.allowed('alpha', '333') is None
    by_id = _gate(_task('x', 'alpha', {'channel_id': '333, 999'}))
    assert by_id.allowed('alpha', '333')['id'] == 'x' and by_id.allowed('alpha', '111') is None
    contains = _gate(_task('c', 'alpha', {'channel_name_contains': 'oun'}))
    assert contains.allowed('alpha', '111')['id'] == 'c' and contains.allowed('alpha', '222') is None


def test_payload_survives_a_missing_or_broken_describe():
    gate = VoiceGate(FakeLoader([_task('x', 'alpha', {'channel_id': '5'})]))
    assert gate.payload('alpha', '5') == {'channel_id': '5', 'guild_id': '', 'guild_name': '', 'channel_name': ''}
    assert gate.allowed('alpha', '5')['id'] == 'x'
    broken = VoiceGate(FakeLoader([_task('x', 'alpha')]), describe=lambda a, c: 1 / 0)
    assert broken.allowed('alpha', '5')['id'] == 'x'
    assert gate.select('alpha', {'channel_id': '5', 'extra': 'ignored'})['id'] == 'x'


def test_auto_join_and_config_come_from_the_rule():
    task = _task('a', 'alpha', provider='claude', model='claude-opus-5', toolset='voice-tools', prompt='Be brief.')
    task['trigger_config'].update(keep_chat_history='true', auto_join=True)
    assert VoiceGate.auto_join(task) is True
    assert VoiceGate.auto_join(_task('b', 'alpha')) is False and VoiceGate.auto_join(None) is False
    task['trigger_config']['auto_join'] = 'false'
    assert VoiceGate.auto_join(task) is False
    cfg = VoiceGate.config(task)
    assert cfg == {'task_name': 'Voice a', 'keep_history': True, 'llm_provider': 'claude', 'llm_model': 'claude-opus-5',
                   'toolset': 'voice-tools', 'prompt': 'Be brief.'}
    assert VoiceGate.config(_task('b', 'alpha'))['llm_provider'] == ''   # auto = leave the chat alone
    assert VoiceGate.config(None)['keep_history'] is False
