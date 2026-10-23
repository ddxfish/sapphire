"""Regression tests for the 2026-05-10 heartbeat loop-cap bug.

Reported chain: a heartbeat task created without an explicit `max_tool_rounds`
ran for exactly one iteration. The LLM used its single round on a tool call
(get_time), the loop exhausted with `final_content=""` and a silent
`degraded_reason`, and the user saw an empty bubble with no progress.

Root cause: `core/continuity/scheduler.py:321` defaults `max_tool_rounds` to
0 when missing — the scheduler's "unset" sentinel. `ExecutionContext` then
read that 0 and computed `max(1, 0) = 1`, capping the loop to a single round.

Same pattern hit `max_parallel_tools` (also defaults to 0 in the scheduler).

Cascading bug: when the loop exhausted, `_patch_dangling_tool_calls` wrote
tool placeholders missing the `name` field. Persisted to disk. The next read
of that chat hit `msg["name"]` in `get_messages_for_llm`, threw KeyError,
returned `[]` from `read_chat_messages` — silent empty history poisoned
every subsequent run of the same heartbeat.

Both fixes covered here.
"""
from unittest.mock import MagicMock, patch

import pytest

import config


# ─────────────────────────────────────────────────────────────────────────────
# 1. max_tool_rounds / max_parallel_tools: 0 must mean "use default"
# ─────────────────────────────────────────────────────────────────────────────


def _build_ctx(task_settings, llm_responses, *, allowed_tools=None):
    """Construct an ExecutionContext with all I/O patched out.

    `llm_responses` is a list of MagicMock response objects to return on
    successive call_llm_with_metrics() calls. We patch _build_* methods
    before instantiation so the constructor doesn't reach for real providers
    or scope state.
    """
    from core.continuity.execution_context import ExecutionContext

    fm = MagicMock()
    fm.all_possible_tools = []
    fm._apply_mode_filter = lambda x: x

    te = MagicMock()
    te.call_llm_with_metrics.side_effect = list(llm_responses)
    te.execute_tool_calls.return_value = (1, [])  # 1 tool ran successfully
    te.extract_function_call_from_text.return_value = None

    with patch.object(ExecutionContext, "_build_prompt", return_value="sys"), \
         patch.object(ExecutionContext, "_resolve_provider",
                      return_value=("k", MagicMock(), "")), \
         patch.object(ExecutionContext, "_build_gen_params", return_value={}), \
         patch.object(ExecutionContext, "_resolve_tools",
                      return_value=[{"function": {"name": "get_time"}}]), \
         patch.object(ExecutionContext, "_build_scopes", return_value={}):
        ctx = ExecutionContext(fm, te, task_settings)
        ctx._allowed_tool_names = allowed_tools or {"get_time"}
    return ctx, te


def _tool_call_response(call_id="tc1", name="get_time"):
    r = MagicMock()
    r.has_tool_calls = True
    r.get_tool_calls_as_dicts.return_value = [
        {"id": call_id, "function": {"name": name, "arguments": "{}"}}
    ]
    r.content = ""
    return r


def _text_response(text="The time is 13:45."):
    r = MagicMock()
    r.has_tool_calls = False
    r.content = text
    return r


def test_max_tool_rounds_zero_falls_through_to_config_default():
    """Reproduces the actual heartbeat bug: max_tool_rounds=0 (the scheduler's
    'unset' sentinel) must not cap the loop to 1 round.

    Pre-fix: `max(1, 0)` = 1 — LLM called once, loop exhausts after the
    first tool call, user sees an empty bubble.
    Post-fix: `0 or config.MAX_TOOL_ITERATIONS` falls through to the real
    default (7), giving the LLM enough rounds to finish its work.
    """
    responses = [
        _tool_call_response(call_id="tc1"),
        _text_response("The time is 13:45."),
    ]
    ctx, te = _build_ctx(
        {"prompt": "agent", "toolset": "all", "max_tool_rounds": 0},
        responses,
    )

    with patch("core.chat.history.count_tokens", return_value=10):
        result = ctx.run("check the time")

    assert te.call_llm_with_metrics.call_count == 2, (
        f"Heartbeat with max_tool_rounds=0 ran the LLM "
        f"{te.call_llm_with_metrics.call_count}x — expected 2 (tool round + "
        f"final response). 0 is the scheduler's 'unset' sentinel and must "
        f"fall through to config.MAX_TOOL_ITERATIONS, not coerce to 1."
    )
    assert result == "The time is 13:45.", (
        f"Expected the LLM's second-round text response; got {result!r}. "
        f"Loop probably exhausted on round 1."
    )
    assert ctx.degraded_reason is None, (
        f"Clean tool→text run set degraded_reason={ctx.degraded_reason!r}; "
        f"this should resolve as a clean run."
    )


def test_max_tool_rounds_missing_uses_config_default():
    """Same shape as above but the task dict literally omits the key
    (older tasks created before the field existed). Must also fall through.
    """
    responses = [
        _tool_call_response(call_id="tc1"),
        _text_response("ok"),
    ]
    ctx, te = _build_ctx(
        {"prompt": "agent", "toolset": "all"},  # no max_tool_rounds key
        responses,
    )

    with patch("core.chat.history.count_tokens", return_value=10):
        ctx.run("hi")

    assert te.call_llm_with_metrics.call_count == 2


def test_max_tool_rounds_explicit_value_honored():
    """An explicit positive value still wins. Loop must terminate at the
    configured cap, not run forever.
    """
    # 3 rounds requested, but we feed it 5 tool-call responses. Loop should
    # call LLM exactly 3 times and stop with degraded_reason set.
    responses = [_tool_call_response(call_id=f"tc{i}") for i in range(5)]
    ctx, te = _build_ctx(
        {"prompt": "agent", "toolset": "all", "max_tool_rounds": 3},
        responses,
    )

    with patch("core.chat.history.count_tokens", return_value=10):
        result = ctx.run("loop forever")

    assert te.call_llm_with_metrics.call_count == 3, (
        f"max_tool_rounds=3 should bound the loop to exactly 3 LLM calls, "
        f"got {te.call_llm_with_metrics.call_count}."
    )
    assert result == "", "Exhaustion must produce empty final_content."
    assert ctx.degraded_reason is not None, (
        "Exhausted run must set degraded_reason for the UI's amber pill."
    )


def test_max_tool_rounds_one_still_works_when_explicit():
    """If a user explicitly chooses 1 tool round (a real, valid choice for
    a 'fire-and-forget' task), they should get exactly 1 LLM call. The
    'falsy means default' fix must not break this case.
    """
    responses = [_tool_call_response(call_id="tc1")]
    ctx, te = _build_ctx(
        {"prompt": "agent", "toolset": "all", "max_tool_rounds": 1},
        responses,
    )

    with patch("core.chat.history.count_tokens", return_value=10):
        ctx.run("one shot")

    assert te.call_llm_with_metrics.call_count == 1


def test_max_parallel_tools_zero_falls_through_to_config_default():
    """Same fix applied to max_parallel_tools — scheduler also stores 0 as
    the unset sentinel here. Pre-fix, an LLM that requested 3 parallel tool
    calls would only get 1 executed (sliced [:1]), the other 2 would be
    dangling tool_calls — corrupting history.
    """
    # LLM requests 3 parallel tool calls
    r = MagicMock()
    r.has_tool_calls = True
    r.get_tool_calls_as_dicts.return_value = [
        {"id": f"tc{i}", "function": {"name": "get_time", "arguments": "{}"}}
        for i in range(3)
    ]
    r.content = ""
    responses = [r, _text_response("done")]

    ctx, te = _build_ctx(
        {"prompt": "agent", "toolset": "all",
         "max_parallel_tools": 0, "max_tool_rounds": 5},
        responses,
    )

    with patch("core.chat.history.count_tokens", return_value=10):
        ctx.run("burst")

    # The first call to execute_tool_calls should have received all 3 tool
    # calls (sliced to max_parallel = config default, which is >= 3).
    first_call = te.execute_tool_calls.call_args_list[0]
    tool_calls_passed = first_call.args[0]
    assert len(tool_calls_passed) == 3, (
        f"max_parallel_tools=0 should fall through to config default "
        f"(MAX_PARALLEL_TOOLS={config.MAX_PARALLEL_TOOLS}), allowing all 3 "
        f"requested tools through. Got {len(tool_calls_passed)} — likely "
        f"capped at 1 by the old `max(1, 0)` coercion."
    )


def test_context_limit_zero_still_means_unlimited():
    """Regression guard: `context_limit = 0` historically means 'no limit'
    (per the comment that's been there since the rewrite). The fix to
    rounds/parallel must not change context_limit's semantics.
    """
    from core.continuity.execution_context import ExecutionContext

    fm = MagicMock()
    fm.all_possible_tools = []
    fm._apply_mode_filter = lambda x: x

    te = MagicMock()
    te.call_llm_with_metrics.return_value = _text_response("ok")
    te.extract_function_call_from_text.return_value = None

    task_settings = {"prompt": "agent", "toolset": "all", "context_limit": 0}

    with patch.object(ExecutionContext, "_build_prompt", return_value="sys"), \
         patch.object(ExecutionContext, "_resolve_provider",
                      return_value=("k", MagicMock(), "")), \
         patch.object(ExecutionContext, "_build_gen_params", return_value={}), \
         patch.object(ExecutionContext, "_resolve_tools",
                      return_value=[{"function": {"name": "x"}}]), \
         patch.object(ExecutionContext, "_build_scopes", return_value={}):
        ctx = ExecutionContext(fm, te, task_settings)
        ctx._allowed_tool_names = {"x"}
        # Astronomical token count — if 0 weren't honored as "unlimited" the
        # loop would never call the LLM.
        with patch("core.chat.history.count_tokens", return_value=10**9):
            result = ctx.run("hi")

    assert te.call_llm_with_metrics.call_count == 1, (
        "context_limit=0 must mean 'no limit' — the LLM should still get "
        "called even with absurd token counts."
    )
    assert result == "ok"


# ─────────────────────────────────────────────────────────────────────────────
# 2. History reader tolerance: tool messages missing `name` must not crash
# ─────────────────────────────────────────────────────────────────────────────


def test_history_get_messages_for_llm_tolerates_tool_missing_name():
    """`_patch_dangling_tool_calls` historically wrote tool placeholders
    without a `name` field. `get_messages_for_llm` did `msg["name"]` →
    KeyError → caught at the outer except in `read_chat_messages` →
    returned [].

    Effect: every read of any chat that had ever exhausted a tool loop
    returned no history at all. Heartbeats running on those chats kept
    starting from scratch and re-corrupting the file. Self-perpetuating.
    """
    from core.chat.history import ConversationHistory

    chat = ConversationHistory()
    chat.messages = [
        {"role": "user", "content": "what time is it?"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "call_1",
                "type": "function",
                "function": {"name": "get_time", "arguments": "{}"},
            }],
        },
        # The corrupted placeholder — no `name` field
        {
            "role": "tool",
            "tool_call_id": "call_1",
            "content": "(Tool did not execute — loop exhausted before resolution.)",
        },
        {"role": "assistant", "content": ""},
    ]

    msgs = chat.get_messages_for_llm()  # must not raise

    tool_msg = next(m for m in msgs if m["role"] == "tool")
    assert "name" in tool_msg, (
        "Reader must synthesize a `name` field for tool messages missing "
        "it — downstream LLM provider adapters expect the key to exist."
    )
    assert tool_msg["name"], "Synthesized `name` must be truthy."
    assert tool_msg["tool_call_id"] == "call_1"


def test_history_read_does_not_silently_return_empty_for_legacy_corruption():
    """Direct end-to-end shape check: a chat with a legacy bad-tool-message
    in storage must read back a non-empty message list. (Pre-fix this came
    back as []  — the heartbeat then ran with `history=0 msgs` forever.)
    """
    from core.chat.history import ConversationHistory

    chat = ConversationHistory()
    chat.messages = [
        {"role": "user", "content": "ping"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "x",
                "type": "function",
                "function": {"name": "f", "arguments": "{}"},
            }],
        },
        {"role": "tool", "tool_call_id": "x", "content": "(orphan)"},
    ]

    msgs = chat.get_messages_for_llm()
    assert len(msgs) >= 2, (
        f"Got {len(msgs)} messages — pre-fix this returned 0 because the "
        f"missing-`name` KeyError aborted the whole iteration."
    )


# ─────────────────────────────────────────────────────────────────────────────
# 3. Forward-write fix: the placeholder dict now includes `name`
# ─────────────────────────────────────────────────────────────────────────────


def test_patch_dangling_tool_calls_writes_name_field():
    """`_patch_dangling_tool_calls` is a closure inside `_run_inner`, so we
    can't import it directly. Instead drive the loop into the path where it
    fires (assistant requested tool calls, none of the tool responses are
    present) and inspect the patched messages via ctx.new_messages.
    """
    # Build a single tool-call response that the loop will execute, then
    # cap rounds to 1 so the loop exhausts cleanly with the asst(tc) but
    # the patched placeholder will only fire when no tool response is
    # present. To produce that state, force tools_executed=0 — the loop
    # then breaks with degraded_reason and _patch fires before return.
    r = MagicMock()
    r.has_tool_calls = True
    r.get_tool_calls_as_dicts.return_value = [
        {"id": "call_zzz", "function": {"name": "search_web", "arguments": "{}"}}
    ]
    r.content = ""

    from core.continuity.execution_context import ExecutionContext

    fm = MagicMock()
    fm.all_possible_tools = []
    fm._apply_mode_filter = lambda x: x

    te = MagicMock()
    te.call_llm_with_metrics.return_value = r
    te.execute_tool_calls.return_value = (0, [])  # nothing executed → break path

    with patch.object(ExecutionContext, "_build_prompt", return_value="sys"), \
         patch.object(ExecutionContext, "_resolve_provider",
                      return_value=("k", MagicMock(), "")), \
         patch.object(ExecutionContext, "_build_gen_params", return_value={}), \
         patch.object(ExecutionContext, "_resolve_tools",
                      return_value=[{"function": {"name": "search_web"}}]), \
         patch.object(ExecutionContext, "_build_scopes", return_value={}):
        ctx = ExecutionContext(
            fm, te,
            {"prompt": "agent", "toolset": "all", "max_tool_rounds": 2},
        )
        ctx._allowed_tool_names = {"search_web"}
        with patch("core.chat.history.count_tokens", return_value=10):
            ctx.run("research the time")

    # ctx.new_messages should now include a tool placeholder with the right
    # call id AND a name (so persisted history doesn't re-corrupt anyone's
    # next read).
    placeholders = [
        m for m in ctx.new_messages
        if m.get("role") == "tool" and m.get("tool_call_id") == "call_zzz"
    ]
    assert placeholders, (
        "Expected a tool placeholder for the dangling call_zzz — none found."
    )
    placeholder = placeholders[0]
    assert "name" in placeholder, (
        "Placeholder must carry a `name` field so persisted history is "
        "well-formed for any reader that expects it."
    )
    assert placeholder["name"] == "search_web", (
        f"Placeholder `name` should mirror the original tool call's function "
        f"name (search_web), got {placeholder['name']!r}."
    )


# ─────────────────────────────────────────────────────────────────────────────
# 4. Scheduler default sanity check (locks the contract this fix relies on)
# ─────────────────────────────────────────────────────────────────────────────


def test_scheduler_default_max_tool_rounds_is_zero_sentinel():
    """If someone changes scheduler.py:321 to default to None instead of 0
    in the future, this test reminds them that ExecutionContext relies on
    `0` being the 'unset' sentinel from this writer (the `or` chain handles
    both 0 and None). Either default is fine — but they have to stay in sync.
    """
    from core.continuity import scheduler as sched_mod
    import inspect
    src = inspect.getsource(sched_mod.ContinuityScheduler.create_task)
    # Light contract check — both 0 and None defaults are acceptable as
    # long as the executor's `or` chain (which falls through both) stays.
    assert ('"max_tool_rounds": data.get("max_tool_rounds", 0)' in src or
            '"max_tool_rounds": data.get("max_tool_rounds", None)' in src), (
        "scheduler.create_task no longer defaults max_tool_rounds to 0 or "
        "None — verify ExecutionContext still treats the new default as "
        "'use config.MAX_TOOL_ITERATIONS'."
    )


# ─────────────────────────────────────────────────────────────────────────────
# 4. Context-trim must not corrupt the persisted slice (2026-06-13)
#    The in-loop context trim deletes front messages but historically did NOT
#    adjust msg_start_idx, so new_messages (= messages[msg_start_idx:]) went
#    stale and dropped the user turn (and assistant). Fixed by anchoring the
#    user message by object identity. These tests drive a real trim.
# ─────────────────────────────────────────────────────────────────────────────


def test_context_trim_preserves_user_turn_in_new_messages():
    """After the in-loop trim deletes oldest messages, the persisted slice must
    still START at the user turn. Pre-fix, msg_start_idx was stale and the slice
    came back empty (turn silently lost) or corrupt."""
    ctx, te = _build_ctx(
        {"max_tool_rounds": 3, "context_limit": 1300},
        [_text_response("Final answer.")],
    )
    # 10 prior messages → non_system=11 (>4, trim has material). count_tokens=100
    # each: pre-trim 12 msgs + 1 schema = 1300 > 0.9*1300=1170 → trim drops 2 →
    # 1100 < 1170 → loop proceeds to the LLM and gets a real assistant reply.
    history = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"msg {i}"}
               for i in range(10)]
    with patch("core.chat.history.count_tokens", return_value=100):
        ctx.run("look at the latest", history_messages=history)
    new = ctx.new_messages
    assert new, "new_messages empty after trim — user turn was silently dropped"
    assert new[0]["role"] == "user", f"slice must start at the user turn, got {new[0]['role']}"
    assert "look at the latest" in str(new[0]["content"]), "user content lost in the slice"
    assert any(m["role"] == "assistant" for m in new), "assistant reply missing from slice"


def test_no_trim_unaffected_baseline():
    """Sanity: with tokens well under the limit (no trim), the slice is correct.
    Guards against the anchor fix breaking the common no-trim path."""
    ctx, te = _build_ctx(
        {"max_tool_rounds": 3, "context_limit": 100000},
        [_text_response("Hi back.")],
    )
    history = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"msg {i}"}
               for i in range(4)]
    with patch("core.chat.history.count_tokens", return_value=10):
        ctx.run("hello", history_messages=history)
    new = ctx.new_messages
    assert new and new[0]["role"] == "user" and "hello" in str(new[0]["content"])
    assert any(m["role"] == "assistant" for m in new)


def test_inline_tool_image_base64_scrubbed_from_new_messages():
    """Tool-returned images are injected as base64 for the LLM THIS turn, but must
    NOT persist inline (replay bloat). new_messages must carry no type:image block
    — the image lives in the DB behind its tool-result marker. The injected text
    note survives. 2026-06-13."""
    ctx, te = _build_ctx(
        {"max_tool_rounds": 3, "context_limit": 100000},
        [_tool_call_response(), _text_response("Looked at it.")],
    )
    # Tool returns an image → _inject_tool_images appends a base64 user message.
    te.execute_tool_calls.return_value = (1, [{"data": "aGVsbG8=", "media_type": "image/png"}])
    with patch("core.chat.history.count_tokens", return_value=10):
        ctx.run("see this", history_messages=[{"role": "user", "content": "prior"}])
    new = ctx.new_messages
    assert new, "new_messages should not be empty"
    for m in new:
        content = m.get("content")
        if isinstance(content, list):
            assert not any(isinstance(b, dict) and b.get("type") == "image" for b in content), \
                f"inline base64 image leaked into persisted {m.get('role')} message"
    assert any("shown to the user" in str(m.get("content", "")) for m in new), \
        "injected image note should survive the scrub as text"


def test_trim_never_deletes_current_user_turn_short_history():
    """When history is short/empty, the trim's front-delete range can reach the
    CURRENT turn's user message. Pre-fix, deleting it triggered the anchor
    fallback and the user turn vanished from persisted history (#4 reopened).
    The trim must structurally protect the live user_msg. 2026-06-13.

    Empty history → user_msg sits right after system. count_tokens=100/msg,
    0.9*800=720: tokens stay under until ~6 msgs accumulate across tool rounds,
    then the trim fires with drop=1 → del messages[1:2] which (pre-fix) deleted
    the user_msg at index 1."""
    ctx, te = _build_ctx(
        {"max_tool_rounds": 10, "context_limit": 800},
        [_tool_call_response() for _ in range(5)] + [_text_response("done")],
    )
    with patch("core.chat.history.count_tokens", return_value=100):
        ctx.run("LOOK AT THIS SPECIFIC THING", history_messages=[])
    new = ctx.new_messages
    assert new, "new_messages empty — current turn lost entirely"
    assert any("LOOK AT THIS SPECIFIC THING" in str(m.get("content", "")) for m in new), \
        "the current user turn was deleted by the trim and lost from persisted history"


# ─────────────────────────────────────────────────────────────────────────────
# 5. _scrub_inline_images unit coverage (the chaos-scout edge shapes, 2026-06-13)
# ─────────────────────────────────────────────────────────────────────────────


def test_scrub_unit_text_plus_image_collapses_to_string():
    from core.continuity.execution_context import _scrub_inline_images
    img = {"type": "image", "data": "x", "media_type": "image/png"}
    out = _scrub_inline_images({"role": "user", "content": [{"type": "text", "text": "hi"}, img]})
    assert out == {"role": "user", "content": "hi"}


def test_scrub_unit_preserves_tool_result_drops_image():
    from core.continuity.execution_context import _scrub_inline_images
    img = {"type": "image", "data": "x", "media_type": "image/png"}
    tr = {"type": "tool_result", "tool_use_id": "t1", "content": "ok"}
    out = _scrub_inline_images({"role": "user", "content": [tr, img]})
    assert out == {"role": "user", "content": [tr]}  # tool_result kept, image gone


def test_scrub_unit_all_image_becomes_placeholder_not_empty():
    from core.continuity.execution_context import _scrub_inline_images
    img = {"type": "image", "data": "x", "media_type": "image/png"}
    out = _scrub_inline_images({"role": "user", "content": [img]})
    assert out["content"] == "[image]"  # never an empty-string user bubble


def test_scrub_unit_non_string_text_does_not_crash():
    from core.continuity.execution_context import _scrub_inline_images
    img = {"type": "image", "data": "x", "media_type": "image/png"}
    out = _scrub_inline_images({"role": "user", "content": [{"type": "text", "text": None}, img]})
    assert isinstance(out["content"], str)  # coerced, no TypeError


def test_scrub_unit_leaves_non_user_and_string_untouched():
    from core.continuity.execution_context import _scrub_inline_images
    img = {"type": "image", "data": "x", "media_type": "image/png"}
    asst = {"role": "assistant", "content": [img]}
    assert _scrub_inline_images(asst) is asst              # non-user untouched
    s = {"role": "user", "content": "plain text"}
    assert _scrub_inline_images(s) is s                    # string content untouched


def test_scrub_unit_is_pure_does_not_mutate_input():
    from core.continuity.execution_context import _scrub_inline_images
    img = {"type": "image", "data": "x", "media_type": "image/png"}
    orig_content = [{"type": "text", "text": "hi"}, img]
    orig = {"role": "user", "content": orig_content}
    _scrub_inline_images(orig)
    assert orig["content"] == [{"type": "text", "text": "hi"}, img]  # input intact
