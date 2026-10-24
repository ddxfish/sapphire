"""Browser conversation mode survives a network drop (the 2026-10-24 report).

A drop parks the session server-side; the tab's retry resumes it and the mode
never flaps. A deliberate Off still ends it at once. A network that stays dead
past the grace ends the mode, and the tab that comes back later starts fresh.
"""
import time

import pytest

from conftest import api, server_log, wait_until

GRACE_S = 45
KEEPALIVE_S = 40      # ping interval + timeout: how long a server may take to notice a silent drop


def _mode():
    return api('/api/runtime/true-speech')


def _browser_on():
    m = _mode()
    return m['enabled'] and m['source'] == 'browser'


def _click(page, mode):
    page.evaluate(f"document.querySelector('[data-conv={mode}]').click()")


@pytest.fixture
def conversing(page, relay):
    if _mode()['enabled']:
        pytest.skip('conversation mode is already on (someone is using it)')
    sockets = []
    page.on('websocket', lambda ws: sockets.append(ws) if '/ws/conversation' in ws.url else None)
    with page.expect_console_message(lambda m: '[Init] Complete' in m.text, timeout=15000):
        page.goto(relay.url + '/#chat')           # the segment's click handler lands with init
    _click(page, 'browser')
    assert wait_until(_browser_on, 10, page=page), 'Browser mic did not turn conversation mode on'
    yield page, relay, sockets
    _click(page, 'off')
    if not wait_until(lambda: not _mode()['enabled'], 10):
        api('/api/runtime/true-speech', 'PUT', {'enabled': False})


def test_drop_parks_and_resumes(conversing):
    page, relay, sockets = conversing
    t0 = time.time()
    relay.drop()
    page.wait_for_timeout(500)
    assert _browser_on(), 'the drop ended the mode instead of parking it'
    assert wait_until(lambda: len(sockets) >= 2, 15, page=page), 'the tab never reconnected'
    # the server either saw the drop first (parked, then resumed) or the tab's
    # retry won the race (the old socket half-open: taken over) - never a flap
    adopted = lambda: any(w in (server_log(t0) or 'resumed') for w in ('session resumed', 'taken over'))
    assert wait_until(adopted, 10), 'the new socket did not adopt the session'
    assert _browser_on()
    log = server_log(t0)
    if log is not None:
        assert 'conversation mode OFF' not in log and 'ending conversation mode' not in log
    assert not page.errors


def test_off_ends_at_once(conversing):
    page, relay, sockets = conversing
    t0 = time.time()
    _click(page, 'off')
    assert wait_until(lambda: not _mode()['enabled'], 5), 'Off did not end the mode'
    page.wait_for_timeout(3000)
    assert len(sockets) == 1, 'the tab reconnected after a deliberate Off'
    log = server_log(t0)
    if log is not None:
        assert 'parked' not in log


@pytest.mark.skipif(not __import__('os').environ.get('E2E_SLOW'), reason='takes up to 2 min: set E2E_SLOW=1')
def test_dead_network_ends_mode_then_tab_starts_fresh(conversing):
    page, relay, sockets = conversing
    relay.refusing = True
    relay.drop()
    assert wait_until(lambda: not _mode()['enabled'], KEEPALIVE_S + GRACE_S + 10, step=1), 'the grace never ran out'
    relay.refusing = False
    assert wait_until(_browser_on, 40, step=1), 'the tab did not come back and start fresh'
