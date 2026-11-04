"""/compact-daily (core/chat/compress.py daily mode): one labelled pair per day."""
from datetime import date, datetime
from types import SimpleNamespace

import pytest

from core.chat import compress

NOW = datetime(2026, 11, 3, 15, 0)


def _turn(ts, n):
    return [{'role': 'user', 'content': f'u{n}', 'timestamp': ts},
            {'role': 'assistant', 'content': f'a{n} ' * 50, 'timestamp': ts}]


def _pair(day, text='old summary'):
    stamp = {'timestamp': f'{day}T20:00:00', 'compressed': True, 'compressed_day': day}
    return [{'role': 'user', 'content': f'[Day summary: {day}]', **stamp},
            {'role': 'assistant', 'content': text, **stamp}]


class Provider:
    def __init__(self):
        self.prompts = []

    def chat_completion(self, msgs, generation_params=None):
        self.prompts.append(msgs[0]['content'])
        return SimpleNamespace(content='sum')


def test_day_of_honours_the_start_hour():
    assert compress.day_of('2026-11-03T02:30:00', 4) == date(2026, 11, 2)
    assert compress.day_of('2026-11-03T04:00:00', 4) == date(2026, 11, 3)
    assert compress.day_of('2026-11-03T02:30:00', 0) == date(2026, 11, 3)
    assert compress.day_of('not a time', 4) is None and compress.day_of(None) is None


def test_day_label_names_the_weekday():
    assert compress.day_label(date(2026, 11, 2)) == 'Monday, November 2, 2026'


def test_plan_keeps_the_window_and_rolls_off_the_rest():
    head = (_pair('2026-10-28') + _pair('2026-10-30') + _pair('2026-11-01')
            + _turn('2026-11-02T10:00:00', 1) + _turn('2026-11-03T01:00:00', 2)   # 1 am: still the 2nd
            + _turn('2026-11-03T09:00:00', 3))
    plan = compress.daily_plan(head, days=5, start_hour=4, now=NOW)
    assert [d.isoformat() for d, _, _ in plan['kept']] == ['2026-10-30', '2026-11-01']
    assert plan['dropped'] == 1
    assert sorted(d.isoformat() for d in plan['raw']) == ['2026-11-02', '2026-11-03']
    assert [m['content'] for m in plan['raw'][date(2026, 11, 2)][1] if m['role'] == 'user'] == ['u1', 'u2']


def test_plan_drops_raw_older_than_the_window_and_dates_undated_rows():
    head = (_turn('2026-10-20T10:00:00', 1)
            + [{'role': 'user', 'content': 'no time'}, {'role': 'assistant', 'content': 'x'}]
            + _turn('2026-11-03T10:00:00', 2))
    plan = compress.daily_plan(head, days=3, start_hour=4, now=NOW)
    assert plan['dropped'] == 2 and list(plan['raw']) == [date(2026, 11, 3)]   # undated: the turn before's day


def test_an_old_undated_summary_becomes_a_day():
    legacy = [{'role': 'user', 'content': '[Compressed history: ...]', 'timestamp': '2026-11-02T22:00:00',
               'compressed': True},
              {'role': 'assistant', 'content': 'weeks of it', 'timestamp': '2026-11-02T22:00:00',
               'compressed': True}]
    plan = compress.daily_plan(legacy, days=5, start_hour=4, now=NOW)
    assert list(plan['raw']) == [date(2026, 11, 2)] and not plan['kept']


def test_pairs_come_out_in_day_order_with_labels():
    head = _pair('2026-11-01') + _turn('2026-11-02T10:00:00', 1) + _pair('2026-11-03') + _turn('2026-11-03T12:00:00', 2)
    plan = compress.daily_plan(head, days=5, start_hour=4, now=NOW)
    p = Provider()
    out = compress.daily_pairs(plan, p, 3000)
    days = [m['compressed_day'] for m in out if m['role'] == 'user']
    assert days == ['2026-11-01', '2026-11-02', '2026-11-03', '2026-11-03']   # twice in a day: two entries
    assert out[2]['content'] == '[Day summary: Monday, November 2, 2026 (2 messages)]'
    assert out[3]['content'] == 'sum' and out[3]['compressed']
    assert out[0]['content'] == '[Day summary: 2026-11-01]'                    # earlier runs pass through untouched
    assert 'took place on Monday, November 2, 2026' in p.prompts[0]


class FakeSM:
    history_dir = '/nonexistent'

    def __init__(self, rows):
        self.rows = rows

    def export_chat(self, chat):
        return {'messages': [dict(m) for m in self.rows]}

    def read_chat_settings(self, chat):
        return {'private_chat': True}


def test_nothing_new_spends_nothing(monkeypatch):
    now = datetime.now().isoformat()
    rows = _pair(compress.day_of(now, 4).isoformat()) + _turn(now, 1)    # the turn is the kept tail
    monkeypatch.setattr('config.COMPACT_DAILY_DAYS', 5, raising=False)
    monkeypatch.setattr('config.DAY_START_HOUR', 4, raising=False)
    monkeypatch.setattr(compress, 'make_provider', lambda *a, **k: pytest.fail('no provider for a no-op'))
    with pytest.raises(ValueError, match='Nothing new'):
        compress._compress_chat(FakeSM(rows), 'c', 'daily', 'auto', '', 3000, 1, True)


def test_a_private_chat_resolves_privately(monkeypatch):
    seen = {}
    def _resolve(*a, **k):
        seen.update(k)
        return SimpleNamespace(provider=Provider())
    monkeypatch.setattr('core.chat.llm_providers.resolve.resolve', _resolve)
    compress.make_provider('auto', '', conversation='c', private=True)
    assert seen['private'] is True
