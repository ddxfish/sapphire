# core/routes/rooms.py - the chat room door (core/rooms.py, 2026-10-11)
#
# A room is a chat with several speakers. These routes make and shape one
# (purpose, seats), take the person's line, and run or stop the rounds.
# Nothing here runs a turn itself: a line goes through the room's inbox and
# the runner puts each seat turn there in order.
import asyncio
import logging

from fastapi import APIRouter, Request, Depends, HTTPException

from core.auth import require_login
from core import rooms

logger = logging.getLogger(__name__)
router = APIRouter()


async def _json(request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    return body if isinstance(body, dict) else {}


async def _call(fn, *a, **kw):
    try:
        return await asyncio.to_thread(fn, *a, **kw)
    except rooms.RoomError as e:
        raise HTTPException(status_code=400, detail=str(e))


async def _room_or_404(chat):
    if not await asyncio.to_thread(rooms.is_room, chat):
        raise HTTPException(status_code=404, detail=f"There is no room named '{chat}'.")


@router.get("/api/rooms")
async def list_rooms(_=Depends(require_login)):
    return {"rooms": await _call(rooms.list_rooms)}


@router.post("/api/rooms")
async def create_room(request: Request, _=Depends(require_login)):
    body = await _json(request)
    chat = await _call(rooms.create, body.get('name'), body.get('purpose') or '')
    return await _call(rooms.describe, chat)


@router.get("/api/rooms/{chat}")
async def describe_room(chat: str, _=Depends(require_login)):
    await _room_or_404(chat)
    return await _call(rooms.describe, chat)


@router.put("/api/rooms/{chat}")
async def update_room(chat: str, request: Request, _=Depends(require_login)):
    """The purpose and the rules (human_every, pause_s, max_rounds, bots_enabled, shuffle)."""
    await _room_or_404(chat)
    await _call(rooms.update_room, chat, await _json(request))
    return await _call(rooms.describe, chat)


@router.post("/api/rooms/{chat}/seats")
async def add_seat(chat: str, request: Request, _=Depends(require_login)):
    await _room_or_404(chat)
    body = await _json(request)
    return await _call(rooms.add_seat, chat, body.get('persona'), body.get('name'))


@router.put("/api/rooms/{chat}/seats/{seat_id}")
async def update_seat(chat: str, seat_id: str, request: Request, _=Depends(require_login)):
    await _room_or_404(chat)
    return await _call(rooms.update_seat, chat, seat_id, await _json(request))


@router.delete("/api/rooms/{chat}/seats/{seat_id}")
async def remove_seat(chat: str, seat_id: str, _=Depends(require_login)):
    await _room_or_404(chat)
    await _call(rooms.remove_seat, chat, seat_id)
    return {"ok": True}


@router.post("/api/rooms/{chat}/say")
async def say(chat: str, request: Request, _=Depends(require_login)):
    """The person's line. It lands between seat turns and wakes the room."""
    await _room_or_404(chat)
    body = await _json(request)
    try:
        ticket = await asyncio.to_thread(rooms.say, chat, body.get('text'))
    except rooms.RoomError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:                     # the inbox's own refusal (sealed, full)
        raise HTTPException(status_code=409, detail=str(e))
    return {"ticket": ticket, "state": rooms.state(chat)}


@router.post("/api/rooms/{chat}/clear")
async def clear(chat: str, _=Depends(require_login)):
    """Wipe the transcript; seats, rules and purpose stay."""
    await _room_or_404(chat)
    return await _call(rooms.clear, chat)


@router.post("/api/rooms/{chat}/import")
async def import_room(chat: str, request: Request, _=Depends(require_login)):
    """{messages?, room?} - a chat export's messages and settings.room."""
    await _room_or_404(chat)
    return await _call(rooms.import_room, chat, await _json(request))


@router.post("/api/rooms/{chat}/start")
async def start(chat: str, request: Request, _=Depends(require_login)):
    """Go: a waiting room runs one more round; `rounds` = N rounds before the human rule asks again."""
    await _room_or_404(chat)
    body = await _json(request)
    rounds = body.get('rounds')
    try:
        rounds = None if rounds is None else max(0, int(rounds))
    except (TypeError, ValueError):
        rounds = None
    return await _call(rooms.start, chat, rounds)


@router.post("/api/rooms/{chat}/stop")
async def stop(chat: str, _=Depends(require_login)):
    return await _call(rooms.stop, chat)


@router.get("/api/rooms/{chat}/state")
async def room_state(chat: str, _=Depends(require_login)):
    return rooms.state(chat)
