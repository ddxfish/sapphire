"""Retention (the daily purge) and "forget this person" — one door each (S7).

v2 keeps one table that accumulates: messages. Retention prunes it on
retention.message_days when retention.enabled; forget_user removes one person's
rows regardless. A purge is the one moment the WAL is worth folding back into
the file.
"""

from __future__ import annotations

import logging
import time

from plugins.discord.storage.repositories import MessageRepository

logger = logging.getLogger(__name__)


def purge(sqlite_service, settings) -> dict:
    retention = getattr(settings, 'retention', None)
    if retention is None or not retention.enabled:
        return {'status': 'skipped', 'reason': 'retention_disabled'}
    results: dict = {}
    if retention.message_days > 0:
        cutoff = time.time() - retention.message_days * 86400
        results['messages'] = MessageRepository(sqlite_service).purge_before(cutoff)
    checkpoint = getattr(sqlite_service, 'checkpoint', None)
    if callable(checkpoint):
        checkpoint()
    return {'status': 'purged', 'results': results}


def forget_user(sqlite_service, user_id: str) -> dict:
    user_id = str(user_id or '').strip()
    if not user_id:
        return {'status': 'error', 'error': 'user_id required'}
    removed = MessageRepository(sqlite_service).forget_author(user_id)
    logger.info('[DISCORD] forgot user %s: %s', user_id, removed)
    return {'status': 'forgotten', 'user_id': user_id, **removed}
