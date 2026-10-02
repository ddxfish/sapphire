"""ExecCtx context budget vs. images, and the multi-pass trim (2026-10-02).

Discord, 14:22: someone posted a 374KB PNG. The task's model does vision, so
the user turn was a content list carrying the base64, and the trim loop in
ExecutionContext counted it with count_tokens(str(content)) — the text
tokenizer chews base64 at ~1.4 chars/token, so one image was charged 356k
"tokens" on a 228k limit. Trim took one 25% bite of a history that fit fine
on its own, was still "over", bailed as overflow, and she never replied.

Two invariants:
  * an image block costs its flat estimate, never its base64 length
  * the trim keeps biting until the turn fits — one bite was never a rescue
"""
from unittest.mock import MagicMock, patch

from test_heartbeat_loop_cap import _build_ctx, _text_response


class _Vision:
    supports_images = True


def _run(ctx, te, *, user, history, images=None):
    """Returns (reply, the messages list AS SENT) — the loop appends to that
    list after the call, so snapshot it inside the mock."""
    queued, sent = list(te.call_llm_with_metrics.side_effect), []
    def call(provider, messages, *a, **k):
        sent.append([dict(m) for m in messages])
        return queued.pop(0)
    te.call_llm_with_metrics.side_effect = call
    with patch("core.chat.history.count_tokens", side_effect=lambda t: len(t) // 4):
        out = ctx.run(user, history, images=images)
    return out, (sent[0] if sent else None)


def test_vision_turn_is_not_charged_for_its_base64():
    ctx, te = _build_ctx({"prompt": "p", "toolset": "none", "context_limit": 10_000}, [_text_response("seen it")])
    ctx.provider = _Vision()
    ctx.tools = []
    history = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hey"}]
    # 600k base64 chars: the OLD counter charged this 150k on a 10k limit → overflow.
    big = [{"data": "A" * 600_000, "media_type": "image/png"}]
    out, sent = _run(ctx, te, user="what is this?", history=history, images=big)
    assert out == "seen it" and ctx.degraded_reason is None
    assert isinstance(sent[-1]["content"], list) and sent[-1]["content"][1]["type"] == "image"
    assert len(sent) == 4                              # system + 2 history + the turn: nothing trimmed


def test_trim_keeps_biting_until_the_turn_fits():
    # 40 rows × ~1000 tokens on a 10k limit. One 25% bite (the old behaviour)
    # leaves 30k and bails; the loop lands under 80%.
    ctx, te = _build_ctx({"prompt": "p", "toolset": "none", "context_limit": 10_000}, [_text_response("ok")])
    ctx.tools = []
    history = [{"role": "user" if i % 2 == 0 else "assistant", "content": "x" * 4000} for i in range(40)]
    out, sent = _run(ctx, te, user="now?", history=history)
    assert out == "ok" and ctx.degraded_reason is None
    assert sent[-1]["content"] == "now?"               # the live turn always survives
    assert sum(len(str(m["content"])) // 4 for m in sent) <= 8_000


def test_trim_still_reports_overflow_when_nothing_is_droppable():
    ctx, te = _build_ctx({"prompt": "p", "toolset": "none", "context_limit": 1_000}, [_text_response("ok")])
    ctx.tools = []
    out, sent = _run(ctx, te, user="y" * 40_000, history=[])
    assert not te.call_llm_with_metrics.called
    assert "Context overflow" in (ctx.degraded_reason or "")
