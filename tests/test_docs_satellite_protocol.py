# tests/test_docs_satellite_protocol.py - docs/SATELLITE-PROTOCOL.md is what
# firmware is written against. These tests run the page's own example through
# the real driver and hold its tables to the code, so the page cannot drift.
import inspect
import json
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from core.devices import voice
from core.devices.drivers import satellite as sat
from core.routes import devices as routes

PAGE = (Path(__file__).absolute().parent.parent / 'docs' / 'SATELLITE-PROTOCOL.md').read_text(encoding='utf-8')
DEV = {'id': 'den', 'label': 'Den', 'location': ''}
CFG = {'url': 'http://192.168.0.50', 'camera': True, 'chat': ''}


class Secrets(dict):
    def get(self, k, default=''):
        return dict.get(self, k, default)


def _example():
    return json.loads(re.search(r'```json\n(.*?)```', PAGE, re.S).group(1))


def _rows(heading):
    """The table rows under one heading, as lists of cells."""
    part = PAGE.split(heading, 1)[1].split('\n## ', 1)[0]
    rows = [[c.strip() for c in line.strip('|').split('|')]
            for line in part.splitlines() if line.startswith('|')]
    return [r for r in rows[2:] if r]


def test_the_health_example_runs_through_the_real_driver():
    said = _example()
    reply = SimpleNamespace(status_code=200, content=b'', json=lambda: said)
    sat._about.clear()
    with patch.object(sat.net, 'request', lambda method, url, **kw: reply):
        st = sat.status(DEV, CFG, Secrets(token='board-key-12345678'))
    sat._about.clear()
    assert st['online'] is True and st['has'] == said['has']
    assert st['detail'].startswith(said['name'])
    assert st['readings']['program'] == f"{said['board']} {said['firmware']}"
    assert st['readings']['volume'] == f"{said['volume']}%"
    assert st['readings']['link to Sapphire'] == 'connected'
    assert set(said['has']) <= set(sat.SPEC['capabilities'])
    assert voice.wanted(said['plays']) == said['plays']


def test_the_page_names_every_capability_a_satellite_can_have():
    line = re.search(r'\*\*`has`\*\* takes these names: (.*?)\.', PAGE, re.S).group(1)
    assert sorted(re.findall(r'`(\w+)`', line)) == sorted(sat.SPEC['capabilities'])


def test_every_address_in_the_table_is_one_the_driver_calls():
    source = inspect.getsource(sat)
    rows = _rows('## What Sapphire asks of the board')
    assert len(rows) >= 11
    for has, request, _ in rows:
        assert has.strip('`') in sat.SPEC['capabilities']
        path = re.search(r'(/[a-z/]+)', request).group(1)
        assert path in source, f"the page lists {path}, the driver never calls it"
    assert sorted({r[0].strip('`') for r in rows}) == sorted(sat.SPEC['capabilities'])


def test_the_light_states_are_the_ones_core_sends():
    states = [r[0].strip('`') for r in _rows('### What its light should show')]
    assert sorted(states) == sorted(voice.CUES + ('connected',))


def test_the_limits_on_the_page_are_the_limits_in_the_code():
    assert f"{voice.MAX_AUDIO // (1024 * 1024)} MB" in PAGE
    assert f"{routes.VOICE_PER_MIN} requests a\nminute" in PAGE
    assert f"every {routes.STREAM_QUIET} seconds" in PAGE
    assert f"within {sat.SPEAK_WAIT} s" in PAGE
    assert f"`rate`\nis {voice.RATES[0]} to {voice.RATES[1]}" in PAGE
    assert 'audio/wav' in routes.AUDIO_BODIES
