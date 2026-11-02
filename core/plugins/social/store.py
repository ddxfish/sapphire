# core/plugins/social/store.py - profiles, their connections, and where secrets live
#
# A PROFILE is who posts (Anita, Sapphire): the value of the `social` scope a
# chat or persona selects. It holds CONNECTIONS, each one provider plus a
# label: Sapphire's profile may hold two Facebook Pages.
#
# Two homes, one object:
#   plugin state  user/plugin_state/social.json  profiles.<p>.connections.<c> =
#                 {provider, label, mode: draft|auto, disclose, meta}  (plain wiring)
#   credentials   core's plugin_accounts['social']['<p>/<c>'] = every config_schema
#                 field, the secret ones scrambled (never plugin state, never backups)
# Connection reads both; a provider gets a Connection and never a raw dict.

import logging
import re

from . import registry

logger = logging.getLogger(__name__)

PLUGIN = 'social'
MODES = ('draft', 'auto')
_NAME_RE = re.compile(r'[a-z0-9][a-z0-9_-]{0,32}$')


def _loader():
    from core.plugin_loader import plugin_loader
    return plugin_loader


def _state():
    return _loader().get_plugin_state(PLUGIN)


def _creds():
    from core.credentials_manager import credentials
    return credentials


def settings():
    try:
        return _loader().get_plugin_settings(PLUGIN) or {}
    except Exception:
        return {}


def clean_name(raw):
    s = re.sub(r'[^a-z0-9_-]+', '', str(raw or '').strip().lower().replace(' ', '-'))
    return s if _NAME_RE.fullmatch(s) else ''


def profiles():
    return dict(_state().get('profiles', {}) or {})


def get_profile(name):
    return profiles().get(name)


def _mutate(fn):
    _state().update_with_lock('profiles', lambda cur: fn(dict(cur or {})), default={})


def put_profile(name, label=None):
    """Create or relabel a profile. (ok, name-or-reason)."""
    name = clean_name(name)
    if not name:
        return False, 'Profile names: lowercase letters, digits, - and _ (33 max).'

    def fn(table):
        row = dict(table.get(name) or {'label': name, 'connections': {}})
        if label is not None:
            row['label'] = str(label).strip()[:60] or name
        table[name] = row
        return table
    _mutate(fn)
    return True, name


def delete_profile(name):
    name = clean_name(name)
    if name not in profiles():
        return False
    for c in connections(name):
        _creds().delete_plugin_account(PLUGIN, c.key)

    def fn(table):
        table.pop(name, None)
        return table
    _mutate(fn)
    return True


def list_profiles():
    """For the scope dropdown and the card: [{name, value, label, connections}]."""
    out = []
    for name, row in sorted(profiles().items()):
        out.append({'name': name, 'scope': name, 'value': name, 'label': row.get('label') or name,
                    'connections': [c.summary() for c in connections(name)]})
    return out


def split_config(spec, config):
    """(fields, secrets) by the provider's schema; keys it does not declare
    are dropped. A secret given as None is left alone, '' clears it."""
    fields, secrets = {}, {}
    for f in spec.get('config_schema') or []:
        k = f['key']
        if k not in (config or {}):
            continue
        v = config[k]
        if f.get('secret') or f.get('type') == 'password':
            if v is not None:
                secrets[k] = '' if v == '' else str(v)
        else:
            fields[k] = v
    return fields, secrets


def put_connection(profile, name, provider, label=None, mode=None, disclose=None, config=None):
    """Create or update one connection. (ok, name-or-reason)."""
    profile, name = clean_name(profile), clean_name(name)
    if not profile or profile not in profiles():
        return False, 'No such profile.'
    if not name:
        return False, 'Connection names: lowercase letters, digits, - and _ (33 max).'
    spec = registry.get_provider(provider)
    if not spec:
        return False, f"Provider '{provider}' is not loaded (its plugin may be off)."
    existing = get_connection(profile, name)
    if existing and existing.provider != spec['provider']:
        return False, f"'{name}' is a {existing.provider} connection; delete it to change the provider."
    if mode is not None and mode not in MODES:
        return False, f"mode must be one of {MODES}."
    fields, secrets = split_config(spec, config or {})
    if not _creds().set_plugin_account(PLUGIN, f'{profile}/{name}', fields=fields, secrets=secrets):
        return False, 'Could not save the credentials.'

    def fn(table):
        row = table.setdefault(profile, {'label': profile, 'connections': {}})
        conns = row.setdefault('connections', {})
        c = dict(conns.get(name) or {
            'provider': spec['provider'], 'label': '',
            'mode': settings().get('SOCIAL_DEFAULT_MODE') if settings().get('SOCIAL_DEFAULT_MODE') in MODES else 'draft',
            'disclose': True, 'meta': {}})
        if label is not None:
            c['label'] = str(label).strip()[:60]
        if mode is not None:
            c['mode'] = mode
        if disclose is not None:
            c['disclose'] = bool(disclose)
        conns[name] = c
        return table
    _mutate(fn)
    return True, name


def delete_connection(profile, name):
    profile, name = clean_name(profile), clean_name(name)
    c = get_connection(profile, name)
    if not c:
        return False
    _creds().delete_plugin_account(PLUGIN, c.key)

    def fn(table):
        ((table.get(profile) or {}).get('connections') or {}).pop(name, None)
        return table
    _mutate(fn)
    return True


def get_connection(profile, name):
    row = ((profiles().get(profile) or {}).get('connections') or {}).get(name)
    return Connection(profile, name, row) if row else None


def connections(profile):
    conns = (profiles().get(profile) or {}).get('connections') or {}
    return [Connection(profile, n, r) for n, r in sorted(conns.items())]


class Connection:
    """One account on one platform, as a provider sees it. Printing it never
    shows a secret."""

    def __init__(self, profile, name, row):
        self.profile = profile
        self.name = name
        self.provider = str(row.get('provider') or '')
        self.label = str(row.get('label') or '')
        self.mode = row.get('mode') if row.get('mode') in MODES else 'draft'
        self.disclose = row.get('disclose', True) is not False
        self.meta = dict(row.get('meta') or {})
        self._acct = None

    @property
    def key(self):
        return f'{self.profile}/{self.name}'

    def _account(self):
        if self._acct is None:
            self._acct = _creds().get_plugin_account(PLUGIN, self.key) or {'fields': {}, 'secrets': {}}
        return self._acct

    @property
    def config(self):
        """The non-secret schema fields."""
        return dict(self._account().get('fields') or {})

    @property
    def secrets(self):
        """The secret schema fields and whatever auth_finish stored, in the clear."""
        return dict(self._account().get('secrets') or {})

    def save_secrets(self, secrets):
        """Merge: a str replaces, '' clears, None keeps."""
        self._acct = None
        return _creds().set_plugin_account(PLUGIN, self.key, secrets=dict(secrets or {}))

    def save_config(self, fields):
        self._acct = None
        return _creds().set_plugin_account(PLUGIN, self.key, fields=dict(fields or {}))

    def save_meta(self, meta):
        """Merge plain facts a provider learned (identity, ids, expiry) into the wiring."""
        meta = dict(meta or {})

        def fn(table):
            c = ((table.get(self.profile) or {}).get('connections') or {}).get(self.name)
            if c is not None:
                c['meta'] = {**(c.get('meta') or {}), **meta}
            return table
        _mutate(fn)
        self.meta.update(meta)
        return True

    def spec(self):
        return registry.get_provider(self.provider)

    @property
    def identity(self):
        return str(self.meta.get('identity') or '')

    @property
    def connected(self):
        spec = self.spec()
        if not spec:
            return False
        if spec['auth'] == 'oauth':
            return bool(self.meta.get('connected'))
        needed = [f['key'] for f in spec['config_schema'] if f.get('secret') or f.get('type') == 'password']
        return all(self.secrets.get(k) for k in needed)

    def summary(self):
        spec = self.spec()
        return {'name': self.name, 'provider': self.provider, 'label': self.label,
                'provider_label': spec['label'] if spec else self.provider, 'icon': spec['icon'] if spec else '',
                'mode': self.mode, 'disclose': self.disclose, 'identity': self.identity,
                'connected': self.connected, 'loaded': spec is not None, 'config': self.config,
                'secrets_set': sorted(k for k, v in self.secrets.items() if v),
                'meta': {k: v for k, v in self.meta.items() if k in ('identity', 'url', 'expires_at', 'connected')}}

    def display(self):
        who = f" ({self.identity})" if self.identity else ''
        return f"{self.name} [{self.provider}{who}]"

    def __repr__(self):
        return f"<Connection {self.key} {self.provider} redacted>"

    __str__ = __repr__
