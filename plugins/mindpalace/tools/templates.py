# plugins/mindpalace/tools/templates.py
# Entity kind templates. Defaults ship with the plugin (templates/*.json);
# user-defined kinds auto-load from user/mind_palace/templates/*.json and win
# on key collision (their palace, their rules). Template shape:
#   {kind, label, icon, order, fields: [{key, label, type: text|bool, default}]}
# Fields live on the entity row as meta.fields = {key: value}.

import json
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

_PLUGIN_DIR = Path(__file__).parent.parent
_DEFAULTS_DIR = _PLUGIN_DIR / 'templates'

_SLUG_RE = re.compile(r'[^a-z0-9_-]')

_cache = None
_cache_stamp = None


def _user_dir() -> Path:
    import config
    return Path(config.__file__).parent / 'user' / 'mind_palace' / 'templates'


def _dir_stamp(d: Path):
    try:
        return tuple(sorted((f.name, f.stat().st_mtime) for f in d.glob('*.json')))
    except Exception:
        return ()


def _load_file(path: Path):
    try:
        t = json.loads(path.read_text(encoding='utf-8'))
        kind = _SLUG_RE.sub('', str(t.get('kind', '')).strip().lower())
        if not kind:
            logger.warning(f"[MINDPALACE] Template {path.name}: missing/invalid kind, skipped")
            return None
        fields = []
        for f in t.get('fields', []):
            key = _SLUG_RE.sub('', str(f.get('key', '')).strip().lower())
            if not key:
                continue
            ftype = f.get('type') if f.get('type') in ('text', 'bool', 'textarea') else 'text'
            fields.append({'key': key, 'label': str(f.get('label') or key),
                           'type': ftype, 'default': f.get('default')})
        return {'kind': kind, 'label': str(t.get('label') or kind.title()),
                'icon': str(t.get('icon') or ''), 'order': int(t.get('order', 50)),
                'fields': fields}
    except Exception as e:
        logger.warning(f"[MINDPALACE] Template {path.name} unreadable: {e}")
        return None


def get_templates() -> dict:
    """{kind: template}, defaults + user dir merged (user wins). Cached,
    invalidated by file mtimes so dropped-in templates appear without a
    restart."""
    global _cache, _cache_stamp
    udir = _user_dir()
    stamp = (_dir_stamp(_DEFAULTS_DIR), _dir_stamp(udir))
    if _cache is not None and stamp == _cache_stamp:
        return _cache
    out = {}
    for d in (_DEFAULTS_DIR, udir):
        if not d.is_dir():
            continue
        for f in sorted(d.glob('*.json')):
            t = _load_file(f)
            if t:
                out[t['kind']] = t
    _cache, _cache_stamp = out, stamp
    return out


def valid_kinds() -> set:
    return set(get_templates().keys())
