# core/devices/glass.py - a satellite's small screen as a window into its chat.
#
# A board with a screen shows the name of the chat it talks in and that chat's
# scene (chat.background, the Scene Backgrounds library) behind its clock. All
# the image work is done here; the board stores what it is sent and paints it.
#
# sync(device_id) compares what the board says it shows (/health.screen) with
# what it should and sends only the difference. It is called when the board's
# events stream opens (a boot, a new link), when its settings are saved, and by
# a listener on the event bus when a chat's scene changes or the chat in use
# changes (core.event_bus on()). A private chat is never named or pictured on a screen in a room.
import io
import logging
import re
import threading
import time

logger = logging.getLogger(__name__)

NAME_MAX = 31                 # bytes of the chat's name the board keeps
_EVENTS = ('chat_settings_changed', 'chat_switched', 'chat_renamed', 'chat_deleted')
_lock = threading.Lock()
_worker = None
_dirty = threading.Event()
_busy = {}                    # device id -> a lock: one sync at a time per board


def _name(text, most=NAME_MAX):
    """A name as the board can keep it: cut on a whole character."""
    return str(text or '').encode('utf-8')[:most].decode('utf-8', 'ignore')


def cover(raw, width, height):
    """An image cropped to fill width x height, as the bytes a small panel
    paints (RGB565 big-endian). A background fills the glass; her `picture`
    fits inside it instead (satellite.rgb565)."""
    import numpy as np
    from PIL import Image, ImageOps
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(raw))).convert('RGB')
    img = ImageOps.fit(img, (width, height), Image.Resampling.LANCZOS)
    a = np.asarray(img, dtype=np.uint16)
    packed = (a[..., 0] >> 3) << 11 | (a[..., 1] >> 2) << 5 | (a[..., 2] >> 3)
    return packed.astype('>u2').tobytes()


BRAIN_MAX = 47                # bytes of the model's name the board keeps
_TRIM = re.compile(r'^#[0-9a-fA-F]{6}$')


def _brain(settings):
    """The model that answers in this chat, by name, as the chat's own
    resolver would pick it now. '' when it cannot be said. Asks no provider."""
    try:
        from core.chat.llm_providers import resolve
        picked = resolve.resolve(settings.get('llm_primary'), settings.get('llm_model'), health='skip')
        said = str(picked.effective_model or picked.display_name)
        return _name(said.rstrip('/').rsplit('/', 1)[-1], BRAIN_MAX)      # accounts/fireworks/models/glm-5p3 -> glm-5p3: the page is 320 px wide
    except Exception:
        return ''


def wanted(part):
    """What this part's screen should show: {'chat', 'scene', 'brain', 'trim'}.
    All empty for a private chat or one that is gone."""
    from core.api_fastapi import get_system
    sm = get_system().llm_chat.session_manager
    chat = str((part.get('config') or {}).get('chat') or '').strip() or sm.get_active_chat_name()
    settings = sm.get_settings_for(chat)
    if settings is None or settings.get('private_chat'):
        return {'chat': '', 'scene': '', 'brain': '', 'trim': ''}
    trim = str(settings.get('trim_color') or '').strip().lower()
    return {'chat': _name(chat), 'scene': str(settings.get('background') or ''),
            'brain': _brain(settings), 'trim': trim if _TRIM.match(trim) else ''}


def _words_differ(screen, want):
    """The name, the model, the trim: only what this board's program states is compared."""
    return any(k in screen and screen.get(k) != want[k] for k in ('chat', 'brain', 'trim'))


def _scene_bytes(scene):
    from core.routes import backgrounds
    if not backgrounds._NAME_RE.match(scene):
        return None
    path = backgrounds._full_path(scene)
    if not backgrounds._contained(path) or not path.is_file():
        return None
    return path.read_bytes()


def sync(device_id):
    """Bring one device's screen in line with its chat. Never raises. Says
    what it sent ([] = it already showed it), or None when the board could
    not be asked or told."""
    from core.devices import engine
    from core.devices.drivers import satellite
    device_id = str(device_id or '').strip().lower()
    with _lock:
        one = _busy.setdefault(device_id, threading.Lock())
    did = []
    with one:
        try:
            row = engine.rows().get(device_id)
            if not row or not row.get('enabled', True):
                return did
            for part in row.get('parts', []):
                if part.get('driver') != 'satellite':
                    continue
                config = dict(part.get('config') or {})
                secrets = engine._part_secrets(row['id'], 'satellite')
                device = engine._brief(row)
                screen = satellite._health(device, config, secrets, fresh=True).get('screen')
                if not isinstance(screen, dict) or 'chat' not in screen:      # no screen, or a program before 0.5.3
                    continue
                want = wanted(part)
                scene = want['scene']
                if _words_differ(screen, want):
                    satellite.screen_chat(config, secrets, want['chat'], want['brain'], want['trim'])
                    did.append(f"chat {want['chat'] or '(none)'} (model {want['brain'] or '-'}, trim {want['trim'] or '-'})")
                if screen.get('background', '') != scene:
                    raw = _scene_bytes(scene) if scene else None
                    if raw:
                        w, h = int(screen['w']), int(screen['h'])
                        satellite.screen_scene(config, secrets, scene, w, h, cover(raw, w, h))
                        did.append(f'scene {scene}')
                    elif screen.get('background'):
                        satellite.screen_scene(config, secrets, '')
                        did.append('scene cleared')
                if did:
                    satellite.forget_health(row['id'])
                    logger.info(f"[DEVICES] {row['id']}: screen now shows {', '.join(did)}")
        except Exception as e:
            logger.info(f"[DEVICES] {device_id}: its screen was not brought up to date: {e}")
            return None
    return did


def behind(config, screen):
    """Does this /health screen block show something other than its chat?
    The level check: the driver's status() asks at every probe, so a push
    that was lost (the board was still starting, the link dropped) is made
    good at the next one, whatever was missed."""
    if not isinstance(screen, dict) or 'chat' not in screen:
        return False
    try:
        want = wanted({'config': config})
    except Exception:
        return False
    return _words_differ(screen, want) or screen.get('background', '') != want['scene']


RETRY_AFTER = (3, 10)         # seconds: a board that has just linked may not answer its own door yet


def _sync_until_told(device_id):
    for wait in (0,) + RETRY_AFTER:
        if wait:
            time.sleep(wait)
        if sync(device_id) is not None:
            return


def sync_soon(device_id):
    threading.Thread(target=_sync_until_told, args=(device_id,), daemon=True, name='device-glass').start()


def sync_all():
    """Every satellite that has said it has a screen."""
    from core.devices import engine
    from core.devices.drivers import satellite
    for row in engine.rows().values():
        if row.get('enabled', True) and any(p.get('driver') == 'satellite' for p in row.get('parts', [])) \
                and 'screen' in satellite._has(row):
            sync(row['id'])


def _work():
    while True:
        _dirty.wait()
        _dirty.clear()
        try:
            sync_all()
        except Exception as e:
            logger.warning(f"[DEVICES] screens were not brought up to date: {e}")


def start():
    """Follow the bus: a chat's scene or the chat in use changed. Safe to call
    twice. The listener only raises a flag; the worker does the sending, so a
    board that is slow to answer never holds up whoever published."""
    global _worker
    from core.event_bus import get_event_bus
    with _lock:
        if _worker and _worker.is_alive():
            return
        _worker = threading.Thread(target=_work, daemon=True, name='device-glass')
        _worker.start()
        get_event_bus().on(_EVENTS, lambda event: _dirty.set())
