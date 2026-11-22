"""ComfyUI tools: run one of the installed workflows on the user's ComfyUI server.

Workflows are added in Settings > Plugins > ComfyUI (upload a file, or import ComfyUI's
last run). The graph logic and the HTTP client live in plugins.comfyui.comfy.
"""
import io
import logging
import random
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
                        "description": "Input image for a workflow that edits or animates one: img:<id>, doc:<N>, a path or a URL"
                    },
                    "width": {
                        "type": "integer",
                        "description": "Pixels (default: the workflow's)"
                    },
                    "height": {
                        "type": "integer",
                        "description": "Pixels (default: the workflow's)"
                    },
                    "seed": {
                        "type": "integer",
                        "description": "Omit for random"
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
            "description": "List installed ComfyUI workflows: what each makes and whether it takes an input image.",
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
        bits.append('takes an input image')
    if d['size']:
        bits.append(f"{d['size'][0]}x{d['size'][1]}")
    return (f"  {name}{' (default)' if name == default else ''}: {', '.join(bits)}"
            + (f". {d['description']}" if d['description'] else ''))


def _list(settings):
    default = cf.pick(None, settings.get('default_workflow'))[0]         # raises the "none installed" words
    lines = [_line(name, path, default) for name, path in cf.workflows().items()]
    return "Installed workflows:\n" + "\n".join(lines) + f"\n\nUse comfy_generate(prompt='...', workflow='{default}')."


def _generate(args, settings):
    prompt = args.get('prompt')
    if prompt is not None and not isinstance(prompt, str):
        raise cf.ComfyError("The prompt has to be text.")
    if not (prompt or '').strip():
        raise cf.ComfyError("No prompt provided")
    prompt = prompt.strip()
    workflow = args.get('workflow')
    name, path = cf.pick(workflow if isinstance(workflow, str) else None, settings.get('default_workflow'))
    graph, meta = cf.read(path)
    kind = cf.kind(graph)

    comfy, image = cf.Comfy(settings.get('comfy_url')), None
    source = args.get('image')
    source = source.strip() if isinstance(source, str) else ''
    if _private():
        _guard_private(comfy.url, source)
    if source:
        if not cf.image_target(graph, meta):
            raise cf.ComfyError(f"Workflow '{name}' takes no input image. comfy_list_workflows shows the ones that do.")
        from core import images as ci
        try:
            image = comfy.upload(ci.resolve(source).data)
        except ci.ImageError as e:
            raise cf.ComfyError(str(e)) from None

    seed = _int(args.get('seed'))
    if seed is None or seed < 0:
        seed = random.randint(1, 2 ** 31 - 1)
    size = {k: _int(args.get(k)) for k in ('width', 'height')}
    unread = [f"{k} {str(args[k])[:20]!r}" for k in size if args.get(k) not in (None, '') and (size[k] is None or size[k] <= 0)]
    run, notes = cf.apply(graph, meta, prompt, seed, size['width'] if not unread else None,
                          size['height'] if not unread else None, image)
    if unread:
        notes.append(f"Couldn't read the size ({', '.join(unread)}): width and height are whole numbers of pixels. "
                     "The workflow kept its own.")
    wait = (_seconds(settings.get('timeout'), 300) if kind == 'image'
            else _seconds(settings.get('video_timeout'), 1200))
    logger.info(f"[COMFY] {name}: {kind}, seed {seed}, waiting up to {int(wait)} s")

    outputs, fixed = comfy.run(run, wait)
    notes += [f"Read '{f['value']}' as '{f['to']}' on this ComfyUI." for f in fixed]
    items = cf.outputs_of(outputs)
    if not items:
        raise cf.ComfyError("ComfyUI finished but handed back no file. The workflow needs a Save Image or Save Video "
                            "node" + ("" if cf.seed_inputs(graph) else
                                      ", and with no seed input ComfyUI skips a run it has already made") + ".")
    return _deliver(comfy, name, items, seed, prompt, notes)


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


def _deliver(comfy, name, items, seed, prompt, notes):
    """Fetch what the run made and shape the tool result: stills go to the chat's image
    store, clips are kept on disk and played from this plugin's file route."""
    from core import attachments, images as ci
    pics = [i for i in items if i['kind'] == 'image'][:MAX_PICS]
    files = [i for i in items if i['kind'] != 'image'][:MAX_FILES]
    made, lines, shown, links = [], [], [], []

    for n, item in enumerate(files):
        dest = cf.keep(name, Path(item['filename']).suffix.lower())
        comfy.fetch(item, dest)
        links.append({'url': f'/api/plugin/comfyui/file/{dest.name}', 'name': dest.name})
        frames, seconds = cf.stills(dest) if n == 0 else ([], None)
        what = {'video': 'clip', 'audio': 'audio file'}.get(item['kind'], 'file')
        made.append(f"a {f'{seconds:.0f} s ' if seconds else ''}{what} '{dest.name}'")
        if len(frames) > 1:
            shown.append(ci.contact_sheet(frames))
            lines.append(f"You're looking at {len(frames)} frames of it, start to end.")
        elif frames:
            shown.append(ci.for_chat(frames[0]))
            lines.append("You're looking at its first frame.")
        elif n == 0 and item['kind'] == 'video':
            lines.append("(No frames to show you: " + ("ffmpeg couldn't read the clip.)" if cf.has_ffmpeg()
                                                        else "this machine has no ffmpeg.)"))

    if len(pics) == 1:
        shown.append(_shape(comfy.fetch(pics[0])))
        made.append("an image")
    elif pics:
        entries = [{'raw': _shape(comfy.fetch(p))} for p in pics]
        sheet, tail = ci.gallery(f"ComfyUI: {name}", entries)
        shown += sheet
        made.append(f"{len(pics)} images")
        lines += [f"{i}. {e['handle']}" for i, e in enumerate(entries, 1) if e.get('handle')] + [tail]

    text = (f"Made {' and '.join(made)} with workflow '{name}', seed {seed}. Already shown to the user.\n"
            f"Prompt: {prompt[:200]}")
    for extra in notes + lines:
        text += "\n" + extra
    if links:
        text += "\n" + attachments.marker(f"ComfyUI: {name}", links)
    return ci.result(text, shown) if shown else text
