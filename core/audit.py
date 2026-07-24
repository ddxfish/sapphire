# core/audit.py — Change-audit sink registry (prompt ledger, 2026-07-22).
#
# Contacts-provider pattern: core emits change events at its mutation seams
# (prompt saves, piece activations); an interested plugin registers a sink
# and decides what to record where. With no sink registered (memory v1
# users), emit() drops the event before the queue — zero behavior change.
#
# Events are delivered on a dedicated daemon worker thread (scout find,
# 2026-07-22): sinks do real SQLite writes with a 10s busy_timeout, and the
# emitters include async route handlers — a contended mind.db must never
# stall the event loop or her chat turn. emit() only enqueues.
#
# Event contract (dict, by 'kind'):
#   monolith:   {kind, name, before, after, actor, reason?}
#   component:  {kind, comp_type, key, before, after, actor, reason?}
#   activation: {kind, comp_type, key, active, prompt, actor, reason?,
#                ttl_minutes?}
#   reason:     {kind, name | comp_type+key, actor, reason} — a why arriving
#               on its own (typed after the last content keystroke produces
#               no saver diff); sinks attach it to the open session or the
#               newest matching record

import logging
import queue
import threading

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_sinks = {}          # plugin_name -> callable(event: dict)
_queue = queue.Queue()
_worker = None


def actor():
    """'ai' when running inside a tool call (tool_context set), 'user'
    otherwise (UI routes). Same convention as palace _added_by."""
    try:
        from core.chat.function_manager import tool_context
        return 'ai' if tool_context.get() else 'user'
    except Exception:
        return 'user'


def _run():
    while True:
        event = _queue.get()
        try:
            with _lock:
                sinks = list(_sinks.items())
            for name, fn in sinks:
                try:
                    fn(event)
                except Exception as e:
                    logger.warning(f"[AUDIT] sink '{name}' failed — record "
                                   f"dropped: {e}")
        finally:
            _queue.task_done()


def _ensure_worker():
    global _worker
    with _lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_run, name='audit-sink',
                                       daemon=True)
            _worker.start()


def register_sink(plugin_name: str, fn):
    with _lock:
        _sinks[plugin_name] = fn
    _ensure_worker()
    logger.info(f"[AUDIT] Sink registered: {plugin_name}")


def unregister_sink(plugin_name: str):
    with _lock:
        if _sinks.pop(plugin_name, None):
            logger.info(f"[AUDIT] Sink unregistered: {plugin_name}")


def emit(event: dict):
    """Enqueue and return immediately — never blocks the caller. With no
    sinks the event is dropped here (true no-op for memory v1)."""
    with _lock:
        if not _sinks:
            return
    _queue.put(event)


def flush(timeout: float = 5.0):
    """Block until every queued event has been delivered (tests, shutdown).
    Best-effort: returns False on timeout instead of raising."""
    done = threading.Event()

    def _waiter():
        _queue.join()
        done.set()

    threading.Thread(target=_waiter, daemon=True).start()
    return done.wait(timeout)
