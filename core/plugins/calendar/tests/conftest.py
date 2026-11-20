"""The calendar tests' kit. Every test runs on a database in tmp_path with the
clock pinned; the real database, the credentials file and the network are
never touched (Google is the FakeGoogle below, a CalDAV server the FakeDav, an
ICS address is a string, and the credentials file a dict)."""
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


class FakeDav:
    """A CalDAV server as the plugin's caldav.py offers it: the calendars of
    one login, event files by address, the calls made, and switches to make
    it fail. Every file gets a new version mark when it is written."""

    BASE = 'https://dav.example/u/'

    def __init__(self):
        self.calendars = [{'ref': self.BASE + 'home/', 'name': 'Home', 'writable': True},
                          {'ref': self.BASE + 'team/', 'name': 'Team', 'writable': False}]
        self.files = {}                  # address -> (text, version mark)
        self.calls = []
        self.password = 'app-pass'
        self.down = None                 # a CalError text: every call fails with it
        self._n = 0

    def _check(self, login, *call):
        from plugins.calendar import caldav
        from plugins.calendar.errors import CalError
        self.calls.append(call)
        if self.down:
            raise CalError(self.down)
        if login.password != self.password:
            raise caldav.Refused("dav.example refused the login.")

    def discover(self, login):
        self._check(login, 'discover', login.url, login.user)
        return {'home': self.BASE, 'calendars': [dict(c) for c in self.calendars]}

    def ctag(self, login, calendar):
        self._check(login, 'ctag', calendar)
        return f"mark-{self._n}"

    def events(self, login, calendar, first, last):
        self._check(login, 'events', calendar)
        return [(href, mark, text) for href, (text, mark) in self.files.items() if href.startswith(calendar)]

    def get(self, login, href):
        from plugins.calendar import caldav
        self._check(login, 'get', href)
        if href not in self.files:
            raise caldav.Gone("dav.example has nothing at that address (any more).")
        return self.files[href]

    def put(self, login, href, text, etag='', new=False):
        from plugins.calendar import caldav
        self._check(login, 'put', href, etag, new)
        if (new and href in self.files) or (etag and self.files.get(href, ('', ''))[1] != etag):
            raise caldav.Changed("That event was changed somewhere else a moment ago. Look at it again, then repeat the change.")
        self._n += 1
        self.files[href] = (text, f'"v{self._n}"')
        return self.files[href][1]

    def delete(self, login, href):
        self._check(login, 'delete', href)
        self._n += 1
        self.files.pop(href, None)

    def add(self, name, text, calendar='home/'):
        """A file someone else's app put on the server."""
        self._n += 1
        self.files[self.BASE + calendar + name] = (text.replace('\n', '\r\n'), f'"v{self._n}"')
        return self.BASE + calendar + name

    def text(self, href=None):
        """One file's text, lines unfolded (the only file when no address is given)."""
        from plugins.calendar import ics
        if href is None:
            href = next(iter(self.files))
        return '\n'.join(ics._lines(self.files[href][0]))

    def named(self, name):
        return [c for c in self.calls if c and c[0] == name]


class FakeCreds:
    """The credentials manager's plugin account store, and its email accounts, as a dict."""

    def __init__(self):
        self.book, self.mail = {}, {}

    def list_plugin_accounts(self, plugin):
        return [{'name': n, 'fields': dict(a['fields']), 'secrets': sorted(a['secrets'])} for n, a in sorted(self.book.items())]

    def get_plugin_account(self, plugin, name):
        a = self.book.get(name)
        return {'name': name, 'fields': dict(a['fields']), 'secrets': dict(a['secrets'])} if a else {}

    def has_plugin_account(self, plugin, name):
        return name in self.book

    def set_plugin_account(self, plugin, name, fields=None, secrets=None):
        a = self.book.setdefault(name, {'fields': {}, 'secrets': {}})
        a['fields'].update(fields or {})
        for k, v in (secrets or {}).items():
            if v == '':
                a['secrets'].pop(k, None)
            elif v is not None:
                a['secrets'][k] = v
        return True

    def delete_plugin_account(self, plugin, name):
        return self.book.pop(name, None) is not None

    def list_email_accounts(self):
        return [{'scope': s, 'address': m['address'], 'auth_type': m.get('auth_type', 'password')} for s, m in self.mail.items()]

    def get_email_account(self, scope):
        return dict(self.mail.get(scope) or {'address': '', 'app_password': ''})


@pytest.fixture
def cal(tmp_path, monkeypatch):
    from core.chat.function_manager import register_plugin_scope
    register_plugin_scope('gcal', plugin_name='calendar')     # plugin tests run under the root conftest
    from core.chat.function_manager import scope_gcal, scope_private
    from plugins.calendar import accounts, api, caldav, connect, dav, google, ics, icsw, repeat, store, sync
    from plugins.calendar.tools import calendar_tools as tools
    import plugins.calendar.routes.events as routes
    import plugins.calendar.routes.links as link_routes
    import plugins.calendar.routes.accounts as account_routes
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
    creds = FakeCreds()
    monkeypatch.setattr(accounts, '_creds', lambda: creds)             # no test reaches the credentials file
    monkeypatch.setattr(accounts, '_refused', {})
    monkeypatch.setattr(connect, '_offers', {})
    monkeypatch.setattr(caldav, '_digest_hosts', set())
    monkeypatch.setattr(sync, '_writes', {})
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

        def use_dav(self, add=True):
            """Put the FakeDav on the wire; add=True also adds its account to 'default' (both calendars linked)."""
            d = FakeDav()
            for name in ('discover', 'ctag', 'events', 'get', 'put', 'delete'):
                monkeypatch.setattr(caldav, name, getattr(d, name))
            from zoneinfo import ZoneInfo
            monkeypatch.setattr(api, '_zone', lambda: ZoneInfo('America/New_York'))
            # a refresh nobody waits for runs at once here: no thread outlives a test
            real = sync.refresh
            monkeypatch.setattr(sync, 'refresh', lambda calendar=None, need=None, force=False, wait=True:
                                real(calendar, need, force, True) if wait else (real(calendar, need, force, True), [])[1])
            self.d = d
            if add:
                self.account = connect.add_account('default', {'preset': 'caldav', 'server': 'https://dav.example',
                                                               'username': 'ann', 'password': 'app-pass'})
                self.home = next(l for l in db.links('default') if l['name'] == 'Home')
            return d

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
    kit.accounts, kit.caldav, kit.connect, kit.dav, kit.icsw, kit.creds = accounts, caldav, connect, dav, icsw, creds
    kit.account_routes = account_routes
    yield kit
    scope_gcal.reset(scope_tok)
    scope_private.reset(private_tok)
    db.close()
