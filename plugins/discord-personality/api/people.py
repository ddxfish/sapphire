"""People browser routes — plain `def` (core threadpools sync handlers)."""
from __future__ import annotations

import sys
from pathlib import Path

_PLUGIN_ROOT = str(Path(__file__).absolute().parents[1])
if _PLUGIN_ROOT not in sys.path:
    sys.path.insert(0, _PLUGIN_ROOT)

from discord_personality.storage import normalize_birthday, people  # noqa: E402


def list_people(**kwargs):
    query = kwargs.get('query') or {}
    account = str(query.get('account') or '').strip()
    if not account:
        return {'people': [], 'error': 'account required'}
    return {'people': people().list(account)}


def save_person(**kwargs):
    body = kwargs.get('body') or {}
    account = str(body.get('account') or '').strip()
    user_id = str(body.get('user_id') or '').strip()
    if not account or not user_id.isdigit():
        return {'error': 'account and a numeric Discord user id are required'}
    birthday = normalize_birthday(body.get('birthday')) if 'birthday' in body else None
    if birthday is None and 'birthday' in body:
        return {'error': 'birthday must be MM-DD (or blank to clear)'}
    display_name = str(body['display_name']).strip() if 'display_name' in body else None
    return {'person': people().upsert(account, user_id, display_name=display_name, birthday=birthday)}


def delete_person(**kwargs):
    body = kwargs.get('body') or {}
    account = str(body.get('account') or '').strip()
    user_id = str(body.get('user_id') or '').strip()
    if not account or not user_id:
        return {'error': 'account and user_id required'}
    return {'deleted': people().delete(account, user_id)}
