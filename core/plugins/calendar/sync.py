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

A read and a write of the same link can overlap: a read that asked the source
BEFORE a change was made there, and stores its answer AFTER, would undo the
change in the copy (drop the new event, bring a deleted one back). So every
write to a link is counted (wrote), and a read that finds the count moved
since it asked throws its answer away and leaves the link due for another.

A Google or CalDAV link copies a window of days, and the window grows when
someone looks further out. An ICS link is the whole file. What each kind of
link does to read is its own module's (sources.py).
"""
import logging
import threading
import time
from datetime import date, timedelta

from . import google, sources, store
from .errors import CalError

logger = logging.getLogger(__name__)

PAST_DAYS, AHEAD_DAYS = 62, 400
MAX_REACH_DAYS = 3660

_guard = threading.Lock()
_locks: dict = {}
_tried: dict = {}
_running: set = set()
_writes: dict = {}            # link id -> how many changes were made through it
_store_locks: dict = {}       # link id -> held while the copy is made to match an answer
_linking = threading.Lock()   # one caller at a time links a Google account that just appeared


def _api():
    from . import api
    return api


def _lock_for(link_id):
    with _guard:
        return _locks.setdefault(link_id, threading.Lock())


def storing(link_id):
    """The lock a link's copy is changed under: a read storing its answer, or
    a write storing what it just sent. Short; never held across the network."""
    with _guard:
        return _store_locks.setdefault(link_id, threading.Lock())


def wrote(link_id):
    """A change is being made (or was just made) through this link."""
    with _guard:
        _writes[link_id] = _writes.get(link_id, 0) + 1


def running(link_id=None):
    with _guard:
        return bool(_running) if link_id is None else link_id in _running


def ensure_google():
    """A Google account connected under a calendar's name is linked into that
    calendar, and becomes where its new events go unless the person chose.
    Not when the person removed the link by hand (the mark is 'off')."""
    s = store.get()
    with _linking:
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
    src = sources.BY_KIND.get(link['kind'])
    if link['error'] and now - _tried.get(link['id'], 0) < (src.RETRY if src else 300):
        return False
    if now - link['synced_at'] > (src.EVERY if src else 900):
        return True
    if need and src is not None and src.WINDOWED:
        lo, hi = _window(link, None, today)
        # days past the furthest reach can never be copied: asking for them must not read forever
        reach = timedelta(days=MAX_REACH_DAYS)
        first, last = max(need[0], today - reach), min(need[1], today + reach)
        return first <= last and (first < lo or last > hi)
    return False


def pull(link, need=None, force=False):
    """Copy one link's events now and return the link as it is after.
    Raises CalError, which is also left on the link for the page to show.
    `force`: read it all again even when the source says nothing changed."""
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
            with _guard:
                seen = _writes.get(link['id'], 0)
            got = sources.of(link).pull(link, lo, hi, force)
            if got is not None:                       # None: the source says nothing changed
                rows, note, done = got
                with storing(link['id']):
                    with _guard:
                        moved = _writes.get(link['id'], 0) != seen
                    if moved:                         # a change was made meanwhile: this answer may predate it
                        return s.get_link(link['id'])
                    added, changed, removed = s.sync_rows(link, rows)
                    if done:
                        done()
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


def _pull_all(todo, need, force=False):
    failed = []
    for link in todo:
        try:
            pull(link, need, force)
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
        return _pull_all(todo, need, force)
    todo = [l for l in todo if not running(l['id'])]
    if todo:
        with _guard:
            _running.update(l['id'] for l in todo)       # the page sees "refreshing" from this answer on
        threading.Thread(target=_pull_all, args=(todo, need, force), name='calendar-sync', daemon=True).start()
    return []
