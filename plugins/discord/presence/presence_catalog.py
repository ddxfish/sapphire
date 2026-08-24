"""Awake/sleep presence preset catalogs."""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_AWAKE_PRESETS = [
    {'id': 'clear', 'category': 'none', 'label': 'No activity (cleared)', 'value': ''},
    {'id': 'listening_chat', 'category': 'listening', 'label': 'Listening to chat', 'value': 'listening: chat'},
    {'id': 'listening_lofi', 'category': 'listening', 'label': 'Listening to lo-fi', 'value': 'listening: lo-fi beats'},
    {'id': 'watching_server', 'category': 'watching', 'label': 'Watching the server', 'value': 'watching: the server'},
    {'id': 'watching_memes', 'category': 'watching', 'label': 'Watching memes roll in', 'value': 'watching: memes roll in'},
    {'id': 'playing_ideas', 'category': 'playing', 'label': 'Playing with ideas', 'value': 'playing: with ideas'},
    {'id': 'playing_dnd', 'category': 'playing', 'label': 'Playing D&D', 'value': 'playing: D&D'},
    {'id': 'custom_vibing', 'category': 'custom', 'label': 'Just vibing', 'value': 'just vibing'},
    {'id': 'custom_chill', 'category': 'custom', 'label': 'Having a chill day', 'value': 'having a chill day'},
    {'id': 'daydreaming', 'category': 'custom', 'label': 'Daydreaming', 'value': 'daydreaming'},
]

DEFAULT_ENABLED_PRESET_IDS = [
    'clear',
    'listening_chat',
    'watching_server',
    'playing_ideas',
    'daydreaming',
]

_DEFAULT_SLEEP_STATUSES = (
    'custom: sleeping',
    'custom: dreaming',
    'custom: do not disturb',
    'custom: tucked in for the night',
)

_VALID_CATEGORIES = {
    'none', 'custom', 'playing', 'listening', 'watching', 'competing',
    'studying', 'working', 'eating',
}


def _status_dir() -> Path:
    return Path(__file__).resolve().parent.parent / 'statuses'


def _normalize_preset(item: object) -> dict | None:
    if not isinstance(item, dict):
        return None
    preset_id = str(item.get('id') or '').strip()
    category = str(item.get('category') or '').strip().lower()
    label = str(item.get('label') or '').strip()
    value = str(item.get('value') or '')
    if not preset_id or category not in _VALID_CATEGORIES or not label:
        return None
    return {
        'id': preset_id[:50],
        'category': category,
        'label': label[:128],
        'value': value[:128],
    }


def load_awake_presets() -> list[dict]:
    path = _status_dir() / 'awake.json'
    try:
        if not path.exists():
            return list(_DEFAULT_AWAKE_PRESETS)
        data = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(data, list):
            raise ValueError('awake.json must be a list')
        presets = []
        seen = set()
        for item in data:
            normalized = _normalize_preset(item)
            if not normalized or normalized['id'] in seen:
                continue
            presets.append(normalized)
            seen.add(normalized['id'])
        return presets or list(_DEFAULT_AWAKE_PRESETS)
    except Exception as exc:
        logger.warning('Failed to load awake presence presets from %s: %s', path, exc)
        return list(_DEFAULT_AWAKE_PRESETS)


def load_sleep_statuses() -> tuple[str, ...]:
    path = _status_dir() / 'sleep.json'
    try:
        if not path.exists():
            return _DEFAULT_SLEEP_STATUSES
        data = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(data, list):
            raise ValueError('sleep.json must be a list')
        values = [str(item).strip()[:128] for item in data if str(item).strip()]
        return tuple(values) if values else _DEFAULT_SLEEP_STATUSES
    except Exception as exc:
        logger.warning('Failed to load sleep presence statuses from %s: %s', path, exc)
        return _DEFAULT_SLEEP_STATUSES


def preset_catalog() -> list[dict]:
    return load_awake_presets()


def activity_pool(presence_settings) -> list[str]:
    presets_by_id = {item['id']: item for item in load_awake_presets()}
    enabled_ids = list(getattr(presence_settings, 'activity_presets', None) or [])
    if not enabled_ids:
        enabled_ids = list(DEFAULT_ENABLED_PRESET_IDS)
    custom = list(getattr(presence_settings, 'activities_custom', None) or [])
    pool: list[str] = []
    for preset_id in enabled_ids:
        preset = presets_by_id.get(preset_id)
        if preset is not None:
            pool.append(preset['value'])
    for line in custom:
        text = str(line or '').strip()
        if text == '-' or text.lower() == 'clear':
            pool.append('')
        elif text:
            pool.append(text[:128])
    if not pool and getattr(presence_settings, 'activity', ''):
        pool.append(str(presence_settings.activity))
    return pool
