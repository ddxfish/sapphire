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
