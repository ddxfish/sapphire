"""A locked or busy memory DB at boot is not a corrupt one.

Before 2026-10-16 both boot integrity checks caught sqlite3.DatabaseError,
whose subclass OperationalError is 'database is locked' (a twin instance, a
backup still reading). The live DB was renamed aside and her memory came up
empty. Now: locked means leave it alone and retry on the next call; real
corruption still goes to repair."""
import sqlite3

import plugins.memory.tools.memory_tools as mt
import plugins.mindpalace.tools.palace_tools as pt


def _valid_db(tmp_path, name):
    p = tmp_path / name
    conn = sqlite3.connect(p)
    conn.execute('create table t(x)')
    conn.commit()
    conn.close()
    return p


def _raising(exc):
    def connect(*a, **k):
        raise exc
    return connect


def test_memory_locked_is_left_alone(tmp_path, monkeypatch):
    db = _valid_db(tmp_path, 'memory.db')
    touched = []
    monkeypatch.setattr(mt, '_get_db_path', lambda: db)
    monkeypatch.setattr(mt, '_repair_db', lambda p: touched.append(p))
    monkeypatch.setattr(mt, '_db_initialized', False)
    monkeypatch.setattr(sqlite3, 'connect', _raising(sqlite3.OperationalError('database is locked')))
    assert mt._ensure_db() is False
    assert touched == [] and db.exists()
    assert mt._db_initialized is False          # the next call tries again


def test_memory_corruption_still_goes_to_repair(tmp_path, monkeypatch):
    db = _valid_db(tmp_path, 'memory.db')
    touched = []
    monkeypatch.setattr(mt, '_get_db_path', lambda: db)
    monkeypatch.setattr(mt, '_repair_db', lambda p: touched.append(p))
    monkeypatch.setattr(mt, '_db_initialized', False)
    monkeypatch.setattr(sqlite3, 'connect', _raising(sqlite3.DatabaseError('file is not a database')))
    try:
        mt._ensure_db()
    except Exception:
        pass
    assert touched == [db]


def test_mindpalace_locked_is_left_alone(tmp_path, monkeypatch):
    db = _valid_db(tmp_path, 'mindpalace.db')
    touched = []
    monkeypatch.setattr(pt, '_get_db_path', lambda: db)
    monkeypatch.setattr(pt, '_safe_rename_corrupted', lambda p: touched.append(p) or True)
    monkeypatch.setattr(pt, '_db_initialized', False)
    monkeypatch.setattr(sqlite3, 'connect', _raising(sqlite3.OperationalError('database is locked')))
    assert pt._ensure_db() is False
    assert touched == [] and db.exists()
    assert pt._db_initialized is False


def test_mindpalace_corruption_still_renames(tmp_path, monkeypatch):
    db = _valid_db(tmp_path, 'mindpalace.db')
    touched = []
    monkeypatch.setattr(pt, '_get_db_path', lambda: db)
    monkeypatch.setattr(pt, '_safe_rename_corrupted', lambda p: touched.append(p) or True)
    monkeypatch.setattr(pt, '_db_initialized', False)
    monkeypatch.setattr(sqlite3, 'connect', _raising(sqlite3.DatabaseError('database disk image is malformed')))
    try:
        pt._ensure_db()
    except Exception:
        pass
    assert touched == [db]


def test_locked_is_an_operational_error():
    """The hierarchy this fix rests on."""
    assert issubclass(sqlite3.OperationalError, sqlite3.DatabaseError)
