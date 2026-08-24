"""Trace repository."""

from __future__ import annotations

import json
import time


class TraceRepository:
    def __init__(self, sqlite_service):
        self.sqlite_service = sqlite_service

    def record_trace(self, trace_type: str, summary: str, detail: dict | None = None) -> None:
        conn = self.sqlite_service.connection()
        conn.execute(
            'INSERT INTO traces (trace_type, summary, detail_json, created_at) VALUES (?, ?, ?, ?)',
            (trace_type, summary, json.dumps(detail or {}), time.time()),
        )
        conn.commit()

    def list_traces(self, limit: int = 50, *, trace_type: str | None = None) -> list[dict]:
        query = 'SELECT id, trace_type, summary, detail_json, created_at FROM traces'
        params: list = []
        if trace_type:
            query += ' WHERE trace_type = ?'
            params.append(trace_type)
        query += ' ORDER BY created_at DESC LIMIT ?'
        params.append(max(1, min(200, int(limit))))
        rows = self.sqlite_service.connection().execute(query, params).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item['detail'] = json.loads(item.pop('detail_json') or '{}')
            items.append(item)
        return items

    def purge_before(self, cutoff_ts: float) -> int:
        conn = self.sqlite_service.connection()
        cursor = conn.execute('DELETE FROM traces WHERE created_at < ?', (cutoff_ts,))
        conn.commit()
        return int(cursor.rowcount)
