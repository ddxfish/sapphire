"""anthropic_compat vision passthrough (2026-06-14).

The vision checkbox (`supports_images`) was made configurable 2026-06-12, but
`_convert_messages` still STRIPPED image blocks unconditionally — opened the gate,
never built the road. A real image through MiniMax M3 (which DOES support vision via
Anthropic-compatible content blocks) hit the strip and the model never saw pixels.

Fix: when `supports_images` is True, convert the internal image block
{type:image, data, media_type} → Anthropic {type:image, source:{type:base64,...}}
(mirroring claude.py / what MiniMax M3's anthropic endpoint expects). When False,
keep the existing strip + placeholder behavior (and the consecutive-assistant 400 guard).
"""
from core.chat.llm_providers.anthropic_compat import AnthropicCompatProvider

IMG = {"type": "image", "data": "QkFTRTY0", "media_type": "image/png"}
TXT = {"type": "text", "text": "look at this"}


def _provider(supports_images):
    p = AnthropicCompatProvider.__new__(AnthropicCompatProvider)
    p.config = {"supports_images": supports_images}
    return p


def _user_blocks(msgs):
    user = [m for m in msgs if m["role"] == "user"][-1]
    return user["content"]


def test_vision_on_converts_image_to_anthropic_source():
    """THE BUG: with vision on, the image must survive AND be in Anthropic source.base64."""
    _sys, msgs = _provider(True)._convert_messages([{"role": "user", "content": [TXT, IMG]}])
    blocks = _user_blocks(msgs)
    imgs = [b for b in blocks if b.get("type") == "image"]
    assert len(imgs) == 1, "image block must survive when vision is on (not stripped)"
    src = imgs[0]["source"]
    assert src["type"] == "base64"
    assert src["media_type"] == "image/png"
    assert src["data"] == "QkFTRTY0"
    assert any(b.get("type") == "text" for b in blocks), "text caption preserved"


def test_vision_off_strips_image():
    """Vision off keeps the existing strip behavior (text-only endpoints)."""
    _sys, msgs = _provider(False)._convert_messages([{"role": "user", "content": [TXT, IMG]}])
    blocks = _user_blocks(msgs)
    assert not any(b.get("type") == "image" for b in blocks), "image stripped when vision off"
    assert any(b.get("type") == "text" for b in blocks)


def test_vision_off_image_only_gets_placeholder():
    """Image-only + vision off must not drop the user turn (alternation-400 guard)."""
    _sys, msgs = _provider(False)._convert_messages([{"role": "user", "content": [IMG]}])
    blocks = _user_blocks(msgs)
    assert blocks and blocks[0]["type"] == "text", "placeholder text keeps the turn"


def test_tool_result_unaffected_by_vision_branch():
    """Tool results (role=tool → user/tool_result) must be untouched by the fix."""
    _sys, msgs = _provider(True)._convert_messages(
        [{"role": "tool", "tool_call_id": "t1", "content": "ok"}])
    blocks = _user_blocks(msgs)
    assert blocks[0]["type"] == "tool_result"


def test_plain_string_user_unaffected():
    _sys, msgs = _provider(True)._convert_messages([{"role": "user", "content": "hi"}])
    user = [m for m in msgs if m["role"] == "user"][-1]
    assert user["content"] == "hi"


def test_a_usage_delta_without_input_tokens_does_not_kill_the_turn():
    """Hunt 19: the SDK types input_tokens Optional, so a gateway sending only
    output_tokens in message_delta made `None + int` raise after the reply."""
    from contextlib import contextmanager
    from types import SimpleNamespace as NS
    from anthropic.types import MessageDeltaUsage

    events = [
        NS(type="content_block_start", index=0, content_block=NS(type="text", text="")),
        NS(type="content_block_delta", index=0, delta=NS(type="text_delta", text="Hello")),
        NS(type="content_block_stop", index=0),
        NS(type="message_delta", delta=NS(stop_reason="end_turn"),
           usage=MessageDeltaUsage.construct(output_tokens=5)),
    ]

    @contextmanager
    def stream(**kw):
        yield iter(events)
    p = _provider(False)
    p.model = "m"
    p._thinking_enabled, p._thinking_budget = False, 0
    p._hdr_kwargs = lambda: {}
    p._client = NS(messages=NS(stream=stream))
    out = list(p.chat_completion_stream([{"role": "user", "content": "hi"}]))
    done = [e for e in out if e.get("type") == "done"][-1]
    assert done["response"].content == "Hello"
    assert done["response"].usage["completion_tokens"] == 5
