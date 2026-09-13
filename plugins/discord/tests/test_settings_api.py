
from plugins.discord.api import settings as settings_api
from plugins.discord.models.settings import SettingsOverlay, overlay_from_flat


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
        if scope_type == 'guild':
            store.guild_overrides[scope_id] = overlay
        self._store = store


class FakeStorage:
    def __init__(self, repo):
        self.channel_repository = repo


def _patch(monkeypatch, repo):
    class Ctx:
        def __enter__(self):
            return FakeStorage(repo)

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(settings_api, 'open_storage', lambda: Ctx())
    monkeypatch.setattr(settings_api, 'get_runtime', lambda: None)


def test_global_save_rejected(monkeypatch):
    """Global settings live in core now — the plugin route only takes overlays."""
    repo = FakeChannelRepository()
    _patch(monkeypatch, repo)

    result = settings_api.save_settings(body={
        'scope_type': 'global',
        'settings': {'media': {'gif_enabled': True}},
    })

    assert 'error' in result
    assert repo.saved == []


def test_guild_overlay_save_merges(monkeypatch):
    repo = FakeChannelRepository()
    _patch(monkeypatch, repo)

    settings_api.save_settings(body={
        'scope_type': 'guild',
        'scope_id': 'g1',
        'settings': {'channel': {'reply_mode': 'mentions_only'}},
    })
    settings_api.save_settings(body={
        'scope_type': 'guild',
        'scope_id': 'g1',
        'settings': {'channel': {'batching_seconds': 4}},
    })

    overlay = repo.load_settings_store().guild_overrides['g1']
    assert overlay.channel['reply_mode'] == 'mentions_only'
    assert overlay.channel['batching_seconds'] == 4


def test_overlay_from_flat_maps_dotted_keys():
    overlay = overlay_from_flat({
        'channel.reply_mode': 'all',
        'media.gif_enabled': True,
        'not_a_dotted_key': 'ignored',
        'unknown_section.field': 'dropped',
    })
    assert overlay.channel['reply_mode'] == 'all'
    assert overlay.media['gif_enabled'] is True
    assert isinstance(overlay, SettingsOverlay)
