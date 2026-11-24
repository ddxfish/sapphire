"""Vault isolation hunt 2026-11-23 (tmp/vault-isolation-hunt-20261123.md).

Each test asserts the SECURE behavior and is marked xfail(strict) while the
bug is open: XFAIL = the leak is proven, and the mark turns into a loud
failure the day the fix lands (remove the mark then).

Hermetic: the vault is in-memory only (no file), the prompt stores are
swapped for small dicts and nothing here ever calls a real saver.
"""
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


# ── Root A: privacy judged from a name, text rendered from elsewhere ──

@pytest.mark.xfail(strict=True, reason="A1: privacy is read from the preset's "
                   "flag, never from the pieces it renders")
def test_a1_plaintext_preset_wearing_a_vault_piece_is_private(world):
    data = pc.get_prompt('plain')
    assert SECRET in data['content'], "setup: the vault piece renders"
    assert data['privacy_required'] is True


@pytest.mark.xfail(strict=True, reason="A1: the provider gate judges the same "
                   "name flag")
def test_a1_provider_gate_refuses_that_preset_on_a_public_turn(world):
    from core.chat.llm_providers.resolve import _prompt_gate, PrivacyRefused
    with pytest.raises(PrivacyRefused):
        _prompt_gate('plain', private=False)


@pytest.mark.xfail(strict=True, reason="A2: the 'default' sentinel ignores "
                   "pieces and the transient overlay")
def test_a2_default_sentinel_with_a_vault_mood_is_private(world):
    ps.set_transient_piece('emotions', 'tender', 30)
    data = pc.get_prompt('default')
    assert SECRET in data['content'], "setup: the timed mood renders"
    assert data['privacy_required'] is True


@pytest.mark.xfail(strict=True, reason="A3: a missing name is judged public "
                   "while the turn runs on the live default")
def test_a3_gate_for_a_missing_name_follows_what_actually_runs(world):
    from core.chat.llm_providers.resolve import _prompt_gate, PrivacyRefused
    ps.set_transient_piece('emotions', 'tender', 30)
    assert pc.get_prompt('gone-prompt') is None, "setup: the name is dead"
    # chat_streaming falls back to get_prompt('default') for the text
    assert SECRET in pc.get_prompt('default')['content']
    with pytest.raises(PrivacyRefused):
        _prompt_gate('gone-prompt', private=False)


# ── Root B: no reader rule ──

@pytest.mark.xfail(strict=True, reason="B1: her read tools have no check of "
                   "the calling turn's privacy")
def test_b1_prompt_pieces_list_hides_vault_pieces_from_a_public_turn(world):
    from core.chat.function_manager import scope_private
    from functions import meta
    assert scope_private.get() is False, "setup: this turn is not private"
    text, ok = meta._prompt_pieces({'action': 'list', 'component': 'emotions'})
    assert ok and 'happy' in text
    assert 'tender' not in text and SECRET[:20] not in text


@pytest.mark.xfail(strict=True, reason="B1: same door, full text")
def test_b1_prompt_pieces_view_refuses_a_vault_piece_on_a_public_turn(world):
    from functions import meta
    text, _ok = meta._prompt_pieces({'action': 'view', 'component': 'emotions',
                                     'key': 'tender'})
    assert SECRET not in text


@pytest.mark.xfail(strict=True, reason="B1: prompt_view(name) resolves vault "
                   "prompts for any caller")
def test_b1_prompt_view_refuses_a_vault_prompt_on_a_public_turn(world):
    from functions import meta
    ok, _ = pv.set_monolith('private-persona', SECRET)
    assert ok
    text, _ok = meta._prompt_view({'name': 'private-persona'})
    assert SECRET not in text


@pytest.mark.xfail(strict=True, reason="B1: the no-arg listing names every "
                   "vault prompt")
def test_b1_prompt_switch_listing_hides_vault_names_from_a_public_turn(world):
    from functions import meta
    ok, _ = pv.set_monolith('private-persona', SECRET)
    assert ok
    text, _ok = meta._prompt_switch({})
    assert 'private-persona' not in text


@pytest.mark.xfail(strict=True, reason="B4: a public turn can hard-delete a "
                   "vault piece")
def test_b4_public_turn_cannot_delete_a_vault_piece(world):
    from functions import meta
    meta._prompt_pieces({'action': 'delete', 'component': 'emotions',
                         'key': 'tender'})
    assert pv.vault_has_piece('emotions', 'tender')


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


@pytest.mark.xfail(strict=True, reason="C1: a turn that outlives the seal "
                   "routes her new piece to the plaintext store")
def test_c1_private_turn_create_after_the_seal_is_refused(world, monkeypatch):
    from core.chat.function_manager import scope_private
    from functions import meta
    token = scope_private.set(True)            # this turn is private
    try:
        monkeypatch.setattr(pv, '_key', None)  # the seal lands mid-turn
        monkeypatch.setattr(pv, '_data', None)
        meta._prompt_pieces({'action': 'create', 'component': 'emotions',
                             'key': 'aftermath', 'value': SECRET})
    finally:
        scope_private.reset(token)
    assert 'aftermath' not in pm._components.get('emotions', {})


# ── A4: the live prompt cache across a lock ──

class _FakeLLM:
    def __init__(self):
        self.current_system_prompt = ''
        self.session_manager = _FakeSM()

    def set_system_prompt(self, text):
        self.current_system_prompt = text
        return True


class _FakeSM:
    _is_streaming = False
    current_settings = {}

    def flush_for_seal(self):
        return True

    def evict_private_active(self):
        return None


@pytest.fixture
def live(world, monkeypatch):
    """The open chat is PUBLIC and wears 'plain'; its prompt was rendered
    into the live cache while the vault was open (what a chat switch does)."""
    import types
    import core.api_fastapi as af
    system = types.SimpleNamespace(llm_chat=_FakeLLM())
    monkeypatch.setattr(af, 'get_system', lambda: system)
    monkeypatch.setattr(pm, '_active_preset_name', 'plain', raising=False)
    assert ps.apply_scenario('plain')
    system.llm_chat.set_system_prompt(ps.get_current_prompt()['content'])
    assert SECRET in system.llm_chat.current_system_prompt, "setup: cache armed"
    return system


@pytest.mark.xfail(strict=True, reason="A4: lock() re-renders only when the "
                   "active NAME is a vault name")
def test_a4_lock_scrubs_vault_text_from_the_live_prompt(live):
    assert pv.lock("user") is True
    assert not pv.vault_unlocked()
    assert SECRET not in live.llm_chat.current_system_prompt


def test_a4_control_a_rerender_after_the_lock_is_clean(live):
    """The fix shape works: one revalidate after the key drop cleans it."""
    assert pv.lock("user") is True
    assert pc.revalidate_active(system=live, reason="test")
    assert SECRET not in live.llm_chat.current_system_prompt


# ── D1: the seal and a private turn running by name ──

@pytest.fixture
def store(tmp_path, monkeypatch):
    """Real vault crypto and a real chat store on tmp paths: a private chat
    'secret', and a public chat 'desk' that the pointer sits on."""
    import types
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


@pytest.mark.xfail(strict=True, reason="D1: the deferral reads pointer "
                   "streams and the open chat only")
def test_d1_lock_waits_for_a_private_turn_running_by_name(store):
    sm = store
    sm.begin_streaming("secret", pinned=True)      # satellite / task / room lane
    try:
        assert sm.is_streaming("secret") and not sm._is_streaming, "setup"
        locked = pv.lock("idle timeout")
        assert locked is False, "the seal must wait for the private turn"
        assert pv.vault_unlocked()
    finally:
        sm.end_streaming("secret", pinned=True)


def test_d1_control_lock_does_wait_for_the_open_private_chat(store):
    """The existing deferral, for contrast: a pointer-bound private turn."""
    sm = store
    assert sm.set_active_chat("secret")
    sm.begin_streaming()
    try:
        assert pv.lock("idle timeout") is False
        assert pv.vault_unlocked()
    finally:
        sm.end_streaming()
