# core/routes/media.py - Tool image serving and SDXL image proxy
import asyncio
import io
import json
import logging
from pathlib import Path

from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import StreamingResponse, Response

from core.auth import require_login
from core.api_fastapi import get_system

logger = logging.getLogger(__name__)

router = APIRouter()

PROJECT_ROOT = Path(__file__).parent.parent.parent
USER_PLUGIN_SETTINGS_DIR = PROJECT_ROOT / 'user' / 'webui' / 'plugins'


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


@router.get("/api/sdxl-image/{image_id}")
async def proxy_sdxl_image(image_id: str, request: Request, _=Depends(require_login)):
    """Proxy SDXL images."""
    import re

    if not re.match(r'^[a-zA-Z0-9_-]+$', image_id):
        raise HTTPException(status_code=400, detail="Invalid image ID")

    # Get SDXL URL from plugin settings
    settings_file = USER_PLUGIN_SETTINGS_DIR / "image-gen.json"
    sdxl_url = "http://127.0.0.1:5153"
    if settings_file.exists():
        try:
            with open(settings_file, encoding='utf-8') as f:
                settings = json.load(f)
            sdxl_url = settings.get('api_url', sdxl_url)
        except Exception:
            pass

    def _fetch_image():
        import requests as req
        return req.get(f'{sdxl_url}/output/{image_id}.jpg', timeout=10)

    try:
        import requests as req
        response = await asyncio.to_thread(_fetch_image)
        if response.status_code == 200:
            return StreamingResponse(io.BytesIO(response.content), media_type='image/jpeg')
        elif response.status_code == 404:
            raise HTTPException(status_code=404, detail="Image not found yet")
        else:
            raise HTTPException(status_code=500, detail=f"SDXL returned {response.status_code}")
    except req.exceptions.Timeout:
        raise HTTPException(status_code=504, detail="SDXL timeout")
    except req.exceptions.ConnectionError:
        raise HTTPException(status_code=502, detail=f"Cannot connect to SDXL at {sdxl_url}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail="Image fetch failed")
