"""credentials_manager: the generic per-plugin account store (plugin_accounts,
2026-11-02). Secrets scrambled at rest, fields plain, names may hold '/'."""
import json

import pytest
from unittest.mock import patch


@pytest.fixture
def creds_manager(tmp_path):
    creds_file = tmp_path / 'credentials.json'
    salt_file = tmp_path / '.scramble_salt'
    with patch('core.credentials_manager.CREDENTIALS_FILE', creds_file), \
         patch('core.credentials_manager.SCRAMBLE_SALT_FILE', salt_file), \
         patch('core.credentials_manager.CONFIG_DIR', tmp_path):
        from core.credentials_manager import CredentialsManager
        yield CredentialsManager(), creds_file


def test_round_trip(creds_manager):
    mgr, _ = creds_manager
    assert mgr.get_plugin_account('social', 'anita/fb') == {}
    assert mgr.set_plugin_account('social', 'anita/fb', fields={'page_id': '123'},
                                  secrets={'token': 'tok-1'}) is True
    acct = mgr.get_plugin_account('social', 'anita/fb')
    assert acct['name'] == 'anita/fb'
    assert acct['fields'] == {'page_id': '123'} and acct['secrets'] == {'token': 'tok-1'}
    assert acct['created_at'] > 0
    created = acct['created_at']
    # merge: None keeps, '' clears, a str replaces; fields merge; created_at stays
    assert mgr.set_plugin_account('social', 'anita/fb', fields={'label': 'Page'},
                                  secrets={'token': None, 'refresh': 'r-1'})
    acct = mgr.get_plugin_account('social', 'anita/fb')
    assert acct['fields'] == {'page_id': '123', 'label': 'Page'}
    assert acct['secrets'] == {'token': 'tok-1', 'refresh': 'r-1'}
    assert acct['created_at'] == created
    assert mgr.set_plugin_account('social', 'anita/fb', secrets={'refresh': ''}, fields={'label': None})
    acct = mgr.get_plugin_account('social', 'anita/fb')
    assert acct['secrets'] == {'token': 'tok-1'} and acct['fields'] == {'page_id': '123'}
    listed = mgr.list_plugin_accounts('social')
    assert listed == [{'name': 'anita/fb', 'fields': {'page_id': '123'}, 'secrets': ['token'],
                       'created_at': created}]
    assert mgr.has_plugin_account('social', 'anita/fb')
    assert mgr.delete_plugin_account('social', 'anita/fb') is True
    assert mgr.delete_plugin_account('social', 'anita/fb') is False
    assert mgr.get_plugin_account('social', 'anita/fb') == {} and mgr.list_plugin_accounts('social') == []


def test_secrets_scrambled_on_disk_fields_plain(creds_manager):
    mgr, creds_file = creds_manager
    mgr.set_plugin_account('social', 'sapphire/x', fields={'handle': 'sapph'}, secrets={'token': 'very-secret'})
    raw = creds_file.read_text(encoding='utf-8')
    assert 'very-secret' not in raw
    stored = json.loads(raw)['plugin_accounts']['social']['sapphire/x']
    assert stored['fields'] == {'handle': 'sapph'}
    assert stored['secrets']['token'].startswith('enc:')
    assert mgr.get_plugin_account('social', 'sapphire/x')['secrets']['token'] == 'very-secret'


def test_bad_names_refused(creds_manager):
    mgr, _ = creds_manager
    for bad in ('', 'Has Space', '../x', 'UPPER', 'a' * 90):
        assert mgr.set_plugin_account('social', bad, fields={}) is False, bad
        assert mgr.set_plugin_account(bad, 'ok', fields={}) is False, bad
    assert mgr.set_plugin_account('social', 'ok', fields='nope') is False
    assert mgr.set_plugin_account('social', 'ok', secrets=['nope']) is False
    assert mgr.list_plugin_accounts('social') == []


def test_rename_never_overwrites(creds_manager):
    mgr, _ = creds_manager
    mgr.set_plugin_account('social', 'a/one', secrets={'t': '1'})
    mgr.set_plugin_account('social', 'a/two', secrets={'t': '2'})
    assert mgr.rename_plugin_account('social', 'a/one', 'a/two') is False
    assert mgr.rename_plugin_account('social', 'a/one', 'b/one') is True
    assert mgr.get_plugin_account('social', 'b/one')['secrets'] == {'t': '1'}
    assert mgr.get_plugin_account('social', 'a/one') == {}
    assert mgr.rename_plugin_account('social', 'a/two', 'a/two') is True
    assert mgr.rename_plugin_account('social', 'ghost', 'x') is False


def test_undecryptable_secret_reads_as_empty(creds_manager):
    mgr, creds_file = creds_manager
    mgr.set_plugin_account('social', 'p/c', secrets={'token': 'abc'})
    data = json.loads(creds_file.read_text(encoding='utf-8'))
    data['plugin_accounts']['social']['p/c']['secrets']['token'] = 'enc:garbage'
    creds_file.write_text(json.dumps(data), encoding='utf-8')
    mgr.reload()
    assert mgr.get_plugin_account('social', 'p/c')['secrets'] == {'token': ''}
    assert mgr.list_plugin_accounts('social')[0]['secrets'] == ['token']


def test_schema_seeded_and_summary(creds_manager):
    mgr, creds_file = creds_manager
    assert json.loads(creds_file.read_text(encoding='utf-8')).get('plugin_accounts') == {}
    mgr.set_plugin_account('social', 'p/c', fields={})
    assert mgr.get_masked_summary()['plugins'] == {'social': 1}
