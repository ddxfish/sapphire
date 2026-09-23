"""credentials_manager: the Discord bot-account section (label → token, scrambled at rest)."""
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


def test_set_get_list_delete_round_trip(creds_manager):
    mgr, creds_file = creds_manager
    assert mgr.get_discord_account('sapphire') == {}
    assert mgr.set_discord_account('sapphire', token='tok-1') is True
    acct = mgr.get_discord_account('sapphire')
    assert acct['name'] == 'sapphire' and acct['token'] == 'tok-1' and acct['created_at'] > 0
    created = acct['created_at']
    # identity refresh keeps the token and the creation time
    assert mgr.set_discord_account('sapphire', bot_name='Sapph', bot_id='42') is True
    acct = mgr.get_discord_account('sapphire')
    assert acct['token'] == 'tok-1' and acct['bot_name'] == 'Sapph' and acct['bot_id'] == '42'
    assert acct['created_at'] == created
    listed = mgr.list_discord_accounts()
    assert listed == [{'name': 'sapphire', 'bot_name': 'Sapph', 'bot_id': '42', 'created_at': created}]
    assert 'token' not in listed[0]
    assert mgr.delete_discord_account('sapphire') is True and mgr.delete_discord_account('sapphire') is False
    assert mgr.get_discord_account('sapphire') == {}


def test_token_is_scrambled_on_disk(creds_manager):
    mgr, creds_file = creds_manager
    mgr.set_discord_account('sapphire', token='super-secret-bot-token')
    raw = creds_file.read_text(encoding='utf-8')
    assert 'super-secret-bot-token' not in raw
    stored = json.loads(raw)['discord_accounts']['sapphire']['token']
    assert stored and stored != 'super-secret-bot-token'
    assert mgr.get_discord_account('sapphire')['token'] == 'super-secret-bot-token'


def test_section_is_seeded_in_the_schema(creds_manager):
    mgr, creds_file = creds_manager
    assert json.loads(creds_file.read_text(encoding='utf-8')).get('discord_accounts') == {}
