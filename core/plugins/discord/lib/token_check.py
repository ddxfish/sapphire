"""Validate a Discord bot token against the REST API (no gateway connect), through
core.net — the one egress door (SSRF lanes, proxies) — off the API thread."""

from __future__ import annotations

import asyncio

DISCORD_ME_URL = 'https://discord.com/api/v10/users/@me'


def _probe(token: str):
    from core import net
    return net.get(DISCORD_ME_URL, headers={'Authorization': f'Bot {token}'}, timeout=6, allow_redirects=False)


async def check_bot_token(token: str) -> dict:
    """Return {'success', 'bot_name', 'bot_id', 'message'} or {'success': False, 'error'}."""
    token = str(token or '').strip()
    if not token:
        return {'success': False, 'error': 'Bot token required'}
    try:
        resp = await asyncio.to_thread(_probe, token)
    except Exception as exc:
        return {'success': False, 'error': f'Could not reach Discord: {exc}'}
    if resp.status_code == 200:
        data = resp.json()
        name = str(data.get('username') or '')
        return {'success': True, 'bot_name': name, 'bot_id': str(data.get('id') or ''),
                'message': f'Token valid — bot: {name}'}
    if resp.status_code == 401:
        return {'success': False, 'error': 'Discord rejected the token (401) — wrong or reset token'}
    return {'success': False, 'error': f'Discord API returned HTTP {resp.status_code}'}
