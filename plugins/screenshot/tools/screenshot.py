# Simple Screenshot — best-effort full-canvas screen capture for the AI.
#
# source="local" (default): silently capture the screen of the machine Sapphire
#   runs on. Backend by platform / display server:
#     Windows, Linux/X11, macOS → mss / screencapture (full virtual desktop)
#     Linux/Wayland             → grim (wlroots) → gnome-screenshot → spectacle (KDE)
#                                 → xdg-desktop-portal (GNOME, last resort)
#   The portal needs no extra package (drives gdbus, which ships with glib): it
#   captures via GNOME's own shutter path, so GNOME Wayland works out of the box.
#   First use may show a one-time permission dialog; granted after that.
#   If nothing works (headless, no portal), it returns guidance telling the model
#   to ask the user to paste a screenshot — Sapphire accepts pasted images.
#
# source="user": ask the user's BROWSER to share its screen. Fires a
#   getDisplayMedia "share screen" prompt in the web UI (the user picks a
#   screen/window), captures one frame, and returns it as the tool result.
#   Needs a secure context (https/localhost) and a user click — so it isn't
#   silent. If no browser responds in time (no web UI open, voice-only client),
#   it falls back to guidance asking the user to paste a screenshot.
#   The handshake (nonce + blocking tool + /pending + /capture route) mirrors
#   the webcam plugin.
#
# Full CANVAS only — all monitors as one image. Simplest, and the AI sees
# everything. mss is imported lazily so the plugin still loads on systems that
# don't use it (e.g. Wayland, which never touches mss).

import base64
import io
import logging
import os
import platform
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import types
from pathlib import Path

logger = logging.getLogger(__name__)

ENABLED = True
EMOJI = '🖥️'
AVAILABLE_FUNCTIONS = ['get_screenshot']

TOOLS = [
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "get_screenshot",
            "description": (
                "Capture the screen so you can see what's on it. "
                "source='local' (default) grabs the Sapphire host machine's full screen silently. "
                "source='user' opens a 'share your screen' prompt in the user's browser — they "
                "pick a screen/window and that frame is returned. "
                "Use 'user' when Sapphire runs on a different machine than the user, or when local "
                "capture isn't available; use 'local' to see the host's own screen. "
                "Call when the user asks you to look at their screen, a window, or an on-screen error."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {
                        "type": "string",
                        "enum": ["local", "user"],
                        "description": "local = capture the Sapphire host's screen automatically (default); user = prompt the user's browser to share its screen"
                    }
                },
                "required": []
            }
        }
    }
]

_MAX_DIM_DEFAULT = 1568

PASTE_HINT = (
    "Couldn't capture the screen automatically on this system. Ask the user to take a "
    "screenshot (PrtSc, Win+Shift+S, or their OS shortcut) and paste or upload it into "
    "the chat — you'll be able to see it then."
)

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_PNG_IEND = b"\x49\x45\x4e\x44\xae\x42\x60\x82"   # final 8 bytes of every complete PNG


def _max_dim():
    try:
        from core.plugin_loader import plugin_loader
        s = plugin_loader.get_plugin_settings("screenshot")
        return int(s.get("max_dimension", _MAX_DIM_DEFAULT)) or _MAX_DIM_DEFAULT
    except Exception:
        return _MAX_DIM_DEFAULT


def _encode_png(raw_png_bytes):
    """Resize a PNG byte string down to the long-edge cap, return base64 PNG.
    If Pillow is missing, returns the original bytes unresized (best effort)."""
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(raw_png_bytes)).convert("RGB")
        cap = _max_dim()
        w, h = im.size
        scale = min(1.0, cap / max(w, h))
        if scale < 1.0:
            im = im.resize((max(1, int(w * scale)), max(1, int(h * scale))))
        out = io.BytesIO()
        im.save(out, format="PNG", optimize=True)
        raw_png_bytes = out.getvalue()
    except Exception as e:
        logger.warning(f"[SCREENSHOT] resize skipped ({type(e).__name__}: {e})")
    return base64.b64encode(raw_png_bytes).decode("ascii")


def _image_result(b64, note):
    return {"text": note, "images": [{"data": b64, "media_type": "image/png"}]}, True


# ── backends ──────────────────────────────────────────────────────────────────

def _capture_mss():
    """Windows / Linux-X11: full virtual desktop. monitors[0] is the bounding box
    of every monitor (the whole canvas). Raises ImportError if mss isn't installed."""
    import mss
    import mss.tools
    with mss.mss() as sct:
        shot = sct.grab(sct.monitors[0])
        return mss.tools.to_png(shot.rgb, shot.size)


def _capture_macos():
    return _run_to_tempfile(["screencapture", "-x", "{path}"])


def _pictures_dir():
    """Resolve the user's Pictures dir — where xdg-desktop-portal drops a
    screenshot. Tries xdg-user-dir, falls back to ~/Pictures."""
    try:
        out = subprocess.run(["xdg-user-dir", "PICTURES"], timeout=3,
                             capture_output=True, text=True, check=False)
        p = Path(out.stdout.strip())
        if p.is_dir():
            return p
    except Exception:
        pass
    p = Path.home() / "Pictures"
    return p if p.is_dir() else None


def _png_complete(path):
    """True once `path` is a fully-written PNG — starts with the magic and ends
    with the IEND chunk. The portal creates the file then writes it async, so we
    must wait for IEND before reading or we get a truncated/0-byte frame."""
    try:
        if path.stat().st_size < 16:
            return False
        with open(path, "rb") as f:
            head = f.read(8)
            f.seek(-8, os.SEEK_END)
            tail = f.read(8)
        return head == _PNG_MAGIC and tail == _PNG_IEND
    except OSError:
        return False


def _capture_portal():
    """Linux Wayland last resort (GNOME etc.): xdg-desktop-portal Screenshot.
    The portal saves a PNG into the user's Pictures dir and returns a URI we
    can't easily read back from a one-shot gdbus call — so we snapshot the dir,
    fire the capture, then grab the new PNG once it's fully written and delete it.

    Dependency-free (shells out to gdbus, which ships with glib). First use may
    show a one-time permission dialog; once granted it's silent (with GNOME's
    shutter flash/sound). Returns PNG bytes or None."""
    if not shutil.which("gdbus"):
        return None
    pics = _pictures_dir()
    if not pics:
        return None
    before = set(pics.glob("*.png"))
    start = time.time()
    token = "sapphire" + os.urandom(4).hex()
    cmd = [
        "gdbus", "call", "--session",
        "--dest", "org.freedesktop.portal.Desktop",
        "--object-path", "/org/freedesktop/portal/desktop",
        "--method", "org.freedesktop.portal.Screenshot.Screenshot",
        "", f"{{'handle_token': <'{token}'>, 'interactive': <false>}}",
    ]
    try:
        subprocess.run(cmd, timeout=15, capture_output=True, check=False)
    except Exception as e:
        logger.debug(f"[SCREENSHOT] portal call failed: {e}")
        return None
    # Wait for a new (or freshly-rewritten) PNG that has finished writing.
    shot = None
    deadline = start + 10
    while time.time() < deadline:
        cands = [p for p in pics.glob("*.png")
                 if p not in before or p.stat().st_mtime >= start]
        if cands:
            newest = max(cands, key=lambda p: p.stat().st_mtime)
            if _png_complete(newest):
                shot = newest
                break
        time.sleep(0.1)
    if not shot:
        return None
    try:
        return shot.read_bytes()
    except Exception as e:
        logger.debug(f"[SCREENSHOT] portal file read failed: {e}")
        return None
    finally:
        try:
            shot.unlink()  # don't leave the user's screen sitting in Pictures
        except OSError:
            pass


def _capture_wayland():
    """Linux Wayland: try silent CLI backends, first valid PNG wins. Returns
    PNG bytes or None (caller falls back to the paste hint)."""
    cmds = []
    if shutil.which("grim"):
        cmds.append(["grim", "{path}"])                                  # wlroots (Sway/Hyprland)
    if shutil.which("gnome-screenshot"):
        cmds.append(["gnome-screenshot", "-f", "{path}"])                # GNOME
    if shutil.which("spectacle"):
        cmds.append(["spectacle", "-b", "-n", "-f", "-o", "{path}"])     # KDE
    for cmd in cmds:
        data = _run_to_tempfile(cmd)
        if data:
            logger.info(f"[SCREENSHOT] Wayland capture via {cmd[0]}")
            return data
    # Last resort (GNOME Wayland with no CLI tool installed): the desktop portal.
    data = _capture_portal()
    if data:
        logger.info("[SCREENSHOT] Wayland capture via xdg-desktop-portal")
        return data
    return None


def _run_to_tempfile(cmd_template):
    """Run a capture command that writes a PNG to a temp path ({path} placeholder).
    Returns the PNG bytes if it produced a valid PNG, else None. Always cleans up."""
    fd, path = tempfile.mkstemp(suffix=".png", prefix="sapphire_shot_")
    os.close(fd)
    try:
        cmd = [path if part == "{path}" else part for part in cmd_template]
        subprocess.run(cmd, timeout=15, capture_output=True, check=False)
        if os.path.getsize(path) > 0:
            with open(path, "rb") as f:
                data = f.read()
            if data[:8] == _PNG_MAGIC:
                return data
    except Exception as e:
        logger.debug(f"[SCREENSHOT] {cmd_template[0]} failed: {e}")
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    return None


def _capture_local():
    """Returns (b64_png_or_None, reason). reason: None=ok, 'MSS_MISSING', 'FALLBACK'."""
    system = platform.system()
    try:
        if system == "Windows":
            raw = _capture_mss()
        elif system == "Darwin":
            raw = _capture_macos()
        elif system == "Linux":
            if os.environ.get("WAYLAND_DISPLAY"):
                raw = _capture_wayland()    # mss would grab black under Wayland
            elif os.environ.get("DISPLAY"):
                raw = _capture_mss()        # X11
            else:
                # The systemd --user service is often started at login with no
                # WAYLAND_DISPLAY / DISPLAY in its env. The desktop portal is pure
                # D-Bus (needs only the session bus), so it still captures here.
                raw = _capture_portal()
        else:
            raw = None
    except ImportError:
        return None, "MSS_MISSING"
    except Exception as e:
        logger.error(f"[SCREENSHOT] local capture failed: {e}", exc_info=True)
        return None, "FALLBACK"
    if not raw:
        return None, "FALLBACK"
    return _encode_png(raw), None


# ── browser capture (source="user") ──────────────────────────────────────────

_SCREENSHOT_STATE_KEY = "_sapphire_screenshot_state"
_BROWSER_TIMEOUT = 30  # seconds to wait for the user to share their screen


def _get_state():
    """Shared pending-capture slot, also reached by routes/capture.py. Both files
    are exec()'d in isolated namespaces, so we hang the state off sys.modules."""
    if _SCREENSHOT_STATE_KEY not in sys.modules:
        mod = types.ModuleType(_SCREENSHOT_STATE_KEY)
        mod.lock = threading.Lock()
        mod.pending = {"event": None, "nonce": None, "image": None}
        sys.modules[_SCREENSHOT_STATE_KEY] = mod
    return sys.modules[_SCREENSHOT_STATE_KEY]


def _browser_capture():
    """Ask the browser to share its screen (getDisplayMedia) and wait for the
    frame. Returns an execute()-style (result, ok) tuple on success/error, or
    None on timeout (no browser responded → caller falls back to the paste hint)."""
    state = _get_state()
    nonce = secrets.token_urlsafe(16)
    event = threading.Event()
    with state.lock:
        state.pending = {"event": event, "nonce": nonce, "image": None}

    logger.info(f"[SCREENSHOT] Waiting for browser screen share (timeout={_BROWSER_TIMEOUT}s)")
    event.wait(timeout=_BROWSER_TIMEOUT)

    with state.lock:
        image = state.pending.get("image")
        state.pending = {"event": None, "nonce": None, "image": None}

    if not image:
        return None  # nothing came back — let the caller fall back to paste guidance
    if "error" in image:
        return image["error"], False
    return {"text": "The user shared their screen.", "images": [image]}, True


# ── executor ────────────────────────────────────────────────────────────────

def execute(function_name, arguments, config):
    if function_name != "get_screenshot":
        return f"Unknown function: {function_name}", False

    source = (arguments.get("source") or "local").lower()

    if source == "user":
        result = _browser_capture()
        if result is not None:
            return result
        # No browser responded (web UI not open, voice-only client) — fall back.
        return PASTE_HINT, True

    try:
        b64, reason = _capture_local()
    except Exception as e:
        logger.error(f"[SCREENSHOT] {e}", exc_info=True)
        return PASTE_HINT, True

    if b64:
        return _image_result(b64, f"Screenshot captured ({platform.system()}).")
    if reason == "MSS_MISSING":
        return (
            "Screen capture needs the 'mss' package on this system, which isn't installed. "
            "Install it (pip install mss) and try again, or ask the user to paste a "
            "screenshot into the chat instead."
        ), True
    return PASTE_HINT, True
