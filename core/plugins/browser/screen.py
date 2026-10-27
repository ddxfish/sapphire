# core/plugins/browser/screen.py - the viewer's eyes and hands on a lane
#
# One Chromium screencast per lane (CDP Page.startScreencast), shared by every
# viewer watching it: a frame comes only when the page repaints, goes to each
# viewer's sink, and a slow viewer just misses frames (routes/view.py keeps the
# newest). The cast follows the tab in hand and survives the lane relaunching,
# and while anyone watches the lane is kept awake. Input goes in through
# Playwright's mouse and keyboard as the person at the viewer, so the rails'
# no-password-typing rule (hers) does not apply; the network rails still do,
# because the traffic still leaves through egress.py. Everything here runs on
# the engine's loop.
import asyncio
import base64
import logging
import shutil
import time

from . import engine

logger = logging.getLogger(__name__)

FOLLOW_S = 0.5             # how often the cast checks its tab and the page's title
QUALITY = 60               # JPEG quality: ~120 KB a frame at 1280x800
_screens = {}              # lane name -> Screen


class Screen:
    def __init__(self, name):
        self.name = name
        self.sinks = {}                # viewer id -> sink(kind, data); called on this loop
        self.lane = self.page = self.cdp = None
        self.url = ''                  # the address the cast started on
        self.state = None              # the last state sent, to send only changes
        self.task = None
        self.acks = set()              # frame acks in flight (a task nobody holds can be collected)

    def send(self, kind, data):
        for sink in list(self.sinks.values()):
            try:
                sink(kind, data)
            except Exception as e:
                logger.debug(f"[BROWSER] viewer sink: {e}")

    async def follow(self):
        while True:
            while self.sinks:
                try:
                    await self._tick()
                except Exception as e:
                    logger.debug(f"[BROWSER] screen {self.name}: {e}")
                await asyncio.sleep(FOLLOW_S)
            await self._stop_cast()
            self.lane = self.page = None       # the next viewer starts a fresh cast
            self.url = ''
            if not self.sinks:                 # nobody came back while the cast was stopping
                return

    async def _tick(self):
        lane = engine._lanes.get(self.name)
        page = lane.tab() if lane else None
        if lane:
            lane.used = time.monotonic()           # watched = awake (the app lets go when its tab is hidden)
        url = page.url if page else ''
        if page is not self.page or lane is not self.lane or url != self.url or (page and self.cdp is None):
            # a new address can mean a new renderer process, and a screencast
            # dies with the renderer it started on: restart it
            await self._stop_cast()
            self.lane, self.page, self.url = lane, page, url
            if page:
                await self._start_cast(lane, page)
        state = await _state(lane)
        if state != self.state:
            self.state = state
            self.send('state', state)

    async def _start_cast(self, lane, page):
        cdp = await lane.ctx.new_cdp_session(page)

        async def ack(f):
            try:
                await cdp.send('Page.screencastFrameAck', {'sessionId': f['sessionId']})
            except Exception:
                pass                               # the cast ended under us

        def frame(f):                              # a plain function: CDP events don't await coroutines
            self.send('frame', base64.b64decode(f['data']))
            t = asyncio.ensure_future(ack(f))
            self.acks.add(t)
            t.add_done_callback(self.acks.discard)
        cdp.on('Page.screencastFrame', frame)
        vp = page.viewport_size or engine.VIEWPORT
        try:
            await cdp.send('Page.startScreencast', {'format': 'jpeg', 'quality': QUALITY,
                                                    'maxWidth': vp['width'], 'maxHeight': vp['height']})
        except Exception:
            try:
                await cdp.detach()                 # the renderer swapped mid-start: the next tick tries again
            except Exception:
                pass
            raise
        self.cdp = cdp

    async def _stop_cast(self):
        cdp, self.cdp = self.cdp, None
        if cdp:
            try:
                await cdp.send('Page.stopScreencast')
                await cdp.detach()
            except Exception:
                pass                                   # the page or lane is already gone


async def _state(lane):
    if lane is None:
        return {'asleep': True}
    page = lane.tab()
    tabs = []
    for p in lane.ctx.pages:
        if p.is_closed():
            continue
        try:
            title = await p.title()
        except Exception:
            title = ''
        tabs.append({'title': title[:80], 'url': p.url[:300], 'active': p is page})
    vp = (page.viewport_size if page else None) or engine.VIEWPORT
    return {'asleep': False, 'tabs': tabs, 'url': page.url if page else '', 'width': vp['width'],
            'height': vp['height'], 'guarded': lane.guarded and engine.guarded(lane.name)}


# --- watching ------------------------------------------------------------------------

async def attach(name, vid, sink):
    """Start sending `name`'s frames and state to `sink(kind, data)`."""
    screen = _screens.get(name)
    if screen is None:
        screen = _screens[name] = Screen(name)
    screen.sinks[vid] = sink
    screen.state = None                        # the new viewer gets the full state at once
    if screen.task is None or screen.task.done():
        screen.task = asyncio.get_running_loop().create_task(screen.follow())
    elif screen.page is not None:
        await screen._stop_cast()              # restarted next tick: the new viewer's first frame
        screen.page = None


async def detach(name, vid):
    screen = _screens.get(name)
    if screen:
        screen.sinks.pop(vid, None)


# --- hands ---------------------------------------------------------------------------

def _touch(lane):
    lane.viewer_at = time.monotonic()          # her next look says the page was moved


async def _page(name):
    lane = engine._lanes.get(name)
    page = lane.tab() if lane else None
    if page is None:
        raise engine.NotReady("Her browser is asleep.")
    return lane, page


async def mouse(name, kind, x, y, button='left', dx=0, dy=0):
    lane, page = await _page(name)
    vp = page.viewport_size or engine.VIEWPORT
    x, y = max(0, min(float(x), vp['width'] - 1)), max(0, min(float(y), vp['height'] - 1))
    if button not in ('left', 'middle', 'right'):
        button = 'left'
    await page.mouse.move(x, y)
    if kind == 'down':
        await page.mouse.down(button=button)
    elif kind == 'up':
        await page.mouse.up(button=button)
    elif kind == 'wheel':
        await page.mouse.wheel(float(dx), float(dy))
    if kind != 'move':
        _touch(lane)


async def key(name, kind, key_name):
    lane, page = await _page(name)
    try:
        await (page.keyboard.down(key_name) if kind == 'down' else page.keyboard.up(key_name))
    except Exception:
        return                                 # a key Playwright has no name for: dropped
    _touch(lane)


async def text(name, words):
    lane, page = await _page(name)
    await page.keyboard.insert_text(str(words)[:10000])
    _touch(lane)


async def nav(name, what, url=''):
    lane, page = await _page(name)
    _touch(lane)
    if what == 'back':
        await page.go_back()
    elif what == 'forward':
        await page.go_forward()
    elif what == 'reload':
        await page.reload()
    else:
        if lane.guarded and engine.guarded(name):
            why = await engine.blocked_reason(url)
            if why:
                raise engine.Blocked(f"The guard rails stopped {url}: {why}.")
        await page.goto(url, wait_until='domcontentloaded')


async def tab(name, index):
    lane, _ = await _page(name)
    pages = [p for p in lane.ctx.pages if not p.is_closed()]
    if 0 <= int(index) < len(pages):
        lane.page = pages[int(index)]
        await lane.page.bring_to_front()
        _touch(lane)


async def wake(name):
    await engine.lane(name)                    # a lane always comes up with a tab in hand


async def release(name, keys, buttons):
    """A viewer left: let go of whatever it was holding down in her page,
    or her own clicks later land as Ctrl-clicks."""
    lane = engine._lanes.get(name)
    page = lane.tab() if lane else None
    if page is None:
        return
    for k in keys:
        try:
            await page.keyboard.up(k)
        except Exception:
            pass
    for b in buttons:
        try:
            await page.mouse.up(button=b)
        except Exception:
            pass


async def forget(name):
    """Close the lane and delete its whole profile: cookies, site storage,
    history, cache. The next call starts a fresh one."""
    root = engine._profiles().resolve()
    path = (root / name).resolve()
    if path.parent != root:
        raise ValueError("not a browser profile")
    async with engine._lock(name):             # no launch or close of this lane while it goes
        if engine._busy[name]:
            raise engine.NotReady("She's using her browser right now; clear it again in a moment.")
        await engine._close_now(name)
        loop = asyncio.get_running_loop()
        for _ in range(10):                    # Windows: Chromium's files close a beat after it exits
            if not path.exists():
                break
            await loop.run_in_executor(None, _rmtree, path)   # off the loop: every lane's traffic runs on it
            await asyncio.sleep(0.5)
    if path.exists():
        raise OSError("part of the profile is still in use; close the browser window and try again")
    logger.info(f"[BROWSER] lane '{name}' forgotten (profile deleted)")


def _rmtree(path):
    try:
        from core.fs_utils import rmtree_robust    # clears read-only files on Windows
        rmtree_robust(path)
    except Exception as e:
        logger.debug(f"[BROWSER] profile delete: {e}")
        shutil.rmtree(path, ignore_errors=True)
