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
