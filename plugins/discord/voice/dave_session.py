"""DAVE voice session readiness helpers for py-cord VoiceClient."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_DAVE_READY_TIMEOUT = 10.0
PASSTHROUGH_MODE_SECONDS = 3600
_PASSTHROUGH_LOG_INTERVAL = 60.0
_last_passthrough_log: dict[str, float] = {}


def enable_dave_passthrough_mode(
    voice_client,
    *,
    duration: int = PASSTHROUGH_MODE_SECONDS,
) -> bool:
    """Allow unencrypted/passthrough DAVE frames through davey.

    Required for ``UnencryptedWhenPassthroughDisabled`` placeholder frames
    (all-0x32 payloads). Safe to keep enabled while recording because
    ``pycord_patches`` skips py-cord's erroneous PCM double-decrypt.
    """
    connection = getattr(voice_client, '_connection', None)
    dave = getattr(connection, 'dave_session', None) if connection else None
    if not dave or not getattr(dave, 'ready', False):
        return False
    setter = getattr(dave, 'set_passthrough_mode', None)
    if not callable(setter):
        return False
    try:
        setter(True, int(duration))
    except TypeError:
        setter(True)
    except Exception as exc:
        logger.debug('DAVE passthrough mode enable failed: %s', exc)
        return False
    channel = getattr(voice_client, 'channel', None)
    channel_id = str(getattr(channel, 'id', '') or 'unknown')
    now = time.monotonic()
    last_log = _last_passthrough_log.get(channel_id, 0.0)
    if now - last_log >= _PASSTHROUGH_LOG_INTERVAL:
        logger.info('DAVE passthrough mode enabled for %ss', duration)
        _last_passthrough_log[channel_id] = now
    else:
        logger.debug('DAVE passthrough mode refreshed for %ss', duration)
    return True


def voice_dave_snapshot(voice_client) -> dict[str, Any]:
    """Summarize DAVE state for logging and health checks."""
    connection = getattr(voice_client, '_connection', None)
    dave = getattr(connection, 'dave_session', None) if connection else None
    is_dave = False
    if dave is not None:
        is_dave = True
    elif hasattr(voice_client, 'is_dave_connection'):
        try:
            is_dave = bool(voice_client.is_dave_connection())
        except Exception:
            is_dave = False
    privacy_code = None
    try:
        privacy_code = getattr(voice_client, 'privacy_code', None)
    except Exception:
        privacy_code = None
    channel = getattr(voice_client, 'channel', None)
    return {
        'connected': bool(getattr(voice_client, 'is_connected', lambda: False)()),
        'channel_id': str(getattr(channel, 'id', '') or ''),
        'is_dave': is_dave,
        'dave_ready': bool(dave and getattr(dave, 'ready', False)),
        'dave_status': str(getattr(dave, 'status', '') or '') if dave else '',
        'privacy_code': privacy_code,
    }


def log_voice_dave_state(voice_client, *, context: str) -> dict[str, Any]:
    snapshot = voice_dave_snapshot(voice_client)
    logger.info(
        'Voice DAVE %s: channel=%s dave=%s ready=%s status=%s privacy=%s',
        context,
        snapshot.get('channel_id') or '?',
        snapshot.get('is_dave'),
        snapshot.get('dave_ready'),
        snapshot.get('dave_status') or '-',
        snapshot.get('privacy_code') or '-',
    )
    return snapshot


async def wait_for_dave_ready(voice_client, *, timeout: float = DEFAULT_DAVE_READY_TIMEOUT) -> dict[str, Any]:
    """Wait until a DAVE call is ready to encrypt/decrypt, or timeout."""
    snapshot = voice_dave_snapshot(voice_client)
    if not snapshot.get('is_dave'):
        return snapshot
    if snapshot.get('dave_ready'):
        snapshot['passthrough_enabled'] = enable_dave_passthrough_mode(voice_client)
        return snapshot
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(0.5, float(timeout))
    while loop.time() < deadline:
        await asyncio.sleep(0.2)
        snapshot = voice_dave_snapshot(voice_client)
        if snapshot.get('dave_ready'):
            snapshot['wait_seconds'] = round(max(0.0, timeout - (deadline - loop.time())), 2)
            snapshot['passthrough_enabled'] = enable_dave_passthrough_mode(voice_client)
            return snapshot
    snapshot = voice_dave_snapshot(voice_client)
    snapshot['wait_timed_out'] = True
    logger.warning(
        'DAVE session not ready after %.1fs (channel=%s status=%s)',
        timeout,
        snapshot.get('channel_id') or '?',
        snapshot.get('dave_status') or '-',
    )
    return snapshot
