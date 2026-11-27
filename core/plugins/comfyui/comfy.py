"""ComfyUI for Sapphire: the workflow files, the wiring of a graph, and the HTTP client.

A workflow is ComfyUI's API-format graph: {node id: {class_type, inputs}}. Each lives in
user/comfyui/workflows/<name>.json. A `_sapphire` key in the same file carries what Sapphire
knows about it (a description, a picked prompt node), so one file moves between machines
whole. ComfyUI never sees that key: read() hands back nodes only.

The wiring rules, stated once:
- Only the live graph counts: the nodes that feed a save or preview node. ComfyUI runs nothing else.
  The side of an either/or switch that is switched off is not live either.
- The prompt goes where the main sampler's `positive` link leads, through guiders, nodes that
  pass conditioning along, and string nodes, to where the text is typed. A sampler that works
  on pixels (a face detailer, an upscaler) keeps its own prompt. An either/or switch on the way
  is followed down the side it is set to: text typed on that side of the switch is the place,
  and a prompt rewriter waiting on the other side is not.
- Every seed is set, also one typed in a number node and wired in.
- The shape is set on every size in the sampler's latent chain, each keeping its pixel count: numbers typed
  there or in a number node wired in, or a pick from a list of ratios. Pixels are never asked for.
- Her input image goes into the image loader that is not a reference (a pose, a style, a mask).
  A workflow with room for several gets them in order, and a slot nobody filled is unplugged:
  the picture saved in the workflow file never rides along with hers.
- Her extras are the workflow's own controls: a titled number or switch node, and the few inputs every
  model has (steps, cfg, frames, frame rate). Each stays in a range around the value the workflow came with.
- Where two answers are possible, the call takes the nearest and says so. It never stays silent.
"""
import copy
import hashlib
import io
import json
import logging
import math
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import quote, urlsplit

from core.paths import PROJECT_ROOT

logger = logging.getLogger(__name__)

WORKFLOW_DIR = PROJECT_ROOT / 'user' / 'comfyui' / 'workflows'
OUTPUT_DIR = PROJECT_ROOT / 'user' / 'comfyui' / 'outputs'
DEFAULT_URL = 'http://127.0.0.1:8188'
META = '_sapphire'
MAX_UPLOAD = 32 * 1024 * 1024
MAX_STILL = 64 * 1024 * 1024
MAX_CLIP = 4 * 1024 ** 3
QUEUE_WAIT = 6 * 3600                       # how long a job may wait its turn in ComfyUI's queue
PANEL = "Settings > Plugins > ComfyUI"
NO_PICTURE = "No input picture was passed"
LEADS = (NO_PICTURE, "Slot ")                # notes that start so mean the run is not the one asked for: a result leads with them
FIXED = "This workflow's shape can't be set from here, so it kept its own."
LOCK = threading.RLock()                    # a workflow file's read-change-write (the panel's routes)

# Where prompt text is typed: encoders first, then the string and wildcard nodes that feed them.
PROMPT_KEYS = ('text', 'prompt', 'clip_l', 'clip_g', 't5xxl', 'text_g', 'text_l', 'bert', 'mt5xl', 'tags',
               'wildcard_text', 'populated_text', 'value', 'string')
SEED_KEYS = ('seed', 'noise_seed')
NUMBER_KEYS = ('value', 'seed', 'noise_seed', 'int', 'number')      # a number node feeding a seed input
COND_IN = {'positive', 'negative', 'conditioning', 'cond', 'cond1', 'cond2', 'uncond', 'conditioning_1',
           'conditioning_2', 'conditioning_to', 'conditioning_from'}
GUIDER_KEYS = ('conditioning', 'cond', 'cond1', 'cond2')
LATENT_IN = ('samples', 'latent', 'latent_image')
# Inputs a prompt never arrives through: tracing stops there, so it can't wander into another branch.
_NOT_TEXT = {'model', 'vae', 'clip', 'latent_image', 'latent', 'samples', 'image', 'images', 'pixels', 'noise',
             'sampler', 'sigmas', 'mask', 'guider', 'audio', 'video', 'clip_vision', 'control_net', 'upscale_model',
             'start_image', 'end_image'}
_NOT_SIZE = {'positive', 'negative', 'conditioning', 'model', 'vae', 'clip', 'noise', 'sampler', 'sigmas', 'guider',
             'control_net', 'clip_vision', 'mask'}
# Classes that take a picture as a guide. No 'mask' here: core's own resize node is ResizeImageMaskNode,
# and a loader used only for its mask is told apart by which of its outputs is wired.
_REFERENCE = re.compile(r'(?i)control|ip.?adapter|clip.?vision|preprocessor|pose|depth|canny|style|instantid|'
                        r'pulid|insight|face.?id|reactor|redux')
_REF_TITLE = re.compile(r'(?i)pose|reference|style|control|mask')
_OUTPUT = re.compile(r'(?i)save|preview|combine|output|export|write|show|display|send')
_UPSCALE = re.compile(r'(?i)upscal|upsampl')
_SAVES = re.compile(r'(?i)save|combine|export|write')             # an output node that leaves a file behind
_YESNO = re.compile(r'(?i)primitive|constant|bool|toggle')          # a node that only HOLDS a yes or no
_WORKS_OUT = re.compile(r'(?i)not|neg|invert|compare|logic')        # ...and one that works it out from something else
SHAPES = ('square', 'portrait', 'landscape')
SIZE_KEYS = ('value', 'int', 'number')                              # a number node feeding a width or a height
_RATIO = re.compile(r'\s*(\d+(?:\.\d+)?)\s*[:xX\u00d7/]\s*(\d+(?:\.\d+)?)')
# Inputs a width or height never arrives through: a number is traced through number and math nodes only.
_NOT_NUMBER = {'image', 'images', 'pixels', 'samples', 'latent', 'latent_image', 'video', 'audio', 'input', 'model',
               'vae', 'clip', 'positive', 'negative', 'conditioning', 'mask', 'sigmas', 'noise', 'guider', 'sampler'}
_RATIO_KEY = re.compile(r'(?i)ratio|aspect|resolution|size|dimension|preset')
VALUE_KEYS = ('value', 'int', 'float', 'number', 'boolean', 'bool')   # what a number or switch node holds
# Inputs every model family types under one name: input -> (her word for it, whole numbers only, lowest, highest)
KNOWN = {'steps': ('Steps', True, 1, 150), 'cfg': ('CFG', False, 0, 30), 'guidance': ('Guidance', False, 0, 30),
         'denoise': ('Denoise', False, 0.05, 1), 'length': ('Frames', True, 1, None), 'num_frames': ('Frames', True, 1, None),
         'video_frames': ('Frames', True, 1, None), 'frames_number': ('Frames', True, 1, None),
         'seconds': ('Seconds', False, 1, 600), 'frame_rate': ('Frame Rate', False, 1, 120),
         'fps': ('Frame Rate', False, 1, 120), 'batch_size': ('Images', True, 1, 4)}
MAX_EXTRAS = 12                                     # controls she is offered per workflow; a file can hold any number of nodes
_PLAIN_TITLE = re.compile(r'(?i)^(primitive)?\s*(int|integer|float|number|bool|boolean|value|constant)?\s*(constant|node)?$')
_TYPE_TITLE = re.compile(r'(?i)^(?:primitive\s*)?(?:bool(?:ean)?|int(?:eger)?|float|number|switch)\s*\((.+)\)$')
_PASS = re.compile(r'(?i)math|expression|primitive|calc|reroute|constant|\bint\b|\bfloat\b')   # nodes a number only passes through
IMAGE_EXT = {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.tif', '.tiff'}
VIDEO_EXT = {'.mp4', '.webm', '.mov', '.m4v', '.mkv'}
AUDIO_EXT = {'.mp3', '.wav', '.ogg', '.oga', '.m4a', '.flac', '.opus'}
KEPT_EXT = IMAGE_EXT | VIDEO_EXT | AUDIO_EXT | {'.gif'}             # anything else is kept as .bin
MEDIA_TYPES = {'.mp4': 'video/mp4', '.webm': 'video/webm', '.mov': 'video/quicktime', '.m4v': 'video/mp4',
               '.mkv': 'video/x-matroska', '.mp3': 'audio/mpeg', '.wav': 'audio/wav', '.ogg': 'audio/ogg',
               '.oga': 'audio/ogg', '.m4a': 'audio/mp4', '.flac': 'audio/flac', '.opus': 'audio/ogg',
               '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp',
               '.gif': 'image/gif', '.bmp': 'image/bmp', '.tif': 'image/tiff', '.tiff': 'image/tiff'}
SAFE_FORMATS = ['PNG', 'JPEG', 'WEBP', 'GIF', 'BMP', 'TIFF']        # never a format that runs an outside program
FOLDERS = {'unet_name': 'diffusion_models', 'ckpt_name': 'checkpoints', 'vae_name': 'vae', 'lora_name': 'loras',
           'clip_name': 'text_encoders', 'clip_name1': 'text_encoders', 'clip_name2': 'text_encoders',
           'clip_name3': 'text_encoders', 'control_net_name': 'controlnet', 'style_model_name': 'style_models'}
EDITOR = ("That is ComfyUI's editor format, which only its own page can run. In ComfyUI use File > Export (API) "
          "and upload that file, or run the workflow once there and use Import last run.")
_WIN = {'con', 'prn', 'aux', 'nul', *(f'com{i}' for i in range(1, 10)), *(f'lpt{i}' for i in range(1, 10))}


class ComfyError(Exception):
    """A failure in words a person (or she) can act on."""


class Rejected(ComfyError):
    """ComfyUI refused the graph; `body` is its own account of why."""

    def __init__(self, body):
        super().__init__('ComfyUI refused the workflow')
        self.body = body if isinstance(body, dict) else {}


def _now():
    return time.monotonic()


def _sleep(seconds):
    time.sleep(seconds)


def _clip(text, n=120):
    """A string from another machine, short enough to quote."""
    s = ' '.join(str(text).split())
    return s if len(s) <= n else s[:n - 3] + '...'


def _num(v):
    """A real number: not a switch, not NaN or infinity. (A whole number of any length is one; isfinite would choke on it.)"""
    return (isinstance(v, int) and not isinstance(v, bool)) or (isinstance(v, float) and math.isfinite(v))


# ── workflow files ───────────────────────────────────────────────────────────

def slug(name):
    raw = str(name or '')
    s = re.sub(r'[^A-Za-z0-9_-]+', '-', raw).strip('-_')[:60]
    if s and s.lower() not in _WIN:
        return s
    if s or not raw.strip():
        return ('workflow-' + s).strip('-')
    # a name with nothing a file name can keep (another script): still its own name, the same every time
    return 'workflow-' + hashlib.sha1(raw.encode('utf-8')).hexdigest()[:6]


def workflows():
    """{name: path}, in the order every list shows them."""
    if not WORKFLOW_DIR.is_dir():
        return {}
    return {p.stem: p for p in sorted(WORKFLOW_DIR.glob('*.json'), key=lambda p: p.stem.lower())}


def find(name):
    """(name, path) of an installed workflow, or (None, None). A name is looked up in the list,
    never joined into a path, so nothing outside the folder can be named."""
    have, n = workflows(), str(name or '').strip()
    if n not in have and n.lower().endswith('.json'):
        n = n[:-5]
    if n in have:
        return n, have[n]
    same = [k for k in have if k.lower() == n.lower()]
    return (same[0], have[same[0]]) if len(same) == 1 else (None, None)


def pick(name, default=''):
    """The workflow a call means: the one named, else the default, else the first."""
    have = workflows()
    if not have:
        raise ComfyError(f"No ComfyUI workflows are installed yet. A person adds one in {PANEL}: "
                         "upload a workflow file, or import the last run from ComfyUI.")
    if str(name or '').strip():
        got = find(name)
        if not got[1]:
            raise ComfyError(f"No workflow called '{_clip(name, 60)}'. Installed: {', '.join(have)}")
        return got
    got = find(default) if str(default or '').strip() else (None, None)
    return got if got[1] else next(iter(have.items()))


def clean_meta(meta):
    m = meta if isinstance(meta, dict) else {}
    out = {k: str(m[k]).strip()[:300] for k in ('description', 'prompt_node', 'image_node') if str(m.get(k) or '').strip()}
    extras = {}                                     # what a person changed about a control: {'node:input': {name, min, max, off}}
    for cid, o in list(m['extras'].items())[:60] if isinstance(m.get('extras'), dict) else []:
        o = o if isinstance(o, dict) else {}
        row = {k: o[k] for k in ('min', 'max') if _num(o.get(k)) and abs(o[k]) <= 1e9}
        if str(o.get('name') or '').strip():
            row['name'] = _clip(o['name'], 40)
        if o.get('off') is True:
            row['off'] = True
        if row:
            extras[str(cid)[:80]] = row
    return {**out, 'extras': extras} if extras else out


def _no_constant(name):
    raise ValueError(f'{name} is not a number a workflow can hold')


def _json(text):
    """JSON as ComfyUI writes it; NaN and Infinity are refused (they break every size and seed)."""
    try:
        return json.loads(text, parse_constant=_no_constant)
    except RecursionError:
        raise ValueError('nested too deep') from None


def parse(raw):
    """Any JSON a person might hand over -> (graph, meta). ComfyError says what it is instead."""
    if isinstance(raw, dict) and isinstance(raw.get('prompt'), dict) and _nodes(raw['prompt']):
        raw = raw['prompt']                                   # the body of a /prompt call
    if not isinstance(raw, dict):
        raise ComfyError("That isn't a ComfyUI workflow.")
    graph = _nodes(raw)
    if not graph:
        raise ComfyError(EDITOR if isinstance(raw.get('nodes'), list) else "There are no ComfyUI nodes in that file.")
    return graph, clean_meta(raw.get(META))


def _nodes(raw):
    out = {}
    for k, v in raw.items():
        if isinstance(v, dict) and isinstance(v.get('class_type'), str):
            out[str(k)] = {**v, 'inputs': v['inputs'] if isinstance(v.get('inputs'), dict) else {}}
    return out


def read(path):
    try:
        raw = _json(Path(path).read_text(encoding='utf-8-sig'))
    except UnicodeDecodeError:
        raise ComfyError(f"The workflow file {Path(path).name} isn't saved as UTF-8 text. Save it as UTF-8 and add it again.") from None
    except (OSError, ValueError) as e:
        why = e.strerror if isinstance(e, OSError) else e
        raise ComfyError(f"The workflow file {Path(path).name} can't be read ({why}).") from None
    return parse(raw)


def write(name, graph, meta=None):
    from core.fs_utils import replace_with_retry          # Windows: a scanner may hold the old file a moment
    WORKFLOW_DIR.mkdir(parents=True, exist_ok=True)
    data, m = dict(graph), clean_meta(meta)
    if m:
        data[META] = m
    dest = WORKFLOW_DIR / f'{name}.json'
    tmp = dest.with_name(f'.{name}.{uuid.uuid4().hex[:8]}.tmp')
    try:
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')
        replace_with_retry(tmp, dest)
    finally:
        tmp.unlink(missing_ok=True)
    return dest


def from_upload(data):
    """An uploaded file -> (graph, meta): a workflow .json, or a PNG that ComfyUI saved
    (its Save Image node writes the API graph into the picture)."""
    if len(data) > MAX_UPLOAD:
        raise ComfyError("That file is too big to be a workflow.")
    if data[:8] == b'\x89PNG\r\n\x1a\n':
        from PIL import Image
        try:
            img = Image.open(io.BytesIO(data), formats=['PNG'])
            text = img.info.get('prompt') or (getattr(img, 'text', None) or {}).get('prompt')
        except Exception:
            raise ComfyError("That picture can't be read.") from None
        if not text:
            raise ComfyError("That picture carries no workflow. Only a PNG saved by ComfyUI's Save Image node does; "
                             "a screenshot or a re-saved copy loses it.")
        data = text.encode('utf-8') if isinstance(text, str) else bytes(text)
    try:
        raw = _json(data.decode('utf-8-sig'))
    except ValueError:
        raise ComfyError("That file isn't JSON. Upload a workflow exported from ComfyUI with File > Export (API), "
                         "or a PNG that ComfyUI saved.") from None
    return parse(raw)


# ── the wiring of a graph ────────────────────────────────────────────────────

def order(graph):
    plain = lambda k: k.isascii() and k.isdigit() and len(k) < 19
    return sorted(graph, key=lambda k: (not plain(k), int(k) if plain(k) else 0, k))


def title(graph, nid):
    n = graph.get(nid) or {}
    meta = n.get('_meta') if isinstance(n.get('_meta'), dict) else {}
    return _clip(meta.get('title') or n.get('class_type') or nid, 80)


def _link(v):
    """The node id an input is wired to, or None for a literal."""
    if isinstance(v, list) and len(v) == 2 and isinstance(v[0], (str, int)) and isinstance(v[1], int) \
            and not isinstance(v[0], bool) and not isinstance(v[1], bool):
        return str(v[0])
    return None


def _links(graph, nid):
    """[(input key, source node)] of one node's wired inputs."""
    return [(k, _link(v)) for k, v in graph[nid]['inputs'].items() if _link(v) in graph]


def live(graph):
    """The nodes ComfyUI would run: everything a save or preview node depends on. A leftover
    branch that reaches no output is not part of the workflow."""
    fed = {src for nid in graph for _, src in _links(graph, nid)}
    ends = [n for n in graph if _OUTPUT.search(graph[n]['class_type'])] or [n for n in graph if n not in fed]
    seen, todo = set(), list(ends)
    while todo:
        nid = todo.pop()
        if nid not in seen:
            seen.add(nid)
            dead = {'on_true', 'on_false'} - set(_sides_of_switch(graph, nid) or ('on_true', 'on_false'))
            todo += [src for k, src in _links(graph, nid) if k not in dead]     # a switch: only the side it is set to
    # an output node we don't know by name: better the whole graph than a workflow with no sampler in it
    samples = lambda nodes: any(k == 'positive' or k in GUIDER_KEYS for n in nodes for k, _ in _links(graph, n))
    return seen if samples(seen) or not samples(graph) else set(graph)


def _yes(graph, v):
    """A yes/no setting as it stands: True, False, or None when it can't be read. It is typed,
    or wired from a node that only holds one. A node that works a yes or no out (a NOT, a
    compare) is never read: its own switch says nothing about what comes out of it."""
    if isinstance(v, bool):
        return v
    src = graph.get(_link(v) or '')
    if not src or v[1] != 0 or len(src['inputs']) != 1 or not _YESNO.search(src['class_type']) \
            or _WORKS_OUT.search(src['class_type']):
        return None
    return next((x for k, x in src['inputs'].items() if k in VALUE_KEYS and isinstance(x, bool)), None)


def _sides_of_switch(graph, nid):
    """The inputs an either/or switch passes on, as it is set now: ['on_false'], ['on_true'],
    both when what sets it can't be read, [] for a node that is no switch. The setting is a
    yes/no typed on the node, or in the switch node wired to it."""
    ins = graph[nid]['inputs']
    if 'on_true' not in ins and 'on_false' not in ins:
        return []
    key = next((k for k in ('switch', 'boolean', 'condition', 'cond', 'bool') if k in ins), None)
    v = _yes(graph, ins.get(key))
    sides = ['on_true' if v else 'on_false'] if isinstance(v, bool) else ['on_true', 'on_false']
    return [k for k in sides if k in ins]


def _typed(graph, nid):
    """The inputs of a node where prompt text is typed: its text inputs, and for an either/or
    switch the text typed on the side it is set to."""
    ins = graph[nid]['inputs']
    return [k for k in PROMPT_KEYS if isinstance(ins.get(k), str)] + \
           [k for k in _sides_of_switch(graph, nid) if isinstance(ins.get(k), str)]


def _leaves(graph, start, role, either=None):
    """Every node upstream of `start` where the prompt for `role` is typed, nearest first.
    A node that passes both sides along (a ControlNet, an image-to-video node) is followed
    on our side only; a text input wired from a string node is followed to that node.
    `either` (a set) collects the nodes found on or behind a switch whose setting can't be
    read: one of them reaches the picture and nobody can tell which, so they belong together."""
    other = 'negative' if role == 'positive' else 'positive'
    out, seen, todo = [], set(), [(start, False)]
    while todo:
        nid, unsure = todo.pop(0)
        if nid in seen or nid not in graph:
            continue
        seen.add(nid)
        links = _links(graph, nid)
        sides = _sides_of_switch(graph, nid)
        unsure = unsure or len(sides) > 1
        if sides:                                    # an either/or switch: only the side it is set to reaches the picture
            found, nxt = bool(_typed(graph, nid)), [src for k, src in links if k in sides]
        elif _typed(graph, nid):
            found, nxt = True, [src for k, src in links if k in PROMPT_KEYS]      # a second piece wired in beside the typed one
        elif any(k == role for k, _ in links):
            found, nxt = False, [src for k, src in links if k == role]
        else:
            found, nxt = False, [src for k, src in links if k != other and k not in _NOT_TEXT]
        if found:
            out.append(nid)
            if unsure and either is not None:
                either.add(nid)
        todo += [(src, unsure) for src in nxt]
    return out


def _takers(graph, alive, role):
    """[(node, source)] of the samplers and guiders that take conditioning for `role`: the ends
    of the chain, not the nodes that pass it along."""
    passes = {src for nid in alive for k, src in _links(graph, nid) if k in COND_IN}
    out = []
    for nid in order(graph):
        if nid not in alive or nid in passes:
            continue
        guider = 'guider' in graph[nid]['class_type'].lower()
        keys = (role, 'uncond') if role == 'negative' else (role,) + (GUIDER_KEYS if guider else ())
        out += [(nid, src) for k, src in _links(graph, nid) if k in keys]
    return out


def _on_pixels(graph, nid):
    """A sampler that reworks a finished picture (a face detailer, a tiled upscaler)."""
    return any(k in ('image', 'images', 'pixels') for k, _ in _links(graph, nid))


def text_nodes(graph, alive=None):
    """Every live node a prompt could be typed into (the panel's choices)."""
    alive = live(graph) if alive is None else alive
    return [nid for nid in order(graph) if nid in alive and _typed(graph, nid)]


def _best(graph, nodes):
    """Of several places prompt text is typed, the one most likely hers: a title that says so, else the nearest."""
    named = [n for n in nodes if re.search(r'(?i)prompt|subject|positive|main', title(graph, n))
             and not re.search(r'(?i)negative|style|prefix|suffix|quality', title(graph, n))]
    return (named or nodes)[0]


def prompt_plan(graph, meta=None, alive=None):
    """(targets, others): [(node, key)] the prompt is written into, and the nodes that also
    hold prompt text for the same sampler and are left alone. ([], []) when it can't be told."""
    alive = live(graph) if alive is None else alive
    keyed = lambda nodes: [(n, k) for n in nodes for k in _typed(graph, n)]
    want = str((meta or {}).get('prompt_node') or '')
    if want in alive and _typed(graph, want):       # a pick that is switched off right now would swallow the prompt
        return keyed([want]), []
    takers = _takers(graph, alive, 'positive')
    main = [t for t in takers if not _on_pixels(graph, t[0])] or takers
    picked, others = [], []
    for _, src in main:
        either = set()
        found = _leaves(graph, src, 'positive', either)
        if not found:
            continue
        best = _best(graph, found)
        mine = [best] + [n for n in found if n != best and best in either and n in either]   # both sides of a switch nobody can read
        picked += [n for n in mine if n not in picked]
        others += [n for n in found if n not in mine and n not in others]
    if picked:
        return keyed(picked), [n for n in others if n not in picked]
    # no sampler we recognise: fall back to the one text node that is not a negative
    negative = {n for _, src in _takers(graph, alive, 'negative') for n in _leaves(graph, src, 'negative')}
    left = [n for n in text_nodes(graph, alive) if n not in negative and 'negative' not in title(graph, n).lower()]
    if len(left) == 1:
        return keyed(left), []
    named = [n for n in left if re.search(r'(?i)positive|prompt', title(graph, n))]
    return (keyed(named), []) if len(named) == 1 else ([], [])


def prompt_targets(graph, meta=None):
    return prompt_plan(graph, meta)[0]


def seed_inputs(graph, alive=None):
    """[(node, key)] of every seed: typed on the node, or typed in the number node wired to it."""
    alive = live(graph) if alive is None else alive
    out = []
    for nid in order(graph):
        if nid not in alive:
            continue
        for key in SEED_KEYS:
            v = graph[nid]['inputs'].get(key)
            hit = None
            if _num(v):
                hit = (nid, key)
            elif _link(v) in graph:
                src = graph[_link(v)]['inputs']
                hit = next(((_link(v), k) for k in NUMBER_KEYS if _num(src.get(k))), None)
            if hit and hit not in out:
                out.append(hit)
    return out


def _ratio(text):
    """'16:9 (Widescreen)' or '1344x768 landscape' -> (16.0, 9.0), else None."""
    m = _RATIO.match(text) if isinstance(text, str) else None
    pair = (float(m[1]), float(m[2])) if m else None
    return pair if pair and min(pair) > 0 and max(pair) < 1e6 else None


def _picker(node):
    """The input of a node that picks the shape from a list of ratios, or None."""
    return next((k for k, v in node['inputs'].items() if _RATIO_KEY.search(k) and _ratio(v)), None)


def _turn(node):
    """The switch beside a ratio list that lays the picked size on its side (a list that only
    holds wide sizes, with `invert` for the tall ones), or None."""
    return next((k for k, v in node['inputs'].items() if isinstance(v, bool) and re.search(r'(?i)invert|swap', k)), None)


def _sides(ins):
    """(width key, height key) of a node: the plain names, or the one pair that ends in them
    (`resize_type.width`, `empty_latent_width`, `custom_width`). (None, None) without such a pair."""
    out = []
    for side in ('width', 'height'):
        keys = [side] if side in ins else [k for k in ins if re.search(rf'(?i)[._]{side}$', k)]
        out.append(keys[0] if len(keys) == 1 else None)
    return tuple(out) if all(out) else (None, None)


def _pixels(v):
    return _num(v) and 16 <= v <= 65536


def _typed_at(graph, nid, key, side):
    """(node, key) where the number behind a width or height input is typed, the nearest one
    through the number and math nodes wired in. A node that picks from a list of ratios ends the
    search and is the answer; so does a node that types that side itself. Never followed through
    a picture, a latent or a model: a size read off another picture is not typed anywhere."""
    v = graph[nid]['inputs'].get(key)
    if _num(v):
        return (nid, key) if _pixels(v) else None
    seen, todo = set(), [_link(v)]
    while todo and len(seen) < 64:
        src = todo.pop(0)
        if src in seen or src not in graph:
            continue
        seen.add(src)
        ins = graph[src]['inputs']
        pick = _picker(graph[src])
        if pick:
            return src, pick
        own = _sides(ins)[side == 'height']
        hit = next((k for k in ([own] if own else []) + [k for k in SIZE_KEYS if k in ins] if _pixels(ins[k])), None)
        if hit:
            return src, hit
        todo += [_link(x) for k, x in ins.items() if _link(x) and k.split('.')[0] not in _NOT_NUMBER]
    return None


def _place(graph, nid):
    """Where this node gets the picture's shape from: {'w': (node, key), 'h': (node, key)} for
    two typed numbers, {'pick': (node, key)} for a list of ratios, None when it sets no size."""
    ins = graph[nid]['inputs']
    pick = _picker(graph[nid])
    if pick and not any(k in LATENT_IN for k, _ in _links(graph, nid)):
        return {'pick': (nid, pick)}                # a ratio list wins over sides typed beside it (kept for its "custom" entry)
    wk, hk = _sides(ins)
    if not wk:
        return None
    w, h = _typed_at(graph, nid, wk, 'width'), _typed_at(graph, nid, hk, 'height')
    if not (w and h):
        return None
    if w == h:                                      # both sides come out of one node: only a ratio list does that
        return {'pick': w} if _ratio(graph[w[0]]['inputs'][w[1]]) else None
    return {'w': w, 'h': h} if all(_num(graph[n]['inputs'][k]) for n, k in (w, h)) else None


def size_plan(graph, alive=None):
    """(places, staged): every place the picture's shape is set on a sampler's latent chain,
    and whether something upscales on the way (then a typed size is not the final one)."""
    alive = live(graph) if alive is None else alive
    places, staged, seen = [], False, set()
    todo = [(src, k) for nid in order(graph) if nid in alive for k, src in _links(graph, nid) if k == 'latent_image']
    # A sampler with no `latent_image` (the video wrappers type the size on the sampler, or on a node
    # wired into it under a name of their own): the walk starts at the sampler itself.
    todo += [(nid, 'latent') for nid in order(graph) if nid in alive and re.search(r'(?i)sampler', graph[nid]['class_type'])
             and not _UPSCALE.search(graph[nid]['class_type']) and not _on_pixels(graph, nid)]
    while todo:
        nid, via = todo.pop(0)
        if nid in seen or nid not in alive:         # not alive: the switched-off side of a switch
            continue
        seen.add(nid)
        links = _links(graph, nid)
        latent_in = any(k in LATENT_IN for k, _ in links)
        place = _place(graph, nid)
        if place and place not in places:
            places.append(place)
        if _UPSCALE.search(graph[nid]['class_type']) or (place and latent_in):
            staged = True
        # A sized node that hands on a LATENT with none coming in starts the chain: the picture feeding it is
        # resized to fit, so the walk ends. One that hands on PIXELS (a resize between two passes) does not:
        # the pass before it has its own size, and both must take the shape or the picture is squeezed.
        if not (place and not latent_in and re.search(r'(?i)latent|samples', via)):
            sides = _sides(graph[nid]['inputs'])
            todo += [(src, k) for k, src in links if k not in _NOT_SIZE and k not in sides]
    return places, staged


def _own(graph, place):
    """(width, height) a place holds now: pixels when typed, the two sides of the ratio when picked."""
    if 'pick' in place:
        nid, key = place['pick']
        w, h = _ratio(graph[nid]['inputs'][key])
        turn = _turn(graph[nid])
        return (h, w) if turn and graph[nid]['inputs'][turn] else (w, h)
    return tuple(graph[n]['inputs'][k] for n, k in (place['w'], place['h']))


def shape_of(ratio):
    """width / height in the three words she uses."""
    return 'square' if abs(math.log(ratio)) < 0.1 else 'landscape' if ratio > 1 else 'portrait'


def reshape(graph, want, choices=None, alive=None):
    """Give the picture another shape, in place. `want` is width / height (her input picture's),
    or one of SHAPES: then a workflow already lying that way keeps its ratio, one lying the other
    way is turned, and a square one gets 16:9 for video and 3:2 for a still. Every size keeps its
    pixel count, on the grid it was typed on. `choices(class, input)` is what a ratio list offers
    on that ComfyUI; a list can only come near, so it goes first and the typed sizes take the
    ratio it landed on. False when the workflow has no size that can be set. What really came
    out is for the caller to measure: a node may ignore what is typed on it."""
    places, _ = size_plan(graph, alive)
    if places and want in SHAPES:
        w, h = _own(graph, places[0])
        long = max(w / h, h / w)
        if shape_of(long) == 'square':
            long = 16 / 9 if kind(graph) == 'video' else 3 / 2
        want = {'square': 1.0, 'landscape': long, 'portrait': 1 / long}[want]
    if not places or not _num(want) or not 0.01 <= want <= 100:
        return False
    done = False
    for place in sorted(places, key=lambda p: 'pick' not in p):
        w, h = _own(graph, place)
        if 'pick' in place:
            nid, key = place['pick']
            ins, turn = graph[nid]['inputs'], _turn(graph[nid])
            if abs(math.log(want * h / w)) >= 0.03:
                offered = [(o, _ratio(o)) for o in (choices(graph[nid]['class_type'], key) if choices else None) or []]
                found = [(o, flip, (r[1], r[0]) if flip else r) for o, r in offered if r for flip in ((False, True) if turn else (False,))]
                if not found:
                    return False                    # a list nobody can read: nothing else is touched, or the passes would disagree
                # the nearest ratio; of two alike, the one nearest in pixels, then the one that leaves the switch alone
                near = lambda c: (round(abs(math.log(want * c[2][1] / c[2][0])), 3),
                                  abs(math.log(c[2][0] * c[2][1] / (w * h))), c[1] != bool(turn and ins[turn]))
                ins[key], flip, (w, h) = min(found, key=near)
                if turn:
                    ins[turn] = flip
            want = w / h
        elif abs(math.log(want * h / w)) >= 0.03:
            step = 32 if w % 32 == 0 and h % 32 == 0 else 16
            side = lambda area: int(max(64, min(round(math.sqrt(area) / step) * step, 8192)))
            (wn, wk), (hn, hk) = place['w'], place['h']
            graph[wn]['inputs'][wk], graph[hn]['inputs'][hk] = side(w * h * want), side(w * h / want)
        done = True
    return done


# -- her extras: the workflow's own controls ----------------------------------

def _said(v):
    """A value as she reads it: on / off, 8, 0.7."""
    return ('on' if v else 'off') if isinstance(v, bool) else str(v) if isinstance(v, int) else f'{v:g}'


def _span(v, whole, low, high):
    """(lowest, highest) she may set: a quarter to four times the workflow's own value, inside
    what the input itself can take. The own value is always in it."""
    lo, hi = (v / 4, v * 4) if v > 0 else (0, 4)
    lo, hi = max(lo, low if low is not None else lo), min(hi, high if high is not None else hi)
    if whole:
        lo, hi = max(math.floor(lo), 1 if v >= 1 else 0), math.ceil(hi)
    return min(lo, v), max(hi, v)


def _plain(name):
    """A control's name as it is matched: letters and digits of any script, whatever the case."""
    return re.sub(r'[\W_]+', '', str(name).casefold())


def _uses(graph, nid, fed):
    """[(node, input, direct)] of the inputs a number node ends up in, through math and number nodes."""
    out, seen, todo = [], {nid}, [(nid, True)]
    while todo and len(seen) < 64:
        src, direct = todo.pop(0)
        for user, key in fed.get(src, []):
            if not _PASS.search(graph[user]['class_type']):
                out.append((user, key, direct))
            elif user not in seen:
                seen.add(user)
                todo.append((user, False))
    return out


def _not_hers(graph, nid, key, v, video, fed):
    """A KNOWN input that is no setting on this node: a detailer's or upscaler's own, a full
    denoise, a distilled model's cfg 1, a batch of videos, and the steps of a sampler that
    shares its run with another (the hand-over step would no longer fit)."""
    ins, cls = graph[nid]['inputs'], graph[nid]['class_type']
    if _UPSCALE.search(cls) or (key in ('steps', 'cfg', 'guidance', 'denoise') and _on_pixels(graph, nid)):
        return True
    if (key == 'denoise' and v >= 1) or (key == 'cfg' and v <= 1) or (key == 'batch_size' and video):
        return True
    if key == 'steps':
        start = next((ins[k] for k in ('start_at_step', 'start_step') if _num(ins.get(k))), 0)
        end = next((ins[k] for k in ('end_at_step', 'end_step') if _num(ins.get(k))), v)
        return start > 0 or 0 < end < v or any(re.search(r'(?i)split.?sigma', graph[u]['class_type']) for u, _ in fed.get(nid, []))
    return False


def controls(graph, meta=None, alive=None):
    """The numbers and switches she may set with `extras`, in the workflow's order:
    [{id, name, found, value, whole, min, max, auto, off, places}]. Found by themselves: a number
    or switch node with a title of its own, and the KNOWN inputs typed on a node. What the tool
    sets itself (seed, size) is never one, typed or fed by a number node. Two places of one name
    and one value are one control and move together (a frame rate typed on the sampler side and
    on the save node); two values are two controls. `meta['extras']` holds what a person
    changed: a name, a range (inside what the input can take), off."""
    alive = live(graph) if alive is None else alive
    owned = set(seed_inputs(graph, alive))
    for place in size_plan(graph, alive)[0]:
        owned |= {place[k] for k in ('w', 'h') if k in place}
    fed = {}
    for nid in graph:
        for key, src in _links(graph, nid):
            fed.setdefault(src, []).append((nid, key))
    video, found = kind(graph) != 'image', []
    for nid in order(graph):
        if nid not in alive:
            continue
        ins, cls = graph[nid]['inputs'], graph[nid]['class_type']
        held = [k for k in VALUE_KEYS if isinstance(ins.get(k), bool) or _num(ins.get(k))]
        if len(ins) == 1 and held:                  # a number or switch node: its title is the control's name
            typed = _TYPE_TITLE.match(title(graph, nid))
            name, v = (typed[1] if typed else title(graph, nid)).strip(' ?:'), ins[held[0]]
            uses = _uses(graph, nid, fed)
            if _PLAIN_TITLE.match(name) or name.lower() == cls.lower() or not _plain(name) \
                    or any(k in SEED_KEYS or re.search(r'(?i)(^|[._])(width|height)$', k) for _, k, _ in uses):
                continue                            # no name of its own, or it feeds a seed or a size: the tool's
            known = [(u, k) for u, k, direct in uses if direct and k in KNOWN]
            if any(_not_hers(graph, u, k, v, video, fed) for u, k in known if _num(v)):
                continue
            whole = isinstance(v, int) and 'float' not in cls.lower() and all(KNOWN[k][1] for _, k in known)
            low = max((KNOWN[k][2] for _, k in known if KNOWN[k][2] is not None), default=None)
            high = min((KNOWN[k][3] for _, k in known if KNOWN[k][3] is not None), default=None)
            found.append((_clip(name, 40), nid, held[0], v, whole, low, high))
            continue
        for key, (name, whole, low, high) in KNOWN.items():
            v = ins.get(key)
            if _num(v) and not _not_hers(graph, nid, key, v, video, fed):
                found.append((name, nid, key, v, whole, low, high))
    out, changed = [], (meta or {}).get('extras') if isinstance((meta or {}).get('extras'), dict) else {}
    for name, nid, key, v, whole, low, high in found:
        if (nid, key) in owned or (_num(v) and not 0 <= v <= 1e9):
            continue
        same = [c for c in out if c['found'].split(' #')[0] == name]
        twin = next((c for c in same if c['value'] == v and type(c['value']) is type(v)), None)
        if twin:
            twin['places'].append((nid, key))
            continue
        c = {'id': f'{nid}:{key}', 'found': name if not same else f'{name} #{len(same) + 1}', 'value': v,
             'whole': whole, 'places': [(nid, key)], 'auto': None if isinstance(v, bool) else list(_span(v, whole, low, high))}
        mine = changed.get(c['id']) if isinstance(changed.get(c['id']), dict) else {}
        c['name'], c['off'] = mine.get('name') or c['found'], mine.get('off') is True
        if c['auto']:
            lo, hi = (mine[k] if _num(mine.get(k)) else c['auto'][i] for i, k in enumerate(('min', 'max')))
            lo, hi = min(lo, hi), max(lo, hi)       # a person's range, never past what the input itself can take
            c['min'], c['max'] = max(lo, low if low is not None else lo), min(hi, high if high is not None else hi)
        n = 1
        while _plain(c['name']) in {_plain(o['name']) for o in out}:        # two controls never answer to one name
            n += 1
            c['name'] = f"{c['found']} #{n}"
        out.append(c)
    return out


def offered(found):
    """The controls she is shown and may set: the first MAX_EXTRAS a person has not switched off."""
    return [c for c in found if not c['off']][:MAX_EXTRAS]


def controls_line(found):
    """'Duration 5 (1 to 20), Frame Rate 24 (6 to 96), Prompt Enhance off': what she can set, as the list shows it."""
    return ', '.join(f"{c['name']} {_said(c['value'])}" + (f" ({_said(c['min'])} to {_said(c['max'])})" if c['auto'] else '')
                     for c in offered(found))


def set_extras(graph, meta, extras, alive=None):
    """Write her extras ({name: value}) into the graph, in place; returns what was changed, in
    words. A name the workflow doesn't have, or a value outside its range, is a ComfyError:
    said before anything runs, with what she can set."""
    if isinstance(extras, str):                     # some models hand an object over as its JSON text
        try:
            extras = _json(extras)
        except ValueError:
            pass
    if not extras and not isinstance(extras, str):  # null, {}, [] : nothing asked for
        return []
    if not isinstance(extras, dict):
        raise ComfyError('extras is a set of name: value pairs, like {"Duration": 8}.')
    said, left = {}, [(k, v) for k, v in extras.items() if v is not None]
    for switches in (True, False):                  # a switch may bring other settings to life: switches go first
        have = offered(controls(graph, meta, alive if switches else None))
        can = f"It has: {controls_line(have)}." if have else "It has no extras."
        for key, v in list(left):
            name = _plain(key)
            hits = [c for c in have if _plain(c['name']) == name] or [c for c in have if name and _plain(c['name']).startswith(name)]
            if len(hits) != 1:                      # the whole name, or a start of it that only one control has
                if switches:
                    continue
                raise ComfyError(f"This workflow has {'more than one' if hits else 'no'} '{_clip(key, 40)}' to set. {can}")
            c = hits[0]
            if isinstance(c['value'], bool) != switches:
                continue
            left.remove((key, v))
            if switches:
                new = {'true': True, 'on': True, 'yes': True, '1': True,
                       'false': False, 'off': False, 'no': False, '0': False}.get(str(v).strip().lower())
                if new is None:
                    raise ComfyError(f"{c['name']} is a switch: true or false.")
            else:
                try:
                    new = float(v) if not isinstance(v, bool) else None
                except (TypeError, ValueError, OverflowError):
                    new = None
                if new is None or not math.isfinite(new) or (c['whole'] and not new.is_integer()):
                    raise ComfyError(f"{c['name']} takes a {'whole ' if c['whole'] else ''}number.")
                if not c['min'] <= new <= c['max']:
                    raise ComfyError(f"{c['name']} goes from {_said(c['min'])} to {_said(c['max'])} here "
                                     f"(the workflow's own is {_said(c['value'])}).")
                new = int(new) if c['whole'] else new
            for nid, k in c['places']:
                graph[nid]['inputs'][k] = new
            if new != c['value']:
                said[key] = f"{c['name']} {_said(new)} (the workflow's own is {_said(c['value'])})."
    return [said[k] for k in extras if k in said]


def _users(graph):
    """({node: [the nodes its first output (the picture) feeds]}, {nodes with any output in use})"""
    first, fed = {}, set()
    for nid in graph:
        for v in graph[nid]['inputs'].values():
            if _link(v) in graph:
                fed.add(_link(v))
                if v[1] == 0:
                    first.setdefault(_link(v), []).append(nid)
    return first, fed


def _is_reference(graph, nid, users, depth=2):
    """An image loaded to guide the picture (a pose, a style, a face) or only for its mask,
    not to be the picture: every node its picture feeds is a guide, directly or one step on."""
    first, fed = users
    takers = first.get(nid) or []
    if not takers:
        return nid in fed                          # only its mask output is used

    def guides(n, left):
        if _REFERENCE.search(graph[n]['class_type']):
            return True
        nxt = first.get(n) or []
        return left > 0 and bool(nxt) and all(guides(m, left - 1) for m in nxt)
    return all(guides(n, depth - 1) for n in takers)


def image_nodes(graph, alive=None):
    """Every live node that loads a picture from ComfyUI's input folder (masks aside)."""
    alive = live(graph) if alive is None else alive
    return [nid for nid in order(graph) if nid in alive and graph[nid]['class_type'].startswith('LoadImage')
            and 'mask' not in graph[nid]['class_type'].lower() and isinstance(graph[nid]['inputs'].get('image'), str)]


def _reaches(graph, nid, first, depth=3):
    """[(node, input)] the picture of a loader arrives at, nearest first, through up to `depth` nodes."""
    out, seen, todo = [], {nid}, [(nid, 0)]
    while todo:
        cur, d = todo.pop(0)
        for user in first.get(cur, []):
            out.append((user, next((k for k, v in graph[user]['inputs'].items() if _link(v) == cur), '')))
            if d < depth and user not in seen:
                seen.add(user)
                todo.append((user, d + 1))
    return out


def _switched_off(graph, nid, alive, first, depth=4):
    """A loader whose picture only ever arrives at nodes that are told to pass it by (`bypass`
    switched on: an image-to-video workflow set to text-to-video). It takes no picture now."""
    users = [u for u in first.get(nid, []) if u in alive]
    return bool(users) and all(
        _yes(graph, graph[u]['inputs'].get('bypass')) is True
        or ('bypass' not in graph[u]['inputs'] and depth > 0 and _switched_off(graph, u, alive, first, depth - 1))
        for u in users)


def image_slots(graph, meta=None, alive=None):
    """The loaders her input pictures go into, in order: the first is THE input picture, the
    rest are room for more. [] when the workflow takes none as it is set. The order is the one
    the model reads them in (image_1 before image_2 on the node they meet at), whatever order
    the loaders were made in. A loader a person picked in the panel is the first."""
    alive = live(graph) if alive is None else alive
    nodes, want = image_nodes(graph, alive), str((meta or {}).get('image_node') or '')
    users = _users(graph)
    mine = [n for n in nodes if not _is_reference(graph, n, users) and not _switched_off(graph, n, alive, users[0])]
    if len(mine) > 1:                              # still several: a title that calls itself a reference steps back
        mine = [n for n in mine if not _REF_TITLE.search(title(graph, n))] or mine
    if len(mine) > 1:
        reach = {n: _reaches(graph, n, users[0]) for n in mine}
        for node, _ in reach[mine[0]]:              # the nearest node all of them arrive at, by numbered inputs
            nums = [re.search(r'(\d+)$', next((k for u, k in reach[n] if u == node), '')) for n in mine]
            if all(nums) and len({int(m[1]) for m in nums}) == len(mine):
                mine = [n for _, n in sorted(zip((int(m[1]) for m in nums), mine))]
                break
    return ([want] + [n for n in mine if n != want]) if want in mine or (want in nodes and not mine) else mine


def image_plan(graph, meta=None, alive=None):
    """(target, others): the loader her input image goes into, and the other slots.
    (None, []) when the workflow takes no input image."""
    slots = image_slots(graph, meta, alive)
    return (slots[0], slots[1:]) if slots else (None, [])


def _unplug(graph, nid, can_drop, alive, keep, depth=6):
    """Take a node's output out of everything it feeds, in place. An input that may stay
    unwired is simply removed. Where it may not, the node that needs it is unplugged the same
    way (it will never run). Left wired, because nothing is lost by it: a node on a switched-off
    side or a leftover branch (not `alive`), and a viewer at the end of a chain. False when the
    cut would reach a node that carries one of HER pictures (`keep`), or a node that saves a
    file: then the caller keeps its untouched graph."""
    for user in list(graph):
        ins = graph[user]['inputs']
        for key in [k for k, v in ins.items() if _link(v) == nid]:
            if user not in alive:
                continue
            family = key.split('.')[0] + '.'                   # a growing family of inputs: images.image_1, images.image_2
            left = sum(1 for k, v in ins.items() if k != key and k.startswith(family) and _link(v))
            if can_drop(graph[user]['class_type'], key, left):
                del ins[key]
            elif user in keep or depth <= 0:
                return False
            elif not any(_link(v) == user for n in graph for v in graph[n]['inputs'].values()):
                if _SAVES.search(graph[user]['class_type']):
                    return False                    # the end of a chain that saves: a real output would be lost
            elif not _unplug(graph, user, can_drop, alive, keep, depth - 1):
                return False
    return True


def _fed_by(graph, sources):
    """Every node downstream of `sources`: the nodes that carry their pictures."""
    out, todo = set(), list(sources)
    while todo:
        cur = todo.pop()
        for n in graph:
            if n not in out and any(_link(v) == cur for v in graph[n]['inputs'].values()):
                out.add(n)
                todo.append(n)
    return out


def image_target(graph, meta=None):
    return image_plan(graph, meta)[0]


def kind(graph):
    """What the workflow makes, read from its save nodes: 'video', 'audio' or 'image'."""
    classes = [n['class_type'] for n in graph.values()]
    if any(re.search(r'(?i)^(save|preview).*(video|webm|animated)|video.?combine', c) for c in classes):
        return 'video'
    if any(re.search(r'(?i)^(save|preview).*audio', c) for c in classes) and \
            not any(re.search(r'(?i)^save.*image', c) for c in classes):
        return 'audio'
    return 'image'


def describe(graph, meta=None):
    meta, alive = meta or {}, live(graph)
    targets, _ = prompt_plan(graph, meta, alive)
    places, _ = size_plan(graph, alive)
    named = lambda ids: [{'id': n, 'title': title(graph, n)} for n in ids]
    own = _own(graph, places[0]) if places else None
    target = image_plan(graph, meta, alive)[0]
    # shape: the workflow's own, in her three words; '' when it has no size that can be set
    return {'kind': kind(graph), 'nodes': len(graph), 'shape': shape_of(own[0] / own[1]) if own else '',
            'seeds': len(seed_inputs(graph, alive)),
            'description': meta.get('description', ''),
            'prompt_node': targets[0][0] if targets else '', 'prompt_nodes': named(text_nodes(graph, alive)),
            'takes_image': target is not None, 'image_slots': len(image_slots(graph, meta, alive)),
            'extras': [{k: v for k, v in c.items() if k != 'places'} for c in controls(graph, meta, alive)],
            'image_node': target or '', 'image_nodes': named(image_nodes(graph, alive))}


def apply(graph, meta, prompt, seed, image=None, can_drop=None):
    """A copy of the graph with this call written in. Returns (graph, notes); a note is
    something the call asked for that the workflow has no place for, or a choice that was made.
    `image` is the name of the input picture on ComfyUI, or a list of them, first slot first.
    `can_drop(class, input, wired beside it)` says whether an input may stay unwired on that
    ComfyUI: with it, a picture slot nobody filled is unplugged. The shape is reshape()'s, on
    the copy this returns."""
    g, notes = copy.deepcopy(graph), []
    quoted = lambda nodes: ', '.join(f"'{title(g, n)}'" for n in nodes[:3])
    slots = image_slots(g, meta)
    given = [i for i in ([image] if isinstance(image, str) else image or []) if isinstance(i, str) and i][:len(slots)]
    saved = [_clip(g[n]['inputs']['image'], 60) for n in slots]
    for nid, name in zip(slots, given):
        g[nid]['inputs']['image'] = name
    if given:                                       # room for more than was given: a saved picture must not ride along
        keep = _fed_by(g, slots[:len(given)])
        for n in range(len(given), len(slots)):
            trial = copy.deepcopy(g)
            if can_drop and _unplug(trial, slots[n], can_drop, live(trial), keep):
                g = trial
            else:
                notes.append(f"Slot {n + 1} of this workflow can't be left empty, so its saved picture ('{saved[n]}') went in too.")
    alive = live(g)
    targets, others = prompt_plan(g, meta, alive)
    loader, image = (slots[0] if slots else None), bool(given)
    if not targets:
        if loader and not image:
            raise ComfyError("This workflow has no prompt input and works on a picture: pass one with "
                             "comfy_generate's image argument.")
        if not (loader and image):
            raise ComfyError("Can't tell where the prompt goes in this workflow. "
                             f"A person can pick its prompt node in {PANEL}.")
        notes.append("This workflow has no prompt input, so the prompt was not used.")
    for nid, key in targets:
        g[nid]['inputs'][key] = prompt
    if others:
        notes.append(f"The prompt went into '{title(g, targets[0][0])}'. This workflow also keeps prompt text in "
                     f"{quoted(others)}, left as it was. A person can pick the node in {PANEL}.")
    seeds = seed_inputs(g, alive)
    for nid, key in seeds:
        g[nid]['inputs'][key] = seed
    if not seeds:
        notes.append("This workflow has no seed input: the same prompt gives the same result.")
    if loader and not image:
        names = ', '.join(f"'{n}'" for n in saved)
        notes.append(f"{NO_PICTURE}, so this used the workflow's saved {'one' if len(saved) == 1 else 'ones'} ({names}). "
                     "To use yours, pass image.")
    return g, notes


def plan(comfy, graph, meta, name, prompt, seed, shape=None, extras=None, picture=None):
    """One call made ready to queue, for her tool and for the Apps page alike. `picture` is the
    input image's bytes (or a list of them, for a workflow with room for several), `shape`
    one of SHAPES, `extras` {name: value}. Everything that can be
    refused is refused here, before a job exists. Returns (graph, notes, shaped): shaped is
    (shape, the picture's size, whether the workflow had a size to set, what to call the
    picture) when a shape was wanted, for shape_note() once the result is measured."""
    # the settings first: one of them may be the switch that decides where the prompt goes,
    # or whether the workflow takes a picture at all
    graph, said = copy.deepcopy(graph), []
    if extras:
        said = set_extras(graph, meta, extras)      # a wrong name or value: said before a picture is sent
    image = pic = None
    pictures = [picture] if isinstance(picture, (bytes, bytearray)) else [p for p in picture or [] if p]
    if pictures:
        room = len(image_slots(graph, meta))
        if not room:
            raise ComfyError(f"Workflow '{name}' takes no input image.")
        if len(pictures) > room:
            raise ComfyError(f"Workflow '{name}' takes {'one input image' if room == 1 else f'at most {room} input images'}.")
        ready = []
        for n, raw in enumerate(pictures, 1):       # every picture is opened before the first one is sent
            try:
                ready.append(as_png(raw))
            except ComfyError as e:
                raise ComfyError(f"Picture {n}: {e}" if len(pictures) > 1 else str(e)) from None
        image, pic = [comfy.send(data) for data, _ in ready], ready[0][1]      # the shape follows the first picture
    run, notes = apply(graph, meta, prompt, seed, image, comfy.can_drop)
    notes += said
    # the shape: the one named, else the picture's, else the workflow is left as it is
    want = None if kind(graph) == 'audio' else shape or (pic[0] / pic[1] if pic else None)
    could = bool(want) and reshape(run, want, comfy.choices)
    if shape and not could:                         # a named shape and no place for it: said now, not after a long render
        raise ComfyError(f"Workflow '{name}' makes its own shape, which can't be set from here. Call again without shape.")
    who = 'Your first picture' if len(pictures) > 1 else 'Your picture'
    return run, notes, ((shape, pic, could, who) if want else None)


def shape_note(shape, pic, could, who, got):
    """What to say about the shape, judged by what really came out. `got` is the measured
    (width, height), None when it couldn't be measured; `could` is whether the workflow had a
    size to set; `who` is "Your picture", or "Your first picture" of several. A workflow can
    ignore what was set, or only offer something near."""
    mine = f"{who} is {pic[0]}x{pic[1]}" if pic else ''
    if shape:
        if got and shape_of(got[0] / got[1]) != shape:
            return f"Asked for {shape} and it came out {got[0]}x{got[1]}: the nearest this workflow makes."
        # the word won over the picture's shape: say so, it explains a crop
        return f"{mine} and you asked for {shape}: it is cropped or squeezed to fit." if pic and shape_of(pic[0] / pic[1]) != shape else None
    if (got and abs(math.log(got[0] * pic[1] / (got[1] * pic[0]))) < 0.12) or (could and not got):
        return f"{mine}: the result takes its shape."
    if could:
        return f"{mine} and the nearest shape this workflow makes is {got[0]}x{got[1]}: it is cropped or squeezed to fit."
    return f"{FIXED} {mine}: where the two shapes differ it is cropped or squeezed to fit."


# ── does it fit that ComfyUI? ────────────────────────────────────────────────

def _options(spec):
    """The choices of a pick-one input, from either shape ComfyUI describes them in."""
    if not isinstance(spec, (list, tuple)) or not spec:
        return None
    if isinstance(spec[0], list):
        return spec[0]
    if spec[0] == 'COMBO' and len(spec) > 1 and isinstance(spec[1], dict) and isinstance(spec[1].get('options'), list):
        return spec[1]['options']
    return None


def _flat(v):
    return str(v).replace('\\', '/').lower()


def _base(v):
    return _flat(v).rsplit('/', 1)[-1]


def match(value, options):
    """The one choice that is the same file as `value` on another machine: a Windows
    `sub\\model` against a Linux `sub/model`, or a file one side keeps in a folder and the
    other does not. Two files of one name in DIFFERENT folders are different models (every
    repo has a `model.safetensors`): that is never matched."""
    opts = [o for o in options if isinstance(o, str)]
    same = [o for o in opts if _flat(o) == _flat(value)]
    if len(same) == 1:
        return same[0]
    named = [o for o in opts if _base(o) == _base(value)]
    if len(named) == 1 and ('/' not in _flat(value) or '/' not in _flat(named[0])):
        return named[0]
    return None


def _missing(cls, key, value, options=()):
    value = _clip(value, 100)
    if re.match(r'(?i)(vhs_)?load(image|audio|video)', cls or ''):
        return f"the input file '{value}' isn't in that ComfyUI's input folder"
    folder = 'clip_vision' if 'vision' in (cls or '').lower() and key == 'clip_name' else FOLDERS.get(key)
    near = [o for o in options if isinstance(o, str) and _base(o) == _base(value)]
    hint = f"; a file of that name is at '{_clip(near[0], 100)}', which may be another model" if len(near) == 1 else ''
    if folder:
        return f"the model file '{value}' isn't there (it goes in ComfyUI's models/{folder} folder{hint})"
    if re.search(r'\.[A-Za-z0-9]{2,12}$', value):
        return f"the file '{value}' ({_clip(key, 40)}) isn't there{hint}"
    return f"'{value}' isn't one of its choices for {_clip(key, 40)}"


def check(graph, info, meta=None):
    """The graph against one ComfyUI's own node list (`info` = its /object_info)."""
    mine = image_plan(graph, meta)[0]              # her picture fills this one at run time
    out = {'missing_nodes': [], 'missing': [], 'fixes': [], 'takes_image': mine is not None}
    for nid in order(graph):
        n = graph[nid]
        known = info.get(n['class_type']) if isinstance(info, dict) else None
        if not isinstance(known, dict):
            if n['class_type'] not in out['missing_nodes']:
                out['missing_nodes'].append(_clip(n['class_type'], 80))
            continue
        ins = known.get('input') if isinstance(known.get('input'), dict) else {}
        specs = {}
        for part in ('required', 'optional'):
            specs.update(ins[part] if isinstance(ins.get(part), dict) else {})
        for key, val in n['inputs'].items():
            opts = _options(specs.get(key))
            if not opts or not isinstance(val, str) or val in opts or nid == mine:
                continue
            to = match(val, opts)
            row = {'node': nid, 'title': title(graph, nid), 'input': key, 'value': val}
            if to is None:
                out['missing'].append({**row, 'words': _missing(n['class_type'], key, val, opts)})
            else:
                out['fixes'].append({**row, 'to': to})
    out['ok'] = not out['missing_nodes'] and not out['missing']
    return out


def fix_rejection(graph, body):
    """Read ComfyUI's refusal. A name that only differs by machine (path separator,
    a folder one side lacks) is corrected in `graph`. Returns (fixed, problems in words)."""
    fixed, problems = [], []
    err = body.get('error')
    if isinstance(err, str):
        err = {'message': err}
    err = err if isinstance(err, dict) else {}
    extra = err.get('extra_info') if isinstance(err.get('extra_info'), dict) else {}
    if err.get('type') == 'missing_node_type':
        problems.append(f"it has no '{_clip(extra.get('class_type') or 'such', 80)}' node "
                        "(a custom node that isn't installed there)")
    elif err.get('type') == 'prompt_no_outputs':
        problems.append("the workflow has no Save Image or Save Video node")
    errors = body.get('node_errors') if isinstance(body.get('node_errors'), dict) else {}
    for nid, ne in errors.items():
        ne = ne if isinstance(ne, dict) else {}
        cls = str(ne.get('class_type') or (graph.get(nid) or {}).get('class_type') or f'node {nid}')
        for e in ne.get('errors') if isinstance(ne.get('errors'), list) else []:
            if not isinstance(e, dict):
                continue
            x = e.get('extra_info') if isinstance(e.get('extra_info'), dict) else {}
            key, val, opts = x.get('input_name'), x.get('received_value'), _options(x.get('input_config'))
            if cls.startswith('LoadImage') and nid in graph and (key == 'image' or 'image' in str(e.get('details', '')).lower()):
                problems.append(f"the picture '{_clip(graph[nid]['inputs'].get('image'), 80)}' that its '{title(graph, nid)}' "
                                "node loads isn't on that ComfyUI (pass one with comfy_generate's image argument, "
                                "or put the file in ComfyUI's input folder)")
            elif e.get('type') == 'value_not_in_list' and isinstance(key, str) and nid in graph:
                to = match(val, opts) if opts else None
                if to is None or to == graph[nid]['inputs'].get(key):
                    problems.append(_missing(cls, key, val, opts or ()))
                else:
                    graph[nid]['inputs'][key] = to
                    fixed.append({'node': nid, 'input': key, 'value': val, 'to': to})
            else:
                problems.append(_clip(f"{cls}: {e.get('message') or 'refused'} {e.get('details') or ''}", 200))
    if not fixed and not problems:
        problems.append(_clip(err.get('message') or 'it gave no reason', 200))
    return fixed, list(dict.fromkeys(problems))


# ── the client ───────────────────────────────────────────────────────────────

def _left_out(graph, node_errors):
    """The titles of the output nodes ComfyUI dropped from a job it took, when the job still
    saves what it was asked for: every dropped node is the end of a chain, none of them saves
    a file, and a node that does is still in. None otherwise (a workflow whose only result is
    a preview has nothing to spare), and when ComfyUI doesn't say which outputs were lost."""
    outs = set()
    for ne in node_errors.values() if isinstance(node_errors, dict) else [None]:
        deps = ne.get('dependent_outputs') if isinstance(ne, dict) else None
        if not isinstance(deps, list) or not deps or not all(isinstance(d, (str, int)) for d in deps):
            return None
        outs |= {str(d) for d in deps}
    feeds = {_link(v) for n in graph for v in graph[n]['inputs'].values()}
    savers = {n for n in graph if _SAVES.search(graph[n]['class_type'])}
    if not outs or not savers or any(o not in graph or o in feeds or o in savers for o in outs):
        return None
    return [title(graph, o) for o in order(graph) if o in outs]


def as_png(raw):
    """One of her input pictures as ComfyUI takes it: (PNG bytes, (width, height)), turned the
    way its EXIF says. A phone's HEIC is read when this machine can; nothing else beyond the
    plain picture formats is ever opened."""
    from PIL import Image, ImageOps
    formats = list(SAFE_FORMATS)
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
        formats.append('HEIF')
    except Exception:
        pass
    try:
        img = ImageOps.exif_transpose(Image.open(io.BytesIO(raw), formats=formats))
        buf = io.BytesIO()
        (img if img.mode in ('RGB', 'RGBA', 'L') else img.convert('RGBA')).save(buf, 'PNG')
    except Exception:
        raise ComfyError("That isn't a picture ComfyUI can take (PNG, JPEG, WEBP, GIF, BMP, TIFF"
                         + (", HEIC" if 'HEIF' in formats else "") + ").") from None
    return buf.getvalue(), img.size


def clean_url(url):
    u = str(url or '').strip() or DEFAULT_URL
    return (u if '://' in u else 'http://' + u).rstrip('/')


def host_of(url):
    try:
        return urlsplit(clean_url(url)).hostname or ''
    except ValueError:
        return ''


def _failure(status):
    """A failed run's status block as one sentence, never ComfyUI's traceback."""
    for m in status.get('messages') if isinstance(status.get('messages'), list) else []:
        if not isinstance(m, (list, tuple)) or len(m) != 2:
            continue
        if m[0] == 'execution_interrupted':
            return "The job was stopped in ComfyUI."
        if m[0] == 'execution_error' and isinstance(m[1], dict):
            msg = _clip(m[1].get('exception_message') or '', 300)
            oom = ' The GPU ran out of memory.' if 'out of memory' in msg.lower() else ''
            return f"ComfyUI failed in its {_clip(m[1].get('node_type') or 'workflow', 60)} node: {msg or 'no reason given'}.{oom}"
    return "ComfyUI reported an error and gave no reason; its own console has it."


class Comfy:
    def __init__(self, url=None, http=None):
        self.url = clean_url(url)
        self.shown = re.sub(r'//[^/@]*@', '//', self.url)      # a name:password in the address is never quoted back
        self.cid = uuid.uuid4().hex
        self.left_out = []                          # nodes ComfyUI ran the last job without (see queue)
        self._specs = {}                            # node class -> its inputs, asked once per client
        if http is None:
            from core import net as http              # the app's pooled, proxy-aware client
        self.http = http

    def _unreachable(self, e):
        import requests
        ex = requests.exceptions
        if isinstance(e, (ex.InvalidURL, ex.MissingSchema, ex.InvalidSchema)):
            return f"'{self.shown}' isn't a web address. It looks like http://192.168.1.20:8188"
        try:
            parts = urlsplit(self.url)
            host, port = parts.hostname or '', parts.port or 80
        except ValueError:
            return f"'{self.shown}' isn't a web address. It looks like http://192.168.1.20:8188"
        if host in ('127.0.0.1', 'localhost', '::1'):
            if isinstance(e, ex.Timeout):
                return f"ComfyUI at {self.shown} didn't answer in time."
            return f"Nothing answers at {self.shown}. Start ComfyUI, or set its address in {PANEL}."
        # another machine: refused and silent look the same from here, and have the same three causes
        return (f"Nothing answers at {self.shown}. Check the address. ComfyUI on that machine has to be started with "
                f"--listen (without it, it answers only its own machine), and its firewall has to allow port {port}.")

    def _call(self, method, path, **kw):
        import requests
        try:
            return getattr(self.http, method)(self.url + path, **kw)
        except requests.exceptions.RequestException as e:
            raise ComfyError(self._unreachable(e)) from None

    def _json(self, r):
        try:
            return r.json()
        except ValueError:
            return None

    def stats(self):
        r = self._call('get', '/system_stats', timeout=6)
        j = self._json(r) if r.status_code == 200 else None
        if not isinstance(j, dict) or not isinstance(j.get('system'), dict):
            raise ComfyError(f"Something answers at {self.shown}, but it isn't ComfyUI (HTTP {r.status_code}).")
        return j

    def info(self):
        r = self._call('get', '/object_info', timeout=90)
        j = self._json(r) if r.status_code == 200 else None
        if not isinstance(j, dict):
            raise ComfyError(f"ComfyUI at {self.shown} wouldn't list its nodes (HTTP {r.status_code}).")
        return j

    def _inputs(self, cls):
        """{'required': {...}, 'optional': {...}} of a node class on this ComfyUI ({} parts when it won't say)."""
        if cls not in self._specs:
            r = self._call('get', f"/object_info/{quote(str(cls), safe='')}", timeout=30)
            j = self._json(r) if r.status_code == 200 else None
            known = j.get(cls) if isinstance(j, dict) else None
            ins = known.get('input') if isinstance(known, dict) and isinstance(known.get('input'), dict) else {}
            self._specs[cls] = {part: ins[part] if isinstance(ins.get(part), dict) else {} for part in ('required', 'optional')}
        return self._specs[cls]

    def choices(self, cls, key):
        """What a pick-one input of a node class offers on this ComfyUI; [] when it won't say."""
        for part in self._inputs(cls).values():
            if key in part:
                return [o for o in _options(part[key]) or [] if isinstance(o, str)]
        return []

    def can_drop(self, cls, key, wired=0):
        """May this input of a node class stay unwired on this ComfyUI? An optional one may. One
        of a growing family (`images.image_2`) may while at least its minimum stays wired:
        `wired` is how many others of the family would. Anything unknown: no."""
        ins = self._inputs(cls)
        if key in ins['optional']:
            return True
        spec = ins['required'].get(key.split('.')[0]) or ins['optional'].get(key.split('.')[0])
        if '.' not in key or not isinstance(spec, (list, tuple)) or 'AUTOGROW' not in str(spec[0]).upper():
            return False
        template = spec[1].get('template') if len(spec) > 1 and isinstance(spec[1], dict) else None
        least = template.get('min') if isinstance(template, dict) else None
        return _num(least) and wired >= least

    def last_run(self):
        """The graph of the newest run that worked, exactly as ComfyUI executed it."""
        r = self._call('get', '/history', params={'max_items': 8}, timeout=30)
        j = self._json(r) if r.status_code == 200 else None
        runs = []
        for e in j.values() if isinstance(j, dict) else []:
            p, status = (e.get('prompt'), e.get('status')) if isinstance(e, dict) else (None, None)
            if isinstance(p, list) and len(p) > 2 and _num(p[0]) and isinstance(p[2], dict) \
                    and not (isinstance(status, dict) and status.get('status_str') == 'error'):
                runs.append(p)
        if not runs:
            raise ComfyError("ComfyUI has no finished run to import. Run the workflow once in ComfyUI, then try again.")
        return max(runs, key=lambda p: p[0])[2]

    def send(self, png):
        """PNG bytes into ComfyUI's input folder; returns the name a LoadImage node takes."""
        name = f"sapphire-{hashlib.sha1(png).hexdigest()[:12]}.png"        # same image, same file, no churn
        r = self._call('post', '/upload/image', files={'image': (name, png, 'image/png')},
                       data={'overwrite': 'true'}, timeout=120)
        j = self._json(r) if r.status_code == 200 else None
        if not isinstance(j, dict) or not isinstance(j.get('name'), str) or not j['name']:
            raise ComfyError(f"ComfyUI wouldn't take the input image (HTTP {r.status_code}).")
        return (f"{j['subfolder']}/" if isinstance(j.get('subfolder'), str) and j['subfolder'] else '') + j['name']

    def upload(self, raw):
        """An input image into ComfyUI's input folder; returns (the name a LoadImage node takes,
        (width, height) as ComfyUI will see it)."""
        png, size = as_png(raw)
        return self.send(png), size

    def queue(self, graph):
        r = self._call('post', '/prompt', json={'prompt': graph, 'client_id': self.cid}, timeout=60)
        j = self._json(r)
        if r.status_code == 200 and isinstance(j, dict) and j.get('prompt_id'):
            pid = str(j['prompt_id'])
            self.left_out = []
            if j.get('node_errors'):
                # It took the job and dropped a branch it couldn't check. A viewer (a compare or preview node,
                # whose saved setting ComfyUI can misread as a wire) costs nothing: ComfyUI's own page runs the
                # workflow the same way. A branch that SAVES a file is not the run that was asked for.
                lost = _left_out(graph, j['node_errors'])
                if lost is None:
                    self.cancel(pid)
                    raise Rejected(j)
                self.left_out = lost
                logger.info(f"[COMFY] ComfyUI ran the job without {', '.join(lost)}: viewers it couldn't check, nothing saved depends on them")
            return pid
        if r.status_code == 400 and isinstance(j, dict):
            raise Rejected(j)
        raise ComfyError(f"ComfyUI at {self.shown} wouldn't take the job (HTTP {r.status_code}).")

    def said(self, fixed):
        """What the last run did differently from the workflow file, in words. (A viewer node
        ComfyUI ran without is in `left_out` and in the log: it is nothing she can act on.)"""
        return [f"Read '{f['value']}' as '{f['to']}' on this ComfyUI." for f in fixed]

    def run(self, graph, timeout, queued=None):
        """Queue and wait. A refusal that is only a name that differs by machine is corrected
        and sent again. Returns (outputs, fixed). `queued(job id)` is called once ComfyUI has
        the job, for a caller that may want to stop it."""
        fixed, pid = [], None
        for last in (False, False, True):
            try:
                pid = self.queue(graph)
                if queued:
                    queued(pid)
                break
            except Rejected as no:
                done, problems = fix_rejection(graph, no.body)
                fixed += done
                if problems or not done or last:
                    raise ComfyError(f"ComfyUI at {self.shown} can't run this workflow: "
                                     + '; '.join(problems or ['it refused the same names again']) + '.') from None
        try:
            return self.wait(pid, timeout), fixed
        except ComfyError:
            raise
        except Exception as e:                     # a reply in a shape nobody expected: don't leave the job running
            logger.error(f"[COMFY] unreadable reply while waiting: {e}", exc_info=True)
            self.cancel(pid)
            raise ComfyError(f"ComfyUI at {self.shown} sent back something Sapphire couldn't read, "
                             "so the job was stopped.") from None

    def _where(self, pid):
        """'running', 'pending', None (not in the queue) or '?' (the queue couldn't be read)."""
        try:
            j = self.http.get(self.url + '/queue', timeout=15).json()
            for state, key in (('running', 'queue_running'), ('pending', 'queue_pending')):
                if any(isinstance(it, list) and len(it) > 1 and str(it[1]) == pid for it in j.get(key) or []):
                    return state
            return None if isinstance(j.get('queue_running'), list) and isinstance(j.get('queue_pending'), list) else '?'
        except Exception:
            return '?'

    def _queued(self, pid):
        """'running', 'pending' or None. Any doubt reads as still there."""
        where = self._where(pid)
        return 'pending' if where == '?' else where

    def cancel(self, pid):
        """Take our job out of ComfyUI: off the queue, and stopped only when it is the one running."""
        try:
            state = self._queued(pid)
            self.http.post(self.url + '/queue', json={'delete': [pid]}, timeout=10)
            if state == 'running':
                self.http.post(self.url + '/interrupt', json={'prompt_id': pid}, timeout=10)
        except Exception as e:
            logger.warning(f"[COMFY] couldn't cancel {pid}: {type(e).__name__}")

    def wait(self, pid, timeout, poll=1.0):
        """The job's outputs. `timeout` is how long ComfyUI may WORK on it: time it spends
        waiting its turn behind another job (hers behind his, or the other way round) is not
        counted, up to QUEUE_WAIT."""
        t0, began, fails, gone, n = _now(), None, 0, 0, 0
        while (_now() - began < timeout) if began is not None else (_now() - t0 < max(timeout, QUEUE_WAIT)):
            _sleep(poll)
            n += 1
            if began is None and self._where(pid) != 'pending':     # its turn has come (or nobody can tell): the clock starts
                began = _now() - poll
            try:
                r = self.http.get(f"{self.url}/history/{pid}", timeout=30)
                j = r.json() if r.status_code == 200 else None
                entry = j.get(pid) if isinstance(j, dict) else None
                fails = 0
            except Exception:
                fails += 1
                if fails >= 10:
                    self.cancel(pid)
                    raise ComfyError(f"Lost contact with ComfyUI at {self.shown} while it was working.") from None
                continue
            if isinstance(entry, dict):
                status = entry.get('status') if isinstance(entry.get('status'), dict) else {}
                if status.get('status_str') == 'error':
                    raise ComfyError(_failure(status))
                if status.get('completed') or entry.get('outputs'):
                    return entry.get('outputs') or {}
            elif n % 5 == 0:
                gone = 0 if self._queued(pid) else gone + 1
                if gone >= 2:                          # twice: a job leaves the queue a moment before its history is written
                    raise ComfyError("ComfyUI no longer has this job. It was restarted, or its queue was cleared.")
        self.cancel(pid)
        if began is None:
            raise ComfyError("ComfyUI never got to this job: it was still waiting behind others after "
                             f"{int(max(timeout, QUEUE_WAIT) // 3600)} hours, so it was taken out.")
        raise ComfyError(f"ComfyUI took longer than {int(timeout)} seconds, so the job was stopped. "
                         f"A slow workflow needs a longer wait: {PANEL}.")

    def fetch(self, item, dest=None):
        """One output file: its bytes, or streamed to `dest`."""
        name = _clip(item['filename'], 80)
        r = self._call('get', '/view', timeout=300, stream=True,
                       params={'filename': item['filename'], 'subfolder': item.get('subfolder', ''),
                               'type': item.get('type', 'output')})
        tmp = Path(dest).with_name(f'.{Path(dest).name}.tmp') if dest else None
        try:
            if r.status_code != 200:
                raise ComfyError(f"ComfyUI made {name} but wouldn't hand it over (HTTP {r.status_code}).")
            cap, size, parts = (MAX_CLIP if dest else MAX_STILL), 0, []
            out = open(tmp, 'wb') if dest else None
            try:
                for chunk in r.iter_content(1 << 20):
                    size += len(chunk)
                    if size > cap:
                        raise ComfyError(f"ComfyUI's {name} is too big to take ({cap >> 20} MB is the limit).")
                    out.write(chunk) if out else parts.append(chunk)
            finally:
                if out:
                    out.close()
            if not size:
                raise ComfyError(f"ComfyUI handed back an empty file for {name}.")
            if not dest:
                return b''.join(parts)
            from core.fs_utils import replace_with_retry      # Windows: a scanner may hold the new file a moment
            replace_with_retry(tmp, Path(dest))
            return dest
        except OSError as e:
            raise ComfyError(f"Couldn't keep {name} ({e.strerror or type(e).__name__}).") from None
        finally:
            if tmp:
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass
            getattr(r, 'close', lambda: None)()


# ── what a run made ──────────────────────────────────────────────────────────

def outputs_of(outputs):
    """A history entry's outputs as [{filename, subfolder, type, kind}], saved files before
    previews. kind: 'image' (a still), 'video', 'audio' or 'file' (an animated image, anything else)."""
    items, seen = [], set()
    for out in outputs.values() if isinstance(outputs, dict) else []:
        if not isinstance(out, dict):
            continue
        flag = out.get('animated')
        moving = any(flag) if isinstance(flag, (list, tuple)) else bool(flag)
        for key in ('images', 'gifs', 'video', 'videos', 'audio'):
            for f in out[key] if isinstance(out.get(key), list) else []:
                if not isinstance(f, dict) or not isinstance(f.get('filename'), str) or not f['filename']:
                    continue
                text = lambda k, d='': f[k] if isinstance(f.get(k), str) else d
                ident = (f['filename'], text('subfolder'), text('type', 'output'))
                if ident in seen:
                    continue
                seen.add(ident)
                ext = Path(f['filename']).suffix.lower()
                k = ('video' if ext in VIDEO_EXT else 'audio' if ext in AUDIO_EXT else
                     'image' if ext in IMAGE_EXT and not moving else 'file')
                items.append({'filename': ident[0], 'subfolder': ident[1], 'type': ident[2], 'kind': k})
    return [i for i in items if i['type'] == 'output'] or items


PRIVATE_MARK = '-pv'       # ends the name (before the extension) of a file made in a private chat


def keep(name, ext, private=False):
    """A fresh path under user/comfyui/outputs for a file a run made. Its extension is one a
    browser plays or shows as a picture; anything else is kept as .bin (never a page).
    `private` = made in a private chat: the name carries PRIVATE_MARK, the one thing that
    says so (the file itself is a plain file, never encrypted)."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ext = ext.lower() if str(ext or '').lower() in KEPT_EXT else '.bin'
    mark = PRIVATE_MARK if private else ''
    return OUTPUT_DIR / f"{slug(name)}-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}{mark}{ext}"


def put_away(name) -> bool:
    """Is this a private chat's file while the vault is locked? Then the app does not list or
    play it, as it does not show that chat (Krem 2026-11-26: clips stay plain files, this is
    only the app's own pages). No vault, or an open one, puts nothing away. Cannot tell = yes."""
    if not Path(str(name or '')).stem.endswith(PRIVATE_MARK):
        return False
    try:
        from core import prompt_vault
        status = prompt_vault.vault_status()
        return bool(status.get('exists')) and not status.get('unlocked')
    except Exception:
        return True


PRIVATE_WORDS = ("This chat is private, but the file is not encrypted with it: it is an ordinary file in "
                 "user/comfyui/outputs that anyone at this computer can open. Sapphire's own pages hide it "
                 "while the vault is locked. Say so if the user may not know.")


def kept(name):
    """The path of a kept clip by its file name, or None. Names only, never paths."""
    n = str(name or '')
    if not re.fullmatch(r'[A-Za-z0-9_-][A-Za-z0-9_.-]*', n) or '..' in n:
        return None
    p = OUTPUT_DIR / n
    return p if p.is_file() else None


def backed_up(settings):
    """Whether the kept files go into Sapphire's backups: the plugin's own switch, off unless
    someone turned it on (core/backup.py reads the same setting; one clip can outweigh the rest
    of a backup)."""
    return bool((settings or {}).get('backup_outputs'))


def backup_words(settings):
    """One line for her about a file that was just kept: is it in the backups or not."""
    if backed_up(settings):
        return "It is kept in user/comfyui/outputs and goes into Sapphire's backups."
    return ("It is kept in user/comfyui/outputs on this computer only: ComfyUI's files are left out of "
            f"Sapphire's backups, and a restore does not bring them back. The user can change that in {PANEL}.")


def _clips():
    """The kept files; a dot-file is one still being written."""
    return [p for p in OUTPUT_DIR.iterdir() if p.is_file() and not p.name.startswith('.')] if OUTPUT_DIR.is_dir() else []


def saved():
    """(count, bytes) of the clips kept so far."""
    files = _clips()
    return len(files), sum(p.stat().st_size for p in files)


def clear_saved():
    """Delete the kept clips. Returns (deleted, left): one a player still holds open stays (Windows)."""
    deleted = left = 0
    for p in _clips():
        try:
            p.unlink(missing_ok=True)
            deleted += 1
        except OSError:
            left += 1
    for p in OUTPUT_DIR.glob('.*.tmp') if OUTPUT_DIR.is_dir() else []:      # a download a restart cut short
        try:
            if time.time() - p.stat().st_mtime > 3600:
                p.unlink(missing_ok=True)
        except OSError:
            pass
    return deleted, left


def measure(path):
    """(width, height) of a kept picture or clip, None when it can't be told (audio, no ffmpeg)."""
    from PIL import Image
    try:
        frames = stills(path, 1)[0]
        return Image.open(io.BytesIO(frames[0]), formats=SAFE_FORMATS).size if frames else None
    except Exception:
        return None


def recent(n=24):
    """The newest kept files, newest first: [{name, kind}]."""
    files = sorted((p for p in _clips() if not put_away(p.name)), key=lambda p: p.stat().st_mtime, reverse=True)[:n]
    kind_of = lambda ext: 'video' if ext in VIDEO_EXT else 'audio' if ext in AUDIO_EXT else 'image' if ext in IMAGE_EXT | {'.gif'} else 'file'
    return [{'name': p.name, 'kind': kind_of(p.suffix.lower())} for p in files]


def has_ffmpeg():
    return bool(shutil.which('ffmpeg'))


def stills(path, n=3):
    """(frames as JPEG bytes spread start to end, seconds) of a clip, so she can see what was
    made. Video needs ffmpeg; without ffprobe (no length) it is the first frame alone.
    An animated image gives its first frame."""
    path = Path(path)
    if path.suffix.lower() not in VIDEO_EXT:
        try:
            from PIL import Image
            buf = io.BytesIO()
            Image.open(path, formats=SAFE_FORMATS).convert('RGB').save(buf, 'JPEG', quality=88)
            return [buf.getvalue()], None
        except Exception:
            return [], None
    ff, probe = shutil.which('ffmpeg'), shutil.which('ffprobe')
    if not ff:
        return [], None
    quiet = {'capture_output': True, 'creationflags': getattr(subprocess, 'CREATE_NO_WINDOW', 0)}
    local = ['-protocol_whitelist', 'file']        # the clip came from another machine: it may name no other source
    seconds = 0.0
    try:
        if probe:
            got = subprocess.run([probe, '-v', 'error', *local, '-show_entries', 'format=duration', '-of', 'csv=p=0',
                                  str(path)], text=True, encoding='utf-8', errors='replace', timeout=30, **quiet)
            try:
                seconds = float(got.stdout.strip())
            except ValueError:
                seconds = 0.0                      # 'N/A' on a stream with no length written
        seconds = seconds if _num(seconds) and seconds > 0 else 0.0
        frames = []
        for i in range(n if seconds else 1):
            at = min(seconds * i / max(1, n - 1), max(0.0, seconds - 0.15))
            shot = subprocess.run([ff, '-v', 'error', *local, '-ss', f'{at:.2f}', '-i', str(path), '-frames:v', '1',
                                   '-f', 'image2pipe', '-vcodec', 'mjpeg', '-q:v', '3', '-'], timeout=60, **quiet)
            if shot.stdout:
                frames.append(shot.stdout)
        return frames, seconds or None
    except Exception as e:
        logger.warning(f"[COMFY] no frames from {path.name}: {type(e).__name__}")
        return [], None
