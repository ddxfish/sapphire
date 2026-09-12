"""Presence preset catalog routes."""

from __future__ import annotations

from plugins.discord.presence.presence_catalog import (
    DEFAULT_ENABLED_PRESET_IDS,
    load_sleep_statuses,
    preset_catalog,
)


async def list_presets(**kwargs):
    return {
        'presets': preset_catalog(),
        'sleep_statuses': list(load_sleep_statuses()),
        'default_enabled_ids': list(DEFAULT_ENABLED_PRESET_IDS),
    }
