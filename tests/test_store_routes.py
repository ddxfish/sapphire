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
