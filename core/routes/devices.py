# core/routes/devices.py - Settings > Devices (tmp/device-manager-plan.md)
#
# Thin doors into core/devices/engine.py. Adding, changing and removing
# devices happens here and ONLY here: no tool can do it.
#
# The engine is imported on first use, never at boot, so a fault in it cannot
# stop Sapphire starting. Engine calls can wait on a slow device, so each runs
# off the event loop.
import asyncio
import json
import logging

from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import StreamingResponse

from core.auth import require_login, check_endpoint_rate, get_client_ip

logger = logging.getLogger(__name__)

router = APIRouter()

READS_PER_MIN = 240        # the page polls its list, and a device window reads detail
WRITES_PER_MIN = 60        # saves, tests, and Try buttons
VOICE_PER_MIN = 30         # one device, counted per caller address, right or wrong key
STREAM_QUIET = 20          # seconds of nothing before a light stream is checked and kept alive


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


def _saved(row, failed, problems=()):
    out = {"device": _view(row)}
    notes = []
    if failed:
        notes.append("these could not be stored: " + ", ".join(failed) + ". Enter them again")
    notes += [f"the device did not take its settings ({p})" for p in problems]
    if notes:
        out["warning"] = "Saved, but " + "; ".join(notes) + "."
    return out


def list_devices():
    e = _engine()
    found = e.statuses(wait_s=0)      # never blocks the page; answers land in the cache
    devices = []
    for device_id, row in e.rows().items():
        v = e.public(row)
        devices.append({"id": v["id"], "label": v["label"], "enabled": v["enabled"],
                        "location": v["location"],
                        "capabilities": [c["capability"] for c in e.describe(row) if not c["error"]],
                        "missing": [p["driver"] for p in v["parts"] if not p["available"]],
                        "status": found.get(device_id)})
    return {"devices": devices, "drivers": e.drivers()}


def get_device(device_id):
    e = _engine()
    row = e.get(device_id)
    status = e.status(row["id"], fresh=False) if row.get("enabled", True) else None
    # read again: asked how it is, the device may have just said what it has
    return {"device": _view(e.get(row["id"]), status)}


def add_device(body):
    e = _engine()
    row, failed = e.add(body.get("id"), body.get("label"), body.get("driver"), body.get("config"),
                        location=body.get("location") or '')
    return _saved(row, failed, e.tell(row))


def update_device(device_id, body):
    e = _engine()
    row, failed = e.update(device_id, label=body.get("label"), enabled=body.get("enabled"),
                           parts=body.get("parts"), new_id=body.get("new_id"),
                           location=body.get("location"), locked=body.get("locked"))
    return _saved(row, failed, e.tell(row) if row.get("enabled", True) else [])


def remove_device(device_id):
    _engine().remove(device_id)
    return {"removed": device_id}


def test_device(device_id):
    e = _engine()
    status = e.status(device_id, fresh=True)
    return {"status": status, "device": _view(e.get(device_id), status)}


def found_things(driver_id, device_id=''):
    """What a driver can see right now, for the pick list. A saved device
    lends its settings to the look."""
    e = _engine()
    config = {}
    if device_id:
        part = e._part(e.get(device_id), str(driver_id or '').strip().lower())
        config = dict((part or {}).get("config") or {})
    return {"found": e.found(driver_id, config)}


def run_action(device_id, body):
    e = _engine()
    result, ok = e.run(device_id, body.get("capability"), body.get("action"), body.get("value"),
                       owner=True)             # the user's own button
    out = {"text": e.text_of(result), "ok": ok}
    if isinstance(result, dict):
        out["images"] = result["images"]       # the Try button shows them
    return out


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


@router.get("/api/devices/found/{driver_id}")
async def devices_found(driver_id: str, request: Request, device: str = '', _=Depends(require_login)):
    _open(request)
    return await _do(found_things, driver_id, device)


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


# --- the device doors: a DEVICE calls these, not a browser --------------------

async def _device_key(device_id, request, door):
    """The key a device presented, once it is proven. No login: a device
    proves itself with its own key. 401 says the same for a wrong key and
    for a device that does not exist, so names cannot be probed."""
    why = _engine().refusal()
    if why:
        raise HTTPException(status_code=404, detail=why)
    # counted before the key is looked at, so guessing keys is slow
    check_endpoint_rate(request, f"devices:{door}:{device_id}", max_calls=VOICE_PER_MIN,
                        identity=f"addr:{get_client_ip(request)}")
    from core.devices import voice
    auth = request.headers.get('authorization', '')
    key = auth[7:].strip() if auth.startswith('Bearer ') else ''
    if not await asyncio.to_thread(voice.key_ok, device_id, key):
        raise HTTPException(status_code=401, detail="unknown device or wrong key")
    return key


AUDIO_BODIES = {'audio/wav': '.wav', 'audio/x-wav': '.wav', 'audio/ogg': '.ogg',
                'audio/mpeg': '.mp3', 'audio/flac': '.flac'}


async def _heard(request, limit):
    """(audio, suffix) a device sent: the request body itself when its
    Content-Type is a sound (a small board), else the form file `audio`.
    Read in pieces, so one that is too large is refused before it is held."""
    kind = request.headers.get('content-type', '').split(';')[0].strip().lower()
    if kind in AUDIO_BODIES:
        data = bytearray()
        async for piece in request.stream():
            data += piece
            if len(data) > limit:
                raise HTTPException(status_code=413, detail="The audio is too large.")
        return bytes(data), AUDIO_BODIES[kind]
    try:
        audio = (await request.form()).get('audio')
    except Exception:
        audio = None
    if audio is None or not hasattr(audio, 'read'):
        raise HTTPException(status_code=422, detail="Send the audio as the body with its "
                            "Content-Type, or as the form file 'audio'.")
    name = (audio.filename or '').lower()
    suffix = '.' + name.rsplit('.', 1)[1] if '.' in name and len(name.rsplit('.', 1)[1]) <= 4 else '.wav'
    return await audio.read(), suffix


@router.post("/api/devices/{device_id}/voice")
async def devices_voice(device_id: str, request: Request):
    """A satellite heard its wake word and sends what was said. The answer
    comes back as soon as the words are known. Her reply is spoken on the
    same device later, when she has one."""
    await _device_key(device_id, request, 'voice')
    from core.devices import voice
    data, suffix = await _heard(request, voice.MAX_AUDIO)
    return await asyncio.to_thread(voice.hear, device_id, data, suffix)


async def light_stream(device_id, key, gone):
    """What one device's light should show, as server-sent events, until the
    device hangs up or its key stops being the right one. gone() is awaited
    to learn whether the caller has left."""
    from core.devices import voice
    loop = asyncio.get_running_loop()
    queue = asyncio.Queue(maxsize=32)
    now = voice.listen(device_id, loop, queue)
    try:
        # the clock rides along: a board keeps local time from it, no internet clock needed
        yield f"data: {json.dumps(dict(voice.clock(), state='connected', src='device'))}\n\n"
        if now:
            yield f"data: {json.dumps(now)}\n\n"
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=STREAM_QUIET)
            except asyncio.TimeoutError:
                if await gone() or not await asyncio.to_thread(voice.key_ok, device_id, key):
                    break
                yield ": still here\n\n"
                continue
            if item is None:                 # a newer stream took this one's place
                break
            yield f"data: {json.dumps(item)}\n\n"
    finally:
        voice.unlisten(device_id, loop, queue)
        logger.info(f"[DEVICES] {device_id}: light stream closed")


@router.get("/api/devices/{device_id}/events")
async def devices_events(device_id: str, request: Request):
    """A satellite holds this open to learn what its light should show:
    thinking, tool, idle, error. It only ever hears about its OWN turns."""
    key = await _device_key(device_id, request, 'events')
    device_id = device_id.strip().lower()
    logger.info(f"[DEVICES] {device_id}: light stream opened")
    return StreamingResponse(
        light_stream(device_id, key, request.is_disconnected),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )
