"""Gallery tools — let Sapphire browse and view the user's image collection."""

import base64
import hashlib
import io
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

ENABLED = True
EMOJI = "\U0001F5BC"

IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp', '.tiff', '.tif'}

TOOLS = [
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "gallery_browse",
            "description": "Browse image folders. Lists subfolders + image counts. Start with path='' for root.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Folder path from gallery root. '' = root."
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "gallery_grid",
            "description": "Numbered thumbnail grid for a folder. Use gallery_browse to find the folder first.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Folder path from gallery root"
                    },
                    "page": {
                        "type": "integer",
                        "description": "Page (default 1)",
                        "default": 1
                    }
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "gallery_view",
            "description": "View one image at full preview. Use index from gallery_grid.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Folder path from gallery root"
                    },
                    "index": {
                        "type": "integer",
                        "description": "Image index from gallery_grid"
                    }
                },
                "required": ["path", "index"]
            }
        }
    }
]


def _get_settings():
    """Get gallery plugin settings."""
    try:
        from core.plugin_loader import plugin_loader
        return plugin_loader.get_plugin_settings("gallery") or {}
    except Exception:
        return {}


def _get_gallery_root():
    """Get and validate the gallery root path."""
    settings = _get_settings()
    root = settings.get("gallery_root", "~/Pictures")
    root = os.path.expanduser(root)
    root = Path(root).resolve()
    if not root.exists():
        return None, f"Gallery root not found: {root}"
    if not root.is_dir():
        return None, f"Gallery root is not a directory: {root}"
    return root, None


def _safe_path(root, relative_path):
    """Resolve a relative path safely within the gallery root. Blocks traversal."""
    if not relative_path:
        return root
    target = (root / relative_path).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return None  # path traversal attempt
    return target


def _list_images(folder):
    """List image files in a folder (non-recursive), sorted by name."""
    if not folder.is_dir():
        return []
    return sorted(
        [f for f in folder.iterdir() if f.is_file() and f.suffix.lower() in IMAGE_EXTS],
        key=lambda f: f.name.lower()
    )


def _get_grid_dims():
    settings = _get_settings()
    grid = settings.get("grid_size", "4x3")
    try:
        cols, rows = grid.split("x")
        return int(cols), int(rows)
    except Exception:
        return 4, 3


def _get_cache_dir(root):
    cache = root / "_sapphire_cache"
    cache.mkdir(exist_ok=True)
    return cache


def _make_thumbnail(img_path, size=200):
    """Create a square thumbnail. Returns PIL Image."""
    from PIL import Image
    img = Image.open(img_path)
    img.thumbnail((size, size), Image.LANCZOS)
    # Pad to exact square
    result = Image.new("RGB", (size, size), (30, 30, 40))
    offset = ((size - img.width) // 2, (size - img.height) // 2)
    result.paste(img, offset)
    return result


def _build_grid(images, cols, rows, thumb_size=200):
    """Build a numbered thumbnail grid. Returns PNG bytes."""
    from PIL import Image, ImageDraw, ImageFont

    grid_w = cols * thumb_size
    grid_h = rows * thumb_size
    grid = Image.new("RGB", (grid_w, grid_h), (20, 20, 30))
    draw = ImageDraw.Draw(grid)

    # Try to get a reasonable font
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 18)
    except Exception:
        try:
            font = ImageFont.truetype("/usr/share/fonts/TTF/DejaVuSans-Bold.ttf", 18)
        except Exception:
            font = ImageFont.load_default()

    for i, img_path in enumerate(images):
        if i >= cols * rows:
            break
        row, col = divmod(i, cols)
        x, y = col * thumb_size, row * thumb_size

        try:
            thumb = _make_thumbnail(img_path, thumb_size)
            grid.paste(thumb, (x, y))
        except Exception as e:
            logger.debug(f"Thumbnail failed for {img_path.name}: {e}")
            # Draw error placeholder
            draw.rectangle([x, y, x + thumb_size, y + thumb_size], fill=(40, 20, 20))
            draw.text((x + 4, y + thumb_size // 2), "ERR", fill=(200, 80, 80), font=font)

        # Number overlay — bottom-left with dark background
        num = str(i + 1)
        bbox = font.getbbox(num)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        nx, ny = x + 4, y + thumb_size - th - 8
        draw.rectangle([nx - 2, ny - 2, nx + tw + 4, ny + th + 4], fill=(0, 0, 0, 180))
        draw.text((nx, ny), num, fill=(255, 255, 255), font=font)

    buf = io.BytesIO()
    grid.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _resize_preview(img_path, max_px=1080):
    """Resize an image for preview. Returns JPEG bytes."""
    from PIL import Image
    img = Image.open(img_path)
    if img.mode in ('RGBA', 'P'):
        img = img.convert('RGB')
    # Resize if larger than max
    if max(img.size) > max_px:
        ratio = max_px / max(img.size)
        new_size = (int(img.width * ratio), int(img.height * ratio))
        img = img.resize(new_size, Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def execute(function_name, arguments, config=None):
    if function_name == "gallery_browse":
        return _exec_browse(arguments)
    elif function_name == "gallery_grid":
        return _exec_grid(arguments)
    elif function_name == "gallery_view":
        return _exec_view(arguments)
    return f"Unknown function: {function_name}", False


def _exec_browse(args):
    root, err = _get_gallery_root()
    if err:
        return err, False

    path = args.get("path", "")
    target = _safe_path(root, path)
    if not target or not target.is_dir():
        return f"Folder not found: {path}", False

    # List subfolders
    subfolders = []
    for child in sorted(target.iterdir()):
        if child.is_dir() and not child.name.startswith((".", "_")):
            # Count images recursively
            img_count = sum(1 for f in child.rglob("*") if f.suffix.lower() in IMAGE_EXTS)
            if img_count > 0:
                subfolders.append(f"  {child.name}/ ({img_count} images)")
            else:
                subfolders.append(f"  {child.name}/ (empty)")

    # Count images in this folder directly
    images = _list_images(target)
    display_path = path or "(root)"

    lines = [f"Gallery: {display_path}"]
    if images:
        lines.append(f"Images here: {len(images)}")
    if subfolders:
        lines.append(f"Subfolders:")
        lines.extend(subfolders)
    if not images and not subfolders:
        lines.append("No images or subfolders found.")

    return "\n".join(lines), True


def _exec_grid(args):
    root, err = _get_gallery_root()
    if err:
        return err, False

    path = args.get("path", "")
    page = max(1, args.get("page", 1))

    target = _safe_path(root, path)
    if not target or not target.is_dir():
        return f"Folder not found: {path}", False

    images = _list_images(target)
    if not images:
        return f"No images in {path or 'root'}", False

    cols, rows = _get_grid_dims()
    per_page = cols * rows
    total_pages = (len(images) + per_page - 1) // per_page
    page = min(page, total_pages)

    start = (page - 1) * per_page
    page_images = images[start:start + per_page]

    # Build grid
    try:
        grid_bytes = _build_grid(page_images, cols, rows)
    except Exception as e:
        logger.error(f"Grid build failed: {e}", exc_info=True)
        return f"Failed to build image grid: {e}", False

    # Return as tool image
    b64 = base64.b64encode(grid_bytes).decode()
    image_result = {
        "type": "image",
        "data": b64,
        "media_type": "image/png"
    }

    # Build text summary
    listing = []
    for i, img in enumerate(page_images):
        size_kb = img.stat().st_size // 1024
        listing.append(f"  {start + i + 1}. {img.name} ({size_kb}KB)")

    text = (
        f"Grid: {path or 'root'} — page {page}/{total_pages} "
        f"(showing {start + 1}-{start + len(page_images)} of {len(images)})\n"
        + "\n".join(listing)
    )

    return {"text": text, "images": [image_result]}, True


def _exec_view(args):
    root, err = _get_gallery_root()
    if err:
        return err, False

    path = args.get("path", "")
    index = args.get("index", 1)

    target = _safe_path(root, path)
    if not target or not target.is_dir():
        return f"Folder not found: {path}", False

    images = _list_images(target)
    if not images:
        return f"No images in {path or 'root'}", False

    if index < 1 or index > len(images):
        return f"Image index {index} out of range (1-{len(images)})", False

    img_path = images[index - 1]
    settings = _get_settings()
    max_px = settings.get("preview_max_px", 1080)

    try:
        preview_bytes = _resize_preview(img_path, max_px=max_px)
    except Exception as e:
        logger.error(f"Preview failed for {img_path}: {e}", exc_info=True)
        return f"Failed to load image: {e}", False

    b64 = base64.b64encode(preview_bytes).decode()
    image_result = {
        "type": "image",
        "data": b64,
        "media_type": "image/jpeg"
    }

    size_kb = img_path.stat().st_size // 1024
    text = f"Image {index}/{len(images)}: {img_path.name} ({size_kb}KB, {max_px}px preview)"

    return {"text": text, "images": [image_result]}, True
