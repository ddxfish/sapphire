"""One primitive for every image tool (2026-09-09, the image-tools rebuild).

Rules — stated once, here:
1. Browser-facing external image URLs go through `proxied()`; the browser
   never hot-links a third party (Krem: "shield me from Bing").
2. Server-side fetches ride core.net (SOCKS-aware). Never a third HTTP stack.
3. The user sees every image forever; the model sees an image only in the
   turn it arrives — unless `display_only`, then never.
4. Chat images live in the tool_images table; long-term images live in the
   Mind Palace library. Nothing else stores image bytes.
5. One return contract: `result()` → {"text", "images": [{data, media_type,
   display_only}]}. `_extract_tool_images` saves each image and appends an
   `(image img:<id>)` receipt the model can hand to any other image tool.
6. `stash()` is the ONE lane for images a tool wants addressable but not
   shown to the model this turn (search hits behind a contact sheet, the
   individuals behind a grid, a pasted original): same table, vault path,
   cascade and route as every tool image — never a disk cache. The tool's
   text names the handles; the GALLERY marker names them for the browser
   (shared/gallery-marker.js, v3). 2026-09-10.

Handles `resolve()` understands:
    img:<id>     a tool image from this chat (the receipt line)
    doc:<N>      a Mind Palace library image (scope = this chat's memory scope)
    /abs/path    any absolute path PIL can decode
    http(s)://   fetched through core.net, 20MB cap
"""
import base64
import io
import logging
import math
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

logger = logging.getLogger(__name__)

MAX_FETCH = 20 * 1024 * 1024
_PROXY = 'https://external-content.duckduckgo.com/iu/?u={u}&f=1'
_DISK = Path(__file__).parent.parent / 'user' / 'tool_images'
_MEDIA = {'JPEG': 'image/jpeg', 'PNG': 'image/png', 'GIF': 'image/gif',
          'WEBP': 'image/webp', 'HEIF': 'image/heic', 'BMP': 'image/bmp',
          'TIFF': 'image/tiff'}


class ImageError(ValueError):
    """Honest, user-readable failure (no such handle / not an image / …)."""


@dataclass
class Resolved:
    data: bytes
    media_type: str
    label: str      # short human name: filename, title, id, or URL
    origin: str     # 'chat' | 'library' | 'file' | 'web'

    @property
    def size(self):
        return _open(self.data).size


def _heic():
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
    except Exception:
        pass


def _open(raw):
    """PIL image or ImageError. Sniffs bytes — never trusts extensions/headers."""
    from PIL import Image, UnidentifiedImageError
    _heic()
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
        return img
    except (UnidentifiedImageError, OSError, ValueError) as e:
        raise ImageError(f"not an image ({e})") from None


def media_type(raw) -> str:
    fmt = (_open(raw).format or '').upper()
    return _MEDIA.get(fmt, f'image/{fmt.lower() or "octet-stream"}')


_sniff = media_type     # stash()'s parameter shadows the name


def new_id(media_type='image/jpeg') -> str:
    """A tool_images row id: 12 hex + extension — the shape since day one."""
    import uuid
    ext = 'png' if 'png' in (media_type or '') else 'jpg'
    return f"{uuid.uuid4().hex[:12]}.{ext}"


def _disk_put(image_id, raw):
    """History-less lane (isolated tool calls, no session): user/tool_images,
    bounded to the newest 300 — it had no GC and grew forever."""
    _DISK.mkdir(parents=True, exist_ok=True)
    (_DISK / image_id).write_bytes(raw)
    try:
        for old in sorted(_DISK.iterdir(), key=lambda f: f.stat().st_mtime)[:-300]:
            old.unlink()
    except Exception as e:
        logger.debug(f"[IMAGES] tool_images GC skipped: {e}")


def stash(raw, media_type=None, *, visible=False, chat_name=None) -> str:
    """Store bytes in the chat's image store NOW; return the `img:<id>` handle.

    visible=False: the model never saw these pixels (they sit behind a sheet
    or a grid) so the vision window never replays them; True = the model saw
    them live (a pasted image). chat_name=None → the EFFECTIVE chat, never the
    active one: a phone/background stream's image belongs to ITS chat (P3-T4).
    '' is the ephemeral sentinel — no durable home → ImageError, so a cadence
    frame can never leak into the store by accident.
    """
    mt = media_type or _sniff(raw)
    image_id = new_id(mt)
    try:
        sm = _session_manager()
    except Exception as e:
        logger.debug(f"[IMAGES] no session for stash, disk lane: {e}")
        sm = None
    if sm is None:
        _disk_put(image_id, raw)
        return f"img:{image_id}"
    owner = chat_name if chat_name is not None else sm._effective_chat_name()
    if not sm.save_tool_image(image_id, raw, mt, chat_name=owner, visible=visible):
        raise ImageError("couldn't store the image — this turn has no chat to keep it in")
    return f"img:{image_id}"


# ── resolve ──────────────────────────────────────────────────────────────────

def resolve(source, *, private_key=None) -> Resolved:
    s = (source or '').strip() if isinstance(source, str) else ''
    if not s:
        raise ImageError("no image given — use img:<id>, doc:<N>, an absolute path, or a URL")
    if s.startswith('img:'):
        return _chat_image(s[4:].strip())
    if s.startswith('doc:'):
        return _library_image(s[4:].strip(), private_key)
    if s.startswith(('http://', 'https://')):
        return _fetch(s)
    p = Path(s)
    if p.is_absolute():
        return _file(p)
    raise ImageError(f"'{s}' is not a handle — use img:<id>, doc:<N>, an absolute path, or a URL")


def _session_manager():
    from core.api_fastapi import get_system
    return get_system().llm_chat.session_manager


def _chat_image(image_id) -> Resolved:
    row = None
    try:
        row = _session_manager().get_tool_image(image_id)
    except Exception as e:
        logger.debug(f"[IMAGES] chat lookup unavailable: {e}")
    if row:
        return Resolved(row[0], row[1] or media_type(row[0]), f"img:{image_id}", 'chat')
    disk = _DISK / image_id
    if image_id and '/' not in image_id and '\\' not in image_id and disk.is_file():
        raw = disk.read_bytes()
        return Resolved(raw, media_type(raw), f"img:{image_id}", 'chat')
    raise ImageError(f"no image img:{image_id} in this chat")


def _scope():
    from core.chat.function_manager import scope_memory
    return scope_memory.get()


def _library():
    from plugins.mindpalace.tools import library
    return library


def _library_image(doc_id, private_key) -> Resolved:
    try:
        n = int(doc_id)
    except (TypeError, ValueError):
        raise ImageError(f"doc:{doc_id} is not a library id") from None
    scope = _scope()
    if scope is None:
        raise ImageError("the library is unavailable when memory is disabled for this chat")
    try:
        path, label = _library().image_source(scope, n, private_key=private_key)
    except LookupError as e:
        raise ImageError(str(e)) from None
    raw = Path(path).read_bytes()
    return Resolved(raw, media_type(raw), label, 'library')   # label = the photo line


def _file(p: Path) -> Resolved:
    if not p.is_file():
        raise ImageError(f"no file at {p}")
    raw = p.read_bytes()
    return Resolved(raw, media_type(raw), p.name, 'file')


def _fetch(url) -> Resolved:
    from core import net
    try:
        r = net.get(url, profile='browser', timeout=15, stream=True)
        r.raise_for_status()
        buf = bytearray()
        for chunk in r.iter_content(65536):
            buf += chunk
            if len(buf) > MAX_FETCH:
                raise ImageError(f"image over {MAX_FETCH // (1024 * 1024)}MB: {url}")
    except ImageError:
        raise
    except Exception as e:
        raise ImageError(f"couldn't fetch {url}: {e}") from None
    raw = bytes(buf)
    try:
        mt = media_type(raw)
    except ImageError:
        ct = (r.headers.get('content-type') or '?').split(';')[0]
        raise ImageError(f"{url} is not an image ({ct})") from None
    return Resolved(raw, mt, url.rsplit('/', 1)[-1][:80] or url, 'web')


# ── shaping ──────────────────────────────────────────────────────────────────

def for_chat(raw, max_px=1536, quality=88) -> bytes:
    """EXIF-upright, ≤max_px on the long side, RGB JPEG. The one resize."""
    from PIL import Image, ImageOps
    img = ImageOps.exif_transpose(_open(raw))
    img.thumbnail((max_px, max_px), Image.LANCZOS)
    buf = io.BytesIO()
    img.convert('RGB').save(buf, 'JPEG', quality=quality)
    return buf.getvalue()


def _badge_font(pt):
    """Pillow's bundled TrueType (Aileron, Pillow ≥ 10.1) — the one font every
    install has, no system fonts involved. Older Pillow: DejaVu if the OS has
    it, else the 6×8 bitmap (the tiny-digit bug Krem saw)."""
    from PIL import ImageFont
    for load in (lambda: ImageFont.load_default(size=pt),
                 lambda: ImageFont.truetype('DejaVuSans-Bold.ttf', pt)):
        try:
            return load()
        except Exception:
            continue
    return ImageFont.load_default()


def contact_sheet(raws, cell=400) -> bytes:
    """Numbered 1..N grid (ceil(sqrt) columns), JPEG bytes. The one grid.
    Undecodable entries keep their number so '#3' still means the 3rd."""
    from PIL import Image, ImageDraw, ImageFont, ImageOps
    n = max(1, len(raws))
    cols = math.ceil(math.sqrt(n))
    rows = math.ceil(n / cols)
    grid = Image.new('RGB', (cols * cell, rows * cell), (24, 24, 28))
    draw = ImageDraw.Draw(grid)
    pt = max(20, cell // 6)          # a number she can read on a downscaled sheet
    font = _badge_font(pt)
    pad, inner = max(6, pt // 5), max(4, pt // 5)
    for i, raw in enumerate(raws):
        x, y = (i % cols) * cell, (i // cols) * cell
        try:
            im = ImageOps.exif_transpose(_open(raw)).convert('RGB')
            im.thumbnail((cell, cell), Image.LANCZOS)
            grid.paste(im, (x + (cell - im.width) // 2, y + (cell - im.height) // 2))
        except ImageError:
            draw.rectangle([x, y, x + cell, y + cell], fill=(40, 20, 20))
        label = str(i + 1)
        l, t, r, b = draw.textbbox((0, 0), label, font=font)
        draw.rectangle([x + pad, y + pad, x + pad + (r - l) + 2 * inner, y + pad + (b - t) + 2 * inner],
                       fill=(0, 0, 0))
        draw.text((x + pad + inner - l, y + pad + inner - t), label, fill=(255, 255, 255), font=font)
    buf = io.BytesIO()
    grid.save(buf, 'JPEG', quality=88)
    return buf.getvalue()


# ── contract ─────────────────────────────────────────────────────────────────

def result(text, images=(), display_only=False) -> dict:
    """The tool-return contract. `images` = raw bytes (jpeg/png/…)."""
    return {"text": text,
            "images": [{"data": base64.b64encode(raw).decode('ascii'),
                        "media_type": media_type(raw),
                        "display_only": bool(display_only)} for raw in images]}


def last_image_id(chat_name=None):
    """Newest tool image of the effective chat, as an img: handle body — or None."""
    try:
        return _session_manager().last_tool_image_id(chat_name)
    except Exception as e:
        logger.debug(f"[IMAGES] last_image_id unavailable: {e}")
        return None


def proxied(url) -> str:
    """Browser-facing form of an external image URL (rule 1)."""
    return _PROXY.format(u=quote(url, safe=''))
