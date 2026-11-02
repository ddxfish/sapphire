# core/plugins/social/registry.py - Social provider registry (tmp/social-media-plan.md)
#
# Plugins declare providers in their manifest (capabilities.social); scan.py
# registers them here, this plugin's own five included. engine.py consumes the
# registry. The provider code stays in its plugin. Dumb, dependency-free holder,
# cloned from core/devices/registry.py.
#
# A provider spec:
#   provider          slug, [a-z0-9][a-z0-9_-]{0,32} - unique across plugins
#   label, icon       for the Add connection picker
#   module            plugin-relative path to the provider module (a .py file)
#   auth              'oauth' (Connect button after Save) or 'fields' (the form is all)
#   capabilities      subset of CAPABILITIES: what the module exports
#   config_schema     plugin-settings field dicts; "secret": true → core's
#                     credentials manager, never plugin state
#   max_chars         post length the platform allows
#   max_images        images per post (0 = none)
#   public_image_url  true = the platform fetches images by URL (Instagram):
#                     the engine publishes bytes to the drop folder first
#   notes             one line for the card

import json
import logging
import re
import threading
from pathlib import PurePosixPath

logger = logging.getLogger(__name__)

MAX_FIELDS = 40
CAPABILITIES = ('post', 'image', 'link', 'inbox', 'reply', 'delete', 'stats')
AUTH = ('oauth', 'fields')
_ID_RE = re.compile(r'[a-z0-9][a-z0-9_-]{0,32}$')

_lock = threading.Lock()
_providers = {}     # provider id -> spec dict + plugin_name
_generation = 0     # bumped on every change - the engine drops its module cache on it


def _clean_module(path):
    path = str(path or '').strip().replace('\\', '/')
    p = PurePosixPath(path)
    if not path.endswith('.py') or p.is_absolute() or '..' in p.parts:
        return ''
    return str(p)


def _clean_schema(schema):
    if schema is None:
        return []
    if not isinstance(schema, list) or len(schema) > MAX_FIELDS:
        return None
    fields = json.loads(json.dumps(schema))
    for f in fields:
        if not isinstance(f, dict) or not isinstance(f.get('key'), str) or not f['key']:
            return None
    return fields


def _int(v, default, lo, hi):
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return default


def register_provider(provider_id, spec, plugin_name):
    """Register one provider. True on accept. Never raises - a bad
    declaration must not break a plugin's load. Re-registering an identical
    spec from the same plugin is a no-op (no generation bump)."""
    global _generation
    try:
        provider_id = str(provider_id or '').strip().lower()
        if not _ID_RE.fullmatch(provider_id):
            logger.warning(f"[SOCIAL] '{plugin_name}': provider id '{provider_id}' invalid - skipped")
            return False
        spec = spec if isinstance(spec, dict) else {}
        module = _clean_module(spec.get('module'))
        if not module:
            logger.warning(f"[SOCIAL] '{plugin_name}': provider '{provider_id}' needs a plugin-relative .py module - skipped")
            return False
        auth = str(spec.get('auth') or 'fields').strip().lower()
        if auth not in AUTH:
            logger.warning(f"[SOCIAL] '{plugin_name}': provider '{provider_id}' auth must be one of {AUTH} - skipped")
            return False
        caps = [str(c).strip().lower() for c in (spec.get('capabilities') or [])]
        if not caps or any(c not in CAPABILITIES for c in caps):
            logger.warning(f"[SOCIAL] '{plugin_name}': provider '{provider_id}' capabilities {caps} must be from {CAPABILITIES} - skipped")
            return False
        schema = _clean_schema(spec.get('config_schema'))
        if schema is None:
            logger.warning(f"[SOCIAL] '{plugin_name}': provider '{provider_id}' has an invalid config_schema - skipped")
            return False
        entry = {
            'provider': provider_id,
            'label': str(spec.get('label') or provider_id.replace('-', ' ').title())[:80],
            'icon': str(spec.get('icon') or '')[:16],
            'module': module,
            'auth': auth,
            'capabilities': caps,
            'config_schema': schema,
            'max_chars': _int(spec.get('max_chars'), 2000, 1, 100000),
            'max_images': _int(spec.get('max_images'), 1, 0, 10),
            'public_image_url': spec.get('public_image_url') is True,
            'notes': str(spec.get('notes') or '')[:300],
            'plugin_name': str(plugin_name),
        }
        with _lock:
            owner = _providers.get(provider_id, {}).get('plugin_name')
            if owner and owner != plugin_name:
                logger.warning(f"[SOCIAL] provider '{provider_id}' already registered by '{owner}' - '{plugin_name}' refused")
                return False
            if _providers.get(provider_id) == entry:
                return True
            _providers[provider_id] = entry
            _generation += 1
        logger.info(f"[SOCIAL] Provider registered: '{provider_id}' from '{plugin_name}'")
        return True
    except Exception as e:
        logger.warning(f"[SOCIAL] register_provider('{provider_id}') failed: {e}")
        return False


def unregister_plugin(plugin_name):
    """Drop every provider a plugin registered. Connections that use them
    stay in the store and go dark until the plugin comes back."""
    global _generation
    with _lock:
        gone = [p for p, v in _providers.items() if v.get('plugin_name') == plugin_name]
        for p in gone:
            _providers.pop(p, None)
        if gone:
            _generation += 1
    if gone:
        logger.info(f"[SOCIAL] Unregistered {len(gone)} provider(s) from '{plugin_name}': {gone}")
    return gone


def list_providers():
    with _lock:
        out = [json.loads(json.dumps(v)) for v in _providers.values()]
    out.sort(key=lambda p: (p['label'], p['provider']))
    return out


def get_provider(provider_id):
    with _lock:
        spec = _providers.get(str(provider_id or '').strip().lower())
        return json.loads(json.dumps(spec)) if spec else None


def has(provider_id):
    with _lock:
        return str(provider_id or '').strip().lower() in _providers


def generation():
    with _lock:
        return _generation


def reset():
    """Forget everything (this plugin unloading, or a test)."""
    global _generation
    with _lock:
        _providers.clear()
        _generation += 1
