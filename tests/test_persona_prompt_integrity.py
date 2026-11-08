"""[REGRESSION_GUARD] Persona/prompt hunt waves W4-W6 (2026-11-08).

Hermetic: tmp persona dir, stubbed prompt savers, fake system — nothing
touches the real stores. Shares fixtures with test_rename_ownership.py.
"""
import pytest

from test_rename_ownership import mgr, world, presets  # noqa: F401 — fixtures


# ── W4: editor integrity ──

def test_settings_update_merges_not_replaces(mgr):
    mgr._personas['zed']['settings'].update(
        {'motion': 'sway', 'memory_scope': 'zed', 'discord_scope': 'zedbot'})
    assert mgr.update('zed', {'settings': {'trim_color': '#333333'}})
    s = mgr._personas['zed']['settings']
    assert s['trim_color'] == '#333333'
    assert s['motion'] == 'sway' and s['memory_scope'] == 'zed'
    assert s['discord_scope'] == 'zedbot' and s['prompt'] == 'ronnie'


def test_client_cannot_set_avatar_field(mgr):
    assert mgr.create('eve', {'avatar': '../personas.json', 'settings': {}})
    assert mgr._personas['eve']['avatar'] is None
    assert mgr.update('zed', {'avatar': '../personas.json', 'avatar_full': 'x'})
    assert mgr._personas['zed']['avatar'] is None


@pytest.mark.parametrize('bad', ['../personas.json', 'a/b.webp', '..', '', None, 'a\\b.webp'])
def test_legacy_avatar_paths_never_joined(mgr, bad):
    victim = mgr.USER_DIR / 'personas.json'
    victim.write_text('{}')
    mgr._personas['zed']['avatar'] = bad
    assert mgr.get_avatar_path('zed') is None
    assert mgr.delete_avatar('zed')
    assert victim.exists()


# ── W5: her tools never overwrite by accident ──

@pytest.fixture
def piece_store(tmp_path, monkeypatch):
    import core.prompt_vault as pv
    import core.prompt_crud as crud
    from core.prompt_manager import prompt_manager
    comps = {'character': {'sapphire': 'original text'}}
    monkeypatch.setattr(prompt_manager, '_components', comps)
    monkeypatch.setattr(prompt_manager, 'save_components', lambda reason=None, audit=True: True)
    monkeypatch.setattr(crud, '_publish_components_changed', lambda action: None, raising=False)
    monkeypatch.setattr(pv, 'VAULT_PATH', tmp_path / 'no_vault.enc')
    monkeypatch.setattr(pv, 'REFS_PATH', tmp_path / 'no_refs.json')
    return comps


def test_piece_create_refuses_existing_key(piece_store):
    from functions.meta import _prompt_pieces
    msg, ok = _prompt_pieces({'action': 'create', 'component': 'character',
                              'key': 'Sapphire', 'value': 'replacement'})
    assert not ok and "edit" in msg
    assert piece_store['character']['sapphire'] == 'original text'


def test_piece_edit_replaces_existing_only(piece_store):
    from functions.meta import _prompt_pieces
    msg, ok = _prompt_pieces({'action': 'edit', 'component': 'character',
                              'key': 'sapphire', 'value': 'new text'})
    assert ok, msg
    assert piece_store['character']['sapphire'] == 'new text'
    msg, ok = _prompt_pieces({'action': 'edit', 'component': 'character',
                              'key': 'nobody', 'value': 'x'})
    assert not ok and 'nobody' not in piece_store['character']


def test_fork_from_generic_state_never_overwrites(presets, monkeypatch):
    import core.prompts as prompts
    from functions.meta import _save_and_activate_assembled
    monkeypatch.setattr(prompts, 'get_active_preset_name', lambda: 'default')
    monkeypatch.setattr(prompts, 'activate_prompt', lambda name, system: (True, ''))
    before = dict(presets.ps['ronnie'])
    name = _save_and_activate_assembled(None, {'character': 'ronnie', 'extras': ['beach']})
    assert name == 'ronnie-2'
    assert presets.ps['ronnie'] == before
    assert presets.ps['ronnie-2']['extras'] == ['beach']


# ── W1: one name authority ──

@pytest.mark.parametrize('bad', ['', '  ', ' lead', 'trail ', '_x', 'a/b', 'a\\b',
                                 'default', 'assembled', 'pieces', 'components', 'piece-usage',
                                 'ronnie'])
def test_prompt_name_problem_refuses(presets, bad):
    import core.prompt_crud as crud
    assert crud.prompt_name_problem(bad)


def test_prompt_name_problem_sealed_vault_name(presets, monkeypatch):
    import core.prompt_crud as crud
    import core.chat.history as h
    monkeypatch.setattr(h, 'sealed_prompt_name', lambda n: n == 'luna')
    assert 'locked vault' in crud.prompt_name_problem('luna')
    assert crud.prompt_name_problem('sol') is None


def test_piece_key_problem(piece_store, monkeypatch):
    import core.prompt_crud as crud
    assert crud.piece_key_problem('character', 'sapphire')          # taken
    assert crud.piece_key_problem('character', 'a,b')               # keep-list breaker
    assert crud.piece_key_problem('bogus', 'x')                     # junk type
    assert crud.piece_key_problem('character', 'a/b')
    assert crud.piece_key_problem('character', 'fresh') is None


def test_rename_piece_uses_key_check(piece_store):
    import core.prompt_crud as crud
    ok, msg = crud.rename_piece('character', 'sapphire', 'a/b')
    assert not ok and 'sapphire' in piece_store['character']


# ── W3: one referrer registry ──

def test_persona_rename_moves_every_referrer(mgr, world):
    import json
    assert mgr.update('zed', {'name': 'zed2'})
    assert world.tokens[0]['persona'] == 'zed2'
    assert world.seats['den'][0]['persona'] == 'zed2'
    assert json.loads(world.defaults.read_text())['persona'] == 'zed2'
    assert world.chats['c1']['persona'] == 'zed2'
    assert world.settings.kv['DEFAULT_PERSONA'] == 'zed2'


def test_persona_delete_releases_name(mgr, world):
    import json
    assert mgr.delete('zed')
    assert world.tokens[0]['persona'] is None           # no takeover by a future 'zed'
    assert world.chats['c1']['persona'] == ''           # not the dangling 'default'
    assert json.loads(world.defaults.read_text())['persona'] == ''
    assert world.seats['den'][0]['persona'] == 'zed'    # seat kept; key is cleared


def test_prompt_rename_moves_chat_defaults(presets, world):
    import json
    import core.prompt_crud as crud
    ok, _ = crud.rename_prompt('ronnie', 'ronnie2')
    assert ok
    assert json.loads(world.defaults.read_text())['prompt'] == 'ronnie2'


def test_prompt_rename_unreferenced_never_stamps_refs(presets, world, monkeypatch):
    import core.prompt_crud as crud
    calls = []
    monkeypatch.setattr(crud, 'vault_ref_sync', lambda new, old: calls.append((new, old)))
    ok, _ = crud.rename_prompt('beach', 'beach2')       # chat c2 refs it → stamp
    assert ok and calls[-1] == ('beach2', 'beach')
    world.chats.clear()
    world.defaults.write_text('{}')
    for p in list(world.tasks):
        p['prompt'] = 'other'
    import sys
    sys.modules['core.personas.persona_manager'].persona_manager._personas.clear()
    ok, _ = crud.rename_prompt('ronnie', 'ronnie3')     # nobody refs it → no stamp
    assert ok and calls[-1] == (None, 'ronnie')


def test_token_repoint_persona_real_impl(monkeypatch):
    import threading
    from core.api_tokens import ApiTokensManager
    m = ApiTokensManager.__new__(ApiTokensManager)
    m._lock = threading.RLock()
    m._tokens = [{'name': 'k1', 'persona': 'zed'}, {'name': 'k2', 'persona': 'amy'}]
    m._save = lambda: True
    assert m.repoint_persona('zed', 'zed2') == ['k1']
    assert m._tokens[0]['persona'] == 'zed2' and m._tokens[1]['persona'] == 'amy'
    assert m.repoint_persona('zed2', None) == ['k1'] and m._tokens[0]['persona'] is None
    m._save = lambda: False
    with pytest.raises(RuntimeError):
        m.repoint_persona('amy', 'x')
    assert m._tokens[1]['persona'] == 'amy'


def test_task_with_deleted_persona_fails_loudly(mgr, monkeypatch):
    import sys
    from core.continuity.executor import ContinuityExecutor
    monkeypatch.setattr(sys.modules['core.personas.persona_manager'], 'persona_manager', mgr)
    import core.personas as cp
    monkeypatch.setattr(cp, 'persona_manager', mgr)
    ex = ContinuityExecutor.__new__(ContinuityExecutor)
    with pytest.raises(LookupError):
        ex._resolve_persona({'persona': 'ghost', 'prompt': 'default'})


# ── W2: one import funnel ──

@pytest.fixture
def importer(presets, piece_store, mgr, world, monkeypatch):
    import core.personas as cp
    monkeypatch.setattr(cp, 'persona_manager', mgr)
    import core.routes.content as content
    from core.prompt_manager import prompt_manager
    monkeypatch.setattr(content, 'publish', lambda *a, **k: None)
    # Never the real monolith store (a monolith card import writes here).
    monkeypatch.setattr(prompt_manager, '_monoliths', {})
    monkeypatch.setattr(prompt_manager, 'save_monoliths', lambda reason=None, audit=True: True)
    return content


def _card(**over):
    b = {'sapphire_export': True, 'type': 'persona', 'name': 'Nova', 'tagline': 'hi',
         'voice': {'voice': 'af_x', 'speed': 1.1, 'pitch': 1.0},
         'prompt': {'name': 'nova', 'data': {'type': 'monolith', 'content': 'You are Nova.'}},
         'components': {'character': {'nova': 'Nova text'}}}
    b.update(over)
    return b


def test_card_overwrite_merges_persona(importer, mgr):
    import asyncio
    mgr._personas['zed']['settings'].update({'memory_scope': 'zed', 'toolset': 'work'})
    res = asyncio.run(importer._import_persona_from_bundle(
        _card(name='zed', overwrite_persona=True, prompt=None, components=None)))
    s = mgr._personas['zed']['settings']
    assert res['name'] == 'zed'
    assert s['memory_scope'] == 'zed' and s['toolset'] == 'work'   # kept
    assert s['voice'] == 'af_x' and s['speed'] == 1.1              # card fields landed


@pytest.mark.parametrize('bad', [float('nan'), float('inf'), 'fast', {}, 99])
def test_card_bad_numbers_refused_before_any_write(importer, presets, mgr, bad):
    import asyncio
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        asyncio.run(importer._import_persona_from_bundle(_card(voice={'speed': bad})))
    assert ei.value.status_code == 400
    assert 'nova' not in mgr._personas and 'nova' not in presets.ps


def test_card_with_unusable_name_writes_nothing(importer, presets, piece_store, mgr):
    import asyncio
    from fastapi import HTTPException
    from core.prompt_manager import prompt_manager
    with pytest.raises(HTTPException) as ei:
        asyncio.run(importer._import_persona_from_bundle(_card(name='💎')))
    assert ei.value.status_code == 400
    assert 'nova' not in prompt_manager._monoliths
    assert 'nova' not in piece_store['character']


def test_card_cannot_shadow_sealed_vault_prompt(importer, monkeypatch, mgr):
    import asyncio
    import core.chat.history as h
    from fastapi import HTTPException
    monkeypatch.setattr(h, 'sealed_prompt_name', lambda n: n == 'nova')
    with pytest.raises(HTTPException) as ei:
        asyncio.run(importer._import_persona_from_bundle(_card()))
    assert ei.value.status_code == 409 and 'vault' in ei.value.detail
    assert 'nova' not in mgr._personas


def test_card_default_sentinel_prompt_ignored(importer, mgr):
    import asyncio
    asyncio.run(importer._import_persona_from_bundle(
        _card(prompt={'name': 'default', 'data': {'type': 'assembled', 'components': {}}})))
    assert 'prompt' not in mgr._personas['nova']['settings']


def test_prompt_import_taken_name_409_writes_nothing(importer, presets, piece_store):
    from fastapi import HTTPException
    before = dict(presets.ps['ronnie'])
    with pytest.raises(HTTPException) as ei:
        importer._install_prompt_bundle('ronnie', {'type': 'assembled', 'components': {'character': 'x'}},
                                        {'character': {'sapphire': 'CLOBBER'}})
    assert ei.value.status_code == 409
    assert presets.ps['ronnie'] == before
    assert piece_store['character']['sapphire'] == 'original text'


def test_batch_keeps_empty_local_piece(piece_store):
    import core.prompt_crud as crud
    piece_store['format'] = {'none': ''}
    ok, msg = crud.save_components_batch({'format': {'none': 'incoming'},
                                          'bogus': {'k': 'v'},
                                          'character': {'_x': 'v', 'a,b': 'v'}}, overwrite=False)
    assert ok and msg == 'Wrote 0 pieces'
    assert piece_store['format']['none'] == ''
    assert 'bogus' not in piece_store


def test_keep_set_parses_json_and_legacy():
    from core.routes.content import _keep_set
    assert _keep_set('["character/Luna, the Moon"]') == {('character', 'Luna, the Moon')}
    assert _keep_set('character/a,extras/b') == {('character', 'a'), ('extras', 'b')}
    assert _keep_set(['x/y']) == {('x', 'y')}


def test_card_new_persona_installs_prompt_then_pieces(importer, piece_store, mgr):
    import asyncio
    from core.prompt_manager import prompt_manager
    res = asyncio.run(importer._import_persona_from_bundle(_card()))
    assert res['name'] == 'nova'
    assert prompt_manager._monoliths['nova']['content'] == 'You are Nova.'
    assert piece_store['character']['nova'] == 'Nova text'
    assert mgr._personas['nova']['settings']['prompt'] == 'nova'


# ── New personas start with every scope off (Krem, 2026-11-08) ──

@pytest.fixture
def two_scopes(monkeypatch):
    import core.chat.function_manager as fm
    monkeypatch.setattr(fm, 'scope_setting_keys', lambda: ['memory_scope', 'email_scope'])


def test_new_from_chat_drops_chat_scopes(mgr, two_scopes):
    chat = {'prompt': 'ronnie', 'memory_scope': 'sapphire', 'email_scope': 'sapphire',
            'goal_scope': 'default'}   # a scope whose plugin is off
    assert mgr.create_from_settings('neo', chat)
    s = mgr._personas['neo']['settings']
    assert s['prompt'] == 'ronnie' and 'goal_scope' not in s
    assert s['memory_scope'] == 'none' and s['email_scope'] == 'none'


def test_create_scopes_off_unless_named(mgr, two_scopes):
    assert mgr.create('neo', {'settings': {'memory_scope': 'neo'}})
    s = mgr._personas['neo']['settings']
    assert s['memory_scope'] == 'neo' and s['email_scope'] == 'none'


def test_duplicate_keeps_source_scopes(mgr, two_scopes):
    mgr._personas['zed']['settings']['memory_scope'] = 'zed'
    assert mgr.duplicate('zed', 'zed2')
    assert mgr._personas['zed2']['settings']['memory_scope'] == 'zed'


# ── One-time release of phantom 'default' scopes (Krem, 2026-11-08) ──

def test_phantom_default_released_once_with_backup(mgr):
    (mgr.USER_DIR / 'personas.json').write_text('{"before": true}')
    mgr._personas['zed']['settings'].update({
        'email_scope': 'default',      # no such account → none
        'twilio_scope': 'default',     # twilio HAS a 'default' → kept
        'github_scope': 'default',     # empty list (failed fetch) → kept
        'memory_scope': 'default',     # store scope → never touched
        'bitcoin_scope': 'het'})       # a real pick → kept
    lists = {'email': ['sapphire'], 'twilio': ['default', 'b'], 'github': [],
             'memory': ['sapphire'], 'bitcoin': ['het']}
    assert mgr.release_phantom_scopes(lists) == {'zed': ['email_scope']}
    s = mgr._personas['zed']['settings']
    assert s['email_scope'] == 'none' and s['twilio_scope'] == 'default'
    assert s['github_scope'] == 'default' and s['memory_scope'] == 'default'
    assert s['bitcoin_scope'] == 'het'
    bak = mgr.USER_DIR / 'personas.json.pre-scope-release.bak'
    assert bak.read_text() == '{"before": true}'
    # Second run is a no-op, even with a fresh phantom.
    s['email_scope'] = 'default'
    assert mgr.release_phantom_scopes(lists) is None
    assert s['email_scope'] == 'default'


def test_phantom_release_nothing_to_do_still_marks(mgr):
    assert mgr.release_phantom_scopes({'email': ['sapphire']}) == {}
    assert (mgr.USER_DIR / mgr.PHANTOM_MARKER).exists()
    assert not (mgr.USER_DIR / 'personas.json.pre-scope-release.bak').exists()
