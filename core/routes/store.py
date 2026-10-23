# core/routes/store.py — In-app Plugin Store proxy.
#
# Proxies the read-only public REST API of sapphireblue.dev's bazaar plugin
# (or any compatible store) and annotates each item with local install state
# so the UI can render Install / Installed / Update buttons without a second
# round-trip.
#
# Anonymous browse (no telemetry), 5-minute in-memory cache, fails graceful
# on store unreachability. Auth-gated like the rest of /api/* — keeps the
# Store inside Sapphire's session perimeter.

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request

import config
from core.auth import require_login

logger = logging.getLogger(__name__)
router = APIRouter()

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
PLUGIN_STATE_DIR = PROJECT_ROOT / "user" / "plugin_state"
SYSTEM_PLUGINS_DIR = PROJECT_ROOT / "plugins"
USER_PLUGINS_DIR = PROJECT_ROOT / "user" / "plugins"

# ── Cache ────────────────────────────────────────────────────────────
# Keyed by (namespace, path, sorted query). Stores {expires_at, payload, etag}.
# In-memory and per-process — restart clears it. Conditional GET: when an entry
# is stale but we hold its ETag, we revalidate with If-None-Match and a 304
# reuses the cached body (zero re-download) — once the store sends ETags.
_cache: dict[tuple, dict] = {}
_cache_lock = asyncio.Lock()


def _cache_ttl() -> float:
    try:
        return float(config.STORE_CACHE_TTL_SECONDS or 1800)
    except AttributeError:
        return 1800.0


def _cache_key(path: str, params: dict, namespace: Optional[str] = None) -> tuple:
    items = tuple(sorted((k, str(v)) for k, v in params.items() if v is not None))
    return (namespace or "", path, items)


# ── Store URL helpers ────────────────────────────────────────────────

# Persona store rides the same bazaar engine under a different WP namespace.
PERSONA_NAMESPACE = "sapphire-prompts/v1"


def _store_base(namespace: Optional[str] = None) -> str:
    try:
        base = (config.STORE_URL or "https://sapphireblue.dev").rstrip("/")
    except AttributeError:
        base = "https://sapphireblue.dev"
    if namespace:
        ns = namespace.strip("/")
    else:
        try:
            ns = (config.STORE_NAMESPACE or "sapphire-store/v1").strip("/")
        except AttributeError:
            ns = "sapphire-store/v1"
    return f"{base}/wp-json/{ns}"


def _store_enabled() -> bool:
    try:
        return bool(config.STORE_ENABLED)
    except AttributeError:
        return True


# ── URL normalization for install-state matching ─────────────────────
# Matches a store item's github_url against installed plugins'
# `installed_from` URL. GitHub URLs come in many shapes — we collapse
# them so /repo, /repo.git, and /repo/tree/main all collide.

def _normalize_url(url: str) -> str:
    if not url:
        return ""
    s = url.strip().lower()
    # strip query + fragment
    for sep in ("#", "?"):
        i = s.find(sep)
        if i >= 0:
            s = s[:i]
    # strip trailing slash
    while s.endswith("/"):
        s = s[:-1]
    # strip .git suffix
    if s.endswith(".git"):
        s = s[:-4]
    # strip /tree/<branch> or /blob/<branch>/... — keep just the repo root
    for marker in ("/tree/", "/blob/"):
        i = s.find(marker)
        if i >= 0:
            s = s[:i]
    return s


# ── Install index ────────────────────────────────────────────────────

def _read_plugin_version(plugin_name: str) -> str:
    """Read version from a plugin's manifest. Empty string if not found."""
    for parent in (SYSTEM_PLUGINS_DIR, USER_PLUGINS_DIR):
        manifest = parent / plugin_name / "plugin.json"
        if not manifest.exists():
            continue
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            return str(data.get("version") or "")
        except Exception:
            return ""
    return ""


def _build_install_index() -> dict[str, dict]:
    """
    Walk user/plugin_state/*.json. Pick out plugins with `installed_from`,
    pair with the manifest version, return dict keyed by normalized URL.

    {normalized_url: {name, version, store_slug, source}}
    """
    index: dict[str, dict] = {}
    if not PLUGIN_STATE_DIR.exists():
        return index
    for state_file in PLUGIN_STATE_DIR.glob("*.json"):
        try:
            data = json.loads(state_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        url = data.get("installed_from") or data.get("store_url")
        if not url or not isinstance(url, str):
            continue
        plugin_name = state_file.stem
        version = _read_plugin_version(plugin_name)
        normalized = _normalize_url(url)
        if not normalized:
            continue
        index[normalized] = {
            "name": plugin_name,
            "version": version,
            "store_slug": data.get("store_slug"),
            "source": data.get("source"),
        }
    return index


# ── Semver compare ───────────────────────────────────────────────────
# Conservative — when in doubt, return "current". Never falsely tell
# the user there's an update. Shared parser: core/versions.py (M1).

def _install_state(store_version: str, local_version: str) -> str:
    if not local_version:
        return "current"
    from core.versions import is_newer
    return "update_available" if is_newer(store_version, local_version) else "current"


def _annotate_item(item: dict, install_index: dict[str, dict]) -> dict:
    """Add installed_state, local_version, local_name fields to an item."""
    github_url = (item.get("github_url") or "").strip()
    normalized = _normalize_url(github_url)
    installed = install_index.get(normalized)
    if not installed:
        item["installed_state"] = "none"
        item["local_version"] = None
        item["local_name"] = None
        return item
    item["local_version"] = installed.get("version") or None
    item["local_name"] = installed.get("name")
    item["installed_state"] = _install_state(
        item.get("version") or "",
        installed.get("version") or "",
    )
    return item


# ── HTTP fetch ───────────────────────────────────────────────────────

async def _proxy_get(path: str, params: Optional[dict] = None,
                     namespace: Optional[str] = None) -> dict:
    """
    Proxy a GET to the store, cached for STORE_CACHE_TTL_SECONDS. A fresh entry
    is served from cache (no network). A stale entry is revalidated with a
    conditional GET (If-None-Match) — a 304 reuses the cached body and resets
    the timer. On error / non-2xx, serves the last cached body if we have one,
    else a graceful-empty payload so the UI renders cleanly.
    """
    params = params or {}
    key = _cache_key(path, params, namespace)
    now = time.monotonic()

    async with _cache_lock:
        entry = _cache.get(key)
    if entry and now <= entry["expires_at"]:
        return entry["payload"]  # fresh — no network

    headers = {}
    if entry and entry.get("etag"):
        headers["If-None-Match"] = entry["etag"]

    url = f"{_store_base(namespace)}{path}"
    try:
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
            resp = await client.get(
                url,
                params={k: v for k, v in params.items() if v is not None},
                headers=headers,
            )
    except httpx.HTTPError as e:
        logger.warning(f"[store] fetch failed: {path} — {e}")
        return entry["payload"] if entry else _graceful_empty(path, params)

    if resp.status_code == 304 and entry:
        async with _cache_lock:
            entry["expires_at"] = now + _cache_ttl()
            _cache[key] = entry
        return entry["payload"]  # unchanged — zero re-download

    if resp.status_code != 200:
        logger.warning(f"[store] {path} returned {resp.status_code}")
        return entry["payload"] if entry else _graceful_empty(path, params)

    try:
        data = resp.json()
    except ValueError as e:
        logger.warning(f"[store] bad json: {path} — {e}")
        return entry["payload"] if entry else _graceful_empty(path, params)

    async with _cache_lock:
        _cache[key] = {"expires_at": now + _cache_ttl(), "payload": data,
                       "etag": resp.headers.get("ETag")}
    return data


async def _proxy_get_bytes(path: str, namespace: Optional[str] = None) -> Optional[bytes]:
    """Fetch a binary asset (e.g. a persona card PNG) from the store. Not cached
    — installs are rare and the payload is large. Returns None on failure."""
    url = f"{_store_base(namespace)}{path}"
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(url)
        if resp.status_code != 200:
            logger.warning(f"[store] binary {path} returned {resp.status_code}")
            return None
        return resp.content
    except httpx.HTTPError as e:
        logger.warning(f"[store] binary fetch failed: {path} — {e}")
        return None


def _graceful_empty(path: str, params: dict) -> dict:
    """Shape-stable empty result so the frontend always renders cleanly."""
    if path.endswith("/categories"):
        return []
    if "/items/" in path:
        # detail endpoint — null-shape but the UI handles 'unreachable'
        return {"unreachable": True}
    return {
        "items": [],
        "total": 0,
        "page": 1,
        "per_page": int(params.get("per_page") or 20),
        "pages": 0,
        "unreachable": True,
    }


# ── Endpoints ────────────────────────────────────────────────────────

def _ensure_enabled():
    if not _store_enabled():
        raise HTTPException(status_code=503, detail="Store is disabled.")


async def _items_query(q, category, featured, sort, page, per_page,
                       namespace: Optional[str] = None, annotate: bool = False) -> dict:
    """Shared list/search query. q triggers FULLTEXT search, otherwise filtered
    list. `annotate` adds install-state (plugins only — personas have no
    github_url/install concept)."""
    page = max(1, int(page))
    per_page = max(1, min(50, int(per_page)))

    if q and len(q.strip()) >= 2:
        path = "/items/search"
        params = {"q": q.strip(), "page": page, "per_page": per_page}
    else:
        path = "/items"
        params = {"page": page, "per_page": per_page}
        if category:
            params["category"] = category
        if sort:
            params["sort"] = sort
        if featured is True:
            params["featured"] = "true"

    data = await _proxy_get(path, params, namespace=namespace)
    if annotate and "items" in data and isinstance(data["items"], list):
        index = _build_install_index()
        data["items"] = [_annotate_item(dict(it), index) for it in data["items"]]
    return data


@router.get("/api/store/plugins/list")
async def store_list(
    request: Request,
    q: Optional[str] = None,
    category: Optional[str] = None,
    featured: Optional[bool] = None,
    sort: Optional[str] = None,
    page: int = 1,
    per_page: int = 20,
    _=Depends(require_login),
):
    """List or search plugins. q triggers FULLTEXT search, otherwise filtered list."""
    _ensure_enabled()
    return await _items_query(q, category, featured, sort, page, per_page, annotate=True)


@router.get("/api/store/plugins/{slug}")
async def store_detail(slug: str, _=Depends(require_login)):
    """Detail page for one plugin. Includes long_description."""
    _ensure_enabled()
    # The bazaar route regex constrains slugs to [a-z0-9\-]+ — if we send
    # anything else WP returns 404, no harm.
    data = await _proxy_get(f"/items/{slug}")
    if data.get("unreachable"):
        raise HTTPException(status_code=503, detail="Store unreachable.")
    if data.get("github_url") is not None:
        index = _build_install_index()
        data = _annotate_item(dict(data), index)
    return data


@router.get("/api/store/categories")
async def store_categories(_=Depends(require_login)):
    """All categories with counts. Returns empty list if unreachable."""
    _ensure_enabled()
    data = await _proxy_get("/categories")
    if isinstance(data, list):
        return data
    return []


@router.get("/api/store/status")
async def store_status(_=Depends(require_login)):
    """Light health check for the UI to know whether to show Store affordances."""
    base = _store_base()
    return {
        "enabled": _store_enabled(),
        "base": base,
        "cache_ttl": int(_cache_ttl()),
    }


# ── Persona store ────────────────────────────────────────────────────
# Same bazaar engine, `sapphire-prompts/v1` namespace. No install-state
# annotation (personas have no github_url). Install pulls the PNG card and
# runs it through the app's existing persona-card importer.

@router.get("/api/store/personas/list")
async def store_personas_list(
    request: Request,
    q: Optional[str] = None,
    category: Optional[str] = None,
    featured: Optional[bool] = None,
    sort: Optional[str] = None,
    page: int = 1,
    per_page: int = 20,
    _=Depends(require_login),
):
    """List or search personas."""
    _ensure_enabled()
    data = await _items_query(q, category, featured, sort, page, per_page,
                              namespace=PERSONA_NAMESPACE, annotate=False)
    # The grid only needs display fields. Drop export_content (the full prompt
    # bundle — ~82% of the payload); the detail/import path re-fetches it per
    # persona. Build new item dicts so the in-memory cache copy stays intact.
    if isinstance(data.get("items"), list):
        data = dict(data)
        data["items"] = [
            {k: v for k, v in it.items() if k != "export_content"}
            for it in data["items"]
        ]
    return data


@router.get("/api/store/personas/categories")
async def store_personas_categories(_=Depends(require_login)):
    """Persona categories with counts. Empty list if unreachable."""
    _ensure_enabled()
    data = await _proxy_get("/categories", namespace=PERSONA_NAMESPACE)
    return data if isinstance(data, list) else []


@router.get("/api/store/personas/{slug}")
async def store_personas_detail(slug: str, _=Depends(require_login)):
    """Detail page for one persona."""
    _ensure_enabled()
    data = await _proxy_get(f"/items/{slug}", namespace=PERSONA_NAMESPACE)
    if data.get("unreachable"):
        raise HTTPException(status_code=503, detail="Store unreachable.")
    return data


@router.post("/api/store/personas/{slug}/install")
async def store_personas_install(slug: str,
                                 overwrite_prompt: bool = False,
                                 overwrite_avatar: bool = False,
                                 overwrite_persona: bool = False,
                                 keep_components: str = "",
                                 _=Depends(require_login)):
    """Download a persona's PNG card and import it through the app's existing
    card importer. 409 if a persona exists and overwrite_persona isn't set."""
    _ensure_enabled()
    raw = await _proxy_get_bytes(f"/items/{slug}/card", namespace=PERSONA_NAMESPACE)
    if not raw:
        raise HTTPException(status_code=502, detail="Could not download persona card.")
    from core.routes.content import _import_persona_card_bytes
    keep = [s for s in keep_components.split(",") if s]
    return await _import_persona_card_bytes(raw, overwrite_prompt, overwrite_avatar, overwrite_persona, keep)
