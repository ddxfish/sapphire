"""Media artifact persistence."""

from __future__ import annotations

import json
import time


class MediaRepository:
    def __init__(self, sqlite_service):
        self.sqlite_service = sqlite_service

    def save_artifact(self, artifact) -> int:
        conn = self.sqlite_service.connection()
        cursor = conn.execute(
            '''
            INSERT INTO media_artifacts
            (message_id, account_name, channel_id, media_kind, source_url, filename,
             content_type, raw_metadata_json, interpreted_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                artifact.message_id,
                artifact.account_name,
                artifact.channel_id,
                artifact.media_kind,
                artifact.source_url,
                artifact.filename,
                artifact.content_type,
                json.dumps(artifact.raw_metadata or {}),
                json.dumps(artifact.interpretation or {}),
                time.time(),
            ),
        )
        conn.commit()
        return int(cursor.lastrowid)

    def _row_to_item(self, row) -> dict:
        item = dict(row)
        item['raw_metadata'] = json.loads(item.pop('raw_metadata_json', '{}') or '{}')
        item['interpretation'] = json.loads(item.pop('interpreted_json', '{}') or '{}')
        return item

    def get_by_message(self, message_id: str) -> list[dict]:
        rows = self.sqlite_service.connection().execute(
            'SELECT * FROM media_artifacts WHERE message_id = ? ORDER BY id ASC',
            (message_id,),
        ).fetchall()
        return [self._row_to_item(row) for row in rows]

    def get_by_message_ids(self, message_ids: list[str]) -> dict[str, list[dict]]:
        ids = [str(message_id) for message_id in message_ids if str(message_id)]
        if not ids:
            return {}
        placeholders = ','.join('?' * len(ids))
        rows = self.sqlite_service.connection().execute(
            f'SELECT * FROM media_artifacts WHERE message_id IN ({placeholders}) ORDER BY id ASC',
            ids,
        ).fetchall()
        grouped: dict[str, list[dict]] = {}
        for row in rows:
            item = self._row_to_item(row)
            grouped.setdefault(str(item['message_id']), []).append(item)
        return grouped

    def get_recent_message_id(self, channel_id: str, *, max_age_seconds: float = 300) -> str | None:
        cutoff = time.time() - max_age_seconds
        row = self.sqlite_service.connection().execute(
            '''
            SELECT message_id FROM media_artifacts
            WHERE channel_id = ? AND created_at >= ?
            ORDER BY id DESC
            LIMIT 1
            ''',
            (channel_id, cutoff),
        ).fetchone()
        return str(row['message_id']) if row else None
