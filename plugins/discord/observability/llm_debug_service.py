"""In-memory ring buffer of recent LLM prompt/response exchanges for operator debug."""

from __future__ import annotations

import threading
import time
import uuid
from collections import deque


def _preview(text: str, limit: int = 240) -> str:
    normalized = ' '.join(str(text or '').split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(0, limit - 1)] + '…'


def _source_label(payload: dict) -> str:
    proactive_kind = str(payload.get('proactive_kind') or '').strip()
    if proactive_kind:
        return f'proactive:{proactive_kind}'
    return 'discord_message'


class LlmDebugService:
    def __init__(self, *, limit: int = 10, plugin_loader=None):
        self._limit = max(1, int(limit))
        self._entries: deque[dict] = deque(maxlen=self._limit)
        self._index: dict[str, dict] = {}
        self._lock = threading.Lock()
        self.plugin_loader = plugin_loader

    @property
    def enabled(self) -> bool:
        """Opt-in (F11): the ring holds full prompts. Read live from the plugin
        setting so the Cognition-tab toggle applies without a restart. No real
        loader (unit tests, standalone) = on."""
        getter = getattr(self.plugin_loader, 'get_plugin_settings', None)
        if not callable(getter):
            return True
        try:
            value = (getter('discord') or {}).get('debug.llm_debug_enabled', False)
        except Exception:
            return False
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ('1', 'true', 'yes', 'on')

    def clear(self) -> int:
        with self._lock:
            count = len(self._entries)
            self._entries.clear()
            self._index.clear()
        return count

    def list_entries(self, *, limit: int | None = None) -> list[dict]:
        cap = self._limit if limit is None else max(1, min(self._limit, int(limit)))
        with self._lock:
            return [dict(item) for item in list(self._entries)[-cap:]][::-1]

    def _append_entry(self, entry: dict) -> None:
        if not self.enabled:
            return
        entry_id = str(entry.get('id') or '')
        existing = self._index.get(entry_id)
        if existing:
            existing.update({
                'updated_at': entry.get('updated_at', existing.get('updated_at')),
                'prompt': entry.get('prompt', existing.get('prompt')),
                'trigger': entry.get('trigger', existing.get('trigger')),
                'source': entry.get('source', existing.get('source')),
                'llm': entry.get('llm', existing.get('llm')),
            })
            return
        if len(self._entries) >= self._limit:
            evicted = self._entries[0]
            self._index.pop(str(evicted.get('id') or ''), None)
        self._entries.append(entry)
        self._index[entry_id] = entry

    def _resolve_llm(self, task: dict | None, event_data: dict | None) -> dict[str, str]:
        """The daemon task's provider/model IS the brain (S0 retired the payload override)."""
        task = dict(task or {})
        return {
            'configured_primary': str(task.get('provider') or 'auto').strip() or 'auto',
            'configured_model': str(task.get('model') or '').strip(),
            'task_name': str(task.get('name') or ''),
            'task_id': str(task.get('id') or ''),
        }

    def record_rejection(
        self,
        *,
        message_id: str = '',
        account: str = '',
        guild_id: str = '',
        guild_name: str = '',
        channel_id: str = '',
        channel_name: str = '',
        username: str = '',
        author_id: str = '',
        content: str = '',
        reason: str,
        stage: str,
        detail: dict | None = None,
    ) -> None:
        now = time.time()
        entry_id = f'reject-{message_id or uuid.uuid4().hex[:10]}-{uuid.uuid4().hex[:6]}'
        entry = {
            'id': entry_id,
            'kind': 'rejection',
            'created_at': now,
            'updated_at': now,
            'source': 'discord_message',
            'account': account,
            'guild_id': guild_id,
            'guild_name': guild_name,
            'channel_id': channel_id,
            'channel_name': channel_name,
            'message_id': message_id,
            'trigger': {
                'username': username,
                'author_id': author_id,
                'content': content,
                'reason': reason,
            },
            'rejection': {
                'reason': reason,
                'stage': stage,
                'detail': dict(detail or {}),
            },
        }
        with self._lock:
            self._append_entry(entry)

    def record_prompt(self, payload: dict, *, extra: dict | None = None) -> None:
        payload = dict(payload or {})
        extra = dict(extra or {})
        message_id = str(payload.get('message_id') or '').strip() or f'prompt-{uuid.uuid4().hex[:12]}'
        now = time.time()
        entry = {
            'id': message_id,
            'kind': 'exchange',
            'created_at': now,
            'updated_at': now,
            'source': extra.get('source') or _source_label(payload),
            'account': str(payload.get('account') or ''),
            'guild_id': str(payload.get('guild_id') or ''),
            'guild_name': str(payload.get('guild_name') or ''),
            'channel_id': str(payload.get('channel_id') or ''),
            'channel_name': str(payload.get('channel_name') or ''),
            'message_id': message_id,
            'trigger': {
                'username': str(payload.get('username') or payload.get('display_name') or ''),
                'author_id': str(payload.get('author_id') or ''),
                'content': str(payload.get('content') or ''),
                'reason': str(extra.get('reason') or ''),
            },
            'prompt': {
                'user_content': str(payload.get('content') or ''),
                'recent_history': str(payload.get('recent_history') or ''),
                'reply_hints': list(payload.get('reply_hints') or []),
                'reply_instructions': str(payload.get('reply_instructions') or ''),
                'batch_size': int(payload.get('batch_size') or extra.get('batch_size') or 0),
            },
            'response': {
                'status': 'pending',
                'raw': '',
                'parsed_chunks': [],
                'strip_think_tags': None,
            },
            'delivery': {
                'typo_applied': False,
                'sent_text': '',
                'corrected_text': '',
                'edit_delay_seconds': 0.0,
                'edit_kind': '',
                'quote_reply_to': '',
                'chunks_sent': 0,
            },
            'timing': {
                'prompt_at': now,
                'response_at': None,
                'latency_ms': None,
            },
            'llm': self._resolve_llm({}, payload),
        }
        with self._lock:
            self._append_entry(entry)

    def record_voice_prompt(
        self,
        *,
        session_id: str,
        account_name: str,
        channel_id: str,
        prompt: str,
        user_text: str,
    ) -> str:
        entry_id = f'voice-{session_id}-{uuid.uuid4().hex[:8]}'
        now = time.time()
        entry = {
            'id': entry_id,
            'kind': 'exchange',
            'created_at': now,
            'updated_at': now,
            'source': 'voice',
            'account': account_name,
            'guild_id': '',
            'guild_name': '',
            'channel_id': channel_id,
            'channel_name': '',
            'message_id': entry_id,
            'trigger': {
                'username': 'voice',
                'author_id': '',
                'content': user_text,
                'reason': 'voice_turn',
            },
            'prompt': {
                'user_content': prompt,
                'recent_history': '',
                'reply_hints': [],
                'reply_instructions': '',
                'batch_size': 0,
            },
            'response': {
                'status': 'pending',
                'raw': '',
                'parsed_chunks': [],
                'strip_think_tags': None,
            },
            'delivery': {
                'typo_applied': False,
                'sent_text': '',
                'corrected_text': '',
                'edit_delay_seconds': 0.0,
                'edit_kind': '',
                'quote_reply_to': '',
                'chunks_sent': 0,
            },
            'timing': {
                'prompt_at': now,
                'response_at': None,
                'latency_ms': None,
            },
            'llm': self._resolve_llm({}, {}),
        }
        with self._lock:
            self._append_entry(entry)
        return entry_id

    def record_response(
        self,
        message_id: str,
        *,
        raw_text: str,
        parsed_chunks: list[str] | None = None,
        status: str = 'sent',
        strip_think_tags: bool | None = None,
        delivery: dict | None = None,
        task: dict | None = None,
        event_data: dict | None = None,
    ) -> None:
        message_id = str(message_id or '').strip()
        if not message_id:
            return
        now = time.time()
        with self._lock:
            entry = self._index.get(message_id)
            if not entry:
                return
            entry['updated_at'] = now
            entry['response'] = {
                'status': status,
                'raw': str(raw_text or ''),
                'parsed_chunks': list(parsed_chunks or []),
                'strip_think_tags': strip_think_tags,
            }
            entry['llm'] = self._resolve_llm(task, event_data)
            if task:
                entry['task'] = {
                    'name': str((task or {}).get('name') or ''),
                    'id': str((task or {}).get('id') or ''),
                }
            timing = entry.setdefault('timing', {})
            prompt_at = timing.get('prompt_at')
            timing['response_at'] = now
            if isinstance(prompt_at, (int, float)):
                timing['latency_ms'] = int(max(0.0, (now - prompt_at) * 1000))
            if delivery:
                entry['delivery'] = {
                    'typo_applied': bool(delivery.get('typo_applied')),
                    'sent_text': str(delivery.get('sent_text') or ''),
                    'corrected_text': str(delivery.get('corrected_text') or ''),
                    'edit_delay_seconds': float(delivery.get('edit_delay_seconds') or 0.0),
                    'edit_kind': str(delivery.get('edit_kind') or ''),
                    'quote_reply_to': str(delivery.get('quote_reply_to') or ''),
                    'chunks_sent': int(delivery.get('chunks_sent') or 0),
                }

    def record_voice_response(self, entry_id: str, *, raw_text: str, spoken_text: str) -> None:
        self.record_response(
            entry_id,
            raw_text=raw_text,
            parsed_chunks=[spoken_text] if spoken_text else [],
            status='sent' if spoken_text else 'empty',
            delivery={
                'typo_applied': False,
                'sent_text': spoken_text,
                'corrected_text': spoken_text,
                'edit_delay_seconds': 0.0,
                'edit_kind': '',
                'quote_reply_to': '',
                'chunks_sent': 1 if spoken_text else 0,
            },
        )
