"""The Apps menu: the Apps item's own flyout, holding the apps the person
pinned on the Apps page (2026-11-02). A shipped plugin may start pinned with
`nav: "apps"`; a user-band plugin saying the same lands on the grid.
"""
import asyncio
from pathlib import Path

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).absolute().parent.parent
STATIC = ROOT / 'interfaces' / 'web' / 'static'


class _Loader:
    def __init__(self, plugins):
        self._plugins = plugins


def _plugin(band, app=None, loaded=True):
    caps = {'app': app} if app is not None else {}
    return {'band': band, 'loaded': loaded, 'path': '/nowhere', 'manifest': {'capabilities': caps}}


class _Req:
    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body


@pytest.fixture
def mod(monkeypatch, tmp_path):
    import core.api_fastapi  # noqa: F401  the app module imports the routes; the routes import it back
    from core.routes import plugins as mod
    monkeypatch.setattr(mod, 'APPS_PINS_FILE', tmp_path / 'apps_pins.json')
    monkeypatch.setattr('core.plugin_loader.plugin_loader', _Loader({
        'social': _plugin('system', {'label': 'Social', 'nav': 'apps'}),
        'fan-made': _plugin('user', {'label': 'Fan', 'nav': 'apps'}),
        'status': _plugin('system', {'label': 'Status'}),
        'game-room': _plugin('system', {'label': 'Game Room', 'nav': 'chat'}),
        'off': _plugin('system', {'label': 'Off', 'nav': 'apps'}, loaded=False),
    }))
    return mod


def _by_name(mod):
    return {a['name']: a for a in mod._list_apps()}


def test_only_a_shipped_plugin_starts_pinned(mod):
    apps = _by_name(mod)
    assert apps['social']['pinned'] is True and apps['social']['nav'] is False
    assert apps['fan-made']['pinned'] is False and apps['fan-made']['nav'] is False   # on the grid like any other
    assert apps['status']['pinned'] is False
    assert apps['game-room']['nav'] == 'chat' and apps['game-room']['pinned'] is False
    assert 'off' not in apps


def test_the_persons_pins_win_and_only_their_choices_are_kept(mod):
    out = asyncio.run(mod.set_app_pin(_Req({'name': 'fan-made', 'pinned': True}), _=None))
    assert {a['name']: a['pinned'] for a in out['apps']}['fan-made'] is True
    asyncio.run(mod.set_app_pin(_Req({'name': 'social', 'pinned': False}), _=None))
    apps = _by_name(mod)
    assert apps['social']['pinned'] is False and apps['fan-made']['pinned'] is True
    assert mod._read_pins() == {'fan-made': True, 'social': False}
    asyncio.run(mod.set_app_pin(_Req({'name': 'social', 'pinned': 1}), _=None))
    assert _by_name(mod)['social']['pinned'] is True


def test_an_unknown_app_or_one_with_its_own_home_is_refused(mod):
    with pytest.raises(HTTPException) as e:
        asyncio.run(mod.set_app_pin(_Req({'name': 'doom', 'pinned': True}), _=None))
    assert e.value.status_code == 404
    with pytest.raises(HTTPException) as e:
        asyncio.run(mod.set_app_pin(_Req({'name': 'game-room', 'pinned': True}), _=None))
    assert e.value.status_code == 400
    assert mod._read_pins() == {}


def test_a_broken_pins_file_reads_as_no_choices(mod):
    mod.APPS_PINS_FILE.parent.mkdir(parents=True, exist_ok=True)
    mod.APPS_PINS_FILE.write_text('[1, 2', encoding='utf-8')
    assert mod._read_pins() == {} and _by_name(mod)['social']['pinned'] is True


def test_the_rail_treats_the_apps_item_as_a_group_with_an_empty_menu_being_no_menu():
    html = (ROOT / 'interfaces' / 'web' / 'templates' / 'index.html').read_text(encoding='utf-8')
    assert '<div class="nav-item nav-group-parent" data-view="apps" id="nav-apps"' in html
    router = (STATIC / 'core' / 'router.js').read_text(encoding='utf-8')
    assert "apps: ['apps']" in router
    rail = (STATIC / 'core' / 'nav-rail.js').read_text(encoding='utf-8')
    assert "item.querySelector('.nav-flyout-item')" in rail and "!parent.querySelector('.nav-flyout-item')" in rail
    seats = (STATIC / 'core' / 'plugin-apps.js').read_text(encoding='utf-8')
    assert "a.nav !== 'apps'" in seats          # a plugin can't seat itself on the menu by naming it as a group
