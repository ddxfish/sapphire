"""Bot accounts over core's credentials manager, and the one-time import from an old SQLite file."""
import pytest

from plugins.discord.accounts import DiscordAccounts

from plugins.discord.tests._fakes import FakeCreds, old_account_db


def _accounts(tmp_path, creds=None):
    return DiscordAccounts(store=creds or FakeCreds(), legacy_dir=tmp_path)


def test_round_trip_and_identity_note(tmp_path):
    accounts = _accounts(tmp_path)
    assert accounts.list_accounts() == [] and accounts.get_account('sapphire') is None
    accounts.upsert_account('sapphire', token='tok-1')
    assert accounts.get_token('sapphire') == 'tok-1'
    assert 'token' not in accounts.get_account('sapphire')
    accounts.update_connection_state('sapphire', 'connected', bot_name='Sapph', bot_id='42')
    accounts.update_connection_state('sapphire', 'error', last_error='boom')     # live state is memory-only
    row = accounts.list_accounts()[0]
    assert row['bot_name'] == 'Sapph' and row['bot_id'] == '42' and 'last_error' not in row
    assert accounts.delete_account('sapphire') is True and accounts.delete_account('sapphire') is False


def test_import_moves_the_accounts_out_of_the_old_file_and_renames_it(tmp_path):
    old_account_db(tmp_path / 'discord' / 'discord.sqlite3')
    old_account_db(tmp_path / 'discord_cognitive' / 'discord.sqlite3', rows=(('leona', 'tok-2', '', '', 3.0),))
    accounts = _accounts(tmp_path)
    summary = accounts.import_legacy()
    assert sorted(summary.values()) == [1, 1]
    assert accounts.get_token('sapphire') == 'tok-1' and accounts.get_token('leona') == 'tok-2'
    assert accounts.get_account('sapphire')['bot_name'] == 'Sapph'
    assert accounts.get_account('sapphire')['created_at'] == 5.0                   # creation time carried over
    assert not (tmp_path / 'discord' / 'discord.sqlite3').exists()
    assert (tmp_path / 'discord' / 'discord.sqlite3.imported').exists()
    assert (tmp_path / 'discord_cognitive' / 'discord.sqlite3.imported').exists()
    assert accounts.import_legacy() == {}                                          # second boot: nothing to do


def test_import_never_overwrites_an_account_already_in_the_manager(tmp_path):
    old_account_db(tmp_path / 'discord' / 'discord.sqlite3', rows=(('sapphire', 'old-token', '', '', 1.0),))
    accounts = _accounts(tmp_path)
    accounts.upsert_account('sapphire', token='new-token')
    assert accounts.import_legacy() == {str(tmp_path / 'discord' / 'discord.sqlite3'): 0}
    assert accounts.get_token('sapphire') == 'new-token'


def test_an_unreadable_old_file_refuses_the_boot(tmp_path):
    path = tmp_path / 'discord' / 'discord.sqlite3'
    path.parent.mkdir(parents=True)
    path.write_bytes(b'this is not a database' * 100)
    with pytest.raises(RuntimeError, match='cannot be read'):
        _accounts(tmp_path).import_legacy()
    assert path.exists()                                                           # left in place for the operator


def test_a_file_without_an_accounts_table_is_just_moved_aside(tmp_path):
    old_account_db(tmp_path / 'discord' / 'discord.sqlite3', with_accounts=False)
    accounts = _accounts(tmp_path)
    assert accounts.import_legacy() == {str(tmp_path / 'discord' / 'discord.sqlite3'): 0}
    assert (tmp_path / 'discord' / 'discord.sqlite3.imported').exists()
