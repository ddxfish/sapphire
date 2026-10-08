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
from fastapi.responses import StreamingResponse, FileResponse

from core.auth import require_login, check_endpoint_rate, get_client_ip

logger = logging.getLogger(__name__)

router = APIRouter()

READS_PER_MIN = 240        # the page polls its list, and a device window reads detail
WRITES_PER_MIN = 60        # saves, tests, and Try buttons
VOICE_PER_MIN = 30         # one device, counted per caller address, right or wrong key
PULL_PER_MIN = 600         # a screen pulls her reply in pieces as she writes: a few a second
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
    found = e.statuses()              # what is believed; never blocks the page
    devices = []
    for device_id, row in e.rows().items():
        v = e.public(row)
        first = v["parts"][0] if v["parts"] else {}
        devices.append({"id": v["id"], "label": v["label"], "enabled": v["enabled"],
                        "location": v["location"], "fingerprint": v["fingerprint"],
                        "driver": first.get("driver", ""),
                        "type": first.get("label", "").split(" (")[0],     # the pill: "Satellite", not the aside
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


def sapphire_addresses():
    """Where a board in the house can find Sapphire: her likeliest address
    first, then the others this machine has. A guess the page shows and
    lets the user correct (a VPN's tunnel address is not the house)."""
    import config
    from core import net
    scheme = 'https' if getattr(config, 'WEB_UI_SSL_ADHOC', False) else 'http'
    port = int(getattr(config, 'WEB_UI_PORT', 8073))
    return [f"{scheme}://{ip}:{port}" for ip in net.local_ips()]


def here():
    found = sapphire_addresses()
    return {"sapphire": found[0] if found else '', "addresses": found}


def _sapphire_url(given):
    """The address the user typed for Sapphire, or the guess. A board's key
    rides it, so it has to be on the local network."""
    from urllib.parse import urlsplit
    from core import net
    e = _engine()
    given = str(given or '').strip().rstrip('/')
    if not given:
        found = sapphire_addresses()
        if not found:
            raise e.DeviceError("This machine has no network address yet. Connect it to the network first.")
        return found[0]
    parts = urlsplit(given if '://' in given else 'https://' + given)
    if parts.scheme not in ('http', 'https') or not parts.hostname:
        raise e.DeviceError(f"'{given}' is not a usable address for Sapphire. Example: https://192.168.1.2:8073")
    if net.classify(parts.hostname) != 'lan':
        raise e.DeviceError("Sapphire's address for a board has to be on your own network.")
    return f"{parts.scheme}://{parts.netloc}"


def provision_device(body):
    """A board being set up from the browser (the flasher in + Add Device):
    the device row with no address yet, its two fresh keys, and what the
    board needs to find Sapphire. The page carries it all to the board over
    USB; the board's own address is learned when it calls in."""
    from core.ssl_utils import cert_pem
    e = _engine()
    sapphire = _sapphire_url(body.get("sapphire"))
    row, keys = e.provision(body.get("label"), body.get("driver") or "satellite",
                            location=body.get("location") or '', mac=body.get("mac") or '')
    return {"device": _view(row), "id": row["id"], **keys, "sapphire": sapphire, "cert": cert_pem()}


def firmware_index():
    from core.devices import firmware
    return firmware.index()


def firmware_part(board_id, path):
    from core.devices import firmware
    try:
        return firmware.part(board_id, path)
    except firmware.FirmwareError as e:
        raise HTTPException(status_code=404, detail=str(e))


# the server lane of the flasher: a board plugged into Sapphire's own computer
def _flasher():
    from core.devices import flasher
    flasher.tend()
    return flasher


def flash_call(name, *args):
    f = _flasher()
    try:
        return getattr(f, name)(*args)
    except f.FlashError as e:
        raise HTTPException(status_code=400, detail=str(e))


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


@router.post("/api/devices/provision")
async def devices_provision(request: Request, _=Depends(require_login)):
    _open(request, write=True)
    return await _do(provision_device, await _body(request))


@router.get("/api/devices/here")
async def devices_here(request: Request, _=Depends(require_login)):
    _open(request)
    return await _do(here)


@router.get("/api/devices/flash/ports")
async def devices_flash_ports(request: Request, _=Depends(require_login)):
    _open(request)
    return {"ports": await _do(flash_call, 'ports')}


@router.post("/api/devices/flash/chip")
async def devices_flash_chip(request: Request, _=Depends(require_login)):
    _open(request, write=True)
    body = await _body(request)
    return await _do(flash_call, 'chip', str(body.get("port") or ''))


@router.post("/api/devices/flash/start")
async def devices_flash_start(request: Request, _=Depends(require_login)):
    _open(request, write=True)
    body = await _body(request)
    return await _do(flash_call, 'start', str(body.get("port") or ''), str(body.get("board") or ''))


@router.get("/api/devices/flash/status")
async def devices_flash_status(request: Request, _=Depends(require_login)):
    _open(request)
    return await _do(flash_call, 'status')


@router.post("/api/devices/flash/ask")
async def devices_flash_ask(request: Request, _=Depends(require_login)):
    _open(request, write=True)
    body = await _body(request)
    wait = min(60.0, max(1.0, float(body.get("wait") or 15)))
    return await _do(flash_call, 'ask', str(body.get("port") or ''), str(body.get("line") or '')[:8000], wait)


@router.post("/api/devices/flash/close")
async def devices_flash_close(request: Request, _=Depends(require_login)):
    _open(request, write=True)
    body = await _body(request)
    return await _do(flash_call, 'close', str(body.get("port") or ''))


@router.get("/api/devices/firmware")
async def devices_firmware(request: Request, _=Depends(require_login)):
    _open(request)
    return await _do(firmware_index)


@router.get("/api/devices/firmware/{board_id}/{path:path}")
async def devices_firmware_part(board_id: str, path: str, request: Request, _=Depends(require_login)):
    _open(request)
    where = await _do(firmware_part, board_id, path)
    return FileResponse(str(where), media_type="application/octet-stream", filename=where.name)


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

async def _device_key(device_id, request, door, per_min=VOICE_PER_MIN):
    """The key a device presented, once it is proven. No login: a device
    proves itself with its own key. 401 says the same for a wrong key and
    for a device that does not exist, so names cannot be probed."""
    why = _engine().refusal()
    if why:
        raise HTTPException(status_code=404, detail=why)
    # counted before the key is looked at, so guessing keys is slow
    check_endpoint_rate(request, f"devices:{door}:{device_id}", max_calls=per_min,
                        identity=f"addr:{get_client_ip(request)}")
    from core.devices import voice
    auth = request.headers.get('authorization', '')
    key = auth[7:].strip() if auth.startswith('Bearer ') else ''
    if not await asyncio.to_thread(voice.key_ok, device_id, key):
        raise HTTPException(status_code=401, detail="unknown device or wrong key")
    from core.devices import health
    device_id = device_id.strip().lower()
    health.seen(device_id)                     # it spoke with its own key: it is online
    await asyncio.to_thread(_engine().learned, device_id, get_client_ip(request))   # and that is where it lives
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


@router.post("/api/devices/{device_id}/wake")
async def devices_wake(device_id: str, request: Request):
    """A satellite's wake word fired: may it take this one? Answered at
    once, before it has recorded anything. {'yours': false} means another
    satellite, or Sapphire's own microphone, heard the same wake first."""
    await _device_key(device_id, request, 'wake')
    from core.devices import voice
    return await asyncio.to_thread(voice.woke, device_id)


@router.post("/api/devices/{device_id}/voice")
async def devices_voice(device_id: str, request: Request):
    """A satellite heard its wake word and sends what was said. The answer
    comes back as soon as the words are known. Her reply is spoken on the
    same device later, when she has one."""
    await _device_key(device_id, request, 'voice')
    from core.devices import voice
    data, suffix = await _heard(request, voice.MAX_AUDIO)
    return await asyncio.to_thread(voice.hear, device_id, data, suffix)


TYPED_BODY_MAX = 16 * 1024


@router.post("/api/devices/{device_id}/text")
async def devices_text(device_id: str, request: Request):
    """A device with a keyboard sends what was typed: the body as text/plain,
    or JSON {"text"}. Answered once a turn has started; her reply reaches
    the device's screen through `text` doorbells on its events stream."""
    await _device_key(device_id, request, 'text')
    from core.devices import voice
    kind = request.headers.get('content-type', '').split(';')[0].strip().lower()
    raw = bytearray()
    async for piece in request.stream():
        raw += piece
        if len(raw) > TYPED_BODY_MAX:
            raise HTTPException(status_code=413, detail="That is too much text.")
    if kind == 'application/json':
        try:
            text = (json.loads(bytes(raw).decode('utf-8')) or {}).get('text')
        except (ValueError, AttributeError):
            raise HTTPException(status_code=422, detail='Send {"text": "..."}, or the text as text/plain.')
    else:
        text = bytes(raw).decode('utf-8', 'replace')
    return await asyncio.to_thread(voice.typed, device_id, text)


@router.get("/api/devices/{device_id}/text")
async def devices_text_read(device_id: str, request: Request):
    """A piece of her reply, for the device's screen: ?msg=&from=&max=.
    No msg = the latest reply, for a device that just connected."""
    await _device_key(device_id, request, 'text-read', per_min=PULL_PER_MIN)
    from core.devices import voice
    q = request.query_params

    def number(name, fallback):
        try:
            return int(q.get(name, fallback))
        except (TypeError, ValueError):
            return fallback
    return await asyncio.to_thread(voice.reply_text, device_id, str(q.get('msg') or '')[:32],
                                   number('from', 0), number('max', voice.PULL_MAX))


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
