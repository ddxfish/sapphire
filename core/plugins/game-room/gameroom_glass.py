"""The glass: a game on a satellite's touch screen, the server as the brain (2026-10-25).

A board whose glass passes its taps on (core/devices/glass.py `claim`, firmware
0.9.0: seven places tiling the glass) shows a game as a small PIL frame made
here, and every tap on it is a move. Nobody needs a browser once a table is on
a glass: the moves go through the same route functions the browser's board
uses (routes/play.py act / start / new_session), her seat plays, her lines are
spoken on the board, and the room (if open) redraws on the bus event.

The host owns the lifecycle and the frame: the seven rectangles from the
board's `zones`, each move's label centred in its place, EXIT on the top place
for every game. The game owns only its labels and the middle of the frame:

    engine.glass_actions(view, mem) -> {zone: slot}   slot = {'label', 'door': 'act'|'start'|'new-session', 'action', 'args'}
                                                           | {'label', 'set': {...}}   (a local change, redraw only)
                                                           | {'label', 'off': True}    (shown dim, does nothing)
    engine.render_glass(draw, box, view, mem)         the middle band, PIL ImageDraw, box = (x0, y0, x1, y1)

`view` is the player's view (engine.redact), `mem` the game's own per-session
scratch (what `set` writes). A private or sealed session is never put on a
glass in a room. In memory only: a restart ends every glass (the board's page
is back within the hold); a table nobody touched for IDLE_END ends by itself.
"""
import importlib.util
import io
import logging
import queue
import re
import sys
import threading
import time
from pathlib import Path

# .absolute(), never .resolve(): symlinked plugin dirs must not escape.
_PLUGIN_ROOT = Path(__file__).absolute().parent
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

import gameroom_core as gc

logger = logging.getLogger(__name__)

WHO = 'game-room'
HOLD = 60                     # the board keeps the table up this long after a frame
REFRESH = 30                  # the frame goes again this often while nothing moves: two chances inside HOLD
IDLE_END = 30 * 60            # a table nobody touched this long is left
EXIT = 'top'                  # the host's place, every game
TALK = 'middle'               # the host's place while the game leaves it empty (mid-hand): say something, it rides the next move
LISTEN_S = 8                  # the board's mic listens up to this long; it ends when the talking stops
STRIP = 22                    # the strip at the middle place's lower edge: its label, the queued words, "thinking..."
LABEL_H = 22                  # a row of labels above and below the picture: the small font with room around it
HEARD = re.compile(r'^Heard: "(.*)"$', re.S)   # the device door's word for a recording with speech in it
JPEG_Q = 80
PICTURE = 0.8                 # the game's picture takes this much of the glass's width; its height runs from the top row of labels to the strip
FELT = (18, 52, 36)
GRID = (26, 66, 46)           # the places' outlines, barely there
INK = (235, 240, 235)
DIM = (120, 132, 125)

_lock = threading.Lock()
_glasses = {}                 # session -> _Glass
_DOORS = None                 # 'act' | 'start' | 'new-session' -> routes/play.py function (loaded once)


def _doors():
    """The route functions, called in process: they hold the session lock and run her seat."""
    global _DOORS
    if _DOORS is None:
        spec = importlib.util.spec_from_file_location('gameroom_glass_play', _PLUGIN_ROOT / 'routes' / 'play.py')
        play = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(play)
        _DOORS = {'act': play.act, 'start': play.start, 'new-session': play.new_session}
    return _DOORS


def can(engine):
    """Does this game draw a glass table?"""
    return bool(engine) and callable(getattr(engine, 'glass_actions', None)) and callable(getattr(engine, 'render_glass', None))


def boards():
    """The boards a table can go on: [{'id', 'label', 'busy'}], every glass that passes its taps on."""
    from core.devices import engine, glass
    out = []
    rows = engine.rows()
    for device_id, _w, _h, _fps in glass.stream_boards(online_only=True):
        try:
            if not glass.takes(device_id)['taps']:
                continue
        except Exception:
            continue
        row = rows.get(device_id) or {}
        out.append({'id': device_id, 'label': str(row.get('label') or row.get('name') or device_id), 'busy': glass.owner(device_id)})
    return out


def active(session):
    """The board a session's table is on, or None."""
    with _lock:
        g = _glasses.get(session)
    return g.device if g else None


def begin(game, session, device):
    """Put a session's table on a board. Raises ValueError (no such game, or one
    with no glass table), PermissionError (a private or sealed session), RuntimeError
    (the session or the board is already taken), LookupError (no such glass)."""
    game, session, device = str(game or '').strip(), str(session or '').strip(), str(device or '').strip().lower()
    meta, engine = gc.get_game(game)
    if not engine:
        raise ValueError(f'Unknown game: {game}')
    if not can(engine):
        raise ValueError(f"{meta.get('title', game)} has no glass table.")
    if not session:
        raise ValueError('session required')
    s = gc._chat_settings(session)
    if s is None or s.get('private_chat'):
        raise PermissionError('A private session is never shown on a glass in the room.')
    from core.devices import glass
    with _lock:
        if session in _glasses:
            raise RuntimeError(f"'{session}' is already on a glass.")
        takes = glass.claim(device, on_press=lambda zone, how: _press(session, zone, how),
                            on_gone=lambda: end(session, why='the player left'), hold=HOLD, who=WHO)
        g = _glasses[session] = _Glass(game, engine, session, device, takes)
    g.thread.start()
    logger.info(f"game-room: {session!r} ({game}) is on the glass of {device}")
    _changed(g)
    return g


def end(session, why=''):
    """Take a session's table off its board. Safe to call twice; the board's own page is back."""
    with _lock:
        g = _glasses.pop(session, None)
    if not g:
        return False
    g.alive = False
    g.presses.put(None)
    from core.devices import glass
    try:
        glass.release(g.device)
    except Exception as e:
        logger.debug(f'game-room: glass release: {e}')
    logger.info(f"game-room: {session!r} left the glass of {g.device}{': ' + why if why else ''}")
    _changed(g)
    return True


def end_all():
    for session in list(_glasses):
        end(session, why='the room closed')


def _press(session, zone, how):
    with _lock:
        g = _glasses.get(session)
    if g:
        g.presses.put((zone, how))


def _changed(g):
    """Every open room redraws (room-host.js listens); a session name is a chat name: ephemeral."""
    try:
        from core.event_bus import publish
        publish('game_state_changed', {'game': g.game, 'session': g.session, 'glass': active(g.session)}, ephemeral=True)
    except Exception as e:
        logger.debug(f'game-room: bus: {e}')


class _Glass:
    def __init__(self, game, engine, session, device, takes):
        self.game, self.engine, self.session, self.device, self.takes = game, engine, session, device, takes
        self.mem = {}
        self.say = ''                 # what the player said at the board, waiting to ride the next move (play.py act `say`)
        self.note = ''                # one frame's word in the strip: "listening...", "nothing heard"
        self.presses = queue.Queue()
        self.alive = True
        self.touched = time.monotonic()
        self.spoken = None            # her talk seq already spoken (None: baseline at the first frame)
        self.thread = threading.Thread(target=self._run, daemon=True, name=f'glass-{session}')

    # --- the worker: one thread, every move in order -------------------------------------

    def _run(self):
        try:
            self._show()
        except Exception as e:
            logger.warning(f'game-room: glass first frame failed: {e}')
        while self.alive:
            try:
                press = self.presses.get(timeout=REFRESH)
            except queue.Empty:
                if time.monotonic() - self.touched > IDLE_END:
                    end(self.session, why='idle')
                    break
                self._safe(self._show)
                continue
            if press is None:
                break
            self.touched = time.monotonic()
            self._safe(self._move, *press)

    def _safe(self, fn, *args):
        try:
            fn(*args)
        except Exception as e:
            logger.warning(f'game-room: glass {fn.__name__} failed: {e}', exc_info=True)

    def _view(self):
        with gc.session_lock(self.game, self.session):
            state = gc.load_state(self.game, self.session)
        view = self.engine.redact(state) if state else {'session': None}
        if self.spoken is None:
            self.spoken = _max_seq(view.get('talk'))
        return view

    def _slots(self, view):
        slots = dict(self.engine.glass_actions(view, self.mem) or {})
        slots[EXIT] = {'label': 'EXIT'}
        if not slots.get(TALK):                        # the game left the middle empty: the player may talk
            slots[TALK] = {'label': f'TALK  "{self.say}"' if self.say else 'TALK', 'talk': True}
        if self.note:
            slots[TALK] = {'label': self.note, 'off': True}
            self.note = ''
        return slots

    def _move(self, zone, how):
        if zone == EXIT:
            end(self.session, why='exit')
            return
        view = self._view()
        slot = self._slots(view).get(zone)
        if not slot or slot.get('off'):
            self._show(view)
            return
        if isinstance(slot.get('set'), dict):
            self.mem.update(slot['set'])
            self._show(view)
            return
        if slot.get('talk'):
            self._listen(view)
            return
        door = _doors().get(str(slot.get('door') or ''))
        if not door:
            self._show(view)
            return
        self._show(view, thinking=True)
        body = {'session': self.session}
        if slot.get('door') == 'act':
            body['action'] = str(slot.get('action') or '')
            body['args'] = dict(slot.get('args') or {})
            if self.say:                               # the words ride the move, as the composer's do in the browser
                body['say'], self.say = self.say, ''
        out = door(self.game, body=body)
        if isinstance(out, tuple):                     # ({'error': ...}, code): nothing happened
            logger.info(f"game-room: glass move {zone} refused: {out[0].get('error')}")
            self._show()
            return
        view = self._view()
        self._show(view)
        self._speak(view)
        _changed(self)

    # --- the player's words, from the board's mic ------------------------------------------

    def _listen(self, view):
        """The board listens (the device door: its mic, the speech-to-text gate) and what was
        said waits in the strip until the next move carries it. Said again: replaced."""
        self.note = 'listening...'
        self._show(view)
        try:
            from core.devices import engine as devices
            said, ok = devices.run(self.device, 'mic', 'listen', str(LISTEN_S), owner=True)
        except Exception as e:
            said, ok = str(e), False
        heard = HEARD.match(str(said or '').strip()) if ok else None
        if heard and heard.group(1).strip():
            self.say = heard.group(1).strip()[:400]
        else:
            self.note = 'nothing heard' if ok else 'the board could not listen'
            logger.info(f'game-room: glass listen on {self.device}: {said}')
        self._show(view)

    # --- her lines, on the board -------------------------------------------------------

    def _speak(self, view):
        fresh = [t for t in view.get('talk') or [] if t.get('who') == 'ai' and not t.get('via')
                 and t.get('seq', 0) > (self.spoken or 0)]
        self.spoken = max(self.spoken or 0, _max_seq(view.get('talk')))
        if not fresh:
            return
        text = ' ... '.join(str(t.get('text') or '') for t in fresh).strip()
        if not text:
            return
        try:
            from core.devices import voice
            cfg = gc.session_cfg(self.session)
            voice.say(self.device, text[:2000], gc._chat_settings(self.session), voice=cfg.get('voice') or None)
        except Exception as e:
            logger.info(f'game-room: her line was not spoken on {self.device}: {e}')

    # --- the frame ---------------------------------------------------------------------

    def _show(self, view=None, thinking=False):
        view = self._view() if view is None else view
        jpeg = frame(self.engine, self.takes, view, self._slots(view), self.mem, thinking)
        from core.devices import glass
        glass.stream_frame(self.device, jpeg)


def _max_seq(talk):
    return max((t.get('seq', 0) for t in talk or []), default=0)


def _font(size):
    from PIL import ImageFont
    return ImageFont.load_default(size=size)


def _centred(draw, box, text, font, fill):
    """Text centred in a box; a label too wide for its place is drawn one size down, then cut short."""
    from PIL import ImageFont
    x0, y0, x1, y1 = box
    for f in (font, ImageFont.load_default(size=max(10, int(font.size * 0.75)))):
        l, t, r, b = draw.textbbox((0, 0), text, font=f)
        if r - l <= x1 - x0 - 6:
            break
    while len(text) > 4 and r - l > x1 - x0 - 6:
        text = text[:-4].rstrip() + '...'
        l, t, r, b = draw.textbbox((0, 0), text, font=f)
    draw.text(((x0 + x1 - (r - l)) / 2 - l, (y0 + y1 - (b - t)) / 2 - t), text, font=f, fill=fill)


def _label_box(z, h, top, bottom):
    """Where a place's label goes, inside the place's own hit box: a place touching the
    top edge of the glass has its label in the row above the picture, one touching the
    bottom edge in the row under it; a place touching neither keeps its own box."""
    x0, x1 = z['x'], z['x'] + z['w'] - 1
    if z['y'] <= 0:
        return (x0, 0, x1, top - 1)
    if z['y'] + z['h'] >= h:
        return (x0, bottom, x1, min(h, bottom + LABEL_H) - 1)
    return (x0, z['y'], x1, z['y'] + z['h'] - 1)


def frame(engine, takes, view, slots, mem, thinking=False):
    """One JPEG of the table. Every label sits inside the hit box of its place (the
    board's, unchanged), so a finger on a word lands in that place: the top row of
    labels above the game's picture, the bottom row under it, and the middle place's
    label (DEAL, TALK, the queued words, "thinking...") on a strip at the middle place's
    lower edge. The picture (render_glass) takes PICTURE of the width, centred, from
    the top row down to the strip; the places' outlines are drawn barely there over the
    whole glass. Labels dim when off or while her seat thinks. Sides multiples of 16."""
    from PIL import Image, ImageDraw
    w, h = int(takes['w']) // 16 * 16, int(takes['h']) // 16 * 16
    ox, oy = (int(takes['w']) - w) // 2, (int(takes['h']) - h) // 2     # the board centres a smaller frame
    zones = [{'name': z['name'], 'x': z['x'] - ox, 'y': z['y'] - oy, 'w': z['w'], 'h': z['h']} for z in takes['zones']]
    mid = next((z for z in zones if z['name'] == TALK), None)
    top = LABEL_H if any(z['y'] <= 0 for z in zones) else 0
    bottom = mid['y'] + mid['h'] if mid else h - top                    # the picture's lower edge: the middle place's
    pw = int(w * PICTURE)
    px = (w - pw) // 2
    talk = slots.get(TALK) or {}
    strip = STRIP if talk.get('label') or thinking else 0
    game = (px, top, px + pw - 1, bottom - strip - 1)
    line = (mid['x'], bottom - strip, mid['x'] + mid['w'] - 1, bottom - 1) if mid else (px, bottom - strip, px + pw - 1, bottom - 1)
    im = Image.new('RGB', (w, h), FELT)
    d = ImageDraw.Draw(im)
    small = _font(14)
    for z in zones:
        d.rectangle((z['x'], z['y'], z['x'] + z['w'] - 1, z['y'] + z['h'] - 1), outline=GRID)
    try:
        engine.render_glass(d, game, view, mem)
    except Exception as e:
        logger.warning(f'game-room: render_glass failed: {e}')
    for z in zones:
        slot = slots.get(z['name'])
        if not slot or not slot.get('label') or z['name'] == TALK:
            continue
        _centred(d, _label_box(z, h, top, bottom), str(slot['label']), small, DIM if (slot.get('off') or thinking) else INK)
    if thinking:
        _centred(d, line, 'thinking...', small, DIM)
    elif strip:
        _centred(d, line, str(talk['label']), small, DIM if talk.get('off') else INK)
    buf = io.BytesIO()
    im.save(buf, 'JPEG', quality=JPEG_Q)
    return buf.getvalue()
