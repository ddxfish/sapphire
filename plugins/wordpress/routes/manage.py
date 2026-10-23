# WordPress plugin - management routes (accounts CRUD + global config).
#
# Self-contained: everything lives in PluginState (user/plugin_state/wordpress.json).
# - accounts[scope] = {base_url, username, app_password, label}
# - pins[scope]     = current burn-on-use PIN (human-only; never sent to the AI)
# - config          = {destructive_enabled, rotation}
#
# The accounts list feeds BOTH the settings UI and the Mind scope dropdown. The dropdown
# label embeds the PIN ("(1234) sapphireblue.dev") for the human; the scope VALUE the AI
# receives is the bare key. The Application Password is never returned (has_password bool).

import re
import secrets

PLUGIN_NAME = 'wordpress'
_NAME_RE = re.compile(r'[^a-z0-9_-]+')


def _state():
    from core.plugin_loader import plugin_loader
    return plugin_loader.get_plugin_state(PLUGIN_NAME)


def _config(st=None):
    cfg = ((st or _state()).get('config', {}) or {})
    return {
        # unsupervised=False (default): destructive ops require the PIN. True: no PIN (opt-in danger).
        'unsupervised': bool(cfg.get('unsupervised', False)),
        'rotation': cfg.get('rotation', 'burn_on_use'),
    }


def _sanitize_scope(raw):
    s = _NAME_RE.sub('', (raw or '').strip().lower().replace(' ', '-'))
    return s[:40]


def _gen_pin():
    return f"{secrets.randbelow(10000):04d}"


def _ensure_pins(st):
    """Every account always has a PIN - supervised (PIN-required) is the default mode."""
    accounts = st.get('accounts', {}) or {}
    pins = dict(st.get('pins', {}) or {})
    changed = False
    for scope in accounts:
        if not pins.get(scope):
            pins[scope] = _gen_pin()
            changed = True
    if changed:
        st.save('pins', pins)


def _label_for(scope, acct, st):
    friendly = (acct.get('label') or '').strip() or scope
    if _config(st)['unsupervised']:
        return f"⚠ {friendly}"  # destructive ops run with NO PIN
    pin = (st.get('pins', {}) or {}).get(scope)
    return f"({pin}) {friendly}" if pin else friendly


def list_accounts(**_):
    st = _state()
    _ensure_pins(st)
    accounts = st.get('accounts', {}) or {}
    pins = st.get('pins', {}) or {}
    unsupervised = _config(st)['unsupervised']
    out = []
    for scope, acct in sorted(accounts.items()):
        out.append({
            'scope': scope,
            'label': _label_for(scope, acct, st),
            'friendly_name': (acct.get('label') or '').strip() or scope,
            'base_url': acct.get('base_url', ''),
            'username': acct.get('username', ''),
            'has_password': bool(acct.get('app_password')),
            'pin': '' if unsupervised else pins.get(scope, ''),
            'unsupervised': unsupervised,
        })
    return {'accounts': out}


def put_account(scope, body=None, **_):
    scope = _sanitize_scope(scope)
    if not scope:
        return {'success': False, 'detail': 'Invalid site name.'}
    body = body or {}
    base_url = (body.get('base_url') or '').strip().rstrip('/')
    username = (body.get('username') or '').strip()
    app_password = (body.get('app_password') or '').strip()
    label = (body.get('label') or '').strip() or scope
    if not base_url or not username:
        return {'success': False, 'detail': 'Base URL and username are required.'}
    if not re.match(r'^https?://', base_url):
        return {'success': False, 'detail': 'Base URL must start with http:// or https://'}

    st = _state()
    accounts = dict(st.get('accounts', {}) or {})
    existing = accounts.get(scope, {})
    accounts[scope] = {
        'base_url': base_url,
        'username': username,
        # blank password on edit = keep the existing one (write-only field)
        'app_password': app_password or existing.get('app_password', ''),
        'label': label,
    }
    st.save('accounts', accounts)
    _ensure_pins(st)
    return {'success': True, 'scope': scope}


def delete_account(scope, **_):
    scope = _sanitize_scope(scope)
    st = _state()
    accounts = dict(st.get('accounts', {}) or {})
    pins = dict(st.get('pins', {}) or {})
    accounts.pop(scope, None)
    pins.pop(scope, None)
    st.save('accounts', accounts)
    st.save('pins', pins)
    return {'success': True}


def get_config(**_):
    return _config()


def put_config(body=None, **_):
    body = body or {}
    st = _state()
    cfg = dict(st.get('config', {}) or {})
    if 'unsupervised' in body:
        cfg['unsupervised'] = bool(body.get('unsupervised'))
    if 'rotation' in body:
        rot = body.get('rotation')
        cfg['rotation'] = rot if rot in ('burn_on_use', 'static') else 'burn_on_use'
    st.save('config', cfg)
    _ensure_pins(st)  # turning it on mints PINs for all sites
    try:
        from core.event_bus import publish
        publish('wp_pin_changed', {'scope': '*'})  # nudge the dropdown to re-render labels
    except Exception:
        pass
    return {'success': True, **_config(st)}
