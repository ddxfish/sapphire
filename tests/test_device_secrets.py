# tests/test_device_secrets.py - core/devices/secret_store (tmp/device-manager-plan.md).
# Every test runs against a temp folder. The real ~/.config/sapphire is never
# read or written: the file path, the salt, and the scrambler are all patched.

import json
from unittest.mock import patch

import pytest

from core.credentials_manager import DecryptionError


@pytest.fixture
def ds(tmp_path):
    with patch('core.credentials_manager.CREDENTIALS_FILE', tmp_path / 'credentials.json'), \
         patch('core.credentials_manager.SCRAMBLE_SALT_FILE', tmp_path / '.scramble_salt'), \
         patch('core.credentials_manager.CONFIG_DIR', tmp_path):
        from core.credentials_manager import CredentialsManager
        import core.devices.secret_store as mod
        mgr = CredentialsManager()
        with patch.object(mod, 'SECRETS_FILE', tmp_path / 'device_secrets.json'), \
             patch.object(mod, 'CONFIG_DIR', tmp_path), \
             patch.object(mod, '_crypto', lambda: mgr):
            mod.reload()
            yield mod, tmp_path / 'device_secrets.json'
            mod.reload()


def test_put_resolve_round_trip(ds):
    mod, path = ds
    assert mod.status('desktop') == {}
    assert mod.put('desktop', 'password', 'hunter2-long') is True
    assert mod.status('desktop') == {'password': 'set'}
    s = mod.resolve('desktop')
    assert s.get('password') == 'hunter2-long' and 'password' in s and bool(s)
    assert s.get('missing') == '' and s.fields() == ['password']


def test_scrambled_on_disk_and_private_file(ds):
    mod, path = ds
    mod.put('desktop', 'password', 'hunter2-long')
    raw = path.read_text(encoding='utf-8')
    assert 'hunter2-long' not in raw
    assert json.loads(raw)['devices']['desktop']['password'].startswith('enc:')
    import os, sys
    if sys.platform != 'win32':
        assert oct(os.stat(path).st_mode & 0o777) == '0o600'


def test_secrets_object_never_prints_values(ds):
    mod, _ = ds
    mod.put('desktop', 'password', 'hunter2-long')
    s = mod.resolve('desktop')
    for shown in (repr(s), str(s), f"{s}", '%s' % s):
        assert 'hunter2-long' not in shown and 'redacted' in shown


def test_scrub_removes_values_and_key_lines(ds):
    mod, _ = ds
    key = "-----BEGIN KEY-----\nAAAAB3NzaC1yc2E\n-----END KEY-----"
    mod.put('desktop', 'password', 'hunter2-long')
    mod.put('desktop', 'private_key', key)
    s = mod.resolve('desktop')
    out = s.scrub("auth failed for hunter2-long near AAAAB3NzaC1yc2E ok")
    assert 'hunter2-long' not in out and 'AAAAB3NzaC1yc2E' not in out
    assert out.count('[secret]') == 2 and out.endswith(' ok')


def test_empty_and_bad_names_refused(ds):
    mod, path = ds
    assert mod.put('desktop', 'password', '') is False
    assert mod.put('desktop', 'password', None) is False
    assert mod.put('Bad Name', 'password', 'x' * 8) is False
    assert mod.put('../etc', 'password', 'x' * 8) is False
    assert mod.put('desktop', 'bad field!', 'x' * 8) is False
    assert mod.put('desktop', '.hidden', 'x' * 8) is False
    assert not path.exists()
    assert mod.status('Bad Name') == {} and not mod.resolve('Bad Name')


def test_clear_delete_rename(ds):
    mod, _ = ds
    mod.put('desktop', 'password', 'pw-one-long')
    mod.put('desktop', 'private_key', 'key-one-long')
    assert mod.clear('desktop', 'password') is True
    assert mod.clear('desktop', 'password') is True          # already gone
    assert mod.status('desktop') == {'private_key': 'set'}
    assert mod.rename('desktop', 'tower') is True
    assert mod.status('desktop') == {} and mod.status('tower') == {'private_key': 'set'}
    assert mod.rename('ghost', 'anything') is True           # nothing to move
    mod.put('laptop', 'password', 'pw-two-long')
    assert mod.rename('laptop', 'tower') is False            # never overwrites
    assert mod.resolve('tower').get('private_key') == 'key-one-long'
    assert mod.resolve('laptop').get('password') == 'pw-two-long'
    assert mod.delete('tower') is True and mod.delete('tower') is True
    assert mod.status('tower') == {}


def test_fields_are_namespaced_by_driver(ds):
    mod, _ = ds
    assert mod.put('raspi', 'ssh.password', 'pw-for-ssh-long') is True
    assert mod.put('raspi', 'body-pi.token', 'tok-for-body-long') is True
    s = mod.resolve('raspi')
    assert s.get('ssh.password') == 'pw-for-ssh-long'
    assert s.get('body-pi.token') == 'tok-for-body-long'


def test_survives_reload_from_disk(ds):
    mod, _ = ds
    mod.put('desktop', 'password', 'hunter2-long')
    mod.reload()
    assert mod.resolve('desktop').get('password') == 'hunter2-long'


def test_undecryptable_is_loud_not_empty(ds):
    mod, path = ds
    mod.put('desktop', 'password', 'hunter2-long')
    data = json.loads(path.read_text(encoding='utf-8'))
    data['devices']['desktop']['password'] = 'enc:not-a-real-token'
    path.write_text(json.dumps(data), encoding='utf-8')
    mod.reload()
    assert mod.status('desktop') == {'password': 'undecryptable'}
    with pytest.raises(DecryptionError) as err:
        mod.resolve('desktop')
    assert 'desktop' in str(err.value) and 'password' in str(err.value)


def test_failed_save_leaves_memory_matching_disk(ds):
    mod, _ = ds
    mod.put('desktop', 'password', 'old-value-long')
    with patch.object(mod, '_save', lambda: False):
        assert mod.put('desktop', 'password', 'new-value-long') is False
        assert mod.put('desktop', 'token', 'tok-value-long') is False
        assert mod.clear('desktop', 'password') is False
        assert mod.delete('desktop') is False
        assert mod.rename('desktop', 'tower') is False
    assert mod.status('desktop') == {'password': 'set'} and mod.status('tower') == {}
    assert mod.resolve('desktop').get('password') == 'old-value-long'


def test_corrupt_file_is_kept_not_lost(ds):
    mod, path = ds
    path.write_text('{ this is not json', encoding='utf-8')
    mod.reload()
    assert mod.status('desktop') == {}
    kept = list(path.parent.glob('device_secrets.json.corrupt.*'))
    assert len(kept) == 1 and kept[0].read_text(encoding='utf-8') == '{ this is not json'


def test_never_stores_plaintext_when_scramble_breaks(ds):
    mod, path = ds

    class Broken:
        def scramble(self, v):
            return v                      # a scrambler that does nothing
    with patch.object(mod, '_crypto', lambda: Broken()):
        assert mod.put('desktop', 'password', 'hunter2-long') is False
    assert not path.exists()


def test_credentials_file_is_never_touched(ds):
    mod, path = ds
    creds = path.parent / 'credentials.json'
    before = creds.read_text(encoding='utf-8') if creds.exists() else None
    mod.put('desktop', 'password', 'hunter2-long')
    mod.rename('desktop', 'tower')
    mod.delete('tower')
    after = creds.read_text(encoding='utf-8') if creds.exists() else None
    assert before == after
