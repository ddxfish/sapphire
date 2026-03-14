# core/routes/scouts.py — Scout status API for frontend
import logging
from fastapi import APIRouter, Depends, HTTPException, Query
from core.auth import require_login
from core.api_fastapi import get_system

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/scouts/status")
async def scout_status(chat: str = Query('', description="Filter by chat name"), _=Depends(require_login)):
    """Get scout statuses for the UI pill bar, optionally filtered by chat."""
    system = get_system()
    if not hasattr(system, 'scout_manager'):
        return {"scouts": []}
    return {"scouts": system.scout_manager.check_all(chat_name=chat)}


@router.post("/api/scouts/{scout_id}/dismiss")
async def dismiss_scout(scout_id: str, _=Depends(require_login)):
    """Dismiss/cancel a scout from the UI."""
    system = get_system()
    if not hasattr(system, 'scout_manager'):
        raise HTTPException(404, "Scout system not available")
    result = system.scout_manager.dismiss(scout_id)
    if 'error' in result:
        raise HTTPException(404, result['error'])
    return result
