"""ComfyUI tools: run one of the installed workflows on the user's ComfyUI server.

Workflows are added in Settings > Plugins > ComfyUI (upload a file, or import ComfyUI's
last run). The graph logic and the HTTP client live in plugins.comfyui.comfy.
"""
import copy
import io
import logging
import random
import re
from pathlib import Path

from plugins.comfyui import comfy as cf

logger = logging.getLogger(__name__)

ENABLED = True
EMOJI = "\U0001F3A8"
TOOL_CATEGORY = 'media'

MAX_PX = 2160          # the long side of a picture kept in chat
MAX_PICS = 12
MAX_FILES = 4

TOOLS = [
    {
        "type": "function",
        "writes": True,
        "is_local": True,
        "function": {
            "name": "comfy_generate",
            "description": (
                "Generate an image or a video with ComfyUI. It appears in chat for you and the user."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "Detailed description: subject, style, lighting, composition (for video: what happens)"
                    },
                    "workflow": {
                        "type": "string",
                        "description": "Workflow name from comfy_list_workflows. Omit for the default."
                    },
                    "image": {
                        "type": "string",
                        "description": "Input image for a workflow that edits or animates: img:<id>, doc:<N>, a path or a URL. Several, if it has room: comma-separated, main picture first (the result takes its shape)."
                    },
                    "shape": {
                        "type": "string",
                        "enum": ["square", "portrait", "landscape"],
                        "description": "portrait = taller than wide, landscape = wider. Omit to keep the main input image's shape (no image: the workflow's own)"
                    },
                    "seed": {
                        "type": "integer",
                        "description": "Omit for random"
                    },
                    "extras": {
                        "type": "object",
                        "description": "A workflow's own settings, by the names comfy_list_workflows gives (each workflow has its own): {\"Duration\": 8}. Omit for its defaults."
                    }
                },
                "required": ["prompt"]
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "comfy_list_workflows",
            "description": "List installed ComfyUI workflows: what each makes, its own shape, whether it takes an input image, and the extras it can be given.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    }
]


def _settings(given=None):
    if given:
        return given
    try:
        from core.plugin_loader import plugin_loader
        return plugin_loader.get_plugin_settings("comfyui") or {}
    except Exception:
        return {}


def _int(v):
    if v in (None, '') or isinstance(v, bool):
        return None
    try:
        return int(v)
    except (TypeError, ValueError, OverflowError):
        return None


def _private():
    """Is this a private chat? Any doubt reads as yes."""
    try:
        from core.chat.function_manager import scope_private
        return bool(scope_private.get())
    except Exception:
        return True


def _guard_private(url, source):
    """The tool is flagged local so it works in a private chat. That only holds while
    nothing leaves this network: no picture fetched from the web, no ComfyUI out on it."""
    from core import net
    host = cf.host_of(url)
    if net.classify(host) == 'wan':
        raise cf.ComfyError(f"This chat is private and ComfyUI is at {host}, outside this network, so nothing was sent.")
    if source and not (source.startswith(('img:', 'doc:')) or Path(source).is_absolute()):
        raise cf.ComfyError("In a private chat the input image has to be one that is already here "
                            "(img:<id>, doc:<N> or a file path), not a web address.")


def _wanted(args):
    """The shape she named, or None. A word that is not one, and the width and height this tool
    used to take, are answered before anything runs, with the word to pass instead."""
    shape = args.get('shape')
    if shape not in (None, ''):
        word = shape.strip().lower() if isinstance(shape, str) else None
        if word not in cf.SHAPES:
            raise cf.ComfyError(f"shape is one of: {', '.join(cf.SHAPES)}. Leave it out to keep the input image's shape.")
        return word
    if any(args.get(k) not in (None, '') for k in ('width', 'height')):
        w, h = _int(args.get('width')), _int(args.get('height'))
        word = f"shape='{cf.shape_of(w / h)}'" if w and h and w > 0 and h > 0 else f"shape ({', '.join(cf.SHAPES)})"
        raise cf.ComfyError(f"width and height are no longer taken: pass {word}, or leave it out to keep the input "
                            "image's shape. The workflow keeps its own pixel count.")
    return None


_HANDLE = re.compile(r'(?i)^(img:|doc:|https?://|/|~|[a-z]:[\\/])')        # what core.images takes as a picture


def _sources(image, room):
    """Her `image` argument as a list of picture handles: one, or several for a workflow with
    room for them (a list, or one text with commas). Anything that is not a handle is refused
    here, before the first picture is fetched: a wrong one would otherwise be dropped in
    silence, or a comma inside an address would cut it in two."""
    form = "image is a picture: img:<id>, doc:<N>, a path or a URL. Several: separated by commas."
    if image in (None, '', []):
        return []
    if isinstance(image, str):
        parts = [s.strip() for s in re.split(r',\s*|\n', image) if s.strip()]
        several = len(parts) > 1 and all(_HANDLE.match(s) for s in parts)
        if several and room == 1:
            raise cf.ComfyError("This workflow takes one input image.")
        items = parts if several and room > 1 else [image.strip()]
    elif isinstance(image, list) and all(isinstance(s, str) and s.strip() for s in image):
        items = [s.strip() for s in image]
    else:
        raise cf.ComfyError(form)
    bad = next((s for s in items if not _HANDLE.match(s)), None)
    if bad is not None:
        raise cf.ComfyError(f"'{cf._clip(bad, 60)}' is not a picture. {form}")
    return items


def _seconds(v, default):
    try:
        return max(10.0, min(float(v), 86400.0))
    except (TypeError, ValueError):
        return float(default)


def execute(function_name, arguments, config=None, plugin_settings=None, credentials=None):
    try:
        if function_name == "comfy_list_workflows":
            return _list(_settings(plugin_settings)), True
        if function_name == "comfy_generate":
            return _generate(arguments or {}, _settings(plugin_settings)), True
    except cf.ComfyError as e:
        return str(e), False
    except Exception as e:
        # the log keeps the detail; her copy gets no Python text and no paths from this machine
        logger.error(f"[COMFY] {function_name} failed: {e}", exc_info=True)
        return f"Something went wrong inside the ComfyUI plugin ({type(e).__name__}). Its log has the detail.", False
    return f"Unknown function: {function_name}", False


def _line(name, path, default):
    try:
        d = cf.describe(*cf.read(path))
    except cf.ComfyError as e:
        return f"  {name}: unusable. {e}"
    except Exception as e:                       # one odd file must not take the list down
        logger.warning(f"[COMFY] {name} can't be described: {e}")
        return f"  {name}: unusable. Its file isn't a workflow ComfyUI could run."
    bits = [d['kind']]
    if d['takes_image']:
        bits.append('takes an input image' if d['image_slots'] < 2 else f"takes up to {d['image_slots']} input images")
    if d['kind'] != 'audio':
        bits.append(f"{d['shape']} by default" if d['shape'] else 'shape not settable')
    extras = cf.controls_line(d['extras'])
    return (f"  {name}{' (default)' if name == default else ''}: {', '.join(bits)}"
            + (f". {d['description']}" if d['description'] else '')
            + (f"\n      extras: {extras}" if extras else ''))


def _list(settings):
    default = cf.pick(None, settings.get('default_workflow'))[0]         # raises the "none installed" words
    lines = [_line(name, path, default) for name, path in cf.workflows().items()]
    hint = (" extras are optional and each workflow has its own: the list shows its value now and how far it goes. "
            "Pass them as extras={\"Name\": value}.") if any('\n      extras: ' in line for line in lines) else ''
    return "Installed workflows:\n" + "\n".join(lines) + f"\n\nUse comfy_generate(prompt='...', workflow='{default}').{hint}"


def _generate(args, settings):
    prompt = args.get('prompt')
    if prompt is not None and not isinstance(prompt, str):
        raise cf.ComfyError("The prompt has to be text.")
    if not (prompt or '').strip():
        raise cf.ComfyError("No prompt provided")
    prompt = prompt.strip()
    workflow = args.get('workflow')
    if workflow not in (None, '') and not isinstance(workflow, str):
        raise cf.ComfyError("workflow is a name from comfy_list_workflows.")
    name, path = cf.pick(workflow, settings.get('default_workflow'))
    graph, meta = cf.read(path)
    kind = cf.kind(graph)
    shape, extras = _wanted(args), args.get('extras') or None
    # Everything that can be refused without a picture is refused first, on the workflow as her
    # settings leave it: a setting may be the switch that decides whether it takes a picture at all.
    as_set = copy.deepcopy(graph)
    if extras:
        cf.set_extras(as_set, meta, extras)
    if shape and not cf.size_plan(as_set)[0]:
        raise cf.ComfyError(f"Workflow '{name}' makes its own shape, which can't be set from here. Call again without shape.")
    room = len(cf.image_slots(as_set, meta))
    sources = _sources(args.get('image'), room)
    comfy, picture = cf.Comfy(settings.get('comfy_url')), None
    if _private():
        for one in sources or ['']:
            _guard_private(comfy.url, one)
    if sources:
        if not room:
            raise cf.ComfyError(f"Workflow '{name}' takes no input image"
                                + (" the way it is set here." if cf.image_slots(graph, meta) else ". comfy_list_workflows shows the ones that do."))
        if len(sources) > room:
            raise cf.ComfyError(f"Workflow '{name}' takes {'one input image' if room == 1 else f'at most {room} input images'}.")
        from core import images as ci
        try:
            picture = [ci.resolve(one).data for one in sources]
        except ci.ImageError as e:
            raise cf.ComfyError(cf._clip(e, 300)) from None

    seed = _int(args.get('seed'))
    if seed is None or seed < 0:
        seed = random.randint(1, 2 ** 31 - 1)
    run, notes, shaped = cf.plan(comfy, graph, meta, name, prompt, seed, shape, extras, picture)
    wait = (_seconds(settings.get('timeout'), 300) if kind == 'image'
            else _seconds(settings.get('video_timeout'), 1200))
    logger.info(f"[COMFY] {name}: {kind}, seed {seed}, waiting up to {int(wait)} s")

    outputs, fixed = comfy.run(run, wait)
    notes += comfy.said(fixed)
    items = cf.outputs_of(outputs)
    if not items:
        raise cf.ComfyError("ComfyUI finished but handed back no file. The workflow needs a Save Image or Save Video "
                            "node" + ("" if cf.seed_inputs(graph) else
                                      ", and with no seed input ComfyUI skips a run it has already made") + ".")
    return _deliver(comfy, name, items, seed, prompt, notes, shaped, cf.backup_words(settings))


def _shape(raw):
    """A still as the chat keeps it: at most MAX_PX on the long side; PNG only when it
    is really see-through (a JPEG would paint that black)."""
    from core import images as ci
    from PIL import Image
    try:
        # the bytes come from another machine: only the plain picture formats are ever decoded
        img = Image.open(io.BytesIO(raw), formats=cf.SAFE_FORMATS)
        if img.mode in ('RGBA', 'LA', 'PA') or 'transparency' in img.info:
            rgba = img.convert('RGBA')
            if rgba.getextrema()[3][0] < 255:
                rgba.thumbnail((MAX_PX, MAX_PX), Image.LANCZOS)
                buf = io.BytesIO()
                rgba.save(buf, 'PNG')
                return buf.getvalue()
        return ci.for_chat(raw, max_px=MAX_PX, quality=90)
    except Exception as e:
        logger.warning(f"[COMFY] an output picture can't be opened: {e}")
        raise cf.ComfyError("ComfyUI handed back a picture that can't be opened.") from None


def _size(raw):
    """(width, height) of a picture's bytes, or None."""
    from PIL import Image
    try:
        return Image.open(io.BytesIO(raw), formats=cf.SAFE_FORMATS).size
    except Exception:
        return None


def _deliver(comfy, name, items, seed, prompt, notes, shaped=None, kept_words=''):
    """Fetch what the run made and shape the tool result: stills go to the chat's image
    store, clips are kept on disk and played from this plugin's file route. A result names
    its real size wherever it can be measured. `shaped` is (the shape she named, her input
    picture's size, whether the workflow had a size to set) when a shape was wanted."""
    from core import attachments, images as ci
    pics = [i for i in items if i['kind'] == 'image'][:MAX_PICS]
    files = [i for i in items if i['kind'] != 'image'][:MAX_FILES]
    made, lines, shown, links, sizes = [], [], [], [], []

    def sized(what, raw):
        sizes.append(_size(raw) if raw else None)
        return what + (f" ({sizes[-1][0]}x{sizes[-1][1]})" if sizes[-1] else '')

    for n, item in enumerate(files):
        dest = cf.keep(name, Path(item['filename']).suffix.lower())
        comfy.fetch(item, dest)
        links.append({'url': f'/api/plugin/comfyui/file/{dest.name}', 'name': dest.name})
        frames, seconds = cf.stills(dest) if n == 0 else ([], None)
        what = {'video': 'clip', 'audio': 'audio file'}.get(item['kind'], 'file')
        made.append(sized(f"a {f'{seconds:.0f} s ' if seconds else ''}{what} '{dest.name}'", frames[0] if frames else b''))
        if len(frames) > 1:
            shown.append(ci.contact_sheet(frames))
            lines.append(f"You're looking at {len(frames)} frames of it on one sheet, start to end.")
        elif frames:
            shown.append(ci.for_chat(frames[0]))
            lines.append("You're looking at its first frame.")
        elif n == 0 and item['kind'] == 'video':
            lines.append("(No frames to show you: " + ("ffmpeg couldn't read the clip.)" if cf.has_ffmpeg()
                                                        else "this machine has no ffmpeg.)"))

    good = []                                       # (the bytes as made, the bytes as the chat keeps them)
    for p in pics:
        raw = comfy.fetch(p)
        try:
            good.append((raw, _shape(raw)))
        except cf.ComfyError:
            if len(pics) == 1 and not files:        # the only thing made: that is the answer
                raise
    if len(good) < len(pics):                       # one bad picture never costs her the clip or the other pictures
        n = len(pics) - len(good)
        lines.append(f"{n} picture{'' if n == 1 else 's'} ComfyUI handed back couldn't be opened and {'was' if n == 1 else 'were'} left out.")
    if len(good) == 1:
        shown.append(good[0][1])
        made.append(sized("an image", good[0][0]))
    elif good:
        entries = [{'raw': kept} for _, kept in good]
        sheet, tail = ci.gallery(f"ComfyUI: {name}", entries)
        shown += sheet
        made.append(sized(f"{len(good)} images", good[0][0]))
        lines += [f"{i}. {e['handle']}" for i, e in enumerate(entries, 1) if e.get('handle')] + [tail]
    if not made:
        raise cf.ComfyError("ComfyUI handed back pictures that can't be opened.")

    first = [n for n in notes if n.startswith(cf.LEADS)]           # not the run that was asked for: she reads that before anything else
    notes = [n for n in notes if n not in first]
    text = ''.join(n + "\n" for n in first) + (
        f"Made {' and '.join(made)} with workflow '{name}', seed {seed}. Already shown to the user.\n"
        f"Prompt: {prompt[:200]}")
    said = cf.shape_note(*shaped, sizes[0]) if shaped else None    # sizes[0]: the main thing made, never a preview beside it
    for extra in notes + ([said] if said else []) + lines:
        text += "\n" + extra
    if links and kept_words:
        text += "\n" + kept_words
    if links:
        text += "\n" + attachments.marker(f"ComfyUI: {name}", links)
    return ci.result(text, shown) if shown else text
