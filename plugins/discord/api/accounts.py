"""Account management routes — the bot accounts live in core's credentials manager."""

from __future__ import annotations

import logging

from plugins.discord.accounts import DiscordAccounts
from plugins.discord.daemon import get_runtime, run_coroutine
from plugins.discord.lib.token_check import check_bot_token

logger = logging.getLogger(__name__)


def _sanitize_account_name(raw: str) -> str:
    return ''.join(c for c in str(raw or '').strip().lower() if c.isalnum() or c in '-_')


def _accounts() -> DiscordAccounts:
    runtime = get_runtime()
    return runtime.account_repository if runtime else DiscordAccounts()


def list_accounts(**kwargs):
    runtime = get_runtime()
    transport = runtime.transport if runtime else None
    rows = _accounts().list_accounts()
    for account in rows:
        live = {}
        if transport:
            try:
                live = transport.account_health(account['name']) or {}
            except Exception:
                live = {}
        account['state'] = str(live.get('state') or 'disconnected')
        account['last_error'] = str(live.get('last_error') or '')
        account['connected'] = account['state'] == 'connected'
        account['value'] = account['name']
        account['label'] = account.get('bot_name') or account['name']
    return {'accounts': rows}


def add_account(**kwargs):
    body = kwargs.get('body') or {}
    name = _sanitize_account_name(body.get('account_name', ''))
    token = str(body.get('token', '')).strip()
    if not name:
        return {'error': 'Account name required'}
    if not token:
        return {'error': 'Bot token required'}
    _accounts().upsert_account(name, token=token)
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
    removed = _accounts().delete_account(name)
    from core.event_bus import publish, Events
    publish(Events.SCOPE_CHANGED, {"kind": "discord", "action": "deleted", "name": name})
    return {'status': 'deleted', 'account_name': name, 'removed': removed}


async def test_account(**kwargs):
    name = _sanitize_account_name(kwargs.get('name', ''))
    accounts = _accounts()
    account = accounts.get_account(name)
    if not account:
        return {'success': False, 'error': f"Account '{name}' not found"}
    result = await check_bot_token(accounts.get_token(name) or '')
    if result.get('success'):
        # A valid token says nothing about the CONNECTION — only refresh the identity.
        accounts.update_connection_state(name, 'unchanged', bot_name=result.get('bot_name', ''),
                                         bot_id=result.get('bot_id', ''))
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
