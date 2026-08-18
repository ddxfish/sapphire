"""[REGRESSION_GUARD] Cleanup primitives (session 1): piece usage index,
piece trash store (soft delete + restore + rollback), vault piece-refs
hashes (the salted sidecar that lets a LOCKED-vault cleanup skip pieces a
hidden prompt still references), and the unlock-time trash reconcile.

Hermetic: vault paths ride tmp_path (test_prompt_vault.py pattern), trash
path is monkeypatched to tmp, prompt_manager's components dict and
save_components are stubbed — nothing touches the real user store.
"""
import json
import time

import pytest

import core.prompt_vault as pv
import core.prompt_crud as crud
from core.prompt_manager import prompt_manager


@pytest.fixture
def vault(tmp_path, monkeypatch):
    monkeypatch.setattr(pv, 'VAULT_PATH', tmp_path / 'prompt_vault.enc')
    monkeypatch.setattr(pv, 'REFS_PATH', tmp_path / 'vault_refs.json')
    monkeypatch.setattr(pv, '_N', 2 ** 10)
    monkeypatch.setattr(pv, '_idle_seconds', lambda: 1800.0)
    monkeypatch.setattr(pv, '_publish', lambda action: None)
    monkeypatch.setattr(pv, '_handoff_active', lambda gone: None)
    # Unlock-tail reconcile must not walk the real trash/store in these tests
    monkeypatch.setattr(crud, 'reconcile_trash_with_vault', lambda: 0)
    pv.stop()
    pv._key = pv._salt = pv._data = None
    pv._refs, pv._refs_loaded = {}, False
    pv._piece_salt, pv._piece_hashes = None, set()
    yield pv
    pv.stop()
    pv._key = pv._salt = pv._data = None
    pv._refs, pv._refs_loaded = {}, False
    pv._piece_salt, pv._piece_hashes = None, set()


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Hermetic plaintext store + trash for prompt_crud: real _lock, fake
    components dict, save_components stubbed (recording), trash in tmp."""
    comps = {'extras': {'fear': 'be afraid', 'romantic': 'hearts'},
             'scenario': {'raptors': 'clever girls'}}
    saves = []
    monkeypatch.setattr(prompt_manager, '_components', comps)
    monkeypatch.setattr(prompt_manager, 'save_components',
                        lambda reason=None, audit=True: saves.append(reason) or True)
    monkeypatch.setattr(crud, '_trash_path', lambda: tmp_path / 'trash.json')
    monkeypatch.setattr(crud, '_publish_components_changed', lambda action: None)
    return {'comps': comps, 'saves': saves, 'tmp': tmp_path}


# ── usage index ──

class TestPieceUsage:
    def test_counts_across_presets(self, monkeypatch):
        presets = {'a': {'character': 'x', 'extras': ['e1', 'e2'],
                         '_privacy_required': True},
                   'b': {'character': 'x', 'extras': []},
                   'broken': 'not-a-dict'}
        monkeypatch.setattr(type(prompt_manager), 'scenario_presets',
                            property(lambda self: presets))
        usage = crud.piece_usage()
        assert sorted(usage['character']['x']) == ['a', 'b']
        assert usage['extras']['e1'] == ['a']
        assert '_privacy_required' not in usage
        assert all('broken' not in users
                   for entries in usage.values() for users in entries.values())


# ── trash store ──

class TestTrash:
    def test_trash_moves_piece(self, store):
        trashed, skipped = crud.trash_pieces([('extras', 'fear')])
        assert trashed == [{'type': 'extras', 'key': 'fear'}]
        assert not skipped
        assert 'fear' not in store['comps']['extras']
        entries = crud.list_trash()
        assert entries[0]['key'] == 'fear' and entries[0]['text'] == 'be afraid'
        assert store['saves']  # store persisted

    def test_trash_skips_unknown(self, store):
        trashed, skipped = crud.trash_pieces([('extras', 'ghost')])
        assert not trashed
        assert skipped[0]['why'] == 'not in the plaintext store'

    def test_store_save_failure_rolls_back(self, store, monkeypatch):
        monkeypatch.setattr(prompt_manager, 'save_components',
                            lambda reason=None, audit=True: False)
        trashed, skipped = crud.trash_pieces([('extras', 'fear')])
        assert not trashed
        assert skipped[0]['why'] == 'store save failed'
        assert store['comps']['extras']['fear'] == 'be afraid'  # back in place
        assert crud.list_trash() == []                          # trash reverted

    def test_restore_latest_and_no_overwrite(self, store):
        crud.trash_pieces([('extras', 'fear')])
        # Re-create + re-trash → two entries; restore must pick the newest
        store['comps'].setdefault('extras', {})['fear'] = 'newer text'
        time.sleep(0.01)
        crud.trash_pieces([('extras', 'fear')])
        restored, skipped = crud.restore_pieces([('extras', 'fear')])
        assert restored == [{'type': 'extras', 'key': 'fear'}]
        assert store['comps']['extras']['fear'] == 'newer text'
        assert crud.list_trash() == []  # all entries for the key dropped
        # Live key now exists → second restore skips
        crud.trash_pieces([('scenario', 'raptors')])
        store['comps'].setdefault('scenario', {})['raptors'] = 'live'
        restored, skipped = crud.restore_pieces([('scenario', 'raptors')])
        assert not restored and skipped[0]['why'] == 'live piece exists'

    def test_purge(self, store):
        crud.trash_pieces([('extras', 'fear'), ('extras', 'romantic')])
        assert crud.purge_trash() == 2
        assert crud.list_trash() == []


# ── vault piece-refs hashes ──

class TestVaultPieceRefs:
    def _setup_with_preset(self, vault):
        ok, _ = vault.setup('hunter2')
        assert ok
        # vault-internal piece + a preset referencing one vault piece and
        # two plaintext pieces
        assert vault.set_piece('extras', 'vaultonly', 'sealed text')[0]
        assert vault.set_preset('raptor-story', {
            'character': 'sapph', 'scenario': 'raptors',
            'extras': ['vaultonly', 'fear']})[0]

    def test_hashes_written_and_answering(self, vault):
        self._setup_with_preset(vault)
        data = json.loads(vault.REFS_PATH.read_text(encoding='utf-8'))
        assert data['version'] == 2 and data['piece_salt']
        assert vault.piece_refs_available()
        assert vault.piece_vault_referenced('scenario', 'raptors') is True
        assert vault.piece_vault_referenced('character', 'sapph') is True
        assert vault.piece_vault_referenced('extras', 'fear') is True
        # vault-internal piece is NOT a plaintext ref
        assert vault.piece_vault_referenced('extras', 'vaultonly') is False
        assert vault.piece_vault_referenced('extras', 'romantic') is False

    def test_answers_while_locked(self, vault):
        self._setup_with_preset(vault)
        vault.lock(reason='test')
        assert vault.piece_vault_referenced('scenario', 'raptors') is True
        assert vault.piece_vault_referenced('extras', 'romantic') is False

    def test_tristate_none_without_data(self, vault):
        # v1 sidecar (names only, no piece_salt) → no answer
        vault.REFS_PATH.write_text(json.dumps({"version": 1, "names": {}}),
                                   encoding='utf-8')
        assert vault.piece_refs_available() is False
        assert vault.piece_vault_referenced('extras', 'fear') is None

    def test_unlock_regenerates_sidecar(self, vault):
        self._setup_with_preset(vault)
        vault.lock(reason='test')
        vault.REFS_PATH.unlink()          # simulate pre-index install
        vault._refs_loaded = False
        vault._piece_salt, vault._piece_hashes = None, set()
        ok, _ = vault.unlock('hunter2')
        assert ok
        assert vault.piece_refs_available()
        assert vault.piece_vault_referenced('extras', 'fear') is True

    def test_piece_ref_pairs_unlocked_only(self, vault):
        self._setup_with_preset(vault)
        pairs = vault.piece_ref_pairs()
        assert ('extras', 'fear') in pairs and ('scenario', 'raptors') in pairs
        assert ('extras', 'vaultonly') not in pairs
        vault.lock(reason='test')
        assert vault.piece_ref_pairs() is None


# ── unlock-time trash reconcile ──

class TestReconcile:
    def test_restores_vault_referenced_trash(self, store, monkeypatch):
        crud.trash_pieces([('extras', 'fear'), ('extras', 'romantic')])
        monkeypatch.setattr(pv, 'piece_ref_pairs',
                            lambda: [('extras', 'fear')])
        assert crud.reconcile_trash_with_vault() == 1
        assert store['comps']['extras']['fear'] == 'be afraid'
        assert [it['key'] for it in crud.list_trash()] == ['romantic']

    def test_noop_when_locked_or_empty(self, store, monkeypatch):
        monkeypatch.setattr(pv, 'piece_ref_pairs', lambda: None)
        assert crud.reconcile_trash_with_vault() == 0
