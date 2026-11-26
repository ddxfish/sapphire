"""Vault isolation hunt 2026-11-23 (tmp/vault-isolation-hunt-20261123.md).

Wave 1 landed 2026-11-26: the READER RULE (core/prompt_vault.py, Krem's
ruling A). Vault content exists only for a private turn and the person at the
editor; every other reader sees the locked-vault world. The tests for roots A
and B now pin that rule.

Wave 2 landed the same day: the VAULT LEASE (core/prompt_vault.py). A private
chat's turn holds the vault open from begin_stream until its rows are written;
a decision to lock stops those turns and seals once they are gone, the idle
lock waits for them (D1, D4, C1, D2). C2 and C3 are still open: their tests
assert the SECURE behavior and stay xfail(strict), so the mark fails loudly
the day the fix lands (remove the mark then).

Hermetic: the vault is in-memory only (no file), the prompt stores are
swapped for small dicts and nothing here ever calls a real saver.
"""
import time
import types

import pytest

import core.api_fastapi  # noqa: F401  (route modules trip circular imports alone)
import core.prompt_crud as pc
import core.prompt_state as ps
import core.prompt_vault as pv
from core.prompt_manager import prompt_manager as pm

SECRET = "VAULT-ONLY intimate text"


@pytest.fixture
def world(tmp_path, monkeypatch):
    """Unlocked in-memory vault holding emotions/tender; a plaintext preset
    'plain' that wears it; nothing touches disk."""
    monkeypatch.setattr(pv, 'VAULT_PATH', tmp_path / 'prompt_vault.enc')
    monkeypatch.setattr(pv, 'REFS_PATH', tmp_path / 'vault_refs.json')
    monkeypatch.setattr(pv, '_save_locked', lambda: True)
    monkeypatch.setattr(pv, '_save_refs_locked', lambda: True)
    monkeypatch.setattr(pv, '_publish', lambda action: None)
    monkeypatch.setattr(pv, '_audit_row', lambda event: None)
    monkeypatch.setattr(pv, '_key', b'k' * 32)
    monkeypatch.setattr(pv, '_salt', b's' * 16)
    monkeypatch.setattr(pv, '_data', pv._normalize(
        {'components': {'emotions': {'tender': SECRET}}}))
    monkeypatch.setattr(pv, '_refs', {})
    monkeypatch.setattr(pv, '_refs_loaded', True)

    monkeypatch.setattr(pm, '_components', {
        'character': {'sapphire': 'You are Sapphire.'},
        'emotions': {'happy': 'You feel happy.'}})
    monkeypatch.setattr(pm, '_scenario_presets', {
        'plain': {'character': 'sapphire', 'emotions': ['tender'],
                  '_privacy_required': False}})
    monkeypatch.setattr(pm, '_monoliths', {})
    monkeypatch.setattr(pm, 'save_components', lambda **kw: True)
    monkeypatch.setattr(pm, 'save_scenario_presets', lambda **kw: True)
    monkeypatch.setattr(pm, 'save_monoliths', lambda **kw: True)
    monkeypatch.setattr(pm, '_active_preset_name', 'default', raising=False)
    saved = dict(ps._assembled_state)
    ps._assembled_state.clear()
    ps._assembled_state.update({'character': 'sapphire', 'emotions': [],
                                'extras': [], 'active_preset': 'default'})
    monkeypatch.setattr(ps, '_transient_overlay', {})
    yield
    ps._assembled_state.clear()
    ps._assembled_state.update(saved)
    pv.stop()


# ── Root A: the text a turn gets is rendered FOR that turn's chat ──

def test_a1_plain_preset_wearing_a_vault_piece_renders_clean_for_a_public_turn(world):
    """Krem's heartbeat case: the public turn runs, without the vault mood,
    exactly as it would with the vault locked."""
    with pv.reading(False):
        data = pc.get_prompt('plain')
    assert SECRET not in data['content'] and 'You are Sapphire.' in data['content']
    assert data['vault'] is False and data['privacy_required'] is False


def test_a1_the_same_preset_renders_whole_for_a_private_turn_and_says_so(world):
    with pv.reading(True):
        data = pc.get_prompt('plain')
    assert SECRET in data['content']
    assert data['vault'] is True, "the text holds vault material: marked"
    # the stored flag is the user's choice and the editor saves it back
    assert data['privacy_required'] is False


def test_a1_no_lane_set_means_closed(world):
    assert SECRET not in pc.get_prompt('plain')['content']


def test_a2_default_sentinel_with_a_vault_mood(world):
    ps.set_transient_piece('emotions', 'tender', 30)
    with pv.reading(False):
        assert SECRET not in pc.get_prompt('default')['content']
    with pv.reading(True):
        data = pc.get_prompt('default')
        assert SECRET in data['content'] and data['vault'] is True


def test_a3_a_dead_name_falls_back_to_a_clean_default_on_a_public_turn(world):
    """The by-name lanes resolve the name, then 'default', both for the chat."""
    from core.chat.llm_providers.resolve import _prompt_gate
    ps.set_transient_piece('emotions', 'tender', 30)
    with pv.reading(False):
        assert pc.get_prompt('gone-prompt') is None
        assert SECRET not in pc.get_prompt('default')['content']
    _prompt_gate('gone-prompt', private=False)      # passes, and the text is clean


# ── Root B: her tools read the vault only inside a private turn ──

def test_b1_prompt_pieces_list_hides_vault_pieces_from_a_public_turn(world):
    from core.chat.function_manager import scope_private
    from functions import meta
    assert scope_private.get() is False, "setup: this turn is not private"
    text, ok = meta._prompt_pieces({'action': 'list', 'component': 'emotions'})
    assert ok and 'happy' in text
    assert 'tender' not in text and SECRET[:20] not in text


def test_b1_prompt_pieces_view_refuses_a_vault_piece_on_a_public_turn(world):
    from functions import meta
    text, ok = meta._prompt_pieces({'action': 'view', 'component': 'emotions',
                                    'key': 'tender'})
    assert SECRET not in text and not ok


def test_b1_prompt_view_refuses_a_vault_prompt_on_a_public_turn(world):
    from functions import meta
    ok, _ = pv.set_monolith('private-persona', SECRET)
    assert ok
    text, ok = meta._prompt_view({'name': 'private-persona'})
    assert SECRET not in text and not ok
    assert 'private-persona' not in text.split('Available:')[-1], "nor in the list of names"


def test_b1_prompt_switch_listing_hides_vault_names_from_a_public_turn(world):
    from functions import meta
    ok, _ = pv.set_monolith('private-persona', SECRET)
    assert ok
    text, _ok = meta._prompt_switch({})
    assert 'private-persona' not in text


def test_b1_a_private_turn_still_sees_her_vault(world):
    """The other half: nothing is taken from the private chat."""
    from functions import meta
    with pv.reading(True):
        text, ok = meta._prompt_pieces({'action': 'view', 'component': 'emotions',
                                        'key': 'tender'})
    assert ok and SECRET in text


def test_b1_a_turn_on_another_chat_reads_its_own_prompt_not_the_open_chats(world, monkeypatch):
    """The open chat is private and wears 'plain' with a timed vault mood. A
    task on the public chat 'desk' asks for "my prompt" and "what is worn"."""
    from core.chat import stream_brain
    from functions import meta
    system = _live(monkeypatch, private=True)
    sm = system.llm_chat.session_manager
    sm.get_active_chat_name = lambda: 'bedroom'
    sm.get_chat_settings = lambda: {'prompt': 'deskprompt', 'private_chat': False}
    pm._monoliths['deskprompt'] = {'content': 'You are the desk clerk.', 'privacy_required': False}
    ps._assembled_state['emotions'] = ['tender']
    token = stream_brain.set_override({'chat': 'desk'})
    try:
        text, ok = meta._prompt_view({})
        assert ok and 'desk clerk' in text
        assert 'plain' not in text and 'tender' not in text and SECRET not in text
        assert meta._prompt_switch({})[0].startswith('Current: deskprompt')
        listing, _ = meta._prompt_pieces({'action': 'list'})
        assert 'tender' not in listing and 'active:' not in listing
    finally:
        stream_brain.reset_override(token)


def _tool_door(monkeypatch):
    """A bare FunctionManager with one tool that reports the vault's reader."""
    from unittest.mock import patch
    from core.chat.function_manager import FunctionManager
    with patch.object(FunctionManager, '__init__', lambda self: None):
        fm = FunctionManager()
    fm.execution_map = {'peek': lambda name, args, cfg: (str(pv.reader_ok()), True)}
    fm._is_local_map = {'peek': True}
    fm.function_modules, fm._function_module_map = {}, {}
    fm.system_instance = None
    monkeypatch.setattr(fm, '_log_tool_call', lambda *a, **k: None)
    monkeypatch.setattr(fm, '_get_tool_parameters', lambda n: None)
    return fm


def test_b1_the_tool_door_opens_the_vault_from_the_turns_private_flag(world, monkeypatch):
    """execute_function is the one door every lane's tools pass (chat, tasks,
    rooms, MCP): it sets the reader from the turn's own flag, both ways."""
    from core.chat.function_manager import reset_scopes
    fm = _tool_door(monkeypatch)
    try:
        assert fm.execute_function('peek', {}, scopes={'private': False}, allowed_tools=['peek']) == 'False'
        assert fm.execute_function('peek', {}, scopes={'private': True}, allowed_tools=['peek']) == 'True'
        assert pv.reader_ok() is False, "and it closes again after the call"
        reset_scopes()
        # the MCP door passes no scopes at all: closed (B2)
        assert fm.execute_function('peek', {}, allowed_tools=['peek']) == 'False'
        # an editor-opened context does not leak into a public turn's tool
        with pv.reading(True):
            assert fm.execute_function('peek', {}, scopes={'private': False}, allowed_tools=['peek']) == 'False'
    finally:
        reset_scopes()


def test_b4_public_turn_cannot_delete_a_vault_piece(world):
    from functions import meta
    meta._prompt_pieces({'action': 'delete', 'component': 'emotions',
                         'key': 'tender'})
    assert pv.vault_has_piece('emotions', 'tender')


def test_b4_public_turn_cannot_shadow_an_unlocked_vault_key(world):
    """Collision checks use the FULL view: on the reader's view the key looks
    free, and the plaintext piece she then creates shadows the vault's."""
    from functions import meta
    with pv.reading(False):
        assert pc.piece_key_problem('emotions', 'tender') is not None
        text, ok = meta._prompt_pieces({'action': 'create', 'component': 'emotions',
                                        'key': 'tender', 'value': 'shadow'})
    assert not ok and 'tender' not in pm._components['emotions']
    assert SECRET not in text


def test_the_editor_routes_read_the_vault(world, client):
    """The person at the screen: the content router opens the reader for its
    requests (an async dependency - this proves the set reaches the route)."""
    assert pv.set_monolith('private-persona', SECRET)[0]
    c, _ = client
    r = c.get('/api/prompts/components')
    assert r.status_code == 200, r.text
    assert r.json()['components']['emotions']['tender'] == SECRET
    names = {p['name']: p for p in c.get('/api/prompts').json()['prompts']}
    assert names['private-persona']['vault'] is True
    assert c.get('/api/prompts/private-persona').json()['data']['content'] == SECRET
    assert pv.reader_ok() is False, "and nothing stays open after the requests"


# ── "sees less" must never become a write or a weaker gate ──

def test_gates_stay_shut_for_a_vault_prompt_on_a_closed_reader(world):
    """106 places read privacy_required off a resolved prompt. A reader who
    may not SEE a vault prompt must still be told it is private."""
    from core.chat.llm_providers.resolve import _prompt_gate, PrivacyRefused
    assert pv.set_monolith('private-persona', SECRET)[0]
    assert pv.set_preset('private-preset', {'character': 'sapphire'})[0]
    with pv.reading(False):
        for name in ('private-persona', 'private-preset'):
            data = pc.get_prompt(name)
            assert data['privacy_required'] is True and SECRET not in data['content']
            with pytest.raises(PrivacyRefused):
                _prompt_gate(name, private=False)
        # the global flag, with a vault prompt active
        ps._assembled_state['active_preset'] = 'private-preset'
        pm._active_preset_name = 'private-preset'
        assert ps.is_current_prompt_private() is True
        assert ps.is_assembled_mode() is True, "and the mode is a fact, not a view"
        assert pc.get_prompt('default')['privacy_required'] is True
        # a taken vault name stays taken
        assert pc.prompt_name_problem('private-persona') is not None


def test_a_private_task_reads_the_vault_and_a_public_one_does_not(world, monkeypatch):
    from core.continuity.execution_context import ExecutionContext
    assert pv.set_monolith('private-persona', SECRET)[0]

    def build(settings):
        ctx = ExecutionContext.__new__(ExecutionContext)
        ctx.task_settings = settings
        return ExecutionContext._build_prompt(ctx), ctx._prompt_privacy_required

    text, private = build({'prompt': 'private-persona'})
    assert private is True and SECRET in text, "held local-only, so it may read"
    text, private = build({'prompt': 'plain'})
    assert private is False and SECRET not in text, "the heartbeat case"
    text, private = build({'prompt': 'plain', 'privacy_required': True})
    assert private is True and SECRET in text


def test_dangling_refs_do_not_depend_on_the_reader(world):
    """On a closed reader's view 'plain' wears a missing piece. The strip
    tool removes what this reports, so it reads the full view."""
    with pv.reading(False):
        assert pc.dangling_refs() == {}
        assert pc.piece_usage()['emotions']['tender'] == ['plain']


def test_the_ledger_watcher_sees_a_vault_presets_pieces_on_any_lane(world):
    """An edit to a piece a watched vault preset wears must leave a row,
    whichever lane made the edit."""
    from plugins.mindpalace.tools import prompt_audit
    assert pv.set_preset('private-preset', {'character': 'sapphire', 'emotions': ['happy']})[0]
    event = {'kind': 'component', 'comp_type': 'emotions', 'key': 'happy'}
    with pv.reading(False):
        assert prompt_audit._covers('private-preset', event) is True
        assert prompt_audit._covers('private-preset', {**event, 'key': 'other'}) is False


# ── the live prompt cache ──

class _FakeSM:
    _is_streaming = False

    def __init__(self, private=False):
        self.current_settings = {'private_chat': private, 'prompt': 'plain'}

    def get_chat_settings(self):
        return self.current_settings

    def flush_for_seal(self):
        return True

    def evict_private_active(self):
        return None


def _live(monkeypatch, private):
    """A system whose open chat wears 'plain', on the REAL cache methods."""
    import core.api_fastapi as af
    from core.chat.chat import LLMChat
    llm = types.SimpleNamespace(current_system_prompt=None, session_manager=_FakeSM(private))
    for name in ('set_system_prompt', '_live_reader', '_reassemble_live', '_live_prompt_for_turn'):
        setattr(llm, name, types.MethodType(getattr(LLMChat, name), llm))
    system = types.SimpleNamespace(llm_chat=llm)
    monkeypatch.setattr(af, 'get_system', lambda: system)
    monkeypatch.setattr(pm, '_active_preset_name', 'plain', raising=False)
    ps._assembled_state['active_preset'] = 'plain'
    return system


def test_a4_a_public_chats_live_prompt_never_holds_vault_text(world, monkeypatch):
    """Whoever renders it: the editor (open reader) switching to the chat,
    the file watcher, a piece save."""
    system = _live(monkeypatch, private=False)
    with pv.reading(True):                         # an editor route did this
        assert pc.revalidate_active(system=system, reason="test")
    assert SECRET not in system.llm_chat.current_system_prompt
    assert 'You are Sapphire.' in system.llm_chat.current_system_prompt


def test_a_private_chats_live_prompt_keeps_its_vault_text_on_any_lane(world, monkeypatch):
    """The file watcher's re-render is nobody's turn (closed reader). It must
    not strip the open private chat's persona."""
    system = _live(monkeypatch, private=True)
    assert pv.reader_ok() is False
    assert pc.revalidate_active(system=system, reason="file watcher")
    assert SECRET in system.llm_chat.current_system_prompt
    assert ps.get_active_preset_name() == 'plain', "and no handoff to default"


def test_a4_lock_scrubs_vault_text_from_the_live_prompt(world, monkeypatch):
    system = _live(monkeypatch, private=True)
    assert pc.revalidate_active(system=system)
    assert SECRET in system.llm_chat.current_system_prompt, "setup: cache armed"
    assert pv.lock("user") is True
    assert not pv.vault_unlocked()
    assert SECRET not in system.llm_chat.current_system_prompt


def test_the_send_point_never_ships_a_cache_rendered_for_another_reader(world, monkeypatch):
    """The last line: a cache rendered for a private chat, and the chat is
    public by the time the turn sends (a flip nothing re-rendered for)."""
    system = _live(monkeypatch, private=True)
    llm = system.llm_chat
    assert pc.revalidate_active(system=system)
    assert SECRET in llm.current_system_prompt
    llm.session_manager.current_settings['private_chat'] = False
    assert SECRET not in llm._live_prompt_for_turn()
    # and the other way: turned private since, so its vault text arrives
    assert SECRET not in llm.current_system_prompt
    llm.session_manager.current_settings['private_chat'] = True
    assert SECRET in llm._live_prompt_for_turn()


def test_a7_a_sealed_vault_presets_keys_leave_the_live_state(world, monkeypatch):
    system = _live(monkeypatch, private=True)
    assert pv.set_preset('private-preset', {'character': 'sapphire', 'emotions': ['tender']})[0]
    with pv.reading(True):
        assert ps.apply_scenario('private-preset')
    pm._active_preset_name = 'private-preset'
    assert pv.lock("user") is True
    assert ps.get_active_preset_name() == 'default'
    assert 'tender' not in ps._assembled_state.get('emotions', [])
    assert SECRET not in system.llm_chat.current_system_prompt


# ── Root C: writes route by lock state, not by where content came from ──

@pytest.mark.xfail(strict=True, reason="C2: pieces have no sealed-key check")
def test_c2_new_piece_cannot_shadow_a_sealed_vault_key(world, monkeypatch):
    monkeypatch.setattr(pv, '_key', None)      # sealed: the vault still
    monkeypatch.setattr(pv, '_data', None)     # holds emotions/tender
    pv.VAULT_PATH.write_bytes(b'x')            # "a vault exists"
    assert pc.piece_key_problem('emotions', 'tender') is not None


@pytest.mark.xfail(strict=True, reason="C3: move-in never checks the vault "
                   "already holds the key")
def test_c3_move_in_does_not_overwrite_the_vault_original(world):
    pm._components['emotions']['tender'] = 'plaintext shadow'
    ok, _msg = pc.move_piece_to_vault('emotions', 'tender', publish=False)
    assert pv.overlay_components()['emotions']['tender'] == SECRET
    assert not ok


def _sealed_private_turn(monkeypatch, chat_private=True):
    """The seal landed under a turn: key gone, vault file still there, the
    turn's own flag still private."""
    import core.api_fastapi as af
    pv.VAULT_PATH.write_bytes(b'sealed')
    monkeypatch.setattr(pv, '_key', None)
    monkeypatch.setattr(pv, '_data', None)
    sm = types.SimpleNamespace(get_chat_settings=lambda: {'private_chat': chat_private})
    monkeypatch.setattr(af, 'get_system', lambda: types.SimpleNamespace(
        llm_chat=types.SimpleNamespace(session_manager=sm)))


def test_c1_private_turn_create_after_the_seal_is_refused(world, monkeypatch):
    from core.chat.function_manager import scope_private
    from functions import meta
    token = scope_private.set(True)            # this turn is private
    try:
        _sealed_private_turn(monkeypatch)
        with pv.reading(True):
            out = meta._prompt_pieces({'action': 'create', 'component': 'emotions',
                                       'key': 'aftermath', 'value': SECRET})
            ok, msg = pc.save_prompt('aftermath', {'type': 'monolith', 'content': SECRET})
    finally:
        scope_private.reset(token)
    assert 'aftermath' not in pm._components.get('emotions', {})
    assert 'vault locked during this turn' in str(out).lower()
    assert not ok and msg == pc.VAULT_SEALED_TURN_MSG
    assert 'aftermath' not in pm._monoliths


def test_c1_a_private_prompt_on_a_public_chat_still_saves_while_locked(world, monkeypatch):
    """A legacy private prompt runs its turn private with the vault locked:
    that is no outlived seal, and her new piece saves as it always did."""
    from core.chat.function_manager import scope_private
    token = scope_private.set(True)
    try:
        _sealed_private_turn(monkeypatch, chat_private=False)
        ok, _ = pc.save_component('emotions', 'calm', 'You feel calm.')
    finally:
        scope_private.reset(token)
    assert ok and pm._components['emotions']['calm'] == 'You feel calm.'


def test_c1_an_existing_plaintext_piece_can_still_be_edited(world, monkeypatch):
    from core.chat.function_manager import scope_private
    token = scope_private.set(True)
    try:
        _sealed_private_turn(monkeypatch)
        ok, _ = pc.save_component('emotions', 'happy', 'You feel very happy.')
    finally:
        scope_private.reset(token)
    assert ok


# ── D1: the seal and a private turn running by name ──

@pytest.fixture
def store(tmp_path, monkeypatch):
    """Real vault crypto and a real chat store on tmp paths: a private chat
    'secret', and a public chat 'desk' that the pointer sits on."""
    from unittest.mock import patch
    import core.chat.history as hist
    import core.api_fastapi as af
    monkeypatch.setattr(pv, 'VAULT_PATH', tmp_path / 'prompt_vault.enc')
    monkeypatch.setattr(pv, 'REFS_PATH', tmp_path / 'vault_refs.json')
    monkeypatch.setattr(pv, '_N', 2 ** 10)
    monkeypatch.setattr(pv, '_publish', lambda action: None)
    monkeypatch.setattr(pv, '_handoff_active', lambda gone: None)
    monkeypatch.setattr(pv, '_audit_row', lambda event: None)
    pv.stop()
    pv._key = pv._salt = pv._data = None
    pv._refs, pv._refs_loaded = {}, False
    monkeypatch.setattr(hist, '_vault_sealed',
                        lambda: pv.vault_exists() and not pv.vault_unlocked())
    defaults = {"prompt": "default"}
    with patch("core.chat.history.get_system_defaults", side_effect=lambda: dict(defaults)), \
         patch("core.chat.history.get_user_defaults", side_effect=lambda: dict(defaults)):
        sm = hist.ChatSessionManager(history_dir=str(tmp_path / 'hist'))
        shim = types.SimpleNamespace(llm_chat=types.SimpleNamespace(session_manager=sm))
        monkeypatch.setattr(af, 'get_system', lambda: shim)
        assert pv.setup("changeme")[0]
        sm.create_chat("secret")
        sm.set_active_chat("secret")
        assert sm.update_chat_settings({'private_chat': True}, expected_active="secret")
        sm.create_chat("desk")
        assert sm.set_active_chat("desk")
        yield sm
    pv.stop()
    pv._key = pv._salt = pv._data = None
    pv._refs, pv._refs_loaded = {}, False
    pv._leases.clear()
    pv._no_leases.set()
    pv._pending = pv._sealing = False


def _llm(sm):
    """A bare LLMChat: begin_stream / end_stream are the real ones."""
    import threading
    from core.chat.chat import LLMChat
    llm = object.__new__(LLMChat)
    llm.session_manager = sm
    llm.tool_engine = None
    llm._streams_lock = threading.Lock()
    llm._streams_by_id, llm._streams_by_chat = {}, {}
    return llm


@pytest.fixture
def fast_stop(monkeypatch):
    monkeypatch.setattr(pv, 'STOP_WAIT_S', 0.3)


def test_d1_a_private_turn_by_name_holds_a_lease_a_public_one_does_not(store):
    llm = _llm(store)
    _, sid, _ = llm.begin_stream("secret", exclusive=True)   # satellite / task / room lane
    assert pv.leases_held() == 1
    _, sid2, _ = llm.begin_stream("desk", exclusive=True)
    assert pv.leases_held() == 1
    llm.end_stream(sid, "secret")
    llm.end_stream(sid2, "desk")
    assert pv.leases_held() == 0


def test_d1_the_open_private_chat_holds_one_too(store):
    sm = store
    assert sm.set_active_chat("secret")
    llm = _llm(sm)
    stream, sid, _ = llm.begin_stream(exclusive=True)        # typed, pointer-bound
    assert stream.target_chat is None and pv.leases_held() == 1
    llm.end_stream(sid, "secret")
    assert pv.leases_held() == 0


def test_d1_the_idle_lock_waits_for_a_private_turn_then_seals(store):
    llm = _llm(store)
    _, sid, _ = llm.begin_stream("secret", exclusive=True)
    pv._last_activity = 0.0                      # the person has been gone a long time
    pv._on_idle()
    assert pv.vault_unlocked(), "the idle seal must wait for the private turn"
    llm.end_stream(sid, "secret")                # its rows are written
    for _ in range(100):
        if not pv.vault_unlocked():
            break
        time.sleep(0.02)
    assert not pv.vault_unlocked(), "and seal the moment it ends"


def test_d1_a_person_back_before_the_turn_ends_calls_the_idle_lock_off(store):
    llm = _llm(store)
    _, sid, _ = llm.begin_stream("secret", exclusive=True)
    pv._last_activity = 0.0
    pv._on_idle()
    assert pv._pending
    pv.touch()                                   # the person spoke
    llm.end_stream(sid, "secret")
    time.sleep(0.2)
    assert pv.vault_unlocked() and not pv._pending


def test_d1_the_idle_locks_limit_stops_the_turn(store, fast_stop):
    llm = _llm(store)
    stream, sid, _ = llm.begin_stream("secret", exclusive=True)
    pv._last_activity = 0.0
    pv._on_idle(stop=True)                       # what the limit timer runs
    assert stream.cancel_flag and not pv.vault_unlocked()
    llm.end_stream(sid, "secret")


def test_a_clicked_lock_stops_private_turns_and_seals_once_they_are_gone(store, fast_stop):
    """Krem's ruling 2026-11-26: the vault does not stay open. The turn is
    stopped, seen gone, then the key drops; a public turn is left alone."""
    import threading
    llm = _llm(store)
    private, sid, _ = llm.begin_stream("secret", exclusive=True)
    public, sid2, _ = llm.begin_stream("desk", exclusive=True)
    seen = {}

    def turn():                                  # the private turn's own thread
        while not private.cancel_flag:
            time.sleep(0.01)
        seen['key_at_last_write'] = pv.chat_data_key() is not None
        llm.end_stream(sid, "secret")
    t = threading.Thread(target=turn, daemon=True)
    t.start()
    assert pv.lock("user") is True
    t.join(2)
    assert seen == {'key_at_last_write': True}, "its last rows were written with the key"
    assert not pv.vault_unlocked() and not public.cancel_flag
    llm.end_stream(sid2, "desk")


def test_a_clicked_lock_seals_anyway_when_a_turn_will_not_stop(store, fast_stop):
    llm = _llm(store)
    stream, sid, _ = llm.begin_stream("secret", exclusive=True)
    assert pv.lock("user") is True               # nothing ends the turn
    assert stream.cancel_flag and not pv.vault_unlocked()
    assert pv.leases_held() == 0
    llm.end_stream(sid, "secret")                # its late end is harmless


def test_d4_nothing_private_starts_while_a_seal_is_under_way(store, monkeypatch):
    from core.chat.chat import ChatBusy
    llm = _llm(store)
    monkeypatch.setattr(pv, '_sealing', True)    # between the decision and the key drop
    with pytest.raises(ChatBusy):
        llm.begin_stream("secret", exclusive=True)
    assert not llm._streams_by_chat, "refused turns leave nothing registered"
    _, sid, _ = llm.begin_stream("desk", exclusive=True)     # a public chat is untouched
    llm.end_stream(sid, "desk")


def test_a_refused_turn_hands_its_lease_back(store):
    from core.chat.chat import ChatBusy
    llm = _llm(store)
    _, sid, _ = llm.begin_stream("secret", exclusive=True)
    with pytest.raises(ChatBusy):
        llm.begin_stream("secret", exclusive=True)           # the chat is taken
    assert pv.leases_held() == 1
    llm.end_stream(sid, "secret")
    assert pv.leases_held() == 0


def test_no_lease_while_the_vault_is_locked(store):
    assert pv.lock("user")
    assert pv.lease() is None


def test_delete_vault_stops_private_turns_and_goes_through(store, fast_stop):
    llm = _llm(store)
    stream, sid, _ = llm.begin_stream("secret", exclusive=True)
    ok, code = pv.destroy()
    assert (ok, code) == (True, '') and stream.cancel_flag
    assert not store.chat_exists("secret")
    llm.end_stream(sid, "secret")


# ── D2: a turn that outlived delete-vault never writes into the public
#    chat that took its name ──

def _private_default(sm):
    assert sm.set_active_chat("default")
    assert sm.update_chat_settings({'private_chat': True}, expected_active="default")
    sm.add_user_message("the private window")
    sm.add_assistant_final("still private")
    assert sm.set_active_chat("desk")


def _plain_rows(sm, name):
    with sm._get_connection() as conn:
        return [r[0] for r in conn.execute(
            "SELECT message_json FROM chat_messages WHERE chat_name = ?", (name,))]


def test_d2_a_by_name_turns_save_after_delete_vault_is_refused(store):
    from core.chat import stream_brain
    sm = store
    _private_default(sm)
    sess = sm.make_stream_session("default")     # a by-name turn read the private chat
    assert pv.destroy() == (True, '')
    assert sm.chat_exists("default")             # back, and public
    tok = stream_brain.set_override(sess)
    try:
        sm.add_assistant_final("a late row from the private turn")
    finally:
        stream_brain.reset_override(tok)
    assert not any('private' in r for r in _plain_rows(sm, "default"))


def test_d2_a_tasks_append_after_delete_vault_is_refused(store):
    sm = store
    _private_default(sm)
    mark = sm.purge_mark(True)                   # the task read it as private
    assert pv.destroy() == (True, '')
    assert sm.append_messages_to_chat(
        "default", [{"role": "assistant", "content": "late private row"}],
        purge_mark=mark) is False
    assert _plain_rows(sm, "default") == []


def test_d2_a_chat_made_public_mid_turn_still_takes_the_turns_rows(store):
    """No purge since the read: the guard stays out of the way."""
    sm = store
    mark = sm.purge_mark(True)
    assert sm.append_messages_to_chat(
        "desk", [{"role": "assistant", "content": "hello"}], purge_mark=mark) is True
