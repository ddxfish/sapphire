from plugins.discord.api.storage_access import open_storage


def test_open_storage_without_daemon(tmp_path, monkeypatch):
    db_path = tmp_path / 'discord.sqlite3'
    monkeypatch.setattr('plugins.discord.api.storage_access.resolve_default_db_path', lambda plugin_name='discord': db_path)
    monkeypatch.setattr('plugins.discord.api.storage_access.get_runtime', lambda: None)

    with open_storage() as storage:
        storage.account_repository.upsert_account('alpha', token='secret-token')
        assert [item['name'] for item in storage.account_repository.list_accounts()] == ['alpha']
        assert storage.settings_store.resolve().channel.reply_mode == 'default'

    with open_storage() as storage:
        assert [item['name'] for item in storage.account_repository.list_accounts()] == ['alpha']
