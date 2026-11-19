# core/plugins/calendar/sync.py - keeping the copies of linked calendars fresh
"""
A link's events are copied here so the page opens at once and works offline;
the source stays the authority. pull() fetches one link and makes the copy
match (store.sync_rows keeps each row's id).

When a pull happens:
  - her tools ask for a refresh and WAIT for it before they read, so she never
    answers from a stale copy without being told;
  - the page asks without waiting, shows what is here, and paints again when
    the pull lands;
  - right after a change made here to a Google event.
There is no background timer: nothing is fetched while nobody is looking.

A Google link copies a window of days (repeats arrive already expanded), and
the window grows when someone looks further out. An ICS link is the whole file.
"""
import logging
import threading
import time
from datetime import date, timedelta

from . import google, ics, store
from .errors import CalError

logger = logging.getLogger(__name__)

EVERY = {'google': 60, 'ics': 900}        # seconds a copy counts as fresh
RETRY = {'google': 60, 'ics': 300}        # seconds before a failed pull is tried again
PAST_DAYS, AHEAD_DAYS = 62, 400
MAX_REACH_DAYS = 3660

_guard = threading.Lock()
_locks: dict = {}
_tried: dict = {}
_running: set = set()


def _api():
    from . import api
    return api


def _lock_for(link_id):
    with _guard:
        return _locks.setdefault(link_id, threading.Lock())


def running(link_id=None):
    with _guard:
        return bool(_running) if link_id is None else link_id in _running


def ensure_google():
    """A Google account connected under a calendar's name is linked into that
    calendar, and becomes where its new events go unless the person chose.
    Not when the person removed the link by hand (the mark is 'off')."""
    s = store.get()
    for acct in google.accounts():
        name = acct['scope']
        if s.meta(f"gauto:{name}") == 'off' or any(l['kind'] == 'google' for l in s.links(name)):
            continue
        link = s.add_link({'calendar': name, 'kind': 'google', 'name': 'Google', 'ref': acct['calendar_id'],
                           'color': 2, 'writable': True})
        if not s.meta(f"target:{name}"):
            s.set_meta(f"target:{name}", link['id'])
        logger.info(f"[calendar] linked the Google account of calendar '{name}'")


def _window(link, need, today):
    lo, hi = today - timedelta(days=PAST_DAYS), today + timedelta(days=AHEAD_DAYS)
    for key, pick in (('win_from', min), ('win_to', max)):
        try:
            if link[key]:
                if key == 'win_from':
                    lo = pick(lo, date.fromisoformat(link[key]))
                else:
                    hi = pick(hi, date.fromisoformat(link[key]))
        except ValueError:
            pass
    if need:
        lo, hi = min(lo, need[0]), max(hi, need[1])
    reach = timedelta(days=MAX_REACH_DAYS)
    return max(lo, today - reach), min(hi, today + reach)


def _stale(link, need, force, today):
    if force:
        return True
    now = time.time()
    if link['error'] and now - _tried.get(link['id'], 0) < RETRY.get(link['kind'], 300):
        return False
    if now - link['synced_at'] > EVERY.get(link['kind'], 900):
        return True
    if need and link['kind'] == 'google':
        lo, hi = _window(link, None, today)
        return need[0] < lo or need[1] > hi
    return False


def pull(link, need=None):
    """Copy one link's events now and return the link as it is after.
    Raises CalError, which is also left on the link for the page to show."""
    api = _api()
    s = store.get()
    with _lock_for(link['id']):
        link = s.get_link(link['id'])
        if link is None:
            return None
        with _guard:
            _running.add(link['id'])
        try:
            lo, hi = _window(link, need, api.today())
            note = ''
            if link['kind'] == 'google':
                items = google.instances(link['calendar'], link['ref'], api.rfc3339(lo),
                                         api.rfc3339(hi + timedelta(days=1)), api.zone_name())
                rows = [r for r in (google.to_row(i, api.to_local) for i in items) if r]
            elif link['kind'] == 'ics':
                _name, rows, notes = ics.parse(ics.fetch(link['ref']), api.convert, api.zone_name())
                note = ' '.join(notes)
            else:
                raise CalError(f"This link is of a kind ({link['kind']}) this version cannot read.")
            added, changed, removed = s.sync_rows(link, rows)
            if added or changed or removed:
                logger.info(f"[calendar] link '{link['name']}' ({link['kind']}): +{added} ~{changed} -{removed}")
            s.set_meta(f"note:{link['id']}", note)
            return s.update_link(link['id'], {'synced_at': time.time(), 'win_from': lo.isoformat(),
                                              'win_to': hi.isoformat(), 'error': ''})
        except CalError as e:
            _tried[link['id']] = time.time()
            s.update_link(link['id'], {'error': str(e)})
            raise
        except Exception as e:
            _tried[link['id']] = time.time()
            logger.error(f"[calendar] sync of link '{link['name']}' failed: {e}", exc_info=True)
            s.update_link(link['id'], {'error': f"Sync failed ({type(e).__name__})."})
            raise CalError(f"Could not refresh '{link['name']}' ({type(e).__name__}).")
        finally:
            with _guard:
                _running.discard(link['id'])


def _pull_all(todo, need):
    failed = []
    for link in todo:
        try:
            pull(link, need)
        except CalError as e:
            failed.append((link, str(e)))
    return failed


def refresh(calendar=None, need=None, force=False, wait=True):
    """Bring stale links up to date. wait=True: returns [(link, why)] for the
    ones that could not be reached (their old copy is still here).
    wait=False: starts it in the background and returns at once."""
    try:
        ensure_google()
    except Exception as e:
        logger.warning(f"[calendar] could not check for Google accounts: {e}")
    today = _api().today()
    todo = [l for l in store.get().links(calendar) if _stale(l, need, force, today)]
    if not todo:
        return []
    if wait:
        return _pull_all(todo, need)
    todo = [l for l in todo if not running(l['id'])]
    if todo:
        with _guard:
            _running.update(l['id'] for l in todo)       # the page sees "refreshing" from this answer on
        threading.Thread(target=_pull_all, args=(todo, need), name='calendar-sync', daemon=True).start()
    return []
