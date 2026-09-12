"""Validate a Discord bot token against the REST API (no gateway connect)."""

from __future__ import annotations

DISCORD_ME_URL = 'https://discord.com/api/v10/users/@me'


async def check_bot_token(token: str) -> dict:
    """Return {'success', 'bot_name', 'bot_id', 'message'} or {'success': False, 'error'}."""
    token = str(token or '').strip()
    if not token:
        return {'success': False, 'error': 'Bot token required'}
    try:
        import aiohttp

        timeout = aiohttp.ClientTimeout(total=6)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(DISCORD_ME_URL, headers={'Authorization': f'Bot {token}'}) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    name = str(data.get('username') or '')
                    bot_id = str(data.get('id') or '')
                    return {
                        'success': True,
                        'bot_name': name,
                        'bot_id': bot_id,
                        'message': f'Token valid — bot: {name}',
                    }
                if resp.status == 401:
                    return {'success': False, 'error': 'Discord rejected the token (401) — wrong or reset token'}
                return {'success': False, 'error': f'Discord API returned HTTP {resp.status}'}
    except Exception as exc:
        return {'success': False, 'error': f'Could not reach Discord: {exc}'}
