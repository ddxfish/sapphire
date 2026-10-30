# core/plugins/mindpalace/routes/import_routes.py
# The Import tab (2026-10-30): what a classic import would bring across,
# per store and scope, BEFORE it runs; the run itself in a background
# thread with a progress row in the notification center; a status poll;
# the removed-before set (rows the user deleted from the palace after an
# earlier import) listed apart, brought back only on an explicit ask, or
# scrubbed from the old database on a typed confirm — the one write to a
# classic database anywhere in the palace, and only ever the human's.
import logging

logger = logging.getLogger(__name__)


def _it():
    from plugins.mindpalace.tools import import_tools
    return import_tools


def _config():
    import config
    return config


def preview(query=None, **_):
    try:
        return _it().preview(_config())
    except Exception as e:
        logger.error(f"[MINDPALACE] import preview failed: {e}", exc_info=True)
        return {'error': f'preview failed: {e}'}, 500


def run(body=None, **_):
    b = body or {}
    what = (b.get('what') or 'all').strip().lower()
    if what not in ('all', *_it().STORES):
        return {'error': f"unknown store '{what}'"}, 400
    scope = (b.get('scope') or '').strip() or None
    include_removed = bool(b.get('include_removed'))
    if include_removed and (b.get('confirm') or '') != (scope or 'all'):
        return {'error': "bringing back removed rows needs confirm = the scope name "
                         "(or 'all')"}, 400
    msg, ok = _it().start(_config(), what=what, scope=scope,
                          include_removed=include_removed)
    if not ok:
        return {'error': msg}, 409
    return {'success': True, 'message': msg}


def status(query=None, **_):
    return _it().status()


def removed(query=None, **_):
    q = query or {}
    try:
        return {'rows': _it().removed_list(_config(), scope=(q.get('scope') or None),
                                           store=(q.get('store') or None))}
    except Exception as e:
        logger.error(f"[MINDPALACE] removed list failed: {e}", exc_info=True)
        return {'error': f'listing failed: {e}'}, 500


def scrub(body=None, **_):
    """Typed confirm: the scope name, or 'all' for every scope. Human-only
    by construction — there is no tool for this."""
    b = body or {}
    scope = (b.get('scope') or '').strip() or None
    if (b.get('confirm') or '') != (scope or 'all'):
        return {'error': "confirm must equal the scope name exactly (or 'all')"}, 400
    with_state = _it().status()
    if with_state.get('running'):
        return {'error': 'an import is running — scrub after it finishes'}, 409
    try:
        counts = _it().scrub_removed(_config(), scope=scope)
    except Exception as e:
        logger.error(f"[MINDPALACE] scrub failed: {e}", exc_info=True)
        return {'error': f'scrub failed: {e}'}, 500
    return {'success': True, **counts}
