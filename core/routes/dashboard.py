"""Dashboard widgets API.

Three endpoints power the user's panel curation:
  GET  /api/dashboard/widgets             — current panel list (auto-seeds defaults)
  PUT  /api/dashboard/widgets             — replace the list (add/remove/reorder/resize)
  GET  /api/dashboard/widgets/available   — catalog of all registered widgets

Persistence: user/webui/dashboard.json with schema version 1.
Validation: each saved panel must reference a registered widget; entries
referencing missing plugins are kept (so users see "(unavailable)" rather
than silent removal — Stage 3 surfaces the placeholder UI).
"""
import asyncio
import json
import logging
import re
import uuid
from pathlib import Path

from fastapi import APIRouter, Request, Depends, HTTPException

from core.auth import require_login
from core.dashboard_widgets import list_widgets, get_widget
from core.fs_utils import replace_with_retry

# Serialize PUTs to dashboard.json — without this, two near-simultaneous
# saves (drag + delete in flight, multi-tab edits) can interleave at the
# file level and last-writer-wins. Race scout finding 2026-05-07.
_DASHBOARD_LOCK = asyncio.Lock()

logger = logging.getLogger(__name__)
router = APIRouter()

PROJECT_ROOT = Path(__file__).parent.parent.parent
DASHBOARD_FILE = PROJECT_ROOT / "user" / "webui" / "dashboard.json"

# Default panel list seeded on first install. Order matters — these are
# what shows on a fresh dashboard. User can remove/reorder later.
DEFAULT_PANELS = [
    {"plugin": "core", "widget_id": "system",         "size": "1x1"},
    {"plugin": "core", "widget_id": "updates",        "size": "1x1"},
    {"plugin": "core", "widget_id": "backups",        "size": "1x1"},
    {"plugin": "core", "widget_id": "maintenance",    "size": "1x1"},
    {"plugin": "core", "widget_id": "mini-spotlight", "size": "1x1"},
]

VALID_SIZES = {"1x1", "1x2", "1x3", "1x4"}
MAX_PANELS = 32                # Sanity cap — prevents accidental DoS via crafted PUT
MAX_SETTINGS_BYTES = 4 * 1024  # Per-panel settings cap (in JSON bytes)
MAX_TOTAL_BYTES = 64 * 1024    # Whole-file cap


def _new_instance_id() -> str:
    return uuid.uuid4().hex[:12]


def _seed_defaults() -> dict:
    """Build the initial dashboard.json content."""
    return {
        "version": 1,
        "panels": [
            {
                "instance_id": _new_instance_id(),
                "plugin": p["plugin"],
                "widget_id": p["widget_id"],
                "size": p["size"],
                "settings": {},
            }
            for p in DEFAULT_PANELS
        ],
    }


def _load() -> dict:
    """Read the dashboard file. Auto-seeds and writes defaults if missing
    or malformed (so a fresh install has a working dashboard immediately).

    On parse failure, the malformed file is preserved as `dashboard.json.bak`
    before reseeding — so a one-shot corruption doesn't silently destroy
    the user's customizations forever. 2026-05-07 chaos-scout finding."""
    if not DASHBOARD_FILE.exists():
        DASHBOARD_FILE.parent.mkdir(parents=True, exist_ok=True)
        data = _seed_defaults()
        DASHBOARD_FILE.write_text(json.dumps(data, indent=2), encoding='utf-8')
        return data
    try:
        # utf-8-sig: locale-decoded reads on Windows + a PowerShell BOM
        # otherwise fall into the reseed path and reset the user's layout
        data = json.loads(DASHBOARD_FILE.read_text(encoding='utf-8-sig'))
        if not isinstance(data, dict) or "panels" not in data:
            raise ValueError("missing 'panels'")
        return data
    except Exception as e:
        backup = DASHBOARD_FILE.with_suffix(DASHBOARD_FILE.suffix + ".bak")
        try:
            backup.write_bytes(DASHBOARD_FILE.read_bytes())
        except Exception as backup_err:
            logger.warning(f"dashboard.json backup before reseed failed: {backup_err}")
        logger.warning(
            f"dashboard.json malformed ({e}); reseeding defaults — "
            f"prior contents saved to {backup.name}"
        )
        data = _seed_defaults()
        DASHBOARD_FILE.write_text(json.dumps(data, indent=2), encoding='utf-8')
        return data


def _save(data: dict) -> None:
    """Atomic-write the dashboard file."""
    DASHBOARD_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = DASHBOARD_FILE.with_suffix(DASHBOARD_FILE.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding='utf-8')
    replace_with_retry(tmp, DASHBOARD_FILE)


def _validate_panel(p: dict) -> tuple[bool, str]:
    """Return (ok, reason). Each panel must have plugin/widget_id/size and
    be registered. Settings must be a dict and small."""
    if not isinstance(p, dict):
        return False, "not an object"
    plugin = p.get("plugin")
    widget_id = p.get("widget_id")
    size = p.get("size", "1x1")
    if not isinstance(plugin, str) or not re.match(r"^[a-z0-9_\-]{1,64}$", plugin, re.I):
        return False, "invalid plugin"
    if not isinstance(widget_id, str) or not re.match(r"^[a-z0-9_\-]{1,64}$", widget_id, re.I):
        return False, "invalid widget_id"
    if size not in VALID_SIZES:
        return False, f"invalid size {size!r}"
    settings = p.get("settings", {})
    if not isinstance(settings, dict):
        return False, "settings must be an object"
    if len(json.dumps(settings)) > MAX_SETTINGS_BYTES:
        return False, "settings too large"
    return True, ""


# =============================================================================
# ENDPOINTS
# =============================================================================

@router.get("/api/dashboard/widgets")
async def get_dashboard_widgets(_=Depends(require_login)):
    """Current user's panel list. Auto-seeds with defaults on first read.
    Each panel is annotated with its current registry status — `available`
    is False if the widget's plugin is no longer loaded (frontend renders
    a placeholder instead of trying to import a missing module)."""
    data = _load()
    panels = []
    for p in data.get("panels", []):
        spec = get_widget(p.get("plugin"), p.get("widget_id"))
        panels.append({
            "instance_id":     p.get("instance_id") or _new_instance_id(),
            "plugin":          p.get("plugin"),
            "widget_id":       p.get("widget_id"),
            "size":            p.get("size", "1x1"),
            "settings":        p.get("settings", {}) or {},
            "available":       spec is not None,
            "render_url":      spec.render_url if spec else None,
            "name":            spec.name if spec else p.get("widget_id"),
            "sizes":           spec.sizes if spec else ["1x1"],
            "settings_schema": spec.settings_schema if spec else [],
        })
    return {"version": data.get("version", 1), "panels": panels}


@router.put("/api/dashboard/widgets")
async def put_dashboard_widgets(request: Request, _=Depends(require_login)):
    """Replace the user's panel list (add / remove / reorder / resize all
    flow through here). Validates each entry; rejects the whole PUT if
    anything's malformed so we never half-write a corrupt list."""
    body = await request.body()
    if len(body) > MAX_TOTAL_BYTES:
        raise HTTPException(status_code=413, detail="dashboard.json too large")
    try:
        payload = json.loads(body)
    except Exception:
        raise HTTPException(status_code=400, detail="invalid JSON")

    panels = payload.get("panels")
    if not isinstance(panels, list):
        raise HTTPException(status_code=400, detail="panels must be an array")
    if len(panels) > MAX_PANELS:
        raise HTTPException(status_code=400, detail=f"too many panels (max {MAX_PANELS})")

    cleaned: list[dict] = []
    for i, p in enumerate(panels):
        ok, reason = _validate_panel(p)
        if not ok:
            raise HTTPException(status_code=400, detail=f"panel[{i}]: {reason}")
        cleaned.append({
            "instance_id": p.get("instance_id") or _new_instance_id(),
            "plugin": p["plugin"],
            "widget_id": p["widget_id"],
            "size": p.get("size", "1x1"),
            "settings": p.get("settings") or {},
        })

    # Lock around the actual write so concurrent PUTs serialize. Validation
    # above is already done by this point (intentionally outside the lock —
    # cheap and parallelizable) so contention only covers the file write.
    async with _DASHBOARD_LOCK:
        _save({"version": 1, "panels": cleaned})
    return {"status": "ok", "count": len(cleaned)}


@router.get("/api/dashboard/widgets/available")
async def get_available_widgets(_=Depends(require_login)):
    """Catalog for the picker modal — every registered widget, grouped by
    plugin. Built-ins (plugin=='core') typically appear first because they
    register at app boot before plugins load."""
    items = []
    for spec in list_widgets():
        items.append({
            "plugin":          spec.plugin,
            "widget_id":       spec.widget_id,
            "name":            spec.name,
            "description":     spec.description,
            "icon":            spec.icon,
            "sizes":           spec.sizes,
            "default_size":    spec.default_size,
            "multi_instance":  spec.multi_instance,
            "settings_schema": spec.settings_schema,
            "render_url":      spec.render_url,
        })
    return {"widgets": items}
