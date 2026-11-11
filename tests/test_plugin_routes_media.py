"""Plugin route option `media` (2026-10-10): a file door for a <video>, <audio>
or <img>. Its GETs skip the per-plugin rate meter (a seeking player fires Range
requests in bursts; a thumbnail grid is one GET per tile); everything else
about the route, and every other verb on it, is unchanged. An unknown route is
refused before it is metered."""
import pytest

ROUTES_SRC = '''
def bytes_door(vid=None, **_):
    return {"vid": vid}

def api_door(**_):
    return {"ok": True}
'''
NAME = 'zz-media-test'


@pytest.fixture
def loader(tmp_path):
    from core.plugin_loader import plugin_loader
    d = tmp_path / NAME
    (d / 'routes').mkdir(parents=True)
    (d / 'routes' / 'doors.py').write_text(ROUTES_SRC)
    plugin_loader._register_routes(NAME, d, [
        {'method': 'GET', 'path': 'video/{vid}', 'handler': 'routes/doors.py:bytes_door', 'media': True},
        {'method': 'PUT', 'path': 'video/{vid}', 'handler': 'routes/doors.py:bytes_door', 'media': True},
        {'method': 'GET', 'path': 'list', 'handler': 'routes/doors.py:api_door'},
        {'method': 'GET', 'path': 'status', 'handler': 'routes/doors.py:api_door', 'live': True},
    ])
    yield plugin_loader
    plugin_loader._unregister_routes(NAME)


def test_media_option_registers_and_defaults_off(loader):
    handler, params, opts = loader.get_route_handler(NAME, 'GET', 'video/abc')
    assert params == {'vid': 'abc'} and opts == {'media': True, 'live': False}
    assert handler(vid='abc') == {'vid': 'abc'}
    _h, _p, opts = loader.get_route_handler(NAME, 'GET', 'list')
    assert opts == {'media': False, 'live': False}
    assert loader.get_route_handler(NAME, 'GET', 'nope') is None


def test_media_get_skips_the_meter_everything_else_is_metered(client, loader, monkeypatch):
    c, csrf = client
    import core.routes.plugins as pr
    import core.auth as auth
    metered = []
    monkeypatch.setattr(pr, '_check_plugin_bearer', lambda *a, **k: True)       # bearer ok = login skipped
    monkeypatch.setattr(auth, 'check_endpoint_rate', lambda req, ep, **kw: metered.append(ep))

    r = c.get(f'/api/plugin/{NAME}/video/abc')
    assert r.status_code == 200 and r.json() == {'vid': 'abc'}
    assert metered == []                                     # the media door is not metered

    r = c.get(f'/api/plugin/{NAME}/list')
    assert r.status_code == 200 and metered == [f'plugin_route:{NAME}:GET']

    r = c.put(f'/api/plugin/{NAME}/video/abc', json={}, headers={'X-CSRF-Token': csrf})
    assert metered[-1] == f'plugin_route:{NAME}:PUT'          # media covers GET only

    before = len(metered)
    assert c.get(f'/api/plugin/{NAME}/nope').status_code == 404
    assert len(metered) == before                            # refused before it is metered


def test_a_live_feed_or_its_poll_skips_the_meter(client, loader, monkeypatch):
    """`live` (2026-11-11): FlashHead's panel polls status every 3 s; two panels
    and a Watch burst past 60 GETs a minute and showed "status unavailable"."""
    c, _csrf = client
    import core.routes.plugins as pr
    import core.auth as auth
    metered = []
    monkeypatch.setattr(pr, '_check_plugin_bearer', lambda *a, **k: True)
    monkeypatch.setattr(auth, 'check_endpoint_rate', lambda req, ep, **kw: metered.append(ep))
    _h, _p, opts = loader.get_route_handler(NAME, 'GET', 'status')
    assert opts == {'media': False, 'live': True}
    assert c.get(f'/api/plugin/{NAME}/status').status_code == 200 and metered == []
