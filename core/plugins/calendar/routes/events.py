# core/plugins/calendar/routes/events.py - the Calendar page's doors
#
# Mounted at /api/plugin/calendar/... (login, CSRF and the rate meter are
# core's). Plain `def` on purpose: core threadpools sync handlers, so SQLite
# and a call to Google never run on the web loop. The page is the person, so
# it sees and edits every calendar; her tools are held to the chat's calendar
# scope instead.
import logging

from plugins.calendar import api

logger = logging.getLogger(__name__)


def _refused(e):
    return {'success': False, 'detail': str(e)}, 400


def _scope_changed(action, name):
    """Tell the chat sidebar's calendar dropdown the list changed."""
    try:
        from core.event_bus import publish, Events
        publish(Events.SCOPE_CHANGED, {'kind': 'gcal', 'action': action, 'name': name})
    except Exception as e:
        logger.debug(f"[calendar] scope change not announced: {e}")


def _calendars():
    out = []
    for c in api.calendars():
        n = c['events']
        out.append({'name': c['name'], 'events': n,
                    'label': f"{c['name']} ({n} event{'s' if n != 1 else ''})"})
    return out


def list_calendars(**_):
    """GET calendars - also what the chat sidebar's calendar dropdown reads."""
    try:
        from plugins.calendar import sync
        sync.ensure_google()                # a Google account's calendar shows before the page was ever opened
    except Exception as e:
        logger.debug(f"[calendar] Google accounts not checked: {e}")
    return {'calendars': _calendars()}


def put_calendar(name, **_):
    try:
        name = api.add_calendar(name)
    except api.CalError as e:
        return _refused(e)
    _scope_changed('added', name)
    return {'success': True, 'name': name, 'calendars': _calendars()}


def delete_calendar(name, **_):
    try:
        removed = api.delete_calendar(name)
    except api.CalError as e:
        return _refused(e)
    _scope_changed('deleted', name)
    return {'success': True, 'removed_events': removed, 'calendars': _calendars()}


def list_events(query=None, settings=None, **_):
    """GET events?start=YYYY-MM-DD&end=YYYY-MM-DD[&calendar=name] - repeats
    expanded. Linked calendars are refreshed in the background: this answers
    with what is here, and `refreshing` tells the page to ask again soon."""
    q = query or {}
    try:
        first = api.parse_day(q.get('start') or api.today(), 'start')
        last = api.parse_day(q.get('end') or first, 'end')
        calendar = q.get('calendar') or None
        api.refresh(calendar, need=(first, last), wait=False)
        events = api.list_range(first, last, calendar)
        links = api.links(calendar)
    except api.CalError as e:
        return _refused(e)
    week_start = str((settings or {}).get('week_start') or 'sunday').lower()
    return {'events': events, 'calendars': _calendars(), 'links': links,
            'refreshing': any(l['refreshing'] for l in links), 'today': api.today().isoformat(),
            'timezone': api.tz_name(), 'week_start': 'monday' if week_start == 'monday' else 'sunday'}


def create_event(body=None, **_):
    try:
        ev = api.create(body or {}, by='user')
    except api.CalError as e:
        return _refused(e)
    return {'success': True, 'event': api.describe(ev)}


def update_event(event_id, body=None, **_):
    """PUT events/{id} - with {"day": "YYYY-MM-DD"} only that occurrence of a repeating one."""
    body = dict(body or {})
    day = body.pop('day', None)
    try:
        ev = api.update(event_id, body, day=day)
    except api.CalError as e:
        return _refused(e)
    return {'success': True, 'event': api.describe(ev)}


def delete_event(event_id, body=None, query=None, **_):
    """DELETE events/{id} - the whole event; with {"day": "YYYY-MM-DD"} only
    that occurrence of a repeating one."""
    day = (body or {}).get('day') or (query or {}).get('day')
    try:
        kind, _ev = api.remove(event_id, day)
    except api.CalError as e:
        return _refused(e)
    return {'success': True, 'removed': kind}
