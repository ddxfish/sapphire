"""scheduler.tasks_matching — the event gates as a read-only question (Discord 2.3.0)."""
from core.continuity.scheduler import tasks_matching


def _t(**over):
    t = {'id': 't', 'name': 'Chat', 'enabled': True, 'type': 'daemon',
         'trigger_config': {'source': 'discord_message', 'account': 'alpha'}}
    t.update(over)
    return t


def test_tasks_matching_applies_the_scheduler_gates_read_only():
    ev = {'account': 'alpha', 'channel_name': 'general', 'mentioned': 'True'}
    ok = _t()
    disabled = _t(id='d', enabled=False)
    other_bot = _t(id='o', trigger_config={'source': 'discord_message', 'account': 'beta'})
    filtered = _t(id='f', trigger_config={'source': 'discord_message', 'account': 'alpha', 'filter': {'channel_name': 'lobby'}})
    matching = _t(id='m', trigger_config={'source': 'discord_message', 'account': 'alpha',
                                          'filter': {'channel_name_not': 'lobby', 'mentioned': 'True'}})
    asleep = _t(id='s', active_hours_start=9, active_hours_end=17)

    got = [t['id'] for t in tasks_matching([ok, disabled, other_bot, filtered, matching, asleep], ev, hour=3)]

    assert got == ['t', 'm']
    assert [t['id'] for t in tasks_matching([asleep], ev, hour=12)] == ['s']
    assert tasks_matching([ok], 'not a dict') == [] and tasks_matching(None, ev) == []
