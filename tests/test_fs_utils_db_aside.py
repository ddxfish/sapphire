"""core.fs_utils.move_db_aside: a SQLite file moves WITH its sidecars, or not at all."""
from core.fs_utils import move_db_aside


def _family(tmp_path):
    db = tmp_path / "x.db"
    db.write_bytes(b"db")
    (tmp_path / "x.db-wal").write_bytes(b"wal")
    (tmp_path / "x.db-shm").write_bytes(b"shm")
    return db


def test_sidecars_follow(tmp_path):
    db = _family(tmp_path)
    target = tmp_path / "x.db.corrupted"
    assert move_db_aside(db, target) is True
    assert not list(tmp_path.glob("x.db")) and not (tmp_path / "x.db-wal").exists()
    assert target.read_bytes() == b"db"
    assert (tmp_path / "x.db.corrupted-wal").read_bytes() == b"wal"
    assert (tmp_path / "x.db.corrupted-shm").read_bytes() == b"shm"


def test_all_or_nothing(tmp_path):
    db = _family(tmp_path)
    target = tmp_path / "x.db.corrupted"
    blocker = tmp_path / "x.db.corrupted-shm"       # a non-empty folder: the -shm cannot land
    blocker.mkdir()
    (blocker / "f").write_text("x")
    assert move_db_aside(db, target) is False
    assert db.read_bytes() == b"db"
    assert (tmp_path / "x.db-wal").read_bytes() == b"wal"
    assert (tmp_path / "x.db-shm").read_bytes() == b"shm"
    assert not target.exists()
