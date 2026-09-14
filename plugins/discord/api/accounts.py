"""Account management routes."""

from __future__ import annotations


import logging

from plugins.discord.api.storage_access import open_storage
from plugins.discord.daemon import get_runtime, run_coroutine
from plugins.discord.lib.token_check import check_bot_token

logger = logging.getLogger(__name__)


def _sanitize_account_name(raw: str) -> str:
    return ''.join(c for c in str(raw or '').strip().lower() if c.isalnum() or c in '-_')


def list_accounts(**kwargs):
    with open_storage() as storage:
        accounts = storage.account_repository.list_accounts()
        connected = set()
        if storage.transport:
            connected = set(storage.transport.list_connected())
        for account in accounts:
            account.pop('token', None)
            account['connected'] = account['name'] in connected
            account['value'] = account['name']
            account['label'] = account['bot_name'] or account['name']
        return {'accounts': accounts}


def add_account(**kwargs):
    body = kwargs.get('body') or {}
    name = _sanitize_account_name(body.get('account_name', ''))
    token = str(body.get('token', '')).strip()
    if not name:
        return {'error': 'Account name required'}
    if not token:
        return {'error': 'Bot token required'}
    with open_storage() as storage:
        storage.account_repository.upsert_account(name, token=token)
    from core.event_bus import publish, Events
    publish(Events.SCOPE_CHANGED, {"kind": "discord", "action": "added", "name": name})
    return {'status': 'added', 'account_name': name, 'connected': False,
            'note': 'Bot connects when an enabled daemon task selects it'}


def delete_account(**kwargs):
    name = _sanitize_account_name(kwargs.get('name', ''))
    if not name:
        return {'error': 'Account name required'}
    runtime = get_runtime()
    if runtime and runtime.transport:
        try:
            run_coroutine(runtime.transport.disconnect_account(name)).result(timeout=5)
        except Exception as exc:
            # A wedged gateway must not make the account undeletable (M3).
            logger.warning('Discord account %s: disconnect before delete failed (%s) — deleting anyway', name, exc)
    with open_storage() as storage:
        removed = storage.account_repository.delete_account(name)
    from core.event_bus import publish, Events
    publish(Events.SCOPE_CHANGED, {"kind": "discord", "action": "deleted", "name": name})
    return {'status': 'deleted', 'account_name': name, 'removed': removed or {}}


async def test_account(**kwargs):
    name = _sanitize_account_name(kwargs.get('name', ''))
    with open_storage() as storage:
        account = storage.account_repository.get_account(name)
        if not account:
            return {'success': False, 'error': f"Account '{name}' not found"}
        token = storage.account_repository.get_token(name) or ''
    result = await check_bot_token(token)
    if result.get('success'):
        with open_storage() as storage:
            # A valid token says nothing about the CONNECTION — keep state and
            # last_error (e.g. missing portal intents) intact, only refresh identity.
            storage.account_repository.update_connection_state(
                name,
                account.get('state') or 'disconnected',
                bot_name=result.get('bot_name', ''),
                bot_id=result.get('bot_id', ''),
                last_error=str(account.get('last_error') or ''),
            )
        runtime = get_runtime()
        if runtime and runtime.transport and name not in set(runtime.transport.list_connected()):
            # Token proven good but bot offline — the earlier failure (e.g. portal
            # intents) may be fixed now, so skip the backoff and retry immediately.
            try:
                run_coroutine(runtime.retry_account_connect(name))
                result['reconnect'] = 'started'
            except Exception:
                pass
    return result


async def test_token(**kwargs):
    """Validate a raw token before an account is saved (Add Bot form)."""
    body = kwargs.get('body') or {}
    return await check_bot_token(body.get('token', ''))
