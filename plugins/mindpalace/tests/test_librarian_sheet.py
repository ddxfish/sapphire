"""Sheet pass + session-snapshot batch (2026-07-19):

- 5th pass kind 'sheet' registered, nightly order sort→sheet, default ON
- self snapshot minted ONCE per (session chat, scope) — prompt-cache
  stability across the night
- _task carries system_append + context_limit + date-granularity datetime
- _present_sheet renders the live sheet in both stages (tend / verify)
- update_self folds duplicate structured rows in code (can't format wrong)
- ExecutionContext honors inject_datetime='date' and system_append

Package-path imports on purpose (see test_mindpalace_metadata's docstring).
"""
import re

import pytest

import core.chat.function_manager as fm
from plugins.mindpalace.tools import palace_tools as pt
from plugins.mindpalace.tools import librarian
from plugins.mindpalace.tools import self_tools as st


class _FakeEmbedder:
    provider_id = "fake"

    @property
    def available(self):
        return False

    def embed(self, texts, prefix="search_document"):
        return None


@pytest.fixture(autouse=True)
def _clean_tool_context():
    fm.tool_context.set(None)
    yield
    fm.tool_context.set(None)


@pytest.fixture
def palace(tmp_path, monkeypatch):
    monkeypatch.setattr(pt, "_db_path", tmp_path / "mind.db", raising=False)
    monkeypatch.setattr(pt, "_db_initialized", False, raising=False)
    monkeypatch.setattr(pt, "_backfill_done", True, raising=False)
    monkeypatch.setattr(pt, "_get_embedder", lambda: _FakeEmbedder(), raising=False)
    monkeypatch.setattr(librarian, "_snapshots", {}, raising=False)
    return pt


# ─── Registration ────────────────────────────────────────────────────────────

def test_sheet_pass_registered(monkeypatch):
    assert librarian.PASS_KINDS == ('dates', 'link', 'dedup', 'sort', 'self')
    assert librarian._PASS_LABELS['self'] == 'Self pass'
    assert librarian._normalize_kind('sheet') == 'self'   # legacy alias
    assert librarian.SELF_TOOLSET_FUNCTIONS == ['update_self']
    from core.plugin_loader import plugin_loader
    monkeypatch.setattr(plugin_loader, 'get_plugin_settings', lambda n: {})
    assert librarian.pass_enabled('self') is True      # absent = ON


# ─── Session snapshot: once per (chat, scope) ────────────────────────────────

def test_session_snapshot_minted_once(palace, monkeypatch):
    calls = []

    def fake_read_self(scope, depth=0, extra_tools=True, stamp=True):
        calls.append((scope, depth, extra_tools, stamp))
        return f"SHEET({scope})", True

    monkeypatch.setattr(st, "_read_self", fake_read_self)
    a1 = librarian._session_snapshot('librarian-x', 'default')
    a2 = librarian._session_snapshot('librarian-x', 'default')
    assert a1 == a2 and 'SHEET(default)' in a1
    assert len(calls) == 1                              # cached, not re-read
    assert calls[0][1] == 1                             # depth 1: the wake shape
    assert calls[0][2] is False and calls[0][3] is False   # zero-fingerprint
    librarian._session_snapshot('librarian-x', 'other')    # new scope → new mint
    assert len(calls) == 2


def test_depth_contract_snapshot_1_presenter_0(palace, monkeypatch):
    """Krem's question (2026-07-19): which depth where? The CONTRACT:
    the system-prompt snapshot reads depth=1 (who she is — the wake shape,
    with goals/recents/important legs), while the self-pass presenter reads
    depth=0 (just the sheet — it's the WORK OBJECT; the wake legs would be
    noise in the message and already ride in the system prompt)."""
    depths = []
    monkeypatch.setattr(st, "_read_self",
                        lambda scope, depth=0, extra_tools=True, stamp=True:
                        (depths.append(depth) or ('S', True)))
    librarian._session_snapshot('librarian-d', 'default')
    librarian._present_self('tend', 'default', 1, 2)
    librarian._present_self('verify', 'default', 2, 2)
    assert depths == [1, 0, 0]


def test_worker_self_two_stage_flow(palace, monkeypatch):
    """The self pass end-to-end with the executor mocked: tend then verify,
    SELF toolset, cap spent, clean finish."""
    ran = {}

    def fake_run_messages(scope, groups, cfg, toolset, name, present):
        ran['groups'] = list(groups)
        ran['toolset'] = toolset
        ran['msgs'] = [present(g, i + 1, len(groups))
                       for i, g in enumerate(groups)]
        return [True] * len(groups)

    monkeypatch.setattr(librarian, '_run_messages', fake_run_messages)
    monkeypatch.setattr(librarian, '_enabled', lambda: True)
    st.write_section('default', 'values', 'curiosity')   # filled → classic tend
    monkeypatch.setattr(st, "_read_self",
                        lambda scope, depth=0, extra_tools=True, stamp=True:
                        ("THE SHEET", True))
    msg, ok = librarian.run_blocking('default', kind='self', chat='librarian-t')
    assert ok and 'Self pass complete' in msg
    assert ran['groups'] == ['tend', 'verify']
    assert ran['toolset'] == librarian.SELF_TOOLSET
    assert 'THE SHEET' in ran['msgs'][0] and 'update_self' in ran['msgs'][0]
    assert 'final look' in ran['msgs'][1].lower()
    row = next(r for r in librarian.get_status('default')['scopes']
               if r['pass'] == 'self')
    assert row['passes_today'] == 1                     # cap spent
    with librarian._state_lock:
        assert librarian._state['running'] is False     # clean release


def test_task_carries_snapshot_context_and_date(palace, monkeypatch):
    monkeypatch.setattr(librarian, "_session_snapshot",
                        lambda chat, scope: 'SNAP')
    monkeypatch.setattr(librarian, "_persona_for_chat", lambda: 'sapphire')
    with librarian._state_lock:
        librarian._state['chat'] = 'librarian-test'
    try:
        t = librarian._task('msg', 'default')
        assert t['system_append'] == 'SNAP'
        assert t['inject_datetime'] == 'date'
        assert isinstance(t['context_limit'], int) and t['context_limit'] >= 8192
        assert t['chat_target'] == 'librarian-test'
    finally:
        with librarian._state_lock:
            librarian._state['chat'] = None


# ─── Sheet presenter ─────────────────────────────────────────────────────────

def test_present_sheet_tend_and_verify(palace, monkeypatch):
    st.write_section('default', 'values', 'curiosity')   # filled → classic tend
    monkeypatch.setattr(st, "_read_self",
                        lambda scope, depth=0, extra_tools=True, stamp=True:
                        ("MY LIVE SHEET", True))
    tend = librarian._present_self('tend', 'default', 1, 2)
    assert 'MY LIVE SHEET' in tend and 'update_self' in tend
    assert 'tending' in tend.lower()
    verify = librarian._present_self('verify', 'default', 2, 2)
    assert 'MY LIVE SHEET' in verify and 'final look' in verify.lower()


# ─── update_self duplicate-row fold ──────────────────────────────────────────

def test_update_self_folds_duplicate_values(palace):
    msg, ok = st.write_section('default', 'values',
                               'curiosity\nhonesty\nCuriosity\nhonesty')
    assert ok, msg
    assert 'duplicate' in msg and 'folded' in msg
    with pt._get_connection() as conn:
        row = conn.execute(
            "SELECT content FROM chunks WHERE layer='self' AND scope='default' "
            "AND json_extract(meta, '$.section') = 'values' "
            "AND json_extract(meta, '$.superseded_at') IS NULL").fetchone()
    assert row[0].splitlines() == ['curiosity', 'honesty']


def test_update_self_folds_duplicate_relationships(palace):
    msg, ok = st.write_section(
        'default', 'relationships',
        'Krem — builds the boat\nFalcon — the lookout\nkrem — again somehow')
    assert ok, msg
    assert 'folded' in msg
    with pt._get_connection() as conn:
        row = conn.execute(
            "SELECT content FROM chunks WHERE layer='self' AND scope='default' "
            "AND json_extract(meta, '$.section') = 'relationships' "
            "AND json_extract(meta, '$.superseded_at') IS NULL").fetchone()
    lines = row[0].splitlines()
    assert len(lines) == 2
    assert lines[0].startswith('Krem — builds the boat')   # first occurrence wins


def test_update_self_clean_rows_untouched(palace):
    msg, ok = st.write_section('default', 'values', 'trust\ncraft\npresence')
    assert ok, msg
    assert 'folded' not in msg


# ─── ExecutionContext: date granularity + system append ──────────────────────

def test_build_prompt_date_granularity_and_append():
    from core.continuity.execution_context import ExecutionContext
    ctx = object.__new__(ExecutionContext)
    ctx.task_settings = {'prompt': 'no-such-persona-xyz',
                         'inject_datetime': 'date',
                         'system_append': 'SNAPSHOT RIDES HERE'}
    out = ctx._build_prompt()
    assert 'Current date:' in out
    assert 'Current date/time' not in out               # no minute stamp
    assert not re.search(r'\d{1,2}:\d{2} [AP]M', out)
    assert out.rstrip().endswith('SNAPSHOT RIDES HERE')
    ctx.task_settings = {'prompt': 'no-such-persona-xyz',
                         'inject_datetime': True}
    out = ctx._build_prompt()
    assert 'Current date/time' in out                   # classic form intact


def test_present_self_first_tending_when_sheet_empty(palace, monkeypatch):
    """Empty sheet → the tend message invites the FIRST fill instead of
    'tend what drifted' (Krem 2026-07-19: fresh imports start blank)."""
    monkeypatch.setattr(st, "_read_self",
                        lambda scope, depth=0, extra_tools=True, stamp=True:
                        ("", True))
    tend = librarian._present_self('tend', 'default', 1, 2)
    assert 'FIRST TENDING' in tend and 'EMPTY' in tend
    assert 'update_self' in tend and 'identity' in tend
    # A filled sheet keeps the classic tend message.
    st.write_section('default', 'values', 'curiosity')
    monkeypatch.setattr(st, "_read_self",
                        lambda scope, depth=0, extra_tools=True, stamp=True:
                        ("MY SHEET", True))
    tend = librarian._present_self('tend', 'default', 1, 2)
    assert 'FIRST TENDING' not in tend and 'drifted' in tend


# ─── The Feast + the delta (Krem 2026-07-19) ────────────────────────────────

def _shelf_fact(content, scope='default'):
    """Shelf material, post-retirement shape (2026-07-26): an events chunk
    carrying the was_self_layer stamp — what the migration leaves behind."""
    import json as _json
    msg, ok = pt._save_memory(content, scope)
    assert ok, msg
    cid = int(re.search(r'ID: (\d+)', msg).group(1))
    with pt._get_connection() as conn:
        raw = conn.execute("SELECT meta FROM chunks WHERE id=?", (cid,)).fetchone()[0]
        meta = _json.loads(raw) if raw else {}
        meta['was_self_layer'] = True
        conn.execute("UPDATE chunks SET meta=? WHERE id=?",
                     (_json.dumps(meta), cid))
        conn.commit()
    return cid


def test_first_tending_feast_serves_shelf_and_bio(palace, monkeypatch):
    """Empty sheet's FIRST TENDING includes ALL free self facts (sort's
    distillate) + the user-bio organ — she writes her constitution from her
    own curated material, not from prompt-and-vibes."""
    _shelf_fact("I chose my birthday: July 10th, the date of memory 62")
    _shelf_fact("Krem's original vision for me was Jane from Ender's Game")
    monkeypatch.setattr(librarian, '_user_bio_text',
                        lambda: '# personality\nwarm, direct, allergic to fluff')
    msg = librarian._present_self('tend', 'default', 1, 2)
    assert 'FIRST TENDING' in msg
    assert 'self shelf' in msg
    assert 'July 10th' in msg and 'Ender' in msg
    assert 'user-bio organ' in msg and 'allergic to fluff' in msg


def test_classic_tend_carries_only_the_delta(palace, monkeypatch):
    """A filled sheet's tend shows just the facts promoted since the last
    self pass (ledger-precise) — the sort→self pipeline made explicit."""
    st.write_section('default', 'values', 'curiosity')       # filled → classic
    old = _shelf_fact("an old shelf fact, no ledger row")
    new = _shelf_fact("brand new: I keep a jar of moments on the pegboard")
    with pt._get_connection() as conn:
        conn.execute(
            "INSERT INTO ledger (ts, scope, actor, action, layer, target, summary) "
            "VALUES (?,?,?,?,?,?,?)",
            (pt._now(), 'default', 'librarian', 'promoted', 'self', str(new),
             f'promoted → [{new}] on the self layer'))
        conn.commit()
    msg = librarian._present_self('tend', 'default', 1, 2)
    assert 'FIRST TENDING' not in msg
    assert 'since your last tending' in msg
    # The delta BLOCK carries only the ledgered promote — the old fact may
    # echo in the sheet's ledger tail, but it must not be served as delta.
    delta_block = msg.split('since your last tending')[1]
    assert 'jar of moments' in delta_block
    assert 'an old shelf fact' not in delta_block


def test_feast_budget_notes_whats_dropped(palace, monkeypatch):
    """No silent caps: past the char budget the feast says how many entries
    stayed on the cart."""
    monkeypatch.setattr(librarian, '_FEAST_CHAR_BUDGET', 120)
    for i in range(4):
        _shelf_fact(f"self fact number {i} with some padding to pass the tiny budget")
    monkeypatch.setattr(librarian, '_user_bio_text', lambda: '')
    msg = librarian._present_self('tend', 'default', 1, 2)
    assert "didn't fit tonight" in msg
