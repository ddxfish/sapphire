"""A task's turn in a chat: its assistant rows carry the metrics block the web
lane stamps (the ui.js footer), and the turn fits under the chat's own name so
the context bar shows it. Before 2026-11-01 both were blank for continuity
triggers, which is how a 14 s prompt re-read went unseen."""
from unittest.mock import patch

import pytest

from core.chat import budget
from test_execctx_retry import _build_ctx, _text_response


@pytest.fixture(autouse=True)
def clean():
    budget.forget()
    budget._ratio.clear()
    yield
    budget.forget()
    budget._ratio.clear()


def _measured(text):
    r = _text_response(text, finish="stop")
    r.usage = {"prompt_tokens": 400, "completion_tokens": 25, "total_tokens": 425}
    r.thinking = None
    r.content_is_reasoning = False
    return r


def test_a_tasks_assistant_row_carries_the_metrics_block():
    ctx, te = _build_ctx({"prompt": "sapph", "toolset": "none"}, [_measured("Hello there.")], tools=[])
    with patch("core.chat.history.count_tokens", return_value=10):
        assert ctx.run("hi") == "Hello there."
    row = ctx.new_messages[-1]
    meta = row.get("metadata")
    assert row["role"] == "assistant" and meta, row
    assert meta["provider"] == "k"
    assert meta["tokens"]["content"] == 25 and meta["tokens"]["prompt"] == 400
    assert meta["tokens"]["estimated"] is False and meta["finish"] == "stop"
    assert "duration_seconds" in meta and "tokens_per_second" in meta


def test_a_stub_response_leaves_the_row_as_it_was():
    """No real usage to count (a double, a provider with no report): no
    metadata key at all, never `metadata: None` in history."""
    ctx, te = _build_ctx({"prompt": "sapph", "toolset": "none"}, [_text_response("ok")], tools=[])
    with patch("core.chat.history.count_tokens", return_value=10):
        ctx.run("hi")
    assert "metadata" not in ctx.new_messages[-1]


def test_a_task_in_a_chat_fits_under_the_chats_own_name():
    ctx, te = _build_ctx({"prompt": "sapph", "toolset": "none", "chat_target": "trinity"},
                         [_measured("Hello.")], tools=[])
    history = [{"role": "user", "content": "earlier"}, {"role": "assistant", "content": "yes"}]
    with patch("core.chat.history.count_tokens", return_value=10):
        ctx.run("hi", history_messages=history)
    assert budget.meter("trinity", 0)["measured"] is True
    assert budget.meter("task:trinity", 0)["measured"] is False


def test_the_persistence_path_hands_the_run_its_chats_name():
    """The executor stamps the RESOLVED chat name into the task settings before
    building the context: without it the budgeter fit with no key (a fresh
    cut every round) and the context bar never saw a task's turn."""
    import inspect
    from core.continuity.executor import ContinuityExecutor
    src = inspect.getsource(ContinuityExecutor._run_foreground_held)
    assert 'task_settings["chat_target"] = target_chat' in src
    assert src.index('task_settings["chat_target"] = target_chat') < src.index('ExecutionContext(')
