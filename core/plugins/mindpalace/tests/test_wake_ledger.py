"""Wake composite + read_ledger (2026-07-12).

read_self(depth=1) is THE wake call — sheet + dashboard + ledger tail +
active goals + recent memories + spider walk in one tool. The ledger tail
is an OVERVIEW (per-line cap so ~all 10 lines fit inside the 768 budget);
read_ledger is the deep view behind it: full summaries, every actor, and
new_only against the watermark that read_self and read_ledger share.
"""
import pytest

from plugins.mindpalace.tools import palace_tools as pt
from plugins.mindpalace.tools import self_tools
from plugins.mindpalace.tools import goal_tools
from plugins.mindpalace.tools import ledger


class _FakeEmbedder:
    provider_id = "fake"

    @property
    def available(self):
        return False

    def embed(self, texts, prefix="search_document"):
        return None


@pytest.fixture
def palace(tmp_path, monkeypatch):
    monkeypatch.setattr(pt, "_db_path", tmp_path / "mind.db", raising=False)
    monkeypatch.setattr(pt, "_db_initialized", False, raising=False)
    monkeypatch.setattr(pt, "_backfill_done", True, raising=False)
    monkeypatch.setattr(pt, "_dark_cache", None, raising=False)
    monkeypatch.setattr(pt, "_get_embedder", lambda: _FakeEmbedder(), raising=False)
    return pt


# ─── Ledger tail: overview role ──────────────────────────────────────────────

def test_tail_lines_capped_for_overview(palace):
    # Before the per-line cap, one 300-char summary ate half the char budget
    # and the tail showed 2-3 lines. Overview means ~all TAIL_LINES visible
    # (7 of 8 at the 640-char cap — tad-trim, 2026-07-27).
    long = 'x' * 280
    for i in range(10):
        ledger.record('default', 'user', 'edited', summary=f"{i:02d} {long}")
    with pt._get_connection() as conn:
        block = ledger.tail_block(conn.cursor(), 'default')
    lines = [l for l in block.splitlines() if l.startswith('- ')]
    assert all(len(l) <= ledger.TAIL_LINE_CHARS for l in lines)
    assert len(lines) >= 7
    assert 'read_ledger' in block   # overflow points at the TOOL, not the UI


# ─── Wake composite ──────────────────────────────────────────────────────────

def test_wake_composite_depth1(palace):
    msg, ok = goal_tools._create('default', 'Fix the boat engine', priority='high')
    assert ok
    pt._save_memory('Watched the meteor shower from the driveway', 'default')

    d0, ok = self_tools._read_self('default', depth=0)
    assert ok
    assert 'Active goals' not in d0 and 'Recent memories' not in d0

    d1, ok = self_tools._read_self('default', depth=1)
    assert ok
    assert 'Active goals' in d1 and 'Fix the boat engine' in d1
    assert 'Recent memories' in d1 and 'meteor shower' in d1


def test_wake_goals_cap_and_descriptions(palace):
    # v2 contract (Krem 2026-07-16): name + description + prio at BOTH wake
    # depths — depth 1 truncates at 120 chars, depth 2 carries 240.
    long_desc = 'Success looks like ' + 'x' * 200
    for i in range(7):
        goal_tools._create('default', f'Goal number {i:02d}',
                           description=long_desc)
    d1, ok = self_tools._read_self('default', depth=1)
    assert ok
    assert 'Active goals (7)' in d1
    assert '…and 2 more (list_goals)' in d1
    assert 'Success looks like' in d1              # descriptions ride at depth 1
    assert 'x' * 90 in d1 and 'x' * 150 not in d1  # …but truncated
    d2, ok = self_tools._read_self('default', depth=2)
    assert ok and 'x' * 200 in d2                  # depth 2 carries the full 240


# ─── read_ledger ─────────────────────────────────────────────────────────────

def test_read_ledger_full_stream_and_new_only(palace):
    ledger.record('default', 'user', 'edited', layer='self', summary='wrote Values')
    ledger.record('default', 'ai', 'saved', layer='events', summary='routine save')

    text, ok = self_tools._read_ledger('default')
    assert ok
    # Deep view shows every actor — the tail's SHEET_WHERE exclusions don't apply.
    assert 'wrote Values' in text and 'routine save' in text

    # That read stamped the watermark.
    text, ok = self_tools._read_ledger('default', new_only=True)
    assert ok and 'Nothing new' in text

    ledger.record('default', 'librarian', 'merged', summary='merged two garden memories')
    text, ok = self_tools._read_ledger('default', new_only=True)
    assert ok and 'merged two garden' in text and 'wrote Values' not in text


def test_read_self_and_read_ledger_share_watermark(palace):
    ledger.record('default', 'user', 'edited', summary='pre-wake change')
    self_tools._read_self('default', depth=0)      # reading the sheet stamps it
    text, ok = self_tools._read_ledger('default', new_only=True)
    assert ok and 'Nothing new' in text


def test_read_ledger_count_clamp(palace):
    for i in range(30):
        ledger.record('default', 'user', 'edited', summary=f'change {i:02d}')
    text, ok = self_tools._read_ledger('default', count=10)
    assert ok
    assert sum(1 for l in text.splitlines() if l.startswith('- ')) == 10
    assert 'and 20 more' in text
    text, ok = self_tools._read_ledger('default', count='not-a-number')
    assert ok   # falls back to the default, never errors


def test_executor_wiring(palace, monkeypatch):
    monkeypatch.setattr(pt, '_get_current_scope', lambda: 'default')
    ledger.record('default', 'user', 'edited', summary='a visible change')
    text, ok = self_tools.execute('read_ledger', {'count': 5}, None)
    assert ok and 'a visible change' in text


# ─── read_ledger(id=…): the deep view (prompt-ledger Phase 1, 2026-07-22) ────

def test_stream_lines_carry_row_ids(palace):
    rid = ledger.record('default', 'user', 'edited', layer='self',
                        summary='wrote Voice')
    text, ok = self_tools._read_ledger('default')
    assert ok and f'- [{rid}] ' in text
    assert 'id= for a row' in text          # the head teaches the dive


def test_id_deep_view_renders_field_diffs(palace):
    rid = ledger.record('default', 'user', 'update', layer='entities',
                        target=27, summary='updated entity "Krem" — fields: background',
                        detail={'fields': {'background': ['', 'grown in a lab']}})
    text, ok = self_tools._read_ledger('default', ids=[rid])
    assert ok
    assert f'[{rid}]' in text and 'entities' in text and '27' in text
    assert 'background: "" → "grown in a lab"' in text


def test_id_deep_view_renders_before_after_reason(palace):
    rid = ledger.record('default', 'user', 'edited', layer='prompt',
                        target='component/emotions/happy',
                        summary='prompt piece "happy" removed',
                        detail={'before': 'old piece text', 'after': '',
                                'reason': 'wanted Sapph to choose her emotions'})
    text, ok = self_tools._read_ledger('default', ids=[rid])
    assert ok
    assert 'reason: wanted Sapph to choose her emotions' in text
    assert 'before: old piece text' in text


def test_id_deep_view_lists_pass_children(palace):
    parent = ledger.record('default', 'librarian', 'pass',
                           summary='dedup pass — 2 merged')
    kid = ledger.record('default', 'librarian', 'merged',
                        summary='merged two garden memories', parent_id=parent)
    text, ok = self_tools._read_ledger('default', ids=[parent])
    assert ok
    assert 'children (1):' in text and f'[{kid}] merged' in text


def test_id_deep_view_is_scope_guarded(palace):
    rid = ledger.record('secret', 'user', 'edited', summary='private change')
    text, ok = self_tools._read_ledger('default', ids=[rid])
    assert ok
    assert 'no such entry' in text and 'private change' not in text


def test_id_deep_view_clips_huge_values(palace):
    rid = ledger.record('default', 'user', 'edited',
                        detail={'before': 'y' * 6000, 'after': 'z'})
    text, ok = self_tools._read_ledger('default', ids=[rid])
    assert ok and '… [truncated]' in text
    assert 'y' * (ledger.DETAIL_VALUE_CHARS + 10) not in text


def test_parse_ledger_ids_accepts_model_variants(palace):
    p = self_tools._parse_ledger_ids
    assert p(1234) == [1234]
    assert p('1234') == [1234]
    assert p('1234, 1235') == [1234, 1235]
    assert p([1234, '1235']) == [1234, 1235]
    assert p(None) is None
    assert p('garbage') is None
    assert len(p(','.join(str(i) for i in range(50)))) == 10   # capped


def test_id_deep_view_via_executor_and_stamps_watermark(palace, monkeypatch):
    monkeypatch.setattr(pt, '_get_current_scope', lambda: 'default')
    rid = ledger.record('default', 'user', 'edited', summary='the change',
                        detail={'fields': {'notes': ['old', 'new']}})
    text, ok = self_tools.execute('read_ledger', {'id': str(rid)}, None)
    assert ok and 'notes: "old" → "new"' in text
    text, ok = self_tools._read_ledger('default', new_only=True)
    assert ok and 'Nothing new' in text     # the dive counts as reading
