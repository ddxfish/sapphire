# Blender

Gives the AI hands and eyes in a running Blender on this machine. Three tools:

| Tool | What it does |
|------|-------------|
| `blender_scene` | Scene summary (objects, active, frame, engine) — or full detail on one object |
| `blender_code` | Run Python (`bpy`) inside Blender — create, modify, animate, render |
| `blender_see` | Image the AI can see — viewport snapshot (default, reports shading mode) or `mode='render'` for a true render through the scene camera |

The loop that makes it work: change something with `blender_code`, then look at it
with `blender_see`, then iterate. No third-party code — both halves live in this
plugin.

## How it's wired

```
Sapphire plugin tools  ──JSON over localhost:9877──▶  Sapphire Link addon (inside Blender)
```

The addon (`blender_addon/sapphire_link.py`, single file, stdlib only) runs a tiny
socket server inside Blender. All Blender-API work executes on Blender's main
thread via `bpy.app.timers` — the server thread only queues, so Blender stays
responsive. Idle cost is one blocked thread; nothing polls.

## Install — Blender side (one time)

1. Open Blender (3.0+)
2. `Edit > Preferences > Add-ons`, top-right arrow menu → **Install from Disk...**
3. Pick `plugins/blender/blender_addon/sapphire_link.py` from this Sapphire install
4. Tick the checkbox to enable **Sapphire Link**

The server starts automatically whenever Blender opens (toggle that, and the
port, in the addon's preferences). A **Sapphire** tab in the 3D viewport sidebar
(`N` key) shows status and Start/Stop buttons.

## Install — Sapphire side

1. Enable the **Blender** plugin in Settings
2. Go to **Toolsets** and add `blender_scene`, `blender_code`, `blender_see` to
   the toolset your chat uses
3. With Blender open, ask the AI to look at the scene

## Settings

- **Port** (9877) — must match the addon preferences
- **Call timeout** (120s) — ceiling for long `blender_code` runs (renders, heavy ops)
- **Snapshot max dimension** (1024px) — long-edge cap on `blender_see` images

## Notes & limits

- Blender must be running with a window for `blender_see` (no viewport in
  `--background` mode).
- `blender_code` runs with a fresh namespace each call (`bpy` and `math`
  preloaded) — each call must stand alone. Anything Blender can do, it can do:
  modeling, materials, lights, animation, importing assets, rendering.
- `blender_see mode='render'` needs a scene camera and takes as long as the
  render engine takes — Eevee is quick, Cycles depends on the scene.
- If Blender isn't open, tools return a friendly "not reachable" message — no
  errors, no retries, no load on the system.

## Security

The addon binds **127.0.0.1 only** and has no authentication — by design it
executes arbitrary Python in Blender, so treat the port as part of your desktop's
trust domain. Never forward it off the machine.
