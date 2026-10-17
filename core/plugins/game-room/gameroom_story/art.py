# engine/art.py — the story art store (W1, plan tmp/story-builder-plan.md).
#
# One content-addressed pool: user/story_art/<sha16>.webp. Every upload is
# recompressed unconditionally (PIL → webp, capped long edge) and NAMED BY
# THE HASH OF THE OUTPUT BYTES — dedup is the addressing scheme, not a
# cleanup job. Re-encoding also drops EXIF (phone GPS) for free; camera
# orientation is applied first so stripped photos don't lie on their side.
# References everywhere are the bare filename; the DB never holds pixels.
import hashlib
import io
import logging
import os
import re

from . import rooms

logger = logging.getLogger(__name__)

ART_NAME_RE = re.compile(r"^[0-9a-f]{16}\.webp$")
MAX_EDGE = 1600
QUALITY = 82
MAX_UPLOAD = 15 * 1024 * 1024


def store_dir():
    p = rooms.SAPPHIRE_ROOT / "user" / "story_art"
    p.mkdir(parents=True, exist_ok=True)
    return p


def ingest(data):
    """bytes → (name, None) or (None, reason). Idempotent by construction:
    the same source image lands on the same file."""
    if not data:
        return None, "Empty upload."
    if len(data) > MAX_UPLOAD:
        return None, f"Image too large ({MAX_UPLOAD // (1024 * 1024)}MB cap)."
    try:
        from PIL import Image, ImageOps
        img = Image.open(io.BytesIO(data))
        img.load()
        img = ImageOps.exif_transpose(img)
    except Exception:
        return None, "Not a readable image."
    has_alpha = img.mode in ("RGBA", "LA") or (
        img.mode == "P" and "transparency" in img.info)
    img = img.convert("RGBA" if has_alpha else "RGB")
    w, h = img.size
    scale = MAX_EDGE / max(w, h)
    if scale < 1:
        img = img.resize((max(1, round(w * scale)), max(1, round(h * scale))),
                         Image.LANCZOS)
    buf = io.BytesIO()
    try:
        img.save(buf, "WEBP", quality=QUALITY, method=6)
    except Exception as e:
        return None, f"Could not encode: {e}"
    out = buf.getvalue()
    name = hashlib.sha256(out).hexdigest()[:16] + ".webp"
    path = store_dir() / name
    if not path.exists():
        # Unique tmp per writer (2026-08-21 hunt, 3-scout convergence): a
        # shared ".tmp-<name>" let two concurrent identical uploads write
        # into ONE tmp file — a torn interleave could get promoted, and on
        # Windows the second replace hits WinError 32. Content-addressing
        # makes a lost race harmless: the winner's bytes are ours too.
        import threading
        tmp = path.with_name(f".tmp-{os.getpid()}-{threading.get_ident()}-{name}")
        try:
            tmp.write_bytes(out)
            os.replace(tmp, path)
        except OSError:
            if not path.exists():
                raise
        finally:
            tmp.unlink(missing_ok=True)
        logger.info(f"[STORY-ART] stored {name} ({len(out) // 1024}KB, "
                    f"{img.size[0]}x{img.size[1]})")
    return name, None


def art_path(name):
    """Store path for a valid art name, or None (bad name / missing)."""
    n = str(name or "")
    if not ART_NAME_RE.fullmatch(n):
        return None
    p = store_dir() / n
    return p if p.is_file() else None
