"""Account management routes."""

from __future__ import annotations

from plugins.discord.api.storage_access import open_storage
from plugins.discord.daemon import get_runtime, run_coroutine


def _sanitize_account_name(raw: str) -> str:
    return ''.join(c for c in str(raw or '').strip().lower() if c.isalnum() or c in '-_')


async def list_accounts(**kwargs):
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


async def add_account(**kwargs):
    body = kwargs.get('body') or {}
    name = _sanitize_account_name(body.get('account_name', ''))
    token = str(body.get('token', '')).strip()
    if not name:
        return {'error': 'Account name required'}
    if not token:
        return {'error': 'Bot token required'}
    with open_storage() as storage:
        storage.account_repository.upsert_account(name, token=token)
    runtime = get_runtime()
    if runtime and runtime.transport:
        run_coroutine(runtime.transport.connect_account(name, token))
    return {'status': 'added', 'account_name': name, 'connected': bool(runtime and runtime.transport)}


async def delete_account(**kwargs):
    name = _sanitize_account_name(kwargs.get('name', ''))
    if not name:
        return {'error': 'Account name required'}
    runtime = get_runtime()
    if runtime and runtime.transport:
        run_coroutine(runtime.transport.disconnect_account(name)).result(timeout=5)
    with open_storage() as storage:
        storage.account_repository.delete_account(name)
    return {'status': 'deleted', 'account_name': name}


async def test_account(**kwargs):
    name = _sanitize_account_name(kwargs.get('name', ''))
    with open_storage() as storage:
        account = storage.account_repository.get_account(name)
        if not account:
            return {'success': False, 'error': f"Account '{name}' not found"}
        token = storage.account_repository.get_token(name) or ''
    runtime = get_runtime()
    if runtime and runtime.transport:
        future = run_coroutine(runtime.transport.test_account_token(token))
        return future.result(timeout=5)
    if not token:
        return {'success': False, 'error': 'Token missing'}
    return {'success': True, 'message': 'Token stored (daemon offline — format check only)'}
