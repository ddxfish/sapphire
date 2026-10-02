"""Plugin event-images → vision LLM (2026-06-13).

Guards the dual-form invariant: plugin-supplied images on an event payload are
shown to vision models as base64 THIS turn, but chat history persists only the
`<<IMG::tool:id>>` marker — never inline base64 (which would replay every turn
and bloat the chat). Also covers the validation trust boundary.

If a future refactor re-inlines base64 into new_messages, these fail loudly.
"""
import base64
import io
import pytest

from core.continuity.executor import _extract_event_images, ContinuityExecutor
from core.continuity.execution_context import ExecutionContext


def _png(w=8, h=8):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (200, 30, 30)).save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


# A real PNG: since 2026-10-02 every event image is decoded and fitted
# (core.images.for_chat), so a fake header is dropped as undecodable.
GOOD_B64 = _png()


# ---------------------------------------------------------------- validation
def test_valid_image_passes_as_fitted_jpeg():
    out = _extract_event_images({"images": [{"data": GOOD_B64, "media_type": "image/png"}]})
    assert len(out) == 1 and out[0]["media_type"] == "image/jpeg"
    assert base64.b64decode(out[0]["data"])[:3] == b"\xff\xd8\xff"   # JPEG magic


def test_big_image_is_fitted_to_the_chat_edge():
    # 2026-10-02: a 694x1541 PNG off Discord went to the model raw (374KB,
    # 498k base64 chars). Pixels are what's billed; the long edge is capped.
    from PIL import Image
    out = _extract_event_images({"images": [{"data": _png(2400, 3200), "media_type": "image/png"}]})
    im = Image.open(io.BytesIO(base64.b64decode(out[0]["data"])))
    assert max(im.size) <= 1536 and im.format == "JPEG"


def test_undecodable_image_dropped_and_noted():
    fake = base64.b64encode(b"\x89PNG\r\n\x1a\nfakepng").decode()
    notes = []
    assert _extract_event_images({"images": [{"data": fake, "media_type": "image/png"}]}, notes) == []
    assert len(notes) == 1 and "could not be processed" in notes[0]


def test_oversize_image_dropped_and_noted():
    huge = base64.b64encode(b"\0" * (11 * 1024 * 1024)).decode()
    notes = []
    assert _extract_event_images({"images": [{"data": huge, "media_type": "image/jpeg"}]}, notes) == []
    assert notes == ["11MB is over the 10MB cap"]


def test_notes_are_optional():
    fake = base64.b64encode(b"nope").decode()
    assert _extract_event_images({"images": [{"data": fake, "media_type": "image/png"}]}) == []


def test_run_tells_the_model_what_was_removed(monkeypatch):
    """The call site: a removed image becomes a line in the user turn, so
    'what's in the picture?' gets an honest answer instead of a guess."""
    import json
    ex = ContinuityExecutor.__new__(ContinuityExecutor)
    seen = {}
    monkeypatch.setattr(ex, "_resolve_persona", lambda t: t)
    def grab(task, result, *a, **k):
        seen.update(task); return result
    monkeypatch.setattr(ex, "_run_background", grab)
    fake = base64.b64encode(b"\x89PNG\r\n\x1a\nfakepng").decode()
    payload = json.dumps({"text": "look", "channel_id": "1", "account": "a",
                          "images": [{"data": fake, "media_type": "image/png"},
                                     {"data": GOOD_B64, "media_type": "image/png"}]})
    ex.run({"name": "t", "initial_message": "reply", "chat_target": ""}, event_data=payload)
    assert "(An attached image was removed before it reached you: could not be processed" in seen["initial_message"]
    assert len(seen["_event_images"]) == 1          # the good one still rides


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


def test_no_vision_persists_the_description_with_the_marker(monkeypatch):
    # 2026-09-26: a text-only model gets no image on replay, so the caption she
    # read this turn stays in history — text only, never base64.
    from core import image_describe
    monkeypatch.setattr(image_describe, 'describe', lambda data, private=None: 'Vibes: a red barn at dusk.')
    llm, persist = _ctx(_NoVisionProvider())._build_user_message(MARKER_MSG, IMGS)
    assert isinstance(llm, str) and GOOD_B64 not in llm
    assert persist == llm == MARKER_MSG + '\n\nVibes: a red barn at dusk.'
    assert GOOD_B64 not in persist


def test_no_vision_describer_failure_persists_the_marker_alone(monkeypatch):
    from core import image_describe

    def boom(data, private=None):
        raise RuntimeError('no describer')
    monkeypatch.setattr(image_describe, 'describe', boom)
    llm, persist = _ctx(_NoVisionProvider())._build_user_message(MARKER_MSG, IMGS)
    assert llm == persist == MARKER_MSG


def test_no_images_is_identical_passthrough():
    llm, persist = _ctx(_VisionProvider())._build_user_message("plain text", None)
    assert llm == "plain text" and persist == "plain text"
