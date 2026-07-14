"""Loop-guard tests (2026-06-14).

Covers the per-tool, per-turn loop guard: a tool schema flag `loop_warn_after: N`
(+ optional `loop_warn_message`) makes the chat loop append a warning to the
tool-result text the LLM reads (NOT history) once the tool is called >= N times in
one turn. Stops image-gen spirals on impossible prompts.

Scout-prioritised invariants: (1) the flag is stripped before the provider wire,
(2) the warning reaches the LLM message but never history, (3) it fires exactly at
threshold, (4) a malformed flag never breaks registration, (5) the side-map is
populated on register and cleared on unregister.
"""
import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.chat.function_manager import FunctionManager
from core.chat.chat_tool_calling import ToolCallingEngine
from core.chat.llm_providers.base import BaseProvider, LLMResponse
from core.chat.llm_providers.openai_responses import OpenAIResponsesProvider


def _bare_fm():
    """FunctionManager with no heavy __init__ — just the loop-guard surface."""
    fm = FunctionManager.__new__(FunctionManager)
    fm._loop_warn_map = {}
    fm._settings_gates = {}
    return fm


# ── 1. Wire-strip denylist (the 400-on-unknown-key trap) ─────────────────────

class _StripProvider(BaseProvider):
    def health_check(self): return True
    def chat_completion(self, messages, tools=None, generation_params=None):
        return LLMResponse(content="x")
    def chat_completion_stream(self, messages, tools=None, generation_params=None):
        yield {"type": "done", "response": LLMResponse(content="x")}


def test_base_denylist_strips_loop_warn_fields():
    """openai_compat/gemini path: loop_warn_* must not reach the wire."""
    tools = [{"type": "function", "loop_warn_after": 2, "loop_warn_message": "hi",
              "function": {"name": "gen", "parameters": {}}}]
    out = _StripProvider({"provider": "test"}).convert_tools_for_api(tools)
    assert "loop_warn_after" not in out[0]
    assert "loop_warn_message" not in out[0]
    assert out[0]["function"]["name"] == "gen"


def test_responses_whitelist_drops_loop_warn_fields():
    """openai_responses rebuilds from a whitelist — extra keys vanish for free."""
    prov = OpenAIResponsesProvider.__new__(OpenAIResponsesProvider)
    tools = [{"type": "function", "loop_warn_after": 2, "loop_warn_message": "hi",
              "function": {"name": "gen", "description": "d", "parameters": {}}}]
    out = prov._convert_tools_for_api(tools)
    assert "loop_warn_after" not in out[0] and "loop_warn_message" not in out[0]
    assert out[0]["name"] == "gen"


# ── 2. Defensive flag parse (a bad flag must NOT drop the plugin's tools) ─────

def test_parse_loop_warn_valid():
    t, m = FunctionManager._parse_loop_warn({"loop_warn_after": 3, "loop_warn_message": "stop {count}"})
    assert t == 3 and m == "stop {count}"


def test_parse_loop_warn_default_message():
    t, m = FunctionManager._parse_loop_warn({"loop_warn_after": 2})
    assert t == 2 and "{count}" in m  # default carries the placeholder


@pytest.mark.parametrize("tool", [
    {},                                   # no flag
    {"loop_warn_after": None},            # explicit None
    {"loop_warn_after": "nope"},          # non-int string
    {"loop_warn_after": 0},               # below 1
    {"loop_warn_after": -5},              # negative
])
def test_parse_loop_warn_bad_inputs_return_none(tool):
    assert FunctionManager._parse_loop_warn(tool) is None


def test_parse_loop_warn_nonstring_message_falls_back():
    t, m = FunctionManager._parse_loop_warn({"loop_warn_after": 2, "loop_warn_message": 123})
    assert t == 2 and isinstance(m, str) and m.strip()


# ── 3. Counting + suffix helpers (never raise, exact threshold) ──────────────

def test_bump_loop_count_increments_and_is_none_safe():
    fm = _bare_fm()
    counts = {}
    fm.bump_loop_count(counts, "gen")
    fm.bump_loop_count(counts, "gen")
    assert counts["gen"] == 2
    fm.bump_loop_count(None, "gen")  # must not raise


def test_loop_warn_suffix_fires_at_threshold_not_before():
    fm = _bare_fm()
    fm._loop_warn_map = {"gen": (2, "Called {count} times.")}
    assert fm.loop_warn_suffix("gen", {"gen": 1}) == ""          # below
    suffix = fm.loop_warn_suffix("gen", {"gen": 2})              # at threshold
    assert "Called 2 times." in suffix
    assert fm.loop_warn_suffix("gen", {"gen": 3}).endswith("Called 3 times.")  # above, count updates


def test_loop_warn_suffix_safe_paths():
    fm = _bare_fm()
    fm._loop_warn_map = {"gen": (2, "warn")}
    assert fm.loop_warn_suffix("gen", None) == ""        # no counts dict
    assert fm.loop_warn_suffix("other", {"other": 9}) == ""  # no flag for this tool
    # literal braces in message must NOT raise (replace, not str.format)
    fm._loop_warn_map = {"g": (1, "use {x} and {} stuff {count}")}
    assert "use {x} and {} stuff 1" in fm.loop_warn_suffix("g", {"g": 1})


# ── 4. Register populates the side-map, unregister clears it ──────────────────

def test_register_and_unregister_loop_warn_map():
    with patch.object(FunctionManager, '__init__', lambda self: None):
        fm = FunctionManager()
        fm._tools_lock = threading.Lock()
        fm.function_modules = {}
        fm.all_possible_tools = []
        fm._enabled_tools = []
        fm.execution_map = {}
        fm._function_module_map = {}
        fm._network_functions = set()
        fm._is_local_map = {}
        fm._mode_filters = {}
        fm._loop_warn_map = {}
        fm._settings_gates = {}
        fm._hidden_tools = set()
        fm.current_toolset_name = "none"

        tool = {"type": "function", "loop_warn_after": 2, "loop_warn_message": "stop",
                "function": {"name": "gen", "parameters": {}}}
        with patch('core.chat.function_manager.toolset_manager') as ts:
            ts.toolset_exists.return_value = False
            fm.register_dynamic_tools("mod:x", [tool], lambda *a, **k: "ok", plugin_name="zp")
        assert fm._loop_warn_map.get("gen") == (2, "stop")

        fm.unregister_plugin_tools("zp")
        assert "gen" not in fm._loop_warn_map  # cleared on teardown


# ── 5. Integration: warn reaches the LLM message, NEVER history ──────────────

def test_warn_appends_to_llm_msg_not_history():
    fm = _bare_fm()
    fm._loop_warn_map = {"gen": (2, "LOOP {count}")}
    fm.execute_function = lambda name, args, **kw: "TOOL OUTPUT"
    engine = ToolCallingEngine(fm)

    history = MagicMock()
    messages = []
    loop_counts = {}
    tc = [{"id": "c1", "type": "function", "function": {"name": "gen", "arguments": "{}"}}]

    # 1st call: under threshold -> no warn anywhere
    engine.execute_tool_calls(tc, messages, history, provider=None, loop_counts=loop_counts)
    assert "LOOP" not in messages[-1]["content"]

    # 2nd call: at threshold -> warn in LLM message, raw in history
    engine.execute_tool_calls(tc, messages, history, provider=None, loop_counts=loop_counts)
    assert "LOOP 2" in messages[-1]["content"]
    assert "TOOL OUTPUT" in messages[-1]["content"]
    # history.add_tool_result(id, name, result_str, ...) — 3rd positional is raw, no warn
    raw_saved = history.add_tool_result.call_args[0][2]
    assert raw_saved == "TOOL OUTPUT"
    assert "LOOP" not in raw_saved


def test_error_path_warn_reaches_llm_but_not_history():
    """Regression: on the JSON-fail error path the warn must go to the LLM message
    only — NOT history (else it persists + replays). Bug found 2026-06-14 hunt."""
    fm = _bare_fm()
    fm._loop_warn_map = {"gen": (1, "WARN {count}")}  # threshold 1 -> fires immediately
    fm.execute_function = lambda *a, **k: "unused"
    engine = ToolCallingEngine(fm)

    history = MagicMock()
    messages = []
    bad = [{"id": "c1", "type": "function", "function": {"name": "gen", "arguments": "{not valid json"}}]
    engine.execute_tool_calls(bad, messages, history, provider=None, loop_counts={})

    assert "WARN 1" in messages[-1]["content"]          # LLM sees the warn
    raw = history.add_tool_result.call_args[0][2]        # history gets raw error only
    assert "WARN" not in raw and "Invalid JSON" in raw


# ── 6. Trigger version-skew: degrade instead of crash ─────────────────────────

def test_exec_with_loop_counts_passes_through_when_engine_accepts_it():
    from core.continuity.execution_context import _exec_with_loop_counts
    def good(a, b, loop_counts=None, scopes=None):
        return ("ran", loop_counts, scopes)
    assert _exec_with_loop_counts(good, 1, 2, loop_counts={"gen": 1}, scopes="s") == \
        ("ran", {"gen": 1}, "s")


def test_exec_with_loop_counts_degrades_on_old_engine_no_double_exec():
    """Version skew: an engine too old to accept loop_counts raises TypeError at
    BIND time (before the body), so we retry without it — once, no double-exec."""
    import core.continuity.execution_context as ec
    ec._loop_guard_skew_warned = False
    runs = []
    def old(a, b, scopes=None):          # no loop_counts, no **kwargs -> real bind-time TypeError
        runs.append((a, b, scopes))
        return ("ran", None)
    out = ec._exec_with_loop_counts(old, 1, 2, loop_counts={}, scopes="s")
    assert out == ("ran", None)
    assert runs == [(1, 2, "s")], "tool body runs exactly once (the retry), loop_counts dropped"
    assert ec._loop_guard_skew_warned is True   # the degradation was logged


def test_exec_with_loop_counts_reraises_unrelated_typeerror():
    """A TypeError that ISN'T about loop_counts is a real bug — never masked."""
    from core.continuity.execution_context import _exec_with_loop_counts
    def buggy(a, loop_counts=None):
        raise TypeError("something else entirely")
    with pytest.raises(TypeError, match="something else"):
        _exec_with_loop_counts(buggy, 1, loop_counts={})
