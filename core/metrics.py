"""Token usage metrics — per-LLM-call storage and aggregation."""

import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, List

import logging
logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "user" / "metrics" / "token_usage.db"


class TokenMetrics:
    """Thread-safe token usage recorder with SQLite backend."""

    def __init__(self):
        self._lock = threading.RLock()
        self._init_db()

    def _init_db(self):
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            conn = sqlite3.connect(str(DB_PATH))
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS token_usage (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    chat_name TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    call_type TEXT NOT NULL DEFAULT 'conversation',
                    prompt_tokens INTEGER DEFAULT 0,
                    completion_tokens INTEGER DEFAULT 0,
                    thinking_tokens INTEGER DEFAULT 0,
                    cache_read_tokens INTEGER DEFAULT 0,
                    cache_write_tokens INTEGER DEFAULT 0,
                    total_tokens INTEGER DEFAULT 0,
                    duration_seconds REAL DEFAULT 0,
                    estimated INTEGER DEFAULT 0
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_token_usage_ts
                ON token_usage(timestamp)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_token_usage_model
                ON token_usage(model)
            """)
            conn.commit()
            conn.close()

    def record(self, chat_name: str, provider: str, model: str,
               call_type: str, metadata: Dict, estimated: bool = False):
        """Record a single LLM call's token usage from metadata dict."""
        try:
            import config
            if not getattr(config, 'METRICS_ENABLED', True):
                return
        except Exception:
            pass
        tokens = metadata.get("tokens", {})
        duration = metadata.get("duration_seconds", 0)

        row = (
            datetime.now().isoformat(),
            chat_name,
            provider,
            model,
            call_type,
            tokens.get("prompt", 0),
            tokens.get("content", 0),
            tokens.get("thinking", 0),
            tokens.get("cache_read_tokens", 0),
            tokens.get("cache_write_tokens", 0),
            tokens.get("total", 0),
            duration,
            1 if estimated else 0
        )

        try:
            with self._lock:
                conn = sqlite3.connect(str(DB_PATH))
                conn.execute("""
                    INSERT INTO token_usage
                    (timestamp, chat_name, provider, model, call_type,
                     prompt_tokens, completion_tokens, thinking_tokens,
                     cache_read_tokens, cache_write_tokens, total_tokens,
                     duration_seconds, estimated)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, row)
                conn.commit()
                conn.close()
        except Exception as e:
            logger.error(f"[METRICS] Failed to record: {e}")

    def summary(self, days: int = 30) -> Dict:
        """Aggregate usage summary for the last N days."""
        cutoff = datetime.now().replace(hour=0, minute=0, second=0)
        # Go back N days from start of today
        from datetime import timedelta
        cutoff = (cutoff - timedelta(days=days)).isoformat()

        try:
            with self._lock:
                conn = sqlite3.connect(str(DB_PATH))
                conn.row_factory = sqlite3.Row
                cur = conn.execute("""
                    SELECT
                        COUNT(*) as total_calls,
                        COALESCE(SUM(prompt_tokens), 0) as total_prompt,
                        COALESCE(SUM(completion_tokens), 0) as total_completion,
                        COALESCE(SUM(thinking_tokens), 0) as total_thinking,
                        COALESCE(SUM(cache_read_tokens), 0) as total_cache_read,
                        COALESCE(SUM(cache_write_tokens), 0) as total_cache_write,
                        COALESCE(SUM(total_tokens), 0) as total_tokens,
                        COALESCE(SUM(duration_seconds), 0) as total_duration
                    FROM token_usage
                    WHERE timestamp >= ?
                """, (cutoff,))
                row = dict(cur.fetchone())
                conn.close()
                return row
        except Exception as e:
            logger.error(f"[METRICS] Summary failed: {e}")
            return {}

    def breakdown_by_model(self, days: int = 30) -> List[Dict]:
        """Token usage grouped by model for the last N days."""
        from datetime import timedelta
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()

        try:
            with self._lock:
                conn = sqlite3.connect(str(DB_PATH))
                conn.row_factory = sqlite3.Row
                cur = conn.execute("""
                    SELECT
                        provider, model,
                        COUNT(*) as calls,
                        COALESCE(SUM(prompt_tokens), 0) as prompt,
                        COALESCE(SUM(completion_tokens), 0) as completion,
                        COALESCE(SUM(thinking_tokens), 0) as thinking,
                        COALESCE(SUM(cache_read_tokens), 0) as cache_read,
                        COALESCE(SUM(cache_write_tokens), 0) as cache_write,
                        COALESCE(SUM(total_tokens), 0) as total,
                        COALESCE(SUM(duration_seconds), 0) as duration
                    FROM token_usage
                    WHERE timestamp >= ?
                    GROUP BY provider, model
                    ORDER BY total DESC
                """, (cutoff,))
                rows = [dict(r) for r in cur.fetchall()]
                conn.close()
                return rows
        except Exception as e:
            logger.error(f"[METRICS] Breakdown failed: {e}")
            return []

    def daily_usage(self, days: int = 30) -> List[Dict]:
        """Daily token totals for charting."""
        from datetime import timedelta
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()

        try:
            with self._lock:
                conn = sqlite3.connect(str(DB_PATH))
                conn.row_factory = sqlite3.Row
                cur = conn.execute("""
                    SELECT
                        DATE(timestamp) as date,
                        COALESCE(SUM(total_tokens), 0) as tokens,
                        COALESCE(SUM(cache_read_tokens), 0) as cache_read,
                        COUNT(*) as calls
                    FROM token_usage
                    WHERE timestamp >= ?
                    GROUP BY DATE(timestamp)
                    ORDER BY date
                """, (cutoff,))
                rows = [dict(r) for r in cur.fetchall()]
                conn.close()
                return rows
        except Exception as e:
            logger.error(f"[METRICS] Daily usage failed: {e}")
            return []


# Singleton
metrics = TokenMetrics()
