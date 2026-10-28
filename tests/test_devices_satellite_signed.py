# tests/test_devices_satellite_signed.py - calls signed with the board's key (firmware auth.c,
# 0.9.4 / 0.5.2): the key never travels, a board once seen signing is never sent it again, a
# board from before still gets Bearer. The board below checks exactly as auth.c does.
import hashlib
import hmac
import time
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlsplit

import pytest

from core.devices.drivers import satellite as sat

CFG = {'url': 'http://192.168.0.4'}
KEY = 'board-key-abcdefgh'


class Secrets(dict):
    def get(self, k, default=''):
        return dict.get(self, k, default)


SECRETS = Secrets(token=KEY)


class Reply:
    def __init__(self, status=200, headers=None, data=None):
        self.status_code, self.headers, self._data = status, headers or {}, data if data is not None else {'ok': True}

    def json(self):
        return self._data


class Board:
    """auth.c in Python: its own clock, a window, each signature once."""

    def __init__(self, signs=True, clock_ms=10_000):
        self.signs, self.offset, self.seen, self.calls = signs, clock_ms - int(time.time() * 1000), set(), []

    def now(self):
        return int(time.time() * 1000) + self.offset

    def __call__(self, method, url, **kw):
        auth = kw['headers'].get('Authorization', '')
        u = urlsplit(url)
        uri = u.path + ('?' + u.query if u.query else '')
        self.calls.append(SimpleNamespace(method=method, uri=uri, auth=auth, kw=kw))
        if auth == 'Bearer ' + KEY:
            return Reply()
        refused = Reply(401, {'X-Board-Time': str(self.now())} if self.signs else {})
        if not self.signs or not auth.startswith('Sapphire '):
            return refused
        f = dict(x.split('=', 1) for x in auth[9:].split(','))
        ts = int(f['ts'])
        if abs(ts - self.now()) > 30_000 or (ts, f['n']) in self.seen:
            return refused
        text = '\n'.join([method, uri, f['ts'], f['n'], kw['headers'].get('X-Sha256', '')])
        if not hmac.compare_digest(hmac.new(KEY.encode(), text.encode(), hashlib.sha256).hexdigest(), f['sig']):
            return refused
        self.seen.add((ts, f['n']))
        return Reply()


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(sat, 'SIGNED_FILE', tmp_path / 'signed.json')
    monkeypatch.setattr(sat, '_signed', None)
    monkeypatch.setattr(sat, '_old', {})
    monkeypatch.setattr(sat, '_skew', {})
    return tmp_path


def call(board, method='GET', path='/health', **kw):
    with patch.object(sat.net, 'request', board):
        return sat._call(method, path, CFG, SECRETS, **kw)


def test_a_new_board_learns_our_clock_and_never_sees_the_key(fresh):
    b = Board()
    assert call(b).status_code == 200
    assert [c.auth.split(' ')[0] for c in b.calls] == ['Sapphire', 'Sapphire']   # refused once (clock), then in
    call(b, 'POST', '/audio/listen?vad=false&seconds=3')
    assert len(b.calls) == 3                                                      # the clock is kept
    assert not any(KEY in c.auth for c in b.calls)
    assert sat._key_id(KEY) in (fresh / 'signed.json').read_text()


def test_the_signature_covers_the_query_and_the_program_hash():
    b = Board()
    call(b)
    call(b, 'PUT', '/firmware', data=b'program', headers={'X-Sha256': 'ab' * 32})
    assert b.calls[-1].auth.startswith('Sapphire ') and b.calls[-1].kw['headers']['X-Sha256'] == 'ab' * 32


def test_a_board_from_before_gets_the_key_and_is_asked_signed_again_later():
    b = Board(signs=False)
    assert call(b).status_code == 200
    assert [c.auth.split(' ')[0] for c in b.calls] == ['Sapphire', 'Bearer']
    call(b)
    assert b.calls[-1].auth.split(' ')[0] == 'Bearer' and len(b.calls) == 3      # remembered: one call
    sat._old[sat._key_id(KEY)] -= sat.OLD_AGAIN_S + 1
    b.signs = True                                                                # it was updated
    call(b)
    assert b.calls[-1].auth.startswith('Sapphire ') and sat._key_id(KEY) in sat._signed


def test_a_forged_old_board_refusal_cannot_downgrade_a_board_that_signs(fresh):
    call(Board())                                                                 # seen signing: kept on disk
    sat._signed = None                                                            # ... across a restart
    forged = Board(signs=False)
    with pytest.raises(sat.Problem, match='refused the key'):
        call(forged)
    assert not any(c.auth.startswith('Bearer') for c in forged.calls)


def test_a_body_sent_once_learns_the_clock_first():
    b = Board()
    def chunks():
        yield b'piece'
    call(b, 'PUT', '/storage/x.sapphirebak', data=chunks())
    assert [c.uri for c in b.calls] == ['/health', '/health', '/storage/x.sapphirebak']
    assert b.calls[-1].auth.startswith('Sapphire ')


def test_a_404_does_not_mark_a_board_as_signing():
    b = Board(signs=False)
    with patch.object(sat.net, 'request', lambda m, u, **kw: Reply(404, data={'detail': 'no'})):
        with pytest.raises(sat.Missing):
            sat._call('GET', '/nothing', CFG, SECRETS)
    assert not sat._signed_ids()
