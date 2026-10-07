# core/agents/registry.py - agent kind registry (tmp/agents-v2.md §3.2)
#
# Plugins declare agent kinds in their manifest (capabilities.agents); the
# loader registers them here. The engine (core/agents/engine.py) consumes this
# registry. A kind's code stays in its plugin. The app runs fine with nothing
# registered; this module is a dumb, dependency-free holder, cloned from
# core/devices/registry.py.
#
# A kind spec:
#   kind            slug, [a-z0-9][a-z0-9_-]{0,32} - unique across plugins
#   label           display name
#   icon            emoji
#   description     one line for the tool description (what this kind is for)
#   module          plugin-relative path to the kind module (a .py file) that
#                   defines class Agent(core.agents.base.Agent)
#   cloud           True (the DEFAULT when absent) = its work leaves the machine;
#                   refused from a private chat. A plugin that writes false
#                   promises the kind honors privacy_required on every network call.
#   conversational  True = stays alive between turns and takes `say`
#   spawn_schema    list of field dicts (plugin-settings field shape) the kind
#                   ADDS to the common options (mission, name, model, context)
#   names           display names its agents take, in turn

import json
import logging
import re
import threading
from pathlib import PurePosixPath

logger = logging.getLogger(__name__)

MAX_FIELDS = 24
CORE = 'core'                     # the owner name of a kind that ships inside core
CORE_KINDS = ()                   # none today; the LLM kind lives in plugins/agents
_ID_RE = re.compile(r'[a-z0-9][a-z0-9_-]{0,32}$')
COMMON_FIELDS = ('mission', 'name', 'model', 'context')   # every kind takes these; a schema may not redefine them

_lock = threading.Lock()
_kinds = {}         # kind id -> spec dict + plugin_name
_generation = 0     # bumped on every change - consumers cache-invalidate on it


def _clean_module(path):
    """A plugin-relative .py path, or '' when it could leave the plugin dir."""
    path = str(path or '').strip().replace('\\', '/')
    p = PurePosixPath(path)
    if not path.endswith('.py') or p.is_absolute() or '..' in p.parts:
        return ''
    return str(p)


def _clean_schema(schema):
    """Field dicts with a string key, JSON-safe, capped, none of the common
    names. None = reject."""
    if schema is None:
        return []
    if not isinstance(schema, list) or len(schema) > MAX_FIELDS:
        return None
    fields = json.loads(json.dumps(schema))   # raises on anything not JSON-safe
    for f in fields:
        if not isinstance(f, dict) or not isinstance(f.get('key'), str) or not f['key']:
            return None
        if f['key'] in COMMON_FIELDS:
            return None
    return fields


def register_kind(kind_id, spec, plugin_name, builtin=False):
    """Register one agent kind. Returns True on accept. Never raises - a bad
    declaration must not break plugin load."""
    global _generation
    try:
        kind_id = str(kind_id or '').strip().lower()
        if not _ID_RE.fullmatch(kind_id):
            logger.warning(f"[AGENTS] '{plugin_name}': kind id '{kind_id}' invalid - skipped")
            return False
        if builtin:
            plugin_name = CORE
            spec = dict(spec, module=kind_id + '.py')
        elif plugin_name == CORE or kind_id in CORE_KINDS:
            logger.warning(f"[AGENTS] '{plugin_name}': kind id '{kind_id}' belongs to core - refused")
            return False
        module = _clean_module(spec.get('module'))
        if not module:
            logger.warning(f"[AGENTS] '{plugin_name}': kind '{kind_id}' needs a plugin-relative .py module - skipped")
            return False
        schema = _clean_schema(spec.get('spawn_schema'))
        if schema is None:
            logger.warning(f"[AGENTS] '{plugin_name}': kind '{kind_id}' has an invalid spawn_schema - skipped")
            return False
        names = [str(n)[:24] for n in (spec.get('names') or []) if str(n).strip()][:12]
        with _lock:
            owner = _kinds.get(kind_id, {}).get('plugin_name')
            if owner and owner != plugin_name:
                logger.warning(f"[AGENTS] kind '{kind_id}' already registered by '{owner}' - '{plugin_name}' refused")
                return False
            _kinds[kind_id] = {
                'kind': kind_id,
                'label': str(spec.get('label') or kind_id.replace('_', ' ').title())[:80],
                'icon': str(spec.get('icon') or '')[:16],
                'description': ' '.join(str(spec.get('description') or '').split())[:200],
                'module': module,
                # absent = cloud: a forgotten flag over-blocks, never leaks
                'cloud': spec.get('cloud') is not False,
                'conversational': spec.get('conversational') is True,
                'spawn_schema': schema,
                'names': names,
                'plugin_name': plugin_name,
            }
            _generation += 1
        logger.info(f"[AGENTS] Kind registered: '{kind_id}' from '{plugin_name}'")
        return True
    except Exception as e:
        logger.warning(f"[AGENTS] register_kind('{kind_id}') failed: {e}")
        return False


def unregister_plugin(plugin_name):
    """Drop all kinds a plugin registered (unload/disable path). Agent rows
    survive in the engine - they go dark until the plugin comes back."""
    global _generation
    with _lock:
        gone = [k for k, v in _kinds.items() if v.get('plugin_name') == plugin_name]
        for k in gone:
            _kinds.pop(k, None)
        if gone:
            _generation += 1
    if gone:
        logger.info(f"[AGENTS] Unregistered {len(gone)} kind(s) from '{plugin_name}': {gone}")
    return gone


def list_kinds():
    """Snapshot of all registered kinds, stable order (label, id)."""
    with _lock:
        kinds = [json.loads(json.dumps(v)) for v in _kinds.values()]
    kinds.sort(key=lambda d: (d.get('label', ''), d.get('kind', '')))
    return kinds


def has(kind_id):
    with _lock:
        return str(kind_id or '').strip().lower() in _kinds


def get_kind(kind_id):
    """One kind's spec (a copy), or None."""
    with _lock:
        spec = _kinds.get(str(kind_id or '').strip().lower())
        return json.loads(json.dumps(spec)) if spec else None


def generation():
    with _lock:
        return _generation
