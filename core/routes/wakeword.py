# core/routes/wakeword.py - the wake word library (core/wakeword/library.py): every model this
# Sapphire can listen for, who listens for what, uploads, and the switch. Settings > Wakeword
# draws it; the Wakeword Maker's Install goes through the same doors.
import asyncio
import json
import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import FileResponse

from core.auth import require_login, check_endpoint_rate
from core.wakeword import library

logger = logging.getLogger(__name__)
router = APIRouter()
MANIFEST_MAX = 64 * 1024
WRITES_PER_MIN = 60           # uploads, removals and switches, per client address


def _fail(e):
    """A library or device reason is the person's to read; anything else is logged and said without its detail."""
    if isinstance(e, library.Exists):
        return HTTPException(status_code=409, detail=str(e))
    if isinstance(e, library.LibraryError):
        return HTTPException(status_code=400, detail=str(e))
    try:
        from core.devices.engine import DeviceError
        if isinstance(e, DeviceError):
            return HTTPException(status_code=400, detail=str(e))
    except Exception:
        pass
    logger.error(f"[WAKEWORD] route failed: {e}", exc_info=True)
    return HTTPException(status_code=500, detail=f"Something went wrong ({type(e).__name__}). The log has the detail.")


@router.get("/api/wakeword/library")
async def library_get(_=Depends(require_login)):
    """The models, and who listens for what."""
    try:
        models, who = await asyncio.to_thread(lambda: (library.entries(), library.listeners()))
    except Exception as e:
        raise _fail(e)
    models = [{k: v for k, v in m.items() if k != 'path'} for m in models]      # where a file sits is the server's business
    return {'models': models, 'listeners': who, 'families': library.FAMILIES}


@router.post("/api/wakeword/library")
async def library_add(request: Request, file: UploadFile = File(...), manifest: Optional[UploadFile] = File(None),
                      name: str = Form(''), phrase: str = Form(''), overwrite: bool = Form(False),
                      _=Depends(require_login)):
    """A model into the library: the file (.onnx or .tflite), and its manifest when there is one
    (esphome's, the Wakeword Maker's, or the library's own shape). 409 when the name is taken."""
    check_endpoint_rate(request, "wakeword:write", max_calls=WRITES_PER_MIN)
    family = library.family_of(file.filename)
    if not family:
        raise HTTPException(status_code=400, detail="A model is a .onnx (openWakeWord) or a .tflite (microWakeWord).")
    most = library.LIMITS[family][1]
    data = await file.read(most + 1)                   # never more than a model can be: the check in add() sees the size
    if len(data) > most:
        raise HTTPException(status_code=413, detail=f"A {library.FAMILIES[family]} model is {library.SIZES[family]}.")
    meta = {}
    if manifest is not None and manifest.filename:
        raw = await manifest.read()
        if len(raw) > MANIFEST_MAX:
            raise HTTPException(status_code=413, detail="The manifest is too large.")
        try:
            meta = json.loads(raw.decode('utf-8'))
        except Exception:
            raise HTTPException(status_code=400, detail="The manifest is not JSON.")
    try:
        entry = await asyncio.to_thread(library.add, name or Path(file.filename or '').stem, data, family,
                                        meta, overwrite, 'upload', phrase)
    except Exception as e:
        raise _fail(e)
    return {'model': entry}


@router.delete("/api/wakeword/library/{family}/{name}")
async def library_remove(family: str, name: str, request: Request, _=Depends(require_login)):
    """Out of the library. Allowed while in use: the answer names who was using it (they keep their copy)."""
    check_endpoint_rate(request, "wakeword:write", max_calls=WRITES_PER_MIN)
    try:
        users = await asyncio.to_thread(library.remove, name, family)
    except Exception as e:
        raise _fail(e)
    return {'removed': name, 'family': family, 'used_by': users}


@router.get("/api/wakeword/library/{family}/{name}/file")
async def library_file(family: str, name: str, _=Depends(require_login)):
    """The model's bytes, to copy onto a body by hand."""
    e = library.get(name, family) if family in library.FAMILIES else None
    if not e or not e['path']:
        raise HTTPException(status_code=404, detail="No such model file.")
    return FileResponse(e['path'], media_type='application/octet-stream', filename=f"{name}.{family}")


@router.post("/api/wakeword/switch")
async def library_switch(request: Request, _=Depends(require_login)):
    """{listener: library.DESKTOP (this computer) | device id, model: name | '' | 'own'}: make it listen for that."""
    check_endpoint_rate(request, "wakeword:write", max_calls=WRITES_PER_MIN)
    try:
        body = await request.json()
    except Exception:
        body = {}
    body = body if isinstance(body, dict) else {}
    try:
        out = await asyncio.to_thread(library.switch, str(body.get('listener') or ''), body.get('model'))
    except Exception as e:
        raise _fail(e)
    return out
