"""Import App Updates is vault-aware — pre-push hunt 2026-09-08 (E3#4).

A stock name moved INTO the vault has an empty plaintext slot. The merge
used to re-seed the shipped default there, and plaintext wins the overlay,
so a private, edited copy of a stock prompt silently reverted to stock
after the owner's post-pull click. Sealed = blind: a locked vault refuses.
"""
import json

import pytest

from core.prompt_manager import prompt_manager as pm
from core import prompt_vault as pv


@pytest.fixture
def stock(tmp_path, monkeypatch):
    core = tmp_path / 'core'
    core.mkdir()
    (core / 'prompt_monoliths.json').write_text(
        json.dumps({'sapphire': 'STOCK', 'other': 'x'}), encoding='utf-8')
    (core / 'prompt_pieces.json').write_text(json.dumps({
        'components': {'emotions': {'happy': 'h', 'sad': 's'}},
        'scenario_presets': {'cozy': {'emotions': ['happy']}, 'wild': {}}}), encoding='utf-8')
    monkeypatch.setattr(pm, 'CORE_DIR', core)
    for name in ('_monoliths', '_components', '_scenario_presets', '_spices', '_spice_meta'):
        monkeypatch.setattr(pm, name, {})
    for fn in ('save_monoliths', 'save_components', 'save_scenario_presets', 'save_spices', 'reload'):
        monkeypatch.setattr(pm, fn, lambda *a, **k: True)
    monkeypatch.setattr(pm, '_backup_user_files', lambda d: None)
    monkeypatch.setattr(pv, 'vault_exists', lambda: True)
    monkeypatch.setattr(pv, 'vault_unlocked', lambda: True)
    monkeypatch.setattr(pv, 'overlay_monoliths', lambda: {'sapphire': {'content': 'PRIVATE'}})
    monkeypatch.setattr(pv, 'overlay_components', lambda: {'emotions': {'sad': 'private'}})
    monkeypatch.setattr(pv, 'overlay_presets', lambda: {'wild': {}})
    return pm


def test_vaulted_stock_names_are_not_reseeded(stock):
    out = stock._merge_defaults_locked()
    assert 'sapphire' not in stock._monoliths, "the vaulted edit would have been shadowed by stock text"
    assert stock._monoliths['other']['content'] == 'x'
    assert 'sad' not in stock._components['emotions'] and 'happy' in stock._components['emotions']
    assert 'wild' not in stock._scenario_presets and 'cozy' in stock._scenario_presets
    assert out['added'] == {'components': 1, 'presets': 1, 'monoliths': 1, 'spice_categories': 0}


def test_locked_vault_refuses_the_merge(stock, monkeypatch):
    monkeypatch.setattr(pv, 'vault_unlocked', lambda: False)
    out = stock._merge_defaults_locked()
    assert 'locked' in out.get('error', '')
    assert stock._monoliths == {} and stock._components == {}


def test_no_vault_merges_everything(stock, monkeypatch):
    monkeypatch.setattr(pv, 'vault_exists', lambda: False)
    monkeypatch.setattr(pv, 'vault_unlocked', lambda: False)
    for fn in ('overlay_monoliths', 'overlay_components', 'overlay_presets'):
        monkeypatch.setattr(pv, fn, lambda: {})
    out = stock._merge_defaults_locked()
    assert out['added'] == {'components': 2, 'presets': 2, 'monoliths': 2, 'spice_categories': 0}


def test_route_surfaces_the_locked_refusal(client, monkeypatch):
    c, csrf = client
    from core import prompts
    monkeypatch.setattr(prompts.prompt_manager, 'merge_defaults',
                        lambda *a, **k: {'error': 'The prompt vault is locked'})
    r = c.post('/api/prompts/merge', headers={'X-CSRF-Token': csrf})
    assert r.status_code == 409 and 'locked' in r.json()['detail']
