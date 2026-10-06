"""[REGRESSION_GUARD] Encryption in core backups (2026-10-06).

Pins:
- BACKUPS_ENCRYPT_LOCAL off (default) -> plain .tar.gz, exactly as before.
- BACKUPS_ENCRYPT_LOCAL on -> a sealed .sapphirebak, the plaintext tar is gone,
  and it decrypts back to a valid tar with the password.
- Encryption on + NO password -> NO backup at all (never a plain one), the
  reason in last_backup_error, a sapphire_health_alert published.
- export_encrypted (the gate every shipper runs): refuses without a password,
  under a corruption sentinel, over the cap (before building anything), and
  produces ciphertext only; a sabotaged encrypt_file is caught by the verify.
- sealed(): hands back the file itself when already encrypted, a sealed
  temp copy otherwise, and refuses without a password.
- BACKUPS_DIR: blank = user_backups/, relative under base_dir, inside user/
  refused (fallback + reason), unwritable refused.
- select_doomed: pure tier rotation, keep<=0 pauses, strangers untouched.
"""
import tarfile
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from core import backup_crypto


@pytest.fixture
def mgr(tmp_path, monkeypatch):
    import core.backup as B
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
        BACKUPS_KEEP_WEEKLY = 0
        BACKUPS_KEEP_MONTHLY = 0
        BACKUPS_HOUR = 3
        BACKUPS_ENCRYPT_LOCAL = False
        BACKUPS_DIR = ""
    monkeypatch.setattr(B, "config", Cfg)
    b.cfg = Cfg
    alerts = []
    monkeypatch.setattr(b, "_alert", lambda kind, **d: alerts.append((kind, d)))
    b.alerts = alerts
    return b


def _pw(monkeypatch, mgr, value):
    monkeypatch.setattr(mgr, "_backup_password", lambda: value)


# --- create_backup -----------------------------------------------------------

def test_default_is_plain_tar(mgr, monkeypatch):
    _pw(monkeypatch, mgr, "")
    fn = mgr.create_backup("manual")
    assert fn and fn.endswith(".tar.gz")
    with tarfile.open(mgr.backup_dir / fn) as t:
        assert "user/notes.txt" in t.getnames()


def test_encrypt_on_seals_and_drops_plaintext(mgr, monkeypatch, tmp_path):
    mgr.cfg.BACKUPS_ENCRYPT_LOCAL = True
    _pw(monkeypatch, mgr, "hunter2")
    fn = mgr.create_backup("daily")
    assert fn and fn.endswith(".sapphirebak")
    sealed = mgr.backup_dir / fn
    assert backup_crypto.is_encrypted_backup(sealed)
    assert not list(mgr.backup_dir.glob("*.tar.gz")), "plaintext must not outlive the seal"
    assert not list(mgr.backup_dir.glob("*.partial"))
    out = tmp_path / "back.tar.gz"
    backup_crypto.decrypt_file(sealed, out, "hunter2")
    with tarfile.open(out) as t:
        assert "user/notes.txt" in t.getnames()
    listed = mgr.list_backups()["daily"]
    assert listed and listed[0]["encrypted"] is True


def test_encrypt_on_without_password_makes_nothing(mgr, monkeypatch):
    mgr.cfg.BACKUPS_ENCRYPT_LOCAL = True
    _pw(monkeypatch, mgr, "")
    assert mgr.create_backup("daily") is None
    assert "password" in (mgr.last_backup_error or "").lower()
    assert not list(mgr.backup_dir.glob("sapphire_*")), "never a plaintext stand-in"
    assert mgr.alerts and mgr.alerts[0][0] == "backup_password_missing"


def test_encrypt_param_overrides_setting(mgr, monkeypatch):
    _pw(monkeypatch, mgr, "pw")
    fn = mgr.create_backup("manual", encrypt=True)
    assert fn.endswith(".sapphirebak")


def test_sabotaged_encrypt_leaves_no_plaintext(mgr, monkeypatch):
    """If encrypt_file silently passes plaintext through, the verify inside
    seal must catch it AND the finished plain tar must not be left standing."""
    import shutil
    mgr.cfg.BACKUPS_ENCRYPT_LOCAL = True
    _pw(monkeypatch, mgr, "pw")
    monkeypatch.setattr(backup_crypto, "encrypt_file", lambda s, d, p: shutil.copy2(s, d))
    assert mgr.create_backup("daily") is None
    assert "plaintext" in (mgr.last_backup_error or "").lower()
    assert not list(mgr.backup_dir.glob("sapphire_*"))


def test_openers_written_beside_local_backups(mgr, monkeypatch):
    _pw(monkeypatch, mgr, "")
    mgr.create_backup("manual")
    names = {p.name for p in mgr.backup_dir.iterdir()}
    assert {"README.txt", "open-backup.sh", "open-backup.bat", "decrypt_backup.py"} <= names
    bat = (mgr.backup_dir / "open-backup.bat").read_bytes()
    assert b"\r\n" in bat and all(b < 128 for b in bat)


# --- export_encrypted (the shipper gate) -------------------------------------

def test_export_refuses_without_password(mgr, monkeypatch, tmp_path):
    from core.backup import BackupRefused
    _pw(monkeypatch, mgr, "")
    with pytest.raises(BackupRefused, match="password"):
        mgr.export_encrypted(dest_dir=tmp_path / "x")


def test_export_halts_on_sentinel(mgr, monkeypatch, tmp_path):
    from core.backup import BackupRefused
    _pw(monkeypatch, mgr, "pw")
    (mgr.user_dir / "health").mkdir()
    (mgr.user_dir / "health" / "CORRUPT_main_x.flag").write_text("x")
    with pytest.raises(BackupRefused, match="sentinel"):
        mgr.export_encrypted(dest_dir=tmp_path / "x")


def test_export_cap_refuses_before_building(mgr, monkeypatch, tmp_path):
    from core.backup import BackupRefused
    _pw(monkeypatch, mgr, "pw")
    monkeypatch.setattr(mgr, "estimate_size", lambda **k: {"total_bytes": 50 * 1024 * 1024})
    called = []
    monkeypatch.setattr(mgr, "create_backup", lambda *a, **k: called.append(1))
    with pytest.raises(BackupRefused, match="cap"):
        mgr.export_encrypted(dest_dir=tmp_path / "x", cap_mb=1)
    assert called == []


def test_export_produces_only_ciphertext(mgr, monkeypatch, tmp_path):
    _pw(monkeypatch, mgr, "pw")
    dest = tmp_path / "x"
    p = mgr.export_encrypted(dest_dir=dest, cap_mb=2048)
    assert p.parent == dest and p.suffix == ".sapphirebak"
    assert backup_crypto.verify_ciphertext(p) is None
    assert [q.name for q in dest.iterdir()] == [p.name]


# --- sealed() ----------------------------------------------------------------

def test_sealed_returns_self_when_already_encrypted(mgr, monkeypatch):
    _pw(monkeypatch, mgr, "pw")
    fn = mgr.create_backup("daily", encrypt=True)
    path, cleanup = mgr.sealed(fn)
    assert path == mgr.backup_dir / fn
    cleanup()
    assert path.exists()


def test_sealed_copies_plain_into_temp_and_cleans(mgr, monkeypatch):
    _pw(monkeypatch, mgr, "pw")
    fn = mgr.create_backup("daily")
    path, cleanup = mgr.sealed(fn)
    assert path.parent.parent == mgr.backup_dir and path.parent.name.startswith("ship_")
    assert backup_crypto.is_encrypted_backup(path)
    assert fn not in [b["filename"] for b in mgr.list_backups()["daily"] if b["filename"] == path.name]
    cleanup()
    assert not path.parent.exists()
    assert (mgr.backup_dir / fn).exists()


def test_sealed_refuses_without_password(mgr, monkeypatch):
    from core.backup import BackupRefused
    _pw(monkeypatch, mgr, "")
    fn = mgr.create_backup("daily")
    with pytest.raises(BackupRefused, match="password"):
        mgr.sealed(fn)
    assert not list(mgr.backup_dir.glob("ship_*"))


# --- BACKUPS_DIR -------------------------------------------------------------

def test_dir_blank_is_default(mgr):
    assert mgr.backup_dir == mgr.base_dir / "user_backups"
    assert mgr.backup_dir_error is None


def test_dir_relative_under_base(mgr):
    mgr.cfg.BACKUPS_DIR = "elsewhere/bk"
    assert mgr.backup_dir == mgr.base_dir / "elsewhere" / "bk"
    assert mgr.backup_dir.is_dir()


def test_dir_absolute(mgr, tmp_path):
    mgr.cfg.BACKUPS_DIR = str(tmp_path / "nas")
    assert mgr.backup_dir == tmp_path / "nas"


def test_dir_inside_user_refused(mgr):
    mgr.cfg.BACKUPS_DIR = str(mgr.user_dir / "backups")
    assert mgr.backup_dir == mgr.base_dir / "user_backups"
    assert "inside user/" in mgr.backup_dir_error
    assert mgr.health_summary()["backup_dir_error"]


def test_dir_unwritable_falls_back(mgr, tmp_path):
    blocked = tmp_path / "ro"
    blocked.mkdir()
    blocked.chmod(0o500)
    try:
        import os
        if os.access(blocked, os.W_OK):
            pytest.skip("running as root; cannot make an unwritable dir")
        mgr.cfg.BACKUPS_DIR = str(blocked / "sub")
        assert mgr.backup_dir == mgr.base_dir / "user_backups"
        assert "unusable" in mgr.backup_dir_error
    finally:
        blocked.chmod(0o700)


def test_dir_setter_overrides(mgr, tmp_path):
    mgr.backup_dir = tmp_path / "forced"
    mgr.cfg.BACKUPS_DIR = str(tmp_path / "ignored")
    assert mgr.backup_dir == tmp_path / "forced"


# --- select_doomed -----------------------------------------------------------

def test_select_doomed_pure_rotation():
    from core.backup import select_doomed
    names = [f"sapphire_2026-10-0{i}_030000_daily.sapphirebak" for i in range(1, 6)]
    names += ["sapphire_2026-09-28_030000_weekly.tar.gz", "README.txt", "open-backup.sh",
              "sapphire_2026-10-01_120000_pre_update.tar.gz"]
    doomed = select_doomed(names, {"daily": 3, "weekly": 0, "manual": 3})
    assert sorted(doomed) == ["sapphire_2026-10-01_030000_daily.sapphirebak",
                              "sapphire_2026-10-02_030000_daily.sapphirebak"]
    assert select_doomed(names, {"daily": 0}) == []


def test_rotate_backups_uses_select_doomed(mgr, monkeypatch):
    _pw(monkeypatch, mgr, "")
    mgr.cfg.BACKUPS_KEEP_DAILY = 2
    for d in ("01", "02", "03"):
        (mgr.backup_dir / f"sapphire_2026-10-{d}_030000_daily.tar.gz").write_bytes(b"x")
    assert mgr.rotate_backups() == 1
    assert not (mgr.backup_dir / "sapphire_2026-10-01_030000_daily.tar.gz").exists()
