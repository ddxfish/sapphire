from plugins.discord.api.storage_access import open_storage
from plugins.discord.models.settings import SettingsOverlay


def test_open_storage_without_daemon(tmp_path, monkeypatch):
    db_path = tmp_path / 'discord.sqlite3'
    monkeypatch.setattr(
        'plugins.discord.api.storage_access.resolve_default_db_path',
        lambda plugin_name='discord': db_path,
    )
    monkeypatch.setattr('plugins.discord.api.storage_access.get_runtime', lambda: None)

    with open_storage() as storage:
        storage.account_repository.upsert_account('alpha', token='secret-token')
        storage.channel_repository.save_settings_override(
            'global',
            'global',
            SettingsOverlay.from_dict({'cognitive': {'mode': 'conservative'}}),
        )
        names = [item['name'] for item in storage.account_repository.list_accounts()]
        assert names == ['alpha']
        assert storage.channel_repository.load_settings_store().resolve().cognitive.mode == 'conservative'

    with open_storage() as storage:
        names = [item['name'] for item in storage.account_repository.list_accounts()]
        assert names == ['alpha']
        assert storage.channel_repository.load_settings_store().resolve().cognitive.mode == 'conservative'
