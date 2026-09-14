"""Background workers for Discord voice paths that must not block py-cord threads.

Lazy (built on first submit, rebuilt after shutdown) so lifecycle.stop can
retire the threads instead of leaking a module-global executor across plugin
reloads (M22, hunt 2026-09-12). Sized for two conversational sessions each
running STT + an utterance flush at once; the barge-in path no longer rides
here (frames go straight into the conversation engine since 2026-09-13).
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

MAX_WORKERS = 4

_pool: ThreadPoolExecutor | None = None
_lock = threading.Lock()


def get_pool() -> ThreadPoolExecutor:
    global _pool
    with _lock:
        if _pool is None:
            _pool = ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix='discord-voice')
        return _pool


def submit(fn, *args, **kwargs):
    return get_pool().submit(fn, *args, **kwargs)


def shutdown(*, wait: bool = False) -> None:
    global _pool
    with _lock:
        pool, _pool = _pool, None
    if pool is not None:
        pool.shutdown(wait=wait, cancel_futures=True)
