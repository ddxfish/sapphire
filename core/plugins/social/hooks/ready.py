# core/plugins/social/hooks/ready.py - re-read every loaded manifest for providers
#
# plugins_ready fires after the boot scan and after any reload, rescan or
# toggle-on. A provider plugin that came up after this one, or came back,
# is found here. Idempotent (scan.pull registers an identical spec as a no-op).
import logging

logger = logging.getLogger(__name__)


def plugins_ready(event):
    try:
        from core.plugin_loader import plugin_loader
        if not plugin_loader.switched_on('social'):
            return
        from plugins.social import scan
        n = scan.pull()
        logger.debug(f"[SOCIAL] plugins_ready: {n} provider declaration(s) seen")
    except Exception as e:
        logger.warning(f"[SOCIAL] plugins_ready scan failed: {e}")
    try:
        _scrub_saved()
    except Exception as e:
        logger.warning(f"[SOCIAL] scrubbing saved errors failed: {e}")


def _scrub_saved():
    """2.16.1 could save a network error whose text held a Meta token (its
    request URL): clean the last Connect outcome and the Outbox errors.
    Writes only when something changes."""
    from core.redact import scrub
    from plugins.social import store
    st = store._state()
    last = st.get('last_connect')
    if isinstance(last, dict) and isinstance(last.get('message'), str):
        clean = scrub(last['message'])
        if clean != last['message']:
            st.save('last_connect', {**last, 'message': clean})

    def dirty(r):
        err = (r.get('result') or {}).get('error')
        return isinstance(err, str) and scrub(err) != err
    if not any(dirty(r) for r in st.get('outbox', []) or []):
        return

    def fn(cur):
        out = []
        for r in cur or []:
            if dirty(r):
                r = {**r, 'result': {**r['result'], 'error': scrub(r['result']['error'])}}
            out.append(r)
        return out
    st.update_with_lock('outbox', fn, default=[])
    logger.info("[SOCIAL] scrubbed secrets from saved Outbox errors")
