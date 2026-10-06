"""[REGRESSION_GUARD] core/backup_targets.py — the never-plaintext-off-box
gates, with a fake target (2026-10-06).

- A remote target NEVER receives a .tar.gz, whatever its encrypt flag says.
- A .sapphirebak that does not carry the magic is refused (GATE 2).
- A non-remote target with encryption off receives the plain tar; with
  encryption on it receives a sealed copy.
- Shipping seals ONCE for all targets and cleans the temp copy after.
- Rotation on the target follows select_doomed with the target's keep.
- Openers ride along; strangers on the target are never deleted.
- pick_newest: monthly > weekly > daily.
- ship() with no targets is a no-op; no password + encrypting target refuses.
"""
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from core import backup_crypto


@pytest.fixture
def mgr(tmp_path, monkeypatch):
    import core.backup as B
    import core.backup_targets as T
    from core.backup import Backup
    with patch.object(Backup, '__init__', lambda self: None):
        b = Backup()
    b.base_dir = tmp_path
    b.user_dir = tmp_path / "user"
    b._backup_dir_override = None
    b.backup_dir_error = None
    b.user_dir.mkdir(parents=True)
    (b.user_dir / "notes.txt").write_text("hello", encoding="utf-8")
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "decrypt_backup.py").write_text("# stub\n", encoding="utf-8")
    b._stop_event = None
    b._backup_op_lock = threading.Lock()

    class Cfg:
        BASE_DIR = tmp_path
        BACKUPS_ENABLED = True
        BACKUPS_EXCLUDE_PATTERNS = []
        BACKUPS_KEEP_DAILY = 7
        BACKUPS_KEEP_WEEKLY = 4
        BACKUPS_KEEP_MONTHLY = 3
        BACKUPS_KEEP_MANUAL = 5
        BACKUPS_KEEP_UPDATE = 3
        BACKUPS_HOUR = 3
        BACKUPS_ENCRYPT_LOCAL = False
        BACKUPS_DIR = ""
    monkeypatch.setattr(B, "config", Cfg)
    monkeypatch.setattr(T, "backup_manager", b)
    monkeypatch.setattr(T, "_providers", [])
    monkeypatch.setattr(b, "_backup_password", lambda: "pw")
    monkeypatch.setattr(b, "_alert", lambda kind, **d: None)
    b.cfg = Cfg
    return b


class Fake:
    """A target that is just a dict of name -> bytes."""
    from core.backup_targets import Target as _T

    def __init__(self, remote=True, encrypt=True, keep=None, label="fake"):
        self.remote = remote
        self.encrypt = encrypt
        self.label = label
        self.kind = "remote-fake" if remote else "path-fake"
        self.keep = keep or {"daily": 7, "weekly": 4, "monthly": 3, "manual": 3}
        self.store = {}
        self.puts = []

    wants_encryption = _T.wants_encryption
    check = _T.check
    put_openers = _T.put_openers

    def put(self, path, name):
        self.check(path, name)        # GATE 3 as a real driver would
        self.store[name] = Path(path).read_bytes()
        self.puts.append(name)

    def names(self):
        return list(self.store)

    def sizes(self):
        return {n: len(b) for n, b in self.store.items()}

    def delete(self, name):
        self.store.pop(name, None)


def _plain(mgr, tier="daily"):
    mgr.cfg.BACKUPS_ENCRYPT_LOCAL = False
    return mgr.create_backup(tier)


# --- the gates ---------------------------------------------------------------

def test_remote_target_never_gets_plaintext(mgr, tmp_path):
    from core.backup import BackupRefused
    t = Fake(remote=True, encrypt=False)      # encrypt=False must be IGNORED
    assert t.wants_encryption is True
    plain = mgr.backup_dir / _plain(mgr)
    with pytest.raises(BackupRefused, match="plaintext"):
        t.check(plain, plain.name)
    with pytest.raises(BackupRefused):
        t.put(plain, plain.name)
    assert t.store == {}


def test_fake_magic_is_refused(mgr, tmp_path):
    from core.backup import BackupRefused
    t = Fake(remote=True)
    bogus = tmp_path / "sapphire_2026-10-06_030000_daily.sapphirebak"
    bogus.write_bytes(b"this is not ciphertext")
    with pytest.raises(BackupRefused, match="not ciphertext"):
        t.check(bogus, bogus.name)


def test_path_target_encrypt_off_gets_plain(mgr):
    from core import backup_targets as T
    t = Fake(remote=False, encrypt=False)
    fn = _plain(mgr)
    res = T.ship(fn, to=[t])
    assert res and res[0]["ok"], res
    assert fn in t.store and t.store[fn] == (mgr.backup_dir / fn).read_bytes()
    assert not list(mgr.backup_dir.glob("ship_*")), "no seal was needed"


def test_path_target_encrypt_on_gets_sealed(mgr):
    from core import backup_targets as T
    t = Fake(remote=False, encrypt=True)
    fn = _plain(mgr)
    res = T.ship(fn, to=[t])
    assert res[0]["ok"], res
    sealed = fn.removesuffix(".tar.gz") + ".sapphirebak"
    assert sealed in t.store and fn not in t.store
    assert t.store[sealed][:12] == backup_crypto.MAGIC


def test_ship_seals_once_and_cleans(mgr, monkeypatch):
    from core import backup_targets as T
    seals = []
    real_seal = backup_crypto.seal
    monkeypatch.setattr(backup_crypto, "seal", lambda s, d, p: (seals.append(1), real_seal(s, d, p))[1])
    a, b = Fake(remote=True, label="a"), Fake(remote=True, label="b")
    fn = _plain(mgr)
    res = T.ship(fn, to=[a, b])
    assert all(r["ok"] for r in res), res
    assert seals == [1]
    assert set(a.store) == set(b.store)
    assert not list(mgr.backup_dir.glob("ship_*"))
    assert T.last_ship["filename"] == fn and len(T.last_ship["results"]) == 2


def test_ship_refuses_without_password_for_encrypting_target(mgr, monkeypatch):
    from core import backup_targets as T
    from core.backup import BackupRefused
    monkeypatch.setattr(mgr, "_backup_password", lambda: "")
    t = Fake(remote=True)
    fn = _plain(mgr)
    with pytest.raises(BackupRefused, match="password"):
        T.ship(fn, to=[t])
    assert t.store == {}


def test_already_encrypted_local_ships_as_is(mgr):
    from core import backup_targets as T
    mgr.cfg.BACKUPS_ENCRYPT_LOCAL = True
    fn = mgr.create_backup("daily")
    t = Fake(remote=True)
    res = T.ship(fn, to=[t])
    assert res[0]["ok"] and fn in t.store
    assert t.store[fn] == (mgr.backup_dir / fn).read_bytes()


# --- rotation + openers on the target ----------------------------------------

def test_target_rotation_and_strangers(mgr):
    from core import backup_targets as T
    t = Fake(remote=True, keep={"daily": 2, "weekly": 4, "monthly": 3, "manual": 3})
    for d in ("01", "02", "03"):
        t.store[f"sapphire_2026-10-{d}_030000_daily.sapphirebak"] = b"old"
    t.store["family-photos.zip"] = b"mine"
    fn = _plain(mgr)
    res = T.ship(fn, to=[t])
    assert res[0]["ok"], res
    dailies = sorted(n for n in t.store if "_daily." in n)
    assert len(dailies) == 2
    assert "sapphire_2026-10-03_030000_daily.sapphirebak" in dailies      # newest old one kept
    assert "sapphire_2026-10-01_030000_daily.sapphirebak" not in t.store  # oldest rotated out
    assert "family-photos.zip" in t.store
    assert {"README.txt", "open-backup.sh", "open-backup.bat", "decrypt_backup.py"} <= set(t.store)


def test_openers_not_re_put_when_present(mgr):
    from core import backup_targets as T
    t = Fake(remote=True)
    t.store["README.txt"] = b"custom"
    T.ship(_plain(mgr), to=[t])
    assert t.store["README.txt"] == b"custom"
    assert t.puts.count("README.txt") == 0


def test_same_name_not_sent_twice(mgr):
    from core import backup_targets as T
    t = Fake(remote=True)
    fn = _plain(mgr)
    T.ship(fn, to=[t])
    n = len(t.puts)
    r = T.ship(fn, to=[t])
    assert "already there" in r[0]["msg"]
    assert len(t.puts) == n


# --- selection + providers ---------------------------------------------------

def test_pick_newest_prefers_highest_tier():
    from core.backup_targets import pick_newest
    files = ["sapphire_2026-10-01_030000_daily.tar.gz",
             "sapphire_2026-10-01_030004_monthly.tar.gz",
             "sapphire_2026-10-01_030002_weekly.tar.gz"]
    assert pick_newest(files).endswith("_monthly.tar.gz")
    assert pick_newest([]) is None
    assert pick_newest([None, ""]) is None


def test_no_targets_is_a_noop(mgr):
    from core import backup_targets as T
    assert T.ship(_plain(mgr)) == []
    assert T.ship_async(["sapphire_2026-10-01_030000_daily.tar.gz"]) is None


def test_provider_failure_is_contained(mgr):
    from core import backup_targets as T
    good = Fake(remote=True)

    def boom():
        raise RuntimeError("driver down")

    T.register_provider(boom)
    T.register_provider(lambda: [good])
    fn = _plain(mgr)
    res = T.ship(fn)
    assert len(res) == 1 and res[0]["ok"]


def test_ship_now_makes_manual_and_ships(mgr):
    from core import backup_targets as T
    t = Fake(remote=True)
    fn, res = T.ship_now(to=[t])
    assert fn.endswith("_manual.tar.gz") and res[0]["ok"]
    assert any(n.endswith("_manual.sapphirebak") for n in t.store)


def test_ship_verifies_size_on_the_target_and_drops_a_short_copy(mgr):
    """The plain check after every ship: the target's own listing must show
    exactly the bytes sent. A target that kept a short copy gets it deleted
    and the ship fails loudly."""
    from core import backup_targets as T

    class Short(Fake):
        def put(self, path, name):
            self.check(path, name)
            self.store[name] = Path(path).read_bytes()[:-1]      # one byte lost
            self.puts.append(name)

    t = Short(remote=True)
    fn = _plain(mgr)
    res = T.ship(fn, to=[t])
    assert not res[0]["ok"] and "bytes" in res[0]["msg"]
    assert not any(n.endswith(".sapphirebak") for n in t.store)
    good = Fake(remote=True)
    res = T.ship(fn, to=[good])
    assert res[0]["ok"] and "verified" in res[0]["msg"]
