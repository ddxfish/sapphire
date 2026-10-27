# core/plugins/browser/routes/view.py - the viewer's socket (/ws/plugin/browser/view)
#
# Down: binary messages are JPEG frames of her tab, text messages are JSON
# {t:'state', ...} (tabs, url, asleep) and {t:'note', text} (what an action
# said). Up: JSON input - mouse, key, text, nav, tab, wake, forget. Her lane
# only. Core has checked the login and Origin and accepted the socket.
#
# The engine lives on its own loop (engine.py); this handler runs on the web
# server's, so every engine step is awaited through engine.call, and a slow
# viewer keeps only the newest frame instead of queueing old ones.
import asyncio
import itertools
import logging

from starlette.websockets import WebSocketDisconnect

logger = logging.getLogger(__name__)

_ids = itertools.count(1)
QUICK = {'mouse', 'key', 'text'}        # done in order, at once; the rest run beside the input


async def view(websocket, settings):
    from plugins.browser import engine, screen
    engine.apply(settings)
    name, vid = engine.HER, next(_ids)
    held = {'keys': set(), 'buttons': set()}   # what this viewer has down in her page
    loop = asyncio.get_running_loop()
    box = {'frame': None, 'msgs': []}
    ready = asyncio.Event()

    def put(kind, data):                   # the engine's loop -> this one
        if kind == 'frame':
            box['frame'] = data
        else:
            box['msgs'].append({'t': kind, **data})
        ready.set()

    def sink(kind, data):
        try:
            loop.call_soon_threadsafe(put, kind, data)
        except RuntimeError:
            pass                           # this loop is closing; the socket goes with it

    async def sender():
        try:
            while True:
                await ready.wait()
                ready.clear()
                msgs, box['msgs'] = box['msgs'], []
                for m in msgs:
                    await websocket.send_json(m)
                frame, box['frame'] = box['frame'], None
                if frame:
                    await websocket.send_bytes(frame)
        except Exception:
            pass                           # the socket closed under us (the error type varies by server); receive ends it

    async def act(msg):
        try:
            said = await _do(engine, screen, name, msg, held)
            if said:
                put('note', {'text': said})
        except (engine.Blocked, engine.NotReady) as e:
            put('note', {'text': str(e)})
        except Exception as e:
            put('note', {'text': (str(e).strip().splitlines() or [type(e).__name__])[0][:300]})

    out = loop.create_task(sender())
    side = set()
    try:
        await engine.call(screen.attach, name, vid, sink)
        while True:
            msg = await websocket.receive_json()
            if not isinstance(msg, dict):
                continue
            if msg.get('t') in QUICK:
                await act(msg)
            else:
                t = loop.create_task(act(msg))
                side.add(t)
                t.add_done_callback(side.discard)
    except WebSocketDisconnect:
        pass
    finally:
        out.cancel()
        for t in side:
            t.cancel()
        try:
            await engine.call(screen.detach, name, vid, timeout=5)
            if held['keys'] or held['buttons']:
                await engine.call(screen.release, name, list(held['keys']), list(held['buttons']), timeout=5)
        except Exception:
            pass


async def _do(engine, screen, name, m, held):
    """One input message; returns words for the viewer, or None."""
    t = m.get('t')
    if t == 'mouse':
        kind, button = m.get('kind'), m.get('button', 'left')
        if kind in ('down', 'up') and button in ('left', 'middle', 'right'):
            (held['buttons'].add if kind == 'down' else held['buttons'].discard)(button)
        await engine.call(screen.mouse, name, kind, m.get('x', 0), m.get('y', 0),
                          button, m.get('dx', 0), m.get('dy', 0))
    elif t == 'key':
        kind, key = 'up' if m.get('kind') == 'up' else 'down', str(m.get('key', ''))[:40]
        (held['keys'].add if kind == 'down' else held['keys'].discard)(key)
        await engine.call(screen.key, name, kind, key)
    elif t == 'text':
        await engine.call(screen.text, name, m.get('text', ''))
    elif t == 'nav':
        url = str(m.get('url') or '').strip()
        if url and '://' not in url and not url.startswith(('about:', 'data:')):
            url = 'https://' + url
        await engine.call(screen.nav, name, m.get('what', 'go'), url)
    elif t == 'tab':
        await engine.call(screen.tab, name, m.get('index', 0))
    elif t == 'wake':
        await engine.call(screen.wake, name)
    elif t == 'forget':
        await asyncio.shield(engine.call(screen.forget, name))   # a closing socket must not cut a wipe in half
        return "Her browser profile is wiped: logins, site data, history and cache are gone."
