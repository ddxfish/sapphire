"""core/redact.py: one scrubber for logs, tool results, UI text and URLs."""
import logging
import socket

import pytest
import requests

from core.redact import LogFilter, describe, is_network, scrub

SECRET = 'EAABsecretPageToken123'


def _closed_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_requests_error_text_really_holds_the_query():
    """The premise: a connection failure's message carries the full URL."""
    with pytest.raises(requests.ConnectionError) as ei:
        requests.get(f'http://127.0.0.1:{_closed_port()}/v1/me', params={'access_token': SECRET}, timeout=5)
    assert SECRET in str(ei.value)
    said = describe(ei.value)
    assert is_network(ei.value) and SECRET not in said
    assert said == 'network error reaching 127.0.0.1 (ConnectionError)'


def test_scrub_query_values_and_bearer():
    t = scrub(f"url: /x?fields=id%2Cname&access_token={SECRET}&limit=5 and "
              f"client_secret=abc123def&code=xyz Authorization: Bearer {SECRET}")
    assert SECRET not in t and 'abc123def' not in t
    assert 'fields=id%2Cname' in t and 'limit=5' in t
    assert 'code=xyz' not in t and scrub('see ?code=oauthcode1') == 'see ?code=[secret]'
    assert scrub('zipcode=12345 barcode=9') == 'zipcode=12345 barcode=9'   # whole names only


def test_scrub_known_values_longest_first_and_by_line():
    assert scrub('key abcdefgh and abcd', ['abcd', 'abcdefgh']) == 'key [secret] and [secret]'
    pem = '-----BEGIN KEY-----\nMIIEsecretline\n-----END KEY-----'
    assert 'MIIEsecretline' not in scrub('saw MIIEsecretline in output', [pem])
    assert scrub('abc', ['abc']) == 'abc'                # too short to blank safely
    assert scrub('none', [None, '']) == 'none'


def test_describe_other_errors_scrubs_values():
    assert describe(ValueError(f'bad {SECRET}'), [SECRET]) == 'ValueError: bad [secret]'


def test_log_filter_cleans_message_args_and_traceback():
    seen = []

    class H(logging.Handler):
        def emit(self, record):
            seen.append(self.format(record))
    h = H()
    h.setFormatter(logging.Formatter('%(message)s'))
    h.addFilter(LogFilter())
    log = logging.getLogger('test_redact.filter')
    log.addHandler(h)
    log.propagate = False
    try:
        log.warning('GET %s', f'https://graph.facebook.com/me?access_token={SECRET}')
        try:
            raise RuntimeError(f'failed at /me?access_token={SECRET}')
        except RuntimeError:
            log.error('boom', exc_info=True)
    finally:
        log.removeHandler(h)
    assert len(seen) == 2 and all(SECRET not in s for s in seen)
    assert 'access_token=[secret]' in seen[0] and 'RuntimeError' in seen[1]


def test_device_secrets_scrub_still_works():
    from core.devices.secret_store import Secrets
    s = Secrets('dev1', {'ssh.password': 'hunter22', 'ssh.key': 'line one\nline two'})
    assert s.scrub('pw hunter22 then line two') == 'pw [secret] then [secret]'
