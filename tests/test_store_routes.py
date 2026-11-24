"""Regression tests for the in-app Plugin Store proxy.

Pure-helper tests catch silent normalization/version-compare regressions during
future loops. Endpoint tests prove the auth-gated routes mount and that the
graceful-empty fallback keeps shape stable when the upstream store is down —
that fallback is what stops the Store view from going blank on transient WP
unreachability, so it's load-bearing.
"""
import pytest

from core.routes import store as store_mod


# ─── Pure helpers ────────────────────────────────────────────────────────────

def test_normalize_url_collapses_github_variants():
    """All four shapes of a github repo URL must collide on the normalized form."""
    canonical = "https://github.com/shroomshaolin/lantern"
    variants = [
        "https://github.com/shroomshaolin/Lantern.git",
        "https://github.com/shroomshaolin/Lantern",
        "https://github.com/shroomshaolin/Lantern/tree/main",
        "https://GitHub.com/shroomshaolin/Lantern/",
    ]
    for v in variants:
        assert store_mod._normalize_url(v) == canonical, f"failed: {v}"


def test_normalize_url_handles_garbage():
    assert store_mod._normalize_url("") == ""
    assert store_mod._normalize_url(None) == ""
    # query + fragment stripped
    assert store_mod._normalize_url("https://github.com/a/b?x=1#frag") == "https://github.com/a/b"


def test_install_state_is_conservative():
    """Never falsely tell the user there's an update.

    Empty versions, unparseable versions, and downgrade scenarios must all
    return 'current' — we'd rather miss an update prompt than render a wrong one.
    """
    assert store_mod._install_state("1.0.12", "1.0.10") == "update_available"
    assert store_mod._install_state("1.0.10", "1.0.10") == "current"
    # local newer than store -> never prompt
    assert store_mod._install_state("1.0.0", "2.0.0") == "current"
    # empty fields -> conservative
    assert store_mod._install_state("", "1.0.0") == "current"
    assert store_mod._install_state("1.0.0", "") == "current"
    # unparseable -> conservative
    assert store_mod._install_state("weird", "1.0.0") == "current"


# ─── Endpoint shape tests ────────────────────────────────────────────────────

def test_store_status_returns_config(client, monkeypatch):
    c, _ = client
    resp = c.get("/api/store/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "enabled" in data
    assert "base" in data
    assert "cache_ttl" in data
    assert isinstance(data["cache_ttl"], int)


def test_store_list_graceful_when_upstream_unreachable(client, monkeypatch):
    """When the upstream WP store is down, list returns shape-stable empty.

    The Store view depends on this not raising — UI shows an empty state, not
    an error. Killing this guard would silently break the view on transient WP
    outages.
    """
    async def _fail(path, params=None, namespace=None):
        return store_mod._graceful_empty(path, params or {})

    monkeypatch.setattr(store_mod, "_proxy_get", _fail)

    resp = client[0].get("/api/store/plugins/list?featured=true")
    assert resp.status_code == 200
    data = resp.json()
    assert data["items"] == []
    assert data["total"] == 0
    assert data.get("unreachable") is True


def test_store_list_annotates_installed_state(client, monkeypatch):
    """Items get installed_state / local_version / local_name attached.

    Frontend Install/Installed/Update buttons branch on installed_state — if
    annotation regresses, every store card mislabels.
    """
    async def _fake_fetch(path, params=None, namespace=None):
        return {
            "items": [
                {
                    "slug": "lantern",
                    "name": "Lantern",
                    "github_url": "https://github.com/shroomshaolin/Lantern.git",
                    "version": "1.0.12",
                    "author": "Donna",
                    "category": "communication",
                    "trust_level": "community",
                    "featured": False,
                    "votes_up": 0, "votes_down": 0, "vote_ratio": 0,
                    "screenshot_url": "", "author_url": "",
                    "description": "", "status": "approved",
                    "created_at": "", "updated_at": "",
                },
                {
                    "slug": "stranger",
                    "name": "Unknown",
                    "github_url": "https://github.com/example/never-installed",
                    "version": "0.1.0",
                    "author": "", "category": "tools",
                    "trust_level": "community", "featured": False,
                    "votes_up": 0, "votes_down": 0, "vote_ratio": 0,
                    "screenshot_url": "", "author_url": "",
                    "description": "", "status": "approved",
                    "created_at": "", "updated_at": "",
                },
            ],
            "total": 2, "page": 1, "per_page": 20, "pages": 1,
        }

    def _fake_index():
        return {
            "https://github.com/shroomshaolin/lantern": {
                "name": "lantern",
                "version": "1.0.10",  # older than store -> update_available
                "store_slug": "lantern",
                "source": "store",
            }
        }

    monkeypatch.setattr(store_mod, "_proxy_get", _fake_fetch)
    monkeypatch.setattr(store_mod, "_build_install_index", _fake_index)

    resp = client[0].get("/api/store/plugins/list")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert items[0]["installed_state"] == "update_available"
    assert items[0]["local_version"] == "1.0.10"
    assert items[0]["local_name"] == "lantern"
    assert items[1]["installed_state"] == "none"
    assert items[1]["local_version"] is None
    assert items[1]["local_name"] is None


def test_store_disabled_returns_503(client, monkeypatch):
    """STORE_ENABLED=false short-circuits with 503 before any upstream fetch."""
    monkeypatch.setattr(store_mod, "_store_enabled", lambda: False)
    resp = client[0].get("/api/store/plugins/list")
    assert resp.status_code == 503


# ─── The spotlight's daily background copy (2026-11-24) ──────────────────────

class _Answer:
    def __init__(self, status, body=None, etag=None):
        self.status_code, self._body, self.headers = status, body, ({"ETag": etag} if etag else {})

    def json(self):
        return self._body


@pytest.fixture
def spotlight(tmp_path, monkeypatch):
    """The store module with its copy in a tmp file and a fake store behind httpx.Client."""
    asked = []
    box = {"answer": _Answer(200, {"items": [{"slug": "a", "name": "A"}], "total": 1}, 'W/"v1"')}    # weak, as the live store sends it

    class Client:
        def __init__(self, **kw): box['client'] = kw
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, url, params=None, headers=None):
            asked.append({"url": url, "params": params, "headers": headers or {}})
            return box["answer"]
    monkeypatch.setattr(store_mod, "_spotlight_file", tmp_path / "store_spotlight.json")
    monkeypatch.setattr(store_mod.httpx, "Client", Client)
    monkeypatch.setattr(store_mod, "_store_enabled", lambda: True)
    return store_mod, asked, box


def test_the_spotlight_asks_once_a_day_and_an_unchanged_list_costs_no_download(spotlight):
    s, asked, box = spotlight
    assert s._spotlight_tick() is True                               # nothing saved yet: ask
    assert asked[0]["params"] == s.SPOTLIGHT_PARAMS and "If-None-Match" not in asked[0]["headers"]
    assert asked[0]["url"].endswith("/wp-json/sapphire-store/v1/items")
    agent = box["client"]["headers"]["User-Agent"]                   # she says who she is, and which version
    assert agent == s._user_agent()["User-Agent"] and __import__("re").fullmatch(r"Sapphire/[0-9][0-9A-Za-z.+-]*", agent)
    first = s._spotlight_load()
    assert first["etag"] == 'W/"v1"' and first["payload"]["items"][0]["slug"] == "a"
    assert s._spotlight_tick() is False and len(asked) == 1          # fresh: a wake looks at the age and asks nothing
    # a day later: the store says "unchanged", the copy is kept and its clock restarts
    old = {**first, "fetched_at": first["fetched_at"] - s.SPOTLIGHT_EVERY - 5}
    s._spotlight_path().write_text(__import__("json").dumps(old))
    box["answer"] = _Answer(304)
    assert s._spotlight_tick() is True
    assert asked[1]["headers"] == {"If-None-Match": '"v1"'}       # the plain form: the store never matched its own W/ tag
    assert s._tag_to_send('"v1"') == '"v1"' and s._tag_to_send(None) is None
    again = s._spotlight_load()
    assert again["payload"] == first["payload"] and s._spotlight_age(again) < 60
    assert not list(s._spotlight_path().parent.glob("*.tmp"))


def test_the_spotlight_never_asks_with_the_store_switched_off(spotlight, monkeypatch):
    s, asked, _ = spotlight
    monkeypatch.setattr(s, "_store_enabled", lambda: False)
    assert s._spotlight_tick() is False and asked == []
    assert s._spotlight_load() is None


def test_a_store_that_is_down_or_talking_nonsense_leaves_the_copy_alone(spotlight):
    s, asked, box = spotlight
    assert s._spotlight_tick()
    kept = s._spotlight_load()
    stale = {**kept, "fetched_at": kept["fetched_at"] - s.SPOTLIGHT_EVERY - 5}
    s._spotlight_path().write_text(__import__("json").dumps(stale))
    for bad in (_Answer(500), _Answer(200, ["not", "a", "dict"]), _Answer(304)):
        box["answer"] = bad
        if bad.status_code == 304:
            s._spotlight_path().unlink()                             # a 304 with nothing saved is no answer at all
            assert s._spotlight_tick() is False and s._spotlight_load() is None
        else:
            assert s._spotlight_tick() is False and s._spotlight_load() == stale
    s._spotlight_path().write_text("{broken")
    assert s._spotlight_load() is None                               # an unreadable copy is no copy


def test_a_clock_set_back_does_not_freeze_the_spotlight(spotlight):
    s, asked, _ = spotlight
    assert s._spotlight_tick()
    ahead = {**s._spotlight_load(), "fetched_at": __import__("time").time() + 10 * 86400}
    s._spotlight_path().write_text(__import__("json").dumps(ahead))
    assert s._spotlight_tick() is True and len(asked) == 2


def test_the_dashboard_is_served_the_saved_copy_and_nothing_else_is(client, spotlight, monkeypatch):
    s, asked, _ = spotlight
    c, _ = client
    assert s._spotlight_tick()
    live = []

    async def _proxy(path, params=None, namespace=None):
        live.append(params)
        return {"items": [{"slug": "live"}], "total": 1}
    monkeypatch.setattr(s, "_proxy_get", _proxy)
    monkeypatch.setattr(s, "_build_install_index", lambda: {})
    got = c.get("/api/store/plugins/list", params={"featured": "true", "per_page": 50}).json()
    assert [i["slug"] for i in got["items"]] == ["a"] and live == []          # the widget's own question: the copy
    assert "installed_state" in got["items"][0]                               # still marked installed or not, fresh each time
    for other in ({"per_page": 50}, {"featured": "true", "per_page": 50, "page": 2},
                  {"featured": "true", "per_page": 50, "category": "tools"}, {"featured": "true", "per_page": 5, "sort": "votes"}):
        assert c.get("/api/store/plugins/list", params=other).json()["items"][0]["slug"] == "live"
    assert len(live) == 4
    # a copy nobody refreshed for two days is not trusted: the live path answers
    old = {**s._spotlight_load(), "fetched_at": __import__("time").time() - 2 * s.SPOTLIGHT_EVERY - 5}
    s._spotlight_path().write_text(__import__("json").dumps(old))
    assert c.get("/api/store/plugins/list", params={"featured": "true", "per_page": 50}).json()["items"][0]["slug"] == "live"


def test_the_settings_page_spotlight_is_cut_from_the_same_copy(client, spotlight, monkeypatch):
    # it asks for the top 5, the dashboard widget for all 50: one saved list answers both (found by clicking, 2026-11-24)
    s, asked, box = spotlight
    c, _ = client
    box["answer"] = _Answer(200, {"items": [{"slug": f"p{i}"} for i in range(7)], "total": 7, "page": 1, "per_page": 50, "pages": 1}, 'W/"v2"')
    assert s._spotlight_tick()

    async def _never(*a, **k):
        raise AssertionError("the spotlight went to the store by itself")
    monkeypatch.setattr(s, "_proxy_get", _never)
    monkeypatch.setattr(s, "_build_install_index", lambda: {})
    five = c.get("/api/store/plugins/list", params={"featured": "true", "per_page": 5}).json()
    assert [i["slug"] for i in five["items"]] == ["p0", "p1", "p2", "p3", "p4"]
    assert (five["total"], five["per_page"], five["pages"], five["page"]) == (7, 5, 2, 1)
    assert len(c.get("/api/store/plugins/list", params={"featured": "true", "per_page": 50}).json()["items"]) == 7
    assert len(asked) == 1                                           # one request to the store served both


def test_no_test_reads_the_real_copy_or_starts_the_thread():
    assert store_mod._spotlight_path() is None and store_mod._spotlight_load() is None
    assert store_mod._spotlight_refresh() is False
    assert store_mod.start_spotlight_refresher() is None
