# tests/test_devices_health.py - the health keeper (core/devices/health.py):
# the thread that asks devices on its own clock. The belief rules themselves
# (one answer = online, two misses = offline, the last state survives a
# restart) are tested through the engine in test_devices_engine.py. Here the
# engine is a fake, so only the keeper is under test.
import threading
import time
import types
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest

from core.devices import health


def until(pred, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.01)
    return pred()


class Engine:
    def __init__(self):
        self.table = {'pi': {'id': 'pi', 'enabled': True}, 'desk': {'id': 'desk', 'enabled': True},
                      'shelf': {'id': 'shelf', 'enabled': False}}
        self.asked = []
        self.answers = {}
        self.hold = threading.Event()
        self.hold.set()
        self._pool = ThreadPoolExecutor(max_workers=4)
        self.retold = 0

    def refusal(self):
        return ''

    def retell(self):
        self.retold += 1

    def rows(self):
        return {k: dict(v) for k, v in self.table.items()}

    def _probe(self, row):
        self.hold.wait(2)
        self.asked.append(row['id'])
        return {'online': self.answers.get(row['id'], True), 'parts': [], 'ts': time.time()}


@pytest.fixture
def rig():
    e = Engine()
    store = types.SimpleNamespace(get=lambda k, d=None: d, save=lambda k, v: None)
    with patch.object(health, '_engine', lambda: e), patch.object(health, '_store', lambda: store), \
         patch.object(health, 'SETTLE', 0), patch.object(health, 'POLL', 0.2), \
         patch.object(health, 'POLL_DOWN', 1.0):
        health._belief.clear()
        health._saved = None
        health._halt.clear()
        health._wake.clear()
        try:
            yield e
        finally:
            e.hold.set()
            health.stop()
            if health._keeper:
                health._keeper.join(2)
            e._pool.shutdown(wait=True)       # inside the patch: a late probe must not reach the real store
            health._belief.clear()
            health._saved = None


def test_the_keeper_asks_every_enabled_device_and_keeps_asking(rig):
    assert health.start() and health.start()           # twice is fine
    assert until(lambda: {'pi', 'desk'} <= set(rig.asked))
    assert 'shelf' not in rig.asked                     # turned off: never asked
    n = len(rig.asked)
    assert until(lambda: len(rig.asked) >= n + 2)       # the clock came round again
    assert health.view('pi')['online'] is True
    assert rig.retold == 2                              # two devices went online: told twice, no more


def test_a_device_that_is_down_is_asked_less_often(rig):
    rig.answers['desk'] = False
    health.start()
    assert until(lambda: health.view('desk')['misses'] >= 2 and not health.view('desk')['online'], 4)
    rig.asked.clear()
    time.sleep(0.8)
    assert rig.asked.count('pi') >= 2 and rig.asked.count('desk') <= 1


def test_a_poke_asks_now_and_a_probe_in_flight_is_not_doubled(rig):
    health.start()
    assert until(lambda: 'pi' in rig.asked)
    rig.hold.clear()                                    # the device is slow to answer
    assert until(lambda: health.view('pi')['checking'])  # the clock came round while it hangs
    rig.asked.clear()
    health.poke('pi')
    time.sleep(0.3)
    assert rig.asked == []                              # one probe at a time per device
    rig.hold.set()
    assert until(lambda: 'pi' in rig.asked and not health.view('pi')['checking'])


def test_stop_ends_the_keeper(rig):
    health.start()
    assert until(lambda: 'pi' in rig.asked)
    health.stop()
    assert until(lambda: not health._keeper.is_alive())
