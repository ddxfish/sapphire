# WordPress tools - drive a WordPress site through its REST API (Application Password auth).
#
# Multi-site: the active site is the `scope_wordpress` ContextVar (set per-chat in the Mind
# dropdown, defaulted from the persona, carried by daemons). The AI never picks a site.
#
# Destructive ops (permanent delete, user delete, plugin toggle, settings write) are gated by
# a per-site, burn-on-use PIN that lives plugin-side and is NEVER returned to the AI. The human
# reads it off the site selector and supplies it when asked; it regenerates after each use.

import logging
import secrets

import requests
from core import net

logger = logging.getLogger(__name__)

ENABLED = True
EMOJI = '\U0001F310'  # globe

PLUGIN_NAME = 'wordpress'
WP_API = '/wp-json/wp/v2'
TIMEOUT = 20

# wp_settings refuses to WRITE these - changing them can lock you out of the site.
_BLOCKED_SETTINGS = {'url', 'home', 'siteurl', 'email', 'admin_email'}

AVAILABLE_FUNCTIONS = [
    'wp_get_blog', 'wp_create_blog', 'wp_delete_blog',
    'wp_get_page', 'wp_create_page', 'wp_delete_page',
    'wp_get_user', 'wp_delete_user',
    'wp_settings', 'wp_plugin',
]

_PIN_HELP = ("Required only for destructive actions (permanent delete, user delete, plugin "
             "enable/disable, settings change). You do NOT have this value - ask the user for "
             "it (shown in the WordPress site selector), then call again with pin set.")

TOOLS = [
    {"type": "function", "is_local": False, "network": True, "function": {
        "name": "wp_get_blog",
        "description": "List blog posts (no id) or read one full post (with id). Use search to find posts by keyword. Lists are paginated and lean.",
        "parameters": {"type": "object", "properties": {
            "id": {"type": "string", "description": "Post id to read in full. Omit to list."},
            "search": {"type": "string", "description": "Keyword filter when listing (matches title/content)."},
            "page": {"type": "integer", "description": "Page number when listing (50 per page)."}
        }, "required": []}}},
    {"type": "function", "is_local": False, "network": True, "function": {
        "name": "wp_create_blog",
        "description": "Create a blog post (no id) or update an existing one (with id). Content is HTML or plain text. Not destructive - revisions are kept.",
        "parameters": {"type": "object", "properties": {
            "id": {"type": "string", "description": "Post id to update. Omit to create a new post."},
            "title": {"type": "string", "description": "Post title."},
            "content": {"type": "string", "description": "Post body (HTML or plain text)."},
            "status": {"type": "string", "description": "'draft' (default) or 'publish'."},
            "category": {"type": "string", "description": "Category name to file the post under (see the Categories line at the top of wp_get_blog). Optional."}
        }, "required": []}}},
    {"type": "function", "is_local": False, "network": True, "function": {
        "name": "wp_delete_blog",
        "description": "Delete a blog post. Default sends it to Trash (recoverable). force=true permanently deletes it and requires the PIN.",
        "parameters": {"type": "object", "properties": {
            "id": {"type": "string", "description": "Post id to delete."},
            "force": {"type": "boolean", "description": "true = permanent (requires PIN). false/omitted = Trash (recoverable)."},
            "pin": {"type": "string", "description": _PIN_HELP}
        }, "required": ["id"]}}},
    {"type": "function", "is_local": False, "network": True, "function": {
        "name": "wp_get_page",
        "description": "List pages (no id) or read one full page (with id). Use search to find pages by keyword. Lists are paginated and lean.",
        "parameters": {"type": "object", "properties": {
            "id": {"type": "string", "description": "Page id to read in full. Omit to list pages."},
            "search": {"type": "string", "description": "Keyword filter when listing (matches title/content)."},
            "page": {"type": "integer", "description": "Page number when listing (50 per page)."}
        }, "required": []}}},
    {"type": "function", "is_local": False, "network": True, "function": {
        "name": "wp_create_page",
        "description": "Create a page (no id) or update an existing one (with id). Content is HTML or plain text. Note: overwriting a page built with a page-builder flattens its layout - prefer this for plain pages.",
        "parameters": {"type": "object", "properties": {
            "id": {"type": "string", "description": "Page id to update. Omit to create a new page."},
            "title": {"type": "string", "description": "Page title."},
            "content": {"type": "string", "description": "Page body (HTML or plain text)."},
            "status": {"type": "string", "description": "'draft' (default) or 'publish'."}
        }, "required": []}}},
    {"type": "function", "is_local": False, "network": True, "function": {
        "name": "wp_delete_page",
        "description": "Delete a page. Default sends it to Trash (recoverable). force=true permanently deletes it and requires the PIN.",
        "parameters": {"type": "object", "properties": {
            "id": {"type": "string", "description": "Page id to delete."},
            "force": {"type": "boolean", "description": "true = permanent (requires PIN). false/omitted = Trash (recoverable)."},
            "pin": {"type": "string", "description": _PIN_HELP}
        }, "required": ["id"]}}},
    {"type": "function", "is_local": False, "network": True, "function": {
        "name": "wp_settings",
        "description": "List site settings (no args) or change one (name + value). Changing a setting requires the PIN. The site URL and admin email are protected and cannot be changed here.",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string", "description": "Setting name to change (e.g. 'title', 'description'). Omit to list all settings."},
            "value": {"type": "string", "description": "New value for the setting."},
            "pin": {"type": "string", "description": _PIN_HELP}
        }, "required": []}}},
    {"type": "function", "is_local": False, "network": True, "function": {
        "name": "wp_get_user",
        "description": "List users (no args) or find users by keyword (search). Shows id, name, email, roles. Use search to find a user among thousands.",
        "parameters": {"type": "object", "properties": {
            "search": {"type": "string", "description": "Keyword filter - matches name, login, and email. The way to find a user among thousands."},
            "page": {"type": "integer", "description": "Page number (100 per page)."}
        }, "required": []}}},
    {"type": "function", "is_local": False, "network": True, "function": {
        "name": "wp_delete_user",
        "description": "Permanently delete a user (e.g. a spam account). Their content is reassigned to admin. Permanent (no Trash) - requires the PIN.",
        "parameters": {"type": "object", "properties": {
            "id": {"type": "string", "description": "User id to delete (from wp_get_user)."},
            "pin": {"type": "string", "description": _PIN_HELP}
        }, "required": ["id"]}}},
    {"type": "function", "is_local": False, "network": True, "function": {
        "name": "wp_plugin",
        "description": "List installed plugins (no args) or enable/disable one (action + id). Toggling a plugin can change site behavior and requires the PIN.",
        "parameters": {"type": "object", "properties": {
            "action": {"type": "string", "description": "'enable' or 'disable'. Omit to list plugins."},
            "id": {"type": "string", "description": "Plugin file id from the list (e.g. 'akismet/akismet')."},
            "pin": {"type": "string", "description": _PIN_HELP}
        }, "required": []}}},
]


# === plugin state / config / pins ===

def _state():
    from core.plugin_loader import plugin_loader
    return plugin_loader.get_plugin_state(PLUGIN_NAME)


def _config():
    cfg = (_state().get('config', {}) or {})
    return {
        # unsupervised=False (default) => destructive ops require the PIN (supervised).
        # unsupervised=True            => destructive ops run with no PIN (dangerous, opt-in).
        'unsupervised': bool(cfg.get('unsupervised', False)),
        'rotation': cfg.get('rotation', 'burn_on_use'),
    }


def _get_scope():
    try:
        from core.chat.function_manager import scope_wordpress
        return scope_wordpress.get()
    except Exception as e:
        # Fail disabled, never default - same silent-default class closed elsewhere.
        logger.warning(f"[WP] scope resolve failed: {e}")
        return None


def _get_account(scope):
    return (_state().get('accounts', {}) or {}).get(scope)


def _gen_pin():
    return f"{secrets.randbelow(10000):04d}"


def _current_pin(scope):
    return (_state().get('pins', {}) or {}).get(scope)


def _ensure_pin(scope):
    """Make sure this site has a PIN - supervised (default) mode always needs one."""
    st = _state()
    pins = dict(st.get('pins', {}) or {})
    if not pins.get(scope):
        pins[scope] = _gen_pin()
        st.save('pins', pins)
    return pins.get(scope)


def _burn_pin(scope):
    """Regenerate this site's PIN after a successful gated op, notify the UI.
    No-op when unsupervised (no PIN in play) or rotation is static."""
    cfg = _config()
    if cfg['unsupervised'] or cfg['rotation'] == 'static':
        return
    st = _state()
    pins = dict(st.get('pins', {}) or {})
    pins[scope] = _gen_pin()
    st.save('pins', pins)
    try:
        from core.event_bus import publish
        publish('wp_pin_changed', {'scope': scope})
    except Exception as e:
        logger.warning(f"[WP] pin-change publish failed: {e}")


def _gate(scope, supplied_pin):
    """Return None if a destructive op is allowed, else a user-facing refusal string.
    Default is PIN-required (supervised); the caller burns only after the op succeeds."""
    if _config()['unsupervised']:
        return None  # the user explicitly allowed destructive ops without a PIN
    current = _ensure_pin(scope)
    if not supplied_pin:
        return ("This is a destructive action and requires the PIN, which you do not have. " + _PIN_HELP)
    if not secrets.compare_digest(str(supplied_pin).strip(), str(current)):
        return "Incorrect PIN. Ask the user for the current PIN shown in the WordPress site selector."
    return None


# === WP REST client ===

def _wp_request(scope, method, path, params=None, json_body=None):
    """Authenticated WP REST call. Returns (data, err). data is True for empty 2xx."""
    acct = _get_account(scope)
    if not acct:
        return None, (f"No WordPress site is selected for this chat (scope '{scope}'). The user "
                      "picks one in the Mind > WordPress dropdown, or adds one in Settings > WordPress.")
    base = (acct.get('base_url') or '').rstrip('/')
    user = acct.get('username') or ''
    app_pw = acct.get('app_password') or ''
    if not base or not user or not app_pw:
        return None, "This WordPress site is missing its URL, username, or Application Password (Settings > WordPress)."
    url = f"{base}{WP_API}{path}"
    try:
        resp = net.request(method, url, params=params, json=json_body,
                                auth=(user, app_pw), timeout=TIMEOUT)
    except requests.RequestException as e:
        return None, f"Could not reach WordPress ({type(e).__name__}): {e}"
    if resp.status_code in (200, 201):
        try:
            return resp.json(), None
        except ValueError:
            return True, None
    if resp.status_code == 401:
        return None, "WordPress rejected the credentials (401). Check the username + Application Password."
    if resp.status_code == 403:
        return None, "WordPress forbade this action (403). The account may lack the required capability."
    if resp.status_code == 404:
        return None, "Not found (404). Check the id."
    msg = ''
    try:
        msg = (resp.json() or {}).get('message', '') or ''
    except Exception:
        pass
    return None, f"WordPress error {resp.status_code}" + (f": {msg}" if msg else "") + "."


def _strip_html(text, limit=400):
    import re
    t = re.sub(r'<[^>]+>', '', text or '')
    t = re.sub(r'\s+', ' ', t).strip()
    return t[:limit] + ('...' if len(t) > limit else '')


def _rendered(field):
    """WP returns {'rendered': '...'} for title/content; normalize to a string."""
    if isinstance(field, dict):
        return field.get('rendered', '')
    return field or ''


# === post / page helpers ===

def _pg(v):
    try:
        return max(1, int(v))
    except (TypeError, ValueError):
        return 1


def _get_categories(scope):
    """Available post categories: [{id, name, slug}]. Empty on error."""
    data, err = _wp_request(scope, 'GET', '/categories',
                            params={'per_page': 100, '_fields': 'id,name,slug'})
    return data if (not err and isinstance(data, list)) else []


def _resolve_category(scope, name_or_id):
    """Resolve a category name (or numeric id) to an id. Returns (id, err)."""
    val = str(name_or_id).strip()
    if not val:
        return None, None
    if val.isdigit():
        return int(val), None
    cats = _get_categories(scope)
    for c in cats:
        if val.lower() in (str(c.get('name', '')).lower(), str(c.get('slug', '')).lower()):
            return c.get('id'), None
    avail = ', '.join(c.get('name', '') for c in cats) or '(none defined)'
    return None, f"Category '{val}' not found. Available: {avail}."


def _list_content(scope, kind, search=None, page=1):
    path = '/posts' if kind == 'blog' else '/pages'
    params = {
        'per_page': 50, 'status': 'publish,draft,pending,future,private',
        '_fields': 'id,title,status,link,date',
        'orderby': 'date', 'order': 'desc', 'page': _pg(page),
    }
    if search:
        params['search'] = search
    data, err = _wp_request(scope, 'GET', path, params=params)
    if err:
        return err, False
    lines = []
    if kind == 'blog':
        cats = _get_categories(scope)
        if cats:
            lines.append("Categories (pass the name to wp_create_blog): "
                         + ", ".join(c.get('name', '') for c in cats))
    if not isinstance(data, list) or not data:
        extra = f" matching '{search}'" if search else ""
        lines.append(f"No {kind} entries found{extra}.")
        return '\n'.join(lines), True
    lines.append(f"{kind.capitalize()} entries ({len(data)}):")
    for item in data:
        title = _rendered(item.get('title')) or '(untitled)'
        lines.append(f"  id {item.get('id')}  [{item.get('status')}]  {title}  ({(item.get('date') or '')[:10]})")
    return '\n'.join(lines), True


def _get_one_content(scope, kind, cid):
    path = ('/posts/' if kind == 'blog' else '/pages/') + str(cid)
    data, err = _wp_request(scope, 'GET', path, params={'_fields': 'id,title,status,link,date,content'})
    if err:
        return err, False
    title = _rendered(data.get('title')) or '(untitled)'
    content = _rendered(data.get('content'))
    out = [f"{kind.capitalize()} {data.get('id')} [{data.get('status')}]: {title}",
           f"Link: {data.get('link', '')}",
           f"Content: {_strip_html(content, 2000)}"]
    return '\n'.join(out), True


def _create_or_update_content(scope, kind, args):
    cid = (args.get('id') or '').strip()
    body = {}
    if args.get('title') is not None:
        body['title'] = args.get('title')
    if args.get('content') is not None:
        body['content'] = args.get('content')
    status = (args.get('status') or '').strip().lower()
    if status in ('draft', 'publish', 'pending', 'private'):
        body['status'] = status
    elif not cid:
        body['status'] = 'draft'  # safe default for new content
    if kind == 'blog':  # pages have no categories
        cat = (args.get('category') or '').strip()
        if cat:
            cat_id, cat_err = _resolve_category(scope, cat)
            if cat_err:
                return cat_err, False
            if cat_id is not None:
                body['categories'] = [cat_id]
    if not body:
        return "Nothing to write - provide a title and/or content.", False
    base = '/posts' if kind == 'blog' else '/pages'
    if cid:
        data, err = _wp_request(scope, 'POST', f"{base}/{cid}", json_body=body)
        verb = 'Updated'
    else:
        data, err = _wp_request(scope, 'POST', base, json_body=body)
        verb = 'Created'
    if err:
        return err, False
    title = _rendered(data.get('title')) or '(untitled)'
    return f"{verb} {kind} {data.get('id')} [{data.get('status')}]: {title}\n{data.get('link', '')}", True


def _delete_content(scope, kind, args):
    cid = (args.get('id') or '').strip()
    if not cid:
        return "A post/page id is required.", False
    force = bool(args.get('force'))
    if force:
        gate_err = _gate(scope, args.get('pin'))
        if gate_err:
            return gate_err, False
    base = '/posts/' if kind == 'blog' else '/pages/'
    _, err = _wp_request(scope, 'DELETE', f"{base}{cid}",
                         params={'force': 'true'} if force else None)
    if err:
        return err, False
    if force:
        _burn_pin(scope)
        return f"Permanently deleted {kind} {cid}.", True
    return f"Moved {kind} {cid} to Trash (recoverable in WordPress).", True


# === tool execution ===

def execute(function_name, arguments, config, plugin_settings=None):
    scope = _get_scope()
    if scope is None:
        return "WordPress is disabled for this chat (no site scope selected).", False

    if function_name == 'wp_get_blog':
        cid = (arguments.get('id') or '').strip()
        if cid:
            return _get_one_content(scope, 'blog', cid)
        return _list_content(scope, 'blog', arguments.get('search'), arguments.get('page', 1))
    if function_name == 'wp_create_blog':
        return _create_or_update_content(scope, 'blog', arguments)
    if function_name == 'wp_delete_blog':
        return _delete_content(scope, 'blog', arguments)

    if function_name == 'wp_get_page':
        cid = (arguments.get('id') or '').strip()
        if cid:
            return _get_one_content(scope, 'page', cid)
        return _list_content(scope, 'page', arguments.get('search'), arguments.get('page', 1))
    if function_name == 'wp_create_page':
        return _create_or_update_content(scope, 'page', arguments)
    if function_name == 'wp_delete_page':
        return _delete_content(scope, 'page', arguments)

    if function_name == 'wp_settings':
        name = (arguments.get('name') or '').strip()
        if not name:
            data, err = _wp_request(scope, 'GET', '/settings')
            if err:
                return err, False
            lines = ["Site settings:"]
            for k, v in sorted((data or {}).items()):
                lines.append(f"  {k}: {v}")
            return '\n'.join(lines), True
        if name in _BLOCKED_SETTINGS:
            return f"Setting '{name}' is protected and cannot be changed here (it could lock the site).", False
        if arguments.get('value') is None:
            return "Provide a value to change this setting.", False
        gate_err = _gate(scope, arguments.get('pin'))
        if gate_err:
            return gate_err, False
        data, err = _wp_request(scope, 'POST', '/settings', json_body={name: arguments.get('value')})
        if err:
            return err, False
        _burn_pin(scope)
        return f"Updated setting '{name}' to: {(data or {}).get(name, arguments.get('value'))}", True

    if function_name == 'wp_get_user':
        uparams = {'per_page': 100, '_fields': 'id,name,slug,roles,email', 'context': 'edit',
                   'page': _pg(arguments.get('page', 1))}
        if arguments.get('search'):
            uparams['search'] = arguments.get('search')
        data, err = _wp_request(scope, 'GET', '/users', params=uparams)
        if err:
            return err, False
        if not isinstance(data, list) or not data:
            extra = f" matching '{arguments.get('search')}'" if arguments.get('search') else ""
            return f"No users found{extra}.", True
        lines = [f"Users ({len(data)}):"]
        for u in data:
            roles = ','.join(u.get('roles', []) or [])
            email = u.get('email') or '(no email)'
            lines.append(f"  id {u.get('id')}  {u.get('name')}  (@{u.get('slug')})  <{email}>  [{roles}]")
        return '\n'.join(lines), True

    if function_name == 'wp_delete_user':
        uid = (arguments.get('id') or '').strip()
        if not uid:
            return "A user id is required to delete.", False
        gate_err = _gate(scope, arguments.get('pin'))
        if gate_err:
            return gate_err, False
        # Users have no Trash - delete is permanent; reassign their content to admin (id 1).
        _, err = _wp_request(scope, 'DELETE', f'/users/{uid}',
                             params={'force': 'true', 'reassign': '1'})
        if err:
            return err, False
        _burn_pin(scope)
        return f"Deleted user {uid} (their content reassigned to admin).", True

    if function_name == 'wp_plugin':
        action = (arguments.get('action') or '').strip().lower()
        if not action:
            data, err = _wp_request(scope, 'GET', '/plugins')
            if err:
                return err, False
            if not isinstance(data, list) or not data:
                return "No plugins found.", True
            lines = [f"Plugins ({len(data)}):"]
            for p in data:
                lines.append(f"  {p.get('plugin')}  [{p.get('status')}]  {p.get('name')}")
            return '\n'.join(lines), True
        if action not in ('enable', 'disable'):
            return "Unknown plugin action. Use 'enable' or 'disable'.", False
        pid = (arguments.get('id') or '').strip()
        if not pid:
            return "A plugin id is required (from the plugin list, e.g. 'akismet/akismet').", False
        gate_err = _gate(scope, arguments.get('pin'))
        if gate_err:
            return gate_err, False
        new_status = 'active' if action == 'enable' else 'inactive'
        data, err = _wp_request(scope, 'POST', f'/plugins/{pid}', json_body={'status': new_status})
        if err:
            return err, False
        _burn_pin(scope)
        return f"Plugin {pid} is now {(data or {}).get('status', new_status)}.", True

    return f"Unknown function: {function_name}", False
