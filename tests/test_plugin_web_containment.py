"""Containment tests for /plugin-web (2026-08-05 war campaign, finding 0.1).

The three lanes (web/, app/, stories/) must each serve ONLY their own
subtree, and the stories lane is images-only — the room JSONs beside the
backdrops carry puzzle solutions and stay engine-side. Traversal uses
%2e%2e because clients normalize literal dot-segments away; the encoded
form is what actually reaches the route.
"""
import pytest


@pytest.fixture
def fake_plugins(tmp_path, monkeypatch):
    """A scratch plugin band with a pack-shaped plugin + a name-prefix sibling."""
    import core.api_fastapi as api

    band = tmp_path / "plugins"
    pdir = band / "testpack"
    (pdir / "web").mkdir(parents=True)
    (pdir / "web" / "ok.js").write_text("// web lane", encoding="utf-8")
    (pdir / "app").mkdir()
    (pdir / "app" / "index.js").write_text("// app lane", encoding="utf-8")
    (pdir / "app" / "sneak.jpg").write_bytes(b"\xff\xd8not-a-backdrop")
    story = pdir / "stories" / "demo"
    (story / "backdrops").mkdir(parents=True)
    (story / "backdrops" / "scene.jpg").write_bytes(b"\xff\xd8fake-jpeg")
    (story / "rooms").mkdir()
    (story / "rooms" / "1-vault.json").write_text('{"solution": "SECRET"}', encoding="utf-8")
    (pdir / "secret.py").write_text("TOKEN = 'do-not-serve'", encoding="utf-8")

    # Sibling whose name shares testpack as a string prefix — the old
    # startswith() check let this one's files through.
    evil = band / "testpack-evil"
    (evil / "web").mkdir(parents=True)
    (evil / "web" / "e.js").write_text("// sibling", encoding="utf-8")

    monkeypatch.setattr(api, "SYSTEM_PLUGINS_DIR", band)
    monkeypatch.setattr(api, "USER_PLUGINS_DIR_WEB", tmp_path / "user-plugins")
    return pdir


def _get(client, url):
    c, _csrf = client
    return c.get(url)


def test_lanes_serve_their_own_files(client, fake_plugins):
    assert _get(client, "/plugin-web/testpack/ok.js").status_code == 200
    assert _get(client, "/plugin-web/testpack/app/index.js").status_code == 200
    assert _get(client, "/plugin-web/testpack/stories/demo/backdrops/scene.jpg").status_code == 200


def test_js_serves_as_module_mime(client, fake_plugins):
    # Windows HKCR can poison mimetypes with .js → text/plain; the add_type
    # pin must win or every ES module page dies silently (finding 0.2).
    r = _get(client, "/plugin-web/testpack/ok.js")
    assert r.headers["content-type"].startswith("text/javascript")


def test_stories_lane_is_images_only(client, fake_plugins):
    r = _get(client, "/plugin-web/testpack/stories/demo/rooms/1-vault.json")
    assert r.status_code == 404
    assert b"SECRET" not in r.content


def test_app_lane_traversal_refused(client, fake_plugins):
    # app/../secret.py resolves inside the plugin dir — the old prefix check
    # passed it. Lane containment must refuse anything outside app/.
    r = _get(client, "/plugin-web/testpack/app/%2e%2e/secret.py")
    assert r.status_code == 404
    assert b"do-not-serve" not in r.content


def test_stories_lane_traversal_refused(client, fake_plugins):
    # An image by suffix, but outside stories/ — must not serve.
    r = _get(client, "/plugin-web/testpack/stories/%2e%2e/app/sneak.jpg")
    assert r.status_code == 404


def test_plugin_name_dotdot_refused(client, fake_plugins):
    # plugin_name=".." re-rooted the whole candidate walk at the repo.
    r = _get(client, "/plugin-web/%2e%2e/user/anything")
    assert r.status_code == 404
    # The VERIFIED exploit shape (post-fix review 2026-08-05): the bare
    # /user/... path routed through the web lane and 404'd even against the
    # vulnerable code — the real arbitrary-read needed the app lane on top
    # of the re-rooted walk. This URL SERVED before the fix.
    r = _get(client, "/plugin-web/%2e%2e/app/%2e%2e/user/anything")
    assert r.status_code == 404


def test_sibling_prefix_dir_refused(client, fake_plugins):
    # startswith("/plugins/testpack") matched "/plugins/testpack-evil/..."
    r = _get(client, "/plugin-web/testpack/app/%2e%2e/%2e%2e/testpack-evil/web/e.js")
    assert r.status_code == 404
