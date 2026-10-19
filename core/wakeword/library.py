# core/wakeword/library.py - the wake word library: every model this Sapphire can listen for.
#
# Sapphire is the library; each thing that listens (this computer, every
# satellite) is a slot that holds one model at a time. Two families:
#   onnx    openWakeWord: this computer and a Pi body
#   tflite  microWakeWord: an ESP32 board
# Where the models live, first wins for a name and family:
#   user/wakeword/models/<name>.<family>   the user's: uploaded on Settings > Wakeword, or made by the Wakeword Maker
#   core/wakeword/models/<name>.<family>   shipped with Sapphire
#   openWakeWord's own names (alexa, hey_jarvis, ...): onnx, no file here
# One sidecar per name, <name>.json: keys at the top apply to both files, an
# `onnx` or `tflite` block to one. A dropped esphome manifest (type: micro,
# wake_word, micro: {probability_cutoff, sliding_window_size, feature_step_size})
# and the Wakeword Maker's own desktop sidecar read as they are.
import json
import logging
import os
import re
import threading
import time
from pathlib import Path

from core.paths import PROJECT_ROOT

logger = logging.getLogger(__name__)

USER_DIR = PROJECT_ROOT / 'user' / 'wakeword' / 'models'
CORE_DIR = Path(__file__).parent / 'models'
FAMILIES = {'onnx': 'openWakeWord', 'tflite': 'microWakeWord'}
BUILTIN = ['alexa', 'hey_mycroft', 'hey_jarvis', 'hey_rhasspy', 'timer', 'weather']   # openWakeWord's own
USUAL = {'onnx': 0.5, 'tflite': 0.97}          # what a model without a threshold of its own listens at
LIMITS = {'onnx': (4096, 8 * 1024 * 1024), 'tflite': (4096, 1536 * 1024)}   # the board's own limits for tflite
SIZES = {'onnx': '4 KB to 8 MB', 'tflite': '4 KB to 1.5 MB'}
NAME_RE = re.compile(r'[a-z0-9][a-z0-9_-]{0,47}')
DESKTOP = 'this computer'                      # this computer's slot in listeners(): a space, so no device id (engine.ID_RE) can ever be it
OWN = 'own'                                    # a board's built-in word (the model embedded in its program)
KEEP = 'keep'                                  # a board's wish never set here: leave it as it is
_lock = threading.RLock()                      # one writer at a time in the user's folder: add() and remove() read-merge-write the sidecar


class LibraryError(Exception):
    """A reason fit to show as it is."""


class Exists(LibraryError):
    pass


def slug(name):
    return re.sub(r'[^a-z0-9_-]+', '_', str(name or '').strip().lower()).strip('_-')[:48]


def family_of(filename):
    ext = Path(str(filename or '')).suffix.lower().lstrip('.')
    return ext if ext in FAMILIES else ''


def _read_json(path):
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write(path, data):
    """Whole file or nothing: a temp file beside the target, then one rename."""
    path = Path(path)
    tmp = path.with_name(f'{path.name}.{os.getpid()}.{threading.get_ident()}.tmp')
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _ratio(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if 0 < f < 1 else None


def _whole(v, lo, hi, default):
    try:
        n = int(v)
    except (TypeError, ValueError):
        return default
    return n if lo <= n <= hi else default


def normalize(meta, family):
    """One shape from any sidecar: {phrase, threshold, micro, made_by, run, label, added}. Keys at the top
    apply to both families unless the sidecar names one (`family`, `framework`, or esphome's `type: micro`
    = tflite); a block named for the family wins over them."""
    meta = meta if isinstance(meta, dict) else {}
    named = str(meta.get('family') or meta.get('framework') or ('tflite' if meta.get('type') == 'micro' else '')).lower()
    flat = meta if named in ('', family) else {}
    block = meta.get(family) if isinstance(meta.get(family), dict) else {}
    src = {**flat, **block}
    micro = src.get('micro') if isinstance(src.get('micro'), dict) else {}
    thr = _ratio(src.get('threshold'))
    if thr is None:
        thr = _ratio(micro.get('probability_cutoff'))
    out = {'phrase': str(meta.get('phrase') or meta.get('wake_word') or src.get('phrase') or '').strip()[:80],
           'threshold': thr if thr is not None else USUAL[family],
           'made_by': str(src.get('made_by') or meta.get('made_by') or meta.get('author') or '')[:40],
           'run': str(src.get('run') or '')[:60], 'label': str(src.get('label') or '')[:80],
           'added': src.get('added') or src.get('installed') or meta.get('added') or None}
    if family == 'tflite':
        def pick(*keys):
            for k in keys:
                if k in micro:
                    return micro[k]
            for k in keys:
                if k in src:
                    return src[k]
            return None
        out['micro'] = {'sliding_window': _whole(pick('sliding_window', 'sliding_window_size'), 1, 50, 5),
                        'step_ms': _whole(pick('step_ms', 'feature_step_size'), 5, 30, 10)}
        arena = _whole(pick('arena', 'tensor_arena_size'), 1, 10 ** 7, 0)
        if arena:
            out['micro']['arena'] = arena
    return out


def has_micro(meta):
    """True when a sidecar carries microWakeWord's numbers: a `micro` block (esphome, the Maker, ours), the
    firmware's own flat keys, or esphome's `type: micro`."""
    meta = meta if isinstance(meta, dict) else {}
    src = {**meta, **(meta.get('tflite') if isinstance(meta.get('tflite'), dict) else {})}
    return isinstance(src.get('micro'), dict) or meta.get('type') == 'micro' \
        or any(k in src for k in ('sliding_window', 'step_ms', 'sliding_window_size', 'feature_step_size'))


def _block(norm):
    """A sidecar block from a normalized shape: the keys worth keeping."""
    out = {'threshold': norm['threshold']}
    for k in ('made_by', 'run', 'label', 'added', 'micro'):
        if norm.get(k):
            out[k] = norm[k]
    return out


def _files(folder):
    """{(name, family): path} for the model files in a folder."""
    out = {}
    if not Path(folder).is_dir():
        return out
    for p in sorted(Path(folder).iterdir()):
        fam = family_of(p.name)
        if fam and p.is_file():
            out[(p.stem, fam)] = p
    return out


def _check(path, family, meta):
    """Why a file in the folder is not a usable model, or ''. The same gates as add(): a file dropped in by
    hand skips the door, not the rules."""
    size = path.stat().st_size
    lo, hi = LIMITS[family]
    if not lo <= size <= hi:
        return f"{size} bytes; a {FAMILIES[family]} model is {SIZES[family]}"
    if family == 'tflite':
        with open(path, 'rb') as f:
            head = f.read(8)
        if head[4:8] != b'TFL3':
            return 'not a TensorFlow Lite model (no TFL3 header)'
        if not has_micro(meta):
            return 'no .json manifest beside it with the board numbers'
    return ''


def _entry(name, family, path, source):
    meta = _read_json(path.with_name(f'{path.stem}.json'))
    norm = normalize(meta, family)
    why = _check(path, family, meta)
    return {'name': name, 'family': family, 'source': source, 'path': str(path), 'size': path.stat().st_size,
            **norm, 'phrase': norm['phrase'] or name.replace('_', ' '), 'ok': not why, 'why': why}


def entries():
    """Every model: the user's, then the shipped ones, then openWakeWord's own names.
    A user file hides a shipped one of the same name and family."""
    out, seen = [], set()
    for source, folder in (('user', USER_DIR), ('core', CORE_DIR)):
        for (name, fam), path in _files(folder).items():
            if (name, fam) in seen:
                continue
            seen.add((name, fam))
            try:
                out.append(_entry(name, fam, path, source))
            except OSError as e:
                logger.warning(f"[WAKEWORD] {path.name} could not be read: {e}")
    for name in BUILTIN:
        if (name, 'onnx') not in seen:
            out.append({'name': name, 'family': 'onnx', 'source': 'builtin', 'path': '', 'size': 0,
                        'phrase': name.replace('_', ' '), 'threshold': USUAL['onnx'], 'made_by': 'openWakeWord',
                        'run': '', 'label': '', 'added': None, 'ok': True, 'why': ''})
    return out


def _any(name, family):
    """The entry of that name and family, usable or not."""
    return next((e for e in entries() if e['name'] == name and e['family'] == family), None)


def get(name, family):
    """A usable model of that name and family, else None (a file that fails the gates is listed, never used)."""
    e = _any(name, family)
    return e if e and e['ok'] else None


def threshold_of(name, family='onnx'):
    """What this model listens at (its own, else the family's usual); None when the library has no such model."""
    e = get(str(name or ''), family)
    return float(e['threshold']) if e else None


def add(name, data, family, meta=None, overwrite=False, made_by='upload', phrase=''):
    """A model into the user's folder, with its sidecar: checked, written whole or not at all.
    `meta` is any sidecar shape (normalize). Raises Exists when a model of that name and family is
    there and overwrite is False. Returns the entry."""
    name = slug(name)
    if not name or not NAME_RE.fullmatch(name):
        raise LibraryError("A model's name is letters, digits, _ and -, 48 at most.")
    if family not in FAMILIES:
        raise LibraryError("A model is a .onnx (openWakeWord) or a .tflite (microWakeWord).")
    lo, hi = LIMITS[family]
    if not isinstance(data, (bytes, bytearray)) or not lo <= len(data) <= hi:
        raise LibraryError(f"A {FAMILIES[family]} model is {SIZES[family]}.")
    if family == 'tflite' and bytes(data[4:8]) != b'TFL3':
        raise LibraryError("Not a TensorFlow Lite model: no TFL3 header.")
    if family == 'tflite' and not has_micro(meta):
        # the board's cutoff, window and step live in the manifest beside every microWakeWord model (esphome's
        # collection, the Wakeword Maker's run); and an openWakeWord model is published as .tflite too, which a
        # board cannot run: without the manifest that says `micro`, a .tflite is refused rather than guessed at
        raise LibraryError("A satellite model comes with its .json manifest, the one beside it: "
                           "it holds the board's cutoff, window and step.")
    with _lock:
        return _add(name, data, family, meta, overwrite, made_by, phrase)


def _add(name, data, family, meta, overwrite, made_by, phrase):
    USER_DIR.mkdir(parents=True, exist_ok=True)
    target = USER_DIR / f'{name}.{family}'
    taken = _any(name, family)                  # a user file, one that ships with Sapphire, or openWakeWord's own name: all are "already there"
    if taken and not overwrite:
        where = {'core': ' (it ships with Sapphire; yours would come first)', 'builtin': " (openWakeWord's own; yours would come first)"}.get(taken['source'], '')
        raise Exists(f"The library already has a {FAMILIES[family]} model named {name}{where}.")
    side = USER_DIR / f'{name}.json'
    kept = _read_json(side)
    norm = normalize(meta, family)
    norm.update(made_by=made_by, added=time.time())
    new = {'name': name,
           'phrase': (str(phrase or '').strip() or norm['phrase'] or str(kept.get('phrase') or kept.get('wake_word') or '').strip()
                      or name.replace('_', ' '))[:80]}
    for fam in FAMILIES:                           # the other family's file keeps what it had, folded into its own block
        if fam != family and (USER_DIR / f'{name}.{fam}').exists():
            new[fam] = kept[fam] if isinstance(kept.get(fam), dict) else _block(normalize(kept, fam))
    new[family] = _block(norm)
    _write(side, json.dumps(new, indent=1).encode('utf-8'))     # the sidecar first: a model beside a stale block would be sent with the wrong numbers
    _write(target, bytes(data))
    logger.info(f"[WAKEWORD] library: {name}.{family} added by {made_by}")
    return get(name, family)


def remove(name, family):
    """A model out of the user's folder. Returns the listeners that were using it: they keep their copy
    until they are switched (a board holds its own)."""
    with _lock:
        return _remove(name, family)


def _remove(name, family):
    e = _any(str(name or ''), str(family or ''))
    if not e:
        raise LibraryError(f"The library has no {family} model named {name}.")
    if e['source'] != 'user':
        raise LibraryError(f"{name} comes with Sapphire and stays.")
    users = [l['id'] for l in listeners() if l.get('family') == family and l.get('model') == name]
    Path(e['path']).unlink(missing_ok=True)
    side = USER_DIR / f'{name}.json'
    others = [f for f in FAMILIES if f != family and (USER_DIR / f'{name}.{f}').exists()]
    kept = _read_json(side)
    if not others:
        side.unlink(missing_ok=True)
    elif family in kept:
        kept.pop(family)
        _write(side, json.dumps(kept, indent=1).encode('utf-8'))
    logger.info(f"[WAKEWORD] library: {name}.{family} removed" + (f" (in use by {', '.join(users)})" if users else ''))
    return users


def _named(wish):
    """A board's wish as a library name, or None (never set, or its own word)."""
    w = '' if wish is None else str(wish).strip()
    return None if w in ('', KEEP, OWN) else w


def listeners():
    """Who listens, and for what: this computer first, then every device with a wake word.
    model = what it listens for now, in its own words (None when a board has not said yet);
    wish = what it was set to here (a board that could not be told keeps the wish until it can)."""
    import config
    running = None                                 # the live detector: False when the model did not load (a null detector stands in)
    try:
        from core.api_fastapi import get_system
        det = getattr(get_system(), 'wake_detector', None)
        running = det is not None and type(det).__name__ != 'NullWakeWordDetector' and bool(getattr(det, 'running', True))
    except Exception:
        pass
    out = [{'id': DESKTOP, 'label': 'This computer', 'kind': 'desktop', 'location': '', 'family': 'onnx',
            'model': str(getattr(config, 'WAKEWORD_MODEL', '') or ''), 'wish': None, 'online': True,
            'enabled': bool(getattr(config, 'WAKE_WORD_ENABLED', False)), 'running': running,
            'threshold': getattr(config, 'WAKEWORD_THRESHOLD', None)}]
    try:
        from core.devices import engine
        if engine.refusal():
            return out
        from core.devices.drivers import satellite
        found = engine.statuses()
        for dev_id, row in engine.rows().items():
            if not row.get('enabled', True):
                continue
            part = next((p for p in row.get('parts', []) if p.get('driver') == 'satellite'), None)
            if not part:
                continue
            has = part.get('has')
            if isinstance(has, list) and 'wake' not in has:
                continue                           # a body without a wake word (a keyboard, a reader)
            said = satellite.wake_state(dev_id)
            out.append({'id': dev_id, 'label': row.get('label') or dev_id, 'kind': 'device',
                        'location': row.get('location', ''),
                        'family': said.get('format') or satellite.wake_format(dev_id, row=row),
                        'model': said.get('model') if said else None,
                        'wish': _named((part.get('config') or {}).get('wake_model')),
                        'online': bool((found.get(dev_id) or {}).get('online')), 'enabled': True})
    except Exception as e:
        logger.warning(f"[WAKEWORD] the devices that listen could not be listed: {e}")
    return out


def switch(listener, name):
    """Make a listener listen for `name` (a library model; '' or 'own' = a board's built-in word).
    This computer: the two settings and the live detector, as the Wakeword Maker's Install does.
    A device: its wake_model wish, told to the board now through its driver's apply (the board
    restarts on it); a board that cannot be reached keeps the wish. Returns what happened."""
    name = '' if name is None else str(name).strip()
    name = OWN if name in ('', OWN) else name
    if name == KEEP:
        raise LibraryError("Pick a wake word, or the board's own word.")
    if listener == DESKTOP:
        if name == OWN:
            raise LibraryError("This computer has no built-in word: pick a model.")
        e = get(name, 'onnx')
        if not e:
            raise LibraryError(f"The library has no usable desktop (openWakeWord) model named '{name}'.")
        from core.settings_manager import settings
        settings.set('WAKEWORD_MODEL', name, persist=True)        # the model first: a failure leaves the old one at its own threshold
        settings.set('WAKEWORD_THRESHOLD', float(e['threshold']), persist=True)
        try:
            from core.api_fastapi import get_system
            ok = get_system().reload_wakeword_model()
        except Exception as ex:
            raise LibraryError(f"The setting is {name}, but the live swap failed: {ex}")
        return {'listener': DESKTOP, 'model': name, 'threshold': e['threshold'], 'sent': bool(ok), 'had': False, 'warning': ''}
    from core.devices import engine
    row = engine.get(listener)
    part = next((p for p in row.get('parts', []) if p.get('driver') == 'satellite'), None)
    if not part:
        raise LibraryError(f"{listener} is not a satellite.")
    from core.devices.drivers import satellite
    family = satellite.wake_state(listener).get('format') or satellite.wake_format(listener, row=row)
    if name != OWN and not get(name, family):
        raise LibraryError(f"The library has no usable {FAMILIES[family]} model named '{name}', which is what {listener} runs.")
    row, _ = engine.update(listener, parts={'satellite': {'wake_model': name}}, poke=False)
    part = engine._part(row, 'satellite')
    warning, went = '', None
    if not row.get('enabled', True):
        warning = 'the device is turned off'
    else:
        try:                                        # an explicit switch: sent even under the same name (a retrained model lands)
            went = satellite.send_wake_model(dict(engine._brief(row), has=part.get('has'), takes=part.get('takes')),
                                             dict(part.get('config') or {}), engine._part_secrets(row['id'], 'satellite'), force=True)
        except engine.DeviceError as e:
            warning = str(e)
    engine.look_soon(row['id'])
    return {'listener': listener, 'model': name, 'threshold': threshold_of(name, family) if name != OWN else None,
            'sent': went == 'sent', 'had': went == 'has', 'warning': warning}   # had: those very bytes are on it, or went moments ago
