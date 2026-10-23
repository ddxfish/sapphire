"""Plugin event-images → vision LLM (2026-06-13).

Guards the dual-form invariant: plugin-supplied images on an event payload are
shown to vision models as base64 THIS turn, but chat history persists only the
`<<IMG::tool:id>>` marker — never inline base64 (which would replay every turn
and bloat the chat). Also covers the validation trust boundary.

If a future refactor re-inlines base64 into new_messages, these fail loudly.
"""
import base64
import pytest

from core.continuity.executor import _extract_event_images
from core.continuity.execution_context import ExecutionContext

GOOD_B64 = base64.b64encode(b"\x89PNG\r\n\x1a\nfakepng").decode()


# ---------------------------------------------------------------- validation
def test_valid_image_passes():
    out = _extract_event_images({"images": [{"data": GOOD_B64, "media_type": "image/png"}]})
    assert len(out) == 1 and out[0]["media_type"] == "image/png"


def test_no_images_key_returns_empty():
    assert _extract_event_images({"content": "hi"}) == []


def test_bad_base64_dropped():
    assert _extract_event_images({"images": [{"data": "!!!notb64!!!", "media_type": "image/png"}]}) == []


def test_unsupported_media_type_dropped():
    assert _extract_event_images({"images": [{"data": GOOD_B64, "media_type": "application/pdf"}]}) == []


def test_missing_data_dropped():
    assert _extract_event_images({"images": [{"media_type": "image/png"}]}) == []


def test_count_capped_at_8():
    payload = {"images": [{"data": GOOD_B64, "media_type": "image/png"}] * 20}
    assert len(_extract_event_images(payload)) == 8


def test_non_dict_payload_returns_empty():
    assert _extract_event_images("nope") == []
    assert _extract_event_images(None) == []


# --------------------------------------------------------------- dual-form
class _VisionProvider:
    supports_images = True


class _NoVisionProvider:
    supports_images = False


def _ctx(provider):
    ctx = ExecutionContext.__new__(ExecutionContext)
    ctx.provider = provider
    return ctx


MARKER_MSG = "look at this\n<<IMG::tool:abc123.png>>"
IMGS = [{"data": GOOD_B64, "media_type": "image/png"}]


def test_vision_llm_gets_image_block_history_gets_marker():
    llm, persist = _ctx(_VisionProvider())._build_user_message(MARKER_MSG, IMGS)
    # LLM side: a content list carrying an image block, marker stripped from text
    assert isinstance(llm, list)
    assert any(b.get("type") == "image" for b in llm)
    assert "<<IMG" not in llm[0]["text"]
    # Persist side: the marker string, and CRITICALLY no base64 inline
    assert persist == MARKER_MSG
    assert GOOD_B64 not in persist


def test_no_vision_persists_marker_no_base64():
    # vibe path may load CLIP; we only assert no base64 leaks into either form
    llm, persist = _ctx(_NoVisionProvider())._build_user_message(MARKER_MSG, IMGS)
    assert isinstance(llm, str) and GOOD_B64 not in llm
    assert persist == MARKER_MSG and GOOD_B64 not in persist


def test_no_images_is_identical_passthrough():
    llm, persist = _ctx(_VisionProvider())._build_user_message("plain text", None)
    assert llm == "plain text" and persist == "plain text"
