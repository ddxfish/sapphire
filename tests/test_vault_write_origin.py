"""Isolation hunt wave 3 (2026-11-26, tmp/vault-isolation-hunt-20261123.md):
writes that could put private material, or a shadow of it, in plaintext.

C2  seven writers could plant a plaintext piece under a key the LOCKED vault
    holds. The vault's sidecar now carries salted hashes of its own piece
    keys (prompt_vault.sealed_piece) and every writer asks.
C3  move-in overwrote a vault original (pinned in test_vault_isolation_hunt).
C4  the editor's Duplicate of a vault piece did not say where its text came
    from.
C5  a sidebar control saved another chat's painted value onto this one.
C6  a draft written in a private chat was sent into the public chat the
    lock moved the tab to.
C7  "+ New" persona copied a private chat's Custom Context into plaintext.
"""
import json
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import core.api_fastapi  # noqa: F401  (route modules trip circular imports alone)
import core.prompt_crud as pc
import core.prompt_vault as pv
from core.prompt_manager import prompt_manager as pm
from test_vault_isolation_hunt import world, store, _seal, SECRET   # noqa: F401  (fixtures ride along)

STATIC = Path(__file__).parent.parent / "interfaces" / "web" / "static"


# ── C2: the sealed-key index, on real crypto and a real sidecar ──

def test_the_sidecar_answers_for_a_locked_vault_and_stores_no_names(store):
    assert pv.set_piece('emotions', 'tender', SECRET)[0]
    assert pv.sealed_piece('emotions', 'tender') is None, "unlocked: ask vault_has_piece"
    assert pv.holds_piece('emotions', 'tender')
    assert pv.lock("user")
    pv._refs_loaded = False                      # a fresh boot reads it from disk
    assert pv.sealed_piece('emotions', 'tender') is True
    assert pv.sealed_piece('emotions', 'other') is False
    assert pv.sealed_piece('extras', 'tender') is False, "the type is part of the key"
    assert pv.holds_piece('emotions', 'tender') and not pv.holds_piece('emotions', 'other')
    raw = pv.REFS_PATH.read_text(encoding='utf-8')
    assert 'tender' not in raw and SECRET not in raw


def test_a_sidecar_from_before_the_index_gives_no_answer(store):
    assert pv.set_piece('emotions', 'tender', SECRET)[0]
    assert pv.lock("user")
    data = json.loads(pv.REFS_PATH.read_text(encoding='utf-8'))
    data.pop('piece_keys')
    pv.REFS_PATH.write_text(json.dumps(data), encoding='utf-8')
    pv._refs_loaded = False
    assert pv.sealed_piece('emotions', 'tender') is None
    assert not pv.holds_piece('emotions', 'tender'), "no data is not a refusal: one unlock writes the index"


def test_a_piece_deleted_from_the_vault_frees_its_key(store):
    assert pv.set_piece('emotions', 'tender', SECRET)[0]
    assert pv.delete_piece('emotions', 'tender')[0]
    assert pv.lock("user")
    pv._refs_loaded = False
    assert pv.sealed_piece('emotions', 'tender') is False


# ── C2: each writer ──

def test_the_save_funnel_refuses_a_new_piece_under_a_sealed_key(world, monkeypatch):
    _seal(monkeypatch)
    ok, msg = pc.save_component('emotions', 'tender', 'a plaintext shadow')
    assert not ok and 'asleep in the locked vault' in msg
    assert 'tender' not in pm._components['emotions']
    assert pc.save_component('emotions', 'happy', 'You feel very happy.')[0], "her own piece is hers"


def test_her_create_is_told_why(world, monkeypatch):
    from functions import meta
    _seal(monkeypatch)
    out = meta._prompt_pieces({'action': 'create', 'component': 'emotions',
                               'key': 'tender', 'value': 'a plaintext shadow'})
    assert 'asleep in the locked vault' in str(out)
    assert 'tender' not in pm._components['emotions']


def test_the_editor_route_refuses_a_sealed_key_as_a_conflict_not_a_fault(world, client, monkeypatch):
    """A save with neither create nor update (a script, an old page) reached
    the funnel's refusal and came back 500 (live check 2026-11-26)."""
    _seal(monkeypatch)
    c, _ = client
    for body in ({'value': 'a plaintext shadow'}, {'value': 'a plaintext shadow', 'create': True}):
        r = c.put('/api/prompts/components/emotions/tender', json=body)
        assert r.status_code == 409, r.text
        assert 'asleep in the locked vault' in r.json()['detail']
    assert 'tender' not in pm._components['emotions']


def test_rename_cannot_land_on_a_sealed_key(world, monkeypatch):
    _seal(monkeypatch)
    ok, msg = pc.rename_piece('emotions', 'happy', 'tender')
    assert not ok and 'asleep in the locked vault' in msg
    assert 'happy' in pm._components['emotions']


@pytest.mark.parametrize("sealed", [True, False])
def test_an_import_leaves_out_keys_the_vault_holds(world, monkeypatch, sealed):
    if sealed:
        _seal(monkeypatch)
    ok, msg = pc.save_components_batch(
        {'emotions': {'tender': 'card text', 'calm': 'You feel calm.'}})
    assert ok and '1 left out' in msg
    assert 'tender' not in pm._components['emotions']
    assert pm._components['emotions']['calm'] == 'You feel calm.'


def test_a_trash_restore_does_not_shadow_a_sealed_key(world, monkeypatch, tmp_path):
    monkeypatch.setattr(pc, '_trash_path', lambda: tmp_path / 'trash.json')
    monkeypatch.setattr(pc, '_publish_components_changed', lambda action: None)
    pc._save_trash([{'type': 'emotions', 'key': 'tender', 'text': 'old plaintext', 'deleted_at': 1},
                    {'type': 'emotions', 'key': 'calm', 'text': 'You feel calm.', 'deleted_at': 1}])
    _seal(monkeypatch)
    restored, skipped = pc.restore_pieces([('emotions', 'tender'), ('emotions', 'calm')])
    assert [r['key'] for r in restored] == ['calm']
    assert skipped == [{'type': 'emotions', 'key': 'tender', 'why': 'a vault piece has this name'}]
    assert 'tender' not in pm._components['emotions']


@pytest.mark.parametrize("sealed", [True, False])
def test_a_factory_reset_leaves_the_vaults_names_to_the_vault(world, monkeypatch, sealed):
    """What reset_to_defaults runs after its copy: a stock key the person
    moved into the vault must not come back as a plaintext shadow."""
    pm._components['emotions']['tender'] = 'stock text'
    if sealed:
        _seal(monkeypatch)
    with pm._lock:
        pm._drop_vault_held()
    assert 'tender' not in pm._components['emotions']
    assert 'happy' in pm._components['emotions']


def test_first_run_seeding_leaves_sealed_keys_out(world, monkeypatch, tmp_path):
    from core import setup
    _seal(monkeypatch)
    f = tmp_path / 'prompt_pieces.json'
    f.write_text(json.dumps({'components': {'emotions': {'tender': 'stock', 'happy': 'stock'}},
                             'scenario_presets': {}}), encoding='utf-8')
    setup._leave_vault_keys_out(f)
    assert json.loads(f.read_text(encoding='utf-8'))['components'] == {'emotions': {'happy': 'stock'}}


def test_seeding_with_no_vault_touches_nothing(world, monkeypatch, tmp_path):
    from core import setup
    monkeypatch.setattr(pv, '_key', None)
    monkeypatch.setattr(pv, '_data', None)       # and no vault file
    f = tmp_path / 'prompt_pieces.json'
    before = json.dumps({'components': {'emotions': {'tender': 'stock'}}})
    f.write_text(before, encoding='utf-8')
    setup._leave_vault_keys_out(f)
    assert f.read_text(encoding='utf-8') == before


def test_c3_move_in_of_a_prompt_does_not_overwrite_the_vault_original(world):
    assert pv.set_monolith('diary', SECRET)[0]
    pm._monoliths['diary'] = {'content': 'plaintext shadow', 'privacy_required': False}
    ok, msg = pc.move_prompt_to_vault('diary', publish=False)
    assert not ok and 'already in the vault' in msg
    assert pv.overlay_monoliths()['diary']['content'] == SECRET
    assert pm._monoliths['diary']['content'] == 'plaintext shadow'


# ── C6: a private chat's draft is not sent into a public chat ──

def test_c6_private_draft_into_a_public_chat_is_refused(store):
    from core.routes.chat import _private_draft_elsewhere as elsewhere
    sm = store                                   # active: 'desk' (public); 'secret' is private
    assert elsewhere(sm, 'secret', None) is True
    assert elsewhere(sm, 'secret', 'desk') is True
    assert elsewhere(sm, 'desk', None) is False, "the same chat"
    assert elsewhere(sm, 'desk', 'secret') is False, "public words may go private"
    assert elsewhere(sm, 'no-such-chat', None) is False, "a deleted chat's draft is just a draft"
    assert pv.lock("user")                       # the case the hunt found: sealed under the draft
    assert elsewhere(sm, 'secret', None) is True


def test_c6_private_to_private_passes(store):
    from core.routes.chat import _private_draft_elsewhere as elsewhere
    sm = store
    sm.create_chat("also-secret")
    assert sm.set_active_chat("also-secret")
    assert sm.update_chat_settings({'private_chat': True}, expected_active="also-secret")
    assert elsewhere(sm, 'secret', None) is False
    assert elsewhere(sm, 'secret', 'also-secret') is False


def test_c6_the_stream_route_refuses_and_runs_nothing(client, mock_system):
    c, csrf = client
    sm = mock_system.llm_chat.session_manager
    sm.get_active_chat_name.return_value = 'desk'
    sm.is_chat_hidden.side_effect = lambda name: name == 'secret'
    sm.get_chat_settings.return_value = {'private_chat': False}
    r = c.post('/api/chat/stream', json={'text': 'private words', 'draft_chat': 'secret'},
               headers={'X-CSRF-Token': csrf})
    assert r.status_code == 409 and r.json()['code'] == 'private_draft'
    mock_system.llm_chat.begin_stream.assert_not_called()


# ── C7: a persona made from a private chat ──

def _persona_from(client, mock_system, settings):
    c, csrf = client
    mock_system.llm_chat.session_manager.get_chat_settings.return_value = settings
    from core.personas import persona_manager
    with patch.object(persona_manager, 'create_from_settings', return_value=True) as made:
        r = c.post('/api/personas/from-chat', json={'name': 'nova'}, headers={'X-CSRF-Token': csrf})
    return r, made.call_args.args[1]


def test_c7_a_private_chats_custom_context_stays_out_of_the_persona(client, mock_system):
    r, used = _persona_from(client, mock_system, {
        'private_chat': True, 'custom_context': 'words typed in private', 'voice': 'v'})
    assert r.status_code == 200 and r.json()['left_out'] == ['custom_context']
    assert 'custom_context' not in used and used['voice'] == 'v'


def test_c7_a_public_chats_custom_context_still_rides_along(client, mock_system):
    r, used = _persona_from(client, mock_system, {
        'private_chat': False, 'custom_context': 'everyday notes', 'voice': 'v'})
    assert r.json()['left_out'] == [] and used['custom_context'] == 'everyday notes'


# ── C4, C5, C6: the web side (source contracts; the behavior is the browser's) ──

def _src(rel):
    return (STATIC / rel).read_text(encoding='utf-8')


def test_c4_duplicating_a_piece_stamps_its_origin():
    body = _src('views/prompts.js').split('async function duplicateDefinition', 1)[1].split('\nasync function ', 1)[0]
    assert 'pieceOrigin(type, key)' in body, "an unstamped copy of a vault piece saved plaintext after a lock"


def test_c5_the_sidebar_saves_only_what_it_painted_for_this_chat():
    src = _src('views/chat.js')
    save = src.split('function debouncedSave', 1)[1].split('\nexport function cancelPendingSave', 1)[0]
    guard = save.split('if (_sbPaintedChat !== chatNow) {', 1)
    assert len(guard) == 2, "a control still showing another chat's value must not save onto this one"
    # the guard returns before anything is captured for a save
    assert re.search(r'\breturn;', guard[1].split('pendingSaveChatName = chatNow', 1)[0])
    assert 'pendingSaveChatName = chatNow' not in guard[0]
    load = src.split('async function loadSidebar', 1)[1].split('\nfunction ', 1)[0]
    assert 'chatName === _sbPaintedChat' in load, "the editing hold is for a refresh of the SAME chat"
    assert '_sbPaintedChat = chatName' in load


def test_c6_the_send_carries_where_the_draft_was_written():
    assert 'body.draft_chat = queueHooks.draftChat' in _src('api.js')
    send = _src('handlers/send-handlers.js')
    assert 'noteDraft(!!input.value.trim(), getViewedChat())' in send
    assert "e.code === 'private_draft'" in send
    refused = send.split("e.code === 'private_draft'", 1)[1].split('if (statusCode === 409)', 1)[0]
    assert 'restoreDraft' not in refused, "the refused words must not come back to the public chat's box"
    assert 'dropSealedDraft' in _src('main.js')
