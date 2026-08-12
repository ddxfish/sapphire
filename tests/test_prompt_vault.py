"""[REGRESSION_GUARD] Prompt vault core (build item 1, tmp/prompt-vault-plan.md):
bytes-level crypto, lifecycle (setup/unlock/lock), idle auto-lock timer,
item mutators, references index, migration guard. No UI, no merge — the
module proven in isolation before anything consumes it.
"""
import json
import time

import pytest

import core.prompt_vault as pv


@pytest.fixture
def vault(tmp_path, monkeypatch):
    """Hermetic vault: paths into tmp, fast scrypt, prompt-system calls and
    event bus neutered (recorded), module state reset both sides."""
    monkeypatch.setattr(pv, 'VAULT_PATH', tmp_path / 'prompt_vault.enc')
    monkeypatch.setattr(pv, 'REFS_PATH', tmp_path / 'vault_refs.json')
    monkeypatch.setattr(pv, '_N', 2 ** 10)   # fast derive; params ride the header
    monkeypatch.setattr(pv, '_idle_seconds', lambda: 1800.0)
    events = []
    monkeypatch.setattr(pv, '_publish', lambda action: events.append(action))
    monkeypatch.setattr(pv, '_handoff_active', lambda gone: None)
    pv.stop()
    pv._key = pv._salt = pv._data = None
    pv._refs, pv._refs_loaded = {}, False
    pv.events = events
    yield pv
    pv.stop()
    pv._key = pv._salt = pv._data = None
    pv._refs, pv._refs_loaded = {}, False
    del pv.events


# ── crypto ──

class TestCrypto:
    def test_round_trip(self, vault):
        import os
        salt = os.urandom(16)
        key = pv._derive("hunter2", salt)
        blob = pv.encrypt_bytes(b'{"hello": "vault"}', key, salt)
        plaintext, key2, salt2 = pv.decrypt_bytes(blob, "hunter2")
        assert plaintext == b'{"hello": "vault"}'
        assert key2 == key and salt2 == salt

    def test_wrong_passphrase_raises(self, vault):
        import os
        salt = os.urandom(16)
        blob = pv.encrypt_bytes(b'secret', pv._derive("right", salt), salt)
        with pytest.raises(pv.VaultWrongKey):
            pv.decrypt_bytes(blob, "wrong")

    def test_bad_magic_raises_corrupt(self, vault):
        with pytest.raises(pv.VaultCorrupt):
            pv.decrypt_bytes(b'NOTAVAULT---' + b'x' * 64, "any")

    def test_truncated_raises_corrupt(self, vault):
        import os
        salt = os.urandom(16)
        blob = pv.encrypt_bytes(b'secret', pv._derive("k", salt), salt)
        with pytest.raises(pv.VaultCorrupt):
            pv.decrypt_bytes(blob[:20], "k")

    def test_tampered_ciphertext_fails_tag(self, vault):
        import os
        salt = os.urandom(16)
        blob = pv.encrypt_bytes(b'secret', pv._derive("k", salt), salt)
        bad = blob[:-1] + bytes([blob[-1] ^ 0xFF])
        with pytest.raises(pv.VaultWrongKey):
            pv.decrypt_bytes(bad, "k")

    def test_magic_is_not_backup_magic(self, vault, tmp_path):
        """A vault file must never be misidentified as an encrypted backup
        (decrypt tool / is_encrypted_backup would mangle it)."""
        from core.backup_crypto import is_encrypted_backup, MAGIC as BAK
        assert pv.MAGIC != BAK
        ok, _ = pv.setup("key")
        assert ok
        assert not is_encrypted_backup(pv.VAULT_PATH)


# ── lifecycle ──

class TestLifecycle:
    def test_setup_creates_and_unlocks(self, vault):
        ok, code = pv.setup("key")
        assert ok and code == ''
        assert pv.vault_exists() and pv.vault_unlocked()
        assert pv.vault_status() == {"exists": True, "unlocked": True}
        assert "vault_changed" in pv.events

    def test_setup_refuses_existing(self, vault):
        assert pv.setup("key")[0]
        ok, code = pv.setup("other")
        assert not ok and code == 'exists'

    def test_setup_refuses_empty_passphrase(self, vault):
        ok, code = pv.setup("")
        assert not ok and code == 'bad_passphrase'
        assert not pv.vault_exists()

    def test_unlock_no_vault(self, vault):
        ok, code = pv.unlock("key")
        assert not ok and code == 'no_vault'

    def test_lock_then_unlock_round_trip(self, vault):
        pv.setup("key")
        assert pv.set_monolith("moonlight", "secret text")[0]
        assert pv.lock()
        assert not pv.vault_unlocked()
        assert pv._key is None and pv._data is None
        ok, code = pv.unlock("key")
        assert ok and code == ''
        assert pv.overlay_monoliths()["moonlight"]["content"] == "secret text"

    def test_wrong_key_refused_file_intact(self, vault):
        pv.setup("right")
        pv.lock()
        ok, code = pv.unlock("wrong")
        assert not ok and code == 'wrong_key'
        assert pv.vault_exists() and not pv.vault_unlocked()
        ok, _ = pv.unlock("right")
        assert ok

    def test_unlock_idempotent_while_unlocked(self, vault):
        pv.setup("key")
        ok, code = pv.unlock("anything-even-wrong")
        assert ok and code == ''

    def test_lock_idempotent(self, vault):
        assert not pv.lock()
        pv.setup("key")
        assert pv.lock()
        assert not pv.lock()

    def test_corrupt_file_quarantined(self, vault, tmp_path):
        pv.VAULT_PATH.write_bytes(b'garbage that is not a vault at all')
        ok, code = pv.unlock("key")
        assert not ok and code == 'corrupt'
        assert not pv.VAULT_PATH.exists()
        bad = list(tmp_path.glob('prompt_vault.enc.bad-*'))
        assert len(bad) == 1
        assert bad[0].read_bytes() == b'garbage that is not a vault at all'


# ── normalization at unlock ──

class TestNormalization:
    def _write_raw_vault(self, passphrase, raw):
        import os
        salt = os.urandom(16)
        key = pv._derive(passphrase, salt)
        blob = pv.encrypt_bytes(json.dumps(raw).encode(), key, salt)
        pv.VAULT_PATH.write_bytes(blob)

    def test_junk_dropped_good_kept_privacy_forced(self, vault):
        self._write_raw_vault("key", {
            "monoliths": {"good": {"content": "text", "privacy_required": False},
                          "bare": "plain string form",
                          "bad": {"content": 42}, "_hidden": "x"},
            "components": {"character": {"rose": "Rose.", "bad": 7},
                           "junktype": "not-a-dict"},
            "scenario_presets": {"night": {"character": "rose"},
                                 "bad": "nope"},
        })
        ok, _ = pv.unlock("key")
        assert ok
        monos = pv.overlay_monoliths()
        assert set(monos) == {"good", "bare"}
        # privacy_required forced True by construction — even if stored False
        assert all(m["privacy_required"] is True for m in monos.values())
        comps = pv.overlay_components()
        assert comps == {"character": {"rose": "Rose."}}
        presets = pv.overlay_presets()
        assert set(presets) == {"night"}
        assert presets["night"]["_privacy_required"] is True

    def test_non_dict_payload_starts_empty(self, vault):
        self._write_raw_vault("key", ["not", "a", "dict"])
        ok, _ = pv.unlock("key")
        assert ok
        assert pv.overlay_monoliths() == {}


# ── overlays ──

class TestOverlays:
    def test_locked_overlays_empty(self, vault):
        pv.setup("key")
        pv.set_monolith("moonlight", "text")
        pv.lock()
        assert pv.overlay_monoliths() == {}
        assert pv.overlay_components() == {}
        assert pv.overlay_presets() == {}

    def test_overlays_are_copies(self, vault):
        """Aliasing trap (recon E-N6): mutating a returned overlay must not
        touch vault state."""
        pv.setup("key")
        pv.set_piece("character", "rose", "Rose.")
        view = pv.overlay_components()
        view["character"]["rose"] = "MUTATED"
        assert pv.overlay_components()["character"]["rose"] == "Rose."


# ── mutators ──

class TestMutators:
    def test_locked_refused(self, vault):
        pv.setup("key")
        pv.lock()
        assert pv.set_monolith("m", "x") == (False, 'locked')
        assert pv.set_piece("t", "k", "v") == (False, 'locked')
        assert pv.set_preset("p", {}) == (False, 'locked')

    def test_reserved_names_refused(self, vault):
        pv.setup("key")
        assert pv.set_monolith("default", "x") == (False, 'bad_name')
        assert pv.set_preset("assembled", {}) == (False, 'bad_name')

    def test_bad_shapes_refused(self, vault):
        pv.setup("key")
        assert pv.set_monolith("m", 42) == (False, 'bad_value')
        assert pv.set_piece("t", "_k", "v") == (False, 'bad_name')
        assert pv.set_preset("p", {"x": 42}) == (False, 'bad_value')

    def test_delete_codes(self, vault):
        pv.setup("key")
        assert pv.delete_monolith("ghost") == (False, 'not_found')
        assert pv.delete_piece("t", "ghost") == (False, 'not_found')
        assert pv.delete_preset("ghost") == (False, 'not_found')
        pv.set_piece("character", "rose", "Rose.")
        assert pv.delete_piece("character", "rose") == (True, '')
        assert pv.overlay_components() == {}

    def test_mutations_persist_across_relock(self, vault):
        pv.setup("key")
        pv.set_monolith("moonlight", "v1")
        pv.set_monolith("moonlight", "v2")
        pv.set_preset("night", {"character": "rose"})
        pv.delete_monolith("moonlight")
        pv.lock()
        pv.unlock("key")
        assert pv.overlay_monoliths() == {}
        assert set(pv.overlay_presets()) == {"night"}

    def test_save_failure_surfaces(self, vault, monkeypatch):
        pv.setup("key")
        monkeypatch.setattr(pv, 'encrypt_bytes',
                            lambda *a: (_ for _ in ()).throw(OSError("disk")))
        assert pv.set_monolith("m", "x") == (False, 'save_failed')


# ── idle auto-lock ──

class TestIdleLock:
    def test_timer_is_daemon(self, vault):
        pv.setup("key")
        assert pv._timer is not None and pv._timer.daemon is True

    def test_idle_fires_lock(self, vault):
        """Deterministic: age the activity stamp, invoke the callback."""
        pv.setup("key")
        pv._last_activity = time.monotonic() - 3600
        pv._on_idle()
        assert not pv.vault_unlocked()

    def test_activity_rearms_instead_of_locking(self, vault):
        pv.setup("key")
        pv.touch()
        before = pv._timer
        pv._on_idle()
        assert pv.vault_unlocked()
        assert pv._timer is not None and pv._timer is not before

    def test_callback_noop_when_already_locked(self, vault):
        pv.setup("key")
        pv.lock()
        pv._on_idle()   # must not blow up or re-arm
        assert pv._timer is None

    def test_real_timer_fires(self, vault, monkeypatch):
        """One live-fire proof the Timer wiring works end to end."""
        monkeypatch.setattr(pv, '_idle_seconds', lambda: 0.2)
        pv.setup("key")
        deadline = time.monotonic() + 5
        while pv.vault_unlocked() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not pv.vault_unlocked()

    def test_stop_cancels_timer(self, vault):
        pv.setup("key")
        pv.stop()
        assert pv._timer is None
        assert pv.vault_unlocked()   # teardown cancels the timer, not the key


# ── references index ──

class TestRefsIndex:
    def test_stamp_requires_unlocked_and_membership(self, vault):
        pv.setup("key")
        pv.set_monolith("moonlight", "text")
        assert pv.refs_stamp("moonlight") == (True, '')
        assert pv.refs_stamp("ghost") == (False, 'not_in_vault')
        pv.lock()
        assert pv.refs_stamp("moonlight") == (False, 'locked')

    def test_names_survive_lock_and_reload(self, vault):
        pv.setup("key")
        pv.set_preset("night", {"character": "rose"})
        pv.refs_stamp("night")
        pv.lock()
        assert pv.refs_names() == {"night": "preset"}
        # simulate process restart: force reload from disk
        pv._refs, pv._refs_loaded = {}, False
        assert pv.refs_names() == {"night": "preset"}

    def test_drop_works_while_locked(self, vault):
        pv.setup("key")
        pv.set_monolith("moonlight", "text")
        pv.refs_stamp("moonlight")
        pv.lock()
        assert pv.refs_drop("moonlight")
        assert pv.refs_names() == {}
        assert pv.refs_drop("moonlight")   # idempotent

    def test_index_holds_only_referenced_names(self, vault):
        """Ruling C amendment: the sidecar is never a full inventory."""
        pv.setup("key")
        pv.set_monolith("referenced", "a")
        pv.set_monolith("unreferenced", "b")
        pv.refs_stamp("referenced")
        assert set(pv.refs_names()) == {"referenced"}
        on_disk = json.loads(pv.REFS_PATH.read_text(encoding='utf-8'))
        assert set(on_disk["names"]) == {"referenced"}

    def test_delete_reconciles_index(self, vault):
        pv.setup("key")
        pv.set_monolith("moonlight", "text")
        pv.refs_stamp("moonlight")
        pv.delete_monolith("moonlight")
        assert pv.refs_names() == {}

    def test_reconcile_with_referrer_set(self, vault):
        pv.setup("key")
        pv.set_monolith("a", "x")
        pv.set_monolith("b", "y")
        pv.refs_stamp("a")
        pv.refs_stamp("b")
        assert pv.refs_reconcile(referenced={"a"})
        assert set(pv.refs_names()) == {"a"}

    def test_reconcile_refused_while_locked(self, vault):
        pv.setup("key")
        pv.lock()
        assert pv.refs_reconcile() is False

    def test_unreadable_refs_starts_empty(self, vault):
        pv.REFS_PATH.write_text("{not json", encoding='utf-8')
        assert pv.refs_names() == {}


# ── event hygiene ──

class TestEventHygiene:
    def test_publish_payload_is_name_free(self, monkeypatch):
        """SSE replays recent events to every new tab — vault names must
        never ride the bus (recon finding 13)."""
        import core.event_bus as eb
        seen = []
        monkeypatch.setattr(eb, 'publish', lambda ev, data: seen.append((ev, data)))
        pv._publish("vault_changed")
        assert len(seen) == 1
        assert seen[0][1] == {"name": "", "action": "vault_changed"}


# ── migration guard ──

class TestMigrationGuard:
    def _setup_dir(self, tmp_path, monkeypatch):
        from core import migration
        monkeypatch.setattr(migration, 'USER_PROMPTS_DIR', tmp_path)
        return migration

    def test_refs_index_not_eaten_by_loose_fold(self, tmp_path, monkeypatch):
        """Recon finding 10: the loose-file fold globs *.json — without the
        system_stems entry, vault_refs.json warns forever or gets folded."""
        migration = self._setup_dir(tmp_path, monkeypatch)
        (tmp_path / 'prompt_monoliths.json').write_text('{}', encoding='utf-8')
        refs = json.dumps({"version": 1, "names": {"moonlight": "monolith"}})
        (tmp_path / 'vault_refs.json').write_text(refs, encoding='utf-8')
        migration.migrate_loose_prompt_files()
        assert (tmp_path / 'vault_refs.json').read_text(encoding='utf-8') == refs
        assert not (tmp_path / 'vault_refs.json.imported').exists()
        assert not (tmp_path / 'vault_refs.json.duplicate').exists()

    def test_refs_index_skipped_by_user_prompt_migration(self, tmp_path, monkeypatch):
        migration = self._setup_dir(tmp_path, monkeypatch)
        refs = json.dumps({"version": 1, "names": {}})
        (tmp_path / 'vault_refs.json').write_text(refs, encoding='utf-8')
        migration._migrate_user_prompts()
        assert (tmp_path / 'vault_refs.json').read_text(encoding='utf-8') == refs

    def test_enc_file_invisible_to_migration(self, tmp_path, monkeypatch):
        migration = self._setup_dir(tmp_path, monkeypatch)
        (tmp_path / 'prompt_monoliths.json').write_text('{}', encoding='utf-8')
        (tmp_path / 'prompt_vault.enc').write_bytes(b'\x00binary')
        migration.migrate_loose_prompt_files()
        migration._migrate_user_prompts()
        assert (tmp_path / 'prompt_vault.enc').read_bytes() == b'\x00binary'


# ═══ STEP 2: merge into prompt_manager properties (packs < vault < user) ═══

class TestMergeIntoManager:
    """Vault overlay rides the read properties. Locked = names exist nowhere;
    unlocked = merged under user entries; precedence packs < vault < user."""

    PACK = "vault-test-pack"

    @pytest.fixture
    def merged(self, vault):
        from core import prompt_packs
        from core.prompt_manager import prompt_manager
        pv.setup("key")
        pv.set_monolith("vlt_mono", "Vault monolith text")
        pv.set_piece("emotions", "vlt_piece", "Vault piece text")
        pv.set_preset("vlt_preset", {"character": "vlt"})
        yield prompt_manager
        prompt_packs.unregister_plugin(self.PACK)
        prompt_manager._monoliths.pop("vlt_mono", None)
        prompt_manager._components.get("emotions", {}).pop("vlt_piece", None)

    def test_unlocked_names_visible(self, merged):
        assert merged.monoliths["vlt_mono"]["content"] == "Vault monolith text"
        assert merged.monoliths["vlt_mono"]["privacy_required"] is True
        assert merged.components["emotions"]["vlt_piece"] == "Vault piece text"
        assert merged.scenario_presets["vlt_preset"]["_privacy_required"] is True

    def test_locked_names_absent(self, merged):
        pv.lock()
        assert "vlt_mono" not in merged.monoliths
        assert "vlt_piece" not in merged.components.get("emotions", {})
        assert "vlt_preset" not in merged.scenario_presets

    def test_resolver_sees_vault_only_while_unlocked(self, merged):
        from core import prompt_crud
        got = prompt_crud.get_prompt("vlt_mono")
        assert got["type"] == "monolith"
        assert got["content"] == "Vault monolith text"
        assert got["privacy_required"] is True
        pv.lock()
        assert prompt_crud.get_prompt("vlt_mono") is None

    def test_user_wins_over_vault(self, merged):
        merged._monoliths["vlt_mono"] = {"content": "User version",
                                         "privacy_required": False}
        try:
            assert merged.monoliths["vlt_mono"]["content"] == "User version"
            from core import prompt_crud
            assert prompt_crud.get_prompt("vlt_mono")["content"] == "User version"
        finally:
            del merged._monoliths["vlt_mono"]
        assert merged.monoliths["vlt_mono"]["content"] == "Vault monolith text"

    def test_vault_wins_over_pack(self, merged):
        from core import prompt_packs
        prompt_packs.register_pack(self.PACK,
                                   monoliths={"vlt_mono": "Pack version"})
        assert merged.monoliths["vlt_mono"]["content"] == "Vault monolith text"
        pv.lock()
        # Vault sealed — the pack layer shows through underneath
        assert merged.monoliths["vlt_mono"]["content"] == "Pack version"

    def test_shadow_warning_logged_at_unlock(self, merged, caplog):
        import logging
        pv.lock()
        merged._monoliths["vlt_mono"] = {"content": "User version",
                                         "privacy_required": False}
        try:
            with caplog.at_level(logging.WARNING, logger="core.prompt_vault"):
                pv.unlock("key")
            assert any("vlt_mono" in r.message and "shadowed" in r.message
                       for r in caplog.records)
        finally:
            del merged._monoliths["vlt_mono"]


class TestFastPathKilled:
    """Recon finding 2 + aliasing trap E-N6: with NO overlays at all, the old
    properties returned the LIVE private dicts — a mutation through the view
    silently persisted, and a vault unlock would never merge. Views must be
    fresh copies in every overlay state."""

    def test_properties_never_return_live_dicts(self, vault):
        from core.prompt_manager import prompt_manager
        assert not pv.vault_unlocked()   # vault locked, packs whatever's loaded
        view = prompt_manager.components
        assert view is not prompt_manager._components
        view.setdefault("zz_vault_test_type", {})["zz"] = "mutation"
        assert "zz_vault_test_type" not in prompt_manager._components
        mono_view = prompt_manager.monoliths
        assert mono_view is not prompt_manager._monoliths
        mono_view["zz_vault_test"] = {"content": "x"}
        assert "zz_vault_test" not in prompt_manager._monoliths
        preset_view = prompt_manager.scenario_presets
        assert preset_view is not prompt_manager._scenario_presets

    def test_no_packs_no_vault_still_merges_user(self, vault):
        """The kill must not LOSE user entries in the both-empty case."""
        from core.prompt_manager import prompt_manager
        prompt_manager._monoliths["zz_vault_user_mono"] = {
            "content": "User text", "privacy_required": False}
        try:
            assert prompt_manager.monoliths["zz_vault_user_mono"]["content"] == "User text"
        finally:
            del prompt_manager._monoliths["zz_vault_user_mono"]


class TestDegradedTurn:
    """Locked vault + active preset pinned to a vault name → loud fallback to
    assembled default (the standing H3 machinery, now covering the vault)."""

    def test_get_current_prompt_falls_back_loudly(self, vault, caplog):
        import logging
        from core import prompt_state
        pv.setup("key")
        pv.set_monolith("vlt_active", "Vault text")
        pv.lock()
        old = prompt_state._assembled_state.get("active_preset", "default")
        prompt_state._assembled_state["active_preset"] = "vlt_active"
        try:
            with caplog.at_level(logging.WARNING, logger="core.prompt_state"):
                msg = prompt_state.get_current_prompt()
            assert msg["role"] == "system"
            assert "Vault text" not in msg["content"]
            assert any("vlt_active" in r.message for r in caplog.records)
        finally:
            prompt_state._assembled_state["active_preset"] = old

    def test_heals_on_unlock(self, vault):
        from core import prompt_state
        pv.setup("key")
        pv.set_monolith("vlt_active", "Vault text")
        pv.lock()
        pv.unlock("key")
        old = prompt_state._assembled_state.get("active_preset", "default")
        prompt_state._assembled_state["active_preset"] = "vlt_active"
        try:
            msg = prompt_state.get_current_prompt()
            assert msg["content"] == "Vault text"
        finally:
            prompt_state._assembled_state["active_preset"] = old


class TestVaultWarmth:
    """Turn-time touch wiring: a turn wearing a vault prompt keeps the vault
    warm; a public turn does not (idle-lock is for walking away)."""

    def _run_turn(self, chat_prompt_name):
        from unittest.mock import MagicMock, patch
        import config
        from core.chat.chat import LLMChat
        obj = LLMChat.__new__(LLMChat)
        obj.current_system_prompt = 'Base.'
        obj.session_manager = MagicMock()
        obj.session_manager.get_chat_settings.return_value = {
            'prompt': chat_prompt_name, 'spice_enabled': False}
        with patch.object(config, 'DEFAULT_USERNAME', 'T', create=True), \
             patch('core.chat.chat.hook_runner') as mock_hooks, \
             patch('core.chat.stream_brain.get_override', return_value=None):
            mock_hooks.has_handlers.return_value = False
            obj._get_system_prompt()

    def test_vault_prompt_turn_touches(self, vault):
        pv.setup("key")
        pv.set_monolith("vlt_warm", "text")
        pv._last_activity = 0.0
        self._run_turn("vlt_warm")
        assert pv._last_activity > 0.0

    def test_public_turn_does_not_touch(self, vault):
        pv.setup("key")
        pv.set_monolith("vlt_warm", "text")
        pv._last_activity = 0.0
        self._run_turn("zz_not_a_vault_name")
        assert pv._last_activity == 0.0

    def test_locked_vault_turn_does_not_touch(self, vault):
        pv.setup("key")
        pv.set_monolith("vlt_warm", "text")
        pv.lock()
        pv._last_activity = 0.0
        self._run_turn("vlt_warm")
        assert pv._last_activity == 0.0


# ═══ STEP 3: write routing at the funnels ═══

class TestPromptWriteRouting:
    """The one behavioral rule: exists-in-user → regular; exists-in-vault →
    vault; pack-shadow edit → regular; NEW: unlocked → vault, locked →
    regular. origin='vault' refused while locked (stale-editor guard);
    origin='regular' pins the user store (import lanes)."""

    PACK = "vault-routing-test-pack"

    @pytest.fixture
    def routed(self, vault, tmp_path, monkeypatch):
        """Unlocked vault + real singleton with stores redirected to tmp."""
        from core import prompt_packs
        from core.prompt_manager import prompt_manager
        store_dir = tmp_path / "user_prompts"
        store_dir.mkdir()
        monkeypatch.setattr(prompt_manager, "USER_DIR", store_dir)
        pv.setup("key")
        yield prompt_manager
        prompt_packs.unregister_plugin(self.PACK)
        for n in list(prompt_manager._monoliths):
            if n.startswith("vlt_"):
                del prompt_manager._monoliths[n]
        for n in list(prompt_manager._scenario_presets):
            if n.startswith("vlt_"):
                del prompt_manager._scenario_presets[n]
        for ctype in list(prompt_manager._components):
            for k in list(prompt_manager._components[ctype]):
                if k.startswith("vlt_"):
                    del prompt_manager._components[ctype][k]

    def test_new_monolith_while_unlocked_routes_to_vault(self, routed):
        from core import prompt_crud
        ok, msg = prompt_crud.save_prompt(
            "vlt_new", {"type": "monolith", "content": "secret"})
        assert ok and msg.endswith("(vault)")
        assert pv.vault_has_prompt("vlt_new")
        assert "vlt_new" not in routed._monoliths

    def test_new_monolith_while_locked_routes_regular(self, routed):
        from core import prompt_crud
        pv.lock()
        ok, msg = prompt_crud.save_prompt(
            "vlt_plain", {"type": "monolith", "content": "public"})
        assert ok and "(vault)" not in msg
        assert "vlt_plain" in routed._monoliths
        pv.unlock("key")
        assert not pv.vault_has_prompt("vlt_plain")

    def test_existing_user_prompt_stays_regular_while_unlocked(self, routed):
        from core import prompt_crud
        routed._monoliths["vlt_mine"] = {"content": "v1", "privacy_required": False}
        ok, msg = prompt_crud.save_prompt(
            "vlt_mine", {"type": "monolith", "content": "v2"})
        assert ok and "(vault)" not in msg
        assert routed._monoliths["vlt_mine"]["content"] == "v2"
        assert not pv.vault_has_prompt("vlt_mine")

    def test_existing_vault_prompt_stays_vault(self, routed):
        from core import prompt_crud
        prompt_crud.save_prompt("vlt_v", {"type": "monolith", "content": "v1"})
        ok, msg = prompt_crud.save_prompt(
            "vlt_v", {"type": "monolith", "content": "v2"})
        assert ok and msg.endswith("(vault)")
        assert pv.overlay_monoliths()["vlt_v"]["content"] == "v2"
        assert "vlt_v" not in routed._monoliths

    def test_pack_shadow_edit_stays_regular(self, routed):
        from core import prompt_crud, prompt_packs
        prompt_packs.register_pack(self.PACK, monoliths={"vlt_shipped": "Pack text"})
        ok, msg = prompt_crud.save_prompt(
            "vlt_shipped", {"type": "monolith", "content": "My custom edit"})
        assert ok and "(vault)" not in msg
        assert routed._monoliths["vlt_shipped"]["content"] == "My custom edit"
        assert not pv.vault_has_prompt("vlt_shipped")

    def test_origin_vault_while_locked_refused(self, routed):
        from core import prompt_crud
        pv.lock()
        ok, msg = prompt_crud.save_prompt(
            "vlt_stale", {"type": "monolith", "content": "decrypted text"},
            origin="vault")
        assert not ok and msg == prompt_crud.VAULT_LOCKED_MSG
        assert "vlt_stale" not in routed._monoliths   # nothing splashed to disk

    def test_origin_regular_forces_user_store(self, routed):
        from core import prompt_crud
        ok, msg = prompt_crud.save_prompt(
            "vlt_import", {"type": "monolith", "content": "card content"},
            origin="regular")
        assert ok and "(vault)" not in msg
        assert "vlt_import" in routed._monoliths
        assert not pv.vault_has_prompt("vlt_import")

    def test_reserved_names_blocked_before_routing(self, routed):
        from core import prompt_crud
        ok, msg = prompt_crud.save_prompt(
            "default", {"type": "monolith", "content": "x"})
        assert not ok and "reserved" in msg
        assert not pv.vault_has_prompt("default")

    def test_allow_overwrite_false_counts_vault_entries(self, routed):
        from core import prompt_crud
        prompt_crud.save_prompt("vlt_dup", {"type": "monolith", "content": "a"})
        ok, msg = prompt_crud.save_prompt(
            "vlt_dup", {"type": "monolith", "content": "b"}, allow_overwrite=False)
        assert not ok and "already exists" in msg

    def test_vault_cross_type_refused(self, routed):
        from core import prompt_crud
        prompt_crud.save_prompt("vlt_x", {"type": "assembled",
                                          "components": {"character": "a"}})
        ok, msg = prompt_crud.save_prompt(
            "vlt_x", {"type": "monolith", "content": "b"})
        assert not ok and "other prompt type" in msg

    def test_assembled_routes_to_vault_privacy_forced(self, routed):
        from core import prompt_crud
        ok, msg = prompt_crud.save_prompt(
            "vlt_scene", {"type": "assembled",
                          "components": {"character": "rose"},
                          "privacy_required": False})
        assert ok and msg.endswith("(vault)")
        assert pv.overlay_presets()["vlt_scene"]["_privacy_required"] is True

    def test_delete_vault_prompt(self, routed):
        from core import prompt_crud
        prompt_crud.save_prompt("vlt_gone", {"type": "monolith", "content": "x"})
        assert prompt_crud.delete_prompt("vlt_gone") is True
        assert not pv.vault_has_prompt("vlt_gone")

    def test_delete_user_shadow_reveals_vault(self, routed):
        from core import prompt_crud
        prompt_crud.save_prompt("vlt_pair", {"type": "monolith", "content": "vault v"})
        routed._monoliths["vlt_pair"] = {"content": "user v", "privacy_required": False}
        assert prompt_crud.delete_prompt("vlt_pair") is True
        assert "vlt_pair" not in routed._monoliths
        assert pv.overlay_monoliths()["vlt_pair"]["content"] == "vault v"

    def test_delete_sealed_name_not_found(self, routed):
        from core import prompt_crud
        prompt_crud.save_prompt("vlt_sealed", {"type": "monolith", "content": "x"})
        pv.lock()
        assert prompt_crud.delete_prompt("vlt_sealed") is False
        pv.unlock("key")
        assert pv.vault_has_prompt("vlt_sealed")   # untouched behind the seal


class TestPieceWriteRouting:
    """Same routing rule at piece granularity, through save_component /
    delete_component."""

    @pytest.fixture
    def routed(self, vault, tmp_path, monkeypatch):
        from core.prompt_manager import prompt_manager
        store_dir = tmp_path / "user_prompts"
        store_dir.mkdir()
        monkeypatch.setattr(prompt_manager, "USER_DIR", store_dir)
        pv.setup("key")
        yield prompt_manager
        for ctype in list(prompt_manager._components):
            for k in list(prompt_manager._components[ctype]):
                if k.startswith("vlt_"):
                    del prompt_manager._components[ctype][k]

    def test_new_piece_routes_to_vault(self, routed):
        from core import prompt_crud
        ok, msg = prompt_crud.save_component("character", "vlt_rose", "Rose.")
        assert ok and msg.endswith("(vault)")
        assert pv.vault_has_piece("character", "vlt_rose")
        assert "vlt_rose" not in routed._components.get("character", {})

    def test_existing_user_piece_stays_regular(self, routed):
        from core import prompt_crud
        routed._components.setdefault("character", {})["vlt_old"] = "v1"
        ok, msg = prompt_crud.save_component("character", "vlt_old", "v2")
        assert ok and "(vault)" not in msg
        assert routed._components["character"]["vlt_old"] == "v2"
        assert not pv.vault_has_piece("character", "vlt_old")

    def test_pack_shadow_piece_stays_regular(self, routed, monkeypatch):
        from core import prompt_crud, prompt_packs
        monkeypatch.setattr(prompt_packs, "piece_source",
                            lambda t, k: "some-plugin" if k == "vlt_shipped" else None)
        ok, msg = prompt_crud.save_component("character", "vlt_shipped", "edit")
        assert ok and "(vault)" not in msg
        assert routed._components["character"]["vlt_shipped"] == "edit"

    def test_origin_vault_while_locked_refused(self, routed):
        from core import prompt_crud
        pv.lock()
        ok, msg = prompt_crud.save_component("character", "vlt_leak",
                                             "decrypted", origin="vault")
        assert not ok and msg == prompt_crud.VAULT_LOCKED_MSG
        assert "vlt_leak" not in routed._components.get("character", {})

    def test_vault_piece_event_is_name_free(self, routed, monkeypatch):
        import core.event_bus as eb
        seen = []
        monkeypatch.setattr(eb, "publish", lambda ev, data: seen.append(data))
        from core import prompt_crud
        prompt_crud.save_component("character", "vlt_quiet", "text")
        assert seen and all("vlt_quiet" not in str(d) for d in seen)

    def test_delete_routes_to_vault(self, routed):
        from core import prompt_crud
        prompt_crud.save_component("character", "vlt_del", "x")
        ok, code = prompt_crud.delete_component("character", "vlt_del")
        assert ok and code == ''
        assert not pv.vault_has_piece("character", "vlt_del")

    def test_delete_sealed_piece_not_found(self, routed):
        from core import prompt_crud
        prompt_crud.save_component("character", "vlt_hidden", "x")
        pv.lock()
        ok, code = prompt_crud.delete_component("character", "vlt_hidden")
        assert not ok and code == 'not_found'

    def test_origin_regular_pins_user_store(self, routed):
        """Client import lane: origin='regular' must beat new-while-unlocked
        routing (same rule as the prompt funnel)."""
        from core import prompt_crud
        ok, msg = prompt_crud.save_component("character", "vlt_imported",
                                             "card text", origin="regular")
        assert ok and "(vault)" not in msg
        assert routed._components["character"]["vlt_imported"] == "card text"
        assert not pv.vault_has_piece("character", "vlt_imported")

    def test_batch_import_never_routes_to_vault(self, routed):
        from core import prompt_crud
        ok, _ = prompt_crud.save_components_batch(
            {"character": {"vlt_card": "imported text"}})
        assert ok
        assert routed._components["character"]["vlt_card"] == "imported text"
        assert not pv.vault_has_piece("character", "vlt_card")


# ═══ STEP 4: vault lifecycle routes ═══

class _Req:
    headers = {}

    def __init__(self, payload):
        self._payload = payload

    async def json(self):
        return self._payload


class TestVaultRoutes:
    """POST setup/unlock/lock. Wrong key MUST be 403 — fetch.js redirects
    any 401 to /login, so a typo would log the user out (finding 5)."""

    @pytest.fixture
    def routes(self, vault, monkeypatch):
        import core.api_fastapi  # noqa: F401  (route modules trip circular imports alone)
        from core.routes import vault as vault_routes
        slept = []

        async def _fake_sleep(s):
            slept.append(s)
        monkeypatch.setattr(vault_routes.asyncio, "sleep", _fake_sleep)
        vault_routes.slept = slept
        yield vault_routes
        del vault_routes.slept

    def _run(self, coro):
        import asyncio
        return asyncio.run(coro)

    def test_setup_then_duplicate_409(self, routes):
        from fastapi import HTTPException
        result = self._run(routes.vault_setup(_Req({"key": "hunter2"}), None))
        assert result["vault"] == {"exists": True, "unlocked": True}
        with pytest.raises(HTTPException) as ei:
            self._run(routes.vault_setup(_Req({"key": "other"}), None))
        assert ei.value.status_code == 409

    def test_setup_empty_key_400(self, routes):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as ei:
            self._run(routes.vault_setup(_Req({"key": ""}), None))
        assert ei.value.status_code == 400

    def test_wrong_key_is_403_not_401_with_delay(self, routes):
        from fastapi import HTTPException
        self._run(routes.vault_setup(_Req({"key": "right"}), None))
        self._run(routes.vault_lock(None))
        with pytest.raises(HTTPException) as ei:
            self._run(routes.vault_unlock(_Req({"key": "wrong"}), None))
        assert ei.value.status_code == 403          # NEVER 401
        assert routes.slept == [routes._FAIL_DELAY_S]

    def test_unlock_no_vault_404(self, routes):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as ei:
            self._run(routes.vault_unlock(_Req({"key": "x"}), None))
        assert ei.value.status_code == 404

    def test_unlock_corrupt_500(self, routes):
        from fastapi import HTTPException
        pv.VAULT_PATH.write_bytes(b"not a vault")
        with pytest.raises(HTTPException) as ei:
            self._run(routes.vault_unlock(_Req({"key": "x"}), None))
        assert ei.value.status_code == 500

    def test_lock_unlock_round_trip_and_idempotent(self, routes):
        self._run(routes.vault_setup(_Req({"key": "k"}), None))
        r1 = self._run(routes.vault_lock(None))
        assert r1["vault"]["unlocked"] is False
        r2 = self._run(routes.vault_lock(None))       # idempotent
        assert r2["vault"]["unlocked"] is False
        r3 = self._run(routes.vault_unlock(_Req({"key": "k"}), None))
        assert r3["vault"]["unlocked"] is True
        r4 = self._run(routes.vault_unlock(_Req({"key": "anything"}), None))
        assert r4["vault"]["unlocked"] is True        # idempotent while open
        assert routes.slept == []                     # no failures, no delays

    def test_managed_mode_gates_setup_and_unlock_not_lock(self, routes, monkeypatch):
        from fastapi import HTTPException
        from core.settings_manager import settings as sm_settings
        self._run(routes.vault_setup(_Req({"key": "k"}), None))
        monkeypatch.setattr(sm_settings, "is_managed", lambda: True)
        with pytest.raises(HTTPException) as ei:
            self._run(routes.vault_setup(_Req({"key": "k"}), None))
        assert ei.value.status_code == 403
        with pytest.raises(HTTPException) as ei:
            self._run(routes.vault_unlock(_Req({"key": "k"}), None))
        assert ei.value.status_code == 403
        r = self._run(routes.vault_lock(None))        # locking always allowed
        assert r["vault"]["unlocked"] is False


# ═══ STEP 6: references-index wiring (ruling C amendment) ═══

class TestVaultRefSync:
    """vault_ref_sync: stamp at reference time (unlocked-only by construction),
    release only when the ground-truth scan says no referrer remains."""

    def test_stamp_on_new_reference(self, vault):
        from core import prompt_crud
        pv.setup("key")
        pv.set_monolith("vlt_ref", "text")
        prompt_crud.vault_ref_sync("vlt_ref", None)
        assert "vlt_ref" in pv.refs_names()

    def test_stamp_noop_for_non_vault_name(self, vault):
        from core import prompt_crud
        pv.setup("key")
        prompt_crud.vault_ref_sync("plain-user-prompt", None)
        assert pv.refs_names() == {}

    def test_release_when_last_referrer_gone(self, vault, monkeypatch):
        from core import prompt_crud
        pv.setup("key")
        pv.set_monolith("vlt_ref", "text")
        prompt_crud.vault_ref_sync("vlt_ref", None)
        monkeypatch.setattr(prompt_crud, "_vault_name_still_referenced",
                            lambda n: False)
        prompt_crud.vault_ref_sync(None, "vlt_ref")
        assert pv.refs_names() == {}

    def test_release_skipped_while_still_referenced(self, vault, monkeypatch):
        from core import prompt_crud
        pv.setup("key")
        pv.set_monolith("vlt_ref", "text")
        prompt_crud.vault_ref_sync("vlt_ref", None)
        monkeypatch.setattr(prompt_crud, "_vault_name_still_referenced",
                            lambda n: True)
        prompt_crud.vault_ref_sync(None, "vlt_ref")
        assert "vlt_ref" in pv.refs_names()

    def test_release_works_while_locked(self, vault, monkeypatch):
        """Drop-only while sealed — the one locked-state mutation allowed."""
        from core import prompt_crud
        pv.setup("key")
        pv.set_monolith("vlt_ref", "text")
        prompt_crud.vault_ref_sync("vlt_ref", None)
        pv.lock()
        monkeypatch.setattr(prompt_crud, "_vault_name_still_referenced",
                            lambda n: False)
        prompt_crud.vault_ref_sync(None, "vlt_ref")
        assert pv.refs_names() == {}

    def test_swap_stamps_new_releases_old(self, vault, monkeypatch):
        from core import prompt_crud
        pv.setup("key")
        pv.set_monolith("vlt_a", "a")
        pv.set_monolith("vlt_b", "b")
        prompt_crud.vault_ref_sync("vlt_a", None)
        monkeypatch.setattr(prompt_crud, "_vault_name_still_referenced",
                            lambda n: False)
        prompt_crud.vault_ref_sync("vlt_b", "vlt_a")
        assert set(pv.refs_names()) == {"vlt_b"}

    def test_scan_fail_safe_without_system(self, vault, monkeypatch):
        """Unreadable referrer source → treated as still-referenced (a stale
        index name is a smaller sin than dropping a live reference)."""
        from core import prompt_crud
        import core.api_fastapi as af
        monkeypatch.setattr(af, "get_system",
                            lambda: (_ for _ in ()).throw(RuntimeError("no system")))
        assert prompt_crud._vault_name_still_referenced("anything") is True


class TestReferrerHooks:
    """The three referrer classes call vault_ref_sync on their write paths."""

    def test_scheduler_task_lifecycle_syncs(self, vault, monkeypatch):
        import threading
        from core import prompt_crud
        from core.continuity.scheduler import ContinuityScheduler
        calls = []
        monkeypatch.setattr(prompt_crud, "vault_ref_sync",
                            lambda n, o: calls.append((n, o)))
        s = ContinuityScheduler.__new__(ContinuityScheduler)
        s._lock = threading.RLock()
        s._tasks = {}
        s._task_pending = {}
        s._task_running = {}
        s._task_last_matched = {}
        s._task_progress = {}
        s._save_tasks = lambda: None
        t = s.create_task({"prompt": "vlt_task"})
        assert ("vlt_task", None) in calls
        s.update_task(t["id"], {"prompt": "vlt_other"})
        assert ("vlt_other", "vlt_task") in calls
        s.delete_task(t["id"])
        assert (None, "vlt_other") in calls

    def test_persona_lifecycle_syncs(self, vault, monkeypatch):
        import threading
        from core import prompt_crud
        from core.personas.persona_manager import PersonaManager
        calls = []
        monkeypatch.setattr(prompt_crud, "vault_ref_sync",
                            lambda n, o: calls.append((n, o)))
        pm = PersonaManager.__new__(PersonaManager)
        pm._lock = threading.Lock()
        pm._personas = {}
        pm._save_to_user = lambda: True
        assert pm.create("vlt-tester", {"settings": {"prompt": "vlt_p"}})
        assert ("vlt_p", None) in calls
        assert pm.update("vlt-tester", {"settings": {"prompt": "vlt_other"}})
        assert ("vlt_other", "vlt_p") in calls
        assert pm.delete("vlt-tester")
        assert (None, "vlt_other") in calls


class TestRouteStatusMapping:
    """The web route maps VAULT_LOCKED_MSG → 409 (not the generic 500/400)."""

    def test_component_put_locked_vault_is_409(self, vault, monkeypatch):
        import asyncio
        from fastapi import HTTPException
        import core.api_fastapi  # noqa: F401  (route modules trip circular imports alone)
        from core.routes.content import save_prompt_component

        class _Req:
            headers = {}
            async def json(self):
                return {"value": "decrypted text", "origin": "vault"}

        pv.setup("key")
        pv.lock()
        with pytest.raises(HTTPException) as ei:
            asyncio.run(save_prompt_component("character", "vlt_stale", _Req(), None))
        assert ei.value.status_code == 409
