"""Optional import utility from leona_discord SQLite data."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path


SETTINGS_MAP = {
    'greeting_enabled': ('proactive', 'greeting_enabled'),
    'greeting_utc_hour': ('proactive', 'greeting_utc_hour'),
    'greeting_targets': ('proactive', 'greeting_targets'),
    'greeting_message': ('proactive', 'greeting_message'),
    'greeting_fallback': ('proactive', 'greeting_fallback'),
    'greeting_use_llm': ('proactive', 'greeting_use_llm'),
    'greeting_model_provider': ('proactive', 'greeting_model_provider'),
    'greeting_model_name': ('proactive', 'greeting_model_name'),
    'greeting_max_tokens': ('proactive', 'greeting_max_tokens'),
    'outreach_enabled': ('proactive', 'outreach_enabled'),
    'sleep_schedule_enabled': ('proactive', 'sleep_schedule_enabled'),
    'sleep_utc_hour': ('proactive', 'sleep_utc_hour'),
    'sleep_message': ('proactive', 'goodnight_message'),
    'sleep_fallback': ('proactive', 'goodnight_fallback'),
    'sleep_use_llm': ('proactive', 'goodnight_use_llm'),
    'sleep_model_provider': ('proactive', 'goodnight_model_provider'),
    'sleep_model_name': ('proactive', 'goodnight_model_name'),
    'sleep_max_tokens': ('proactive', 'goodnight_max_tokens'),
    'quiet_hours_enabled': ('safety', 'quiet_hours_enabled'),
    'quiet_hours_start': ('safety', 'quiet_hours_start'),
    'quiet_hours_end': ('safety', 'quiet_hours_end'),
    'gif_enabled': ('media', 'gif_enabled'),
    'gif_api_key': ('media', 'gif_api_key'),
    'gif_provider': ('media', 'gif_provider'),
    'gif_content_filter': ('media', 'gif_content_filter'),
    'tenor_api_key': ('media', 'gif_api_key'),
    'tenor_content_filter': ('media', 'gif_content_filter'),
    'gif_replies_enabled': ('media', 'gif_enabled'),
    'name_match_enabled': ('channel', 'name_match_enabled'),
    'name_match_case_sensitive': ('channel', 'name_match_case_sensitive'),
    'image_enabled': ('media', 'image_understanding_enabled'),
    'profiling_enabled': ('profile', 'enabled'),
    'presence_cycling_enabled': ('presence', 'cycling_enabled'),
}


class LeonaImportService:
    SOURCE = 'leona_discord'

    def __init__(self, *, leona_db_path, memory_repository, profile_repository, sqlite_service, settings_repository=None):
        self.leona_db_path = Path(leona_db_path)
        self.memory_repository = memory_repository
        self.profile_repository = profile_repository
        self.sqlite_service = sqlite_service
        self.settings_repository = settings_repository

    def run(self, *, include: list[str] | None = None, leona_settings: dict | None = None) -> dict:
        include = include or ['pinned_memories', 'profile_facts', 'profile_summaries', 'settings']
        results = {'imported': [], 'skipped': []}
        if 'settings' in include and leona_settings:
            results['settings'] = self._import_settings(leona_settings)
        if not self.leona_db_path.exists():
            results['error'] = 'leona database not found'
            return results
        conn = sqlite3.connect(str(self.leona_db_path))
        conn.row_factory = sqlite3.Row
        if 'pinned_memories' in include:
            results['pinned_memories'] = self._import_pinned(conn)
        if 'profile_facts' in include:
            results['profile_facts'] = self._import_facts(conn)
        if 'profile_summaries' in include:
            results['profile_summaries'] = self._import_summaries(conn)
        conn.close()
        return results

    def _audit_key_seen(self, import_key: str) -> bool:
        row = self.sqlite_service.connection().execute(
            'SELECT 1 FROM import_audit WHERE source = ? AND import_key = ?',
            (self.SOURCE, import_key),
        ).fetchone()
        return row is not None

    def _record_audit(self, import_key: str, status: str, detail: dict | None = None) -> None:
        conn = self.sqlite_service.connection()
        conn.execute(
            '''
            INSERT INTO import_audit (source, import_key, status, detail_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(source, import_key) DO UPDATE SET
                status = excluded.status,
                detail_json = excluded.detail_json,
                created_at = excluded.created_at
            ''',
            (self.SOURCE, import_key, status, json.dumps(detail or {}), time.time()),
        )
        conn.commit()

    def _import_pinned(self, conn) -> int:
        imported = 0
        rows = conn.execute('SELECT * FROM pinned_memories').fetchall()
        for row in rows:
            key = f"pinned:{row['account']}:{row['id']}"
            if self._audit_key_seen(key):
                continue
            self.memory_repository.pin_memory(
                row['account'],
                row['guild_id'] or '',
                row['channel_id'] or '',
                row['author_id'] or '',
                row['username'] or '',
                row['content'],
            )
            self._record_audit(key, 'imported')
            imported += 1
        return imported

    def _import_facts(self, conn) -> int:
        imported = 0
        rows = conn.execute('SELECT * FROM profile_facts').fetchall()
        for row in rows:
            key = f"fact:{row['account']}:{row['id']}"
            if self._audit_key_seen(key):
                continue
            content = f"{row['fact_key']}: {row['fact_value']}".strip(': ').strip()
            self.profile_repository.add_fact(
                row['account'],
                row['author_id'],
                content,
                source='leona_import',
                confidence=float(row['confidence'] or 0.7),
            )
            self._record_audit(key, 'imported')
            imported += 1
        return imported

    def _import_summaries(self, conn) -> int:
        imported = 0
        rows = conn.execute('SELECT * FROM user_profiles').fetchall()
        for row in rows:
            key = f"summary:{row['account']}:{row['author_id']}"
            if self._audit_key_seen(key):
                continue
            summary = ''
            row_dict = dict(row)
            summary = (row_dict.get('summary_l1') or row_dict.get('summary_l2') or '').strip()
            if summary:
                self.profile_repository.update_profile(
                    row['account'],
                    row['author_id'],
                    summary=summary,
                    fondness=float(row['warmth'] or 0.5),
                    message_count=int(row['message_count'] or 0),
                )
                self._record_audit(key, 'imported')
                imported += 1
        return imported

    def _import_settings(self, leona_settings: dict) -> dict:
        global_settings = leona_settings.get('global') or {}
        overlay = {'proactive': {}, 'safety': {}, 'media': {}, 'profile': {}, 'presence': {}}
        mapped = 0
        for leona_key, (section, cognitive_key) in SETTINGS_MAP.items():
            if leona_key not in global_settings:
                continue
            overlay[section][cognitive_key] = global_settings[leona_key]
            mapped += 1
        if self.settings_repository and mapped:
            from plugins.discord.models.settings import SettingsOverlay
            self.settings_repository.save_settings_override('global', 'global', SettingsOverlay.from_dict(overlay))
        return {'mapped': mapped, 'overlay': overlay}
