# core/plugins/calendar/accounts.py - the logins a calendar is linked through
"""
A calendar (what a chat picks) can hold accounts: a login at a provider, whose
calendars are then linked in. This is where a CalDAV login is kept.

Storage is core's plugin account store (the credentials manager), under the
name '<calendar>/<account id>': the password is scrambled there with the
machine's key, and nothing secret is written to this plugin's own files.

Google is the one account NOT kept here: it lives in core's own Google
calendar store, under the calendar's name, where the plugin this one replaced
put it. One Google account per calendar; any number of CalDAV accounts.

A CalDAV account can borrow the login of an email account instead of holding
a password of its own (iCloud, Fastmail and Yahoo use one app password for
mail and calendar). It then keeps the email account's name and address, and
reads the password from there each time, so changing it in one place fixes
both. If that name later holds a different mailbox, nothing is borrowed: the
password of another address is never sent to this account's server.

A password only goes where the account's own server said its calendars are:
`hosts` is the list of servers the login was last tried against and found
calendars on, and allows() is asked before a link is read or written.
"""
import hashlib
import logging
import re
import secrets
import time

from . import caldav
from .errors import CalError

logger = logging.getLogger(__name__)

PLUGIN = 'calendar'
_ID_LETTERS = 'abcdefghijkmnpqrstuvwxyz'
_ID_REST = 'abcdefghijkmnpqrstuvwxyz23456789'
_STORE_NAME = re.compile(r'[a-z0-9][a-z0-9_-]{0,31}')

# What the "Add account" picker offers. {user} in an address is the login name.
PRESETS = (
    {'id': 'icloud', 'label': 'iCloud', 'url': 'https://caldav.icloud.com/', 'ask_server': False,
     'user_label': 'Apple Account email',
     'help': "Needs an app-specific password: account.apple.com, Sign-In and Security, App-Specific Passwords. "
             "Your normal Apple password does not work here."},
    {'id': 'fastmail', 'label': 'Fastmail', 'url': 'https://caldav.fastmail.com/dav/principals/user/{user}/',
     'ask_server': False, 'user_label': 'Fastmail address',
     'help': "Needs an app password that covers calendars: Settings, Privacy & Security, App passwords."},
    {'id': 'yahoo', 'label': 'Yahoo', 'url': 'https://caldav.calendar.yahoo.com/', 'ask_server': False,
     'user_label': 'Yahoo address',
     'help': "Needs an app password: Yahoo Account Security, Generate app password."},
    {'id': 'nextcloud', 'label': 'Nextcloud', 'url': '{server}/remote.php/dav/', 'ask_server': True,
     'user_label': 'Login name',
     'help': "Your Nextcloud's address, your login name, and an app password (Settings, Security, Devices & sessions)."},
    {'id': 'caldav', 'label': 'Other (CalDAV)', 'url': '{server}', 'ask_server': True, 'user_label': 'Login name',
     'help': "Any CalDAV server: the address your provider or your own server gives for CalDAV."},
)


def preset(preset_id):
    return next((p for p in PRESETS if p['id'] == preset_id), PRESETS[-1])


def _creds():
    from core.credentials_manager import credentials
    return credentials


def _key(calendar, account_id):
    return f"{calendar}/{account_id}"


def _row(name, fields):
    calendar, _, account_id = name.partition('/')
    return {'id': account_id, 'calendar': calendar, 'provider': fields.get('provider') or 'caldav',
            'preset': fields.get('preset') or 'caldav', 'label': fields.get('label') or '',
            'url': fields.get('url') or '', 'username': fields.get('username') or '',
            'email_account': fields.get('email_account') or '', 'email_address': fields.get('email_address') or '',
            'hosts': [str(h) for h in fields.get('hosts') or []]}


def can_hold(calendar):
    """Refuse (before any login is tried) a calendar whose name an account cannot be stored under."""
    if not _STORE_NAME.fullmatch(str(calendar or '')):
        raise CalError(f"The calendar '{calendar}' has a name from an older version that an account cannot be "
                       "saved under. Make a new calendar and add the account there.")


def allows(row, ref):
    """True when a calendar address is on a server this account's own login found its calendars on."""
    host = caldav.host_of(ref).lower()
    return bool(host) and host in {h.lower() for h in row['hosts']}


def rows(calendar=None):
    """Every stored account (of one calendar), no secrets."""
    try:
        listed = _creds().list_plugin_accounts(PLUGIN)
    except Exception as e:
        logger.warning(f"[calendar] accounts could not be listed: {e}")
        return []
    out = [_row(a['name'], a.get('fields') or {}) for a in listed if '/' in a['name']]
    return [r for r in out if calendar is None or r['calendar'] == calendar]


def get(calendar, account_id):
    return next((r for r in rows(calendar) if r['id'] == str(account_id or '')), None)


def email_logins():
    """Email accounts whose login a CalDAV account may borrow: the ones with a password."""
    try:
        listed = _creds().list_email_accounts()
    except Exception as e:
        logger.debug(f"[calendar] email accounts not listed: {e}")
        return []
    return [{'scope': a['scope'], 'address': a.get('address') or ''} for a in listed
            if a.get('auth_type', 'password') == 'password' and a.get('address')]


def _password(row):
    if row['email_account']:
        try:
            mail = _creds().get_email_account(row['email_account'])
        except Exception as e:
            logger.warning(f"[calendar] email account '{row['email_account']}' not read: {e}")
            mail = {}
        if not mail.get('app_password') or mail.get('auth_type', 'password') != 'password':
            raise CalError(f"'{row['label']}' uses the login of the email account '{row['email_account']}', "
                           "and that account has no password any more. Open Accounts and give this one its own.")
        if (mail.get('address') or '').strip().lower() != row['email_address'].strip().lower():
            raise CalError(f"'{row['label']}' uses the login of the email account '{row['email_account']}', "
                           "and that account now holds a different address. Open Accounts and set this one's "
                           "password again.")
        return mail['app_password']
    try:
        got = _creds().get_plugin_account(PLUGIN, _key(row['calendar'], row['id'])) or {}
    except Exception as e:
        logger.warning(f"[calendar] account '{row['label']}' not read: {e}")
        got = {}
    secret = (got.get('secrets') or {}).get('password') or ''
    if not secret:
        raise CalError(f"The password of '{row['label']}' cannot be read on this machine. Open Accounts and type it again.")
    return secret


# A login a server refused is not tried again for a while: a calendar page left
# open would otherwise knock every couple of minutes with a password known to
# be wrong, and providers lock accounts for that. A changed password (or
# address, or user name) is a different login and is tried at once.
REFUSED_HOLD = 600
_refused = {}        # (calendar, account id) -> (mark of the login, when, the words)


def _mark(login):
    return hashlib.sha256('\n'.join(login).encode('utf-8')).hexdigest()[:16]


def login_of(row):
    """The login a stored account is reached with. Raises (without asking
    the server) while that same login is on hold after a refusal."""
    login = caldav.Login(row['url'], row['username'], _password(row))
    held = _refused.get((row['calendar'], row['id']))
    if held and held[0] == _mark(login) and time.time() - held[1] < REFUSED_HOLD:
        raise CalError(f"{held[2]} Not trying again for a few minutes, so the account is not locked out: "
                       "open Accounts and fix the login.")
    return login


def refused(row, login, words):
    _refused[(row['calendar'], row['id'])] = (_mark(login), time.time(), str(words))


def forgive(calendar, account_id):
    _refused.pop((calendar, account_id), None)


def typed(body, old=None):
    """What a person typed into the form -> (fields to store, the Login to
    try, the password to store or None). `old`: the account being changed; a
    blank password then keeps the stored one, but only for the same server
    and user name (a stored password is never sent anywhere new)."""
    b = body or {}
    chosen = preset(b.get('preset') or (old or {}).get('preset'))
    username = ' '.join(str(b.get('username') or '').split()) if 'username' in b else (old or {}).get('username', '')
    if not username or len(username) > 200:
        raise CalError(f"Type the {chosen['user_label'].lower()}.")
    server = str(b.get('server') or '').strip()
    if chosen['ask_server']:
        if not server and old:
            url = old['url']
        else:
            url = chosen['url'].replace('{server}', caldav.clean_url(server).rstrip('/'))
    else:
        url = chosen['url'].replace('{user}', username)
    url = caldav.clean_url(url)
    email_account = str(b.get('email_account') or '').strip()
    email_address = ''
    password = str(b.get('password') or '')
    stored = None
    if email_account:
        mail = next((m for m in email_logins() if m['scope'] == email_account), None)
        if mail is None:
            raise CalError("That email account has no password to share. Type a password instead.")
        try:
            secret = _creds().get_email_account(email_account).get('app_password') or ''
        except Exception:
            secret = ''
        if not secret:
            raise CalError("That email account's password cannot be read. Type a password instead.")
        email_address = mail['address']
    elif password:
        secret, stored = password, password
    elif old and old['url'] == url and old['username'] == username and not old['email_account']:
        secret = _password(old)
    elif old:
        raise CalError("Type the password to use it with a changed address or user name. "
                       "The saved one is only sent to the server it was saved for.")
    else:
        raise CalError("Type the password (an app password where the provider asks for one).")
    label = ' '.join(str(b.get('label') or '').split())[:40] or (old or {}).get('label') or chosen['label']
    fields = {'provider': 'caldav', 'preset': chosen['id'], 'label': label, 'url': url, 'username': username,
              'email_account': email_account, 'email_address': email_address}
    return fields, caldav.Login(url, username, secret), stored


def save(calendar, account_id, fields, password=None):
    """Store an account (a new one when account_id is None); returns its row."""
    can_hold(calendar)
    creds = _creds()
    if account_id is None:
        for _ in range(20):
            account_id = secrets.choice(_ID_LETTERS) + ''.join(secrets.choice(_ID_REST) for _ in range(5))
            if not creds.has_plugin_account(PLUGIN, _key(calendar, account_id)):
                break
    secret = {'password': ''} if fields.get('email_account') else ({'password': password} if password else None)
    if not creds.set_plugin_account(PLUGIN, _key(calendar, account_id), fields=fields, secrets=secret):
        raise CalError("The account could not be saved (the credentials file refused the write).")
    return get(calendar, account_id)


def remove(calendar, account_id):
    forgive(calendar, account_id)
    try:
        return bool(_creds().delete_plugin_account(PLUGIN, _key(calendar, account_id)))
    except Exception as e:
        logger.warning(f"[calendar] account not removed: {e}")
        return False


def remove_all(calendar):
    for row in rows(calendar):
        remove(calendar, row['id'])
