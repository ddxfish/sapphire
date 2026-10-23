# core/routes/fonts.py — Downloadable UI webfonts (themes-v2 P2).
#
# Krem's rule: fonts are NEVER shipped in git — Sapphire downloads them on
# first use, and only fonts we are certain are license-safe (SIL OFL). Each
# entry is pinned to an exact google/fonts commit AND a sha256; a mismatch is
# refused, never served. Files land in user/fonts/ (gitignored user dir) and
# are served through this auth-gated handler — NOT the public /user-assets
# mount (same doctrine as backgrounds.py).
#
# Client side: shared/fonts.js registers downloaded fonts via the FontFace
# API (no static @font-face → no 404 noise for never-downloaded fonts).

import hashlib
import logging
import tempfile
import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from core.auth import require_login
from core.fs_utils import replace_with_retry

logger = logging.getLogger(__name__)
router = APIRouter()

PROJECT_ROOT = Path(__file__).absolute().parent.parent.parent
FONTS_DIR = PROJECT_ROOT / "user" / "fonts"

# Pinned to google/fonts commit 352f6b7d9d6cc4fa9e242b931291d31b21a6dc84
# (verified live + hashed 2026-08-16). All SIL Open Font License.
_PIN = "352f6b7d9d6cc4fa9e242b931291d31b21a6dc84"
_RAW = f"https://raw.githubusercontent.com/google/fonts/{_PIN}"

FONTS = {
    "caveat": {
        "label": "Caveat", "css_family": "Caveat", "file": "Caveat.ttf",
        "url": f"{_RAW}/ofl/caveat/Caveat%5Bwght%5D.ttf",
        "sha256": "0bdb6b660482d31531b3945849fba5916b3ef8695da7024a9e6b9ee3c4157988",
        "license": "OFL",
    },
    "lora": {
        "label": "Lora", "css_family": "Lora", "file": "Lora.ttf",
        "url": f"{_RAW}/ofl/lora/Lora%5Bwght%5D.ttf",
        "sha256": "822a6621ccbe8d97d20ac88c1c41f5615c9c2c202eaa75f272cd452aac6475a7",
        "license": "OFL",
    },
    "jetbrainsmono": {
        "label": "JetBrains Mono", "css_family": "JetBrains Mono", "file": "JetBrainsMono.ttf",
        "url": f"{_RAW}/ofl/jetbrainsmono/JetBrainsMono%5Bwght%5D.ttf",
        "sha256": "48715a42ec242c21e9f02692891e147d022299a52e48d5e413e1a942193ffeda",
        "license": "OFL",
    },
    "nunito": {
        "label": "Nunito", "css_family": "Nunito", "file": "Nunito.ttf",
        "url": f"{_RAW}/ofl/nunito/Nunito%5Bwght%5D.ttf",
        "sha256": "bb55a5ca5c2042335b3991af27c4d0705d0ef41cac6164ac737fd8f2a1e85207",
        "license": "OFL",
    },
}


def _font_path(family):
    return FONTS_DIR / FONTS[family]["file"]


@router.get("/api/fonts")
async def list_fonts(_=Depends(require_login)):
    """Registry + downloaded state, for the Visual tab's Type cards."""
    return {"fonts": [
        {"family": fam, "label": f["label"], "css_family": f["css_family"],
         "license": f["license"], "downloaded": _font_path(fam).exists()}
        for fam, f in FONTS.items()
    ]}


@router.get("/api/fonts/file/{family}")
async def serve_font(family: str, _=Depends(require_login)):
    if family not in FONTS:
        raise HTTPException(status_code=404, detail="Unknown font")
    path = _font_path(family)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Font not downloaded")
    return FileResponse(str(path), media_type="font/ttf",
                        headers={"Cache-Control": "public, max-age=86400"})


class _DownloadReq(BaseModel):
    family: str


@router.post("/api/fonts/download")
async def download_font(req: _DownloadReq, _=Depends(require_login)):
    """Fetch a pinned font (socks-aware), verify sha256, install atomically."""
    family = req.family
    if family not in FONTS:
        raise HTTPException(status_code=404, detail="Unknown font")
    meta = FONTS[family]
    path = _font_path(family)
    if path.exists():
        return {"status": "ok", "family": family, "downloaded": True, "cached": True}

    try:
        from core.socks_proxy import get_session
        resp = get_session().get(meta["url"], timeout=60)
        resp.raise_for_status()
        data = resp.content
    except Exception as e:
        logger.warning(f"[Fonts] download failed for {family}: {e}")
        raise HTTPException(status_code=502, detail=f"Download failed: {e}")

    digest = hashlib.sha256(data).hexdigest()
    if digest != meta["sha256"]:
        # Refuse, never install: the pin is the whole integrity story.
        logger.error(f"[Fonts] sha256 mismatch for {family}: got {digest}")
        raise HTTPException(status_code=502, detail="Integrity check failed (sha256 mismatch)")

    FONTS_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(FONTS_DIR), suffix=".part")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        replace_with_retry(Path(tmp), path)
    except Exception:
        try: os.unlink(tmp)
        except OSError: pass
        raise
    logger.info(f"[Fonts] installed {family} ({len(data)} bytes, sha256 verified)")
    return {"status": "ok", "family": family, "downloaded": True}
