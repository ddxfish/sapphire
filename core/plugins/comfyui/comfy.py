"""ComfyUI for Sapphire: the workflow files, the wiring of a graph, and the HTTP client.

A workflow is ComfyUI's API-format graph: {node id: {class_type, inputs}}. Each lives in
user/comfyui/workflows/<name>.json. A `_sapphire` key in the same file carries what Sapphire
knows about it (a description, a picked prompt node), so one file moves between machines
whole. ComfyUI never sees that key: read() hands back nodes only.

The wiring rules, stated once:
- Only the live graph counts: the nodes that feed a save or preview node. ComfyUI runs nothing else.
- The prompt goes where the main sampler's `positive` link leads, through guiders, nodes that
  pass conditioning along, and string nodes, to where the text is typed. A sampler that works
  on pixels (a face detailer, an upscaler) keeps its own prompt.
- Every seed is set, also one typed in a number node and wired in.
- The size is set at the root of the sampler's latent chain, and only when nothing upscales on the way.
- Her input image goes into the image loader that is not a reference (a pose, a style, a mask).
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
from urllib.parse import urlsplit

from core.paths import PROJECT_ROOT

logger = logging.getLogger(__name__)

WORKFLOW_DIR = PROJECT_ROOT / 'user' / 'comfyui' / 'workflows'
OUTPUT_DIR = PROJECT_ROOT / 'user' / 'comfyui' / 'outputs'
DEFAULT_URL = 'http://127.0.0.1:8188'
META = '_sapphire'
MAX_UPLOAD = 32 * 1024 * 1024
MAX_STILL = 64 * 1024 * 1024
MAX_CLIP = 4 * 1024 ** 3
PANEL = "Settings > Plugins > ComfyUI"
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
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


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
    return {k: str(m[k]).strip()[:300] for k in ('description', 'prompt_node', 'image_node') if str(m.get(k) or '').strip()}


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
            todo += [src for _, src in _links(graph, nid)]
    # an output node we don't know by name: better the whole graph than a workflow with no sampler in it
    samples = lambda nodes: any(k == 'positive' or k in GUIDER_KEYS for n in nodes for k, _ in _links(graph, n))
    return seen if samples(seen) or not samples(graph) else set(graph)


def _typed(graph, nid):
    """The inputs of a node where prompt text is typed."""
    ins = graph[nid]['inputs']
    return [k for k in PROMPT_KEYS if isinstance(ins.get(k), str)]


def _leaves(graph, start, role):
    """Every node upstream of `start` where the prompt for `role` is typed, nearest first.
    A node that passes both sides along (a ControlNet, an image-to-video node) is followed
    on our side only; a text input wired from a string node is followed to that node."""
    other = 'negative' if role == 'positive' else 'positive'
    out, seen, todo = [], set(), [start]
    while todo:
        nid = todo.pop(0)
        if nid in seen or nid not in graph:
            continue
        seen.add(nid)
        links = _links(graph, nid)
        if _typed(graph, nid):
            out.append(nid)
            todo += [src for k, src in links if k in PROMPT_KEYS]      # a second piece wired in beside the typed one
        elif any(k == role for k, _ in links):
            todo += [src for k, src in links if k == role]
        else:
            todo += [src for k, src in links if k != other and k not in _NOT_TEXT]
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
    if want in graph and _typed(graph, want):
        return keyed([want]), []
    takers = _takers(graph, alive, 'positive')
    main = [t for t in takers if not _on_pixels(graph, t[0])] or takers
    picked, others = [], []
    for _, src in main:
        found = _leaves(graph, src, 'positive')
        if not found:
            continue
        best = _best(graph, found)
        picked += [best] if best not in picked else []
        others += [n for n in found if n != best and n not in others]
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


def _sized(node):
    return all(_num(node['inputs'].get(k)) for k in ('width', 'height'))


def size_plan(graph, alive=None):
    """(roots, staged): the nodes that set the picture's size at the start of a sampler's
    latent chain, and whether something upscales on the way (then the root is not the final size)."""
    alive = live(graph) if alive is None else alive
    roots, staged, seen = [], False, set()
    todo = [src for nid in order(graph) if nid in alive for k, src in _links(graph, nid) if k == 'latent_image']
    while todo:
        nid = todo.pop(0)
        if nid in seen:
            continue
        seen.add(nid)
        node, links = graph[nid], _links(graph, nid)
        latent_in = [src for k, src in links if k in LATENT_IN]
        if _UPSCALE.search(node['class_type']) or (_sized(node) and latent_in):
            staged = True
        if _sized(node) and not latent_in:
            roots.append(nid)                       # the size is typed here; what feeds it is resized to fit
        else:
            todo += [src for k, src in links if k not in _NOT_SIZE]
    return roots, staged


def size_nodes(graph):
    return size_plan(graph)[0]


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


def image_plan(graph, meta=None, alive=None):
    """(target, others): the loader her input image goes into, and the other loaders that
    could have been meant. (None, []) when the workflow takes no input image."""
    nodes, want = image_nodes(graph, alive), str((meta or {}).get('image_node') or '')
    if want in nodes:
        return want, []
    users = _users(graph)
    mine = [n for n in nodes if not _is_reference(graph, n, users)]
    if len(mine) > 1:                              # still several: a title that calls itself a reference steps back
        mine = [n for n in mine if not _REF_TITLE.search(title(graph, n))] or mine
    return (mine[0], mine[1:]) if mine else (None, [])


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
    roots, staged = size_plan(graph, alive)
    named = lambda ids: [{'id': n, 'title': title(graph, n)} for n in ids]
    size = None
    if len(roots) == 1 and not staged:
        i = graph[roots[0]]['inputs']
        size = [int(i['width']), int(i['height'])]
    target = image_plan(graph, meta, alive)[0]
    return {'kind': kind(graph), 'nodes': len(graph), 'size': size, 'seeds': len(seed_inputs(graph, alive)),
            'description': meta.get('description', ''),
            'prompt_node': targets[0][0] if targets else '', 'prompt_nodes': named(text_nodes(graph, alive)),
            'takes_image': target is not None,
            'image_node': target or '', 'image_nodes': named(image_nodes(graph, alive))}


def _dim(v):
    return max(64, min(int(v), 8192)) // 16 * 16


def apply(graph, meta, prompt, seed, width=None, height=None, image=None):
    """A copy of the graph with this call written in. Returns (graph, notes); a note is
    something the call asked for that the workflow has no place for, or a choice that was made."""
    g, notes = copy.deepcopy(graph), []
    alive = live(g)
    quoted = lambda nodes: ', '.join(f"'{title(g, n)}'" for n in nodes[:3])
    targets, others = prompt_plan(g, meta, alive)
    loader, other_loaders = image_plan(g, meta, alive)
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
    if width or height:
        roots, staged = size_plan(g, alive)
        if len(roots) == 1 and not staged:
            i = g[roots[0]]['inputs']
            i['width'], i['height'] = _dim(width or i['width']), _dim(height or i['height'])
            notes.append(f"Size {i['width']}x{i['height']}.")
        else:
            why = ('upscales on the way' if staged and roots else
                   'has more than one size input' if roots else 'has no size input')
            notes.append(f"This workflow {why}, so it kept its own size.")
    if image and loader:
        g[loader]['inputs']['image'] = image
        if other_loaders:
            notes.append(f"The input image went into '{title(g, loader)}'. This workflow also loads {quoted(other_loaders)}. "
                         f"A person can pick the node in {PANEL}.")
    elif loader:
        notes.append(f"This workflow works on a picture and none was passed, so it used its own "
                     f"('{_clip(g[loader]['inputs']['image'], 60)}'). Pass one with the image argument.")
    return g, notes


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

    def upload(self, raw):
        """An input image into ComfyUI's input folder; returns the name a LoadImage node takes."""
        from PIL import Image, ImageOps
        try:
            img = ImageOps.exif_transpose(Image.open(io.BytesIO(raw), formats=SAFE_FORMATS))
            buf = io.BytesIO()
            (img if img.mode in ('RGB', 'RGBA', 'L') else img.convert('RGBA')).save(buf, 'PNG')
        except Exception as e:
            raise ComfyError(f"That isn't an image ComfyUI can take ({_clip(e, 80)}).") from None
        data = buf.getvalue()
        name = f"sapphire-{hashlib.sha1(data).hexdigest()[:12]}.png"       # same image, same file, no churn
        r = self._call('post', '/upload/image', files={'image': (name, data, 'image/png')},
                       data={'overwrite': 'true'}, timeout=120)
        j = self._json(r) if r.status_code == 200 else None
        if not isinstance(j, dict) or not isinstance(j.get('name'), str) or not j['name']:
            raise ComfyError(f"ComfyUI wouldn't take the input image (HTTP {r.status_code}).")
        return (f"{j['subfolder']}/" if isinstance(j.get('subfolder'), str) and j['subfolder'] else '') + j['name']

    def queue(self, graph):
        r = self._call('post', '/prompt', json={'prompt': graph, 'client_id': self.cid}, timeout=60)
        j = self._json(r)
        if r.status_code == 200 and isinstance(j, dict) and j.get('prompt_id'):
            pid = str(j['prompt_id'])
            if j.get('node_errors'):               # it took the job but dropped a broken branch: not the run that was asked for
                self.cancel(pid)
                raise Rejected(j)
            return pid
        if r.status_code == 400 and isinstance(j, dict):
            raise Rejected(j)
        raise ComfyError(f"ComfyUI at {self.shown} wouldn't take the job (HTTP {r.status_code}).")

    def run(self, graph, timeout):
        """Queue and wait. A refusal that is only a name that differs by machine is corrected
        and sent again. Returns (outputs, fixed)."""
        fixed, pid = [], None
        for last in (False, False, True):
            try:
                pid = self.queue(graph)
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

    def _queued(self, pid):
        """'running', 'pending' or None. Any doubt reads as still there."""
        try:
            j = self.http.get(self.url + '/queue', timeout=15).json()
            for state, key in (('running', 'queue_running'), ('pending', 'queue_pending')):
                if any(isinstance(it, list) and len(it) > 1 and str(it[1]) == pid for it in j.get(key) or []):
                    return state
            return None if isinstance(j.get('queue_running'), list) and isinstance(j.get('queue_pending'), list) else 'pending'
        except Exception:
            return 'pending'

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
        t0, fails, gone, n = _now(), 0, 0, 0
        while _now() - t0 < timeout:
            _sleep(poll)
            n += 1
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
            os.replace(tmp, dest)
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


def keep(name, ext):
    """A fresh path under user/comfyui/outputs for a file a run made. Its extension is one a
    browser plays or shows as a picture; anything else is kept as .bin (never a page)."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ext = ext.lower() if str(ext or '').lower() in KEPT_EXT else '.bin'
    return OUTPUT_DIR / f"{slug(name)}-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}{ext}"


def kept(name):
    """The path of a kept clip by its file name, or None. Names only, never paths."""
    n = str(name or '')
    if not re.fullmatch(r'[A-Za-z0-9_-][A-Za-z0-9_.-]*', n) or '..' in n:
        return None
    p = OUTPUT_DIR / n
    return p if p.is_file() else None


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
    return deleted, left


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
