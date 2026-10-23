# core/routes/media.py - Tool image serving
# (the /api/sdxl-image/ live proxy died with the image-gen plugin, 2026-09-09)
import logging
from pathlib import Path

from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import Response

from core.auth import require_login
from core.api_fastapi import get_system

logger = logging.getLogger(__name__)

router = APIRouter()

PROJECT_ROOT = Path(__file__).parent.parent.parent


@router.get("/api/tool-image/{image_id}")
async def serve_tool_image(image_id: str, request: Request, _=Depends(require_login)):
    """Serve tool-returned images from the chat history database."""
    import re
    # jpg/png are the classic tool-image extensions; gif/webp arrive via the
    # event-image path (validation allows them, vision models see them, and
    # browsers render them in <img>). Strictly more permissive — existing
    # jpg/png serving is unaffected. 2026-06-13.
    if not re.match(r'^[a-zA-Z0-9_-]+\.(jpg|jpeg|png|gif|webp)$', image_id):
        raise HTTPException(status_code=400, detail="Invalid image ID")

    system = get_system()
    result = system.llm_chat.session_manager.get_tool_image(image_id)
    if not result:
        # Disk fallback: history-less tool lanes without a stream-brain
        # override save here (see _save_tool_image). image_id is already
        # regex-validated above — no traversal possible.
        disk = PROJECT_ROOT / "user" / "tool_images" / image_id
        if disk.is_file():
            media_type = "image/png" if image_id.endswith(".png") else "image/jpeg"
            return Response(content=disk.read_bytes(), media_type=media_type)
        raise HTTPException(status_code=404, detail="Image not found")

    data, media_type = result
    return Response(content=data, media_type=media_type)
