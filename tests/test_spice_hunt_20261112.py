"""Spice fixes from the 2026-11-12 hunt."""
import json
import pytest
from fastapi import HTTPException


def _manager(tmp_path, text=None):
    from core.spice_sets.spice_set_manager import SpiceSetManager
    m = SpiceSetManager.__new__(SpiceSetManager)
    import threading
    m.BASE_DIR, m.USER_DIR = tmp_path / 'core', tmp_path / 'user'
    m.BASE_DIR.mkdir(); m.USER_DIR.mkdir()
    m._sets, m._active_name, m.corrupt_error = {}, 'default', None
    m._lock, m._last_mtimes = threading.Lock(), {}
    if text is not None:
        (m.USER_DIR / 'spice_sets.json').write_text(text, encoding='utf-8')
    m._load()
    return m


def test_an_unreadable_sets_file_is_never_written_over(tmp_path):
    good = json.dumps({"cozy": {"categories": ["a"]}, "wild": {"categories": ["b"]}})
    m = _manager(tmp_path, good)
    f = m.USER_DIR / 'spice_sets.json'
    f.write_text(good[:-1], encoding='utf-8')            # truncated mid-write
    m.reload()
    assert m.corrupt_error and set(m.get_set_names()) == {'cozy', 'wild'}   # kept
    assert m.save_set('new', ['a']) is False
    assert m.delete_set('cozy') is False
    assert f.read_text(encoding='utf-8') == good[:-1]
    f.write_text(good, encoding='utf-8')
    m.reload()
    assert m.corrupt_error is None and m.save_set('new', ['a']) is True


def test_a_renamed_category_stays_in_its_sets(tmp_path):
    m = _manager(tmp_path, json.dumps({"cozy": {"categories": ["a", "b"]}}))
    assert m.rename_category('a', 'z') is True
    assert m.get_categories('cozy') == ['z', 'b']
    assert json.loads((m.USER_DIR / 'spice_sets.json').read_text())['cozy']['categories'] == ['z', 'b']


def test_names_that_would_vanish_on_reload_are_refused():
    import core.api_fastapi  # noqa: F401 - routes import through the app
    from core.routes.content import _spice_name
    assert _spice_name('  mood ') == 'mood'
    for bad in ('_hidden', '', None, '   '):
        with pytest.raises(HTTPException):
            _spice_name(bad)


def test_a_refused_spice_save_is_not_a_success(monkeypatch):
    import core.api_fastapi  # noqa: F401
    from core.routes import content
    monkeypatch.setattr(content.prompts.prompt_manager, 'save_spices', lambda: False)
    with pytest.raises(HTTPException) as e:
        content._save_spices()
    assert e.value.status_code == 409
