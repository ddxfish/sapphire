"""Prompt mutations emit audit events at the SAVER layer (2026-07-22).

Scout find: emits used to live in 4 routes + 3 tools, so persona import,
factory reset, and merge rewrote prompts with no ledger row. Now the
prompt_manager savers diff persisted state against an audit snapshot —
coverage is by construction: ANY writer that persists through them is
audited, and reload() turns out-of-band file edits (vim, reset, restored
backup) into events too. Boot seeds silently.
"""
import json

import pytest

from core import audit
from core.prompt_manager import PromptManager


@pytest.fixture
def pm(tmp_path):
    m = PromptManager()          # reads real user files (read-only)
    m.USER_DIR = tmp_path        # ...but persists only to the sandbox
    m._monoliths = {'sapph': {'content': 'v1', 'privacy_required': False}}
    m._scenario_presets = {}
    m._components = {'emotions': {'happy': 'joyful'}}
    m._audit_seed()
    return m


@pytest.fixture
def sink():
    # Snapshot-and-clear the registry: a previously imported palace sink
    # must NOT receive these synthetic events — it would route them against
    # the REAL mind.db (test pollution of live ledger data).
    with audit._lock:
        saved = dict(audit._sinks)
        audit._sinks.clear()
    got = []
    audit.register_sink('test-seam', got.append)
    yield got
    audit.flush(timeout=5)
    with audit._lock:
        audit._sinks.clear()
        audit._sinks.update(saved)


def test_monolith_saver_diffs_and_snapshot_prevents_double(pm, sink):
    pm._monoliths['sapph']['content'] = 'v2'
    pm.save_monoliths(reason='test why')
    assert audit.flush(timeout=5)
    assert len(sink) == 1
    e = sink[0]
    assert e['kind'] == 'monolith' and e['name'] == 'sapph'
    assert e['before'] == 'v1' and e['after'] == 'v2'
    assert e['reason'] == 'test why'
    pm.save_monoliths()          # unchanged re-save: snapshot already current
    assert audit.flush(timeout=5)
    assert len(sink) == 1


def test_audit_false_refreshes_snapshot_silently(pm, sink):
    pm._monoliths['sapph']['content'] = 'v3'
    pm.save_monoliths(audit=False)   # higher-level event covers it (activations)
    pm.save_monoliths()              # ...and it must not surface as phantom later
    assert audit.flush(timeout=5)
    assert sink == []


def test_component_saver_emits_per_changed_key(pm, sink):
    pm._components['emotions']['happy'] = 'joyful and light'   # changed
    pm._components['emotions']['feral'] = 'unhinged'           # added
    pm.save_components()
    assert audit.flush(timeout=5)
    assert len(sink) == 2
    by_key = {e['key']: e for e in sink}
    assert by_key['happy']['before'] == 'joyful'
    assert by_key['feral']['before'] == '' and by_key['feral']['after'] == 'unhinged'
    assert all(e['kind'] == 'component' and e['comp_type'] == 'emotions'
               for e in sink)


def test_reload_diff_catches_out_of_band_file_edits(pm, sink):
    # The vim scenario: the file changes underneath the process. reload()
    # (file watcher, factory reset, restored backup) diffs vs the snapshot.
    (pm.USER_DIR / 'prompt_monoliths.json').write_text(
        json.dumps({'sapph': {'content': 'edited in vim',
                              'privacy_required': False}}), encoding='utf-8')
    pm.reload(audit_reason='reset to factory defaults')
    assert audit.flush(timeout=5)
    mono = [e for e in sink if e['kind'] == 'monolith' and e['name'] == 'sapph']
    assert len(mono) == 1
    assert mono[0]['before'] == 'v1' and mono[0]['after'] == 'edited in vim'
    assert mono[0]['reason'] == 'reset to factory defaults'


def test_boot_seed_is_silent(tmp_path, sink):
    PromptManager()   # construction seeds the snapshot without emitting
    assert audit.flush(timeout=5)
    assert sink == []
