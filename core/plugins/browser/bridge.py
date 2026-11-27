# core/plugins/browser/bridge.py - the relay between Playwright and a person's own browser
#
# The extension in their browser is a thin pipe: chrome.debugger on the tabs
# they share, one socket to Sapphire. Playwright can't speak to that, so this
# relay stands in for a browser: Playwright connects to it over loopback
# (connect_over_cdp) and speaks the DevTools protocol. Browser-level commands
# are answered here (an extension's debugger has no browser session); a tab's
# commands go down the pipe to that tab and its events come back.
#
# One Bridge per paired browser (a device of the `browser` driver), kept in
# _bridges while its pipe is open. Everything here runs on one asyncio loop
# (the engine's). The pipe side is two plain functions: send(dict) puts a
# frame on the socket, feed(dict) takes one that arrived (routes/pipe.py).
#
# The debugger goes on a tab only when she takes it (take, open) and comes off
# when Playwright disconnects (the lane idles out, engine.py): their browser's
# "is being debugged" banner shows only while she is really there.
import asyncio
import collections
import itertools
import json
import logging
import re
import secrets
import time

logger = logging.getLogger(__name__)

CONTEXT = 'person'                     # the one browser context Playwright sees
ASK_S = 30                             # one answer from the extension
# Answered here, never sent to their browser. The first two Playwright sends
# to every tab it attaches: they would force the light colour scheme and swap
# the fonts on a person's own pages. The Network domain stays off there: its
# events carry cookies and the forms they post. Playwright navigates without
# it (a navigation then has no Response object, and failed requests are not
# listed in the console lines).
SWALLOW = {'Emulation.setEmulatedMedia', 'Page.setFontFamilies', 'Network.enable'}


# why a tab left her hand, as the extension or Chromium names it -> as she is told
WHY = {'target_closed': 'it was closed', 'canceled_by_user': 'the user stopped the debugging from their browser',
       'replaced_with_devtools': 'the user opened DevTools on it', 'not shared': 'the user stopped sharing it',
       'off': 'the user switched sharing off', 'unpaired': 'the user made a new pairing code'}


class Gone(Exception):
    """The browser isn't connected (any more); the words are for her."""


class Bridge:
    def __init__(self, name, send, hangup=None, label=''):
        self.name = name               # the device's id
        self.label = label             # ...and what the person called it
        self._send = send
        self._hangup = hangup          # closes the pipe's socket
        self.info = {}                 # the extension's hello: v, ua, mode, origin (her address, as that browser dials it)
        self.listed = []               # tab ids as her last tab list numbered them
        self.last = _last.get(name)    # the tab she last held: taken again when she comes back (kept across a reconnect)
        self.blocked = None            # fn(url, local_ok) -> why a frame is off limits to her, or '' (engine.py sets it)
        self.closed = False
        self.lost = ''                 # why that tab left her hand, until she is told
        self.notes = []                # things the extension did on its own (a download it stopped), until she is told
        self.since = time.time()
        self.tabs = {}                 # tab id -> {'target', 'session', 'url', 'title'}: tabs held now
        self._session_tab = {}         # session id (a tab's own, or a child's) -> tab id
        self._side = {}                # a side session Playwright opened -> target id ('' = the browser)
        self._calls = {}               # pipe call id -> future, or (client id, session) to answer
        self._ids = itertools.count(1)
        self._client = None            # Playwright's socket
        self._out = None               # frames for Playwright, in order
        self._server = None
        self._writer = None
        self.token = secrets.token_urlsafe(24)
        self.port = 0

    # --- Playwright's side -------------------------------------------------------

    async def start(self):
        """The address Playwright connects to (loopback, a path only we know)."""
        if self.closed:
            raise Gone(f"{self.name} disconnected")
        if self._server:
            return f'ws://127.0.0.1:{self.port}/{self.token}'
        try:
            from websockets.asyncio.server import serve
        except ImportError:                        # it comes with uvicorn[standard]; this API since websockets 13
            raise Gone("a person's browser needs the 'websockets' package, version 13 or newer, in Sapphire's "
                       "Python (pip install -U websockets)") from None
        self._server = await serve(self._serve, '127.0.0.1', 0, max_size=None, ping_interval=None)
        self.port = self._server.sockets[0].getsockname()[1]
        return f'ws://127.0.0.1:{self.port}/{self.token}'

    async def close(self):
        """The pipe went away or the plugin is stopping."""
        self.closed = True
        for call in self._calls.values():
            if isinstance(call, asyncio.Future) and not call.done():
                call.set_exception(Gone(f"{self.name} disconnected"))
        self._calls.clear()
        if self._writer:
            self._writer.cancel()
        if self._server:
            self._server.close()
            self._server = None
        if self._client:
            try:
                await self._client.close()
            except Exception:
                pass
        if self._hangup:
            self._hangup()

    async def _serve(self, conn):
        if conn.request.path != '/' + self.token or self.closed:
            await conn.close(1008, 'no')
            return
        old = self._client
        if old is not None:            # a lane closing as the next one attaches: the newer client is the one
            self._client = None
            await old.close()
        self._client = conn
        self._out = out = asyncio.Queue()
        self._writer = writer = asyncio.ensure_future(self._write(conn, out))
        try:
            async for raw in conn:
                try:
                    self._command(json.loads(raw))
                except Exception as e:
                    logger.warning(f"[BROWSER] relay {self.name}: {e!r}")
        except Exception:
            pass                       # Playwright hung up
        finally:
            writer.cancel()
            if self._client is conn:
                self._client = None
                self._calls = {n: c for n, c in self._calls.items() if isinstance(c, asyncio.Future)}
                self.release()         # nobody is driving: let go of their tabs

    async def _write(self, conn, queue):
        try:
            while True:
                await conn.send(json.dumps(await queue.get()))
        except Exception:
            pass

    def _tell(self, frame):
        if self._client is not None:
            self._out.put_nowait(frame)

    def _answer(self, cid, session, result=None, error=None):
        frame = {'id': cid}
        if session:
            frame['sessionId'] = session
        if error is not None:
            frame['error'] = {'code': -32000, 'message': str(error)}
        else:
            frame['result'] = result if result is not None else {}
        self._tell(frame)

    def _command(self, msg):
        """One command from Playwright. Never awaits: a tab's commands reach
        the pipe in the order Playwright sent them."""
        cid, method, params = msg.get('id'), msg.get('method', ''), msg.get('params') or {}
        session = msg.get('sessionId') or ''
        if session in self._session_tab:
            tab = self._session_tab[session]
            if method in SWALLOW:
                return self._answer(cid, session)
            if method == 'Browser.getWindowForTarget':     # a tab's debugger has no Browser domain
                return self._answer(cid, session, {'windowId': 1, 'bounds': {
                    'left': 0, 'top': 0, 'width': 1280, 'height': 800, 'windowState': 'normal'}})
            held = self.tabs.get(tab)
            child = session if held and held['session'] != session else None
            n = next(self._ids)
            self._calls[n] = (cid, session)
            return self._send({'id': n, 'cmd': 'cdp', 'tab': tab, 'session': child,
                               'method': method, 'params': params})
        if session in self._side:
            target = self._side[session]
            if method == 'Target.attachToTarget':
                s = f"side-{next(self._ids)}"
                self._side[s] = params.get('targetId', '')
                return self._answer(cid, session, {'sessionId': s})
            if method == 'Target.getTargetInfo' and target:
                return self._answer(cid, session, {'targetInfo': self._target_info(target)})
            if method == 'Target.detachFromTarget':
                self._side.pop(params.get('sessionId', ''), None)
                return self._answer(cid, session)
            return self._answer(cid, session, error=f"'{method}' is not available here")
        if session:
            return self._answer(cid, session, error='Session with given id not found.')
        if method == 'Browser.getVersion':
            ua = self.info.get('ua') or 'Mozilla/5.0 Chrome/0.0.0.0'
            return self._answer(cid, '', {'protocolVersion': '1.3', 'product': 'Chrome/' + _version(ua),
                                          'revision': '', 'userAgent': ua, 'jsVersion': ''})
        if method in ('Target.setAutoAttach', 'Browser.setDownloadBehavior', 'Target.setDiscoverTargets'):
            return self._answer(cid, '')
        if method == 'Target.getTargetInfo':
            return self._answer(cid, '', {'targetInfo': {'targetId': 'browser', 'type': 'browser', 'title': '',
                                                         'url': '', 'attached': True, 'canAccessOpener': False}})
        if method == 'Target.attachToBrowserTarget':
            s = f"side-{next(self._ids)}"
            self._side[s] = ''
            return self._answer(cid, '', {'sessionId': s})
        if method == 'Target.createTarget':
            return asyncio.ensure_future(self._create(cid, params))
        if method == 'Target.closeTarget':
            return asyncio.ensure_future(self._close_target(cid, params))
        self._answer(cid, '', error=f"'{method}' wasn't found")

    async def _create(self, cid, params):
        try:
            tab = await self.open()
            self._answer(cid, '', {'targetId': self.tabs[tab]['target']})
        except Exception as e:
            self._answer(cid, '', error=e)

    async def _close_target(self, cid, params):
        tab = next((t for t, h in self.tabs.items() if h['target'] == params.get('targetId')), None)
        try:
            if tab is not None:
                await self.ask('close', tab=tab)
            self._answer(cid, '', {'success': True})
        except Exception as e:
            self._answer(cid, '', error=e)

    def _target_info(self, target):
        held = next((h for h in self.tabs.values() if h['target'] == target), None) or {}
        info = {'targetId': target, 'type': 'page', 'title': held.get('title', ''), 'url': held.get('url', ''),
                'attached': True, 'canAccessOpener': False, 'browserContextId': CONTEXT}
        if held.get('opener'):
            info['openerId'] = held['opener']
        return info

    # --- the pipe's side ---------------------------------------------------------

    def feed(self, msg):
        """One frame from the extension. Never raises: a frame this relay
        cannot make sense of is dropped, the pipe lives on."""
        try:
            self._feed(msg)
        except Exception as e:
            logger.warning(f"[BROWSER] relay {self.name}: a frame was dropped ({e!r})")

    def _feed(self, msg):
        if 'id' in msg:
            call = self._calls.pop(msg['id'], None)
            if isinstance(call, asyncio.Future):
                if not call.done():
                    if 'err' in msg:
                        call.set_exception(Gone(str(msg['err'])))
                    else:
                        call.set_result(msg.get('ok'))
            elif call:
                cid, session = call
                if 'err' in msg:
                    self._answer(cid, session, error=msg['err'])
                else:
                    self._answer(cid, session, msg.get('ok') or {})
            return
        ev = msg.get('ev')
        if ev == 'cdp':
            tab, method, params = msg.get('tab'), msg.get('method', ''), msg.get('params') or {}
            held = self.tabs.get(tab)
            if not held:
                return
            session = msg.get('session') or held['session']
            if method == 'Target.attachedToTarget':
                info = params.get('targetInfo') or {}
                if self.blocked and info.get('type') == 'iframe' and self.blocked(info.get('url'), held['local']):
                    # a frame from somewhere she may not be (this Sapphire's own page, a local address):
                    # let go at once, and Playwright never hears of it
                    return self._send({'cmd': 'cdp', 'tab': tab, 'session': msg.get('session'),
                                       'method': 'Target.detachFromTarget',
                                       'params': {'sessionId': params.get('sessionId')}})
                self._session_tab[params.get('sessionId', '')] = tab
            elif method == 'Target.detachedFromTarget':
                self._session_tab.pop(params.get('sessionId', ''), None)
            self._tell({'method': method, 'params': params, 'sessionId': session})
        elif ev == 'attached':                  # a tab that one she holds opened: the extension took hold of it
            if msg.get('opener') and self.tab_of(msg['opener']) is not None and msg.get('tab') not in self.tabs:
                self._hold(msg.get('tab'), msg)
        elif ev == 'detached':
            self._drop(msg.get('tab'), msg.get('reason', ''))
        elif ev == 'note':
            self.notes = (self.notes + [str(msg.get('text') or '')[:200]])[-3:]
        elif ev == 'hello':
            self.info = {k: str(msg.get(k) or '')[:300] for k in ('v', 'ua', 'mode', 'origin', 'browser')}
        elif ev == 'mode':
            self.info['mode'] = str(msg.get('mode') or '')[:20]

    async def ask(self, cmd, **kw):
        """Ask the extension for something; awaits its answer."""
        if self.closed:
            raise Gone(f"{self.name} disconnected")
        n = next(self._ids)
        fut = self._calls[n] = asyncio.get_running_loop().create_future()
        self._send({'id': n, 'cmd': cmd, **kw})
        try:
            return await asyncio.wait_for(fut, ASK_S)
        except asyncio.TimeoutError:
            raise Gone(f"{self.name}'s browser did not answer") from None
        finally:
            self._calls.pop(n, None)

    def _hold(self, tab, info):
        """The extension's debugger is on this tab: Playwright gets a page."""
        session = f"tab-{tab}-{next(self._ids)}"
        self.tabs[tab] = {'target': info['target'], 'session': session, 'url': info.get('url', ''),
                          'title': info.get('title', ''), 'opener': info.get('opener'),
                          'local': bool(info.get('local')),      # a local address they shared by hand
                          'hers': bool(info.get('hers'))}        # a tab she opened: hers to navigate and close
        self._session_tab[session] = tab
        self._tell({'method': 'Target.attachedToTarget', 'params': {
            'sessionId': session, 'targetInfo': self._target_info(info['target']), 'waitingForDebugger': False}})

    def _drop(self, tab, reason=''):
        held = self.tabs.pop(tab, None)
        if not held:
            return
        for s in [s for s, t in self._session_tab.items() if t == tab]:
            del self._session_tab[s]
        self._tell({'method': 'Target.detachedFromTarget',
                    'params': {'sessionId': held['session'], 'targetId': held['target']}})
        if tab == self.last and reason not in ('released', 'disconnected'):
            self.last, self.lost = None, WHY.get(reason, reason)
            _last.pop(self.name, None)

    async def list(self):
        """The tabs she may use now: [{tab, url, title, active, held}]."""
        return await self.ask('tabs') or []

    async def take(self, tab):
        """Put the debugger on a tab; returns its target id."""
        if tab not in self.tabs:
            info = await self.ask('attach', tab=tab)
            if tab not in self.tabs:               # two asks for one tab: the first answer holds it
                self._hold(tab, info)
        return self.tabs[tab]['target']

    async def open(self):
        """A new tab in their browser, held; returns its tab id."""
        info = await self.ask('new')
        if info['tab'] not in self.tabs:
            self._hold(info['tab'], info)
        return info['tab']

    async def usable(self, tab):
        """Why the extension no longer lets her use this tab, where it is
        right now, or ''. Asked before every read, picture and action: a tab
        that moved (a click, Back) is judged by the browser it is in, not by
        an event that may still be on its way."""
        why = str(await self.ask('ok', tab=tab) or '')
        return WHY.get(why, why)

    def release(self):
        """Let go of every tab (their debugging banner clears)."""
        if self.tabs:
            self._send({'cmd': 'release'})
        for tab in list(self.tabs):
            self._drop(tab, 'released')

    def tab_of(self, target):
        return next((t for t, h in self.tabs.items() if h['target'] == target), None)


def _version(ua):
    m = re.search(r'Chrom(?:e|ium)/([\d.]+)', ua)
    return m.group(1) if m else '0.0.0.0'


# --- the browsers connected now ------------------------------------------------------

_bridges = {}                          # device id -> Bridge
_last = {}                             # device id -> the tab she last held there, across reconnects


def get(device):
    return _bridges.get(device)


def names():
    return sorted(_bridges)


def register(device, bridge):
    """A paired browser's pipe opened (routes/pipe.py). One pipe per device:
    a second one replaces the first, which is how a browser comes back after
    its old socket died without saying so. Plain, not a coroutine: nothing
    can cut in between the old one leaving and the new one arriving."""
    old = _bridges.pop(device, None)
    _bridges[device] = bridge
    if old:
        asyncio.ensure_future(old.close())
    logger.info(f"[BROWSER] {device}: its browser connected")


def unregister(device, bridge):
    """That pipe closed."""
    if _bridges.get(device) is bridge:
        del _bridges[device]
        logger.info(f"[BROWSER] {device}: its browser disconnected")
    bridge._hangup = None
    asyncio.ensure_future(bridge.close())


def remember(device, tab):
    """The tab she holds there now (engine.person_take)."""
    _last[device] = tab


async def close_all():
    for device in list(_bridges):
        await _bridges.pop(device).close()
