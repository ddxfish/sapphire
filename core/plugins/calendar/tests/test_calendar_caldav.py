"""Calendar plugin, CalDAV: the calendar-file writer, the wire client, accounts
(the login is tried before it is kept, a stored password only goes home), and
events written through to a server without losing what this calendar does not
model. Kit, FakeDav and FakeCreds: conftest.py."""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from test_calendar_plugin import _run

SHARED = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Other App//EN
BEGIN:VTIMEZONE
TZID:Europe/London
BEGIN:STANDARD
DTSTART:19701025T020000
TZOFFSETFROM:+0100
TZOFFSETTO:+0000
RRULE:FREQ=YEARLY;BYMONTH=10;BYDAY=-1SU
END:STANDARD
END:VTIMEZONE
BEGIN:VEVENT
UID:standup-1
SUMMARY:Standup
DTSTART;TZID=Europe/London:20261005T140000
DTEND;TZID=Europe/London:20261005T141500
RRULE:FREQ=WEEKLY;UNTIL=20261221T140000Z
EXDATE;TZID=Europe/London:20261109T140000
ORGANIZER;CN=Ann Lee:mailto:ann@example.com
ATTENDEE;CN=Bo Chan;PARTSTAT=ACCEPTED:mailto:bo@example.com
ATTENDEE;CUTYPE=ROOM;CN=Room 4:mailto:room4@example.com
ATTENDEE;PARTSTAT=TENTATIVE:mailto:cy@example.com
X-OTHER-APP-COLOR:teal
BEGIN:VALARM
ACTION:DISPLAY
TRIGGER:-PT10M
DESCRIPTION:not the event's description
END:VALARM
END:VEVENT
BEGIN:VEVENT
UID:standup-1
RECURRENCE-ID;TZID=Europe/London:20261116T140000
SUMMARY:Standup (retro)
DTSTART;TZID=Europe/London:20261116T160000
DTEND;TZID=Europe/London:20261116T170000
END:VEVENT
END:VCALENDAR
"""


def _days(cal, first='2026-11-16', last='2026-12-06'):
    return [(o['start'], o['title']) for o in cal.api.list_range(first, last, 'default')]


# -- the calendar-file writer ----------------------------------------------------------------

def test_a_file_goes_back_as_it_came_except_what_was_changed(cal):
    w = cal.icsw
    doc = w.load(SHARED)
    master = doc.comps('VEVENT')[0]
    w.apply(doc, master, {'title': 'Daily; sync, now\nwith a second line'}, 'America/New_York', None)
    out = '\n'.join(cal.ics._lines(w.dump(doc)))
    assert 'SUMMARY:Daily\\; sync\\, now\\nwith a second line' in out          # nothing typed can start a new line
    for kept in ('X-OTHER-APP-COLOR:teal', 'TRIGGER:-PT10M', "DESCRIPTION:not the event's description",
                 'ATTENDEE;CN=Bo Chan;PARTSTAT=ACCEPTED:mailto:bo@example.com', 'ORGANIZER;CN=Ann Lee:mailto:ann@example.com',
                 'RRULE:FREQ=WEEKLY;UNTIL=20261221T140000Z', 'SUMMARY:Standup (retro)', 'PRODID:-//Other App//EN'):
        assert kept in out, kept
    assert out.count('BEGIN:VALARM') == 1 and out.count('SUMMARY:') == 2


def test_long_lines_fold_between_characters_and_unfold_to_the_same_words(cal):
    w = cal.icsw
    words = 'Größe ' * 30 + '終わり'
    doc = w.new_event({'title': words, 'start': '2026-11-19T09:00', 'end': '2026-11-19T10:00', 'all_day': False},
                      'u1', 'America/New_York', None)
    wire = w.dump(doc)
    assert all(len(line.encode('utf-8')) <= 75 for line in wire.split('\r\n'))
    assert f'SUMMARY:{words}' in cal.ics._lines(wire)
    _name, rows, _notes = cal.ics.parse(wire, cal.api.convert, 'America/New_York')
    assert rows[0]['title'] == words[:200] and rows[0]['start'] == '2026-11-19T09:00'


def test_a_local_time_goes_out_with_its_zone_and_what_the_zone_means(cal):
    w = cal.icsw
    doc = w.new_event({'title': 'Call', 'start': '2026-11-19T09:00', 'end': '2026-11-19T10:00', 'all_day': False,
                       'rrule': 'FREQ=WEEKLY'}, 'u1', 'America/New_York', None)
    out = '\n'.join(cal.ics._lines(w.dump(doc)))
    assert 'DTSTART;TZID=America/New_York:20261119T090000' in out and 'RRULE:FREQ=WEEKLY' in out
    assert 'TZID:America/New_York' in out and 'RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=2SU' in out
    assert 'RRULE:FREQ=YEARLY;BYMONTH=11;BYDAY=1SU' in out and 'TZOFFSETFROM:-0500' in out and 'TZOFFSETTO:-0400' in out
    london = '\n'.join(cal.ics._lines(w.dump(w.vtimezone('Europe/London', 2026))))
    assert 'BYMONTH=3;BYDAY=-1SU' in london and 'BYMONTH=10;BYDAY=-1SU' in london         # the LAST Sunday, not the 4th
    india = w.dump(w.vtimezone('Asia/Kolkata', 2026))
    assert 'TZOFFSETTO:+0530' in india and 'DAYLIGHT' not in india
    assert w.vtimezone('Mars/Olympus', 2026) is None
    # no zone name known (a Windows machine that does not say): a UTC instant, never a bare local time
    utc = w.dump(w.new_event({'title': 'x', 'start': '2026-11-19T09:00', 'end': '2026-11-19T10:00', 'all_day': False}, 'u2', '',
                             lambda naive: naive.replace(tzinfo=timezone.utc)))
    assert 'DTSTART:20261119T090000Z' in utc and 'VTIMEZONE' not in utc
    day = w.dump(w.new_event({'title': 'x', 'start': '2026-11-20', 'end': '2026-11-21', 'all_day': True}, 'u3', 'America/New_York', None))
    assert 'DTSTART;VALUE=DATE:20261120' in day and 'DTEND;VALUE=DATE:20261122' in day   # the file's end day is exclusive


# -- the wire client -----------------------------------------------------------------------------

class Wire:
    """core.net.request, answering from a table of (METHOD, address) -> (status, headers, body)."""

    def __init__(self, table):
        self.table, self.sent = table, []

    def __call__(self, method, url, **kw):
        self.sent.append(SimpleNamespace(method=method, url=url, headers=kw.get('headers') or {}, data=kw.get('data'), kw=kw))
        status, headers, body = self.table.get((method, url), (404, {}, ''))
        return SimpleNamespace(status_code=status, headers=headers, close=lambda: None,
                               iter_content=lambda n: iter([body.encode('utf-8')]))


def _multi(*entries):
    out = ['<?xml version="1.0"?><d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav" '
           'xmlns:cs="http://calendarserver.org/ns/">']
    for href, props in entries:
        out.append(f'<d:response><d:href>{href}</d:href><d:propstat><d:prop>{props}</d:prop>'
                   '<d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>')
    return ''.join(out) + '</d:multistatus>'


CAL = '<d:resourcetype><d:collection/><c:calendar/></d:resourcetype>'
HOME = _multi(
    ('/u/ann/', '<d:resourcetype><d:collection/></d:resourcetype>'),
    ('/u/ann/home/', CAL + '<d:displayname>Home &amp; family</d:displayname><c:supported-calendar-component-set>'
                           '<c:comp name="VEVENT"/><c:comp name="VTODO"/></c:supported-calendar-component-set>'),
    ('/u/ann/tasks/', CAL + '<d:displayname>Tasks</d:displayname><c:supported-calendar-component-set>'
                            '<c:comp name="VTODO"/></c:supported-calendar-component-set>'),
    ('/u/ann/team%20shared/', CAL + '<d:current-user-privilege-set><d:privilege><d:read/></d:privilege>'
                                    '</d:current-user-privilege-set>'),
    ('/u/ann/inbox/', '<d:resourcetype><d:collection/><c:schedule-inbox/></d:resourcetype>'))


def test_the_calendars_of_a_login_are_found_from_the_server_address(cal, monkeypatch):
    c = cal.caldav
    wire = Wire({
        ('PROPFIND', 'https://dav.example/'): (207, {}, _multi(('/', '<d:current-user-principal><d:href>/p/ann/</d:href></d:current-user-principal>'))),
        ('PROPFIND', 'https://dav.example/p/ann/'): (207, {}, _multi(('/p/ann/', '<c:calendar-home-set><d:href>https://data.dav.example/u/ann/</d:href></c:calendar-home-set>'))),
        ('PROPFIND', 'https://data.dav.example/u/ann/'): (207, {}, HOME),
    })
    monkeypatch.setattr(c.net, 'request', wire)
    found = c.discover(c.Login('dav.example', 'ann@example.com', 'pässword'))
    assert found['calendars'] == [
        {'ref': 'https://data.dav.example/u/ann/home/', 'name': 'Home & family', 'writable': True},
        {'ref': 'https://data.dav.example/u/ann/team%20shared/', 'name': 'team shared', 'writable': False}]   # no tasks, no inbox
    first = wire.sent[0]
    assert first.headers['Depth'] == '0' and first.kw['allow_redirects'] is False
    import base64
    assert base64.b64decode(first.headers['Authorization'][6:]).decode('utf-8') == 'ann@example.com:pässword'
    assert [s.headers['Depth'] for s in wire.sent] == ['0', '0', '1']


def test_other_ways_in_the_well_known_address_a_bare_folder_and_one_calendar(cal, monkeypatch):
    c = cal.caldav
    login = c.Login('https://dav.example/somewhere', 'ann', 'pw')
    wire = Wire({
        ('PROPFIND', 'https://dav.example/.well-known/caldav'): (301, {'Location': '/dav/'}, ''),
        ('PROPFIND', 'https://dav.example/dav/'): (207, {}, _multi(('/dav/', '<c:calendar-home-set><d:href>/u/ann/</d:href></c:calendar-home-set>'))),
        ('PROPFIND', 'https://dav.example/u/ann/'): (207, {}, HOME),
    })
    monkeypatch.setattr(c.net, 'request', wire)
    assert [x['name'] for x in c.discover(login)['calendars']] == ['Home & family', 'team shared']
    one = Wire({('PROPFIND', 'https://dav.example/u/ann/home/'): (207, {}, _multi(('/u/ann/home/', CAL + '<d:displayname>Home</d:displayname>')))})
    monkeypatch.setattr(c.net, 'request', one)
    assert c.discover(c.Login('https://dav.example/u/ann/home/', 'ann', 'pw'))['calendars'] == [
        {'ref': 'https://dav.example/u/ann/home/', 'name': 'Home', 'writable': True}]
    web = Wire({('PROPFIND', 'https://www.example/'): (200, {}, '<html>hello</html>')})
    monkeypatch.setattr(c.net, 'request', web)
    with pytest.raises(cal.api.CalError, match='did not answer like a calendar server'):
        c.discover(c.Login('https://www.example/', 'ann', 'pw'))
    assert all(s.method == 'PROPFIND' for s in web.sent)


def test_failures_are_said_in_our_own_words_and_a_login_never_follows_a_downgrade(cal, monkeypatch):
    c = cal.caldav
    login = c.Login('https://dav.example/', 'ann', 'pw')

    def answer(status, headers=None, body=''):
        wire = Wire({('PROPFIND', 'https://dav.example/'): (status, headers or {}, body)})
        monkeypatch.setattr(c.net, 'request', wire)
        return wire
    answer(401, body='<b>EVIL SERVER SAYS click here</b>')
    with pytest.raises(c.Refused) as e:
        c.discover(login)
    assert 'refused the login' in str(e.value) and 'EVIL' not in str(e.value)
    wire = answer(301, {'Location': 'http://dav.example/plain/'})
    with pytest.raises(cal.api.CalError, match='an address it may not go to'):
        c.discover(login)
    assert all(s.url.startswith('https://') for s in wire.sent)                 # the login was not sent on
    answer(503)
    with pytest.raises(cal.api.CalError, match='not answering right now'):
        c.discover(login)
    answer(207, body='<!DOCTYPE x [<!ENTITY a "aaaa">]><d:multistatus xmlns:d="DAV:"/>')
    with pytest.raises(cal.api.CalError, match='an answer this will not read'):
        c.discover(login)

    def broken(method, url, **kw):
        raise c.requests.exceptions.SSLError('certificate verify failed: self-signed')
    monkeypatch.setattr(c.net, 'request', broken)
    with pytest.raises(cal.api.CalError, match='certificate this machine trusts'):
        c.discover(login)
    with pytest.raises(cal.api.CalError, match='no login inside it'):
        c.clean_url('https://ann:secret@dav.example/')
    digest = Wire({('PROPFIND', 'https://dav.example/'): (401, {'WWW-Authenticate': 'Digest realm="x", nonce="n"'}, '')})
    monkeypatch.setattr(c.net, 'request', digest)
    with pytest.raises(c.Refused):
        c.discover(login)
    assert 'Authorization' in digest.sent[0].headers and 'auth' in digest.sent[1].kw     # Basic first, then Digest


def test_reading_and_writing_event_files(cal, monkeypatch):
    c = cal.caldav
    login = c.Login('https://dav.example/', 'ann', 'pw')
    home = 'https://dav.example/u/ann/home/'
    listing = _multi(('/u/ann/home/a.ics', '<d:getetag>"1"</d:getetag><c:calendar-data>BEGIN:VCALENDAR\nEND:VCALENDAR</c:calendar-data>'),
                     ('/u/ann/home/b.ics', '<d:getetag>"2"</d:getetag>'))
    wire = Wire({('REPORT', home): (207, {}, listing),
                 ('PROPFIND', home): (207, {}, _multi(('/u/ann/home/', '<cs:getctag>mark-9</cs:getctag>'))),
                 ('GET', home + 'a.ics'): (200, {'ETag': '"1"'}, 'BEGIN:VCALENDAR\nEND:VCALENDAR'),
                 ('PUT', home + 'a.ics'): (204, {'ETag': '"3"'}, ''),
                 ('PUT', home + 'old.ics'): (412, {}, 'precondition failed, says the server'),
                 ('DELETE', home + 'a.ics'): (204, {}, '')})
    monkeypatch.setattr(c.net, 'request', wire)
    got = c.events(login, home, datetime(2026, 11, 1, tzinfo=timezone.utc), datetime(2026, 12, 1, tzinfo=timezone.utc))
    assert [(h, e) for h, e, _t in got] == [(home + 'a.ics', '"1"'), (home + 'a.ics', '"1"')]   # b came bare: asked for by name
    report, multiget = [s for s in wire.sent if s.method == 'REPORT']
    assert b'start="20261101T000000Z" end="20261201T000000Z"' in report.data and b'expand' not in report.data
    assert b'<d:href>/u/ann/home/b.ics</d:href>' in multiget.data
    assert c.ctag(login, home) == 'mark-9'
    assert c.get(login, home + 'a.ics') == ('BEGIN:VCALENDAR\nEND:VCALENDAR', '"1"')
    assert c.put(login, home + 'a.ics', 'x', etag='"1"') == '"3"' and wire.sent[-1].headers['If-Match'] == '"1"'
    c.put(login, home + 'a.ics', 'x', new=True)
    assert wire.sent[-1].headers['If-None-Match'] == '*' and 'If-Match' not in wire.sent[-1].headers
    with pytest.raises(c.Changed) as e:
        c.put(login, home + 'old.ics', 'x', etag='"0"')
    assert 'changed somewhere else' in str(e.value) and 'says the server' not in str(e.value)
    c.delete(login, home + 'a.ics')
    c.delete(login, home + 'never-there.ics')                                   # already gone counts as removed


# -- accounts ------------------------------------------------------------------------------------

def test_an_account_is_tried_before_it_is_kept_and_its_calendars_are_linked(cal):
    d = cal.use_dav(add=False)
    with pytest.raises(cal.caldav.Refused):
        cal.connect.add_account('default', {'preset': 'caldav', 'server': 'https://dav.example', 'username': 'ann', 'password': 'wrong'})
    assert cal.creds.book == {} and cal.db.links('default') == []                # a refused login leaves nothing behind
    for body, words in (({'preset': 'caldav', 'server': 'https://dav.example', 'password': 'x'}, 'Type the login name'),
                        ({'preset': 'caldav', 'server': 'https://dav.example', 'username': 'ann'}, 'Type the password'),
                        ({'preset': 'caldav', 'server': 'ftp://dav.example', 'username': 'ann', 'password': 'x'}, 'looks like https://'),
                        ({'preset': 'icloud', 'username': '', 'password': 'x'}, 'Type the apple account email')):
        with pytest.raises(cal.api.CalError, match=words):
            cal.connect.add_account('default', body)
    row = cal.connect.add_account('default', {'preset': 'icloud', 'username': 'ann@example.com', 'password': 'app-pass'})
    assert (row['label'], row['url']) == ('iCloud', 'https://caldav.icloud.com/')
    stored = cal.creds.book[f"default/{row['id']}"]
    assert stored['secrets'] == {'password': 'app-pass'} and 'password' not in stored['fields']
    links = cal.db.links('default')
    assert [(l['name'], l['kind'], l['writable'], l['account']) for l in links] == [
        ('Home', 'caldav', True, row['id']), ('Team', 'caldav', False, row['id'])]
    assert cal.api.target_of('default')['name'] == 'Home'                        # the first one it can write to
    with pytest.raises(cal.api.CalError, match='already in this calendar'):
        cal.connect.add_account('default', {'preset': 'icloud', 'username': 'ANN@example.com', 'password': 'app-pass'})
    fm = cal.connect.add_account('default', {'preset': 'fastmail', 'username': 'ann@fastmail.example', 'password': 'app-pass'})
    assert fm['url'] == 'https://caldav.fastmail.com/dav/principals/user/ann@fastmail.example/'
    assert d.named('discover')[-1][1] == fm['url']


def test_the_panel_view_ticks_and_unticks(cal):
    d = cal.use_dav()
    r = cal.account_routes
    cal.patch.setattr(cal.links, '_account', lambda calendar: {'client_id': '', 'calendar_id': 'primary'})
    state = r.get_accounts(query={})
    acct = state['accounts'][0]
    assert state['success'] and (acct['provider'], acct['label'], acct['detail'], acct['host']) == ('caldav', 'Other (CalDAV)', 'ann', 'dav.example')
    assert [(c['name'], c['writable'], bool(c['link'])) for c in acct['calendars']] == [('Home', True, True), ('Team', False, True)]
    assert state['target'] == cal.home['id'] and [t['name'] for t in state['targets']] == ['Home']
    assert 'app-pass' not in str(state) and not any('password' in k for k in acct)       # no secret leaves the server
    assert [p['id'] for p in state['presets']] == ['icloud', 'fastmail', 'yahoo', 'nextcloud', 'caldav'] and 'url' not in state['presets'][0]
    team = acct['calendars'][1]['link']
    assert team['where'] == 'dav.example'                                        # the host, never the address
    cal.links.delete_link(team['id'])
    acct = r.get_accounts(query={})['accounts'][0]
    assert [(c['name'], bool(c['link'])) for c in acct['calendars']] == [('Home', True), ('Team', False)]
    key = acct['calendars'][1]['key']
    assert 'dav.example/u/' not in str(state['accounts'])                        # a calendar's address stays on the server
    assert cal.links.add_link(body={'kind': 'caldav', 'account': acct['id'], 'key': 'https://evil.example/cal/'})[1] == 400
    assert cal.links.add_link(body={'kind': 'caldav', 'account': acct['id'], 'key': d.BASE + 'team/'})[1] == 400
    assert cal.links.add_link(body={'kind': 'caldav', 'account': 'nope', 'key': key})[1] == 400
    back = cal.links.add_link(body={'kind': 'caldav', 'account': acct['id'], 'key': key})
    assert sorted(l['name'] for l in back['links']) == ['Home', 'Team']
    cal.links.delete_link(cal.home['id'])                                        # the target goes: new events stay home
    assert r.get_accounts(query={})['target'] == ''


def test_a_stored_password_only_goes_to_the_server_it_was_stored_for(cal):
    d = cal.use_dav()
    r = cal.account_routes
    cal.patch.setattr(cal.links, '_account', lambda calendar: {})
    acct = cal.account['id']
    ok = r.update_account(acct, body={'server': 'https://dav.example', 'username': 'ann', 'label': 'Home server'})
    assert ok['success'] and ok['accounts'][0]['label'] == 'Home server'          # a blank password keeps the stored one
    assert cal.creds.book[f'default/{acct}']['secrets'] == {'password': 'app-pass'}
    seen = len(d.calls)
    for body in ({'server': 'https://elsewhere.example', 'username': 'ann'}, {'server': 'https://dav.example', 'username': 'bob'}):
        refused, status = r.update_account(acct, body=body)
        assert status == 400 and 'only sent to the server it was saved for' in refused['detail']
    assert len(d.calls) == seen and cal.creds.book[f'default/{acct}']['fields']['url'] == 'https://dav.example/'
    d.password = 'new-pass'
    assert r.update_account(acct, body={'server': 'https://dav.example', 'username': 'ann', 'password': 'typo'})[1] == 400
    assert cal.creds.book[f'default/{acct}']['secrets'] == {'password': 'app-pass'}   # a refused change is not stored
    assert r.update_account(acct, body={'server': 'https://dav.example', 'username': 'ann', 'password': 'new-pass'})['success']
    assert cal.creds.book[f'default/{acct}']['secrets'] == {'password': 'new-pass'}
    assert r.update_account('nope', body={})[1] == 400


def test_an_account_can_borrow_an_email_accounts_login(cal):
    d = cal.use_dav(add=False)
    cal.creds.mail = {'work': {'address': 'ann@example.com', 'app_password': 'app-pass'},
                      'ms': {'address': 'ann@outlook.example', 'app_password': '', 'auth_type': 'oauth2'}}
    assert cal.accounts.email_logins() == [{'scope': 'work', 'address': 'ann@example.com'}]   # only ones with a password
    row = cal.connect.add_account('default', {'preset': 'icloud', 'username': 'ann@example.com', 'email_account': 'work'})
    stored = cal.creds.book[f"default/{row['id']}"]
    assert stored['secrets'] == {} and stored['fields']['email_account'] == 'work'    # no second copy of the password
    home = next(l for l in cal.db.links('default') if l['name'] == 'Home')
    cal.api.create({'title': 'Lunch', 'start': '2026-11-19 12:00'}, to=home['id'])
    assert len(d.files) == 1
    d.password = cal.creds.mail['work']['app_password'] = 'changed-under-email'    # changed in one place: both follow
    cal.api.create({'title': 'Dinner', 'start': '2026-11-19 19:00'}, to=home['id'])
    assert len(d.files) == 2
    cal.creds.mail['work']['app_password'] = ''
    with pytest.raises(cal.api.CalError, match="has no password any more"):
        cal.api.create({'title': 'Late', 'start': '2026-11-19 22:00'}, to=home['id'])
    with pytest.raises(cal.api.CalError, match='no password to share'):
        cal.connect.add_account('default', {'preset': 'yahoo', 'username': 'ann@yahoo.example', 'email_account': 'ms'})


def test_removing_an_account_or_its_calendar_takes_the_login_along(cal):
    d = cal.use_dav()
    cal.api.create({'title': 'Lunch', 'start': '2026-11-19 12:00'})
    r = cal.account_routes
    cal.patch.setattr(cal.links, '_account', lambda calendar: {})
    gone = r.delete_account(cal.account['id'], query={'calendar': 'default'})
    assert gone['accounts'] == [] and gone['removed_events'] == 1 and cal.creds.book == {} and cal.db.links('default') == []
    assert len(d.files) == 1 and d.named('delete') == []                           # nothing is touched at the server
    assert r.delete_account(cal.account['id'], query={})[1] == 400
    cal.api.add_calendar('work')
    cal.connect.add_account('work', {'preset': 'caldav', 'server': 'https://dav.example', 'username': 'ann', 'password': 'app-pass'})
    assert list(cal.creds.book) and cal.api.delete_calendar('work') == 1
    assert cal.creds.book == {}


def test_a_refused_login_is_not_tried_again_for_a_while(cal):
    d = cal.use_dav()
    d.password = 'changed-at-the-provider'
    before = len(d.named('ctag'))
    failed = cal.api.refresh('default', force=True)
    assert len(failed) == 2 and 'refused the login' in failed[0][1]
    assert len(d.named('ctag')) == before + 1                                              # the second calendar did not knock again
    seen = len(d.calls)
    again = cal.api.refresh('default', force=True)
    assert len(d.calls) == seen and 'Not trying again for a few minutes' in again[0][1]
    with pytest.raises(cal.api.CalError, match='Not trying again'):
        cal.api.create({'title': 'Lunch', 'start': '2026-11-19 12:00'})
    cal.patch.setattr(cal.links, '_account', lambda calendar: {})
    assert cal.account_routes.update_account(cal.account['id'], body={'server': 'https://dav.example', 'username': 'ann',
                                                                      'password': 'changed-at-the-provider'})['success']
    assert cal.api.refresh('default', force=True) == []                           # the fixed login is tried at once


# -- events, written through ---------------------------------------------------------------------

def test_new_events_are_files_on_the_server(cal):
    d = cal.use_dav()
    one = cal.api.create({'title': 'Dentist', 'start': '2026-11-19 14:00', 'end': '15:00', 'location': 'Main St', 'notes': 'Ask'})
    assert (one['source'], one['href'], one['source_ref'] != '') == (cal.home['id'], next(iter(d.files)), True)
    assert one['href'].startswith(d.BASE + 'home/') and one['href'].endswith('.ics')
    assert d.named('put')[0][3] is True                                           # only if nothing is there yet
    text = d.text()
    for line in ('SUMMARY:Dentist', 'DTSTART;TZID=America/New_York:20261119T140000', 'DTEND;TZID=America/New_York:20261119T150000',
                 'LOCATION:Main St', 'DESCRIPTION:Ask', 'BEGIN:VTIMEZONE', 'SEQUENCE:0'):
        assert line in text, line
    cal.api.create({'title': 'Offsite', 'start': '2026-11-20', 'end': '2026-11-21'})
    cal.api.create({'title': 'Standup', 'start': '2026-11-16 09:00', 'end': '09:15', 'repeat': 'weekly', 'days': 'mon,wed'})
    assert _days(cal, '2026-11-16', '2026-11-22') == [
        ('2026-11-16T09:00', 'Standup'), ('2026-11-18T09:00', 'Standup'), ('2026-11-19T14:00', 'Dentist'), ('2026-11-20', 'Offsite')]
    seen = cal.routes.list_events(query={'start': '2026-11-19', 'end': '2026-11-19'})['events'][0]
    assert (seen['source'], seen['editable'], seen['link_name']) == ('caldav', True, 'Home')
    with pytest.raises(cal.api.CalError, match='Google calendar only'):
        cal.api.create({'title': 'Lunch', 'start': '2026-11-21 12:00'}, guests=[{'email': 'x@example.com'}])
    d.down = 'dav.example is not answering right now (HTTP 503). Try again in a few minutes.'
    with pytest.raises(cal.api.CalError, match='not answering'):
        cal.api.create({'title': 'Lost', 'start': '2026-11-22 12:00'})
    assert len(d.files) == 3 and 'Lost' not in str(_days(cal))                    # down: not added anywhere


def test_a_change_keeps_everything_this_calendar_does_not_model(cal):
    d = cal.use_dav()
    href = d.add('standup.ics', SHARED)
    cal.api.refresh('default', force=True)
    ev = cal.db.find(cal.home['id'], 'standup-1')
    # the file's end is an instant in UTC; here it is the day of the last occurrence where the event is kept
    assert (ev['tz'], ev['start'], ev['rrule']) == ('Europe/London', '2026-10-05T14:00', 'FREQ=WEEKLY;UNTIL=20261221')
    assert _days(cal, '2026-11-08', '2026-11-24') == [('2026-11-16T11:00', 'Standup (retro)'), ('2026-11-23T09:00', 'Standup')]
    cal.api.update(ev['id'], {'title': 'Standup, EU', 'notes': 'Bring the board'})
    text = d.text(href)
    for kept in ('X-OTHER-APP-COLOR:teal', 'TRIGGER:-PT10M', 'ATTENDEE;CN=Bo Chan;PARTSTAT=ACCEPTED:mailto:bo@example.com',
                 'ATTENDEE;CUTYPE=ROOM;CN=Room 4:mailto:room4@example.com', 'ORGANIZER;CN=Ann Lee:mailto:ann@example.com',
                 'DTSTART;TZID=Europe/London:20261005T140000', 'EXDATE;TZID=Europe/London:20261109T140000',
                 'RECURRENCE-ID;TZID=Europe/London:20261116T140000', 'SUMMARY:Standup (retro)', 'PRODID:-//Other App//EN',
                 "DESCRIPTION:not the event's description"):
        assert kept in text, kept
    assert 'SUMMARY:Standup\\, EU' in text and 'DESCRIPTION:Bring the board' in text and 'SEQUENCE:1' in text
    assert text.count('BEGIN:VEVENT') == 2 and text.count('BEGIN:VTIMEZONE') == 1
    assert d.named('put')[-1][2] == '"v1"'                                        # only if it is still the version that was read
    assert cal.db.get(ev['id'])['title'] == 'Standup, EU'                         # the same row, the same id
    with pytest.raises(cal.api.CalError, match='Nothing to change'):
        cal.api.update(ev['id'], {'title': 'Standup, EU'})


def test_guests_in_a_file_are_shown_by_name_and_left_alone(cal):
    d = cal.use_dav()
    d.add('standup.ics', SHARED)
    cal.api.refresh('default', force=True)
    seen = cal.api.list_range('2026-11-23', '2026-11-23', 'default')[0]
    assert [(g['name'], g['rsvp']) for g in seen['guests']] == [('Bo Chan', 'yes'), ('cy@example.com', 'maybe')]   # no room
    assert seen['my_rsvp'] == ''
    listing, ok = _run(cal, 'calendar_range', start_date='2026-11-23', end_date='2026-11-23')
    assert ok and '[guests: Bo Chan (yes), cy (maybe)]' in listing and 'example.com' not in listing
    msg, ok = _run(cal, 'calendar_update', id=seen['id'], add_guests=['Nobody'])
    assert not ok


def test_one_occurrence_of_a_series_becomes_a_changed_occurrence(cal):
    d = cal.use_dav()
    wk = cal.api.create({'title': 'Standup', 'start': '2026-11-16 09:00', 'end': '09:15', 'repeat': 'weekly', 'days': 'mon,wed'})
    new = cal.api.update(wk['id'], {'title': 'Standup, long one', 'start': '2026-11-25 10:00', 'end': '2026-11-25 11:00'}, day='2026-11-25')
    assert new['id'] != wk['id'] and new['source_ref'] == f"{wk['source_ref']}#2026-11-25" and new['rrule'] == ''
    text = d.text()
    assert 'RECURRENCE-ID;TZID=America/New_York:20261125T090000' in text and text.count('BEGIN:VEVENT') == 2
    assert text.count('RRULE:FREQ=WEEKLY') == 1 and 'SUMMARY:Standup\\, long one' in text
    assert _days(cal, '2026-11-23', '2026-11-30') == [('2026-11-23T09:00', 'Standup'), ('2026-11-25T10:00', 'Standup, long one'),
                                                      ('2026-11-30T09:00', 'Standup')]
    for bad, words in ((dict(day='2026-11-24'), 'no occurrence that starts on 2026-11-24'),
                       (dict(day='2026-11-23', fields={'repeat': 'daily'}), 'cannot have its own repeat')):
        with pytest.raises(cal.api.CalError, match=words):
            cal.api.update(wk['id'], bad.get('fields', {'title': 'x'}), day=bad['day'])
    with pytest.raises(cal.api.CalError, match='does not repeat'):
        cal.api.update(new['id'], {'title': 'x'}, day='2026-11-25')
    again = cal.api.update(new['id'], {'location': 'Room 2'})                     # the changed occurrence, changed again
    assert again['id'] == new['id'] and 'LOCATION:Room 2' in d.text() and d.text().count('BEGIN:VEVENT') == 2
    assert cal.api.remove(wk['id'], day='2026-11-30')[0] == 'occurrence'
    assert 'EXDATE;TZID=America/New_York:20261130T090000' in d.text()
    assert cal.api.remove(new['id'])[0] == 'event'                                # it goes, and its day leaves the series
    text = d.text()
    assert text.count('BEGIN:VEVENT') == 1 and 'EXDATE;TZID=America/New_York:20261125T090000' in text
    assert _days(cal, '2026-11-23', '2026-12-02') == [('2026-11-23T09:00', 'Standup'), ('2026-12-02T09:00', 'Standup')]
    assert cal.api.remove(wk['id'])[0] == 'event' and d.files == {} and _days(cal) == []


def test_moving_a_whole_series_moves_its_exceptions_and_its_end_with_it(cal):
    d = cal.use_dav()
    href = d.add('standup.ics', SHARED)
    cal.api.refresh('default', force=True)
    ev = cal.db.find(cal.home['id'], 'standup-1')
    # seen from New York the 14:00 London meeting is at 09:00; the person drags Nov 23 to 10:30, for every occurrence
    cal.api.update(ev['id'], {'start': '2026-11-23T10:30', 'end': '2026-11-23T11:00'}, shown='2026-11-23')
    text = d.text(href)
    for line in ('DTSTART;TZID=Europe/London:20261005T153000', 'DTEND;TZID=Europe/London:20261005T160000',
                 'EXDATE;TZID=Europe/London:20261109T153000', 'RRULE:FREQ=WEEKLY;UNTIL=20261221T153000Z',
                 'RECURRENCE-ID;TZID=Europe/London:20261116T153000',
                 'DTSTART;TZID=Europe/London:20261116T160000'):                  # the changed occurrence keeps its own time
        assert line in text, line
    assert _days(cal, '2026-11-08', '2026-11-24') == [('2026-11-16T11:00', 'Standup (retro)'), ('2026-11-23T10:30', 'Standup')]
    assert _days(cal, '2026-12-20', '2026-12-22') == [('2026-12-21T10:30', 'Standup')]    # the last one was not cut off
    with pytest.raises(cal.api.CalError, match='changed on their own'):
        cal.api.update(ev['id'], {'repeat': 'daily'})
    with pytest.raises(cal.api.CalError, match='between timed and all-day'):
        cal.api.update(ev['id'], {'all_day': True, 'start': '2026-11-23'}, shown='2026-11-23')
    assert d.text(href) == text                                                   # a refusal sends nothing


def test_a_series_without_changed_occurrences_can_change_how_it_repeats(cal):
    d = cal.use_dav()
    wk = cal.api.create({'title': 'Run', 'start': '2026-11-16 07:00', 'end': '07:30', 'repeat': 'weekly'})
    cal.api.remove(wk['id'], day='2026-11-23')
    cal.api.update(wk['id'], {'repeat': 'daily', 'times': 3})
    text = d.text()
    assert 'RRULE:FREQ=DAILY;COUNT=3' in text and 'EXDATE' not in text             # the days taken out were the old series'
    assert _days(cal) == [('2026-11-16T07:00', 'Run'), ('2026-11-17T07:00', 'Run'), ('2026-11-18T07:00', 'Run')]
    cal.api.update(wk['id'], {'repeat': 'none'})
    assert 'RRULE:FREQ=DAILY' not in d.text() and 'RRULE:FREQ=WEEKLY' not in d.text()
    assert _days(cal) == [('2026-11-16T07:00', 'Run')]
    once = cal.db.find(cal.home['id'], wk['source_ref'])
    cal.api.update(once['id'], {'repeat': 'weekly', 'start': '2026-11-18 07:00'})  # a one-time event gains a repeat
    assert 'RRULE:FREQ=WEEKLY\n' in d.text() and 'DTSTART;TZID=America/New_York:20261118T070000' in d.text()


def test_a_change_made_elsewhere_in_the_meantime_is_never_overwritten(cal):
    d = cal.use_dav()
    ev = cal.api.create({'title': 'Dentist', 'start': '2026-11-19 14:00'})
    real_get = d.get

    def get_then_someone_else_writes(login, href):
        text, mark = real_get(login, href)
        d.files[href] = (text.replace('SUMMARY:Dentist', 'SUMMARY:Dentist (their edit)'), '"theirs"')
        return text, mark
    cal.patch.setattr(cal.caldav, 'get', get_then_someone_else_writes)
    with pytest.raises(cal.api.CalError, match='changed somewhere else'):
        cal.api.update(ev['id'], {'title': 'Dentist (mine)'})
    assert 'their edit' in d.text() and cal.db.get(ev['id'])['title'] == 'Dentist'  # their change stands; our copy is untouched
    cal.patch.setattr(cal.caldav, 'get', real_get)
    del d.files[ev['href']]
    with pytest.raises(cal.api.CalError, match='no longer on its server'):
        cal.api.update(ev['id'], {'title': 'x'})


def test_nothing_is_read_again_while_the_server_says_nothing_changed(cal):
    d = cal.use_dav()
    cal.api.create({'title': 'Dentist', 'start': '2026-11-19 14:00'})
    cal.api.refresh('default', force=True)
    reads = len(d.named('events'))
    cal.db.update_link(cal.home['id'], {'synced_at': 0})                           # stale by the clock...
    assert cal.api.refresh('default') == [] and len(d.named('events')) == reads    # ...but the server's mark has not moved
    d.add('other.ics', SHARED)                                                     # another app adds an event
    cal.db.update_link(cal.home['id'], {'synced_at': 0})
    cal.api.refresh('default')
    assert len(d.named('events')) == reads + 1 and ('2026-11-23T09:00', 'Standup') in _days(cal)
    d.calendars.pop(0)
    cal.patch.setattr(cal.caldav, 'ctag', lambda login, calendar: (_ for _ in ()).throw(cal.caldav.Gone('gone')))
    failed = cal.api.refresh('default', force=True)
    assert 'no longer on its server' in failed[0][1]


def test_a_read_only_calendar_and_a_private_chat_change_nothing(cal):
    d = cal.use_dav()
    d.add('standup.ics', SHARED, calendar='team/')
    cal.api.refresh('default', force=True)
    team = next(l for l in cal.db.links('default') if l['name'] == 'Team')
    ev = cal.db.find(team['id'], 'standup-1')
    with pytest.raises(cal.api.CalError, match='read-only for this account'):
        cal.api.update(ev['id'], {'title': 'x'})
    with pytest.raises(cal.api.CalError, match='read-only'):
        cal.api.create({'title': 'x', 'start': '2026-11-19 10:00'}, to='Team')
    calls = len(d.calls)
    cal.private.set(True)
    listing, ok = _run(cal, 'calendar_range', start_date='2026-11-23', end_date='2026-11-23')
    assert ok and 'Standup' in listing and 'were not refreshed' in listing and len(d.calls) == calls
    msg, ok = _run(cal, 'calendar_add', title='x', start='2026-11-19 10:00')
    assert not ok and 'private' in msg and len(d.calls) == calls


def test_the_page_names_the_occurrence_it_was_looking_at(cal):
    cal.use_dav()
    wk = cal.api.create({'title': 'Run', 'start': '2026-11-16 07:00', 'end': '07:30', 'repeat': 'weekly'})
    ok = cal.routes.update_event(wk['id'], body={'title': 'Run', 'start': '2026-11-30T08:00', 'end': '2026-11-30T08:30',
                                                 'all_day': False, 'location': '', 'notes': '', 'shown': '2026-11-30'})
    assert ok['success'] and _days(cal, '2026-11-16', '2026-11-30') == [
        ('2026-11-16T08:00', 'Run'), ('2026-11-23T08:00', 'Run'), ('2026-11-30T08:00', 'Run')]    # the series, not a new start day
    assert cal.routes.update_event(wk['id'], body={'start': '2026-11-30T08:00', 'shown': '2026-11-29'})[1] == 400


# -- found by the scouts (2026-11-19) ----------------------------------------------------------

def test_a_login_only_follows_a_server_to_addresses_it_may_go_to(cal, monkeypatch):
    c = cal.caldav
    login = c.Login('https://dav.example/', 'ann', 'pw')
    root = _multi(('/', '<c:calendar-home-set><d:href>{home}</d:href></c:calendar-home-set>'))
    for home, ok in (('https://p07.dav.example/u/ann/', True),            # another machine of the provider, encrypted
                     ('http://p07.dav.example/u/ann/', False),            # the same, in the clear
                     ('http://dav.example/u/ann/', False),                # down from https on the same host
                     ('https://192.168.1.20/u/ann/', False),              # from outside onto this machine's own network
                     ('https://router.lan/u/ann/', False)):
        wire = Wire({('PROPFIND', 'https://dav.example/'): (207, {}, root.replace('{home}', home)),
                     ('PROPFIND', home): (207, {}, HOME)})
        monkeypatch.setattr(c.net, 'request', wire)
        if ok:
            assert len(c.discover(login)['calendars']) == 2
        else:
            with pytest.raises(cal.api.CalError, match='an address it may not go to'):
                c.discover(login)
            assert [s.url for s in wire.sent] == ['https://dav.example/'], home     # the login never went there
    # a calendar listed on another server than its folder, and an event file on another than its calendar: left out
    listing = HOME.replace('<d:href>/u/ann/home/</d:href>', '<d:href>https://elsewhere.example/u/ann/home/</d:href>')
    wire = Wire({('PROPFIND', 'https://dav.example/'): (207, {}, root.replace('{home}', '/u/ann/')),
                 ('PROPFIND', 'https://dav.example/u/ann/'): (207, {}, listing)})
    monkeypatch.setattr(c.net, 'request', wire)
    assert [x['name'] for x in c.discover(login)['calendars']] == ['team shared']
    files = _multi(('https://elsewhere.example/steal.ics', '<d:getetag>"9"</d:getetag>'),
                   ('http://dav.example/u/ann/home/plain.ics', '<d:getetag>"8"</d:getetag>'),
                   ('/u/ann/home/a.ics', '<d:getetag>"1"</d:getetag><c:calendar-data>BEGIN:VCALENDAR</c:calendar-data>'))
    wire = Wire({('REPORT', 'https://dav.example/u/ann/home/'): (207, {}, files)})
    monkeypatch.setattr(c.net, 'request', wire)
    day = datetime(2026, 11, 1, tzinfo=timezone.utc)
    assert [h for h, _e, _t in c.events(login, 'https://dav.example/u/ann/home/', day, day)] == ['https://dav.example/u/ann/home/a.ics']
    assert len(wire.sent) == 1                                                    # nothing asked for by name elsewhere


def test_the_version_mark_a_change_is_tied_to(cal, monkeypatch):
    c = cal.caldav
    login = c.Login('https://dav.example/', 'ann', 'pw')
    href = 'https://dav.example/u/ann/home/a.ics'
    wire = Wire({('GET', href): (200, {'ETag': 'W/"1-gzip"'}, 'BEGIN:VCALENDAR\nEND:VCALENDAR'),
                 ('PROPFIND', href): (207, {}, _multi(('/u/ann/home/a.ics', '<d:getetag>"1"</d:getetag>')))})
    monkeypatch.setattr(c.net, 'request', wire)
    assert c.get(login, href)[1] == '"1"'                                         # a weak mark never matches: the file's own is asked
    assert wire.sent[0].headers['Accept-Encoding'] == 'identity'                  # so no proxy rewrites the mark on the way
    digest = Wire({('PROPFIND', 'https://old.example/'): (401, {'WWW-Authenticate': 'Digest realm="x", nonce="n"'}, '')})
    monkeypatch.setattr(c.net, 'request', digest)
    for _ in range(2):
        with pytest.raises(c.Refused):
            c.ctag(c.Login('https://old.example/', 'ann', 'pw'), 'https://old.example/')
    assert ['auth' in s.kw for s in digest.sent] == [False, True, True]           # the password in Basic once, never again


def test_moving_an_account_to_another_server_takes_its_old_calendars_off(cal):
    d = cal.use_dav()
    cal.api.create({'title': 'Lunch', 'start': '2026-11-19 12:00'})
    r = cal.account_routes
    cal.patch.setattr(cal.links, '_account', lambda calendar: {})
    old = [l['ref'] for l in cal.db.links('default')]
    d.calendars = [{'ref': 'https://new.example/u/home/', 'name': 'Home', 'writable': True}]
    seen = len(d.calls)
    ok = r.update_account(cal.account['id'], body={'server': 'https://new.example', 'username': 'ann', 'password': 'app-pass'})
    assert ok['success'] and [l['ref'] for l in cal.db.links('default')] == ['https://new.example/u/home/']
    assert not any(any(str(part).startswith(o) for o in old) for call in d.calls[seen:] for part in call)   # nothing more went to the old one
    assert cal.accounts.get('default', cal.account['id'])['hosts'] == ['new.example']
    assert _days(cal) == []                                                        # the old server's copies went with its links
    # a link that somehow still points at a server the account is not set to: the login stays home
    cal.db.add_link({'calendar': 'default', 'kind': 'caldav', 'name': 'Stray', 'ref': 'https://old-and-sold.example/c/',
                     'writable': True, 'account': cal.account['id']})
    seen = len(d.calls)
    failed = cal.api.refresh('default', force=True)
    assert [why for link, why in failed if link['name'] == 'Stray'][0].startswith("'Stray' is not on the server its account is set to now")
    assert not any('old-and-sold' in str(call) for call in d.calls[seen:])


def test_a_borrowed_password_is_not_lent_to_a_different_mailbox(cal):
    d = cal.use_dav(add=False)
    cal.creds.mail = {'default': {'address': 'ann@icloud.example', 'app_password': 'app-pass'}}
    row = cal.connect.add_account('default', {'preset': 'icloud', 'username': 'ann@icloud.example', 'email_account': 'default'})
    assert cal.creds.book[f"default/{row['id']}"]['fields']['email_address'] == 'ann@icloud.example'
    cal.creds.mail = {'default': {'address': 'ann@gmail.example', 'app_password': 'a-gmail-app-password'}}   # the name now holds another mailbox
    seen = len(d.calls)
    failed = cal.api.refresh('default', force=True)
    assert failed and all('now holds a different address' in why for _link, why in failed) and len(d.calls) == seen


def test_a_link_removed_while_it_was_being_read_takes_nothing(cal):
    d = cal.use_dav()
    cal.api.create({'title': 'Lunch', 'start': '2026-11-19 12:00'})
    real = d.events

    def unticked_meanwhile(login, calendar, first, last):
        rows = real(login, calendar, first, last)
        cal.api.remove_link(cal.home['id'])                                        # the person unticks it mid-read
        return rows
    cal.patch.setattr(cal.caldav, 'events', unticked_meanwhile)
    cal.sync.pull(cal.home, force=True)
    assert cal.db.get_link(cal.home['id']) is None and _days(cal) == []           # no copies nobody could delete


def test_a_read_that_started_before_a_change_cannot_undo_it(cal):
    d = cal.use_dav()
    made = {}
    real = d.events

    def slow_read(login, calendar, first, last):
        rows = real(login, calendar, first, last)                                  # the server answers: nothing yet
        if calendar.endswith('home/') and not made:
            made['ev'] = cal.api.create({'title': 'Lunch', 'start': '2026-11-19 12:00'})   # ...and she adds an event meanwhile
        return rows
    cal.patch.setattr(cal.caldav, 'events', slow_read)
    cal.sync.pull(cal.home, force=True)
    assert cal.db.get(made['ev']['id'])['title'] == 'Lunch'                        # still here, under the id she was given
    assert cal.db.get_link(cal.home['id'])['synced_at'] == 0                       # and the link is due for a real read
    cal.patch.setattr(cal.caldav, 'events', real)
    cal.api.refresh('default')
    assert cal.db.get(made['ev']['id'])['title'] == 'Lunch' and _days(cal) == [('2026-11-19T12:00', 'Lunch')]
    g_ev = cal.api.create({'title': 'Gone', 'start': '2026-11-20 09:00'})
    stale = real(cal.dav.login_of(cal.home), cal.home['ref'], None, None)
    cal.patch.setattr(cal.caldav, 'events', lambda *a: (cal.api.remove(g_ev['id']), stale)[1])
    cal.sync.pull(cal.home, force=True)                                            # a read that still holds the deleted event
    assert cal.db.get(g_ev['id']) is None                                          # does not bring it back


def test_an_event_written_outside_the_copied_days_survives_the_next_read(cal):
    cal.use_dav()
    cal.api.refresh('default', force=True)
    far = cal.api.create({'title': 'Far off', 'start': '2029-03-05 10:00'})
    link = cal.db.get_link(cal.home['id'])
    assert link['win_to'] >= '2029-03-05' and link['synced_at'] == 0
    cal.api.refresh('default')
    assert cal.db.get(far['id'])['title'] == 'Far off'


def test_days_past_the_furthest_reach_are_not_asked_for_forever(cal):
    from datetime import date
    d = cal.use_dav()
    cal.api.refresh('default', force=True)
    reads = len(d.named('ctag'))
    need = (date(2046, 1, 1), date(2046, 12, 31))
    assert not any(cal.sync._stale(l, need, False, cal.api.today()) for l in cal.db.links('default'))
    cal.api.refresh('default', need=need)
    assert len(d.named('ctag')) == reads
    near = (date(2030, 1, 1), date(2030, 1, 31))                                   # inside the reach: read once, then covered
    assert all(cal.sync._stale(l, near, False, cal.api.today()) for l in cal.db.links('default'))
    cal.api.refresh('default', need=near)
    assert not any(cal.sync._stale(l, near, False, cal.api.today()) for l in cal.db.links('default'))


def test_two_calendars_of_one_name_get_names_she_can_tell_apart(cal):
    d = cal.use_dav()
    d.calendars = [{'ref': 'https://dav.example/v/home/', 'name': 'Home', 'writable': True},
                   {'ref': 'https://dav.example/v/team/', 'name': 'Team', 'writable': True}]
    cal.connect.add_account('default', {'preset': 'fastmail', 'username': 'ann@fastmail.example', 'password': 'app-pass'})
    assert sorted(l['name'] for l in cal.db.links('default')) == ['Home', 'Home (Fastmail)', 'Team', 'Team (Fastmail)']
    ev = cal.api.create({'title': 'x', 'start': '2026-11-19 10:00'}, to='Team (Fastmail)')    # the writable one, by its own name
    assert ev['href'].startswith('https://dav.example/v/team/')
    cal.db.update_link(next(l['id'] for l in cal.db.links('default') if l['name'] == 'Home (Fastmail)'), {'name': 'Home'})
    with pytest.raises(cal.api.CalError, match="Several linked calendars are named 'Home'"):
        cal.api.create({'title': 'y', 'start': '2026-11-19 11:00'}, to='home')


def test_she_is_told_a_copy_is_old_every_time_and_what_cannot_be_answered(cal):
    d = cal.use_dav()
    d.add('standup.ics', SHARED.replace('mailto:bo@example.com', 'mailto:ann@example.com'))
    cal.accounts.save('default', cal.account['id'], {'username': 'ann@example.com'})
    cal.api.refresh('default', force=True)
    seen = cal.api.list_range('2026-11-23', '2026-11-23', 'default')[0]
    assert [g['name'] for g in seen['guests']] == ['cy@example.com'] and seen['my_rsvp'] == ''   # the owner is not their own guest
    msg, ok = _run(cal, 'calendar_update', id=seen['id'], rsvp='yes')
    assert not ok and 'Answering an invitation from here works on a Google calendar only' in msg
    d.down = 'dav.example is not answering right now (HTTP 503). Try again in a few minutes.'
    for link in cal.db.links('default'):
        cal.db.update_link(link['id'], {'synced_at': 1})                           # long ago: due for a read
    first, _ = _run(cal, 'calendar_range', start_date='2026-11-23', end_date='2026-11-23')
    calls = len(d.calls)
    second, _ = _run(cal, 'calendar_range', start_date='2026-11-23', end_date='2026-11-23')
    assert "'Home' could not be refreshed" in first and "'Home' could not be refreshed" in second   # not only the first time
    assert len(d.calls) == calls                                                    # and the server was not knocked on again


def test_a_server_that_is_down_is_asked_once_a_minute_and_a_double_press_adds_one(cal):
    d = cal.use_dav()
    cal.patch.setattr(cal.links, '_account', lambda calendar: {})
    cal.connect._offers.clear()
    d.down = 'dav.example did not answer in time.'
    for _ in range(3):
        state = cal.account_routes.get_accounts(query={})
        assert state['accounts'][0]['error'] == 'dav.example did not answer in time.'
        assert [c['name'] for c in state['accounts'][0]['calendars']] == ['Home', 'Team']     # still listed, to untick
    assert len(d.named('discover')) == 2                                            # the add, then one failed look
    d.down = None
    assert cal.account_routes.get_accounts(query={'fresh': '0'})['accounts'][0]['error']      # fresh=0 is not "fresh"
    assert cal.account_routes.get_accounts(query={'fresh': '1'})['accounts'][0]['error'] == ''
    cal.patch.setattr(cal.accounts, '_STORE_NAME', __import__('re').compile(r'nothing-fits'))
    seen = len(d.calls)
    with pytest.raises(cal.api.CalError, match='from an older version'):
        cal.connect.add_account('default', {'preset': 'yahoo', 'username': 'ann@yahoo.example', 'password': 'app-pass'})
    assert len(d.calls) == seen                                                     # refused before any login is tried


# -- found by the fourth scout: what an edit must never do to an event --------------------------

def _vevent(*lines):
    return 'BEGIN:VCALENDAR\nVERSION:2.0\nBEGIN:VEVENT\n' + '\n'.join(lines) + '\nEND:VEVENT\nEND:VCALENDAR\n'


def test_a_series_moved_within_its_day_leaves_bare_dates_where_they_are(cal):
    from datetime import timedelta
    w = cal.icsw
    doc = w.load(_vevent('UID:x', 'DTSTART;TZID=America/New_York:20261116T090000', 'DTEND;TZID=America/New_York:20261116T093000',
                         'RRULE:FREQ=WEEKLY;UNTIL=20261221', 'EXDATE;VALUE=DATE:20261130'))
    ev = doc.comps('VEVENT')[0]
    assert w.move_series(ev, timedelta(minutes=-30)) == 0                         # half an hour earlier is no day at all
    assert 'RRULE:FREQ=WEEKLY;UNTIL=20261221' in ev.items and 'EXDATE;VALUE=DATE:20261130' in ev.items
    assert w.move_series(ev, timedelta(hours=16)) == 1                            # 08:30 to 00:30 the next day is one
    assert 'RRULE:FREQ=WEEKLY;UNTIL=20261222' in ev.items and 'EXDATE;VALUE=DATE:20261201' in ev.items


def test_a_series_that_pins_its_days_is_not_dragged_to_another_day(cal):
    d = cal.use_dav()
    href = d.add('class.ics', _vevent('UID:class-1', 'SUMMARY:Class', 'DTSTART;TZID=America/New_York:20261102T090000',
                                      'DTEND;TZID=America/New_York:20261102T100000', 'RRULE:FREQ=WEEKLY;BYDAY=MO,WE',
                                      'EXDATE;TZID=America/New_York:20261109T090000'))
    cal.api.refresh('default', force=True)
    ev = cal.db.find(cal.home['id'], 'class-1')
    before = d.text(href)
    with pytest.raises(cal.api.CalError, match='repeats on set days'):              # Monday dragged to Wednesday, for all
        cal.api.update(ev['id'], {'start': '2026-11-18T09:00', 'end': '2026-11-18T10:00'}, shown='2026-11-16')
    assert d.text(href) == before
    cal.api.update(ev['id'], {'start': '2026-11-16T11:00', 'end': '2026-11-16T12:00'}, shown='2026-11-16')   # another time is fine
    assert 'DTSTART;TZID=America/New_York:20261102T110000' in d.text(href) and 'EXDATE;TZID=America/New_York:20261109T110000' in d.text(href)
    # 'weekly on Mondays' is the one pinned rule whose day can follow the start
    href = d.add('gym.ics', _vevent('UID:gym-1', 'SUMMARY:Gym', 'DTSTART;TZID=America/New_York:20261102T180000',
                                    'DTEND;TZID=America/New_York:20261102T190000', 'RRULE:FREQ=WEEKLY;BYDAY=MO',
                                    'EXDATE;TZID=America/New_York:20261109T180000'))
    cal.api.refresh('default', force=True)
    gym = cal.db.find(cal.home['id'], 'gym-1')
    cal.api.update(gym['id'], {'start': '2026-11-17T18:00', 'end': '2026-11-17T19:00'}, shown='2026-11-16')
    text = d.text(href)
    assert 'RRULE:FREQ=WEEKLY;BYDAY=TU' in text and 'DTSTART;TZID=America/New_York:20261103T180000' in text
    assert 'EXDATE;TZID=America/New_York:20261110T180000' in text                  # the day taken out is still taken out
    assert [s for s, t in _days(cal, '2026-11-02', '2026-11-18') if t == 'Gym'] == ['2026-11-03T18:00', '2026-11-17T18:00']


def test_a_new_title_never_moves_an_occurrence_or_flattens_another_apps_words(cal):
    d = cal.use_dav()
    href = d.add('visit.ics', _vevent('UID:visit-1', 'SUMMARY:Site visit',
                                      'DTSTART;TZID=W. Central Africa Standard Time:20261116T150000',
                                      'DTEND;TZID=W. Central Africa Standard Time:20261116T160000', 'RRULE:FREQ=WEEKLY',
                                      'LOCATION:Apple Park\\n1 Apple Park Way\\, Cupertino',
                                      'X-APPLE-STRUCTURED-LOCATION;VALUE=URI:geo:37.33,-122.00'))
    cal.api.refresh('default', force=True)
    ev = cal.db.find(cal.home['id'], 'visit-1')
    assert ev['location'] == 'Apple Park\n1 Apple Park Way, Cupertino'
    # one day gets a new title: its time stays the series' own, in the series' own words for its zone
    cal.api.update(ev['id'], {'title': 'Site visit (bring boots)', 'location': 'Apple Park 1 Apple Park Way, Cupertino'}, day='2026-11-23')
    text = d.text(href)
    assert 'RECURRENCE-ID;TZID=W. Central Africa Standard Time:20261123T150000' in text
    assert 'DTSTART;TZID=W. Central Africa Standard Time:20261123T150000' in text
    assert 'DTEND;TZID=W. Central Africa Standard Time:20261123T160000' in text and 'America/New_York' not in text
    assert text.count('LOCATION:Apple Park\\n1 Apple Park Way\\, Cupertino') == 2   # a one-line copy of it is not a change
    assert text.count('X-APPLE-STRUCTURED-LOCATION') == 2
    cal.api.update(ev['id'], {'location': 'Infinite Loop'})                         # a real change of place drops the old map pin
    text = d.text(href)
    assert 'LOCATION:Infinite Loop' in text and text.count('X-APPLE-STRUCTURED-LOCATION') == 1
    # the page sends only what was changed in the form
    from pathlib import Path
    js = (Path(__file__).resolve().parents[1] / 'app' / 'index.js').read_text(encoding='utf-8')
    assert "if (!edit || q('where').value !== opened.where) fields.location" in js and "if (!edit || when !== opened.when)" in js


def test_the_end_of_a_repeat_is_a_day_here_and_an_instant_in_the_file(cal):
    def rule(*lines, zone):
        from zoneinfo import ZoneInfo
        cal.patch.setattr(cal.api, '_zone', lambda: ZoneInfo(zone))
        cal.patch.setattr(cal.api, 'zone_name', lambda: zone)
        _n, rows, _notes = cal.ics.parse(_vevent('UID:u', *lines), cal.api.convert, zone)
        return rows[0]['rrule']
    # Sydney: the instant of the Nov 23 08:00 occurrence is Nov 22 in UTC
    assert rule('DTSTART;TZID=Australia/Sydney:20261102T080000', 'RRULE:FREQ=WEEKLY;UNTIL=20261122T210000Z',
                zone='Australia/Sydney') == 'FREQ=WEEKLY;UNTIL=20261123'
    # New York: the end of Nov 20 local is Nov 21 in UTC, and there is no Nov 21 occurrence
    assert rule('DTSTART;TZID=America/New_York:20261116T200000', 'RRULE:FREQ=DAILY;UNTIL=20261121T045959Z',
                zone='America/New_York') == 'FREQ=DAILY;UNTIL=20261120'
    assert rule('DTSTART;VALUE=DATE:20261116', 'RRULE:FREQ=DAILY;UNTIL=20261120', zone='America/New_York') == 'FREQ=DAILY;UNTIL=20261120'
    d = cal.use_dav()
    ev = cal.api.create({'title': 'Run', 'start': '2026-11-16 09:00', 'end': '09:30', 'repeat': 'weekly', 'until': '2026-12-21'})
    assert 'RRULE:FREQ=WEEKLY;UNTIL=20261222T045959Z' in d.text()                  # the last second of Dec 21 in New York
    assert ev['rrule'] == 'FREQ=WEEKLY;UNTIL=20261221'
    assert _days(cal, '2026-12-14', '2026-12-31') == [('2026-12-14T09:00', 'Run'), ('2026-12-21T09:00', 'Run')]
    allday = cal.api.create({'title': 'Trip', 'start': '2026-11-16', 'repeat': 'daily', 'until': '2026-11-18'})
    assert 'RRULE:FREQ=DAILY;UNTIL=20261118' in d.text(allday['href'])            # an all-day end stays a day


def test_a_change_is_made_on_what_is_really_there(cal):
    d = cal.use_dav()
    wk = cal.api.create({'title': 'Standup', 'start': '2026-11-16 09:00', 'end': '09:15', 'repeat': 'weekly'})
    text, _mark = d.files[wk['href']]
    d.files[wk['href']] = (text.replace('T090000', 'T100000').replace('T091500', 'T101500'), '"phone"')   # a phone moved it
    puts = len(d.named('put'))
    with pytest.raises(cal.api.CalError, match='was changed somewhere else since it was read here'):
        cal.api.update(wk['id'], {'title': 'Standup!'}, day='2026-11-23')
    assert len(d.named('put')) == puts and cal.db.get(wk['id'])['start'] == '2026-11-16T10:00'    # nothing sent; our copy caught up
    new = cal.api.update(wk['id'], {'title': 'Standup!'}, day='2026-11-23')           # again, on what is really there
    assert 'RECURRENCE-ID;TZID=America/New_York:20261123T100000' in d.text() and new['start'] == '2026-11-23T10:00'
    long = d.add('long.ics', _vevent('UID:long-1', 'SUMMARY:Minutes', 'DTSTART:20261119T150000Z', 'DESCRIPTION:' + 'word ' * 900))
    cal.api.refresh('default', force=True)
    ev = cal.db.find(cal.home['id'], 'long-1')
    cal.api.update(ev['id'], {'title': 'Minutes (final)'})                          # other words can still change
    assert d.text(long).count('word ') == 900
    with pytest.raises(cal.api.CalError, match='longer than this calendar keeps'):
        cal.api.update(ev['id'], {'notes': 'short now'})
    odd = d.add('hash.ics', _vevent('UID:meeting#42@host', 'SUMMARY:Sync', 'DTSTART:20261120T150000Z', 'DTEND:20261120T150000Z'))
    cal.api.refresh('default', force=True)
    ev = cal.db.find(cal.home['id'], 'meeting#42@host')
    cal.api.update(ev['id'], {'title': 'Sync (moved room)'})                        # a '#' in an id, and an event with no length
    assert 'SUMMARY:Sync (moved room)' in d.text(odd) and 'DTEND:20261120T150000Z' in d.text(odd)
    assert cal.api.remove(ev['id'])[0] == 'event' and odd not in d.files


def test_a_series_kept_in_another_zone_moves_by_what_the_person_moved(cal):
    d = cal.use_dav()
    href = d.add('tokyo.ics', _vevent('UID:tokyo-1', 'SUMMARY:Tokyo sync', 'DTSTART;TZID=Asia/Tokyo:20260706T230000',
                                      'DTEND;TZID=Asia/Tokyo:20260706T233000', 'RRULE:FREQ=WEEKLY'))
    cal.api.refresh('default', force=True)
    ev = cal.db.find(cal.home['id'], 'tokyo-1')
    assert _days(cal, '2026-11-23', '2026-11-23') == [('2026-11-23T09:00', 'Tokyo sync')]      # 23:00 Tokyo is 09:00 in New York in winter
    cal.api.update(ev['id'], {'start': '2026-11-23 10:00'})                         # she moves it an hour later, no date named for all
    text = d.text(href)
    assert 'DTSTART;TZID=Asia/Tokyo:20260707T000000' in text and 'DTEND;TZID=Asia/Tokyo:20260707T003000' in text   # one hour, from July on
    assert _days(cal, '2026-11-23', '2026-11-30') == [('2026-11-23T10:00', 'Tokyo sync'), ('2026-11-30T10:00', 'Tokyo sync')]


def test_an_event_that_cannot_be_read_exactly_is_not_edited(cal, monkeypatch):
    c = cal.caldav
    href = 'https://dav.example/u/ann/home/a.ics'
    body = 'BEGIN:VCALENDAR\nSUMMARY:caf\xe9\nEND:VCALENDAR'.encode('latin-1')
    wire = lambda method, url, **kw: SimpleNamespace(status_code=200, headers={'ETag': '"1"'}, close=lambda: None,
                                                      iter_content=lambda n: iter([body]))
    monkeypatch.setattr(c.net, 'request', wire)
    with pytest.raises(cal.api.CalError, match='cannot read exactly'):
        c.get(c.Login('https://dav.example/', 'ann', 'pw'), href)
