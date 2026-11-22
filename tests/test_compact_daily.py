"""/compact-daily (core/chat/compress.py daily mode): one labelled pair per day."""
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest

from core.chat import compress

NOW = datetime(2026, 11, 3, 15, 0)


@pytest.fixture(autouse=True)
def _own_instruction_files(tmp_path, monkeypatch):
    """The two editable texts live under user/prompts/: a test never reads or
    writes the install's own."""
    monkeypatch.setattr(compress, 'INSTRUCTIONS_PATH', tmp_path / 'compress_instructions.txt')
    monkeypatch.setattr(compress, 'DAILY_INSTRUCTIONS_PATH', tmp_path / 'compact_daily_instructions.txt')
    monkeypatch.setattr(compress, '_user_zone', lambda: None)      # days on this machine's clock, whatever its settings


REAL_USER_ZONE = compress._user_zone


def test_a_day_is_cut_in_the_persons_time_zone_when_one_is_set(monkeypatch):
    """Rows are stamped with the computer's wall time, her tasks run on the
    time zone setting. On a server on UTC or a container pinned elsewhere the
    two differ: the nightly compact could fire before the day had ended, and
    each day was the computer's. With a zone set, the day is the person's.
    Unset (the "UTC" default), a bad name, or the same zone: as before."""
    from datetime import timezone
    monkeypatch.setattr(compress, '_user_zone', REAL_USER_ZONE)
    for unset in ('UTC', 'utc', '', None, 'Not/AZone'):
        monkeypatch.setattr('config.USER_TIMEZONE', unset, raising=False)
        assert compress._user_zone() is None
    monkeypatch.setattr('config.USER_TIMEZONE', 'Asia/Tokyo', raising=False)
    assert str(compress._user_zone()) == 'Asia/Tokyo'

    here = datetime.now().astimezone().utcoffset()
    same, ahead = timezone(here), timezone(here + timedelta(hours=6))
    stamps = ['2026-11-21T03:59:59', '2026-11-21T04:00:00', '2026-11-21T21:30:00', '2026-11-21T23:59:59']
    monkeypatch.setattr(compress, '_user_zone', lambda: None)
    plain = [compress.day_of(s, 4) for s in stamps]
    assert plain == [date(2026, 11, 20), date(2026, 11, 21), date(2026, 11, 21), date(2026, 11, 21)]
    monkeypatch.setattr(compress, '_user_zone', lambda: same)
    assert [compress.day_of(s, 4) for s in stamps] == plain                 # the same zone on both: nothing changes
    monkeypatch.setattr(compress, '_user_zone', lambda: ahead)              # the person is six hours ahead of the box
    assert [compress.day_of(s, 4) for s in stamps] == [date(2026, 11, 21), date(2026, 11, 21),
                                                       date(2026, 11, 21), date(2026, 11, 22)]
    assert compress.day_of('2026-11-21T22:30:00+00:00', 4) == compress.day_of(
        datetime(2026, 11, 21, 22, 30, tzinfo=timezone.utc).astimezone().replace(tzinfo=None).isoformat(), 4)
    assert compress.day_of('not a time', 4) is None

    # the plan follows: 9:30 pm on the box is already 3:30 am for the person, still "yesterday's" day there,
    # and 11:30 pm on the box is 5:30 am: their new day, so it stays raw as today.
    rows = _turn('2026-11-20T12:00:00', 1) + _turn('2026-11-21T21:30:00', 2) + _turn('2026-11-21T23:30:00', 3)
    plan = compress.daily_plan(rows, days=5, start_hour=4, now=datetime(2026, 11, 21, 23, 45))
    assert sorted(plan['raw']) == [date(2026, 11, 20), date(2026, 11, 21)] and len(plan['today']) == 1


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
    assert sorted(d.isoformat() for d in plan['raw']) == ['2026-11-02']        # today is never summarized
    assert [r[0]['content'] for _, _, r in plan['today']] == ['u3']
    assert [m['content'] for m in plan['raw'][date(2026, 11, 2)][1] if m['role'] == 'user'] == ['u1', 'u2']


def test_plan_drops_raw_older_than_the_window_and_dates_undated_rows():
    head = (_turn('2026-10-20T10:00:00', 1)
            + [{'role': 'user', 'content': 'no time'}, {'role': 'assistant', 'content': 'x'}]
            + _turn('2026-11-02T10:00:00', 2))
    plan = compress.daily_plan(head, days=3, start_hour=4, now=NOW)
    assert plan['dropped'] == 2 and list(plan['raw']) == [date(2026, 11, 2)]   # undated: the turn before's day


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
    days = [m.get('compressed_day') for m in out if m['role'] == 'user']
    assert days == ['2026-11-01', '2026-11-02', '2026-11-03', None]            # today's turn rides along raw
    assert out[-2]['content'] == 'u2' and len(p.prompts) == 1
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


def test_a_job_with_nothing_to_do_ends_done_not_failed(monkeypatch):
    """A nightly /compact-daily on a quiet chat left a red "Chat compression
    failed" row every morning. Nothing to do is not a failure: the job ends
    done, nothing is written and no event wakes the day recorder."""
    import time
    from core import notify
    now = datetime.now().isoformat()
    rows = _pair(compress.day_of(now, 4).isoformat()) + _turn(now, 1)
    ended, events = [], []
    monkeypatch.setattr('config.COMPACT_DAILY_DAYS', 5, raising=False)
    monkeypatch.setattr('config.DAY_START_HOUR', 4, raising=False)
    monkeypatch.setattr(notify, 'begin', lambda *a, **k: 7)
    monkeypatch.setattr(notify, 'tick', lambda *a, **k: None)
    monkeypatch.setattr(notify, 'finish', lambda rid, **k: ended.append(k))
    monkeypatch.setattr(compress, 'publish', lambda *a, **k: events.append(a))
    monkeypatch.setattr(compress, 'make_provider', lambda *a, **k: pytest.fail('no provider for a no-op'))
    ok, err = compress.start_compress_job(FakeSM(rows), 'c', mode='daily', provider_key='auto', model='',
                                          target_tokens=2000, keep_last_turns=1, backup=True)
    assert ok, err
    for _ in range(100):
        if compress.get_job_status().get('done'):
            break
        time.sleep(0.02)
    status = compress.get_job_status()
    assert status['ok'] is True and 'error' not in status and 'Nothing new to compact' in status['result']['nothing']
    assert ended == [{'title': status['result']['nothing']}] and events == []


def test_a_chat_whose_settings_cannot_be_read_is_not_compressed(monkeypatch, tmp_path):
    """read_chat_settings answers None on a locked database or a sealed row.
    The job read that as "not private": a plaintext backup, any provider, and
    the chat's name in the failure notice. Nothing is written, and the notice
    keeps to generic words."""
    import time
    from core import notify
    ended = []
    monkeypatch.setattr(notify, 'begin', lambda *a, **k: 7)
    monkeypatch.setattr(notify, 'tick', lambda *a, **k: None)
    monkeypatch.setattr(notify, 'finish', lambda rid, **k: ended.append(k))
    monkeypatch.setattr(compress, 'make_provider', lambda *a, **k: pytest.fail('no provider'))
    monkeypatch.setattr(compress, '_write_backup', lambda *a, **k: pytest.fail('no backup'))

    class Unreadable(FakeSM):
        history_dir = str(tmp_path)

        def read_chat_settings(self, chat):
            return None

        def replace_messages(self, *a, **k):
            pytest.fail('nothing is written')
    rows = _turn('2026-11-01T10:00:00', 1) + _turn('2026-11-02T10:00:00', 2) + _turn('2026-11-03T10:00:00', 3)
    ok, err = compress.start_compress_job(Unreadable(rows), 'lawyer-notes', mode='whole', provider_key='auto', model='',
                                          target_tokens=2000, keep_last_turns=1, backup=True)
    assert ok, err
    for _ in range(100):
        if compress.get_job_status().get('done'):
            break
        time.sleep(0.02)
    status = compress.get_job_status()
    assert status['ok'] is False and 'could not be read' in status['error']
    assert ended == [{'failed': True, 'title': 'Chat compression failed', 'body': 'See the log.'}]


class PreviewSM(FakeSM):
    def __init__(self, rows, settings=None):
        super().__init__(rows)
        self.settings = settings or {}

    def read_chat_settings(self, chat):
        return self.settings

    def replace_messages(self, *a, **k):
        pytest.fail('a preview writes nothing')


def test_the_day_wording_can_be_tried_with_nothing_written(monkeypatch, tmp_path):
    """Every real run costs a night and a day that cannot be summarized again.
    Try it: one day, the box's own text, no backup, no write, no event."""
    monkeypatch.setattr('config.COMPACT_DAILY_DAYS', 5, raising=False)
    monkeypatch.setattr('config.DAY_START_HOUR', 4, raising=False)
    monkeypatch.setattr(compress, 'publish', lambda *a, **k: pytest.fail('a preview publishes nothing'))
    monkeypatch.setattr(compress, '_write_backup', lambda *a, **k: pytest.fail('a preview leaves no backup'))
    p = Provider()
    monkeypatch.setattr(compress, 'make_provider', lambda *a, **k: p)
    # noon of the day the clock is in (a run between midnight and 4 am is still yesterday)
    noon = datetime.combine(compress.day_of(datetime.now().isoformat(), 4), datetime.min.time()).replace(hour=12)
    ago = lambda n: (noon - timedelta(days=n)).isoformat()
    rows = _turn(ago(2), 1) + _turn(ago(1), 2) + _turn(noon.isoformat(), 3)
    out = compress.preview_day(PreviewSM(rows), 'c', 'auto', '', 2000, daily_instructions='Write it as a haiku.')
    assert out['day'] == ago(1)[:10] and out['finished'] and out['messages'] == 2 and out['text'] == 'sum'
    assert len(p.prompts) == 1 and 'Write it as a haiku.' in p.prompts[0] and 'u2' in p.prompts[0]
    assert 'u1' not in p.prompts[0] and 'u3' not in p.prompts[0] and 'tight prose' not in p.prompts[0]

    days = compress.preview_days(PreviewSM(rows), 'c')                      # the choice, without a model call
    assert [(d['day'], d['finished'], d['messages']) for d in days] == [
        (noon.date().isoformat(), False, 2), (ago(1)[:10], True, 2), (ago(2)[:10], True, 2)]
    assert len(p.prompts) == 1
    older = compress.preview_day(PreviewSM(rows), 'c', 'auto', '', 2000, day=ago(2)[:10])
    assert older['day'] == ago(2)[:10] and 'u1' in p.prompts[-1] and 'u2' not in p.prompts[-1]
    with pytest.raises(ValueError, match='no raw turns'):
        compress.preview_day(PreviewSM(rows), 'c', 'auto', '', 2000, day='2020-01-01')

    today = compress.preview_day(PreviewSM(_pair(ago(1)[:10]) + _turn(noon.isoformat(), 3)), 'c', 'auto', '', 2000)
    assert today['finished'] is False and today['day'] == noon.date().isoformat() and 'tight prose' in p.prompts[-1]

    with pytest.raises(compress.NothingToCompress):
        compress.preview_day(PreviewSM(_pair(ago(1)[:10])), 'c', 'auto', '', 2000)
    with pytest.raises(ValueError, match='private chat'):
        compress.preview_day(PreviewSM(rows, {'private_chat': True}), 'c', 'auto', '', 2000)
    unreadable = PreviewSM(rows)
    unreadable.read_chat_settings = lambda chat: None           # a locked database, a sealed row: not "not private"
    with pytest.raises(ValueError, match='private chat'):
        compress.preview_day(unreadable, 'c', 'auto', '', 2000)
    with pytest.raises(ValueError, match='private chat'):
        compress.preview_days(unreadable, 'c')
    for mode in ('room', 'game'):
        with pytest.raises(ValueError, match='not compacted by day'):
            compress.preview_days(PreviewSM(rows, {'mode': mode}), 'c')
    compress.save_instructions('y' * 7000, daily=True)           # a long saved wording is not the dry run's to refuse
    assert compress.preview_day(PreviewSM(rows), 'c', 'auto', '', 2000)['text'] == 'sum'
    with pytest.raises(ValueError, match='capped'):
        compress.preview_day(PreviewSM(rows), 'c', 'auto', '', 2000, daily_instructions='x' * 9000)


def test_today_alone_is_nothing_to_compact(monkeypatch):
    """Sapphire, 2026-11-06: a noon compact split today into its own block."""
    now = datetime.now().isoformat()
    rows = _turn(now, 1) + _turn(now, 2)                                  # all today
    monkeypatch.setattr('config.COMPACT_DAILY_DAYS', 5, raising=False)
    monkeypatch.setattr('config.DAY_START_HOUR', 4, raising=False)
    monkeypatch.setattr(compress, 'make_provider', lambda *a, **k: pytest.fail('today is not compacted'))
    with pytest.raises(ValueError, match='today stays'):
        compress._compress_chat(FakeSM(rows), 'c', 'daily', 'auto', '', 3000, 1, True)


def test_a_private_chat_resolves_privately(monkeypatch):
    seen = {}
    def _resolve(*a, **k):
        seen.update(k)
        return SimpleNamespace(provider=Provider())
    monkeypatch.setattr('core.chat.llm_providers.resolve.resolve', _resolve)
    compress.make_provider('auto', '', conversation='c', private=True)
    assert seen['private'] is True


def test_a_days_size_is_a_ceiling_and_routine_tasks_are_not_recapped():
    head = _turn('2026-11-02T10:00:00', 1)
    p = Provider()
    compress.daily_pairs(compress.daily_plan(head, days=5, start_hour=4, now=NOW), p, 2000)
    assert 'ceiling, not a goal' in p.prompts[0] and 'SCHEDULED TASK' in p.prompts[0]
    assert 'tight prose' in p.prompts[0] and 'never in shorthand' in p.prompts[0]
    assert 'leaves out what happens every day' in p.prompts[0]
    q = Provider()                                                  # no day: the plain frame
    compress.compress_messages(head, 'whole', q, 2000)
    assert 'ceiling, not a goal' not in q.prompts[0]


def test_a_row_the_person_did_not_write_is_not_labelled_user():
    rows = [{'role': 'user', 'content': 'check the mail', 'origin': 'task:Morning mail'},
            {'role': 'assistant', 'content': 'nothing new'},
            {'role': 'user', 'content': 'tick', 'origin': 'cadence'},
            {'role': 'user', 'content': 'hello'}]
    text = compress.render_transcript(rows)
    assert 'SCHEDULED TASK (Morning mail): check the mail' in text
    assert 'NOT THE USER (cadence): tick' in text and '\n\nUSER: hello' in text
    assert 'USER: check the mail' not in text
    # A room names its speakers; that rule still comes first.
    room = compress.render_transcript([{'role': 'user', 'content': 'hi', 'name': 'Ann', 'origin': 'room:Ann'}], room=True)
    assert room == 'Ann: hi'


def test_the_day_wording_is_the_persons_to_edit_and_the_mechanics_are_not():
    """Krem, 2026-11-20: how she writes a day is editable. What the size means
    and what the transcript's labels mean stay in code, so no edit loses them."""
    assert compress.load_instructions(daily=True) == compress.DAILY_INSTRUCTIONS
    compress.save_instructions('  Keep the feelings {curly} and the jokes.  ', daily=True)
    assert compress.load_instructions(daily=True) == 'Keep the feelings {curly} and the jokes.'
    assert compress.load_instructions() == compress.DEFAULT_INSTRUCTIONS       # the other text is untouched
    head = _turn('2026-11-02T10:00:00', 1)
    p = Provider()
    compress.daily_pairs(compress.daily_plan(head, days=5, start_hour=4, now=NOW), p, 2000,
                         daily_instructions=compress.load_instructions(daily=True))
    assert 'Keep the feelings {curly} and the jokes.' in p.prompts[0]           # a brace is just a brace
    assert 'tight prose' not in p.prompts[0]
    assert 'ceiling, not a goal' in p.prompts[0] and 'SCHEDULED TASK are automatic jobs' in p.prompts[0]
    compress.save_instructions(compress.DAILY_INSTRUCTIONS, daily=True)        # the default is not stored
    assert not compress.DAILY_INSTRUCTIONS_PATH.exists()
    compress.save_instructions('edited', daily=True)
    compress.save_instructions('', daily=True)                                  # blank = back to the default
    assert compress.load_instructions(daily=True) == compress.DAILY_INSTRUCTIONS
    with pytest.raises(ValueError):
        compress.save_instructions('x' * (compress.MAX_INSTRUCTIONS_CHARS + 1), daily=True)


def test_the_job_reads_the_edited_day_wording(monkeypatch):
    compress.save_instructions('Write it as a haiku.', daily=True)
    rows = _turn('2026-11-01T10:00:00', 1) + _turn(NOW.isoformat(), 2) + _turn(datetime.now().isoformat(), 3)
    p = Provider()
    monkeypatch.setattr(compress, 'make_provider', lambda *a, **k: p)

    class SMx(FakeSM):
        def read_chat_settings(self, chat):
            return {}

        def replace_messages(self, chat, msgs, **k):
            return True, ''

        def messages_digest(self, msgs):
            return 'd'

        def _prune_orphaned_tool_images(self, chat):
            pass
    old = [{'role': 'user', 'content': 'long ago ' * 200, 'timestamp': (datetime.now().replace(hour=12)
            .fromordinal(datetime.now().toordinal() - 2)).isoformat()},
           {'role': 'assistant', 'content': 'yes ' * 200, 'timestamp': (datetime.now().replace(hour=12)
            .fromordinal(datetime.now().toordinal() - 2)).isoformat()}]
    compress._compress_chat(SMx(old + rows[-2:]), 'c', 'daily', 'auto', '', 2000, 1, backup=False)
    assert 'Write it as a haiku.' in p.prompts[0]


def test_what_a_keyed_read_returned_never_reaches_the_summarizer():
    """A summary carries no key, and a day's summary is kept in memory for the
    whole scope. Her own words about what she read stay: ordinary chat text."""
    rows = [
        {'role': 'user', 'content': 'what is the door code?'},
        {'role': 'assistant', 'content': '', 'tool_calls': [
            {'id': 'a1', 'type': 'function', 'function': {'name': 'search_memory',
             'arguments': '{"query": "door code", "private_key": "house"}'}},
            {'id': 'a2', 'type': 'function', 'function': {'name': 'search_memory',
             'arguments': '{"query": "weather"}'}},
            {'id': 'a3', 'type': 'function', 'function': {'name': 'get_recent_memories',
             'arguments': '{"private_key": "house", broken json'}},
            {'id': 'a4', 'type': 'function', 'function': {'name': 'search_memory',
             'arguments': '{"query": "x", "private_key": "  "}'}}]},
        {'role': 'tool', 'tool_call_id': 'a1', 'name': 'search_memory', 'content': 'Found 1: the code is 4417'},
        {'role': 'tool', 'tool_call_id': 'a2', 'name': 'search_memory', 'content': 'Found 1: rain on Tuesday'},
        {'role': 'tool', 'tool_call_id': 'a3', 'name': 'get_recent_memories', 'content': 'gate pin 9902'},
        {'role': 'tool', 'tool_call_id': 'a4', 'name': 'search_memory', 'content': 'nothing keyed here'},
        {'role': 'assistant', 'content': 'It is the one you set in March.'},
    ]
    text = compress.render_transcript(rows)
    assert '4417' not in text and '9902' not in text                       # unreadable arguments naming the key: withheld
    assert text.count('[withheld: read with a private key]') == 2
    assert '(search_memory result) [withheld' in text and 'rain on Tuesday' in text and 'nothing keyed here' in text
    assert 'It is the one you set in March.' in text and '[called tools: search_memory, search_memory' in text


def test_a_days_summary_is_written_without_tool_results(monkeypatch):
    """A real day (2026-11-21) retold the self sheet her 4am task had read:
    nine dates and seven people that had nothing to do with the day. A day is
    what was said; what a tool handed back is not shown, only that it ran.
    Plain /compact still shows results: a working chat leans on what was read."""
    sheet = 'I am the sheet. Pepper the cat, adopted Aug 4. ' * 400
    head = [
        {'role': 'user', 'content': 'run the night routine', 'timestamp': '2026-11-02T04:00:00'},
        {'role': 'assistant', 'content': 'Reading my sheet.', 'timestamp': '2026-11-02T04:00:01', 'tool_calls': [
            {'id': 'r1', 'type': 'function', 'function': {'name': 'read_self', 'arguments': '{}'}}]},
        {'role': 'tool', 'tool_call_id': 'r1', 'name': 'read_self', 'content': sheet, 'timestamp': '2026-11-02T04:00:02'},
        {'role': 'assistant', 'content': 'All quiet.', 'timestamp': '2026-11-02T04:00:03'},
    ] + _turn('2026-11-02T10:00:00', 1)
    monkeypatch.setattr(compress, 'CHUNK_INPUT_TOKENS', 2000)
    p = Provider()
    compress.daily_pairs(compress.daily_plan(head, days=5, start_hour=4, now=NOW), p, 2000)
    assert len(p.prompts) == 1                                      # the sheet no longer forces a second chunk
    assert 'Pepper' not in p.prompts[0] and 'read_self result' not in p.prompts[0]
    assert 'Reading my sheet.\n[called tools: read_self]' in p.prompts[0] and 'All quiet.' in p.prompts[0]
    assert 'What a tool returned is not shown' in p.prompts[0]
    q = Provider()
    compress.compress_messages(head, 'whole', q, 2000)
    assert len(q.prompts) > 2 and any('Pepper' in x for x in q.prompts) and 'not shown' not in q.prompts[0]


def test_a_hand_edited_instructions_file_is_read_whatever_the_editor_did(monkeypatch):
    """Windows scout, 2026-11-21. Notepad's "UTF-8 with BOM" put an invisible
    character at the head of the prompt; a file saved as ANSI failed every
    nightly compact with a codec error nobody could act on."""
    compress.DAILY_INSTRUCTIONS_PATH.write_bytes(b'\xef\xbb\xbfKeep the jokes.\r\nAnd the feelings.\r\n')
    assert compress.load_instructions(daily=True) == 'Keep the jokes.\nAnd the feelings.'
    compress.DAILY_INSTRUCTIONS_PATH.write_bytes('Keep the “jokes”.'.encode('cp1252'))
    with pytest.raises(ValueError) as e:
        compress.load_instructions(daily=True)
    assert 'not saved as UTF-8' in str(e.value) and 'compact_daily_instructions.txt' in str(e.value)
    compress.save_instructions('fixed from the app', daily=True)             # a Save from the box repairs it
    assert compress.load_instructions(daily=True) == 'fixed from the app'
    compress.DAILY_INSTRUCTIONS_PATH.write_bytes(b'\x93broken again\x94')
    compress.save_instructions('', daily=True)                               # and so does Reset
    assert compress.load_instructions(daily=True) == compress.DAILY_INSTRUCTIONS


def test_an_unreadable_day_wording_fails_the_job_before_a_backup_is_written(monkeypatch, tmp_path):
    """It was read after the backup: every night left a full export of the
    chat behind and then failed."""
    compress.DAILY_INSTRUCTIONS_PATH.write_bytes(b'\x93not utf-8\x94')
    wrote = []
    monkeypatch.setattr(compress, '_write_backup', lambda *a, **k: wrote.append(1) or 'x')
    monkeypatch.setattr(compress, 'make_provider', lambda *a, **k: Provider())
    old = (datetime.now() - timedelta(days=2)).replace(hour=12).isoformat()
    rows = [{'role': 'user', 'content': 'long ago ' * 200, 'timestamp': old},
            {'role': 'assistant', 'content': 'yes ' * 200, 'timestamp': old}] + _turn(datetime.now().isoformat(), 3)

    class Public(FakeSM):
        def read_chat_settings(self, chat):
            return {}
    with pytest.raises(ValueError, match='not saved as UTF-8'):
        compress._compress_chat(Public(rows), 'c', 'daily', 'auto', '', 2000, 1, backup=True)
    assert wrote == []
    # A whole or chunked compress never reads the day wording.
    monkeypatch.setattr(compress, '_write_backup', lambda *a, **k: 'x')
    with pytest.raises(Exception) as e:
        compress._compress_chat(Public(rows), 'c', 'whole', 'auto', '', 2000, 1, backup=True)
    assert 'not saved as UTF-8' not in str(e.value)


def test_the_day_wording_asks_for_paragraphs_that_stand_alone():
    """A day is stored and found paragraph by paragraph: one built on "he" or
    "tomorrow" is noise when it is read alone months later (AIX scout)."""
    text = compress.DAILY_INSTRUCTIONS
    assert 'in the first person (I/me) as the ASSISTANT' in text and 'your' not in text    # "you" is the USER in this prompt
    assert 'The USER is always "you"' in text
    assert 'Give each topic its own paragraph, and name people and things in it' in text
    assert 'write it as a date' in text and 'Do not guess a date' in text
    assert 'reciting its own sheet' in text and 'without a word that they ran' in text
    assert 'Keep one only for what was new in it' in text
    assert 'one short sentence saying it was a quiet day' in text


def test_a_timestamp_the_clock_cannot_place_is_no_day_not_a_crash():
    got = compress.day_of('0001-01-01T00:00:00+00:00')          # raises OSError inside on Windows: None there
    assert got is None or isinstance(got, date)
    assert compress.day_of('not a time') is None and compress.day_of(None) is None
