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
