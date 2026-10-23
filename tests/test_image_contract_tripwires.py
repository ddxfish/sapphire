"""Image-tools rebuild (2026-09-09) — the contract, the handle, the dead flags.

One primitive (core.images), one contract, one handle line, one gallery
renderer. These tripwires keep the old ecosystems from growing back.
"""
import base64
import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.chat.chat_tool_calling import _extract_tool_images, strip_ui_markers

ROOT = Path(__file__).resolve().parent.parent
BANDS = ('core', 'functions', 'plugins')


def _py_files():
    for band in BANDS:
        for p in (ROOT / band).rglob('*.py'):
            if '__pycache__' not in p.parts:
                yield p


# ── the handle line ──────────────────────────────────────────────────────────

def test_receipt_line_rides_with_the_marker():
    history = MagicMock()
    history._effective_chat_name.return_value = 'trinity'
    result = {"text": "here you go",
              "images": [{"data": base64.b64encode(b"x").decode(), "media_type": "image/png",
                          "display_only": True}]}
    text, llm_images = _extract_tool_images(result, history, None, "camera")
    m = re.match(r'<<IMG::tool:([0-9a-f]{12}\.png)>>\n\(image img:\1\)\nhere you go', text)
    assert m, text                                   # marker + receipt at the HEAD, same id
    assert llm_images == []                          # display_only stays hidden
    # the LLM copy keeps the receipt, loses the marker
    assert strip_ui_markers(text) == f"(image img:{m.group(1)})\nhere you go"


# ── UI markers never reach the model ─────────────────────────────────────────

def test_gallery_marker_stripped_from_llm_copy_unless_keep_img():
    body = 'Top 2 images:\n1. a\n   https://h/a.jpg\n<!--GALLERY:[{"thumb":"https://p/t","full":"https://p/f","title":"a]-->x","page":""}]-->'
    assert strip_ui_markers(body) == 'Top 2 images:\n1. a\n   https://h/a.jpg'
    assert '<!--GALLERY:' in strip_ui_markers(body, keep_img=True)   # history-less lanes keep their tiles
    assert strip_ui_markers('<<IMG::tool:a.jpg>>\n<!--GALLERY:["u"]-->\ntext') == 'text'


# ── dead flags stay dead ─────────────────────────────────────────────────────

@pytest.mark.parametrize('flag', ['vibe_when_hidden', 'inject_note'])
def test_no_producer_or_consumer_of_dead_image_flags(flag):
    hits = [p.relative_to(ROOT).as_posix() for p in _py_files()
            if flag in p.read_text(encoding='utf-8', errors='replace') and 'deleted 2026-09-09' not in p.read_text(encoding='utf-8', errors='replace')]
    assert not hits, f"{flag} grew back in: {hits}"


def test_no_sdxl_proxy_in_the_frontend():
    static = ROOT / 'interfaces' / 'web' / 'static'
    hits = [p.relative_to(ROOT).as_posix() for p in static.rglob('*.js')
            if 'sdxl-image' in p.read_text(encoding='utf-8', errors='replace')]
    assert not hits, hits


def test_only_the_core_lanes_read_the_disk_fallback_dir():
    allowed = {'core/chat/chat_tool_calling.py', 'core/images.py',
               'core/routes/media.py'}          # /api/tool-image/ serves the disk fallback
    hits = {p.relative_to(ROOT).as_posix() for p in _py_files()
            if 'tool_images' in p.read_text(encoding='utf-8', errors='replace')
            and re.search(r'["\']tool_images["\']', p.read_text(encoding='utf-8', errors='replace'))}
    assert hits <= allowed, f"user/tool_images read outside the core lanes: {hits - allowed}"


def test_one_gallery_renderer():
    """The GALLERY marker is parsed in exactly one place; the dead GALLERIES /
    CATEGORIES renderers stay dead. (ui-parsing's createGallery wraps markdown
    images — a different, legitimate builder.)"""
    static = ROOT / 'interfaces' / 'web' / 'static'
    parsers = [p.relative_to(ROOT).as_posix() for p in static.rglob('*.js')
               if '<!--GALLERY' in p.read_text(encoding='utf-8', errors='replace')]
    assert parsers == ['interfaces/web/static/shared/gallery-marker.js'], parsers
    for name in ('GALLERIES:', 'CATEGORIES:', '_createGalleryListing', '_createCategoryGrid'):
        hits = [p.name for p in static.rglob('*.js') if name in p.read_text(encoding='utf-8', errors='replace')]
        assert not hits, f"{name} grew back in {hits}"
