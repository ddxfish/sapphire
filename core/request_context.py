"""Per-request context the route layer can't thread by hand.

`session_origin` is the browser tab's X-Session-ID for the request being
handled — set by the http middleware in core/api_fastapi.py, None outside a
request (tool calls, cron, tests). Publishers put it on their events as
`origin` so the tab that caused a change skips its own echo (event-bus.js:
`origin === sessionId`). Contextvars ride into threadpool handlers (anyio
copies the context), so sync `def` routes see it too.

Added 2026-09-08 (DOM-refresh hunt): mind_changed carried no origin, so every
Mind Palace click painted its view twice — 17 publish sites in the palace
routes, none with a `request` in scope. One middleware line beats 34 edits.
"""
from contextvars import ContextVar

session_origin: ContextVar = ContextVar('session_origin', default=None)
