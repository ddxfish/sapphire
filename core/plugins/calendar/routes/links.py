# core/plugins/calendar/routes/links.py - linked calendars, for the Calendar page
#
# A link feeds one calendar from somewhere else: a Google calendar (two-way)
# or an ICS address (read-only). Only the person makes or removes a link;
# there is no tool for it, so nothing she reads can point her calendar at a
# new address.
import logging

from plugins.calendar import api

logger = logging.getLogger(__name__)


def _refused(e):
    return {'success': False, 'detail': str(e)}, 400


def _state(calendar):
    """Everything the Calendars dialog shows for one calendar."""
    info = {'connected': False, 'calendars': []}
    google_error = ''
    try:
        info = api.google_calendars(calendar)
    except api.CalError as e:
        info = {'connected': True, 'calendars': []}
        google_error = str(e)
    target = api.target_of(api._need_calendar(calendar))
    return {'success': True, 'calendar': calendar, 'links': api.links(calendar),
            'target': target['id'] if target else '', 'google': dict(info, error=google_error),
            'account': _account(calendar)}


def _account(calendar):
    """The saved Google account row of this calendar, without its secrets."""
    try:
        from core.credentials_manager import credentials
        row = next((a for a in credentials.list_gcal_accounts() if a.get('scope') == calendar), None)
    except Exception as e:
        logger.debug(f"[calendar] Google account row not read: {e}")
        row = None
    return {'client_id': (row or {}).get('client_id', ''), 'calendar_id': (row or {}).get('calendar_id', 'primary')}


def get_links(query=None, **_):
    """GET links?calendar=name"""
    calendar = (query or {}).get('calendar') or 'default'
    try:
        return _state(calendar)
    except api.CalError as e:
        return _refused(e)


def add_link(body=None, **_):
    """POST links {calendar, kind: 'ics', url, name} | {calendar, kind: 'google', google_id, name}"""
    b = body or {}
    calendar = b.get('calendar') or 'default'
    try:
        if b.get('kind') == 'ics':
            api.add_ics(calendar, b.get('url'), b.get('name') or '')
        elif b.get('kind') == 'google':
            api.add_google(calendar, b.get('google_id'), b.get('name') or '')
        else:
            return {'success': False, 'detail': "kind is 'ics' or 'google'."}, 400
        return _state(calendar)
    except api.CalError as e:
        return _refused(e)


def update_link(link_id, body=None, **_):
    """PUT links/{id} {name?, color?}"""
    try:
        link = api.update_link(link_id, body or {})
        return _state(link['calendar'])
    except api.CalError as e:
        return _refused(e)


def delete_link(link_id, **_):
    """DELETE links/{id} - unlink; the copies go, the source is untouched."""
    try:
        calendar = next((l['calendar'] for l in api.links() if l['id'] == link_id), 'default')
        removed = api.remove_link(link_id)
        return dict(_state(calendar), removed_events=removed)
    except api.CalError as e:
        return _refused(e)


def set_target(body=None, **_):
    """POST target {calendar, to: 'local' | link id} - where new events go."""
    b = body or {}
    calendar = b.get('calendar') or 'default'
    try:
        api.set_target(calendar, b.get('to') or 'local')
        return _state(calendar)
    except api.CalError as e:
        return _refused(e)


def sync_now(body=None, **_):
    """POST sync {calendar?} - refresh every link now and wait for it."""
    calendar = (body or {}).get('calendar') or None
    try:
        failed = api.refresh(calendar, force=True, wait=True)
    except api.CalError as e:
        return _refused(e)
    return {'success': True, 'failed': [{'name': l['name'], 'detail': why} for l, why in failed],
            'links': api.links(calendar)}
