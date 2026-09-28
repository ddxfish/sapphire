# core/routes/devices.py - Settings > Devices (tmp/device-manager-plan.md)
#
# Thin doors into core/devices/engine.py. Adding, changing and removing
# devices happens here and ONLY here: no tool can do it.
#
# The engine is imported on first use, never at boot, so a fault in it cannot
# stop Sapphire starting. Engine calls can wait on a slow device, so each runs
# off the event loop.
import asyncio
import logging

from fastapi import APIRouter, Request, Depends, HTTPException

from core.auth import require_login, check_endpoint_rate

logger = logging.getLogger(__name__)

router = APIRouter()

READS_PER_MIN = 240        # the page polls its list, and a device window reads detail
WRITES_PER_MIN = 60        # saves, tests, and Try buttons


def _engine():
    from core.devices import engine
    return engine


# --- what the doors do (plain functions, tested directly) --------------------

def _view(row, status=None):
    e = _engine()
    out = e.public(row)
    out["capabilities"] = e.describe(row) if row.get("enabled", True) else []
    out["status"] = status
    return out


def _saved(row, failed):
    out = {"device": _view(row)}
    if failed:
        out["warning"] = ("Saved, but these could not be stored: " + ", ".join(failed)
                          + ". Enter them again.")
    return out


def list_devices():
    e = _engine()
    found = e.statuses(wait_s=0)      # never blocks the page; answers land in the cache
    devices = []
    for device_id, row in e.rows().items():
        v = e.public(row)
        devices.append({"id": v["id"], "label": v["label"], "enabled": v["enabled"],
                        "capabilities": [c["capability"] for c in e.describe(row) if not c["error"]],
                        "missing": [p["driver"] for p in v["parts"] if not p["available"]],
                        "status": found.get(device_id)})
    return {"devices": devices, "drivers": e.drivers()}


def get_device(device_id):
    e = _engine()
    row = e.get(device_id)
    return {"device": _view(row, e.status(row["id"], fresh=False) if row.get("enabled", True) else None)}


def add_device(body):
    row, failed = _engine().add(body.get("id"), body.get("label"), body.get("driver"), body.get("config"))
    return _saved(row, failed)


def update_device(device_id, body):
    row, failed = _engine().update(device_id, label=body.get("label"), enabled=body.get("enabled"),
                                   parts=body.get("parts"), new_id=body.get("new_id"))
    return _saved(row, failed)


def remove_device(device_id):
    _engine().remove(device_id)
    return {"removed": device_id}


def test_device(device_id):
    return {"status": _engine().status(device_id, fresh=True)}


def run_action(device_id, body):
    text, ok = _engine().run(device_id, body.get("capability"), body.get("action"), body.get("value"))
    return {"text": text, "ok": ok}


# --- the doors themselves ----------------------------------------------------

def _open(request, write=False):
    """Refuse on a hosted install, then count the call."""
    why = _engine().refusal()
    if why:
        raise HTTPException(status_code=404, detail=why)
    if write:
        check_endpoint_rate(request, "devices:write", max_calls=WRITES_PER_MIN)
    else:
        check_endpoint_rate(request, "devices:read", max_calls=READS_PER_MIN)


async def _body(request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    return body if isinstance(body, dict) else {}


async def _do(fn, *args, missing=404):
    """Run one door off the event loop. A DeviceError is the user's to read;
    anything else is logged and answered without its detail."""
    try:
        return await asyncio.to_thread(fn, *args)
    except HTTPException:
        raise
    except Exception as e:
        if isinstance(e, _engine().DeviceError):
            gone = str(e).startswith("There is no device named")
            raise HTTPException(status_code=missing if gone else 400, detail=str(e))
        logger.error(f"[DEVICES] route failed: {e}", exc_info=True)
        raise HTTPException(status_code=500,
                            detail=f"Something went wrong ({type(e).__name__}). The log has the detail.")


@router.get("/api/devices")
async def devices_list(request: Request, _=Depends(require_login)):
    _open(request)
    return await _do(list_devices)


@router.post("/api/devices")
async def devices_add(request: Request, _=Depends(require_login)):
    _open(request, write=True)
    return await _do(add_device, await _body(request))


@router.get("/api/devices/{device_id}")
async def devices_get(device_id: str, request: Request, _=Depends(require_login)):
    _open(request)
    return await _do(get_device, device_id)


@router.put("/api/devices/{device_id}")
async def devices_update(device_id: str, request: Request, _=Depends(require_login)):
    _open(request, write=True)
    return await _do(update_device, device_id, await _body(request))


@router.delete("/api/devices/{device_id}")
async def devices_remove(device_id: str, request: Request, _=Depends(require_login)):
    _open(request, write=True)
    return await _do(remove_device, device_id)


@router.post("/api/devices/{device_id}/test")
async def devices_test(device_id: str, request: Request, _=Depends(require_login)):
    _open(request, write=True)
    return await _do(test_device, device_id)


@router.post("/api/devices/{device_id}/run")
async def devices_run(device_id: str, request: Request, _=Depends(require_login)):
    _open(request, write=True)
    return await _do(run_action, device_id, await _body(request))
