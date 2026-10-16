"""core.watchdog: a wedged teardown ends the process with the expected code
after dumping every thread's stack (2026-10-16, row 12)."""
import time

from core import watchdog


def test_fires_with_the_code_evaluated_at_that_moment(monkeypatch, caplog):
    exits, dumps = [], []
    monkeypatch.setattr(watchdog.os, '_exit', lambda code: exits.append(code))
    monkeypatch.setattr(watchdog.faulthandler, 'dump_traceback', lambda **k: dumps.append(k))
    wanted = {'code': 1}
    with caplog.at_level('ERROR', logger='core.watchdog'):
        t = watchdog.arm(0.1, lambda: wanted['code'], what='test-teardown')
        wanted['code'] = 42                    # decided after arming, read when it fires
        t.join(timeout=3)
    assert exits == [42]
    assert dumps and dumps[0].get('all_threads') is True
    assert any('[WATCHDOG]' in r.message and 'test-teardown' in r.message for r in caplog.records)


def test_a_finished_teardown_can_cancel_it(monkeypatch):
    exits = []
    monkeypatch.setattr(watchdog.os, '_exit', lambda code: exits.append(code))
    t = watchdog.arm(0.2, 0)
    t.cancel()
    time.sleep(0.4)
    assert exits == []


def test_timer_is_a_daemon_so_it_never_blocks_exit():
    t = watchdog.arm(30, 0)
    try:
        assert t.daemon is True and t.name.startswith('watchdog-')
    finally:
        t.cancel()
