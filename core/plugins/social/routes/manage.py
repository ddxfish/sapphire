# core/plugins/social/routes/manage.py - the card's doors: profiles, connections,
# providers, the Outbox, and the one-click daily round task.
#
# Plain `def` on purpose: core threadpools sync handlers; an async one would
# block the web loop on a platform call.
import logging
import re
import time

from plugins.social import engine, outbox, registry, store
from plugins.social.errors import SocialError

logger = logging.getLogger(__name__)

ROUND_PROMPT = (
    "Daily social round. 1) social_inbox: read what came in since yesterday; reply with social_reply "
    "where a reply is wanted, skip spam. 2) Find news worth sharing: what changed in your app today, "
    "new videos on your YouTube, new blog posts, anything on the sites you follow (web tools). "
    "3) Write one post per thing worth saying, in your own voice, and social_post it (an image from "
    "social_meme or an image tool when it helps; a link goes in `link`). If nothing happened, post "
    "nothing. 4) End with a short summary of what you read and what you posted or queued.")


def _scope_changed(action, name):
    try:
        from core.event_bus import publish, Events
        publish(Events.SCOPE_CHANGED, {'kind': 'social', 'action': action, 'name': name})
    except Exception:
        pass


def list_profiles(**_):
    st = store._state()
    return {'profiles': store.list_profiles(), 'pending': len(outbox.pending()),
            'last_connect': st.get('last_connect') or None}


def put_profile(profile, body=None, **_):
    ok, r = store.put_profile(profile, (body or {}).get('label'))
    if not ok:
        return {'success': False, 'detail': r}, 400
    _scope_changed('added', r)
    return {'success': True, 'name': r}


def delete_profile(profile, **_):
    name = store.clean_name(profile)
    if not store.delete_profile(name):
        return {'success': False, 'detail': 'No such profile.'}, 404
    _scope_changed('deleted', name)
    return {'success': True}


def put_connection(profile, conn, body=None, **_):
    b = body or {}
    ok, r = store.put_connection(profile, conn, b.get('provider'), label=b.get('label'),
                                 mode=b.get('mode'), disclose=b.get('disclose'),
                                 config=b.get('config') if isinstance(b.get('config'), dict) else {})
    if not ok:
        return {'success': False, 'detail': r}, 400
    c = store.get_connection(store.clean_name(profile), r)
    return {'success': True, 'connection': c.summary() if c else None}


def delete_connection(profile, conn, **_):
    if not store.delete_connection(profile, conn):
        return {'success': False, 'detail': 'No such connection.'}, 404
    return {'success': True}


def test_connection(profile, conn, **_):
    c = store.get_connection(store.clean_name(profile), store.clean_name(conn))
    if not c:
        return {'success': False, 'detail': 'No such connection.'}, 404
    try:
        mod, spec = engine.module_for(c)
        engine._ready(c, mod, spec)
        if callable(getattr(mod, 'validate', None)):
            ok, msg = mod.validate(c)
        else:
            ok, msg = True, 'ready'
    except SocialError as e:
        return {'success': False, 'detail': str(e)}
    except Exception as e:
        logger.error(f"[SOCIAL] test {c.key}: {e}", exc_info=True)
        return {'success': False, 'detail': f"{type(e).__name__}: {e}"}
    fresh = store.get_connection(c.profile, c.name)
    return {'success': bool(ok), 'detail': str(msg or ''), 'connection': fresh.summary() if fresh else None}


def list_providers(**_):
    return {'providers': registry.list_providers()}


def get_config(settings=None, **_):
    from core.plugin_loader import plugin_loader
    info = plugin_loader.get_plugin_info('social') or {}
    schema = ((info.get('manifest') or {}).get('capabilities') or {}).get('settings') or []
    return {'schema': schema, 'values': settings or {}}


def list_outbox(query=None, **_):
    status = (query or {}).get('status') or None
    rows = outbox.rows(status=status if status in outbox.STATUSES else None)
    for r in rows:
        r['image_count'] = len(r.get('images') or [])
        r.pop('images', None)
    return {'rows': rows[:100], 'pending': len(outbox.pending()),
            'last_connect': store._state().get('last_connect') or None}


def approve(rid, **_):
    ok, msg = engine.approve(str(rid))
    return {'success': ok, 'detail': msg, 'row': outbox.get(str(rid)), 'pending': len(outbox.pending())}


def reject(rid, **_):
    ok, msg = engine.reject(str(rid))
    return {'success': ok, 'detail': msg, 'pending': len(outbox.pending())}


def clear_outbox(**_):
    return {'success': True, 'removed': outbox.clear_done()}


def outbox_image(rid, n, **_):
    row = outbox.get(str(rid))
    p = outbox.image_path(row, n) if row else None
    if not p:
        return {'error': 'no such image'}, 404
    from fastapi.responses import FileResponse
    return FileResponse(str(p))


_HHMM = re.compile(r'^([01]?\d|2[0-3]):([0-5]\d)$')


def make_round(body=None, **_):
    """A Triggers task that runs ROUND_PROMPT daily as a persona with this
    profile as its social scope. The person edits it there like any task."""
    b = body or {}
    profile = store.clean_name(b.get('profile'))
    if not profile or profile not in store.profiles():
        return {'success': False, 'detail': 'Pick a profile.'}, 400
    m = _HHMM.match(str(b.get('time') or '09:00').strip())
    if not m:
        return {'success': False, 'detail': 'Time must be HH:MM.'}, 400
    chat = re.sub(r'[^A-Za-z0-9_-]+', '', str(b.get('chat') or 'social'))[:40] or 'social'
    try:
        from core.api_fastapi import get_system
        sched = getattr(get_system(), 'continuity_scheduler', None)
    except Exception:
        sched = None
    if not sched:
        return {'success': False, 'detail': 'The scheduler is not running.'}, 503
    data = {'name': f"Social round: {profile}", 'schedule': f"{int(m.group(2))} {int(m.group(1))} * * *",
            'enabled': True, 'toolset': 'comms, web, media', 'persona': str(b.get('persona') or ''),
            'chat_target': chat, 'initial_message': ROUND_PROMPT, 'tts_enabled': False,
            'inject_datetime': True, 'social_scope': profile}
    try:
        task = sched.create_task(data)
    except ValueError as e:
        return {'success': False, 'detail': str(e)}, 400
    tid = task.get('id') if isinstance(task, dict) else task
    return {'success': True, 'task_id': tid, 'name': data['name'], 'time': time.strftime('%H:%M', (0, 0, 0, int(m.group(1)), int(m.group(2)), 0, 0, 0, 0))}
