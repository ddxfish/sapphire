# core/routes/worker_door.py - who may use this computer's shared workers
"""The clients of a shared worker door (core/worker_door.py): list, add (which
answers the pairing string to paste on the client, once), remove. A door is
`kokoro`, or a loaded plugin that declares a client mode (manifest
environment.client): its server opens user/worker_door/<plugin>/."""
from fastapi import APIRouter, Depends, HTTPException, Request

import config
from core import worker_door
from core.auth import require_login
from core.paths import PROJECT_ROOT

router = APIRouter()
DOORS = PROJECT_ROOT / "user" / "worker_door"


def _folder(door):
    if door == "kokoro":
        return DOORS / "kokoro"
    from core.plugin_loader import plugin_loader
    manifest = (plugin_loader.get_plugin_info(door) or {}).get("manifest") or {}
    if (manifest.get("environment") or {}).get("client"):
        return DOORS / door
    raise HTTPException(status_code=404, detail=f"No shared door named {door!r}")


def _port(door):
    if door == "kokoro":
        return int(getattr(config, "TTS_SHARE_PORT", 5013) or 5013)
    from core.plugin_loader import plugin_loader
    return int(plugin_loader.get_plugin_settings(door).get("share_port") or 0) or None


def _address(door):
    """host:port as another computer on this network reaches it (a guess)."""
    return f"{worker_door.guess_host()}:{_port(door)}"


@router.get("/api/worker-door/{door}")
def list_clients(door: str, _=Depends(require_login)):
    folder = _folder(door)
    return {"clients": sorted(worker_door.clients(folder)), "address": _address(door)}


@router.post("/api/worker-door/{door}")
async def add_client(door: str, request: Request, _=Depends(require_login)):
    folder = _folder(door)
    body = await request.json()
    try:
        name = worker_door.slug(body.get("name"))
        worker_door.add_client(folder, name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    address = str(body.get("address") or "").strip() or _address(door)
    return {"name": name, "pairing": worker_door.pairing(folder, name, address)}


@router.delete("/api/worker-door/{door}/{name}")
def remove_client(door: str, name: str, _=Depends(require_login)):
    worker_door.remove_client(_folder(door), name)
    return {"ok": True}
