"""Data retention and privacy purge service."""

from __future__ import annotations

import time


class RetentionService:
    def __init__(self, *, sqlite_service, trace_repository=None):
        self.sqlite_service = sqlite_service
        self.trace_repository = trace_repository

    def purge(self, settings) -> dict:
        retention = settings.retention
        if not retention.enabled:
            return {'status': 'skipped', 'reason': 'retention_disabled'}
        now = time.time()
        results = {}
        conn = self.sqlite_service.connection()
        if retention.message_days > 0:
            cutoff = now - retention.message_days * 86400
            cursor = conn.execute('DELETE FROM messages WHERE created_at < ?', (cutoff,))
            results['messages'] = cursor.rowcount
        if retention.trace_days > 0:
            cutoff = now - retention.trace_days * 86400
            cursor = conn.execute('DELETE FROM traces WHERE created_at < ?', (cutoff,))
            results['traces'] = cursor.rowcount
        if retention.transcript_days > 0:
            cutoff = now - retention.transcript_days * 86400
            cursor = conn.execute('DELETE FROM voice_transcripts WHERE created_at < ?', (cutoff,))
            results['voice_transcripts'] = cursor.rowcount
        if retention.profile_buffer_days > 0:
            cutoff = now - retention.profile_buffer_days * 86400
            cursor = conn.execute(
                'DELETE FROM profile_buffers WHERE created_at < ? AND processed = 1',
                (cutoff,),
            )
            results['profile_buffers'] = cursor.rowcount
        conn.commit()
        return {'status': 'purged', 'results': results}

    def forget_user(self, account_name: str, user_id: str, *, memory_repository=None, profile_repository=None) -> dict:
        removed = {'account_name': account_name, 'user_id': user_id}
        if profile_repository:
            profile_repository.forget_user(account_name, user_id)
            removed['profile'] = True
        if memory_repository:
            memory_repository.forget_user(account_name, user_id)
            removed['pinned_memories'] = True
        conn = self.sqlite_service.connection()
        conn.execute(
            'DELETE FROM messages WHERE author_id = ?',
            (user_id,),
        )
        removed['messages'] = conn.total_changes
        conn.commit()
        return {'status': 'forgotten', **removed}
