"""[REGRESSION_GUARD] Persona-store payload size.

2026-06-24: the persona LIST endpoint must NOT ship `export_content` (the grid
doesn't use it; it was ~82% of the payload). The DETAIL endpoint must KEEP it
(import + overwrite-preview need the bundle).
"""
import asyncio

import core.api_fastapi  # noqa: F401 — load app so route modules init cleanly
import core.routes.store as store


def test_persona_list_strips_export_content(client, monkeypatch):
    c, _csrf = client

    async def fake_proxy_get(path, params=None, namespace=None):
        return {"items": [{"slug": "x", "sapphire_name": "X", "tagline": "t",
                           "export_content": "B" * 5000}],
                "total": 1, "page": 1, "per_page": 20, "pages": 1}
    monkeypatch.setattr(store, "_proxy_get", fake_proxy_get)

    r = c.get("/api/store/personas/list")
    assert r.status_code == 200, r.text
    items = r.json().get("items")
    assert items, r.text
    assert "export_content" not in items[0]      # stripped
    assert items[0]["sapphire_name"] == "X"      # display fields survive


def test_persona_detail_keeps_export_content(client, monkeypatch):
    c, _csrf = client

    async def fake_proxy_get(path, params=None, namespace=None):
        return {"slug": "x", "sapphire_name": "X", "export_content": "BUNDLE"}
    monkeypatch.setattr(store, "_proxy_get", fake_proxy_get)

    r = c.get("/api/store/personas/x")
    assert r.status_code == 200, r.text
    assert r.json().get("export_content") == "BUNDLE"  # detail keeps the bundle


# ── conditional GET: a stale entry with an ETag revalidates (If-None-Match);
#    a 304 reuses the cached body with zero re-download ──────────────────
class _FakeResp:
    def __init__(self, status_code, json_data=None, etag=None):
        self.status_code = status_code
        self._json = json_data
        self.headers = {"ETag": etag} if etag else {}
    def json(self):
        return self._json


class _FakeClient:
    def __init__(self, responses):
        self._responses = responses
        self.calls = []
    async def __aenter__(self):
        return self
    async def __aexit__(self, *a):
        return False
    async def get(self, url, params=None, headers=None):
        self.calls.append({"url": url, "headers": headers or {}})
        return self._responses.pop(0)


def test_proxy_conditional_get_304_reuses_cache(monkeypatch):
    store._cache.clear()
    fake = _FakeClient([
        _FakeResp(200, {"items": [], "total": 0}, etag='"v1"'),  # first fetch
        _FakeResp(304),                                          # revalidation
    ])
    monkeypatch.setattr(store.httpx, "AsyncClient", lambda **kw: fake)

    async def scenario():
        d1 = await store._proxy_get("/items", {"page": 1}, namespace="ns")
        # force the entry stale so the next call revalidates
        key = store._cache_key("/items", {"page": 1}, "ns")
        store._cache[key]["expires_at"] = 0
        d2 = await store._proxy_get("/items", {"page": 1}, namespace="ns")
        return d1, d2

    d1, d2 = asyncio.run(scenario())
    assert d1 == {"items": [], "total": 0}
    assert d2 == {"items": [], "total": 0}                      # reused via 304
    assert fake.calls[1]["headers"].get("If-None-Match") == '"v1"'
    store._cache.clear()

