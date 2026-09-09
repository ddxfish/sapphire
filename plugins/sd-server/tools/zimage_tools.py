"""Z-Image Turbo image generation via sd-server (stable-diffusion.cpp).

One tool, `generate_image`. The model:
  - generates `count` images at once (each with its own seed),
  - SEES them (a single labeled grid when count>1, or the image when count==1) —
    this rides the core `{"text","images"}` tool-return path (chat_tool_calling
    ._extract_tool_images), so a vision-capable model views them and the user
    sees them rendered in chat,
  - gets a numbered RECIPE in the text so any image can be recreated exactly by
    calling generate_image again with that image's seed + params.

Seeds are assigned client-side, so the recipe is always complete regardless of
what the server reports back.
"""

import base64
import logging
import random
import re

logger = logging.getLogger(__name__)

ENABLED = True
EMOJI = "\U0001F5BC"  # 🖼️

_DEFAULTS = {
    "api_url": "http://127.0.0.1:7861",
    "max_count": 6,
    "default_steps": 8,
    "default_cfg": 1.0,
    "default_sampler": "",
    "default_scheduler": "",
    "default_negative": "",
    "default_width": 1024,
    "default_height": 1024,
    "timeout": 180,
    "static_keywords": "",
    "ai_name": "",
    "ai_description": "",
    "user_name": "",
    "user_description": "",
}


def _name_pairs(cfg):
    """[(marker_name, replacement_description), ...] from settings, in order."""
    return [
        ((cfg.get("ai_name") or "").strip(), (cfg.get("ai_description") or "").strip()),
        ((cfg.get("user_name") or "").strip(), (cfg.get("user_description") or "").strip()),
    ]


def _expand_prompt(prompt, cfg):
    """Replace the configured AI/user NAMES (whole-word, case-insensitive) with
    their physical descriptions, then append static keywords. The model writes
    just the name (e.g. 'Sapphire in the front yard'); the appearance is filled
    in here, so the model never has to spell out a description."""
    out = prompt
    for marker, desc in _name_pairs(cfg):
        if marker and desc:
            out = re.sub(rf"\b{re.escape(marker)}\b", desc, out, count=1, flags=re.IGNORECASE)
    kw = (cfg.get("static_keywords") or "").strip()
    if kw:
        out = f"{out.rstrip('. ')}. {kw}".strip()
    return out


_ASPECT_DIMS = {
    "square": (1024, 1024),
    "portrait": (832, 1216),
    "landscape": (1216, 832),
}


def pick_aspect_dims(aspects):
    """Pick a random aspect from the allowed list -> (name, (w, h)). Empty -> square."""
    choices = [a for a in (aspects or []) if a in _ASPECT_DIMS] or ["square"]
    name = random.choice(choices)
    return name, _ASPECT_DIMS[name]


def assemble_slideshow_prompt(slots, cfg, expand=True):
    """Wildcards assembly: slots = [{"name":.., "options":[str]}, ...]. Pick one
    random non-empty line per slot, join with commas, then run the name-swap +
    static-keyword expansion (unless expand=False). Returns the prompt string."""
    parts = []
    for slot in (slots or []):
        opts = [o.strip() for o in (slot.get("options") or [])
                if isinstance(o, str) and o.strip()]
        if opts:
            parts.append(random.choice(opts))
    raw = ", ".join(parts)
    if not raw:
        return ""
    return _expand_prompt(raw, cfg) if expand else raw


def _settings(plugin_settings=None):
    s = dict(_DEFAULTS)
    if plugin_settings:
        s.update({k: v for k, v in plugin_settings.items() if v is not None})
    else:
        try:
            from core.plugin_loader import plugin_loader
            stored = plugin_loader.get_plugin_settings("sd-server") or {}
            s.update({k: v for k, v in stored.items() if v is not None})
        except Exception:
            pass
    return s


def _apply_sampler(payload, sampler, scheduler):
    """Inject sampler_name/scheduler into a txt2img payload — but only when set.
    Blank = omit the key so sd-server uses its own default. This is what makes
    the plugin model-agnostic: Z-Image Turbo runs fine with both blank; for
    SDXL/Pony the user sets e.g. sampler='dpm++ 2m', scheduler='karras'."""
    sampler = (sampler or "").strip()
    scheduler = (scheduler or "").strip()
    if sampler:
        payload["sampler_name"] = sampler
    if scheduler:
        payload["scheduler"] = scheduler
    return payload


def _build_description(cfg):
    """Tool description, built from settings. When AI/user names are configured,
    it tells the model to write those names (the plugin fills in the appearance),
    so the model never spells out a physical description itself."""
    base = ("Generate an image via Z-Image Turbo and optionally view it yourself. "
            "Describe the scene or action in ~20 words. Add the count param for multiple images.")
    ai_name = (cfg.get("ai_name") or "").strip()
    user_name = (cfg.get("user_name") or "").strip()
    parts = []
    if ai_name:
        parts.append(f"'{ai_name}' for yourself")
    if user_name:
        parts.append(f"'{user_name}' for the user")
    if parts:
        base += (" Write " + " and ".join(parts) +
                 " - just the name plus the scene or action, never a physical description; "
                 "the appearance is filled in automatically.")
    return base


def _tool_schema(description):
    return [
        {
            "type": "function",
            # Loop guard (core feature): warn after 2 calls in one turn. Top-level
            # flag, read into function_manager and stripped from the wire. ASCII only.
            "loop_warn_after": 2,
            "loop_warn_message": (
                "You have now generated images {count} times this turn, and each "
                "generation has a real cost. If the result is not what you wanted, the "
                "prompt may be asking for something physically impossible (for example a "
                "face and the back of a head at the same time) - regenerating will not "
                "fix that. Stop and ask the user rather than trying again."
            ),
            # Local hardware by design (your own SD box) — usable in
            # private chats. Same declaration image-gen carries.
            "is_local": True,
            "function": {
                "name": "generate_image",
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prompt": {"type": "string", "description": "The scene or action to depict (~20 words), using the configured names."},
                        "view": {
                            "type": "boolean",
                            "description": "Whether you see the image yourself (default true). true: you see the full image - richer, but on some models it can pull you into regenerating it repeatedly. false: you get only the text confirmation, no description - cheapest, nothing to second-guess. The user always sees the full image either way."
                        },
                        "count": {"type": "integer", "description": "How many images to make. Leave unset (default 1) in almost all cases - only raise it if the user explicitly asks for several."},
                        "seed": {"type": "integer", "description": "Optional. Pass a seed from a prior result to reproduce that exact image; otherwise leave unset for a fresh one."}
                    },
                    "required": ["prompt"]
                }
            }
        }
    ]


def get_tools():
    """Settings-aware schema builder. Core calls this at registration AND when
    settings are saved (function_manager.refresh_plugin_tools), so the tool
    description reflects the configured AI/user names live, with no reload."""
    return _tool_schema(_build_description(_settings()))


# Static fallback (core prefers get_tools() when present).
TOOLS = _tool_schema(_build_description(_DEFAULTS))


def execute(function_name, arguments, config=None, plugin_settings=None, credentials=None):
    if function_name == "generate_image":
        return _exec_generate(arguments, plugin_settings)
    return f"Unknown function: {function_name}", False


def _call_sdserver(api_url, payload, timeout):
    """POST to sd-server's A1111-compatible txt2img. Returns image bytes or raises."""
    import requests
    from core import net
    url = api_url.rstrip("/") + "/sdapi/v1/txt2img"
    resp = net.post(url, json=payload, timeout=timeout)
    if resp.status_code != 200:
        raise RuntimeError(f"sd-server {resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    # A1111 shape: {"images": ["<base64>", ...], "info": "..."}.
    # Defensive: tolerate a couple of common variants if the server differs.
    imgs = None
    if isinstance(data, dict):
        imgs = data.get("images") or data.get("data")
    if not imgs:
        raise RuntimeError(f"sd-server returned no images. Raw: {str(data)[:200]}")
    b64 = imgs[0]
    if isinstance(b64, dict):              # e.g. [{"b64_json": "..."}]
        b64 = b64.get("b64_json") or b64.get("data")
    if isinstance(b64, str) and b64.startswith("data:"):
        b64 = b64.split(",", 1)[1]
    return base64.b64decode(b64)


def _exec_generate(arguments, plugin_settings=None):
    cfg = _settings(plugin_settings)

    prompt = (arguments.get("prompt") or "").strip()
    if not prompt:
        return "No prompt provided.", False

    try:
        max_count = int(cfg.get("max_count", 6))
    except (TypeError, ValueError):
        max_count = 6
    count = arguments.get("count", 1) or 1
    try:
        count = max(1, min(int(count), max_count))
    except (TypeError, ValueError):
        count = 1

    # Gen params come from SETTINGS only. The AI tool deliberately can't set
    # steps/cfg/size/sampler — they're not in the schema, and sourcing them here
    # (not from arguments) also ignores any a model hallucinates. The AI controls
    # prompt / count / seed / view; everything else is the user's UI settings.
    width = int(cfg.get("default_width") or 1024)
    height = int(cfg.get("default_height") or 1024)
    steps = int(cfg.get("default_steps", 8))
    cfg_scale = float(cfg.get("default_cfg", 1.0))
    negative = cfg.get("default_negative", "")
    api_url = cfg.get("api_url", _DEFAULTS["api_url"])
    timeout = int(cfg.get("timeout", 180))

    # Client-assigned seeds → recipe is always complete + reproducible.
    base_seed = arguments.get("seed")
    if base_seed is not None:
        seeds = [int(base_seed) + i for i in range(count)]
    else:
        seeds = [random.randint(1, 2**31 - 1) for _ in range(count)]

    final_prompt = _expand_prompt(prompt, cfg)  # name swap + static keywords
    logger.info(f"[ZIMAGE] generating {count} @ {width}x{height} steps={steps} cfg={cfg_scale} seeds={seeds}")

    raw_images = []
    for seed in seeds:
        payload = {
            "prompt": final_prompt,
            "negative_prompt": negative,
            "steps": steps,
            "cfg_scale": cfg_scale,
            "width": width,
            "height": height,
            "seed": seed,
            "batch_size": 1,
        }
        _apply_sampler(payload, cfg.get("default_sampler"), cfg.get("default_scheduler"))
        try:
            raw_images.append(_call_sdserver(api_url, payload, timeout))
        except Exception as e:
            logger.error(f"[ZIMAGE] generation failed (seed={seed}): {e}")
            if raw_images:
                break  # keep what we have; report the partial set
            # Connection/timeout errors are noisy — boil down to a short reason.
            # HTTP errors from _call_sdserver already carry the status code.
            ename = type(e).__name__
            if "Connection" in ename or "Timeout" in ename:
                reason = f"could not reach the image server at {api_url} (it may be down)"
            else:
                reason = str(e)
            # Explicit no-retry instruction so the model doesn't loop on a dead server.
            return (f"Image generation FAILED - {reason}. Do NOT call generate_image again "
                    f"right now; retrying immediately will not help until the server is back. "
                    f"Tell the user it failed (mention the error) and to check the sd-server.", False)

    # ---- result text (travels WITH the images in the tool result) ----
    # Reference-only, NOT a call to action: phrased so the model treats the
    # image as DONE (already shown to the user) and doesn't loop into more
    # generate_image calls. Seeds are reference data, not an instruction.
    n = len(raw_images)
    if n == 1:
        recipe = (f"Done - image generated and already shown to the user. You normally do not "
                  f"need to generate again unless the user asks for a change. "
                  f"(Seed {seeds[0]}, reference only - reuse it only to recreate this exact image.)")
    else:
        seed_list = ", ".join(f"#{i} seed={s}" for i, s in enumerate(seeds[:n], start=1))
        recipe = (f"Done - {n} images generated and already shown to the user. You normally do not "
                  f"need to generate more unless the user asks for a change. "
                  f"Seeds (reference only, reuse one only to recreate that exact image): {seed_list}.")

    # ---- build images for the return ----
    # The user ALWAYS sees the images. The model also sees them (vision tokens)
    # by default (view defaults true, matching the tool description); pass
    # view=false to skip the model's own look for a cheaper, hands-off call.
    view = bool(arguments.get("view", True))
    from core import images as ci     # the one resize + the one grid (2026-09-09)

    # view=false → display_only: the user still sees the full image, the model
    # gets ONLY the recipe text. No CLIP description — its subject-blind guesses
    # ("multiple people at a computer" for a solid square) read as "wrong image"
    # to literal models and DROVE the regeneration loop view=false was meant to
    # prevent. CLIP remains core's automatic fallback for view=true on a
    # non-vision model (an image the model was meant to see). 2026-08-09.
    if len(raw_images) == 1:
        shaped = [ci.for_chat(raw_images[0], quality=90)]
    else:
        # count>1: ONE clean image — the labeled grid (contact sheet). Individuals
        # aren't rendered to avoid the grid+duplicates clutter; the recipe above
        # gives each image's seed, so any single is a recreate-by-seed away.
        shaped = [ci.contact_sheet(raw_images, cell=512)]

    return ci.result(recipe, shaped, display_only=(not view)), True
