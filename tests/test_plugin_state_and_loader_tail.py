"""Surface 4 tail — PluginState + plugin_loader remaining P1/P2 tests.

Covers:
  4.14 get_load_errors non-destructive read (contract FLIPPED 2026-08-31, N7)
  4.21 uninstall evicts plugin_state cache (already done but verify via PluginLoader)
  4.38 corrupted state file quarantined to .bad-{ts} suffix
  4.39 update_with_lock survives concurrent RMW (MCP/discord/telegram clobber class)
  4.40 plugin_state tmp file cleaned on write error

See tmp/coverage-test-plan.md Surface 4 P2.
"""
import json
import threading
from pathlib import Path

import pytest


# ─── 4.38 Corrupted state file quarantined to .bad-{ts} ──────────────────────

def test_corrupted_state_file_quarantined_to_bad_suffix(tmp_path, monkeypatch):
    """[REGRESSION_GUARD] If plugin_state/{name}.json is corrupted JSON, the
    loader quarantines it to {name}.json.bad-{ts} BEFORE returning an empty
    dict — otherwise the next save() silently overwrites whatever was
    salvageable. Often the only copy of plugin auth/state (MCP session).
    """
    import core.plugin_loader as pl
    monkeypatch.setattr(pl, 'PLUGIN_STATE_DIR', tmp_path)

    # Write a corrupt state file
    bad_path = tmp_path / 'mcp.json'
    bad_path.write_text('{"servers": {broken json')

    # Instantiate PluginState — triggers _load → quarantine
    state = pl.PluginState('mcp')

    # Original file no longer at that path
    assert not bad_path.exists(), "corrupted file not moved/quarantined"
    # Quarantine file exists with .bad- prefix
    bad_files = list(tmp_path.glob('mcp.json.bad-*'))
    assert len(bad_files) == 1, f"expected 1 quarantine file, found {bad_files}"
    # Original corrupt content preserved in quarantine
    assert '{"servers": {broken' in bad_files[0].read_text()
    # State dict is empty (graceful degradation)
    assert state.all() == {}


# ─── 4.39 update_with_lock survives concurrent RMW ───────────────────────────

def test_update_with_lock_survives_concurrent_rmw(tmp_path, monkeypatch):
    """[REGRESSION_GUARD] update_with_lock protects RMW patterns that broke
    in MCP/discord/telegram. 50 threads each incrementing a counter via
    update_with_lock — final count MUST equal 50 (no clobbers)."""
    import core.plugin_loader as pl
    monkeypatch.setattr(pl, 'PLUGIN_STATE_DIR', tmp_path)

    state = pl.PluginState('counter_plugin')
    N = 50

    def worker():
        state.update_with_lock('count', lambda current: (current or 0) + 1, default=0)

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(N)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert all(not t.is_alive() for t in threads), "threads deadlocked"
    assert state.get('count') == N, f"lost updates — expected {N}, got {state.get('count')}"


def test_update_with_lock_returns_new_value(tmp_path, monkeypatch):
    """update_with_lock returns the post-mutation value."""
    import core.plugin_loader as pl
    monkeypatch.setattr(pl, 'PLUGIN_STATE_DIR', tmp_path)
    state = pl.PluginState('p')
    result = state.update_with_lock('x', lambda cur: (cur or 0) + 5, default=0)
    assert result == 5
    # Second call sees the updated value
    result2 = state.update_with_lock('x', lambda cur: cur + 10)
    assert result2 == 15


# ─── 4.40 tmp file cleaned on write error ────────────────────────────────────

def test_plugin_state_tmp_file_cleaned_when_save_raises(tmp_path, monkeypatch):
    """[PROACTIVE] If the rename in _save fails (e.g. rename raises), the
    tmp file must NOT linger — otherwise /tmp slowly fills with orphans."""
    import core.plugin_loader as pl
    monkeypatch.setattr(pl, 'PLUGIN_STATE_DIR', tmp_path)

    state = pl.PluginState('p')
    # Monkeypatch Path.replace to raise on the tmp → real rename
    orig_replace = Path.replace
    def _raising(self, target):
        if self.name.endswith('.tmp') or '.tmp.' in self.name:
            raise OSError('disk full')
        return orig_replace(self, target)
    monkeypatch.setattr(Path, 'replace', _raising)

    with pytest.raises(OSError):
        state.save('key', 'value')

    # No leftover tmp files
    leftovers = list(tmp_path.glob('*.tmp*'))
    assert leftovers == [], f"tmp files leaked: {leftovers}"


# ─── 4.14 get_load_errors non-destructive read ──────────────────────────────────

def test_get_load_errors_is_non_destructive():
    """[CONTRACT FLIP — negspace N7/P1#12, 2026-08-31] get_load_errors used to
    DRAIN the list, so a background tab's /api/init could swallow the only
    report of a failed plugin. Now: non-destructive read (frontend dedupes its
    toasts per browser session via sessionStorage) with a size bound instead
    of a clear."""
    from core.plugin_loader import PluginLoader
    loader = PluginLoader()
    loader._load_errors = [
        {'plugin': 'a', 'error': 'x'},
        {'plugin': 'b', 'error': 'y'},
    ]
    first = loader.get_load_errors()
    assert len(first) == 2
    second = loader.get_load_errors()
    assert second == first, "read must NOT drain — background tabs swallowed errors"
    # Bound: >100 entries trims to the newest 50 (keeps latest, drops oldest)
    loader._load_errors = [{'plugin': f'p{i}', 'error': 'e'} for i in range(150)]
    bounded = loader.get_load_errors()
    assert len(bounded) == 50
    assert bounded[-1]['plugin'] == 'p149'
    assert bounded[0]['plugin'] == 'p100'
    # Mutating the returned list must not touch loader state (it's a copy)
    bounded.clear()
    assert len(loader.get_load_errors()) == 50
