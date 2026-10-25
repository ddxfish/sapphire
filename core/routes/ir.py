# core/routes/ir.py - the IR code library (core/ir/library.py): appliances in a
# room, each with named commands (power, vol up), each a code a satellite's IR
# blaster sends. Settings > IR draws it; a board blasts a code by name through
# the devices door (POST /api/devices/{id}/run, ir/blast). Doors: /api/ir/*.
import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from core.http_words import server_error
from core.auth import require_login, check_endpoint_rate
from core.ir import library

logger = logging.getLogger(__name__)
router = APIRouter()
WRITES_PER_MIN = 60           # adds, edits and removals, per client address


def _fail(e):
    """A library reason is the person's to read; anything else is logged and said without its detail."""
    if isinstance(e, library.LibraryError):
        return HTTPException(status_code=400, detail=str(e))
    return server_error("finish that", e, why=False)


async def _body(request):
    try:
        b = await request.json()
    except Exception:
        b = {}
    return b if isinstance(b, dict) else {}


@router.get("/api/ir/library")
async def library_get(_=Depends(require_login)):
    """Every appliance and its named codes, plus the built-in brands to import from."""
    try:
        apps = await asyncio.to_thread(library.appliances)
    except Exception as e:
        raise _fail(e)
    return {'appliances': apps, 'brands': sorted(library.BRANDS)}


@router.post("/api/ir/appliance")
async def ir_add_appliance(request: Request, _=Depends(require_login)):
    """{name, brand?}: a new, empty appliance."""
    check_endpoint_rate(request, "ir:write", max_calls=WRITES_PER_MIN)
    b = await _body(request)
    try:
        name = await asyncio.to_thread(library.add_appliance, b.get('name', ''), b.get('brand', ''))
    except Exception as e:
        raise _fail(e)
    return {'appliance': name}


@router.post("/api/ir/import")
async def ir_import(request: Request, _=Depends(require_login)):
    """{brand, appliance?}: seed an appliance from the built-in codes for a brand."""
    check_endpoint_rate(request, "ir:write", max_calls=WRITES_PER_MIN)
    b = await _body(request)
    try:
        name = await asyncio.to_thread(library.import_brand, b.get('brand', ''), b.get('appliance') or None)
    except Exception as e:
        raise _fail(e)
    return {'appliance': name}


@router.post("/api/ir/command")
async def ir_set_command(request: Request, _=Depends(require_login)):
    """{appliance, command, protocol='nec', addr, cmd}: add or replace a command's code (NEC hex bytes)."""
    check_endpoint_rate(request, "ir:write", max_calls=WRITES_PER_MIN)
    b = await _body(request)
    if str(b.get('protocol') or 'nec').lower() != 'nec':
        raise HTTPException(status_code=400, detail="Only NEC codes can be entered by hand for now; learned codes come with an IR receiver.")
    try:
        addr = int(str(b.get('addr', '')).strip(), 16)
        cmd = int(str(b.get('cmd', '')).strip(), 16)
    except ValueError:
        raise HTTPException(status_code=400, detail="addr and cmd are hex bytes, like 04 and 08.")
    if not (0 <= addr <= 0xffff and 0 <= cmd <= 0xff):
        raise HTTPException(status_code=400, detail="addr is up to two hex bytes (standard 04, or extended 57e3), cmd one (00-ff).")
    try:
        await asyncio.to_thread(library.set_command, b.get('appliance', ''), b.get('command', ''),
                                {'protocol': 'nec', 'addr': addr, 'cmd': cmd})
    except Exception as e:
        raise _fail(e)
    return {'ok': True}


@router.delete("/api/ir/library/{appliance}")
async def ir_remove_appliance(appliance: str, request: Request, _=Depends(require_login)):
    """Drop a whole appliance."""
    check_endpoint_rate(request, "ir:write", max_calls=WRITES_PER_MIN)
    try:
        await asyncio.to_thread(library.remove, appliance)
    except Exception as e:
        raise _fail(e)
    return {'removed': appliance}


@router.delete("/api/ir/library/{appliance}/{command}")
async def ir_remove_command(appliance: str, command: str, request: Request, _=Depends(require_login)):
    """Drop one command from an appliance."""
    check_endpoint_rate(request, "ir:write", max_calls=WRITES_PER_MIN)
    try:
        await asyncio.to_thread(library.remove, appliance, command)
    except Exception as e:
        raise _fail(e)
    return {'removed': command}
