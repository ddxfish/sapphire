# core/routes/perception.py — the perception inbox door + cadence status
# (Game Room F3, 2026-09-09). A room's board (or any depositor) drops what
# she should see next into the per-chat inbox; the cadence organ takes it
# when her unprompted turn fires. Nothing here starts a turn — arming is
# the room host plugin's business (it reads its own settings spine).
import logging

from fastapi import APIRouter, Request, Depends, HTTPException

from core.auth import require_login
from core.api_fastapi import get_system
from core import perception, cadence

logger = logging.getLogger(__name__)

router = APIRouter()


def _chat_ok(system, chat):
    try:
        return system.llm_chat.session_manager.get_settings_for(chat) is not None
    except Exception:
        return False


@router.post("/api/perception/{chat}")
async def deposit(chat: str, request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Deposit frames and/or a line of state for `chat`. Latest wins."""
    if not _chat_ok(system, chat):
        raise HTTPException(status_code=404, detail="chat not found")
    try:
        body = await request.json()
    except Exception:
        body = {}
    body = body if isinstance(body, dict) else {}
    got = perception.deposit(chat, frames=body.get('frames'), text=body.get('text'),
                             source=body.get('source') or 'api')
    st = cadence.status(chat)
    return {"ok": bool(got), "stored": got or {'frames': 0, 'text': 0},
            "next_in": st.get('next_in'), "armed": st.get('armed', False)}


@router.get("/api/cadence/{chat}")
async def cadence_status(chat: str, _=Depends(require_login), system=Depends(get_system)):
    if not _chat_ok(system, chat):
        raise HTTPException(status_code=404, detail="chat not found")
    return cadence.status(chat)
