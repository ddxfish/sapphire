"""Background workers for Discord voice paths that must not block py-cord threads."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

VOICE_WORKER_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix='discord-voice')
