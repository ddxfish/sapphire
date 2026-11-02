# core/plugins/social/tools/social.py - the one social grammar (tmp/social-media-plan.md)
"""
Thin doors into plugins/social/engine.py.

The profile a chat posts as is its `social` scope (Mind > Social, or the
persona's). Every tool takes an optional `profile` to say otherwise. Images
travel as handles: img:<id> from this chat, doc:N from the library, a path,
or an https URL. A connection set to `draft` queues for the person's
approval in the Outbox; one set to `auto` posts at once.
"""
import io
import logging

logger = logging.getLogger(__name__)

ENABLED = True
EMOJI = '\U0001f4e3'
TOOL_CATEGORY = 'comms'
AVAILABLE_FUNCTIONS = ['social_list', 'social_post', 'social_inbox', 'social_reply',
                       'social_stats', 'social_delete', 'social_meme']

_PROFILE = {"type": "string", "description": "Post as this profile instead of the chat's (from social_list)."}
_CONNS = {"type": "string", "description": "Connection names or platforms, comma-separated (facebook, x, youtube, instagram, linkedin, ...). Blank = every connection of the profile."}

TOOLS = [
    {"type": "function", "is_local": "endpoint",
     "function": {"name": "social_list",
                  "description": "Your social media profiles and their connections: platform, who it posts as, draft or auto, connected or not.",
                  "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "is_local": "endpoint", "writes": True,
     "function": {"name": "social_post",
                  "description": ("Post to social media as this chat's profile. Draft connections queue for the person's "
                                  "approval; auto connections post at once. Keep X under 280 characters. Links: use `link`, "
                                  "not the text. Images: img:<id> handles from this chat (comma-separated for several), "
                                  "doc:N, a path, or an https URL."),
                  "parameters": {"type": "object",
                                 "properties": {"text": {"type": "string", "description": "The post"},
                                                "connections": _CONNS,
                                                "image": {"type": "string", "description": "img:<id>, doc:N, path or https URL; several comma-separated"},
                                                "link": {"type": "string", "description": "A URL to share with the post"},
                                                "profile": _PROFILE},
                                 "required": ["text"]}}},
    {"type": "function", "is_local": "endpoint",
     "function": {"name": "social_inbox",
                  "description": "New comments, mentions and replies on your connections in the last N hours (default 24). Each item has an id for social_reply.",
                  "parameters": {"type": "object",
                                 "properties": {"hours": {"type": "number", "description": "How far back (default 24)"},
                                                "connections": _CONNS, "profile": _PROFILE},
                                 "required": []}}},
    {"type": "function", "is_local": "endpoint", "writes": True,
     "function": {"name": "social_reply",
                  "description": "Reply to one inbox item. Draft connections queue the reply for approval.",
                  "parameters": {"type": "object",
                                 "properties": {"connection": {"type": "string", "description": "One connection name or platform"},
                                                "item_id": {"type": "string", "description": "The [id] from social_inbox"},
                                                "text": {"type": "string"}, "profile": _PROFILE},
                                 "required": ["connection", "item_id", "text"]}}},
    {"type": "function", "is_local": "endpoint",
     "function": {"name": "social_stats",
                  "description": "Followers and other numbers for your connections.",
                  "parameters": {"type": "object", "properties": {"connections": _CONNS, "profile": _PROFILE}, "required": []}}},
    {"type": "function", "is_local": "endpoint", "writes": True,
     "function": {"name": "social_delete",
                  "description": "Delete one of your own posts or comments by its platform id (from the post result or the inbox).",
                  "parameters": {"type": "object",
                                 "properties": {"connection": {"type": "string", "description": "One connection name or platform"},
                                                "post_id": {"type": "string"}, "profile": _PROFILE},
                                 "required": ["connection", "post_id"]}}},
    {"type": "function", "is_local": True,
     "function": {"name": "social_meme",
                  "description": "Caption an image meme-style: bold white text with a black edge, top and bottom. Gives back a new image handle to post.",
                  "parameters": {"type": "object",
                                 "properties": {"image": {"type": "string", "description": "img:<id>, doc:N, path or URL"},
                                                "top": {"type": "string"}, "bottom": {"type": "string"}},
                                 "required": ["image"]}}},
]


def get_tools():
    """The schemas, with the profiles in the posting tool's description."""
    import json
    tools = json.loads(json.dumps(TOOLS))
    try:
        from plugins.social import store
        profiles = store.list_profiles()
    except Exception as e:
        logger.debug(f"[SOCIAL] tool descriptions built without profiles: {e}")
        return tools
    by_name = {t['function']['name']: t['function'] for t in tools}
    if profiles:
        said = ' · '.join(f"{p['name']}: " + (', '.join(f"{c['name']} ({c['provider']}, {c['mode']})" for c in p['connections']) or 'no connections')
                          for p in profiles[:6])
        by_name['social_post']['description'] += f" Profiles now: {said}."
    else:
        by_name['social_post']['description'] += " No profiles yet: the user makes them in Settings > Plugins > Social."
    return tools


def on_load():
    from plugins.social import scan
    scan.pull()


def shutdown():
    from plugins.social import registry
    registry.reset()


_OK_TYPES = ('image/jpeg', 'image/png', 'image/gif')
_MAX_BYTES = 4_500_000


def _images(arg):
    """[(bytes, media_type, https source or None)] for the handles given.
    Platforms take JPEG, PNG and GIF under a few MB: anything else is
    re-encoded as JPEG, the way the chat window does it."""
    from core import images as ci
    out = []
    for h in [h.strip() for h in str(arg or '').replace(';', ',').split(',') if h.strip()]:
        r = ci.resolve(h)
        raw, mt = r.data, r.media_type
        if mt not in _OK_TYPES or len(raw) > _MAX_BYTES:
            raw, mt = ci.for_chat(raw, max_px=2048, quality=90), 'image/jpeg'
        out.append((raw, mt, h if h.startswith('https://') and mt == r.media_type else None))
    return out


def _wrap(draw, text, font, width):
    lines, line = [], ''
    for word in text.split():
        trial = f"{line} {word}".strip()
        if line and draw.textlength(trial, font=font) > width:
            lines.append(line)
            line = word
        else:
            line = trial
    if line:
        lines.append(line)
    return lines


def _meme(source, top, bottom):
    from PIL import Image, ImageDraw, ImageFont, ImageOps
    from core import images as ci
    r = ci.resolve(source)
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(r.data))).convert('RGB')
    w, h = img.size
    draw = ImageDraw.Draw(img)
    size = max(16, w // 11)
    try:
        font = ImageFont.load_default(size=size)          # Pillow's bundled TrueType (>= 10.1)
    except Exception:
        font = ImageFont.load_default()
    stroke = max(2, size // 12)
    pad = max(8, w // 40)
    line_h = int(size * 1.15)
    for text, at_top in ((top, True), (bottom, False)):
        text = ' '.join(str(text or '').upper().split())
        if not text:
            continue
        lines = _wrap(draw, text, font, w - 2 * pad)[:6]
        y = pad if at_top else h - pad - line_h * len(lines)
        for line in lines:
            x = (w - draw.textlength(line, font=font)) / 2
            draw.text((x, y), line, font=font, fill='white', stroke_width=stroke, stroke_fill='black')
            y += line_h
    out = io.BytesIO()
    img.save(out, 'JPEG', quality=92)
    return out.getvalue()


def execute(function_name, arguments, config, plugin_settings=None, credentials=None):
    a = arguments or {}
    try:
        from core import images as ci
        from plugins.social import engine
        from plugins.social.errors import SocialError
        try:
            if function_name == 'social_meme':
                raw = _meme(a.get('image'), a.get('top'), a.get('bottom'))
                return ci.result("Meme ready — post it with social_post(image=...).", [raw])
            if function_name == 'social_list':
                return engine.list_text(), True
            profile, why = engine.resolve_profile(a.get('profile'))
            if not profile:
                return why, False
            if function_name == 'social_post':
                images = _images(a.get('image'))
                return engine.post(profile, a.get('connections'), a.get('text'), images, str(a.get('link') or '').strip()), True
            if function_name == 'social_inbox':
                return engine.inbox(profile, a.get('connections'), a.get('hours') or 24), True
            if function_name == 'social_reply':
                return engine.reply(profile, a.get('connection'), a.get('item_id'), a.get('text')), True
            if function_name == 'social_stats':
                return engine.stats(profile, a.get('connections')), True
            if function_name == 'social_delete':
                return engine.delete(profile, a.get('connection'), a.get('post_id')), True
            return f"Unknown social tool '{function_name}'.", False
        except (SocialError, ci.ImageError) as e:
            return str(e), False
    except Exception as e:
        logger.error(f"[SOCIAL] {function_name} failed: {e}", exc_info=True)
        return f"{function_name} failed: {type(e).__name__}: {e}", False
