"""An unreadable credentials file is never written over (hunt 2026-11-12).
It used to be reset to defaults and saved at once: every key gone from the
live file, said only in the log."""
import json
from unittest.mock import patch


def _manager(tmp_path, text):
    f = tmp_path / 'credentials.json'
    f.write_text(text, encoding='utf-8')
    with patch('core.credentials_manager.CREDENTIALS_FILE', f), \
         patch('core.credentials_manager.CONFIG_DIR', tmp_path):
        from core.credentials_manager import CredentialsManager
        yield CredentialsManager(), f


def test_boot_on_an_unreadable_file_leaves_it_alone(tmp_path):
    bad = '{"llm": {"claude": {"api_key": "sk-keep-me",}}}'
    for cm, f in _manager(tmp_path, bad):
        assert cm.corrupt_error and 'credentials.json' in cm.corrupt_error
        assert cm._save() is False
        assert f.read_text(encoding='utf-8') == bad
        assert list(tmp_path.glob('credentials.json.corrupt.*'))
        # the button: start fresh, the old file kept beside it
        assert 'fresh' in cm.start_fresh().lower()
        assert cm.corrupt_error is None
        assert isinstance(json.loads(f.read_text(encoding='utf-8')), dict)


def test_a_reload_of_a_broken_file_keeps_what_was_loaded(tmp_path):
    for cm, f in _manager(tmp_path, '{"llm": {"claude": {"api_key": "sk-good"}}}'):
        assert cm.corrupt_error is None
        f.write_text('{"llm": ', encoding='utf-8')
        cm.reload()
        assert cm.corrupt_error
        assert cm._credentials['llm']['claude']['api_key'] == 'sk-good'
        assert f.read_text(encoding='utf-8') == '{"llm": '


def test_a_bom_does_not_make_the_keys_unreadable(tmp_path):
    for cm, f in _manager(tmp_path, '﻿{"llm": {"claude": {"api_key": "sk-good"}}}'):
        assert cm.corrupt_error is None
        assert cm._credentials['llm']['claude']['api_key'] == 'sk-good'


def test_key_routes_refuse_while_the_file_is_unreadable():
    # "saved" for a key the disk refused: it worked all session and was gone
    # at the next boot (scout hunt 2026-11-13)
    import pytest
    from fastapi import HTTPException
    import core.api_fastapi  # noqa: F401 - the route modules import through it
    import core.routes.settings as rs
    from core.credentials_manager import credentials
    with patch.object(credentials, 'corrupt_error', 'keys unreadable'), \
         patch.object(credentials, 'set_service_api_key') as setter:
        with pytest.raises(HTTPException) as e:
            rs._store_service_key('EMBEDDING_API_KEY', 'sk-x')
        assert e.value.status_code == 409 and e.value.detail == 'keys unreadable'
        setter.assert_not_called()                  # not even in memory
    with pytest.raises(HTTPException) as e:
        rs._key_saved(False)                        # any other failed write
    assert e.value.status_code == 409
    src = open(rs.__file__, encoding='utf-8').read()
    assert src.count('_key_saved(credentials.set_') == 3
    assert '        credentials.set_llm_api_key(' not in src      # a bare call drops the answer
