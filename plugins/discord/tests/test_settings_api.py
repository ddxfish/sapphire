import asyncio

from plugins.discord.api import settings as settings_api
from plugins.discord.models.settings import SettingsOverlay


class FakeChannelRepository:
    def __init__(self):
        self.saved = []
        self._store = None

    def load_settings_store(self):
        from plugins.discord.models.settings import SettingsStore
        if self._store is None:
            self._store = SettingsStore()
        return self._store

    def save_settings_override(self, scope_type, scope_id, overlay):
        self.saved.append((scope_type, scope_id, overlay))
        store = self.load_settings_store()
        if scope_type == 'global':
            store.global_overlay = overlay
        self._store = store


class FakeStorage:
    def __init__(self, repo):
        self.channel_repository = repo


def test_save_settings_merges_voice_join_targets(monkeypatch):
    repo = FakeChannelRepository()

    class Ctx:
        def __enter__(self):
            return FakeStorage(repo)

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(settings_api, 'open_storage', lambda: Ctx())
    monkeypatch.setattr(settings_api, 'get_runtime', lambda: None)

    asyncio.run(settings_api.save_settings(body={
        'scope_type': 'global',
        'settings': {
            'voice': {
                'enabled': True,
                'join_targets': ['alpha:vc1'],
            },
        },
    }))

    asyncio.run(settings_api.save_settings(body={
        'scope_type': 'global',
        'settings': {
            'media': {'gif_enabled': True},
        },
    }))

    store = repo.load_settings_store()
    resolved = store.resolve()
    assert resolved.voice.enabled is True
    assert resolved.voice.join_targets == ['alpha:vc1']
    assert resolved.media.gif_enabled is True
