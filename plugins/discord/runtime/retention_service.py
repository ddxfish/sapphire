"""Data retention and privacy purge service."""

from __future__ import annotations

import time

from plugins.discord.runtime.forget_service import ForgetService

# Chunked deletes (H6, hunt 2026-09-12): one DELETE over a year of messages held
# the shared connection lock for seconds and stalled every reply. Each chunk
# commits and yields between rounds so the daemon loop and the voice pool get in.
PURGE_CHUNK = 5000
_CHUNK_PAUSE_SECONDS = 0.02


def _purge_where(conn, table: str, where: str, params: tuple) -> int:
    total = 0
    while True:
        cursor = conn.execute(
            f'DELETE FROM {table} WHERE rowid IN (SELECT rowid FROM {table} WHERE {where} LIMIT {PURGE_CHUNK})',
            params,
        )
        conn.commit()
        deleted = int(cursor.rowcount or 0)
        total += deleted
        if deleted < PURGE_CHUNK:
            return total
        time.sleep(_CHUNK_PAUSE_SECONDS)


class RetentionService:
    def __init__(self, *, sqlite_service, trace_repository=None, forget_service=None):
        self.sqlite_service = sqlite_service
        self.trace_repository = trace_repository
        self.forget_service = forget_service or ForgetService(
            sqlite_service=sqlite_service, trace_repository=trace_repository,
        )

    def purge(self, settings) -> dict:
        """Every table that accumulates, on the retention windows the operator
        already has (H7): messages + their media artifacts + processed
        sleep-buffer rows (message_days); traces + finished tasks (trace_days);
        transcripts, summaries and closed voice sessions (transcript_days);
        unprocessed profile buffers (profile_buffer_days)."""
        retention = settings.retention
        if not retention.enabled:
            return {'status': 'skipped', 'reason': 'retention_disabled'}
        now = time.time()
        results: dict = {}
        conn = self.sqlite_service.connection()
        if retention.message_days > 0:
            cutoff = now - retention.message_days * 86400
            results['messages'] = _purge_where(conn, 'messages', 'created_at < ?', (cutoff,))
            results['media_artifacts'] = _purge_where(conn, 'media_artifacts', 'created_at < ?', (cutoff,))
            results['sleep_buffer'] = _purge_where(conn, 'sleep_buffer', 'processed = 1 AND created_at < ?', (cutoff,))
        if retention.trace_days > 0:
            cutoff = now - retention.trace_days * 86400
            results['traces'] = _purge_where(conn, 'traces', 'created_at < ?', (cutoff,))
            results['tasks'] = _purge_where(
                conn, 'tasks',
                "status IN ('completed', 'cancelled', 'failed', 'expired') AND created_at < ?", (cutoff,),
            )
        if retention.transcript_days > 0:
            cutoff = now - retention.transcript_days * 86400
            results['voice_transcripts'] = _purge_where(conn, 'voice_transcripts', 'created_at < ?', (cutoff,))
            results['voice_summaries'] = _purge_where(conn, 'voice_summaries', 'created_at < ?', (cutoff,))
            results['voice_sessions'] = _purge_where(
                conn, 'voice_sessions', "state = 'closed' AND ended_at > 0 AND ended_at < ?", (cutoff,),
            )
        if retention.profile_buffer_days > 0:
            cutoff = now - retention.profile_buffer_days * 86400
            results['profile_buffers'] = _purge_where(conn, 'profile_buffers', 'created_at < ?', (cutoff,))
        # A purge is the one moment the WAL is worth folding back into the file.
        checkpoint = getattr(self.sqlite_service, 'checkpoint', None)
        if callable(checkpoint):
            checkpoint()
        return {'status': 'purged', 'results': results}

    def forget_user(self, account_name: str, user_id: str, **_repositories) -> dict:
        """Every door lands here; the table list is the ForgetService's (M4).
        Repository kwargs are accepted for older callers and ignored — the
        service reaches every table through the one connection."""
        return self.forget_service.forget(account_name, user_id)
