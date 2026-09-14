"""User profile, facts, and birthday repository."""

from __future__ import annotations

import time


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
            'birthday_wish_run_at', 'username', 'display_name',
            'first_seen_at', 'last_interaction_at',
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

    def add_fact(self, account_name: str, user_id: str, content: str, *, source: str = 'explicit',
                 confidence: float = 1.0, origin: str = '') -> int:
        """origin = the guild id the fact was learned in, 'dm' for a direct
        message, '' for operator/legacy rows (visible everywhere). Facts learned
        in DMs never reach a server prompt (H16b, hunt 2026-09-12)."""
        now = time.time()
        conn = self.sqlite_service.connection()
        cursor = conn.execute(
            '''
            INSERT INTO profile_facts
            (account_name, user_id, content, confidence, source, created_at, pinned, forgotten, updated_at, origin)
            VALUES (?, ?, ?, ?, ?, ?, 0, 0, ?, ?)
            ''',
            (account_name, user_id, content, confidence, source, now, now, str(origin or '')),
        )
        conn.commit()
        return int(cursor.lastrowid)

    def get_fact(self, fact_id: int) -> dict | None:
        row = self.sqlite_service.connection().execute(
            'SELECT * FROM profile_facts WHERE id = ?',
            (int(fact_id),),
        ).fetchone()
        return dict(row) if row else None

    def list_facts(
        self,
        account_name: str,
        user_id: str,
        limit: int = 20,
        *,
        include_forgotten: bool = False,
        for_guild: str | None = None,
    ) -> list[dict]:
        """for_guild: the server this list is FOR — facts learned in DMs are
        left out. None = a DM or the operator UI: everything."""
        query = '''
            SELECT id, content, confidence, source, created_at, pinned, forgotten, updated_at, origin
            FROM profile_facts
            WHERE account_name = ? AND user_id = ?
        '''
        params: list = [account_name, user_id]
        if not include_forgotten:
            query += ' AND forgotten = 0'
        if for_guild:
            query += " AND origin != 'dm'"
        # Pinned first, then freshest.
        query += ' ORDER BY pinned DESC, created_at DESC LIMIT ?'
        params.append(max(1, int(limit)))
        rows = self.sqlite_service.connection().execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def list_recent_facts(
        self,
        account_name: str,
        *,
        source: str = 'ambient_distill',
        pending_only: bool = True,
        limit: int = 40,
    ) -> list[dict]:
        """Cross-user fact list for operator review queues."""
        query = '''
            SELECT f.id, f.account_name, f.user_id, f.content, f.confidence, f.source,
                   f.created_at, f.pinned, f.forgotten, f.updated_at,
                   p.username, p.display_name
            FROM profile_facts f
            LEFT JOIN user_profiles p
              ON p.account_name = f.account_name AND p.user_id = f.user_id
            WHERE f.account_name = ? AND f.forgotten = 0
        '''
        params: list = [account_name]
        if source:
            query += ' AND f.source = ?'
            params.append(str(source))
        if pending_only:
            # Unpinned ambient facts = waiting for operator approve/reject.
            query += ' AND f.pinned = 0'
        query += ' ORDER BY f.created_at DESC LIMIT ?'
        params.append(max(1, min(100, int(limit))))
        rows = self.sqlite_service.connection().execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def update_fact_content(self, fact_id: int, content: str) -> dict | None:
        conn = self.sqlite_service.connection()
        conn.execute(
            'UPDATE profile_facts SET content = ?, updated_at = ? WHERE id = ?',
            (content, time.time(), int(fact_id)),
        )
        conn.commit()
        return self.get_fact(fact_id)

    def set_fact_pinned(self, fact_id: int, pinned: bool) -> dict | None:
        conn = self.sqlite_service.connection()
        conn.execute(
            'UPDATE profile_facts SET pinned = ?, updated_at = ? WHERE id = ?',
            (1 if pinned else 0, time.time(), int(fact_id)),
        )
        conn.commit()
        return self.get_fact(fact_id)

    def soft_forget_fact(self, fact_id: int) -> dict | None:
        """Hide a fact from recall without deleting the user profile."""
        conn = self.sqlite_service.connection()
        conn.execute(
            'UPDATE profile_facts SET forgotten = 1, updated_at = ? WHERE id = ?',
            (time.time(), int(fact_id)),
        )
        conn.commit()
        return self.get_fact(fact_id)

    def restore_fact(self, fact_id: int) -> dict | None:
        conn = self.sqlite_service.connection()
        conn.execute(
            'UPDATE profile_facts SET forgotten = 0, updated_at = ? WHERE id = ?',
            (time.time(), int(fact_id)),
        )
        conn.commit()
        return self.get_fact(fact_id)

    def delete_fact(self, fact_id: int) -> bool:
        conn = self.sqlite_service.connection()
        cursor = conn.execute('DELETE FROM profile_facts WHERE id = ?', (int(fact_id),))
        conn.commit()
        return cursor.rowcount > 0

    def delete_facts(self, account_name: str, user_id: str, *, match: str) -> int:
        """Delete facts whose content contains `match`. Empty match deletes
        nothing — a full wipe is forget_user, a deliberate human action."""
        match = str(match or '').strip()
        if not match:
            return 0
        conn = self.sqlite_service.connection()
        cursor = conn.execute(
            'DELETE FROM profile_facts WHERE account_name = ? AND user_id = ? AND content LIKE ?',
            (account_name, user_id, f'%{match}%'),
        )
        conn.commit()
        return cursor.rowcount

    def search_facts(self, account_name: str, query: str, *, limit: int = 20) -> list[dict]:
        like = f"%{str(query or '').strip()}%"
        rows = self.sqlite_service.connection().execute(
            '''
            SELECT f.id, f.user_id, f.content, f.source, f.created_at, f.pinned, f.forgotten,
                   p.username, p.display_name, p.birthday_username, p.birthday_display_name
            FROM profile_facts f
            LEFT JOIN user_profiles p
              ON p.account_name = f.account_name AND p.user_id = f.user_id
            WHERE f.account_name = ? AND f.content LIKE ? AND f.forgotten = 0
            ORDER BY f.pinned DESC, f.created_at DESC LIMIT ?
            ''',
            (account_name, like, max(1, int(limit))),
        ).fetchall()
        return [dict(row) for row in rows]

    def find_user(self, account_name: str, ref: str) -> dict | None:
        """Resolve a user by id, username, or display name (case-insensitive)."""
        ref = str(ref or '').strip().lstrip('@')
        if not ref:
            return None
        conn = self.sqlite_service.connection()
        row = conn.execute(
            'SELECT * FROM user_profiles WHERE account_name = ? AND user_id = ?',
            (account_name, ref),
        ).fetchone()
        if row:
            return dict(row)
        row = conn.execute(
            '''
            SELECT * FROM user_profiles
            WHERE account_name = ?
              AND (LOWER(username) = LOWER(?) OR LOWER(display_name) = LOWER(?)
                   OR LOWER(birthday_username) = LOWER(?) OR LOWER(birthday_display_name) = LOWER(?))
            ORDER BY message_count DESC LIMIT 1
            ''',
            (account_name, ref, ref, ref, ref),
        ).fetchone()
        return dict(row) if row else None

    def forget_user(self, account_name: str, user_id: str) -> None:
        conn = self.sqlite_service.connection()
        conn.execute('DELETE FROM user_profiles WHERE account_name = ? AND user_id = ?', (account_name, user_id))
        conn.execute('DELETE FROM profile_facts WHERE account_name = ? AND user_id = ?', (account_name, user_id))
        conn.execute('DELETE FROM profile_buffers WHERE account_name = ? AND user_id = ?', (account_name, user_id))
        conn.commit()

    def list_profiles(self, account_name: str, limit: int = 50) -> list[dict]:
        rows = self.sqlite_service.connection().execute(
            '''
            SELECT account_name, user_id, username, display_name, summary,
                   fondness, trust, patience, respect, interest, familiarity,
                   message_count, birthday_month, birthday_day,
                   birthday_username, birthday_display_name, updated_at,
                   first_seen_at, last_interaction_at
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
