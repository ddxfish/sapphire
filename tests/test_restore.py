"""[REGRESSION_GUARD] In-app restore (Remembrance Stage 3).

The most destructive op in the app — it swaps the live user/ dir. These lock in:
extract-before-swap, the user.old/.prev rollback rotation, trusted symlink
restore, the result-file feedback, and the never-touch-user/-on-failure invariant.
"""
import os
import tarfile

import pytest

from core import restore as R


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Point restore's module-level paths at an isolated temp tree."""
    ur = tmp_path / "user_restore"
    monkeypatch.setattr(R, "BASE", tmp_path)
    monkeypatch.setattr(R, "RESTORE_DIR", ur)
    monkeypatch.setattr(R, "MARKER", ur / "pending_restore.json")
    monkeypatch.setattr(R, "STAGED", ur / "pending.tar.gz")
    monkeypatch.setattr(R, "RESULT", ur / "last_restore_result.json")
    monkeypatch.setattr(R, "USER", tmp_path / "user")
    monkeypatch.setattr(R, "USER_NEW", tmp_path / "user.new")
    monkeypatch.setattr(R, "USER_OLD", tmp_path / "user.old")
    monkeypatch.setattr(R, "USER_OLD_PREV", tmp_path / "user.old.prev")
    return tmp_path


def _backup(d, name, content, symlink=None):
    su = d / ("src_" + name) / "user"
    su.mkdir(parents=True)
    (su / "f.txt").write_text(content)
    if symlink:
        (su / "plugins").mkdir()
        os.symlink(symlink, su / "plugins" / "drives")
    p = d / (name + ".tar.gz")
    with tarfile.open(p, "w:gz") as t:
        t.add(su, arcname="user")
    return p


def test_validate_accepts_user_root(env):
    assert "user" in R.validate_tar(_backup(env, "v", "x"))


def test_validate_rejects_no_user_root(env):
    su = env / "sx" / "notuser"
    su.mkdir(parents=True)
    (su / "a").write_text("x")
    p = env / "bad.tar.gz"
    with tarfile.open(p, "w:gz") as t:
        t.add(su, arcname="notuser")
    with pytest.raises(ValueError):
        R.validate_tar(p)


def test_full_swap_and_result(env):
    R.USER.mkdir()
    (R.USER / "f.txt").write_text("OLD")
    R.request_restore(_backup(env, "s", "NEW"), source="backup:test", trusted=True)
    assert R.apply_pending_restore(lambda m: None) is True
    assert (R.USER / "f.txt").read_text() == "NEW"
    assert (R.USER_OLD / "f.txt").read_text() == "OLD"   # rollback kept
    assert not R.MARKER.exists()                          # consumed
    res = R.read_restore_result()
    assert res["ok"] is True and "test" in res["source"]


def test_rollback_rotation_keeps_original(env):
    R.USER.mkdir()
    (R.USER / "f.txt").write_text("GOOD")
    R.request_restore(_backup(env, "b1", "BAD1"), trusted=True)
    assert R.apply_pending_restore(lambda m: None)
    R.request_restore(_backup(env, "b2", "BAD2"), trusted=True)
    assert R.apply_pending_restore(lambda m: None)
    assert (R.USER / "f.txt").read_text() == "BAD2"
    assert (R.USER_OLD / "f.txt").read_text() == "BAD1"
    assert (R.USER_OLD_PREV / "f.txt").read_text() == "GOOD"  # original survives a double-restore


def test_restore_skips_symlinks(env):
    """Symlinks are skipped on restore (path leak / needs admin on Windows / not
    data) — real files still restore. War-campaign fix E."""
    R.USER.mkdir()
    R.request_restore(_backup(env, "sym", "x", symlink="/mnt/drive"), trusted=True)
    assert R.apply_pending_restore(lambda m: None) is True
    assert (R.USER / "f.txt").read_text() == "x"          # real file restored
    assert not (R.USER / "plugins" / "drives").exists()    # symlink skipped


def test_failure_leaves_user_untouched(env):
    R.USER.mkdir()
    (R.USER / "f.txt").write_text("SAFE")
    R.request_restore(_backup(env, "f", "x"), trusted=True)
    R.STAGED.unlink()                                      # force the failure path
    assert R.apply_pending_restore(lambda m: None) is False
    assert (R.USER / "f.txt").read_text() == "SAFE"        # never touched
    assert R.read_restore_result()["ok"] is False
