"""Settings routes for the Discord cognitive plugin."""

from __future__ import annotations

from plugins.discord.api.storage_access import open_storage
from plugins.discord.daemon import get_health_state, get_runtime, is_daemon_alive
from plugins.discord.models.settings import SettingsOverlay, SettingsStore, _merge_overlay
from plugins.discord.sapphire.voice_prompt import default_conversation_prompt_template


async def get_settings(**kwargs):
    query = kwargs.get('query') or {}
    guild_id = str(query.get('guild_id', '')).strip() or None
    channel_id = str(query.get('channel_id', '')).strip() or None
    dm_id = str(query.get('dm_id', '')).strip() or None
    with open_storage() as storage:
        store = storage.channel_repository.load_settings_store()
        return {
            'settings': store.to_dict(),
            'resolved': store.resolve(guild_id=guild_id, channel_id=channel_id, dm_id=dm_id).to_dict(),
            'defaults': {
                'voice': {
                    'conversation_prompt_template': default_conversation_prompt_template(),
                },
            },
            'daemon_running': is_daemon_alive(),
            'daemon_state': get_health_state(),
        }


async def save_settings(**kwargs):
    body = kwargs.get('body') or {}
    scope_type = str(body.get('scope_type', 'global')).strip().lower()
    scope_id = str(body.get('scope_id', '')).strip() or 'global'
    incoming = SettingsOverlay.from_dict(body.get('settings') or {})
    with open_storage() as storage:
        store = storage.channel_repository.load_settings_store()
        if scope_type == 'global':
            merged = SettingsOverlay.from_dict(store.global_overlay.to_dict())
            _merge_overlay(merged, incoming)
            storage.channel_repository.save_settings_override(scope_type, scope_id, merged)
        elif scope_type == 'guild':
            merged = SettingsOverlay.from_dict(store.guild_overrides.get(scope_id, SettingsOverlay()).to_dict())
            _merge_overlay(merged, incoming)
            storage.channel_repository.save_settings_override(scope_type, scope_id, merged)
        elif scope_type == 'channel':
            merged = SettingsOverlay.from_dict(store.channel_overrides.get(scope_id, SettingsOverlay()).to_dict())
            _merge_overlay(merged, incoming)
            storage.channel_repository.save_settings_override(scope_type, scope_id, merged)
        elif scope_type == 'dm':
            merged = SettingsOverlay.from_dict(store.dm_overrides.get(scope_id, SettingsOverlay()).to_dict())
            _merge_overlay(merged, incoming)
            storage.channel_repository.save_settings_override(scope_type, scope_id, merged)
        else:
            storage.channel_repository.save_settings_override(scope_type, scope_id, incoming)
        store = storage.channel_repository.load_settings_store()
    runtime = get_runtime()
    if runtime:
        runtime.settings_store = store
    return {'status': 'saved', 'scope_type': scope_type, 'scope_id': scope_id, 'daemon_running': is_daemon_alive(), 'daemon_state': get_health_state()}
