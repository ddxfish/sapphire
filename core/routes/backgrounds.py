# core/routes/backgrounds.py — Scene Backgrounds: a global library of named chat backgrounds.
#
# Security model (scout-hardened 2026-06-15): files live under user/backgrounds/ (NOT
# user/public/, which is a public StaticFiles mount). Every byte is served through an
# auth-gated handler. The 1-word scene name is UNTRUSTED input that becomes a filesystem
# stem, so it's run through a real sanitizer (avatars never had to — they whitelist/DB-
# indirect). Every upload is re-encoded through PIL (strips SVG/script payloads, validates
# it's a real image, bomb-guarded); the server picks the extension (always .webp), so the
# whole extension/MIME class disappears.

import io
import re
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse

from core.auth import require_login

logger = logging.getLogger(__name__)
router = APIRouter()

# Self-contained root (no api_fastapi import -> no circular). .absolute() not .resolve()
# so a symlinked install dir doesn't relocate the root (symlinked_plugins_resolve_trap).
# core/routes/backgrounds.py -> core/routes -> core -> <root>
PROJECT_ROOT = Path(__file__).absolute().parent.parent.parent

# NOT user/public — auth-gated serve only. Base kept un-resolved (symlink-trap memory);
# resolved only inside _contained() for the containment compare.
BACKGROUNDS_DIR = PROJECT_ROOT / "user" / "backgrounds"
MAX_UPLOAD_BYTES = 12 * 1024 * 1024  # 12MB raw (fullscreen images)
FULL_MAX_PX = 1920
THUMB_MAX_PX = 240
ALLOWED_UPLOAD_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
_WINDOWS_RESERVED = ({"CON", "PRN", "AUX", "NUL"}
                     | {f"COM{i}" for i in range(1, 10)}
                     | {f"LPT{i}" for i in range(1, 10)})
_NAME_RE = re.compile(r"^[a-z0-9_-]{1,50}$")  # serve/delete: the stem, no extension


def _sanitize_scene_name(raw: str) -> str:
    """Untrusted 1-word name -> safe filesystem stem, or '' if invalid.
    alnum + - _ only (kills / \\ . : .. traversal), lowercase (kills Boat/boat per-OS
    divergence), strip trailing dots/spaces (Windows strips them), reject empty / too-long
    / Windows reserved device names."""
    if not raw:
        return ""
    s = "".join(c for c in raw.strip() if c.isalnum() or c in "-_")
    s = s.strip(". ").lower()
    if not s or len(s) > 50 or s.upper() in _WINDOWS_RESERVED:
        return ""
    return s


def _full_path(name: str) -> Path:
    return BACKGROUNDS_DIR / f"{name}.webp"


def _thumb_path(name: str) -> Path:
    return BACKGROUNDS_DIR / f"{name}.thumb.webp"


def _contained(path: Path) -> bool:
    """True only if path is a direct child of the library dir. Resolves both sides
    consistently so symlinks/separators can't escape. The name regex already blocks
    traversal; this is defense-in-depth."""
    try:
        return path.resolve().parent == BACKGROUNDS_DIR.resolve()
    except Exception:
        return False


def _reencode(raw: bytes, max_px: int) -> bytes:
    """Validate + re-encode to webp. Strips non-image payloads, bomb-guarded, capped."""
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = 8192 * 8192
    img = Image.open(io.BytesIO(raw)).convert("RGB")
    w, h = img.size
    if max(w, h) > max_px:
        r = max_px / max(w, h)
        img = img.resize((max(1, int(w * r)), max(1, int(h * r))), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="WEBP", quality=85)
    return buf.getvalue()


def _publish_library_changed():
    """Refresh the scene tool's live-menu description after an upload/delete so
    Sapphire always sees the current scenes. Defensive — never breaks the request."""
    try:
        from core.api_fastapi import get_system
        system = get_system()
        if system and getattr(system, 'llm_chat', None):
            system.llm_chat.function_manager.refresh_core_tool_descriptions()
    except Exception as e:
        logger.warning(f"[BG] scene-tool refresh failed: {e}")


@router.get("/api/backgrounds")
async def list_backgrounds(_=Depends(require_login)):
    """List the scene library: [{name, url, thumb}]. Filesystem is the source of truth."""
    out = []
    try:
        if BACKGROUNDS_DIR.exists():
            for p in sorted(BACKGROUNDS_DIR.glob("*.webp")):
                if p.name.endswith(".thumb.webp"):
                    continue
                name = p.name[:-5]  # strip ".webp"
                out.append({
                    "name": name,
                    "url": f"/api/backgrounds/{name}",
                    "thumb": f"/api/backgrounds/{name}?thumb=1",
                })
    except Exception as e:
        logger.warning(f"[BG] list failed: {e}")
    return {"backgrounds": out}


@router.get("/api/backgrounds/{name}")
async def serve_background(name: str, thumb: int = 0, _=Depends(require_login)):
    """Serve a scene image (auth-gated, name-validated, traversal-contained)."""
    if not _NAME_RE.match(name):
        raise HTTPException(status_code=400, detail="Invalid name")
    path = _thumb_path(name) if thumb else _full_path(name)
    if not _contained(path) or not path.exists():
        raise HTTPException(status_code=404, detail="Background not found")
    return FileResponse(str(path), media_type="image/webp")


@router.post("/api/backgrounds")
async def upload_background(name: str = Form(...), overwrite: bool = Form(False),
                            file: UploadFile = File(...), _=Depends(require_login)):
    """Upload a scene. Re-encodes to webp (full + thumb); name -> sanitized stem."""
    safe = _sanitize_scene_name(name)
    if not safe:
        raise HTTPException(status_code=400, detail="Invalid scene name")
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_UPLOAD_EXT:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {ext or 'unknown'}")
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Empty upload")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Image too large (max 12MB)")
    if _full_path(safe).exists() and not overwrite:
        raise HTTPException(status_code=409, detail=f"Scene '{safe}' already exists")

    try:
        full = _reencode(raw, FULL_MAX_PX)
        thumb = _reencode(raw, THUMB_MAX_PX)
    except Exception as e:
        logger.warning(f"[BG] re-encode failed for '{safe}': {e}")
        raise HTTPException(status_code=400, detail="Could not process image (not a valid image?)")

    try:
        BACKGROUNDS_DIR.mkdir(parents=True, exist_ok=True)
        _full_path(safe).write_bytes(full)
        _thumb_path(safe).write_bytes(thumb)
    except Exception as e:
        logger.error(f"[BG] save failed for '{safe}': {e}")
        raise HTTPException(status_code=500, detail="Failed to save background")

    _publish_library_changed()
    logger.info(f"[BG] uploaded scene '{safe}'")
    return {"status": "success", "name": safe, "url": f"/api/backgrounds/{safe}"}


@router.delete("/api/backgrounds/{name}")
async def delete_background(name: str, _=Depends(require_login)):
    """Delete a scene (full + thumb), confined to the library dir + extension."""
    if not _NAME_RE.match(name):
        raise HTTPException(status_code=400, detail="Invalid name")
    full, thumb = _full_path(name), _thumb_path(name)
    if not _contained(full) or not full.exists():
        raise HTTPException(status_code=404, detail="Background not found")
    try:
        full.unlink()
        if thumb.exists():
            thumb.unlink()
    except Exception as e:
        logger.error(f"[BG] delete failed for '{name}': {e}")
        raise HTTPException(status_code=500, detail="Failed to delete background")
    _publish_library_changed()
    logger.info(f"[BG] deleted scene '{name}'")
    return {"status": "success"}
