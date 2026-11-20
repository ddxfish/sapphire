# core/plugins/calendar/routes/accounts.py - the Accounts panel's doors
#
# The panel (web/index.js) is drawn in Settings > Plugins > Calendar and on the
# Calendar page; both talk to these. Only a person adds, changes or removes an
# account: there is no tool for it, so nothing she reads can hand her calendar
# a new login or point it at a new server.
import logging

from plugins.calendar import api, connect

logger = logging.getLogger(__name__)


def _refused(e):
    return {'success': False, 'detail': str(e)}, 400


def _state(calendar, fresh=False, **more):
    out = connect.view(calendar, fresh)
    from plugins.calendar.routes.links import _account
    out['google_client'] = _account(out['calendar'])
    return dict(out, success=True, **more)


def _calendar(body=None, query=None):
    return (body or {}).get('calendar') or (query or {}).get('calendar') or 'default'


def get_accounts(query=None, **_):
    """GET accounts?calendar=name"""
    try:
        return _state(_calendar(query=query), fresh=str((query or {}).get('fresh') or '').lower() in ('1', 'true', 'yes'))
    except api.CalError as e:
        return _refused(e)


def add_account(body=None, **_):
    """POST accounts {calendar, preset, server?, username, password? | email_account?, label?}"""
    calendar = _calendar(body)
    try:
        row = connect.add_account(calendar, body or {})
        return _state(calendar, added=row['id'])
    except api.CalError as e:
        return _refused(e)


def update_account(account_id, body=None, **_):
    """PUT accounts/{id} - the same fields; a blank password keeps the stored one."""
    calendar = _calendar(body)
    try:
        connect.update_account(calendar, account_id, body or {})
        return _state(calendar)
    except api.CalError as e:
        return _refused(e)


def delete_account(account_id, body=None, query=None, **_):
    """DELETE accounts/{id}?calendar=name - its links and their copies go; the server is untouched."""
    calendar = _calendar(body, query)
    try:
        removed = connect.remove_account(calendar, account_id)
        return _state(calendar, removed_events=removed)
    except api.CalError as e:
        return _refused(e)


def get_config(settings=None, **_):
    """GET config - the plugin's own settings and their form, for the Settings card."""
    from core.plugin_loader import plugin_loader
    info = plugin_loader.get_plugin_info('calendar') or {}
    schema = ((info.get('manifest') or {}).get('capabilities') or {}).get('settings') or []
    return {'schema': schema, 'values': settings or {}}
