# core/plugins/calendar/connect.py - a calendar's accounts, and what each offers
"""
What the Accounts panel shows and does (routes/accounts.py is its door; the
panel is web/index.js, drawn both in Settings and on the Calendar page).

The shape, top down:

    a calendar            what a chat picks; its events live on this machine
      an account          one login at one provider (accounts.py), or Google
        its calendars     each can be linked in (a link, store.py) or left out
      calendar addresses  read-only .ics links; no login, so no account

Adding an account tries the login at once, links every calendar it has, and
(as connecting Google always has) makes the first one it can write to the
place new events go, unless a place was already chosen.

The page names a calendar of an account by a short key (caldav.key_of), never
by its address: the address names the person's account at the provider, and
the page has no use for it.
"""
import logging
import threading
import time

from . import accounts, api, caldav, google, store, sync
from .errors import CalError

logger = logging.getLogger(__name__)

OFFER_SECONDS = 60            # how long an account's list of calendars (or the reason it has none) is believed
MAX_LINKED_AT_ONCE = 25
_offers = {}                  # (calendar, account id | 'google') -> (when, [what the server listed], CalError | None)
_changing = threading.Lock()  # one account is added or changed at a time: a double press cannot make two


def _offered(row, fresh=False):
    """What a CalDAV account lists, asked at most once a minute, a failure included:
    a page that polls must not knock on a server that is down every few seconds."""
    key = (row['calendar'], row['id'])
    hit = _offers.get(key)
    if hit and not fresh and time.time() - hit[0] < OFFER_SECONDS:
        if hit[2] is not None:
            raise hit[2]
        return hit[1]
    try:
        login = accounts.login_of(row)
        try:
            found = caldav.discover(login)['calendars']
        except caldav.Refused as e:
            accounts.refused(row, login, e)
            raise
    except CalError as e:
        _offers[key] = (time.time(), [], e)
        raise
    _offers[key] = (time.time(), found, None)
    return found


def _google_offered(calendar, fresh=False):
    key = (calendar, 'google')
    hit = _offers.get(key)
    if hit and not fresh and time.time() - hit[0] < OFFER_SECONDS:
        if hit[2] is not None:
            raise hit[2]
        return hit[1]
    try:
        found = google.calendar_list(calendar)
    except CalError as e:
        _offers[key] = (time.time(), [], e)
        raise
    _offers[key] = (time.time(), found, None)
    return found


def _mine(calendar, account_id):
    return [l for l in store.get().links(calendar) if l['kind'] == 'caldav' and l.get('account') == account_id]


def _hosts(fields, found):
    return sorted({caldav.host_of(fields['url'])} | {caldav.host_of(c['ref']) for c in found} - {''})


def _link_all(calendar, row, found):
    linked = []
    have = {l['ref'] for l in _mine(calendar, row['id'])}
    for offered in [c for c in found if c['ref'] not in have][:MAX_LINKED_AT_ONCE]:
        try:
            linked.append(api.add_caldav(calendar, row['id'], offered, hint=row['label'], wait=False))
        except CalError as e:
            logger.info(f"[calendar] a calendar of the account was not linked: {e}")
    s = store.get()
    if not s.meta(f"target:{calendar}"):
        first = next((l for l in linked if l['writable']), None)
        if first:
            s.set_meta(f"target:{calendar}", first['id'])
    return linked


def add_account(calendar, body):
    """A CalDAV account from what the person typed. The login is tried here,
    so a wrong one is refused now and never stored."""
    calendar = api._need_calendar(calendar)
    accounts.can_hold(calendar)
    fields, login, password = accounts.typed(body)
    with _changing:
        same = [r for r in accounts.rows(calendar)
                if r['url'] == fields['url'] and r['username'].lower() == fields['username'].lower()]
        if same:
            raise CalError(f"That account is already in this calendar (as '{same[0]['label']}').")
        found = caldav.discover(login)['calendars']
        if not found:
            raise CalError("That login works, but it has no calendars for events.")
        fields['hosts'] = _hosts(fields, found)
        row = accounts.save(calendar, None, fields, password)
        _offers[(calendar, row['id'])] = (time.time(), found, None)
        _link_all(calendar, row, found)
    sync.refresh(calendar, wait=False)
    return row


def update_account(calendar, account_id, body):
    """Change an account's login or name. The new login is tried before it
    is stored. A link the new login's server does not list is taken off: it
    belonged to the old server, and the new password must never be sent there."""
    calendar = api._need_calendar(calendar)
    with _changing:
        row = accounts.get(calendar, account_id)
        if row is None:
            raise CalError("That account is already gone.")
        fields, login, password = accounts.typed(dict(body or {}, preset=row['preset']), old=row)
        found = caldav.discover(login)['calendars']
        moved = fields['url'] != row['url'] or fields['username'].lower() != row['username'].lower()
        fields['hosts'] = _hosts(fields, found)
        refs = {c['ref'] for c in found}
        for link in _mine(calendar, account_id):
            if link['ref'] not in refs:
                api.remove_link(link['id'])
        accounts.forgive(calendar, account_id)
        row = accounts.save(calendar, account_id, fields, password)
        _offers[(calendar, account_id)] = (time.time(), found, None)
        if moved:
            _link_all(calendar, row, found)
        s = store.get()
        for link in _mine(calendar, account_id):
            s.update_link(link['id'], {'error': ''})
    sync.refresh(calendar, force=True, wait=False)
    return row


def remove_account(calendar, account_id):
    """Take an account out: its links and their copies go; nothing is touched at the server."""
    calendar = api._need_calendar(calendar)
    with _changing:
        if accounts.get(calendar, account_id) is None:
            raise CalError("That account is already gone.")
        removed = sum(api.remove_link(link['id']) for link in _mine(calendar, account_id))
        accounts.remove(calendar, account_id)
        _offers.pop((calendar, account_id), None)
    return removed


def link_calendar(calendar, account_id, key):
    """Tick one calendar of a CalDAV account. `key` names one the account itself lists (caldav.key_of)."""
    calendar = api._need_calendar(calendar)
    row = accounts.get(calendar, account_id)
    if row is None:
        raise CalError("That account is gone.")
    pick = lambda found: next((c for c in found if caldav.key_of(c['ref']) == str(key)), None)
    offered = pick(_offered(row)) or pick(_offered(row, fresh=True))
    if offered is None:
        raise CalError("That calendar is not in this account (any more).")
    if not accounts.allows(row, offered['ref']):        # the account's server has moved it to another of its machines
        row = accounts.save(calendar, account_id, {'hosts': sorted(set(row['hosts']) | {caldav.host_of(offered['ref'])})})
    return api.add_caldav(calendar, account_id, offered, hint=row['label'])


def view(calendar, fresh=False):
    """Everything the Accounts panel shows for one calendar."""
    calendar = api._need_calendar(calendar)
    raw = store.get().links(calendar)
    shown = {v['id']: v for v in api.links(calendar)}
    placed = set()
    out = []

    def entry(key, name, writable, link):
        if link:
            placed.add(link['id'])
        return {'key': key, 'name': name, 'writable': bool(writable), 'link': shown.get(link['id']) if link else None}

    def trouble(e):
        if isinstance(e, CalError):
            return str(e)
        logger.error(f"[calendar] an account's calendars could not be listed: {type(e).__name__}", exc_info=True)
        return f"Its calendars could not be listed ({type(e).__name__})."

    glinks = [l for l in raw if l['kind'] == 'google']
    connected = google.connected(calendar)
    if connected or glinks:
        g = {'id': 'google', 'provider': 'google', 'preset': 'google', 'label': 'Google', 'detail': '',
             'connected': connected, 'error': '', 'calendars': []}
        if connected:
            try:
                acct = next((a for a in google.accounts() if a['scope'] == calendar), {'calendar_id': 'primary'})
                for c in _google_offered(calendar, fresh):
                    link = next((l for l in glinks if l['ref'] == c['id']
                                 or (c['primary'] and l['ref'] in ('primary', acct['calendar_id']))), None)
                    g['calendars'].append(entry(c['id'], c['name'], c['writable'], link))
            except Exception as e:
                g['error'] = trouble(e)
        else:
            g['error'] = "Google is not connected any more. Connect it again, or untick these."
        for l in glinks:                              # linked, but not in what Google listed: still shown, to untick
            if l['id'] not in placed:
                g['calendars'].append(entry(l['ref'], l['name'], l['writable'], l))
        out.append(g)

    for row in accounts.rows(calendar):
        mine = [l for l in raw if l['kind'] == 'caldav' and l.get('account') == row['id']]
        a = {'id': row['id'], 'provider': 'caldav', 'preset': row['preset'], 'label': row['label'],
             'detail': row['username'], 'host': caldav.host_of(row['url']), 'username': row['username'],
             # what the person typed as the server, for the edit form (never a calendar's own address)
             'server': row['url'] if accounts.preset(row['preset'])['ask_server'] else '',
             'email_account': row['email_account'], 'connected': True, 'error': '', 'calendars': []}
        try:
            for c in _offered(row, fresh):
                a['calendars'].append(entry(caldav.key_of(c['ref']), c['name'], c['writable'],
                                            next((l for l in mine if l['ref'] == c['ref']), None)))
        except Exception as e:
            a['error'] = trouble(e)
        for l in mine:
            if l['id'] not in placed:
                a['calendars'].append(entry(caldav.key_of(l['ref']), l['name'], l['writable'], l))
        out.append(a)

    target = api.target_of(calendar)
    return {
        'calendar': calendar, 'calendars': [c['name'] for c in api.calendars()], 'accounts': out,
        'addresses': [shown[l['id']] for l in raw if l['kind'] == 'ics' and l['id'] in shown],
        # a link whose account was removed some other way: shown so it can be taken off
        'loose': [shown[l['id']] for l in raw if l['kind'] != 'ics' and l['id'] not in placed and l['id'] in shown],
        'target': target['id'] if target else '',
        'targets': [shown[l['id']] for l in raw if l['writable'] and l['id'] in shown],
        'presets': [{k: p[k] for k in ('id', 'label', 'ask_server', 'user_label', 'help')} for p in accounts.PRESETS],
        'email_logins': accounts.email_logins(),
        'refreshing': any(v['refreshing'] for v in shown.values()),
    }
