"""The calendar tests' kit. Every test runs on a database in tmp_path with the
clock pinned; the real database, the credentials file and the network are
never touched (Google is the FakeGoogle below, an ICS address is a string)."""
from datetime import date, datetime, timedelta

import pytest

TODAY = date(2026, 11, 18)          # a Wednesday


class FakeGoogle:
    """Google Calendar as the plugin's google.py sees it: events per calendar
    id, the calls made, and a switch to make it fail."""

    def __init__(self):
        self.items = {'primary': {}}
        self.calls = []
        self.down = None                 # a CalError text: every call fails with it
        self.accounts = ['default']
        self.lists = [{'id': 'primary', 'name': 'Me', 'primary': True, 'writable': True},
                      {'id': 'team@group', 'name': 'Team', 'primary': False, 'writable': False}]
        self._n = 0

    def _check(self):
        if self.down:
            from plugins.calendar.errors import CalError
            raise CalError(self.down)

    def put(self, cal_id='primary', **item):
        self._n += 1
        item.setdefault('id', f"g{self._n}")
        item.setdefault('summary', 'Event')
        self.items.setdefault(cal_id, {})[item['id']] = item
        return item

    @staticmethod
    def timed(day, start='09:00', end='10:00', offset='-05:00'):
        return {'start': {'dateTime': f"{day}T{start}:00{offset}"}, 'end': {'dateTime': f"{day}T{end}:00{offset}"}}

    @staticmethod
    def all_day(first, last=None):
        end = date.fromisoformat(last or first) + timedelta(days=1)
        return {'start': {'date': first}, 'end': {'date': end.isoformat()}}

    # -- what google.py offers ------------------------------------------------
    def instances(self, scope, cal_id, time_min, time_max, tz=''):
        self.calls.append(('instances', scope, cal_id, time_min, time_max))
        self._check()
        return [i for i in self.items.get(cal_id, {}).values() if 'recurrence' not in i]

    def get(self, scope, cal_id, event_id):
        self.calls.append(('get', scope, cal_id, event_id))
        self._check()
        return self.items[cal_id][event_id]

    def insert(self, scope, cal_id, body, notify=False):
        self.calls.append(('insert', scope, cal_id, body, notify))
        self._check()
        return self.put(cal_id, **{k: v for k, v in body.items() if v is not None})

    def patch(self, scope, cal_id, event_id, body, notify=False):
        self.calls.append(('patch', scope, cal_id, event_id, body, notify))
        self._check()
        item = self.items[cal_id][event_id]
        for k, v in body.items():
            if isinstance(v, dict) and isinstance(item.get(k), dict):
                merged = {**item[k], **v}
                item[k] = {a: b for a, b in merged.items() if b is not None}
            else:
                item[k] = v
        return item

    def delete(self, scope, cal_id, event_id, notify=False):
        self.calls.append(('delete', scope, cal_id, event_id, notify))
        self._check()
        gone = self.items[cal_id].pop(event_id, None)
        for key, item in list(self.items[cal_id].items()):          # a series takes its occurrences along
            if item.get('recurringEventId') == event_id:
                del self.items[cal_id][key]
        return bool(gone)

    def calendar_list(self, scope):
        self._check()
        return [dict(c) for c in self.lists]

    def named(self, name):
        return [c for c in self.calls if c[0] == name]


@pytest.fixture
def cal(tmp_path, monkeypatch):
    from core.chat.function_manager import register_plugin_scope
    register_plugin_scope('gcal', plugin_name='calendar')     # plugin tests run under the root conftest
    from core.chat.function_manager import scope_gcal, scope_private
    from plugins.calendar import api, google, ics, repeat, store, sync
    from plugins.calendar.tools import calendar_tools as tools
    import plugins.calendar.routes.events as routes
    import plugins.calendar.routes.links as link_routes
    db = store.Store(tmp_path / 'calendar_events.db')
    monkeypatch.setattr(store, 'DB_PATH', tmp_path / 'not-the-real-one.db')
    monkeypatch.setattr(store, '_store', db)
    monkeypatch.setattr(api, 'today', lambda: TODAY)
    monkeypatch.setattr(api, 'now', lambda: datetime(2026, 11, 18, 8, 30))
    monkeypatch.setattr(api, 'tz_name', lambda: '')
    monkeypatch.setattr(api, 'zone_name', lambda: 'America/New_York')
    monkeypatch.setattr(google, 'accounts', lambda: [])                 # no test reaches the credentials file
    monkeypatch.setattr(google, 'connected', lambda scope: False)
    monkeypatch.setattr(sync, '_tried', {})
    monkeypatch.setattr(sync, '_running', set())
    scope_tok = scope_gcal.set('default')
    private_tok = scope_private.set(False)

    class Kit:
        def use_google(self):
            """Connect the FakeGoogle under the 'default' calendar."""
            g = FakeGoogle()
            monkeypatch.setattr(google, 'accounts', lambda: [{'scope': s, 'calendar_id': 'primary'} for s in g.accounts])
            monkeypatch.setattr(google, 'connected', lambda scope: scope in g.accounts)
            for name in ('instances', 'get', 'insert', 'patch', 'delete', 'calendar_list'):
                monkeypatch.setattr(google, name, getattr(g, name))
            # the fake speaks -05:00; read it as New York time whatever the test machine's zone is
            from zoneinfo import ZoneInfo
            monkeypatch.setattr(api, '_zone', lambda: ZoneInfo('America/New_York'))
            self.g = g
            return g

        def use_ics(self, text):
            """Every calendar address answers with `text` (a str, or a CalError to raise)."""
            def fetch(url):
                self.fetched.append(url)
                if isinstance(text, Exception):
                    raise text
                return self.ics_text
            self.ics_text, self.fetched = text, []
            monkeypatch.setattr(ics, 'fetch', fetch)

    kit = Kit()
    kit.api, kit.repeat, kit.store, kit.tools, kit.routes, kit.db, kit.tmp = api, repeat, store, tools, routes, db, tmp_path
    kit.google, kit.ics, kit.sync, kit.links, kit.patch = google, ics, sync, link_routes, monkeypatch
    kit.scope, kit.private = scope_gcal, scope_private
    yield kit
    scope_gcal.reset(scope_tok)
    scope_private.reset(private_tok)
    db.close()
