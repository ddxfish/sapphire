# Sapphire Link — Blender addon (single file, stdlib only).
# Runs a localhost JSON socket server inside Blender so Sapphire can inspect
# the scene, run bpy Python, and capture the viewport. One request per
# connection, newline-delimited JSON both ways:
#   request:  {"type": "ping|scene|object|code|screenshot", "params": {...}}\n
#   response: {"ok": true, "result": ...} | {"ok": false, "error": "..."}\n
# All bpy work runs on Blender's main thread via bpy.app.timers (thread-safe
# registration is the documented pattern). The socket thread only queues.
# Security: binds 127.0.0.1 only, no auth — same trust domain as the desktop
# it runs on. Never port-forward this.

bl_info = {
    "name": "Sapphire Link",
    "author": "sapphire",
    "version": (1, 0, 0),
    "blender": (3, 0, 0),
    "location": "View3D > Sidebar (N) > Sapphire",
    "description": "Local JSON socket so Sapphire (local AI) can see and drive Blender",
    "category": "System",
}

import base64
import contextlib
import io
import json
import math
import os
import socket
import tempfile
import threading
import traceback

import bpy

DEFAULT_PORT = 9877
MAX_REQUEST = 2 * 1024 * 1024
MAX_OUTPUT = 10000
EXEC_TIMEOUT = 600  # seconds a single request may hold the main thread


# -- main-thread execution ----------------------------------------------------

def _run_on_main(fn, timeout=EXEC_TIMEOUT):
    """Run fn() on Blender's main thread, wait for the result."""
    box = {}
    done = threading.Event()

    def wrapper():
        try:
            box["result"] = fn()
        except Exception as e:
            box["error"] = str(e) or f"{type(e).__name__}"
        done.set()
        return None  # one-shot timer

    bpy.app.timers.register(wrapper, first_interval=0.0)
    if not done.wait(timeout):
        return {"ok": False, "error": f"timed out after {timeout}s inside Blender"}
    if "error" in box:
        return {"ok": False, "error": box["error"]}
    return {"ok": True, "result": box["result"]}


# -- handlers (run on main thread) --------------------------------------------

def _vec(v, nd=3):
    return [round(c, nd) for c in v]


def _h_ping(params):
    sc = bpy.context.scene
    return {"pong": True, "blender": bpy.app.version_string,
            "scene": sc.name, "objects": len(sc.objects)}


def _h_scene(params):
    sc = bpy.context.scene
    objs = [{"name": o.name, "type": o.type, "location": _vec(o.location)}
            for o in sc.objects[:60]]
    return {
        "scene": sc.name,
        "engine": sc.render.engine,
        "frame": sc.frame_current,
        "object_count": len(sc.objects),
        "objects": objs,
        "truncated": len(sc.objects) > 60,
        "active": bpy.context.view_layer.objects.active.name
                  if bpy.context.view_layer.objects.active else None,
    }


def _h_object(params):
    name = params.get("name") or ""
    o = bpy.data.objects.get(name)
    if not o:
        raise ValueError(f"no object named '{name}'")
    info = {
        "name": o.name, "type": o.type,
        "location": _vec(o.location),
        "rotation_euler": _vec(o.rotation_euler),
        "scale": _vec(o.scale),
        "dimensions": _vec(o.dimensions),
        "visible": o.visible_get(),
        "parent": o.parent.name if o.parent else None,
        "modifiers": [m.name for m in o.modifiers],
        "materials": [s.material.name for s in o.material_slots if s.material],
    }
    if o.type == 'MESH':
        info["vertices"] = len(o.data.vertices)
        info["polygons"] = len(o.data.polygons)
    return info


def _h_code(params):
    code = params.get("code") or ""
    if not code.strip():
        raise ValueError("no code provided")
    ns = {"bpy": bpy, "math": math, "__name__": "__main__"}
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            exec(compile(code, "<sapphire>", "exec"), ns)
    except Exception:
        # hand back the traceback of *their* code only, not addon internals
        tb = traceback.format_exc()
        idx = tb.find('File "<sapphire>"')
        err = ("Traceback (most recent call last):\n  " + tb[idx:]) if idx != -1 \
              else tb.strip().splitlines()[-1]
        printed = buf.getvalue().strip()
        if printed:
            err = f"(printed before the error)\n{printed}\n\n{err}"
        raise RuntimeError(f"code failed:\n{err}") from None
    out = buf.getvalue().strip()
    if len(out) > MAX_OUTPUT:
        out = out[:MAX_OUTPUT] + f"\n...(truncated, {len(out)} chars total)"
    return {"output": out or "(ok - no output)"}


def _downscale_and_b64(path, max_dim):
    """Cap the PNG at path to max_dim on the long edge, return base64, remove it."""
    img = bpy.data.images.load(path)
    try:
        w, h = img.size
        scale = min(1.0, max_dim / max(w, h))
        if scale < 1.0:
            img.scale(max(1, int(w * scale)), max(1, int(h * scale)))
            img.filepath_raw = path
            img.file_format = 'PNG'
            img.save()
    finally:
        bpy.data.images.remove(img)
    with open(path, "rb") as f:
        data = base64.b64encode(f.read()).decode()
    os.remove(path)
    return data


def _h_screenshot(params):
    max_dim = int(params.get("max_dim") or 1024)
    path = os.path.join(tempfile.gettempdir(), "sapphire_link_shot.png")
    shading = None
    shot = False
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                region = next((r for r in area.regions if r.type == 'WINDOW'), None)
                if not region:
                    continue
                shading = area.spaces.active.shading.type
                with bpy.context.temp_override(window=window, area=area, region=region):
                    bpy.ops.screen.screenshot_area(filepath=path)
                shot = True
                break
        if shot:
            break
    if not shot:  # no 3D viewport — fall back to the whole window
        bpy.ops.screen.screenshot(filepath=path)
    return {"png_b64": _downscale_and_b64(path, max_dim), "shading": shading}


def _h_render(params):
    max_dim = int(params.get("max_dim") or 1024)
    sc = bpy.context.scene
    if not sc.camera:
        raise ValueError("no camera in the scene — add one, or use the viewport snapshot")
    path = os.path.join(tempfile.gettempdir(), "sapphire_link_render.png")
    prev_path = sc.render.filepath
    prev_fmt = sc.render.image_settings.file_format
    sc.render.filepath = path
    sc.render.image_settings.file_format = 'PNG'
    try:
        bpy.ops.render.render(write_still=True)
    finally:
        sc.render.filepath = prev_path
        sc.render.image_settings.file_format = prev_fmt
    return {"png_b64": _downscale_and_b64(path, max_dim), "engine": sc.render.engine}


HANDLERS = {"ping": _h_ping, "scene": _h_scene, "object": _h_object,
            "code": _h_code, "screenshot": _h_screenshot, "render": _h_render}


# -- socket server ------------------------------------------------------------

class LinkServer:
    def __init__(self):
        self.thread = None
        self.stop_flag = threading.Event()
        self.port = DEFAULT_PORT

    @property
    def running(self):
        return self.thread is not None and self.thread.is_alive()

    def start(self, port):
        if self.running:
            return
        self.port = port
        self.stop_flag.clear()
        self.thread = threading.Thread(target=self._serve, daemon=True,
                                       name="sapphire-link")
        self.thread.start()

    def stop(self):
        self.stop_flag.set()
        self.thread = None

    def _serve(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            srv.bind(("127.0.0.1", self.port))
        except OSError as e:
            print(f"[sapphire_link] bind failed on {self.port}: {e}")
            return
        srv.listen(2)
        srv.settimeout(1.0)
        print(f"[sapphire_link] listening on 127.0.0.1:{self.port}")
        try:
            while not self.stop_flag.is_set():
                try:
                    conn, _ = srv.accept()
                except socket.timeout:
                    continue
                try:
                    self._handle(conn)
                except Exception as e:
                    print(f"[sapphire_link] request error: {e}")
                finally:
                    conn.close()
        finally:
            srv.close()
            print("[sapphire_link] stopped")

    def _handle(self, conn):
        conn.settimeout(30)
        buf = b""
        while b"\n" not in buf:
            chunk = conn.recv(65536)
            if not chunk:
                return
            buf += chunk
            if len(buf) > MAX_REQUEST:
                conn.sendall(b'{"ok": false, "error": "request too large"}\n')
                return
        try:
            req = json.loads(buf.split(b"\n", 1)[0])
            handler = HANDLERS.get(req.get("type") or "")
            if not handler:
                resp = {"ok": False,
                        "error": f"unknown type; valid: {sorted(HANDLERS)}"}
            else:
                params = req.get("params") or {}
                resp = _run_on_main(lambda: handler(params))
        except Exception as e:
            resp = {"ok": False, "error": str(e)}
        conn.sendall(json.dumps(resp).encode() + b"\n")


server = LinkServer()


# -- UI -----------------------------------------------------------------------

def _prefs():
    return bpy.context.preferences.addons[__name__].preferences


class SapphireLinkPrefs(bpy.types.AddonPreferences):
    bl_idname = __name__
    port: bpy.props.IntProperty(name="Port", default=DEFAULT_PORT,
                                min=1024, max=65535)
    auto_start: bpy.props.BoolProperty(name="Start server when Blender opens",
                                       default=True)

    def draw(self, context):
        row = self.layout.row()
        row.prop(self, "port")
        row.prop(self, "auto_start")


class SAPPHIRE_OT_start(bpy.types.Operator):
    bl_idname = "sapphire.link_start"
    bl_label = "Start"

    def execute(self, context):
        server.start(_prefs().port)
        return {'FINISHED'}


class SAPPHIRE_OT_stop(bpy.types.Operator):
    bl_idname = "sapphire.link_stop"
    bl_label = "Stop"

    def execute(self, context):
        server.stop()
        return {'FINISHED'}


class SAPPHIRE_PT_panel(bpy.types.Panel):
    bl_label = "Sapphire Link"
    bl_idname = "SAPPHIRE_PT_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Sapphire"

    def draw(self, context):
        col = self.layout.column()
        if server.running:
            col.label(text=f"Listening on 127.0.0.1:{server.port}", icon='CHECKMARK')
            col.operator("sapphire.link_stop")
        else:
            col.label(text="Server stopped", icon='X')
            col.operator("sapphire.link_start")


CLASSES = (SapphireLinkPrefs, SAPPHIRE_OT_start, SAPPHIRE_OT_stop, SAPPHIRE_PT_panel)


def _auto_start():
    try:
        if _prefs().auto_start:
            server.start(_prefs().port)
    except Exception as e:
        print(f"[sapphire_link] auto-start failed: {e}")
    return None


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    if not bpy.app.background:
        # context is restricted during register — defer the prefs read
        bpy.app.timers.register(_auto_start, first_interval=0.5)


def unregister():
    server.stop()
    for c in CLASSES:
        bpy.utils.unregister_class(c)
