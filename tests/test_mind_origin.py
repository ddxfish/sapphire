"""mind_changed carries the requesting tab's origin (DOM-refresh hunt, 2026-09-08).

Every Mind Palace click painted its view twice: the route published
mind_changed with no `origin`, so event-bus.js could not skip the tab's own
echo. The fix is a request-scoped contextvar (core/request_context.py) set by
the http middleware from X-Session-ID and read by publish_mind_changed —
17 palace publish sites, zero handler signatures touched. These prove the
three legs: the publisher reads the var, the middleware sets it (for async
AND threadpool-run sync handlers), and outside a request it is None so tool /
cron / librarian saves still reach every tab.
"""
from core import mind_events
from core.request_context import session_origin


def _capture(monkeypatch):
    seen = []
    import core.event_bus as eb
    monkeypatch.setattr(eb, 'publish', lambda et, data=None, **kw: seen.append(data))
    return seen


def test_publish_carries_origin_from_request_context(monkeypatch):
    seen = _capture(monkeypatch)
    token = session_origin.set('tab-1')
    try:
        mind_events.publish_mind_changed('memory', 'default', 'save')
    finally:
        session_origin.reset(token)
    assert seen and seen[0]['origin'] == 'tab-1'
    assert seen[0]['domain'] == 'memory' and seen[0]['action'] == 'save'


def test_publish_outside_a_request_has_no_origin(monkeypatch):
    """Tool-side saves (her save_memory, cron, librarian) must reach every tab."""
    seen = _capture(monkeypatch)
    assert session_origin.get() is None
    mind_events.publish_mind_changed('goal', 'default', 'update')
    assert seen and seen[0]['origin'] is None


def test_middleware_stamps_session_id_for_async_and_sync_handlers(client):
    c, _ = client
    from core.api_fastapi import app

    async def _async_probe():
        return {'origin': session_origin.get()}

    def _sync_probe():           # runs in the threadpool — contextvars must ride along
        return {'origin': session_origin.get()}

    app.add_api_route('/__test/origin-async', _async_probe, methods=['GET'])
    app.add_api_route('/__test/origin-sync', _sync_probe, methods=['GET'])
    try:
        r = c.get('/__test/origin-async', headers={'X-Session-ID': 'tab-9'})
        assert r.status_code == 200, r.text
        assert r.json()['origin'] == 'tab-9'
        r = c.get('/__test/origin-sync', headers={'X-Session-ID': 'tab-9'})
        assert r.status_code == 200, r.text
        assert r.json()['origin'] == 'tab-9'
        r = c.get('/__test/origin-async')
        assert r.json()['origin'] is None
    finally:
        app.router.routes[:] = [rt for rt in app.router.routes
                                if getattr(rt, 'path', '') not in ('/__test/origin-async', '/__test/origin-sync')]


# ─── pre-push hunt 2026-09-08 (D4-B1/B2/B4) ─────────────────────────────────

import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def test_threads_do_not_inherit_the_origin():
    """Every long job that publishes mind_changed (librarian, import, transfer)
    is a threading.Thread — this is the invariant the whole job map rests on."""
    if getattr(sys.flags, 'thread_inherit_context', 0):
        pytest.skip("free-threaded build inherits contextvars; the workers reset explicitly")
    token = session_origin.set('tab-1')
    seen = []
    try:
        t = threading.Thread(target=lambda: seen.append(session_origin.get()))
        t.start()
        t.join()
    finally:
        session_origin.reset(token)
    assert seen == [None]


def test_chat_turn_detaches_the_tabs_origin(client, mock_system):
    """A tool save inside HER turn must repaint every palace view, the
    requesting tab's included. The stream POST sends no X-Session-ID today;
    the belt is for the day it rides fetchWithTimeout (which auto-stamps)."""
    c, csrf = client
    seen = []
    mock_system.process_llm_query = lambda text, flag: seen.append(session_origin.get()) or "ok"
    mock_system.llm_chat.pending_notices = []
    r = c.post('/api/chat', json={'text': 'hi'},
               headers={'X-CSRF-Token': csrf, 'X-Session-ID': 'tab-9'})
    assert r.status_code == 200, r.text
    assert seen == [None]


def test_librarian_workers_detach_the_origin_at_entry():
    src = (ROOT / 'plugins' / 'mindpalace' / 'tools' / 'librarian.py').read_text(encoding='utf-8')
    assert 'def _detach_origin():' in src
    worker = src[src.index('def _worker('):]
    assert worker.index('_detach_origin()') < worker.index('try:')
    drain = src[src.index('def _drain_loop('):]
    assert drain.index('_detach_origin()') < drain.index('_drain_active = True')
