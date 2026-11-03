# core/plugins/social/routes/oauth.py - the one OAuth round trip for every provider
#
# GET connect/{provider}/{profile}/{conn}: the provider's auth_start() gives
# the vendor URL (and may give a stash: a PKCE verifier) - the browser goes
# there. GET callback/{provider}: the vendor comes back with code+state; the
# provider's auth_finish() trades the code for tokens and says what to keep.
# The redirect URI every vendor console must know is
#     https://<this host>/api/plugin/social/callback/<provider>
# (the card shows it). State lives in plugin_state/social-csrf.json for TTL
# seconds; the load-mutate-save cycle is locked (two Connect clicks must not
# clobber each other - the calendar plugin's day-ruiner finding).
import json
import logging
import os
import secrets
import threading
import time
from urllib.parse import quote

from fastapi.responses import RedirectResponse

from core.paths import PLUGIN_STATE_DIR
from core.redact import describe, is_network, scrub
from plugins.social import engine, store
from plugins.social.errors import SocialError

logger = logging.getLogger(__name__)

CSRF_PATH = PLUGIN_STATE_DIR / 'social-csrf.json'
TTL = 600
_csrf_lock = threading.Lock()


def _load_csrf():
    if CSRF_PATH.exists():
        try:
            return json.loads(CSRF_PATH.read_text(encoding='utf-8'))
        except json.JSONDecodeError:
            logger.warning(f"[SOCIAL] CSRF file corrupt at {CSRF_PATH}; starting empty")
    return {}


def _save_csrf(data):
    CSRF_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CSRF_PATH.with_suffix(f'.json.tmp.{os.getpid()}')
    try:
        tmp.write_text(json.dumps(data), encoding='utf-8')
        tmp.replace(CSRF_PATH)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except Exception:
                pass


def redirect_uri(request, provider):
    base = str(request.base_url).rstrip('/')
    return f"{base}/api/plugin/social/callback/{provider}"


def _remember(ok, key, message):
    """The card shows the last Connect outcome after the redirect lands."""
    try:
        store._state().save('last_connect', {'ok': ok, 'connection': key, 'message': message, 'ts': time.time()})
    except Exception:
        pass


def _back(key, message):
    message = scrub(message)          # it lands in the log, plugin_state and the browser's history
    _remember(False, key, message)
    logger.warning(f"[SOCIAL] connect {key}: {message}")
    return RedirectResponse(url=f"/#settings?social_error={quote(message)}", status_code=302)


def connect(provider, profile, conn, request=None, **_):
    c = store.get_connection(store.clean_name(profile), store.clean_name(conn))
    if not c or c.provider != provider:
        return {'error': 'No such connection.'}, 404
    try:
        mod, spec = engine.module_for(c)
    except SocialError as e:
        return {'error': str(e)}, 400
    if spec['auth'] != 'oauth' or not callable(getattr(mod, 'auth_start', None)):
        return {'error': f"{spec['label']} does not use Connect."}, 400
    state = secrets.token_urlsafe(32)
    try:
        started = mod.auth_start(c, redirect_uri(request, provider), state)
    except SocialError as e:
        return {'error': str(e)}, 400
    except Exception as e:
        why = describe(e, c.secrets.values())
        logger.error(f"[SOCIAL] auth_start {c.key}: {why}", exc_info=not is_network(e))
        return {'error': f"{spec['label']}: {why}"}, 500
    url, stash = (started, {}) if isinstance(started, str) else (started[0], dict(started[1] or {}))
    with _csrf_lock:
        now = time.time()
        csrf = {k: v for k, v in _load_csrf().items() if now - v.get('created', 0) < TTL}
        csrf[state] = {'profile': c.profile, 'conn': c.name, 'provider': provider, 'created': now, 'stash': stash}
        _save_csrf(csrf)
    return RedirectResponse(url=url, status_code=302)


def callback(provider, request=None, query=None, **_):
    q = dict(query or {})
    with _csrf_lock:
        csrf = _load_csrf()
        entry = csrf.pop(str(q.get('state') or ''), None)
        _save_csrf(csrf)
    if not entry or entry.get('provider') != provider or time.time() - entry.get('created', 0) > TTL:
        return _back(f'?/{provider}', 'State mismatch or expired - start Connect again.')
    key = f"{entry['profile']}/{entry['conn']}"
    c = store.get_connection(entry['profile'], entry['conn'])
    if not c:
        return _back(key, 'That connection no longer exists.')
    err = q.get('error_description') or q.get('error_reason') or q.get('error')
    if err:
        return _back(key, f"{provider}: {err}")
    try:
        mod, spec = engine.module_for(c)
        got = mod.auth_finish(c, redirect_uri(request, provider), q, entry.get('stash') or {}) or {}
    except SocialError as e:
        return _back(key, str(e))
    except Exception as e:
        why = describe(e, c.secrets.values())
        logger.error(f"[SOCIAL] auth_finish {key}: {why}", exc_info=not is_network(e))
        return _back(key, f"{provider}: {why}")
    if got.get('secrets'):
        if not c.save_secrets(got['secrets']):
            return _back(key, 'The tokens could not be stored.')
    meta = dict(got.get('meta') or {})
    meta.update(connected=True, connected_at=time.time())
    c.save_meta(meta)
    _remember(True, key, f"Connected{(' as ' + meta['identity']) if meta.get('identity') else ''}.")
    try:
        from core.event_bus import publish, Events
        publish(Events.SCOPE_CHANGED, {'kind': 'social', 'action': 'connected', 'name': entry['profile']})
    except Exception:
        pass
    logger.info(f"[SOCIAL] connected {key} ({provider})")
    return RedirectResponse(url='/#settings', status_code=302)
