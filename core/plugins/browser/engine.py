# core/plugins/browser/engine.py - one Playwright, many lanes, its own thread
#
# Playwright's objects belong to the loop that made them, and tool calls come
# from any thread (her chat turns, the MCP door's workers), so the engine owns
# a private asyncio loop on a daemon thread and every call is shipped to it
# with run(). Nothing here ever runs on uvicorn's loop.
#
# A lane is one persistent Chromium profile (user/browser/<lane>): `sapphire`
# for her own turns, `mcp-<persona>` for an MCP caller (core.mcp_server.client).
# Lanes never share cookies or tabs. Her lane wears the guard rails while the
# `guard_rails` setting is on: web addresses only (no LAN, loopback, file),
# no downloads, no service workers, no typing into password fields, no js.
# Her network goes through egress.py, the proxy that enforces the address rule.
# Chromium starts on the first call and a lane closes after it sits idle.
#
# A third kind, `ext-<device>`, is a PERSON'S own browser, paired in Devices:
# nothing is launched, Playwright attaches to bridge.py's relay and the
# extension there drives the tabs that person shares. Always railed, whatever
# the switch says: it holds their real logins. No proxy of ours is in its
# path, so its address rule is checked here (person_blocked) and by the
# extension. Closing such a lane only lets go of their tabs.
import asyncio
import collections
import ipaddress
import logging
import re
import socket
import sys
import threading
import time
import weakref
from urllib.parse import urlsplit

from . import bridge, chromium, egress

logger = logging.getLogger(__name__)

CALL_TIMEOUT = 90          # seconds a tool call may take, end to end
ACTION_MS = 8000           # one click / fill / wait
NAV_MS = 30000             # one navigation
VIEWPORT = {'width': 1280, 'height': 800}
HER = 'sapphire'
EXT = 'ext-'               # + a device id: that person's own browser (bridge.py)

_loop = None
_thread = None
_pw = None
_lanes = {}                # name -> Lane
_locks = {}                # name -> asyncio.Lock: one start, close or wipe of a lane at a time
_busy = collections.Counter()   # name -> calls using it now (using())
_stopped = False           # shutdown() ran: refuse calls until allow()
_start_lock = threading.Lock()
_hands = {}                # chat -> the person's browser that chat's turns are in (absent: her own browser)
settings = {}              # the plugin's settings, refreshed by every tool call
_egress = {}               # lane name -> its proxy server (egress.py): every lane's traffic goes through one
_socks = None              # the SOCKS upstream as last read (None = not read since a settings change)


class Blocked(Exception):
    """A guard rail said no; the words are for her."""


class NotReady(Exception):
    """Chromium is absent or can't run here (chromium.py); the words are for her."""


class Lane:
    def __init__(self, name, ctx, guarded, headed):
        self.name, self.ctx, self.guarded, self.headed = name, ctx, guarded, headed
        self.page = ctx.pages[0] if ctx.pages else None
        self.used = time.monotonic()
        self.logs = collections.deque(maxlen=300)
        self.total = 0                 # lines ever logged (the deque forgets, this does not)
        self.seen = 0                  # `total` when browser_console last handed lines out
        self.logged_in = None          # self-login cookie state as last synced
        self.blocked = ''              # the last address the rails refused
        self.unreached = ''            # the last host the proxy could not reach, in words
        self.unreached_host = ''
        self.viewer_at = 0.0           # when someone at the viewer last moved the page (screen.py)
        self.viewed_at = 0.0           # when a tool last read it
        self.read = None               # (page, document id) her refs came from (tools/browser.py _view)
        self.dialog = ''               # the last alert/confirm a page raised, until her next look says it
        self.bridge = None             # an ext lane: the relay to that person's browser
        self.browser = None            # ...and Playwright's connection to it
        self.tab_ids = weakref.WeakKeyDictionary()   # ...and which of their tabs each page is
        self.watched = weakref.WeakSet()
        self.crashed = weakref.WeakSet()
        for p in ctx.pages:
            self.watch(p)
        ctx.on('page', self._new_page)
        ctx.on('close', self._gone)    # Chromium died or closed: the next call relaunches

    def _gone(self, *_):
        if _lanes.get(self.name) is self:
            _lanes.pop(self.name, None)
            logger.info(f"[BROWSER] lane '{self.name}' went away; the next call starts it again")

    def _new_page(self, page):
        self.watch(page)
        self.page = page               # a popup or target=_blank becomes the tab in hand

    def watch(self, page):
        if page in self.watched:
            return
        self.watched.add(page)

        def log(kind, text):
            self.logs.append(f"[{kind}] {text}"[:500])
            self.total += 1

        def crash(*_):
            self.crashed.add(page)
            log('crash', f"the tab {page.url} crashed")

        def dialog(d):
            # a listener means WE answer it (else the page hangs): dismissed as
            # Playwright would, except leaving a page; she hears what it said
            leaving = d.type == 'beforeunload'

            async def answer():
                try:
                    await (d.accept() if leaving else d.dismiss())
                except Exception:
                    pass                   # the page went away first
            asyncio.ensure_future(answer())
            if not leaving:
                self.dialog = f"the page showed a {d.type} dialog: \"{(d.message or '')[:300]}\" (dismissed)"
                log('dialog', self.dialog)
        page.on('console', lambda m: log(m.type, m.text))
        page.on('pageerror', lambda e: log('pageerror', str(e)))
        page.on('requestfailed', lambda r: log('requestfailed', f"{r.url} {r.failure or ''}"))
        page.on('crash', crash)
        page.on('dialog', dialog)

    def tab(self):
        if self.page is None or self.page.is_closed() or self.page in self.crashed:
            if self.bridge:                # their browser: never slide onto another of their tabs unasked
                self.page = None
                return None
            open_ = [p for p in self.ctx.pages if not p.is_closed() and p not in self.crashed]
            self.page = open_[-1] if open_ else None
        return self.page


# --- the thread ------------------------------------------------------------------

def _ensure():
    global _loop, _thread
    with _start_lock:
        if _stopped:                           # shut down: a late call must not bring Chromium back
            raise NotReady("The browser plugin is off.")
        if _thread and _thread.is_alive():
            return
        # Windows: sapphire.py pins the Selector policy process-wide, and a
        # Selector loop can't spawn Playwright's driver there (NotImplementedError).
        # A Proactor loop in this thread only - the mcp_client daemon's fix.
        _loop = asyncio.ProactorEventLoop() if sys.platform == 'win32' else asyncio.new_event_loop()
        ready = threading.Event()

        def main():
            asyncio.set_event_loop(_loop)
            _loop.set_exception_handler(_loop_error)
            _loop.call_soon(ready.set)
            _loop.create_task(_reaper())
            _loop.run_forever()
        _thread = threading.Thread(target=main, daemon=True, name='browser-engine')
        _thread.start()
        ready.wait(5)


def _loop_error(loop, ctx):
    """When Sapphire stops, the service manager (systemd, or CTRL_BREAK on
    Windows) can kill Playwright's driver before shutdown() closes it, and
    Playwright leaves a future holding that error. Expected at stop: one
    quiet line, not an ERROR. Anything else goes to asyncio's usual report."""
    exc = ctx.get('exception')
    if 'Connection closed while reading from the driver' in str(exc or ''):
        logger.info("[BROWSER] Playwright's driver went away with the process")
        return
    if isinstance(exc, ConnectionResetError) or getattr(exc, 'winerror', 0) in (64, 10054):
        return                                 # Chromium hung up on a proxied connection: routine
    loop.default_exception_handler(ctx)


def run(coro_fn, *args, timeout=CALL_TIMEOUT):
    """Run `await coro_fn(*args)` on the engine's loop from any thread."""
    _ensure()
    fut = asyncio.run_coroutine_threadsafe(coro_fn(*args), _loop)
    try:
        return fut.result(timeout)
    except TimeoutError:
        fut.cancel()
        raise TimeoutError(f"the browser took longer than {timeout}s")


def post(fn, *args):
    """Run the plain function fn(*args) on the engine's loop, in the order
    posted, without waiting (the pipe's frames, routes/pipe.py)."""
    _ensure()
    _loop.call_soon_threadsafe(fn, *args)


async def call(coro_fn, *args, timeout=CALL_TIMEOUT):
    """run() for a caller on another asyncio loop (the viewer's socket): awaits
    instead of blocking that loop."""
    _ensure()
    fut = asyncio.run_coroutine_threadsafe(coro_fn(*args), _loop)
    try:
        return await asyncio.wait_for(asyncio.wrap_future(fut), timeout)
    except asyncio.TimeoutError:
        fut.cancel()
        raise TimeoutError(f"the browser took longer than {timeout}s")


def allow():
    """The plugin started (daemon.start): calls may start the engine again."""
    global _stopped
    with _start_lock:
        _stopped = False


def shutdown():
    """Close every lane and Chromium itself (plugin stop / unload). After
    this, calls are refused until allow(): no late call may start a fresh
    engine that nothing would ever stop."""
    global _loop, _thread, _pw, _stopped
    with _start_lock:
        _stopped = True
        _hands.clear()
        loop, thread = _loop, _thread
    if thread and thread.is_alive():
        try:
            asyncio.run_coroutine_threadsafe(_close_all(), loop).result(25)
        except Exception as e:
            logger.warning(f"[BROWSER] shutdown: {e!r}")
        loop.call_soon_threadsafe(loop.stop)
        thread.join(5)
        if not thread.is_alive():
            loop.close()
    with _start_lock:
        if _thread is thread:                      # an allow() + call during the join started a new engine: keep it
            _loop = _thread = _pw = None
            _locks.clear()                         # everything below belongs to the loop that's gone; a lane
            _busy.clear()                          # or proxy kept past a timed-out close would hang every page
            _lanes.clear()
            _egress.clear()


async def _close_all():
    global _pw
    try:
        names = list(_lanes)
        try:
            await asyncio.wait_for(asyncio.gather(*(_close(n) for n in names), return_exceptions=True), 15)
        except asyncio.TimeoutError:
            logger.warning(f"[BROWSER] lanes still closing at shutdown: {', '.join(names)}")
        for srv in _egress.values():
            srv.close()
        _egress.clear()
        await bridge.close_all()                   # every paired browser's pipe
    finally:
        if _pw:                                    # stopping the driver takes any Chromium still up with it
            try:
                await asyncio.wait_for(_pw.stop(), 10)
            except Exception as e:
                logger.warning(f"[BROWSER] Playwright's driver didn't stop cleanly: {e!r}")
            _pw = None
        for task in asyncio.all_tasks():
            if task is not asyncio.current_task():
                task.cancel()                      # the reaper


def _lock(name):
    lock = _locks.get(name)
    if lock is None:
        lock = _locks[name] = asyncio.Lock()
    return lock


async def _close(name):
    async with _lock(name):
        await _close_now(name)


async def _close_now(name):
    lane = _lanes.pop(name, None)
    if lane:
        try:                                       # a person's browser: disconnect only, their tabs stay
            await asyncio.wait_for(lane.browser.close() if lane.browser else lane.ctx.close(), 15)
        except Exception as e:
            logger.warning(f"[BROWSER] closing {name}: {e!r}")
        logger.info(f"[BROWSER] lane '{name}' closed")


async def using(name, fn, *args):
    """Run `await fn(name, *args)` as one use of the lane: the reaper and a
    window-switch relaunch leave a lane alone while anything is using it."""
    _busy[name] += 1
    try:
        if name.startswith(EXT):                   # a person's browser has ONE tab in hand: one call at a time there
            async with _lock(name + ':use'):
                return await fn(name, *args)
        return await fn(name, *args)
    finally:
        _busy[name] -= 1
        lane = _lanes.get(name)
        if lane:
            lane.used = time.monotonic()


async def _reaper():
    while True:
        await asyncio.sleep(30)
        try:
            idle = max(1.0, float(settings.get('idle_minutes') or 15)) * 60
            for name, lane in list(_lanes.items()):
                if not _busy[name] and time.monotonic() - lane.used > idle:
                    await _close(name)
        except Exception as e:                     # one bad pass must not end idling out for good
            logger.warning(f"[BROWSER] idle check: {e!r}")


# --- lanes -----------------------------------------------------------------------

def lane_name(who):
    """None (her own turn) -> 'sapphire'; an MCP caller -> 'mcp-<persona>'."""
    if who is None:
        return HER
    return 'mcp-' + (re.sub(r'[^a-z0-9_-]+', '-', str(who).lower()).strip('-')[:40] or 'client')


def _profiles():
    from core.paths import PROJECT_ROOT
    return PROJECT_ROOT / 'user' / 'browser'


def _self_origin():
    """(scheme, port) of her own web UI."""
    import config
    return ('https' if getattr(config, 'WEB_UI_SSL_ADHOC', True) else 'http'), int(getattr(config, 'WEB_UI_PORT', 8073))


def _is_self(url):
    scheme, port = _self_origin()
    u = urlsplit(url)
    return u.scheme == scheme and (u.hostname or '') in ('localhost', '127.0.0.1') and (u.port or 0) == port


def guarded(name):
    return name.startswith(EXT) or (name == HER and bool(settings.get('guard_rails', True)))


def hand(chat):
    """The lane this chat's turns use now: her own browser, or the person's
    she switched to there. Per chat: a switch in one chat never moves another
    chat's turns into a person's browser."""
    return _hands.get(chat, HER)


SCHEDULED = ('task', 'heartbeat')                  # task types the clock starts; never one an event sets off


def turn():
    """(who, '') for a turn that may reach a person's browser, (None, what
    started it) otherwise. `who` is the chat when a PERSON started the turn
    (they typed, spoke, or used a device), and ('task', id, run) for a run of
    a scheduled task, which may() lets in only where the person switched that
    on. A person's browser holds their logins: never an agent, a task a
    message or a webhook set off, or a turn a message from outside started (a
    text, Discord, mail, another Sapphire). What cannot be told gets nothing."""
    try:
        from core.chat.function_manager import tool_context
        ctx = tool_context.get() or {}
        from core import mcp_server
        if mcp_server.answering.get():
            return None, 'a message over MCP'
        if ctx.get('channel'):
            run = ctx.get('task')
            run = run if isinstance(run, dict) else {}
            if run.get('id') and run.get('n') and run.get('type') in SCHEDULED and run.get('event') is False:
                return ('task', run['id'], run['n']), ''
            return None, 'an agent, or a task that a message or a webhook set off'
        chat = ctx.get('chat')
        if not chat:
            return None, 'something that is not a chat turn'
        from core import idle
        from core.api_fastapi import get_system
        writing = [s for s in get_system().llm_chat.streams_for_chat(chat) if not s.llm_done]
        if len(writing) != 1:
            return None, 'a turn that cannot be told apart'
        source = getattr(writing[0], 'turn_source', None)
        if idle.is_person(source):
            return chat, ''
        return None, f"a machine turn ({str(source).split(':', 1)[0]})"
    except Exception as e:
        logger.warning(f"[BROWSER] could not tell who started the turn ({type(e).__name__})")
        return None, 'a turn that cannot be told apart'


TASKS = 'tasks'                                    # the Browser device's switch (plugin.json)


def may(who, device):
    """True when the turn `who` (turn()'s first answer) may use this paired
    browser: a person's own turn always; a scheduled task's run only while
    'Scheduled tasks may use this browser' is on for that device. Read from
    the device each time, so switching it off stops a run at its next call."""
    if who is None:
        return False
    if not isinstance(who, tuple):
        return True
    try:
        from core.devices import engine as devices
        row = devices.rows().get(device) or {}
        return any(p.get('driver') == 'browser' and (p.get('config') or {}).get(TASKS) is True
                   for p in row.get('parts', []) if isinstance(p, dict)) and row.get('enabled', True) is not False
    except Exception as e:
        logger.warning(f"[BROWSER] could not read {device}'s tasks switch ({type(e).__name__})")
        return False


def refused(who, started_by, label):
    """The words for a turn that may not use this paired browser."""
    if isinstance(who, tuple):
        return (f"{label} is the user's own browser. A scheduled task may use it only while they have "
                f"'Scheduled tasks may use this browser' switched on for it (Settings > Devices), and it is off.")
    return (f"{label} is the user's own browser: it can be used only in a turn they started themselves "
            f"(typed, spoken, or from a device), or by a scheduled task where they switched that on. "
            f"This turn was started by {started_by}.")


def up(name):
    """The lane if it is running; never starts it."""
    return _lanes.get(name)


class _Free:
    async def __aenter__(self):
        return None

    async def __aexit__(self, *exc):
        return False


def one_at_a_time(name, inside):
    """The lock using() holds for a person's lane, for a call that came in on
    another lane and reaches over (a switch into their browser from her own).
    `inside` is the lane the call is already running on: its lock is held."""
    return _Free() if name == inside else _lock(name + ':use')


def take_hand(chat, name):
    if name == HER:
        _hands.pop(chat, None)
        return
    if isinstance(chat, tuple):                    # a task's hand lasts one run: its earlier runs' are forgotten,
        runs = [k for k in _hands if isinstance(k, tuple)]          # and so is all but the newest few of any task
        for old in [k for k in runs if k[1] == chat[1]] + runs[:-20]:
            _hands.pop(old, None)
    _hands[chat] = name


_RULES = (('guard_rails', True), ('self_login', False))


def apply(new):
    """Take the plugin's settings (a tool call, the viewer, a save). A change
    to the rails or the self-login switch reaches open lanes now - open
    tunnels hung up, the login cookie synced - not at her next tool call."""
    global settings
    old, settings = settings, dict(new or {})
    if any(bool(old.get(k, d)) != bool(settings.get(k, d)) for k, d in _RULES):
        _soon(_rules_changed)


def _soon(coro_fn):
    """Run coro_fn() on the engine's loop if it's up; never starts it."""
    loop, thread = _loop, _thread
    if loop and thread and thread.is_alive():
        try:
            asyncio.run_coroutine_threadsafe(coro_fn(), loop)
        except RuntimeError:
            pass                                   # the loop is closing


async def _drop_tunnels():
    for srv in list(_egress.values()):
        egress.drop(srv)


async def _rules_changed():
    await _drop_tunnels()
    for lane in list(_lanes.values()):
        if lane.bridge:
            continue                               # a person's browser: its cookies are theirs
        try:
            await _sync_login(lane)
        except Exception as e:
            logger.warning(f"[BROWSER] login sync on '{lane.name}': {e!r}")


async def lane(name):
    """The lane, launched if needed, its self-login synced to the switch."""
    async with _lock(name):                        # two callers never launch one profile twice
        have = await (_person_lane(name) if name.startswith(EXT) else _lane(name))
    have.used = time.monotonic()
    if not have.bridge:
        await _sync_login(have)
    return have


async def _lane(name):
    global _pw
    want_headed = bool(settings.get('show_window'))
    have = _lanes.get(name)
    if have and have.headed != want_headed and _busy[name] <= 1:
        await _close_now(name)                     # the window switch flipped: relaunch, cookies stay
        have = None                                # (not under another call: that waits for the next)
    if have is None:
        if _pw is None:
            from playwright.async_api import async_playwright
            _pw = await async_playwright().start()
        g = name == HER                            # her lane is built guard-ready; the switch is read live
        args = []
        from core.ssl_utils import spki_pin
        pin = spki_pin()                           # her own cert trusted by its key, never ignore_https_errors
        if pin:
            args.append(f'--ignore-certificate-errors-spki-list={pin}')
        path = _profiles() / name
        path.mkdir(parents=True, exist_ok=True)
        kw = dict(headless=not want_headed, args=args, viewport=VIEWPORT, accept_downloads=not g,
                  channel='chromium')                  # one full Chromium, headless or not (chromium.py)
        # Every lane's traffic goes through its own egress proxy: the rails
        # (her lane) and Sapphire's SOCKS proxy (every lane) both live there.
        if name not in _egress:
            _egress[name] = await egress.start(
                lambda h, p, n=name: _egress_check(n, h, p),
                lambda h, p, words, n=name: _egress_report(n, h, p, words),
                _upstream)
        kw['proxy'] = {'server': f'http://127.0.0.1:{egress.port(_egress[name])}', 'bypass': ''}
        args.append('--force-webrtc-ip-handling-policy=disable_non_proxied_udp')   # no UDP around the proxy
        if g:
            kw['service_workers'] = 'block'        # nothing of a site's keeps running between visits
        else:                                      # MCP lanes test her voice UI: a fake mic, granted
            args += ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream',
                     '--autoplay-policy=no-user-gesture-required']
        try:
            ctx = await _launch(path, kw)
        except Exception as e:
            words = chromium.explain(e)
            if words:
                raise NotReady(words) from None
            raise
        ctx.set_default_timeout(ACTION_MS)
        ctx.set_default_navigation_timeout(NAV_MS)
        have = _lanes[name] = Lane(name, ctx, g, want_headed)
        logger.info(f"[BROWSER] lane '{name}' up ({'window' if want_headed else 'headless'})")
    if have.tab() is None:                         # every tab closed or crashed: a fresh one
        page = await have.ctx.new_page()
        have.watch(page)
        have.page = page
    return have


# --- a person's own browser (bridge.py) ------------------------------------------

async def _person_lane(name):
    """The lane on a paired browser: attach to its relay; nothing is launched
    and no tab is opened. The tab she last held there is taken again."""
    global _pw
    device = name[len(EXT):]
    b = bridge.get(device)
    have = _lanes.get(name)
    if have and have.bridge is not b:              # that browser reconnected: the old attachment is dead
        await _close_now(name)
        have = None
    if b is None or b.closed:
        raise NotReady(f"{device}'s browser is not connected right now.")
    if not b.info.get('v'):                        # its hello carries what the rails need (her own address there)
        raise NotReady(f"{device}'s browser is still connecting; try again in a moment.")
    if have is None:
        if _pw is None:
            from playwright.async_api import async_playwright
            _pw = await async_playwright().start()
        b.blocked = lambda url, local_ok, b=b: person_blocked_now(b, url, local_ok)
        relay = await b.start()
        try:
            browser = await _pw.chromium.connect_over_cdp(relay)
        except Exception as e:                     # the error names the relay's address, which is a secret path
            logger.warning(f"[BROWSER] could not attach to {device}'s browser: {type(e).__name__}")
            raise NotReady(f"{device}'s browser is not connected right now.") from None
        ctx = browser.contexts[0]
        ctx.set_default_timeout(ACTION_MS)
        ctx.set_default_navigation_timeout(NAV_MS)
        have = _lanes[name] = Lane(name, ctx, True, True)
        have.bridge, have.browser = b, browser
        logger.info(f"[BROWSER] lane '{name}' attached to that browser")
        if b.last is not None:
            try:
                await person_take(have, b.last)
            except Exception:
                b.last = None                      # closed, or no longer shared
    return have


async def person_take(lane, tab):
    """Her hand on one of their tabs: the extension's debugger goes on it and
    Playwright gets a page, which becomes the tab in hand."""
    b = lane.bridge
    await b.take(tab)
    for _ in range(100):
        for page in lane.ctx.pages:
            if not page.is_closed() and await tab_id(lane, page) == tab:
                lane.page, b.last = page, tab
                bridge.remember(b.name, tab)
                return page
        await asyncio.sleep(0.05)
    raise RuntimeError("that tab did not open to me")


async def tab_id(lane, page):
    """Which of their tabs this page is (the relay knows a page by its target)."""
    if page not in lane.tab_ids:
        side = await lane.ctx.new_cdp_session(page)
        try:
            info = await side.send('Target.getTargetInfo')
        finally:
            try:
                await side.detach()
            except Exception:
                pass
        lane.tab_ids[page] = lane.bridge.tab_of(info['targetInfo']['targetId'])
    return lane.tab_ids[page]


def _origin(url):
    u = urlsplit(url or '')
    try:
        return (u.scheme, (u.hostname or '').lower().rstrip('.'), u.port or (443 if u.scheme == 'https' else 80))
    except ValueError:
        return None


def person_blocked_now(b, url, local_ok=False):
    """Why she may not be at `url` in a person's browser, judged without
    asking any resolver (the tab list uses this for every tab), or ''."""
    u = urlsplit(url or '')
    if u.scheme in ('about', 'data', 'blob'):
        return ''
    if u.scheme not in ('http', 'https'):
        return f"{u.scheme or 'such'}: pages are off limits"
    mine = _origin(b.info.get('origin'))
    if mine and mine[1] and _origin(url) == mine and not settings.get('self_login'):
        return "that is this Sapphire's own page, which needs the 'Log in to this Sapphire' switch"
    if not local_ok and _is_local_name(u.hostname or ''):
        return f"{u.hostname} is a local network address, which they would have to share by hand"
    return ''


async def person_blocked(b, url, local_ok=False):
    """person_blocked_now, then the name is resolved here like her own lane's
    are (never when SOCKS is to resolve names: this machine must not hear them)."""
    why = person_blocked_now(b, url, local_ok)
    u, origin = urlsplit(url or ''), _origin(url)
    if why or local_ok or not origin or u.scheme not in ('http', 'https') or (_remote_dns() and _upstream()):
        return why
    if (await host_check(origin[1], origin[2]))[0]:
        return f"{u.hostname} is a local network address, which they would have to share by hand"
    return ''


async def _launch(path, kw):
    try:
        return await _pw.chromium.launch_persistent_context(str(path), **kw)
    except Exception as e:
        if kw['headless'] or chromium.kind(e):
            raise
        logger.warning(f"[BROWSER] no window possible here ({e}); running headless")
        kw['headless'] = True
        return await _pw.chromium.launch_persistent_context(str(path), **kw)


async def _sync_login(lane):
    want = bool(settings.get('self_login'))
    if lane.logged_in == want:
        return
    scheme, port = _self_origin()
    if want:
        from core.auth import mint_session_cookie
        value = mint_session_cookie()
        if value:
            for host in ('localhost', '127.0.0.1'):
                await lane.ctx.add_cookies([{'name': 'sapphire_session', 'value': value,
                                             'url': f'{scheme}://{host}:{port}/',
                                             'httpOnly': True, 'sameSite': 'Lax'}])
    else:
        await lane.ctx.clear_cookies(name='sapphire_session')
    lane.logged_in = want


# --- the rails -------------------------------------------------------------------

def host_port(url):
    """'host:port' of a web address, as the rails name a refusal."""
    u = urlsplit(url)
    try:
        port = u.port or (443 if u.scheme == 'https' else 80)
    except ValueError:
        port = 0
    return f"{u.hostname or ''}:{port}"


async def _egress_check(name, host, port):
    """egress.py asks before every connection a lane makes."""
    railed = guarded(name)
    if await _leave_unresolved(host, railed):
        return '', None                            # SOCKS resolves it: no lookup on this machine
    if not railed:
        return '', (await host_check(host, port))[1]
    why, ips = await host_check(host, port)        # her rails see the real address
    if why:
        lane = _lanes.get(name)
        if lane:
            lane.blocked = f"{host}:{port} ({why})"
        logger.info(f"[BROWSER] rails stopped {host}:{port}: {why}")
        return why, []
    return '', ips


def _egress_report(name, host, port, words):
    """egress.py could not reach host:port: keep the true reason for her,
    or Chromium only says ERR_TUNNEL_CONNECTION_FAILED and she guesses."""
    lane = _lanes.get(name)
    if lane:
        lane.unreached, lane.unreached_host = f"{host} could not be reached: {words}", host
    logger.info(f"[BROWSER] {name}: could not reach {host}:{port}: {words}")


# --- SOCKS: the lanes leave by Sapphire's proxy when it's on (core/socks_proxy.py)

def _upstream():
    """None (SOCKS off: dial directly), {host, port, user, password}, or
    {error} when SOCKS is on but unusable - egress then refuses, never goes
    direct (core's fail-closed rule). Read live, cached until core's next
    proxy-settings change (socks_proxy.register_invalidator)."""
    global _socks
    if _socks is None:
        _socks = _read_socks()
    return _socks or None


def _read_socks():
    import config
    if not getattr(config, 'SOCKS_ENABLED', False):
        return {}
    from core.socks_proxy import socks_target      # core's own parser: one reading of the fields
    host, port, why = socks_target()
    if why:
        return {'error': f"SOCKS is on but unusable: {why}"}
    try:
        from core.setup import get_socks_credentials
        user, password = get_socks_credentials()
    except Exception as e:
        return {'error': f"SOCKS is on but its login couldn't be read ({e})"}
    return {'host': host, 'port': port, 'user': user or '', 'password': password or '', 'far': None}


def _forget_socks():
    """A proxy-settings change: re-read, and hang up every open tunnel so
    nothing keeps leaving by the old route (a pooled HTTP/2 or WebSocket
    would otherwise ride it until the site closed it)."""
    global _socks
    _socks = None
    _soon(_drop_tunnels)


async def _proxy_far(up):
    """True when every address of the SOCKS proxy is out on the internet."""
    if up.get('far') is None:
        try:
            infos = await asyncio.get_running_loop().getaddrinfo(up['host'], up['port'], type=socket.SOCK_STREAM)
            up['far'] = bool(infos) and all(ipaddress.ip_address(i[4][0].split('%')[0]).is_global for i in infos)
        except (OSError, ValueError):
            up['far'] = False
    return up['far']


async def _leave_unresolved(host, railed):
    """True when the SOCKS proxy should resolve `host` (SOCKS_REMOTE_DNS on),
    so this machine's resolver never hears the name. For her railed lane only
    when the proxy itself is out on the internet: its exit can't reach this
    machine or this LAN, so the rails needn't see the address. A proxy on
    loopback or the LAN could, so there names are still resolved and checked
    here. Local-looking names and addresses always stay with the rails."""
    up = _upstream()
    if not up or up.get('error') or not _remote_dns() or _is_local_name(host):
        return False
    return not railed or await _proxy_far(up)


def _remote_dns():
    import config
    return bool(getattr(config, 'SOCKS_REMOTE_DNS', False))


LOCAL_SUFFIXES = ('.localhost', '.local', '.lan', '.internal', '.intranet', '.private', '.corp', '.home', '.box',
                  '.localdomain', '.home.arpa')


def _is_local_name(host):
    h = (host or '').lower().strip('[]').rstrip('.')       # "nas.lan." is nas.lan
    try:
        return not ipaddress.ip_address(h).is_global
    except ValueError:
        return h == 'localhost' or h.endswith(LOCAL_SUFFIXES) or '.' not in h


try:
    from core import socks_proxy as _sp
    _sp.register_invalidator(_forget_socks, owner='browser')
except Exception as e:                             # standalone (tests): the cache simply lives on
    logger.debug(f"[BROWSER] no SOCKS invalidator: {e}")


async def host_check(host, port):
    """(why her lane may not reach host:port or '', every address it resolved
    to, IPv4 first - [] when the name doesn't resolve). The addresses come back
    even with a why: the rails switch, not this, decides. Her own UI's port on
    this machine is the one local door, and only with the self-login switch on.
    All addresses, not the first: a box without IPv6 dies on a v6-first answer."""
    if host in ('localhost', '127.0.0.1') and port == _self_origin()[1]:
        return ('' if settings.get('self_login') else "her own UI needs the 'Log in to this Sapphire' switch"), ['127.0.0.1']
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (OSError, ValueError):                  # ValueError: a name IDNA can't encode
        return '', []
    ips = list(dict.fromkeys(ipaddress.ip_address(info[4][0].split('%')[0]) for info in infos))
    why = f"{host} is a local network address" if any(not ip.is_global for ip in ips) else ''
    return why, [str(ip) for ip in sorted(ips, key=lambda ip: ip.version)]


async def blocked_reason(url):
    """Why her lane may not load `url`, or '' when it may - her own
    navigations get these words before Chromium tries; egress.py enforces."""
    u = urlsplit(url)
    if u.scheme in ('data', 'blob', 'about'):
        return ''
    if u.scheme not in ('http', 'https'):
        return f"{u.scheme}: addresses are off limits"
    if _is_self(url):
        return '' if settings.get('self_login') else "her own UI needs the 'Log in to this Sapphire' switch"
    try:
        port = u.port or (443 if u.scheme == 'https' else 80)
    except ValueError:
        return "that address has a bad port"
    if await _leave_unresolved(u.hostname or '', True):
        return ''                                  # the far proxy resolves it; this box never asks
    return (await host_check(u.hostname or '', port))[0]
