# functions/images.py — image_view: the one door for looking at a picture
# (2026-09-09, image-tools rebuild). Any handle core.images.resolve knows:
# img:<id> (a tool image from this chat), doc:<N> (a Mind Palace library
# image), an absolute path, or a URL. Replaces get_images-as-viewer,
# gallery_view and view_image.

import logging

logger = logging.getLogger(__name__)

ENABLED = True
EMOJI = '🖼️'

AVAILABLE_FUNCTIONS = ['image_view']

TOOLS = [
    {
        "type": "function",
        "network": True,
        "is_local": False,
        "function": {
            "name": "image_view",
            "description": ("Look at an image. source = img:<id> (the '(image img:...)' handle a tool "
                            "gave you), doc:<N> (a library image), an absolute file path, or an image URL. "
                            "You see it; the user sees it too. For keyed library images pass private_key."),
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {"type": "string", "description": "img:<id>, doc:<N>, /absolute/path, or https://..."},
                    "private_key": {"type": "string", "description": "Gate word for a keyed library image (optional)"}
                },
                "required": ["source"]
            }
        }
    }
]


def execute(function_name, arguments, config):
    if function_name != 'image_view':
        return f"Unknown function: {function_name}", False
    from core import images as ci
    source = (arguments.get('source') or '').strip()
    try:
        r = ci.resolve(source, private_key=arguments.get('private_key'))
        w, h = r.size
        shaped = ci.for_chat(r.data)
    except ci.ImageError as e:
        return str(e), False
    except Exception as e:
        logger.error(f"[IMAGES] image_view {source!r}: {type(e).__name__}: {e}")
        return f"Couldn't open that image: {e}", False
    origin = {'chat': 'from this chat', 'library': 'from the library',
              'file': 'from disk', 'web': 'from the web'}[r.origin]
    return ci.result(f"{r.label} — {w}x{h} — {origin}. You're looking at it now.", [shaped]), True
