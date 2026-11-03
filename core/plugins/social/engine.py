# core/plugins/social/engine.py - what the tools and the card call
#
# Resolves the chat's profile (the `social` scope), picks connections by name
# or platform, imports the provider module through the registry, keeps the
# per-connection mode gate (draft → Outbox, auto → post now), hands providers
# a Connection and bytes, and turns their answers into lines for the model.
# Providers raise SocialError; anything else they raise is wrapped here.
import importlib
import logging
import sys
import threading
import time

from . import outbox, registry, store
from .errors import SocialError

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_modules = {}        # provider id -> module
_modules_gen = -1
INBOX_MAX = 50


# ── profile and connections ─────────────────────────────────────────────────

def current_profile_name():
    try:
        from core.chat.function_manager import scope_social
        return scope_social.get()
    except Exception:
        return 'default'


def resolve_profile(name=None):
    """(profile, None) or (None, why not)."""
    table = store.profiles()
    if name:
        name = store.clean_name(name)
        if name in table:
            return name, None
        return None, f"No profile '{name}'. Profiles: {', '.join(sorted(table)) or 'none yet'}."
    if not table:
        return None, "No social profiles yet: make one in Settings > Plugins > Social."
    v = current_profile_name()
    if v is None or v == 'none':
        return None, "Social is off for this chat (Mind > Social is none)."
    if v in table:
        return v, None
    if len(table) == 1:
        return next(iter(table)), None
    return None, f"Pick a profile in Mind > Social ({', '.join(sorted(table))}), or name one in `profile`."


def pick(profile, names=''):
    """Connections of a profile by name or platform; '' = all of them."""
    conns = store.connections(profile)
    if not conns:
        raise SocialError(f"Profile '{profile}' has no connections yet (Settings > Plugins > Social).")
    wanted = [w.strip().lower() for w in str(names or '').replace(';', ',').split(',') if w.strip()]
    if not wanted:
        return conns
    out, unknown = [], []
    for w in wanted:
        hit = [c for c in conns if c.name == w or c.provider == w]
        if hit:
            out.extend(h for h in hit if h not in out)
        else:
            unknown.append(w)
    if unknown:
        raise SocialError(f"No connection {', '.join(unknown)} in profile '{profile}'. "
                          f"Have: {', '.join(c.display() for c in conns)}")
    return out


# ── providers ───────────────────────────────────────────────────────────────

def _plugin_info(name):
    try:
        from core.plugin_loader import plugin_loader
        return plugin_loader.get_plugin_info(name)
    except Exception:
        return None


def module_for(conn):
    """(module, spec) for a connection's provider. The owning plugin must be
    loaded NOW (no unload event exists; checked on every call)."""
    global _modules_gen
    spec = registry.get_provider(conn.provider)
    if not spec:
        raise SocialError(f"the {conn.provider} provider is not loaded (its plugin may be off), "
                          f"so '{conn.name}' cannot be used")
    plugin = spec['plugin_name']
    if plugin != store.PLUGIN:
        info = _plugin_info(plugin)
        if not info or not (info.get('enabled') and info.get('loaded')):
            raise SocialError(f"the {plugin} plugin is off, so its {conn.provider} provider cannot run")
    with _lock:
        gen = registry.generation()
        if gen != _modules_gen:              # a plugin came, went, or reloaded
            for m in _modules.values():
                if sys.modules.get(m.__name__) is m:      # ours, not a newer import under that name
                    sys.modules.pop(m.__name__, None)
            _modules.clear()
            _modules_gen = gen
        mod = _modules.get(spec['provider'])
        if mod is None:
            dotted = spec['module'][:-3].replace('/', '.')
            try:
                mod = importlib.import_module(f"plugins.{plugin}.{dotted}")
            except Exception as e:
                logger.error(f"[SOCIAL] provider '{spec['provider']}' failed to import: {e}", exc_info=True)
                raise SocialError(f"the {conn.provider} provider failed to load: {e}")
            _modules[spec['provider']] = mod
    return mod, spec


def can(spec, cap):
    return cap in spec['capabilities']


def _ready(conn, mod, spec):
    """Connected, and the token fresh enough to use."""
    if not conn.connected:
        where = f"Settings > Plugins > Social > {conn.profile} > {conn.name}"
        if spec['auth'] == 'oauth':
            raise SocialError(f"'{conn.name}' is not connected yet: {where} > Connect")
        raise SocialError(f"'{conn.name}' is missing its credentials: {where}")
    exp = conn.meta.get('expires_at')
    try:
        exp = float(exp) if exp else 0
    except (TypeError, ValueError):
        exp = 0
    if not exp or exp > time.time() + 120:
        return
    if not callable(getattr(mod, 'refresh', None)):
        if exp < time.time():
            raise SocialError(f"'{conn.name}': the token has expired and {spec['label']} gives no refresh: "
                              f"Settings > Plugins > Social > {conn.profile} > {conn.name} > Connect again")
        return
    try:
        mod.refresh(conn)
    except SocialError:
        raise
    except Exception as e:
        raise SocialError(f"'{conn.name}': token refresh failed: {e}")


def _images_for(spec, images):
    """What the provider's post() gets: [(bytes, media_type)], or [url] on a
    platform that fetches by URL (the drop folder, or the https source given)."""
    images = list(images or [])
    if not images:
        return []
    if not can(spec, 'image') or not spec['max_images']:
        raise SocialError(f"{spec['label']} takes no images here")
    images = images[:spec['max_images']]
    if not spec['public_image_url']:
        return [(raw, mt) for raw, mt, _src in images]
    from . import drop
    out = []
    for raw, mt, src in images:
        out.append(src if (src or '').startswith('https://') else drop.publish(raw, mt))
    return out


def publish(conn, text, images=(), link='', reply_to=''):
    """Post (or reply) NOW on one connection. The platform's {'id', 'url'}.
    Raises SocialError; writes no outbox row - the caller keeps the log."""
    mod, spec = module_for(conn)
    cap = 'reply' if reply_to else 'post'
    if not can(spec, cap) or not callable(getattr(mod, cap, None)):
        raise SocialError(f"{spec['label']} cannot {cap} here")
    _ready(conn, mod, spec)
    try:
        if reply_to:
            result = mod.reply(conn, reply_to, text)
        else:
            media = _images_for(spec, images)
            if link and not can(spec, 'link'):
                text, link = f"{text}\n{link}".strip(), ''
            result = mod.post(conn, text, media, link or None, conn.disclose)
    except SocialError:
        raise
    except Exception as e:
        logger.error(f"[SOCIAL] {conn.key} {cap} failed: {e}", exc_info=True)
        raise SocialError(f"{spec['label']} refused: {type(e).__name__}: {e}")
    return dict(result or {})


def _send(conn, text, images=(), link='', reply_to=''):
    """The mode gate. One line for the model; True when a draft was queued."""
    mod, spec = module_for(conn)
    cap = 'reply' if reply_to else 'post'
    if not can(spec, cap):
        return f"{conn.display()}: cannot {cap} (reads only).", False
    if len(text) > spec['max_chars']:
        return f"{conn.display()}: too long ({len(text)} > {spec['max_chars']} characters).", False
    if images and not reply_to and (not can(spec, 'image') or not spec['max_images']):
        return f"{conn.display()}: takes no images; post text only or pick another connection.", False
    if not text and not images:
        return f"{conn.display()}: nothing to send.", False
    files = [(raw, mt) for raw, mt, _ in images] if not reply_to else []
    if conn.mode == 'draft':
        row = outbox.add(conn.profile, conn.name, text, files, link, result={'reply_to': reply_to} if reply_to else None)
        return f"{conn.display()}: queued as draft {row['id']} for approval.", True
    try:
        result = publish(conn, text, images, link, reply_to)
    except SocialError as e:
        outbox.add(conn.profile, conn.name, text, files, link, status='failed',
                   result={'error': str(e), **({'reply_to': reply_to} if reply_to else {})})
        outbox.notify_failed(conn.display(), str(e))
        return f"{conn.display()}: {e} (kept in the Outbox for a retry)", False
    outbox.add(conn.profile, conn.name, text, files, link, status='posted',
               result={**result, **({'reply_to': reply_to} if reply_to else {})})
    url = result.get('url') or ''
    return f"{conn.display()}: {'replied' if reply_to else 'posted'}{(' ' + url) if url else ''}.", False


def post(profile, names, text, images=(), link=''):
    """Post on every picked connection. One line each."""
    text = str(text or '').strip()
    if not text and not images:
        raise SocialError("nothing to post: give text, an image, or both")
    lines, queued = [], False
    for conn in pick(profile, names):
        try:
            line, q = _send(conn, text, images, link)
        except SocialError as e:
            line, q = f"{conn.display()}: {e}", False
        lines.append(line)
        queued = queued or q
    if queued:
        outbox.notify_pending()
    return '\n'.join(lines)


def reply(profile, name, item_id, text):
    conns = pick(profile, name)
    if len(conns) != 1:
        raise SocialError("name exactly one connection to reply on")
    text = str(text or '').strip()
    if not text or not str(item_id or '').strip():
        raise SocialError("a reply needs the item id (from social_inbox) and text")
    line, queued = _send(conns[0], text, (), '', str(item_id).strip())
    if queued:
        outbox.notify_pending()
    return line


def approve(rid):
    """Post a draft now, or retry a failed row. (ok, url-or-reason)."""
    row = outbox.get(rid)
    if not row:
        return False, f"No draft {rid}."
    if row.get('status') not in ('draft', 'failed'):
        return False, f"Draft {rid} is already {row['status']}."
    conn = store.get_connection(row['profile'], row['connection'])
    if not conn:
        outbox.update(rid, status='failed', decided=time.time(), result={'error': 'connection removed'})
        return False, f"Connection {row['profile']}/{row['connection']} no longer exists."
    reply_to = (row.get('result') or {}).get('reply_to') or ''
    images = [(raw, mt, None) for raw, mt in outbox.images_of(row)]
    try:
        result = publish(conn, row['text'], images, row.get('link') or '', reply_to)
    except SocialError as e:
        outbox.update(rid, status='failed', decided=time.time(), result={'error': str(e), 'reply_to': reply_to})
        return False, str(e)
    outbox.update(rid, status='posted', decided=time.time(), result={**result, **({'reply_to': reply_to} if reply_to else {})})
    return True, result.get('url') or result.get('id') or 'posted'


def reject(rid):
    row = outbox.get(rid)
    if not row:
        return False, f"No draft {rid}."
    if row.get('status') not in ('draft', 'failed'):
        return False, f"Draft {rid} is already {row['status']}."
    outbox.update(rid, status='rejected', decided=time.time())
    return True, 'rejected'


# ── reads ───────────────────────────────────────────────────────────────────

def _when(ts):
    try:
        return time.strftime('%m-%d %H:%M', time.localtime(float(ts))) if ts else ''
    except (TypeError, ValueError, OverflowError):
        return ''


def inbox(profile, names='', since_hours=24):
    try:
        hours = max(0.1, min(24 * 90, float(since_hours or 24)))
    except (TypeError, ValueError):
        hours = 24
    since = time.time() - hours * 3600
    lines, readers = [], 0
    for conn in pick(profile, names):
        try:
            mod, spec = module_for(conn)
            if not can(spec, 'inbox') or not callable(getattr(mod, 'inbox', None)):
                continue
            readers += 1
            _ready(conn, mod, spec)
            items = list(mod.inbox(conn, since) or [])
        except SocialError as e:
            lines.append(f"{conn.display()}: {e}")
            continue
        except Exception as e:
            logger.error(f"[SOCIAL] {conn.key} inbox failed: {e}", exc_info=True)
            lines.append(f"{conn.display()}: {type(e).__name__}: {e}")
            continue
        if not items:
            lines.append(f"{conn.display()}: nothing new in the last {hours:g} h.")
            continue
        lines.append(f"{conn.display()}: {len(items)} new")
        for it in items[:INBOX_MAX]:
            text = ' '.join(str(it.get('text') or '').split())[:300]
            tail = f" {it['url']}" if it.get('url') else ''
            lines.append(f"  [{it.get('id', '?')}] {it.get('kind', '')} {it.get('author', '')} "
                         f"{_when(it.get('ts'))}: {text}{tail}")
    if not readers:
        return "No connection in this profile reads an inbox."
    return '\n'.join(lines)


def delete(profile, name, post_id):
    conns = pick(profile, name)
    if len(conns) != 1:
        raise SocialError("name exactly one connection")
    conn = conns[0]
    mod, spec = module_for(conn)
    if not can(spec, 'delete') or not callable(getattr(mod, 'delete', None)):
        raise SocialError(f"{spec['label']} cannot delete here")
    _ready(conn, mod, spec)
    try:
        ok = mod.delete(conn, str(post_id or '').strip())
    except SocialError:
        raise
    except Exception as e:
        raise SocialError(f"{spec['label']} refused: {type(e).__name__}: {e}")
    return f"{conn.display()}: {'deleted' if ok else 'not deleted'} {post_id}."


def stats(profile, names=''):
    lines = []
    for conn in pick(profile, names):
        try:
            mod, spec = module_for(conn)
            if not can(spec, 'stats') or not callable(getattr(mod, 'stats', None)):
                continue
            _ready(conn, mod, spec)
            d = mod.stats(conn) or {}
        except SocialError as e:
            lines.append(f"{conn.display()}: {e}")
            continue
        except Exception as e:
            lines.append(f"{conn.display()}: {type(e).__name__}: {e}")
            continue
        lines.append(f"{conn.display()}: " + (', '.join(f"{k} {v}" for k, v in d.items()) or 'no numbers'))
    return '\n'.join(lines) or "No connection in this profile reports numbers."


def list_text(profile=None):
    table = store.list_profiles()
    if not table:
        return "No social profiles yet. Make one in Settings > Plugins > Social."
    cur, _ = resolve_profile(profile)
    lines = []
    for p in table:
        mark = ' ← this chat' if p['name'] == cur else ''
        conns = ', '.join(
            f"{c['name']} [{c['provider']}{(' ' + c['identity']) if c['identity'] else ''}, {c['mode']}"
            f"{'' if c['connected'] else ', NOT CONNECTED'}{'' if c['loaded'] else ', provider off'}]"
            for c in p['connections']) or 'no connections'
        lines.append(f"{p['name']}{mark}: {conns}")
    drafts = len(outbox.pending())
    if drafts:
        lines.append(f"{drafts} draft{'s' if drafts != 1 else ''} waiting in the Outbox.")
    return '\n'.join(lines)
