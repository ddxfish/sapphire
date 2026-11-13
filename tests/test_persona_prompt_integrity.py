"""[REGRESSION_GUARD] Persona/prompt hunt waves W4-W6 (2026-11-08).

Hermetic: tmp persona dir, stubbed prompt savers, fake system — nothing
touches the real stores. Shares fixtures with test_rename_ownership.py.
"""
import json
import threading

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


def test_duplicate_starts_with_scopes_off(mgr, two_scopes, presets, piece_store):
    mgr._personas['zed']['settings'].update(
        memory_scope='zed', email_scope='zed', goal_scope='zed')  # goal: plugin off
    assert mgr.duplicate('zed', 'zed2')
    s = mgr._personas['zed2']['settings']
    assert s['memory_scope'] == 'none' and s['email_scope'] == 'none'
    assert 'goal_scope' not in s
    assert mgr._personas['zed']['settings']['memory_scope'] == 'zed'


# ── Duplicate forks the prompt (Teri's Seraphina, 2026-11-08) ──

def test_duplicate_forks_prompt_and_character(mgr, presets, piece_store):
    piece_store['character']['ronnie'] = 'You are Ronnie.'
    presets.ps['ronnie']['extras'] = ['beach']
    assert mgr.duplicate('zed', 'Seraphina')
    s = mgr._personas['seraphina']['settings']
    assert s['prompt'] == 'seraphina'
    fork = presets.ps['seraphina']
    assert fork['character'] == 'seraphina' and fork['extras'] == ['beach']
    assert piece_store['character']['seraphina'] == 'You are Ronnie.'
    # The original is untouched — its prompt and its character piece.
    assert presets.ps['ronnie']['character'] == 'ronnie'
    assert mgr._personas['zed']['settings']['prompt'] == 'ronnie'


def test_duplicate_fork_takes_a_free_name(mgr, presets, piece_store):
    piece_store['character']['ronnie'] = 'You are Ronnie.'
    piece_store['character']['nova'] = 'someone else'
    presets.ps['nova'] = {'character': 'x', 'extras': []}
    assert mgr.duplicate('zed', 'nova')
    assert mgr._personas['nova']['settings']['prompt'] == 'nova-2'
    assert presets.ps['nova-2']['character'] == 'nova-2'
    assert piece_store['character']['nova'] == 'someone else'


def test_duplicate_forks_monolith(mgr, presets, piece_store, monkeypatch):
    from core.prompt_manager import prompt_manager
    monos = {'solo': {'content': 'You are Solo.', 'privacy_required': True}}
    monkeypatch.setattr(prompt_manager, '_monoliths', monos)
    monkeypatch.setattr(prompt_manager, 'save_monoliths', lambda reason=None, audit=True: True)
    mgr._personas['zed']['settings']['prompt'] = 'solo'
    assert mgr.duplicate('zed', 'duo')
    assert mgr._personas['duo']['settings']['prompt'] == 'duo'
    assert monos['duo'] == {'content': 'You are Solo.', 'privacy_required': True}


@pytest.mark.parametrize('src', ['default', '', 'no_such_prompt'])
def test_duplicate_keeps_pointer_without_a_real_prompt(mgr, presets, piece_store, src):
    mgr._personas['zed']['settings']['prompt'] = src
    before = dict(presets.ps)
    assert mgr.duplicate('zed', 'zed2')
    assert mgr._personas['zed2']['settings']['prompt'] == src
    assert presets.ps == before


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


# ── Ultrareview 2026-11-08 ──

def test_prompt_rename_reaches_room_seats(presets, world):
    import core.prompt_crud as crud
    ok, msg = crud.rename_prompt('ronnie', 'ronnie2')
    assert ok, msg
    assert ('ronnie', 'ronnie2') in world.seat_prompts


def test_rooms_repoint_prompt_rewrites_frozen_seat(monkeypatch):
    import core.rooms as rooms
    room = {'seats': [{'id': 'a', 'persona': 'zed', 'settings': {'prompt': 'ronnie', 'voice': 'v'}},
                      {'id': 'b', 'persona': 'amy', 'settings': {'prompt': 'beach'}}]}
    written = {}
    monkeypatch.setattr(rooms, 'list_rooms', lambda: [{'chat': 'den'}])
    monkeypatch.setattr(rooms, 'settings_of', lambda chat: {'room': room})
    monkeypatch.setattr(rooms, '_write', lambda chat, raw: written.update({chat: raw}))
    assert rooms.repoint_prompt('ronnie', 'ronnie2') == ['den']
    seats = written['den']['seats']
    assert seats[0]['settings'] == {'prompt': 'ronnie2', 'voice': 'v'}
    assert seats[1]['settings'] == {'prompt': 'beach'}
    assert room['seats'][0]['settings']['prompt'] == 'ronnie'   # copy-on-write


@pytest.mark.parametrize('bad', ['Luna, the Moon'])
def test_prompt_names_refuse_commas(presets, bad):
    import core.prompt_crud as crud
    assert 'comma' in crud.prompt_name_problem(bad)


def test_phantom_release_without_any_list_does_not_mark(mgr):
    assert mgr.release_phantom_scopes({}) == {}
    assert mgr.release_phantom_scopes({'email': []}) == {}
    assert not (mgr.USER_DIR / mgr.PHANTOM_MARKER).exists()


def test_her_piece_create_refuses_hidden_pack_key(piece_store, monkeypatch):
    import core.prompts as prompts
    from functions.meta import _prompt_pieces
    from core.name_problem import Problem, TAKEN
    monkeypatch.setattr(prompts, 'piece_key_problem',
                        lambda t, k: Problem("'character/scaffold' already exists", TAKEN)
                        if k == 'scaffold' else None)
    msg, ok = _prompt_pieces({'action': 'create', 'component': 'character',
                              'key': 'scaffold', 'value': 'x'})
    assert not ok and 'built-in' in msg
    assert 'scaffold' not in piece_store['character']


def test_her_fork_skips_names_the_check_refuses(presets, monkeypatch):
    import core.prompts as prompts
    from functions.meta import _save_and_activate_assembled
    monkeypatch.setattr(prompts, 'get_active_preset_name', lambda: 'default')
    monkeypatch.setattr(prompts, 'activate_prompt', lambda name, system: (True, ''))
    real = prompts.prompt_name_problem
    from core.name_problem import Problem, ASLEEP
    monkeypatch.setattr(prompts, 'prompt_name_problem',
                        lambda n: Problem('asleep in the vault', ASLEEP) if n == 'nova' else real(n))
    name = _save_and_activate_assembled(None, {'character': 'nova', 'extras': []})
    assert name == 'nova-2' and 'nova' not in presets.ps


# ── Refusals carry a code, never parsed from wording (ultrareview #10) ──

def test_name_checks_carry_codes(presets, piece_store, mgr, monkeypatch):
    import core.prompt_crud as crud
    from core.name_problem import TAKEN, ASLEEP, INVALID
    assert crud.prompt_name_problem('ronnie').code == TAKEN
    assert crud.prompt_name_problem('a, b').code == INVALID
    assert crud.prompt_name_problem('default').code == INVALID
    assert crud.piece_key_problem('character', 'sapphire').code == TAKEN
    assert crud.piece_key_problem('nope', 'x').code == INVALID
    assert mgr.rename_problem('zed', 'amy').code == TAKEN
    assert mgr.rename_problem('zed', '!!!').code == INVALID
    import core.chat.history as hist
    monkeypatch.setattr(hist, 'sealed_prompt_name', lambda n: n == 'sleeper')
    assert crud.prompt_name_problem('sleeper').code == ASLEEP


def test_refuse_maps_code_to_status_and_header():
    from core.routes.content import _refuse
    from core.name_problem import Problem, TAKEN, ASLEEP, PERSONA_TAKEN
    for code, status in ((TAKEN, 409), (ASLEEP, 409), (PERSONA_TAKEN, 409), ('invalid', 400)):
        e = _refuse(Problem('whatever the words say', code))
        assert e.status_code == status and e.headers == {'X-Problem': code}
    plain = _refuse("Store save failed — and it already exists")   # wording means nothing now
    assert plain.status_code == 400 and not plain.headers


def test_card_prompt_taken_keeps_existing_by_code(importer, presets, mgr):
    # A taken prompt name on a persona card points at the local one — decided
    # by the code, so rewording the message can't flip keep vs refuse.
    res = importer._install_prompt_bundle('ronnie', {'type': 'assembled', 'components': {}},
                                          None, use_existing=True)
    assert res['kept_existing'] is True


# ── A scope a persona doesn't name is off, for every reader (hunt 2026-11-13) ──

def test_get_states_every_scope_and_never_hands_out_the_record(mgr, two_scopes):
    mgr._personas['zed']['settings']['memory_scope'] = 'zed'
    got = mgr.get('zed')
    assert got['settings']['memory_scope'] == 'zed'          # the record's own value wins
    assert got['settings']['email_scope'] == 'none'          # never named: off, not 'default'
    got['settings']['email_scope'] = 'sapphire'
    assert 'email_scope' not in mgr._personas['zed']['settings']
    assert mgr.get('nobody') is None


def test_activation_stamps_none_for_a_scope_the_persona_never_named():
    # the same answer get() gives: the old "default" here is what the editor
    # (which showed None) then saved over a working account
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "core" / "routes" / "content.py").read_text(encoding="utf-8")
    body = src.split("async def load_persona")[1].split("\n@router")[0]
    assert 'settings[key] = "none"' in body and 'settings[key] = "default"' not in body


def test_pin_writes_down_what_a_missing_scope_used_to_mean(mgr, two_scopes):
    (mgr.USER_DIR / 'personas.json').write_text('{"before": true}')
    (mgr.USER_DIR / mgr.PHANTOM_MARKER).write_text('done')
    mgr._personas['zed']['settings']['email_scope'] = 'none'        # a choice: kept
    mgr._personas['amy']['settings']['memory_scope'] = 'amy'        # a choice: kept
    changed = mgr.pin_absent_scopes()
    # registered scopes + the four store scopes, plugin loaded or not
    assert changed['zed'] == ['goal_scope', 'knowledge_scope', 'memory_scope', 'people_scope']
    assert changed['amy'] == ['email_scope', 'goal_scope', 'knowledge_scope', 'people_scope']
    z, a = mgr._personas['zed']['settings'], mgr._personas['amy']['settings']
    assert z['memory_scope'] == 'default' and z['email_scope'] == 'none'
    assert a['email_scope'] == 'default' and a['memory_scope'] == 'amy'
    assert (mgr.USER_DIR / 'personas.json.pre-scope-pin.bak').read_text() == '{"before": true}'
    saved = json.loads((mgr.USER_DIR / 'personas.json').read_text())
    assert saved[mgr._STATED] is True and saved['zed']['settings']['memory_scope'] == 'default'
    # the pins are 'default' by name: the phantom release judges them again
    assert not (mgr.USER_DIR / mgr.PHANTOM_MARKER).exists()
    # once per file
    del z['memory_scope']
    assert mgr.pin_absent_scopes() is None and 'memory_scope' not in z


def test_pin_runs_again_for_a_restored_old_file(mgr, two_scopes):
    from core.personas.persona_manager import PersonaManager
    assert mgr.pin_absent_scopes()
    # an old backup restored over it: no flag in the file
    (mgr.USER_DIR / 'personas.json').write_text(json.dumps(
        {'old': {'name': 'old', 'settings': {'prompt': 'ronnie'}}}))
    m2 = PersonaManager.__new__(PersonaManager)
    m2._lock, m2.BASE_DIR, m2.USER_DIR, m2.USER_AVATARS = mgr._lock, mgr.BASE_DIR, mgr.USER_DIR, mgr.USER_AVATARS
    m2._personas = {}
    m2._load()
    assert m2._scopes_stated is False
    assert m2.pin_absent_scopes()['old'] == ['email_scope', 'goal_scope', 'knowledge_scope',
                                             'memory_scope', 'people_scope']
    # ...and the file it wrote is stated
    m3 = PersonaManager.__new__(PersonaManager)
    m3._lock, m3.BASE_DIR, m3.USER_DIR, m3.USER_AVATARS = mgr._lock, mgr.BASE_DIR, mgr.USER_DIR, mgr.USER_AVATARS
    m3._personas = {}
    m3._load()
    assert m3._scopes_stated is True and m3.pin_absent_scopes() is None


def test_a_failed_pin_save_keeps_the_pins_in_memory_and_tries_again(mgr, two_scopes, monkeypatch):
    monkeypatch.setattr(mgr, '_save_to_user', lambda: False)
    with pytest.raises(RuntimeError):
        mgr.pin_absent_scopes()
    assert mgr._personas['zed']['settings']['memory_scope'] == 'default'    # this session behaves as before
    assert mgr._scopes_stated is False                                      # the next boot tries again


def test_pin_never_writes_an_empty_personas_file(mgr, two_scopes):
    mgr._personas = {}
    assert mgr.pin_absent_scopes() is None
    assert not (mgr.USER_DIR / 'personas.json').exists()     # an empty file would stop the next boot's seed


def test_a_fresh_seed_is_stated(tmp_path, two_scopes):
    from core.personas.persona_manager import PersonaManager
    (tmp_path / 'personas.json').write_text(json.dumps(
        {'sapphire': {'name': 'sapphire', 'settings': {'memory_scope': 'default'}}}))
    m = PersonaManager.__new__(PersonaManager)
    m._lock = threading.RLock()
    m.BASE_DIR, m.USER_DIR = tmp_path, tmp_path / 'personas'
    m.USER_AVATARS = m.USER_DIR / 'avatars'
    m.BUILTIN_AVATARS = tmp_path / 'none'
    m.USER_AVATARS.mkdir(parents=True)
    m._personas = {}
    m._load()
    assert m._scopes_stated is True and m.pin_absent_scopes() is None
    assert 'email_scope' not in m._personas['sapphire']['settings']      # nothing bound, nothing pinned
    assert m.get('sapphire')['settings']['email_scope'] == 'none'


def test_phantom_release_leaves_a_scope_whose_default_is_implicit(mgr, monkeypatch):
    # Social reads 'default' as "the only profile": releasing it switched off
    # a working binding
    monkeypatch.setattr(type(mgr), '_implicit_default_scopes', staticmethod(lambda: {'social'}))
    mgr._personas['zed']['settings'].update({'social_scope': 'default', 'email_scope': 'default'})
    assert mgr.release_phantom_scopes({'social': ['anita'], 'email': ['work']}) == {'zed': ['email_scope']}
    assert mgr._personas['zed']['settings']['social_scope'] == 'default'


def test_phantom_release_does_nothing_when_the_manifests_cannot_be_read(mgr, monkeypatch):
    def boom():
        raise RuntimeError('no manifests')
    monkeypatch.setattr(type(mgr), '_implicit_default_scopes', staticmethod(boom))
    mgr._personas['zed']['settings']['email_scope'] = 'default'
    with pytest.raises(RuntimeError):
        mgr.release_phantom_scopes({'email': ['work']})
    assert mgr._personas['zed']['settings']['email_scope'] == 'default'
    assert not (mgr.USER_DIR / mgr.PHANTOM_MARKER).exists()


def test_social_says_its_default_is_implicit():
    from pathlib import Path
    m = json.loads((Path(__file__).resolve().parent.parent / "core" / "plugins" / "social"
                    / "plugin.json").read_text(encoding="utf-8"))
    assert [d.get('implicit_default') for d in m['capabilities']['scopes'] if d['key'] == 'social'] == [True]


def test_the_persona_editor_saves_only_what_was_touched():
    from pathlib import Path
    s = (Path(__file__).resolve().parent.parent / "interfaces" / "web" / "static" / "views"
         / "personas.js").read_text(encoding="utf-8")
    save = s.split("async function doSave()")[1].split("\n}\n")[0]
    assert "settings: collectSettings()" not in save                      # the whole form, chosen or not
    assert "[...keys].filter(k => k in shown)" in save
    assert "selectedData.settings = { ...selectedData.settings, ...data.settings };" in save


def test_duplicate_fork_stays_in_its_sources_store(mgr, presets, piece_store, monkeypatch):
    # No origin = "a new name follows the vault's lock state": a plain persona
    # duplicated with the vault open got a private, vault-only prompt.
    import core.prompt_crud as crud
    seen = []
    real_prompt, real_piece = crud.save_prompt, crud.save_component
    monkeypatch.setattr(crud, 'save_prompt', lambda *a, **k: (seen.append(('prompt', k.get('origin'))), real_prompt(*a, **k))[1])
    monkeypatch.setattr(crud, 'save_component', lambda *a, **k: (seen.append(('piece', k.get('origin'))), real_piece(*a, **k))[1])
    piece_store['character']['ronnie'] = 'I am Ronnie.'
    assert mgr.duplicate('zed', 'Seraphina')
    assert seen == [('piece', 'regular'), ('prompt', 'regular')]

    # ...and a vault prompt's copy is a vault save, which a locked vault refuses
    seen.clear()
    monkeypatch.setattr(crud, 'is_vault_prompt', lambda name: True)
    assert mgr.duplicate('zed', 'Nyx')
    assert ('prompt', 'vault') in seen
    assert mgr._personas['nyx']['settings']['prompt'] == 'ronnie'      # refused: the pointer is kept
