"""Trace repository."""

from __future__ import annotations

import json
import time

# Count cap independent of the retention switch (default OFF = never purged).
# Traces are a flight recorder — 4+ rows per inbound message — and the Traces
# tab sorts the table twice per 15 s (hunt 2026-09-12, C3). Trimmed every
# _TRIM_EVERY inserts, at most _TRIM_CHUNK rows per pass, so a big backlog
# drains without ever holding the shared connection for long.
TRACE_ROW_CAP = 20_000
_TRIM_EVERY = 500
_TRIM_CHUNK = 5_000


class TraceRepository:
    def __init__(self, sqlite_service):
        self.sqlite_service = sqlite_service
        self._since_trim = 0

    def record_trace(self, trace_type: str, summary: str, detail: dict | None = None) -> None:
        conn = self.sqlite_service.connection()
        conn.execute(
            'INSERT INTO traces (trace_type, summary, detail_json, created_at) VALUES (?, ?, ?, ?)',
            (trace_type, summary, json.dumps(detail or {}), time.time()),
        )
        self._since_trim += 1
        if self._since_trim >= _TRIM_EVERY:
            self._since_trim = 0
            self._trim(conn)
        conn.commit()

    def _trim(self, conn) -> None:
        boundary = conn.execute(
            'SELECT id FROM traces ORDER BY id DESC LIMIT 1 OFFSET ?', (TRACE_ROW_CAP,),
        ).fetchone()
        if not boundary:
            return
        oldest = conn.execute('SELECT MIN(id) FROM traces').fetchone()[0] or 0
        conn.execute(
            'DELETE FROM traces WHERE id <= ?',
            (min(int(boundary[0]), int(oldest) + _TRIM_CHUNK),),
        )

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
