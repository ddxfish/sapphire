"""Task persistence for world model."""

from __future__ import annotations

import json
import time


class TaskRepository:
    def __init__(self, sqlite_service):
        self.sqlite_service = sqlite_service

    def create_task(
        self,
        account_name: str,
        task_type: str,
        *,
        target_id: str = '',
        reason: str = '',
        status: str = 'pending',
        urgency: float = 0.5,
        confidence: float = 0.5,
        expires_at: float = 0.0,
        run_at: float = 0.0,
        payload: dict | None = None,
    ) -> int:
        conn = self.sqlite_service.connection()
        cursor = conn.execute(
            '''
            INSERT INTO tasks
            (account_name, task_type, target_id, reason, status, urgency, confidence,
             created_at, expires_at, run_at, payload_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                account_name,
                task_type,
                target_id,
                reason,
                status,
                urgency,
                confidence,
                time.time(),
                expires_at,
                float(run_at or 0),
                json.dumps(payload or {}),
            ),
        )
        conn.commit()
        return int(cursor.lastrowid)

    def has_pending(
        self,
        account_name: str,
        task_type: str,
        *,
        target_id: str = '',
        payload_contains: str = '',
        since: float = 0.0,
    ) -> bool:
        """A pending task of this type for this target (and payload text) created
        after `since` — the dedupe behind social check-ins (H7)."""
        query = "SELECT 1 FROM tasks WHERE account_name = ? AND task_type = ? AND status = 'pending'"
        params: list = [account_name, task_type]
        if target_id:
            query += ' AND target_id = ?'
            params.append(str(target_id))
        if payload_contains:
            query += ' AND payload_json LIKE ?'
            params.append(f'%{payload_contains}%')
        if since:
            query += ' AND created_at >= ?'
            params.append(float(since))
        row = self.sqlite_service.connection().execute(query + ' LIMIT 1', params).fetchone()
        return row is not None

    def list_tasks(
        self,
        account_name: str,
        *,
        status: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        query = 'SELECT * FROM tasks WHERE account_name = ?'
        params: list = [account_name]
        if status is not None:
            query += ' AND status = ?'
            params.append(status)
        query += ' ORDER BY created_at DESC LIMIT ?'
        params.append(max(1, int(limit)))
        rows = self.sqlite_service.connection().execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def list_due_tasks(
        self,
        account_name: str,
        *,
        now_ts: float | None = None,
        limit: int = 10,
    ) -> list[dict]:
        now_ts = float(now_ts if now_ts is not None else time.time())
        rows = self.sqlite_service.connection().execute(
            '''
            SELECT * FROM tasks
            WHERE account_name = ?
              AND status = 'pending'
              AND (run_at <= 0 OR run_at <= ?)
              AND (expires_at <= 0 OR expires_at > ?)
            ORDER BY run_at ASC, created_at ASC
            LIMIT ?
            ''',
            (account_name, now_ts, now_ts, max(1, int(limit))),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_task(self, task_id: int) -> dict | None:
        row = self.sqlite_service.connection().execute(
            'SELECT * FROM tasks WHERE id = ?',
            (int(task_id),),
        ).fetchone()
        return dict(row) if row else None

    def update_task_status(self, task_id: int, status: str) -> None:
        conn = self.sqlite_service.connection()
        conn.execute('UPDATE tasks SET status = ? WHERE id = ?', (status, task_id))
        conn.commit()
