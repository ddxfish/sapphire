"""User profile, facts, buffers, and affect repository."""

from __future__ import annotations

import time

from plugins.discord.models.profiles import AgentAffect, RelationshipState


class ProfileRepository:
    def __init__(self, sqlite_service):
        self.sqlite_service = sqlite_service

    def get_or_create_profile(self, account_name: str, user_id: str) -> dict:
        conn = self.sqlite_service.connection()
        row = conn.execute(
            'SELECT * FROM user_profiles WHERE account_name = ? AND user_id = ?',
            (account_name, user_id),
        ).fetchone()
        if row:
            return dict(row)
        now = time.time()
        conn.execute(
            '''
            INSERT INTO user_profiles
            (account_name, user_id, updated_at)
            VALUES (?, ?, ?)
            ''',
            (account_name, user_id, now),
        )
        conn.commit()
        return dict(conn.execute(
            'SELECT * FROM user_profiles WHERE account_name = ? AND user_id = ?',
            (account_name, user_id),
        ).fetchone())

    def update_profile(self, account_name: str, user_id: str, **fields) -> dict:
        profile = self.get_or_create_profile(account_name, user_id)
        allowed = {
            'summary', 'fondness', 'trust', 'patience', 'respect',
            'interest', 'familiarity', 'message_count',
            'birthday_month', 'birthday_day', 'birthday_channel_id',
            'birthday_username', 'birthday_display_name', 'last_birthday_wish_year',
            'birthday_wish_run_at',
        }
        updates = {key: value for key, value in fields.items() if key in allowed}
        if not updates:
            return profile
        updates['updated_at'] = time.time()
        assignments = ', '.join(f'{key} = ?' for key in updates)
        params = list(updates.values()) + [account_name, user_id]
        conn = self.sqlite_service.connection()
        conn.execute(
            f'UPDATE user_profiles SET {assignments} WHERE account_name = ? AND user_id = ?',
            params,
        )
        conn.commit()
        return dict(conn.execute(
            'SELECT * FROM user_profiles WHERE account_name = ? AND user_id = ?',
            (account_name, user_id),
        ).fetchone())

    def add_fact(self, account_name: str, user_id: str, content: str, *, source: str = 'explicit', confidence: float = 1.0) -> int:
        conn = self.sqlite_service.connection()
        cursor = conn.execute(
            '''
            INSERT INTO profile_facts (account_name, user_id, content, confidence, source, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ''',
            (account_name, user_id, content, confidence, source, time.time()),
        )
        conn.commit()
        return int(cursor.lastrowid)

    def list_facts(self, account_name: str, user_id: str, limit: int = 20) -> list[dict]:
        rows = self.sqlite_service.connection().execute(
            '''
            SELECT id, content, confidence, source, created_at
            FROM profile_facts
            WHERE account_name = ? AND user_id = ?
            ORDER BY created_at DESC LIMIT ?
            ''',
            (account_name, user_id, max(1, int(limit))),
        ).fetchall()
        return [dict(row) for row in rows]

    def buffer_message(self, account_name: str, user_id: str, content: str) -> int:
        conn = self.sqlite_service.connection()
        cursor = conn.execute(
            '''
            INSERT INTO profile_buffers (account_name, user_id, content, created_at, processed)
            VALUES (?, ?, ?, ?, 0)
            ''',
            (account_name, user_id, content, time.time()),
        )
        conn.commit()
        return int(cursor.lastrowid)

    def list_pending_buffers(self, account_name: str, user_id: str, limit: int = 20) -> list[dict]:
        rows = self.sqlite_service.connection().execute(
            '''
            SELECT id, content, created_at
            FROM profile_buffers
            WHERE account_name = ? AND user_id = ? AND processed = 0
            ORDER BY created_at ASC LIMIT ?
            ''',
            (account_name, user_id, max(1, int(limit))),
        ).fetchall()
        return [dict(row) for row in rows]

    def mark_buffers_processed(self, buffer_ids: list[int]) -> None:
        if not buffer_ids:
            return
        conn = self.sqlite_service.connection()
        placeholders = ','.join('?' for _ in buffer_ids)
        conn.execute(
            f'UPDATE profile_buffers SET processed = 1 WHERE id IN ({placeholders})',
            buffer_ids,
        )
        conn.commit()

    def forget_user(self, account_name: str, user_id: str) -> None:
        conn = self.sqlite_service.connection()
        conn.execute('DELETE FROM user_profiles WHERE account_name = ? AND user_id = ?', (account_name, user_id))
        conn.execute('DELETE FROM profile_facts WHERE account_name = ? AND user_id = ?', (account_name, user_id))
        conn.execute('DELETE FROM profile_buffers WHERE account_name = ? AND user_id = ?', (account_name, user_id))
        conn.commit()

    def list_profiles(self, account_name: str, limit: int = 50) -> list[dict]:
        rows = self.sqlite_service.connection().execute(
            '''
            SELECT account_name, user_id, summary, fondness, trust, patience, respect,
                   interest, familiarity, message_count, updated_at
            FROM user_profiles
            WHERE account_name = ?
            ORDER BY updated_at DESC LIMIT ?
            ''',
            (account_name, max(1, int(limit))),
        ).fetchall()
        return [dict(row) for row in rows]

    def set_birthday(
        self,
        account_name: str,
        user_id: str,
        *,
        month: int,
        day: int,
        channel_id: str = '',
        username: str = '',
        display_name: str = '',
    ) -> dict:
        profile = self.get_or_create_profile(account_name, user_id)
        updates = {
            'birthday_month': int(month),
            'birthday_day': int(day),
            'birthday_channel_id': str(channel_id or '').strip(),
            'birthday_username': str(username or '').strip(),
            'birthday_display_name': str(display_name or username or '').strip(),
            'updated_at': time.time(),
        }
        assignments = ', '.join(f'{key} = ?' for key in updates)
        params = list(updates.values()) + [account_name, user_id]
        conn = self.sqlite_service.connection()
        conn.execute(
            f'UPDATE user_profiles SET {assignments} WHERE account_name = ? AND user_id = ?',
            params,
        )
        conn.commit()
        return dict(conn.execute(
            'SELECT * FROM user_profiles WHERE account_name = ? AND user_id = ?',
            (account_name, user_id),
        ).fetchone())

    def list_birthdays_on_date(self, account_name: str, month: int, day: int, *, limit: int = 50) -> list[dict]:
        rows = self.sqlite_service.connection().execute(
            '''
            SELECT account_name, user_id, birthday_month, birthday_day, birthday_channel_id,
                   birthday_username, birthday_display_name, last_birthday_wish_year,
                   birthday_wish_run_at
            FROM user_profiles
            WHERE account_name = ?
              AND birthday_month = ?
              AND birthday_day = ?
              AND birthday_month > 0
              AND birthday_day > 0
            ORDER BY updated_at DESC
            LIMIT ?
            ''',
            (account_name, int(month), int(day), max(1, int(limit))),
        ).fetchall()
        return [dict(row) for row in rows]

    def set_birthday_wish_run_at(self, account_name: str, user_id: str, run_at: float) -> None:
        conn = self.sqlite_service.connection()
        conn.execute(
            '''
            UPDATE user_profiles
            SET birthday_wish_run_at = ?, updated_at = ?
            WHERE account_name = ? AND user_id = ?
            ''',
            (float(run_at), time.time(), account_name, user_id),
        )
        conn.commit()

    def mark_birthday_wished(self, account_name: str, user_id: str, year: int) -> None:
        conn = self.sqlite_service.connection()
        conn.execute(
            '''
            UPDATE user_profiles
            SET last_birthday_wish_year = ?, updated_at = ?
            WHERE account_name = ? AND user_id = ?
            ''',
            (int(year), time.time(), account_name, user_id),
        )
        conn.commit()

    def get_affect(self, account_name: str) -> AgentAffect:
        row = self.sqlite_service.connection().execute(
            'SELECT * FROM agent_affect WHERE account_name = ?',
            (account_name,),
        ).fetchone()
        if not row:
            return AgentAffect()
        return AgentAffect.from_dict(dict(row))

    def save_affect(self, account_name: str, affect: AgentAffect) -> AgentAffect:
        conn = self.sqlite_service.connection()
        payload = affect.to_dict()
        conn.execute(
            '''
            INSERT INTO agent_affect
            (account_name, energy, sociability, irritability, playfulness, stress, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(account_name) DO UPDATE SET
                energy = excluded.energy,
                sociability = excluded.sociability,
                irritability = excluded.irritability,
                playfulness = excluded.playfulness,
                stress = excluded.stress,
                updated_at = excluded.updated_at
            ''',
            (
                account_name,
                payload['energy'],
                payload['sociability'],
                payload['irritability'],
                payload['playfulness'],
                payload['stress'],
                time.time(),
            ),
        )
        conn.commit()
        return self.get_affect(account_name)

    def relationship_from_row(self, row: dict) -> RelationshipState:
        return RelationshipState.from_dict(row)

    def upsert_activation(self, entity_type: str, entity_id: str, account_name: str, score: float) -> None:
        conn = self.sqlite_service.connection()
        conn.execute(
            '''
            INSERT INTO activation_scores (entity_type, entity_id, account_name, score, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(entity_type, entity_id, account_name) DO UPDATE SET
                score = excluded.score,
                updated_at = excluded.updated_at
            ''',
            (entity_type, entity_id, account_name, score, time.time()),
        )
        conn.commit()

    def list_activation(self, account_name: str, limit: int = 10) -> list[dict]:
        rows = self.sqlite_service.connection().execute(
            '''
            SELECT entity_type, entity_id, score, updated_at
            FROM activation_scores
            WHERE account_name = ?
            ORDER BY score DESC LIMIT ?
            ''',
            (account_name, max(1, int(limit))),
        ).fetchall()
        return [dict(row) for row in rows]

    def decay_activation(self, account_name: str, factor: float) -> None:
        conn = self.sqlite_service.connection()
        conn.execute(
            '''
            UPDATE activation_scores
            SET score = score * ?, updated_at = ?
            WHERE account_name = ?
            ''',
            (factor, time.time(), account_name),
        )
        conn.commit()
