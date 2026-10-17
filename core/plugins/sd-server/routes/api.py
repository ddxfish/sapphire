"""z-image plugin routes — connection test + a user-facing generate endpoint
(powers the Z-Image Studio app page)."""

import base64
import importlib.util
import logging
import random
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# The route file is exec'd in an isolated namespace (not a package), so we can't
# `import` the sibling tools module normally — load it by path from __file__ and
# reuse its generation helpers so the app and the tool never drift.
_ZT = None


def _tools():
    global _ZT
    if _ZT is None:
        p = Path(__file__).parent.parent / "tools" / "zimage_tools.py"
        spec = importlib.util.spec_from_file_location("zimage_tools_routeload", p)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _ZT = mod
    return _ZT


# POST /api/plugin/z-image/test   body: {"url": "http://host:7861"}
async def test_connection(**kwargs):
    """Reachability check: if sd-server answers ANY HTTP response at the base URL,
    it's connected. Only a connection error / timeout is a real failure. (We don't
    assume a specific API path — builds differ; generation uses /sdapi/v1/txt2img.)"""
    import requests
    from core import net

    request = kwargs.get("request")
    url = ""
    if request is not None:
        try:
            body = await request.json()
            url = (body.get("url") or "").strip()
        except Exception:
            url = ""
    if not url:
        return {"success": False, "error": "No URL provided"}
    if not url.startswith(("http://", "https://")):
        return {"success": False, "error": "URL must start with http:// or https://"}

    base = url.rstrip("/")
    try:
        # Any HTTP response means the host:port is alive and serving sd-server.
        net.get(base + "/", timeout=6)
        return {"success": True}
    except requests.exceptions.ConnectionError:
        return {"success": False, "error": "Could not connect (server down or wrong host/port?)"}
    except requests.exceptions.Timeout:
        return {"success": False, "error": "Timed out reaching sd-server"}
    except Exception as e:
        return {"success": False, "error": str(e)}


# POST /api/plugin/z-image/generate
# body: {prompt, count, steps, cfg_scale, seed, width, height, negative_prompt, expand}
def generate(**kwargs):
    """User-driven one-off generation for the Studio app. Sync handler — the
    dispatcher runs it in a threadpool, so the blocking sd-server call (~4s)
    doesn't stall the event loop. Returns full-res images + the EXACT prompt
    that was sent (after the me/you expansion) so the user can tune in real time."""
    body = kwargs.get("body") or {}
    zt = _tools()
    cfg = zt._settings(kwargs.get("settings"))  # stored settings merged over defaults

    prompt = (body.get("prompt") or "").strip()
    if not prompt:
        return {"success": False, "error": "No prompt provided"}

    def _num(key, default, cast):
        v = body.get(key)
        if v in (None, ""):
            return default
        try:
            return cast(v)
        except (TypeError, ValueError):
            return default

    expand = bool(body.get("expand", True))
    final_prompt = zt._expand_prompt(prompt, cfg) if expand else prompt

    width = _num("width", 1024, int)
    height = _num("height", 1024, int)
    steps = _num("steps", int(cfg.get("default_steps", 8)), int)
    cfg_scale = _num("cfg_scale", float(cfg.get("default_cfg", 1.0)), float)
    negative = body.get("negative_prompt")
    if negative is None:
        negative = cfg.get("default_negative", "")
    max_count = _num("__maxc", int(cfg.get("max_count", 6)), int)
    count = max(1, min(_num("count", 1, int), max_count))
    api_url = cfg.get("api_url", "http://127.0.0.1:7861")
    timeout = int(cfg.get("timeout", 180))

    base_seed = body.get("seed")
    if base_seed not in (None, ""):
        try:
            seeds = [int(base_seed) + i for i in range(count)]
        except (TypeError, ValueError):
            seeds = [random.randint(1, 2**31 - 1) for _ in range(count)]
    else:
        seeds = [random.randint(1, 2**31 - 1) for _ in range(count)]

    images = []
    t0 = time.time()
    try:
        for seed in seeds:
            payload = {
                "prompt": final_prompt, "negative_prompt": negative,
                "steps": steps, "cfg_scale": cfg_scale,
                "width": width, "height": height, "seed": seed, "batch_size": 1,
            }
            # Studio form may override sampler/scheduler in real time; else settings.
            zt._apply_sampler(payload,
                              body.get("sampler_name") or cfg.get("default_sampler"),
                              body.get("scheduler") or cfg.get("default_scheduler"))
            raw = zt._call_sdserver(api_url, payload, timeout)
            images.append("data:image/png;base64," + base64.b64encode(raw).decode())
    except Exception as e:
        return {"success": False, "error": str(e)}

    return {
        "success": True,
        "images": images,
        "seeds": seeds,
        "final_prompt": final_prompt,
        "elapsed": round(time.time() - t0, 1),
        "params": {"width": width, "height": height, "steps": steps,
                   "cfg_scale": cfg_scale, "negative": negative, "expanded": expand},
    }


# POST /api/plugin/sd-server/slideshow/preview   body: {slots, expand}
def slideshow_preview(**kwargs):
    """Assemble a random prompt from the slots and return it as TEXT — no GPU.
    Powers the Studio 'Go/Preview' button so users can test slot combos cheaply."""
    body = kwargs.get("body") or {}
    zt = _tools()
    cfg = zt._settings(kwargs.get("settings"))
    prompt = zt.assemble_slideshow_prompt(body.get("slots"), cfg, expand=bool(body.get("expand", True)))
    if not prompt:
        return {"success": False, "error": "No slot options to assemble a prompt"}
    return {"success": True, "prompt": prompt}


# POST /api/plugin/sd-server/slideshow/next   body: {slots, aspects, expand}
def slideshow_next(**kwargs):
    """Assemble a random prompt, pick a random allowed aspect, generate ONE image.
    The slideshow loop (Studio or the sidebar Reel) calls this on its cooldown timer."""
    body = kwargs.get("body") or {}
    zt = _tools()
    cfg = zt._settings(kwargs.get("settings"))
    prompt = zt.assemble_slideshow_prompt(body.get("slots"), cfg, expand=bool(body.get("expand", True)))
    if not prompt:
        return {"success": False, "error": "No slot options to assemble a prompt"}
    aspect, (w, h) = zt.pick_aspect_dims(body.get("aspects"))

    # Per-slideshow gen overrides; blank/missing inherits the plugin settings.
    def _bnum(key, default, cast):
        v = body.get(key)
        if v in (None, ""):
            return default
        try:
            return cast(v)
        except (TypeError, ValueError):
            return default
    steps = _bnum("steps", int(cfg.get("default_steps", 8)), int)
    cfg_scale = _bnum("cfg_scale", float(cfg.get("default_cfg", 1.0)), float)
    negative = body.get("negative_prompt") or cfg.get("default_negative", "")
    seed = random.randint(1, 2**31 - 1)
    api_url = cfg.get("api_url", "http://127.0.0.1:7861")
    timeout = int(cfg.get("timeout", 180))
    payload = {"prompt": prompt, "negative_prompt": negative, "steps": steps,
               "cfg_scale": cfg_scale, "width": w, "height": h, "seed": seed, "batch_size": 1}
    zt._apply_sampler(payload,
                      body.get("sampler_name") or cfg.get("default_sampler"),
                      body.get("scheduler") or cfg.get("default_scheduler"))
    t0 = time.time()
    try:
        raw = zt._call_sdserver(api_url, payload, timeout)
    except Exception as e:
        return {"success": False, "error": str(e)}
    return {"success": True,
            "image": "data:image/png;base64," + base64.b64encode(raw).decode(),
            "prompt": prompt, "seed": seed, "aspect": aspect,
            "width": w, "height": h, "elapsed": round(time.time() - t0, 1)}
