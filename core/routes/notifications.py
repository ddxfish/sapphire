# core/routes/notifications.py - the notification center's door (core/notify.py)
#
# GET    /api/notifications            rows (active first, newest first) + counts
# POST   /api/notifications            a row from a tab (it painted the toast already)
# POST   /api/notifications/read       everything read (opening the page)
# POST   /api/notifications/{id}/read  one row read (a toast or sidebar row clicked)
# DELETE /api/notifications/{id}       one row gone; 409 while it is active
# DELETE /api/notifications            every row that is not active
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from core import notify
from core.auth import require_login
from core.request_context import session_origin

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/notifications")
async def list_notifications(level: str = '', source: str = '', unread: Optional[bool] = None,
                             limit: int = 200, _=Depends(require_login)):
    return {"rows": notify.rows(level=level or None, source=source or None, unread=unread,
                                limit=max(1, min(int(limit), notify.RING))),
            "counts": notify.counts()}


@router.post("/api/notifications")
async def post_notification(request: Request, _=Depends(require_login)):
    try:
        body = await request.json()
    except Exception:
        body = None
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="a JSON object is required")
    title = str(body.get('title') or '').strip()
    if not title:
        raise HTTPException(status_code=400, detail="title is required")
    rid = notify.notify(title, level=str(body.get('level') or 'info'), body=body.get('body') or '',
                        source=str(body.get('source') or 'web'), go=body.get('go'),
                        key=body.get('key'), origin=session_origin.get())
    return {"id": rid, "row": notify.get(rid), "counts": notify.counts()}


@router.post("/api/notifications/read")
async def read_all(_=Depends(require_login)):
    return {"read": notify.mark_all_read(origin=session_origin.get()), "counts": notify.counts()}


@router.post("/api/notifications/{rid}/read")
async def read_one(rid: str, _=Depends(require_login)):
    if not notify.mark_read(rid, origin=session_origin.get()):
        raise HTTPException(status_code=404, detail="no such notification")
    return {"counts": notify.counts()}


@router.delete("/api/notifications/{rid}")
async def dismiss_one(rid: str, _=Depends(require_login)):
    try:
        gone = notify.dismiss(rid, origin=session_origin.get())
    except ValueError:
        raise HTTPException(status_code=409, detail="still active: it goes when its work ends")
    if not gone:
        raise HTTPException(status_code=404, detail="no such notification")
    return {"counts": notify.counts()}


@router.delete("/api/notifications")
async def clear_all(_=Depends(require_login)):
    return {"cleared": notify.clear(origin=session_origin.get()), "counts": notify.counts()}
