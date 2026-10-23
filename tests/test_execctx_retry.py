"""Regression tests for the 2026-07-19 librarian b12 spiral incident.

A GLM-5.2 thinking model fell into a repetition loop mid-batch, burned its
entire 8192-token budget on thinking (35K chars), and hit finish=length with
zero tool calls. openai_compat converted thinking→content, so ExecutionContext
saw a "normal" text reply and accepted it as the final answer — no degraded
signal, no verdicts for that message's 10 items, and the garbage persisted as
a 36KB assistant message.

Fix: one rescue decode per run. A tool task whose turn ends finish=length
with no tool call, or any turn returning empty content with no tool calls,
gets retried ONCE with identical messages (nothing appended — the garbage
never poisons the retry). A second failure keeps the text for audit but sets
degraded_reason so workers don't count the message as ok.
"""
from unittest.mock import MagicMock, patch

import pytest


def _build_ctx(task_settings, llm_responses, *, tools=None):
    """ExecutionContext with all I/O patched out (test_heartbeat_loop_cap idiom).

    `llm_responses` returned on successive call_llm_with_metrics() calls.
    `tools=None` uses a one-tool toolset; pass [] for a no-tools task.
    """
    from core.continuity.execution_context import ExecutionContext

    fm = MagicMock()
    fm.all_possible_tools = []
    fm._apply_mode_filter = lambda x: x

    te = MagicMock()
    te.call_llm_with_metrics.side_effect = list(llm_responses)
    te.execute_tool_calls.return_value = (1, [])
    te.extract_function_call_from_text.return_value = None

    resolved_tools = [{"function": {"name": "set_event_dates"}}] if tools is None else tools
    with patch.object(ExecutionContext, "_build_prompt", return_value="sys"), \
         patch.object(ExecutionContext, "_resolve_provider",
                      return_value=("k", MagicMock(), "")), \
         patch.object(ExecutionContext, "_build_gen_params", return_value={}), \
         patch.object(ExecutionContext, "_resolve_tools",
                      return_value=resolved_tools), \
         patch.object(ExecutionContext, "_build_scopes", return_value={}):
        ctx = ExecutionContext(fm, te, task_settings)
        ctx._allowed_tool_names = {"set_event_dates"}
    return ctx, te


def _text_response(text, finish="stop"):
    r = MagicMock()
    r.has_tool_calls = False
    r.content = text
    r.finish_reason = finish
    return r


def _tool_call_response(call_id="tc1", name="set_event_dates"):
    r = MagicMock()
    r.has_tool_calls = True
    r.get_tool_calls_as_dicts.return_value = [
        {"id": call_id, "function": {"name": name, "arguments": "{}"}}
    ]
    r.content = ""
    r.finish_reason = "tool_calls"
    return r


SPIRAL = "MILESTONE - Aprilest " * 50  # stand-in for the 35K-char thinking loop


def test_length_death_with_no_tool_call_retries_once_and_recovers():
    """The b12 shape: spiral (finish=length, no call) → retry lands the call."""
    responses = [
        _text_response(SPIRAL, finish="length"),
        _tool_call_response(),
        _text_response("2 dated.", finish="stop"),
    ]
    ctx, te = _build_ctx({"prompt": "librarian", "toolset": "librarian-temporal",
                          "max_tool_rounds": 8}, responses)

    with patch("core.chat.history.count_tokens", return_value=10):
        result = ctx.run("date these memories")

    assert te.call_llm_with_metrics.call_count == 3, (
        f"Expected spiral + retry(tool round) + final = 3 LLM calls, got "
        f"{te.call_llm_with_metrics.call_count}."
    )
    assert result == "2 dated.", f"Retry should recover the run; got {result!r}"
    assert ctx.degraded_reason is None, (
        f"A rescued run must not stay flagged: {ctx.degraded_reason!r}"
    )


def test_length_death_retry_does_not_poison_context():
    """The garbage turn must NOT be appended — the retry sees identical
    messages, not messages + 36KB of spiral."""
    responses = [
        _text_response(SPIRAL, finish="length"),
        _text_response("done", finish="stop"),
    ]
    ctx, te = _build_ctx({"prompt": "librarian", "toolset": "librarian-temporal"},
                         responses)

    with patch("core.chat.history.count_tokens", return_value=10):
        ctx.run("date these")

    retry_messages = te.call_llm_with_metrics.call_args_list[1].args[1]
    assert not any(SPIRAL in str(m.get("content", "")) for m in retry_messages), (
        "Spiral text leaked into the retry call's messages — the failed turn "
        "must be discarded, not appended."
    )


def test_double_length_death_keeps_text_and_flags_degraded():
    """Second consecutive length-death: keep the text for audit, set amber."""
    responses = [
        _text_response(SPIRAL, finish="length"),
        _text_response(SPIRAL, finish="length"),
    ]
    ctx, te = _build_ctx({"prompt": "librarian", "toolset": "librarian-temporal"},
                         responses)

    with patch("core.chat.history.count_tokens", return_value=10):
        result = ctx.run("date these")

    assert te.call_llm_with_metrics.call_count == 2, "Exactly one retry, no more."
    assert result == SPIRAL, "Second failure keeps the text — chat is the audit surface."
    assert ctx.degraded_reason is not None and "max_tokens" in ctx.degraded_reason, (
        f"Twice-failed run must flag degraded: {ctx.degraded_reason!r}"
    )


def test_empty_response_retries_once_then_degrades():
    """Empty content + no calls: one rescue; a second empty breaks degraded."""
    responses = [
        _text_response("", finish="stop"),
        _text_response("recovered", finish="stop"),
    ]
    ctx, te = _build_ctx({"prompt": "agent", "toolset": "all"}, responses)
    with patch("core.chat.history.count_tokens", return_value=10):
        result = ctx.run("hi")
    assert te.call_llm_with_metrics.call_count == 2
    assert result == "recovered"
    assert ctx.degraded_reason is None

    responses = [_text_response("", finish="stop"), _text_response("", finish="stop")]
    ctx, te = _build_ctx({"prompt": "agent", "toolset": "all"}, responses)
    with patch("core.chat.history.count_tokens", return_value=10):
        result = ctx.run("hi")
    assert te.call_llm_with_metrics.call_count == 2
    assert ctx.degraded_reason is not None, "Double-empty must stay degraded."


def test_length_on_no_tool_task_is_accepted_without_retry():
    """A no-tools task that truncates wrote a long answer that got cut —
    retrying reproduces the same truncation, so accept it as-is."""
    responses = [_text_response("A very long essay tha", finish="length")]
    ctx, te = _build_ctx({"prompt": "agent", "toolset": "none"}, responses,
                         tools=[])

    with patch("core.chat.history.count_tokens", return_value=10):
        result = ctx.run("write an essay")

    assert te.call_llm_with_metrics.call_count == 1, (
        "No-tools length truncation is a legitimate long answer — no retry."
    )
    assert result == "A very long essay tha"
    assert ctx.degraded_reason is None
