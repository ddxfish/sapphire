# plugins/blender/tools/blender_tools.py — Sapphire's hands in Blender.
# Talks newline-delimited JSON to the bundled Sapphire Link addon
# (blender_addon/sapphire_link.py) running inside Blender on localhost.
import json
import logging
import socket

logger = logging.getLogger(__name__)

ENABLED = True
EMOJI = '\U0001F9CA'  # ice cube
AVAILABLE_FUNCTIONS = ['blender_scene', 'blender_code', 'blender_see']

_NOT_RUNNING = ("Blender isn't reachable — it's not running, or the Sapphire Link "
                "addon's server is stopped. Ask your human to open Blender "
                "(the addon starts its server automatically).")

TOOLS = [
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "blender_scene",
            "description": (
                "Scene summary of the running Blender (objects, active, frame, engine). "
                "Pass object_name for one object's full detail. Orient before changing things."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "object_name": {"type": "string", "description": "Exact object name for detail instead of the summary."}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "blender_code",
            "description": (
                "Run Python inside the running Blender — your 3D hands; anything Blender can "
                "do. bpy and math preloaded, print() comes back, namespace fresh each call. "
                "Check your work with blender_see."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "Python to run in Blender. print() what you want back."}
                },
                "required": ["code"]
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "blender_see",
            "description": (
                "Look at Blender — returns an image you can see. mode='viewport' (default) "
                "snapshots the 3D viewport; mode='render' truly renders through the scene "
                "camera (slower). Check your work after blender_code changes."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "mode": {"type": "string", "enum": ["viewport", "render"], "description": "viewport (default) or render."}
                },
                "required": []
            }
        }
    },
]


def _settings():
    try:
        from core.plugin_loader import plugin_loader
        return plugin_loader.get_plugin_settings("blender") or {}
    except Exception:
        return {}


def _send(msg_type, params=None, timeout=None):
    """One request to the addon: connect, send, read the newline-terminated reply."""
    s = _settings()
    host = s.get("host") or "127.0.0.1"
    port = int(s.get("port") or 9877)
    timeout = timeout or float(s.get("timeout") or 120)
    payload = json.dumps({"type": msg_type, "params": params or {}}).encode() + b"\n"
    with socket.create_connection((host, port), timeout=5) as sock:
        sock.settimeout(timeout)
        sock.sendall(payload)
        buf = b""
        while b"\n" not in buf:
            chunk = sock.recv(65536)
            if not chunk:
                break
            buf += chunk
    return json.loads(buf.split(b"\n", 1)[0])


def _call(msg_type, params=None, timeout=None):
    """_send with the failure modes turned into friendly tool results."""
    try:
        resp = _send(msg_type, params, timeout)
    except (ConnectionRefusedError, socket.timeout, TimeoutError, OSError) as e:
        if isinstance(e, ConnectionRefusedError):
            return _NOT_RUNNING, False
        return f"blender: connection failed: {e}", False
    except json.JSONDecodeError:
        return "blender: got a malformed reply from the addon", False
    if not resp.get("ok"):
        return f"blender error: {resp.get('error', 'unknown')}", False
    return resp.get("result"), True


def _blender_scene(object_name=None):
    if object_name:
        result, ok = _call("object", {"name": object_name})
    else:
        result, ok = _call("scene")
    if not ok:
        return result, False
    return json.dumps(result, indent=1), True


def _blender_code(code):
    if not (code or "").strip():
        return "blender_code: provide Python code to run", False
    result, ok = _call("code", {"code": code})
    if not ok:
        return result, False
    return result.get("output", "(ok - no output)"), True


def _blender_see(mode="viewport"):
    s = _settings()
    max_dim = {"max_dim": int(s.get("max_dimension") or 1024)}
    if mode == "render":
        result, ok = _call("render", max_dim)
        if ok:
            text = (f"Render through the scene camera ({result.get('engine')}). "
                    "Colors muted? Default AgX color management does that — view "
                    "transform 'Standard' keeps them vivid.")
    else:
        result, ok = _call("screenshot", max_dim)
        if ok:
            shading = result.get("shading") or "?"
            text = f"Snapshot of Blender's 3D viewport ({shading} shading)."
            if shading in ("SOLID", "WIREFRAME"):
                text += (" Materials/emission don't fully show in this shading mode — "
                         "switch the viewport to MATERIAL/RENDERED via blender_code, or "
                         "use mode='render'.")
    if not ok:
        return result, False
    b64 = result.get("png_b64")
    if not b64:
        return "blender_see: addon returned no image data", False
    return {"text": text, "images": [{"data": b64, "media_type": "image/png"}]}, True


def execute(function_name, arguments, config):
    try:
        if function_name == "blender_scene":
            return _blender_scene((arguments.get("object_name") or "").strip() or None)
        if function_name == "blender_code":
            return _blender_code(arguments.get("code", ""))
        if function_name == "blender_see":
            return _blender_see((arguments.get("mode") or "viewport").strip().lower())
        return f"unknown function: {function_name}", False
    except Exception as e:
        logger.error(f"[blender] {function_name} failed: {e}", exc_info=True)
        return f"blender error: {e}", False
