"""The task lane rides the ghost rail (2026-11-01): the time a task asks for,
plugin ghost text and the idle line go in one labeled, unpersisted user row
before the live message, and the system prompt carries no clock. Before this
the minute was stamped onto the system prompt and every minute boundary
re-read the whole prompt on any backend that caches a prefix."""
import inspect
from unittest.mock import patch

from core.ghost_messages import _ENVELOPE_HEADER
from test_execctx_retry import _build_ctx, _text_response


def _run(task_settings):
    ctx, te = _build_ctx(task_settings, [_text_response("done.")], tools=[])
    with patch("core.chat.history.count_tokens", return_value=10):
        ctx.run("hello", history_messages=[{"role": "user", "content": "earlier"},
                                           {"role": "assistant", "content": "yes"}])
    sent = te.call_llm_with_metrics.call_args[0][1]
    return ctx, sent


def test_the_time_rides_a_ghost_row_before_the_live_message():
    ctx, sent = _run({"prompt": "agent", "toolset": "none", "inject_datetime": True})
    ghost, live = sent[-2], sent[-1]
    assert ghost["role"] == "user" and ghost.get("_inject") is True
    assert ghost["content"].startswith(_ENVELOPE_HEADER) and "Time:" in ghost["content"]
    assert live == {"role": "user", "content": "hello"}
    assert "Current date" not in sent[0]["content"]           # the system prompt stays a stable prefix


def test_date_mode_means_the_same_as_true():
    ctx, sent = _run({"prompt": "librarian", "toolset": "none", "inject_datetime": "date"})
    assert sent[-2].get("_inject") is True and "Time:" in sent[-2]["content"]


def test_no_setting_no_ghost_row():
    ctx, sent = _run({"prompt": "sapph", "toolset": "none"})
    assert not any(m.get("_inject") for m in sent)
    assert sent[-1] == {"role": "user", "content": "hello"}


def test_the_ghost_row_is_never_persisted():
    ctx, sent = _run({"prompt": "agent", "toolset": "none", "inject_datetime": True})
    assert ctx.new_messages, "the run persisted nothing"
    assert not any(m.get("_inject") for m in ctx.new_messages)
    assert ctx.new_messages[0] == {"role": "user", "content": "hello"}   # the slice starts at the live row


def test_the_system_prompt_builder_has_no_clock():
    from core.continuity.execution_context import ExecutionContext
    src = inspect.getsource(ExecutionContext._build_prompt)
    assert "strftime" not in src and "Current date" not in src
