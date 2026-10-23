# plugins/mindpalace/routes/scopes.py
# Scope CRUD for the sidebar memory dropdown (manifest scope decl points here).
# Response shapes mirror the classic /api/memory/scopes contract.

import re

_NAME_RE = re.compile(r'[^a-z0-9_-]+')


def _sanitize(raw):
    s = _NAME_RE.sub('', (raw or '').strip().lower().replace(' ', '-'))
    return s[:40]


def _tools():
    # Late import — palace_tools is installed in sys.modules by
    # register_plugin_tools before routes register (load order guarantee).
    from plugins.mindpalace.tools import palace_tools
    return palace_tools


def list_scopes(**_):
    return {"scopes": _tools().get_scopes()}


def _publish_scope_changed(action, name):
    """Chat sidebar dropdowns listen for this — without it, palace-created
    scopes were invisible until reload (classic routes always published)."""
    try:
        from core.event_bus import publish, Events
        publish(Events.SCOPE_CHANGED, {"kind": "memory", "action": action,
                                       "name": name})
    except Exception:
        pass


def create_scope(body=None, **_):
    name = _sanitize((body or {}).get('name'))
    if not name:
        return {"success": False, "detail": "Invalid scope name."}
    ok = _tools().create_scope(name)
    if ok:
        _publish_scope_changed("created", name)
    return {"success": bool(ok), "name": name}


def delete_scope(name=None, body=None, query=None, **_):
    # Server-side confirm re-check (scout find, 2026-07-19): this deletes
    # MORE than the typed-confirm-gated wipe_scope (chunks + entities +
    # edges + ledger + library docs + source files) — the classic contract
    # requires the token and the frontend already sends it; the handler
    # just never checked.
    confirm = ((body or {}).get('confirm') or (query or {}).get('confirm'))
    if confirm != 'DELETE':
        return {"success": False,
                "detail": "Confirmation required — send confirm: 'DELETE'."}
    result = _tools().delete_scope(name or '')
    if "error" in result:
        return {"success": False, "detail": result["error"]}
    _publish_scope_changed("deleted", name or '')
    return {"success": True, "deleted_count": result.get("deleted_count", 0)}
