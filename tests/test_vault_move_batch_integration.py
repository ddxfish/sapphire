"""Bulk vault move — the REAL movers, end to end, hermetic (2026-09-08).

tests/test_vault_move_batch.py proves the loop's contract with stubs. This
file drives `prompt_crud.move_batch` through the real `move_*_to_vault` /
`move_*_from_vault` paths against a tmp vault + tmp plaintext store: vault
bytes written, plaintext dicts and files emptied, refs stamped, and exactly
ONE change event per batch (the per-item echo storm was the whole bug).
"""
import json
import time

import pytest

import core.prompt_vault as pv
from core import prompt_crud
from core.prompt_manager import prompt_manager as pm


@pytest.fixture
def world(tmp_path, monkeypatch):
    # Vault → tmp, fast scrypt, no SSE / no audit sink / no chat handoff.
    monkeypatch.setattr(pv, 'VAULT_PATH', tmp_path / 'prompt_vault.enc')
    monkeypatch.setattr(pv, 'REFS_PATH', tmp_path / 'vault_refs.json')
    monkeypatch.setattr(pv, '_N', 2 ** 10)
    monkeypatch.setattr(pv, '_idle_seconds', lambda: 1800.0)
    monkeypatch.setattr(pv, '_publish', lambda action: None)
    monkeypatch.setattr(pv, '_handoff_active', lambda gone: None)
    pv.stop()
    pv._key = pv._salt = pv._data = None
    pv._refs, pv._refs_loaded = {}, False
    # Plaintext store → tmp, seeded in memory. Two presets share a piece.
    udir = tmp_path / 'prompts'
    udir.mkdir()
    monkeypatch.setattr(pm, 'USER_DIR', udir)
    monkeypatch.setattr(pm, '_load_failed',
                        {'pieces': False, 'monoliths': False, 'spices': False}, raising=False)
    monkeypatch.setattr(pm, '_audit_diff', lambda *a, **k: None)
    monkeypatch.setattr(pm, '_monoliths', {'mono-a': {'content': 'I am a monolith'}})
    # Stored presets are FLAT: component type → key (or list of keys). The
    # API's getPrompt wraps them under `components` for the client; the
    # vault's set_preset refuses nested dicts ('bad_value').
    monkeypatch.setattr(pm, '_scenario_presets', {
        'preset-a': {'character': 'hero', 'extras': ['quiet']},
        'preset-b': {'character': 'hero', 'location': 'lake'},
    })
    monkeypatch.setattr(pm, '_components', {
        'character': {'hero': 'A hero.'},
        'location': {'lake': 'A lake.'},
        'extras': {'quiet': 'Be quiet.'},
    })
    import core.event_bus as eb
    published = []
    monkeypatch.setattr(eb, 'publish', lambda et, data=None, **kw: published.append((str(et), data)))
    import core.audit as audit
    monkeypatch.setattr(audit, 'emit', lambda ev: None)
    ok, code = pv.setup('correct horse battery staple')
    assert ok, code
    yield {'published': published, 'udir': udir}
    pv.stop()
    pv._key = pv._salt = pv._data = None
    pv._refs, pv._refs_loaded = {}, False


ITEMS = [
    {'kind': 'prompt', 'name': 'preset-a'},
    {'kind': 'prompt', 'name': 'preset-b'},
    {'kind': 'prompt', 'name': 'mono-a'},
    {'kind': 'piece', 'comp_type': 'character', 'key': 'hero'},
    {'kind': 'piece', 'comp_type': 'location', 'key': 'lake'},
    {'kind': 'piece', 'comp_type': 'extras', 'key': 'quiet'},
]


def _events(world):
    return [(et.split('.')[-1].lower(), (d or {}).get('action')) for et, d in world['published']]


def test_batch_in_moves_everything_writes_disk_and_publishes_once(world):
    out = prompt_crud.move_batch('in', ITEMS)
    assert out['failed'] == 0 and out['moved'] == 6, out
    assert all(r['ok'] for r in out['results'])
    # pieces ran before prompts
    kinds = [r['item']['kind'] for r in out['results']]
    assert kinds == ['piece', 'piece', 'piece', 'prompt', 'prompt', 'prompt']
    # vault has them
    assert pv.vault_has_prompt('preset-a') and pv.vault_has_prompt('preset-b') and pv.vault_has_prompt('mono-a')
    for t, k in (('character', 'hero'), ('location', 'lake'), ('extras', 'quiet')):
        assert pv.vault_has_piece(t, k), f"{t}/{k} not in vault"
    # plaintext store no longer has them — in memory AND on disk
    assert pm._scenario_presets == {} and pm._monoliths == {}
    assert all(not v for v in pm._components.values())
    disk = json.loads((world['udir'] / 'prompt_pieces.json').read_text(encoding='utf-8'))
    assert 'hero' not in disk['components'].get('character', {})
    assert 'preset-a' not in disk['scenario_presets']
    assert (world['udir'] / 'prompt_vault.enc').exists() is False   # vault lives at VAULT_PATH, not the store
    assert pv.VAULT_PATH.exists()
    # ONE change event pair for the whole batch — not one per item
    ev = _events(world)
    assert ev.count(('prompt_changed', 'vault_changed')) == 1, ev
    assert ev.count(('components_changed', 'vault_changed')) == 1, ev


def test_batch_out_round_trips(world):
    prompt_crud.move_batch('in', ITEMS)
    world['published'].clear()
    out = prompt_crud.move_batch('out', ITEMS)
    assert out['failed'] == 0 and out['moved'] == 6, out
    assert not pv.vault_has_prompt('preset-a') and not pv.vault_has_piece('character', 'hero')
    assert set(pm._scenario_presets) == {'preset-a', 'preset-b'} and 'mono-a' in pm._monoliths
    assert pm._components['character'] == {'hero': 'A hero.'}
    assert pm._scenario_presets['preset-a'].get('_privacy_required') in (None, False)
    ev = _events(world)
    assert ev.count(('prompt_changed', 'vault_changed')) == 1


def test_a_failing_item_is_a_receipt_and_the_rest_still_move(world):
    items = ITEMS[:1] + [{'kind': 'prompt', 'name': 'ghost'}] + ITEMS[1:]
    out = prompt_crud.move_batch('in', items)
    assert out['moved'] == 6 and out['failed'] == 1
    bad = [r for r in out['results'] if not r['ok']]
    assert bad[0]['item']['name'] == 'ghost' and 'not found' in bad[0]['msg']
    assert pv.vault_has_prompt('preset-b')


def test_duplicate_item_in_one_batch_is_a_receipt_not_a_crash(world):
    """The client dedups mirrored rows; the server still answers honestly."""
    piece = {'kind': 'piece', 'comp_type': 'character', 'key': 'hero'}
    out = prompt_crud.move_batch('in', [piece, piece])
    assert out['moved'] == 1 and out['failed'] == 1
    assert 'not found' in out['results'][1]['msg']
    assert pv.vault_has_piece('character', 'hero')


def test_big_batch_completes_with_one_event(world, monkeypatch):
    """~120 prompts + their pieces in one request. The old per-item route
    took the same content five minutes; this must be seconds."""
    presets, comps = {}, {'character': {}}
    for i in range(120):
        presets[f'p{i}'] = {'character': f'c{i}'}
        comps['character'][f'c{i}'] = f'Character {i}.'
    monkeypatch.setattr(pm, '_scenario_presets', presets)
    monkeypatch.setattr(pm, '_components', comps)
    items = [{'kind': 'prompt', 'name': f'p{i}'} for i in range(120)] \
          + [{'kind': 'piece', 'comp_type': 'character', 'key': f'c{i}'} for i in range(120)]
    t0 = time.monotonic()
    out = prompt_crud.move_batch('in', items)
    took = time.monotonic() - t0
    assert out['failed'] == 0 and out['moved'] == 240, out
    assert took < 60, f"batch took {took:.1f}s"
    assert all(pv.vault_has_prompt(f'p{i}') for i in range(120))
    assert _events(world).count(('prompt_changed', 'vault_changed')) == 1
