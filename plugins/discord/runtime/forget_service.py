"""One owner of "forget this person" (M4, hunt 2026-09-12).

Three doors used to each delete a different subset of tables (admin route,
retention service, per-repo helpers) and the orphans lived on: voice
transcripts by speaker, sleep-buffer rows, media artifacts, pending tasks about
the user. Every door now calls ForgetService.forget(); the table list lives
here and nowhere else.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# (table, column holding the user's id) — every row scoped by bot account.
_USER_TABLES = (
    ('user_profiles', 'user_id'),
    ('profile_facts', 'user_id'),
    ('profile_buffers', 'user_id'),
    ('interest_topics', 'user_id'),
    ('relationship_milestones', 'user_id'),
    ('pinned_memories', 'author_id'),
    ('voice_transcripts', 'speaker_id'),
    ('sleep_buffer', 'author_id'),
)


class ForgetService:
    def __init__(self, *, sqlite_service, trace_repository=None):
        self.sqlite_service = sqlite_service
        self.trace_repository = trace_repository

    def forget(self, account_name: str, user_id: str) -> dict:
        account_name = str(account_name or '')
        user_id = str(user_id or '')
        if not account_name or not user_id:
            return {'status': 'error', 'error': 'account_name and user_id required'}
        conn = self.sqlite_service.connection()
        removed: dict = {'account_name': account_name, 'user_id': user_id}
        with conn:  # one transaction under the connection lock: all or nothing
            for table, column in _USER_TABLES:
                cursor = conn.execute(
                    f'DELETE FROM {table} WHERE account_name = ? AND {column} = ?',
                    (account_name, user_id),
                )
                removed[table] = int(cursor.rowcount or 0)
            # Message rows are per channel, shared by every bot account on
            # this install — a person's rows go regardless of which bot saw
            # them (the honest scope of "forget me"). Their media artifacts first.
            cursor = conn.execute(
                'DELETE FROM media_artifacts WHERE message_id IN '
                '(SELECT message_id FROM messages WHERE author_id = ?)',
                (user_id,),
            )
            removed['media_artifacts'] = int(cursor.rowcount or 0)
            cursor = conn.execute('DELETE FROM messages WHERE author_id = ?', (user_id,))
            removed['messages'] = int(cursor.rowcount or 0)
            # Pending follow-ups that name them (social check-ins, reminders).
            cursor = conn.execute(
                "DELETE FROM tasks WHERE account_name = ? AND status = 'pending' AND payload_json LIKE ?",
                (account_name, f'%"author_id": "{user_id}"%'),
            )
            removed['tasks'] = int(cursor.rowcount or 0)
        if self.trace_repository:
            try:
                self.trace_repository.record_trace('user_forgotten', 'Forgot a user across every table', dict(removed))
            except Exception:
                logger.debug('forget trace skipped', exc_info=True)
        logger.info('[DISCORD] forgot user %s for %s: %s', user_id, account_name,
                    {k: v for k, v in removed.items() if isinstance(v, int) and v})
        return {'status': 'forgotten', **removed}
