"""Birthdays module (S1): the people table, the daily clock, the wish delivery."""
from datetime import datetime
from types import SimpleNamespace

from discord_personality.clock import MemoryState, due, in_window, parse_hhmm
from discord_personality.modules import birthdays
from discord_personality.storage import People, normalize_birthday


class FakeLoader:
    def __init__(self, tasks):
        self.tasks, self.fired, self.refuse = tasks, [], False

    def tasks_for_source(self, source):
        return [t for t in self.tasks if t['trigger_config']['source'] == source]

    def fire_task(self, task_id, payload, *, plugin=None):
        self.fired.append((task_id, payload, plugin))
        return {'success': not self.refuse, 'error': 'refused' if self.refuse else None}


class FakeApi:
    def __init__(self):
        self.sent = []

    def send_message(self, channel_id, text, reply_to=None, account=None):
        self.sent.append((channel_id, text, account))
        return {'status': 'sent'}


def _task(tid='b1', account='alpha', channel='555'):
    return {'id': tid, 'name': 'Birthdays', 'enabled': True, 'type': 'daemon',
            'trigger_config': {'source': 'discord_birthday', 'account': account, 'channel': channel}}


def test_clock_helpers():
    assert parse_hhmm('09:00') == (9, 0) and parse_hhmm('x') is None
    assert due('09:00', datetime(2026, 9, 22, 9, 2)) and not due('09:00', datetime(2026, 9, 22, 9, 5))
    assert not due('09:00', datetime(2026, 9, 22, 8, 59, 59))
    assert in_window(datetime(2026, 9, 22, 23, 30), '22:00', '07:00') and in_window(datetime(2026, 9, 22, 6, 59), '22:00', '07:00')
    assert not in_window(datetime(2026, 9, 22, 12, 0), '22:00', '07:00') and not in_window(datetime(2026, 9, 22, 12, 0), '', '07:00')


def test_people_storage_round_trip(tmp_path):
    people = People(tmp_path / 'p.sqlite3')
    assert people.list('alpha') == []
    row = people.upsert('alpha', '42', display_name='Krem', birthday='03-03')
    assert row['display_name'] == 'Krem' and row['birthday_mm_dd'] == '03-03' and row['facts'] == []
    people.upsert('alpha', '42', facts=['likes boats'])                # None = leave alone
    got = people.get('alpha', '42')
    assert got['birthday_mm_dd'] == '03-03' and got['facts'] == ['likes boats'] and got['display_name'] == 'Krem'
    assert [r['user_id'] for r in people.with_birthday('alpha', '03-03')] == ['42']
    assert people.with_birthday('beta', '03-03') == []
    people.upsert('alpha', '42', birthday='')
    assert people.with_birthday('alpha', '03-03') == []
    assert people.delete('alpha', '42') is True and people.get('alpha', '42') is None


def test_normalize_birthday():
    assert normalize_birthday('3/3') == '03-03' and normalize_birthday('2001-12-25') == '12-25'
    assert normalize_birthday('') == '' and normalize_birthday('13-01') is None and normalize_birthday('junk') is None


def test_run_fires_one_wish_per_person_once_a_day(tmp_path):
    people = People(tmp_path / 'p.sqlite3')
    people.upsert('alpha', '42', display_name='Krem', birthday='09-22')
    people.upsert('alpha', '43', display_name='Sudo', birthday='09-22')
    people.upsert('alpha', '44', display_name='Nobody', birthday='01-01')
    loader, state = FakeLoader([_task()]), MemoryState()
    cfg = {'birthdays.time': '09:00'}
    at = datetime(2026, 9, 22, 9, 1)

    fired = birthdays.run('alpha', at, people=people, loader=loader, state=state, cfg=cfg)

    assert sorted(f['user_id'] for f in fired) == ['42', '43']
    task_id, payload, plugin = loader.fired[0]
    assert task_id == 'b1' and plugin == 'discord-personality'
    assert payload['channel_id'] == '555' and payload['proactive_kind'] == 'birthday'
    assert "Krem's birthday" in payload['content'] or "Sudo's birthday" in payload['content']
    assert birthdays.run('alpha', at, people=people, loader=loader, state=state, cfg=cfg) == []      # latched
    assert birthdays.run('alpha', datetime(2026, 9, 22, 9, 4), people=people, loader=loader, state=state, cfg=cfg) == []
    assert len(loader.fired) == 2
    assert birthdays.run('alpha', datetime(2026, 9, 22, 13, 0), people=people, loader=loader, state=state, cfg=cfg) == []


def test_run_outside_window_or_other_account_is_quiet(tmp_path):
    people = People(tmp_path / 'p.sqlite3')
    people.upsert('alpha', '42', display_name='Krem', birthday='09-22')
    loader, state = FakeLoader([_task(account='beta')]), MemoryState()
    cfg = {'birthdays.time': '09:00'}
    assert birthdays.run('alpha', datetime(2026, 9, 22, 8, 0), people=people, loader=loader, state=state, cfg=cfg) == []
    assert birthdays.run('alpha', datetime(2026, 9, 22, 9, 0), people=people, loader=loader, state=state, cfg=cfg) == []   # no task on alpha
    assert loader.fired == []


def test_deliver_mentions_the_person_unless_she_already_did():
    api = FakeApi()
    birthdays.deliver({'channel_id': '555', 'user_id': '42', 'account': 'alpha'}, 'Happy birthday, Krem!', api=api)
    birthdays.deliver({'channel_id': '555', 'user_id': '42'}, '<@42> cake time', api=api)
    assert api.sent[0] == ('555', '<@42> Happy birthday, Krem!', 'alpha')
    assert api.sent[1][1] == '<@42> cake time'
    assert birthdays.deliver({'channel_id': '555', 'user_id': '42'}, '   ', api=api)['status'] == 'skipped'
