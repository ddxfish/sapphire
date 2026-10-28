"""Gemini provider rebuilt 2026-10-27 on live probes against Google's OpenAI-compat
endpoint. Every fixture here is a shape Google actually sent or refused:

- `google` at the top of the body → 'Unknown name "google"' (the user report).
- reasoning_effort + any thinking_config → 'found both'.
- thinking_level a model lacks → 'Thinking level MINIMAL is not supported'.
- thoughts come inline as <thought>…</thought>, the closer sharing a chunk
  with the answer.
- parallel tool calls stream with NO index; only the first carries a
  thought_signature; replaying without it 400s, Google's skip value passes.
- completion_tokens leaves thinking out; total_tokens doesn't.
"""
from unittest.mock import MagicMock

import pytest
from openai._models import construct_type
from openai.types.chat import ChatCompletion, ChatCompletionChunk

from core.chat.llm_providers import gemini as gm
from core.chat.llm_providers.base import ToolCall
from core.chat.llm_providers.gemini import GeminiProvider, SKIP_SIGNATURE
from core.chat.llm_providers.openai_compat import OpenAICompatProvider


@pytest.fixture(autouse=True)
def _fresh_memory():
    from core.chat.llm_providers import openai_compat as oc
    oc._REJECTED_PARAMS.clear()
    gm._REFUSED_LEVELS.clear()
    yield
    oc._REJECTED_PARAMS.clear()
    gm._REFUSED_LEVELS.clear()


def _gemini(model="gemini-3.8-flash", **cfg):
    return GeminiProvider({"provider": "gemini", "api_key": "k", "model": model,
                           "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
                           "thinking_enabled": True, "reasoning_effort": "medium", **cfg})


def _chunk(delta=None, usage=None):
    v = {"id": "x", "object": "chat.completion.chunk", "created": 0, "model": "gemini", "choices": []}
    if delta is not None:
        v["choices"] = [{"index": 0, "delta": {"role": "assistant", **delta}, "finish_reason": None}]
    if usage:
        v["usage"] = usage
    return construct_type(type_=ChatCompletionChunk, value=v)


def _wired(p, *returns):
    p._client = MagicMock()
    p._client.chat.completions.create = MagicMock(side_effect=list(returns))
    return p._client.chat.completions.create


class _GoogleError(Exception):
    def __init__(self, msg):
        super().__init__(msg)
        self.body = msg


def _google(kw):
    return kw["extra_body"]["extra_body"]["google"]


# ── the request body ────────────────────────────────────────────────────────

def test_report_shape_never_returns():
    """The user report: `google` flattened to the top of the JSON body."""
    out = _gemini()._transform_params_for_model({"temperature": 0.7})
    assert "google" not in out and "google" not in out.get("extra_body", {})
    assert _google(out) == {"thinking_config": {"thinking_level": "medium", "include_thoughts": True}}


@pytest.mark.parametrize("model", ["gemini-3.8-flash", "gemini-2.5-flash", "gemini-4-argon"])
def test_never_both_knobs(model):
    """reasoning_effort + any thinking_config is a 400 ('found both')."""
    out = _gemini(model)._transform_params_for_model({"reasoning_effort": "high", "temperature": 0.5})
    assert not ("reasoning_effort" in out and "extra_body" in out)


def test_25_takes_a_budget_not_a_level():
    out = _gemini("gemini-2.5-flash", reasoning_effort="high")._transform_params_for_model({})
    assert _google(out) == {"thinking_config": {"thinking_budget": 24576, "include_thoughts": True}}


def test_version_parse_not_a_prefix_list():
    out = _gemini("gemini-4-argon")._transform_params_for_model({})
    assert _google(out)["thinking_config"]["thinking_level"] == "medium"


@pytest.mark.parametrize("model", ["gemini-2.0-flash", "gemma-3-27b-it", ""])
def test_no_thinking_params_where_none_exist(model):
    assert _gemini(model)._transform_params_for_model({"temperature": 0.5}) == {"temperature": 0.5}


def test_thinking_off_sends_nothing_and_unset_means_on():
    assert _gemini(thinking_enabled=False)._transform_params_for_model({}) == {}
    out = _gemini(thinking_enabled=None)._transform_params_for_model({})
    assert _google(out)["thinking_config"]["include_thoughts"] is True


def test_disable_25_flash_really_stops():
    out = _gemini("gemini-2.5-flash", disable_thinking=True)._transform_params_for_model({})
    assert out == {"reasoning_effort": "none"}


def test_disable_3x_thinks_least_with_thoughts_hidden():
    out = _gemini()._transform_params_for_model({"disable_thinking": True})
    assert _google(out) == {"thinking_config": {"thinking_level": "minimal"}}


def test_disable_25_pro_cannot_stop():
    out = _gemini("gemini-2.5-pro", disable_thinking=True)._transform_params_for_model({})
    assert _google(out) == {"thinking_config": {"thinking_budget": 1024}}
    assert "reasoning_effort" not in out


def test_family_dispatch_stays_out_of_gemini():
    p = _gemini(disable_thinking=True)
    kw = {"model": p.model}
    p._inject_thinking_control(kw, want_disable=True)
    assert kw == {"model": p.model}


def test_wire_body_end_to_end():
    p = _gemini()
    create = _wired(p, iter([_chunk({"content": "OK"})]))
    list(p.chat_completion_stream([{"role": "user", "content": "hi"}],
                                  generation_params={"temperature": 0.7, "top_k": 5}))
    kw = create.call_args.kwargs
    assert "reasoning_effort" not in kw and "google" not in kw
    assert kw["extra_body"] == {"extra_body": {"google": {
        "thinking_config": {"thinking_level": "medium", "include_thoughts": True}}}}


# ── a level the model lacks ─────────────────────────────────────────────────

def test_refused_level_is_learned_stepped_up_and_remembered():
    p = _gemini(disable_thinking=True)                    # asks for minimal; 3.8 has none
    create = _wired(p, _GoogleError("Thinking level MINIMAL is not supported for this model. "
                                    "Please retry with other thinking level."), iter([]))
    list(p.chat_completion_stream([{"role": "user", "content": "hi"}]))
    assert create.call_count == 2
    assert _google(create.call_args.kwargs)["thinking_config"]["thinking_level"] == "low"

    q = _gemini(disable_thinking=True)                    # next turn's fresh provider
    out = q._transform_params_for_model({})
    assert _google(out)["thinking_config"]["thinking_level"] == "low"


def test_other_400s_are_not_retried():
    p = _gemini()
    create = _wired(p, _GoogleError("Expected one of either `reasoning_effort` or custom "
                                    "`thinking_config`; found both."))
    with pytest.raises(_GoogleError):
        list(p.chat_completion_stream([{"role": "user", "content": "hi"}]))
    assert create.call_count == 1


# ── thoughts in the stream ──────────────────────────────────────────────────

THOUGHT_STREAM = [   # 3.5-flash-lite, level=low, include_thoughts (probe, trimmed)
    {"content": "<thought>**Assessing Primality**\n\nI'm checking factors of 91.",
     "extra_content": {"google": {"thought": True}}},
    {"content": "</thought>Step 1: Check divisors up to 9.",
     "extra_content": {"google": {"thought": True}}},
    {"content": "\nNo, 91 is not prime (7 x 13)."},
]
THOUGHT_USAGE = {"completion_tokens": 135, "prompt_tokens": 18, "total_tokens": 650}


def _run(p, deltas, usage=None):
    chunks = [_chunk(d) for d in deltas] + ([_chunk(usage=usage)] if usage else [])
    _wired(p, iter(chunks))
    return list(p.chat_completion_stream([{"role": "user", "content": "q"}]))


def test_thoughts_become_thinking_events_and_never_reach_content():
    events = _run(_gemini(), THOUGHT_STREAM, THOUGHT_USAGE)
    thinking = "".join(e["text"] for e in events if e["type"] == "thinking")
    said = "".join(e["text"] for e in events if e["type"] == "content")
    assert thinking == "**Assessing Primality**\n\nI'm checking factors of 91."
    assert said == "Step 1: Check divisors up to 9.\nNo, 91 is not prime (7 x 13)."
    done = events[-1]
    assert done["thinking"] == thinking
    assert done["response"].content == said
    assert "thought>" not in said


def test_tags_split_across_chunks():
    events = _run(_gemini(), [{"content": "  <tho"}, {"content": "ught>abc</tho"},
                              {"content": "ught>Hi"}])
    assert "".join(e["text"] for e in events if e["type"] == "thinking") == "abc"
    assert events[-1]["response"].content == "Hi"


def test_a_thought_tag_after_the_answer_is_a_word():
    events = _run(_gemini(), [{"content": "Use the <thought> tag like this."}])
    assert not [e for e in events if e["type"] == "thinking"]
    assert events[-1]["response"].content == "Use the <thought> tag like this."


def test_thinking_tokens_are_counted():
    usage = _run(_gemini(), THOUGHT_STREAM, THOUGHT_USAGE)[-1]["response"].usage
    assert usage["completion_tokens"] == 650 - 18
    assert usage["reasoning_tokens"] == 650 - 18 - 135
    assert usage["total_tokens"] == 650


# ── thought signatures ──────────────────────────────────────────────────────

SIG = {"google": {"thought_signature": "EtICCs8CAWkUfROJ"}}
PARALLEL = [   # 3.5-flash-lite: no index, signature on the first call only (probe)
    {"id": "call_3158192", "type": "function",
     "function": {"arguments": '{"city":"Paris"}', "name": "get_weather"}, "extra_content": SIG},
    {"id": "call_3158195", "type": "function",
     "function": {"arguments": '{"city":"Rome"}', "name": "get_weather"}},
]


@pytest.mark.parametrize("deltas", [
    [{"tool_calls": PARALLEL}],                                   # one chunk
    [{"tool_calls": [PARALLEL[0]]}, {"tool_calls": [PARALLEL[1]]}],  # one chunk each
], ids=["one-chunk", "two-chunks"])
def test_indexless_parallel_calls_stay_apart_and_keep_signature(deltas):
    events = _run(_gemini(), deltas)
    calls = events[-1]["response"].tool_calls
    assert [(c.id, c.arguments) for c in calls] == [
        ("call_3158192", '{"city":"Paris"}'), ("call_3158195", '{"city":"Rome"}')]
    assert calls[0].extra_content == SIG and calls[1].extra_content is None
    first = [e for e in events if e["type"] == "tool_call" and e["index"] == 0][-1]
    assert first["extra_content"] == SIG


def test_indexless_continuation_fragments_join_the_last_call():
    events = _run(_gemini(), [
        {"tool_calls": [{"id": "c1", "type": "function", "function": {"name": "f", "arguments": '{"a"'}}]},
        {"tool_calls": [{"function": {"arguments": ': 1}'}}]},
    ])
    [call] = events[-1]["response"].tool_calls
    assert call.arguments == '{"a": 1}'


def test_to_dict_carries_extra_content_only_when_set():
    assert ToolCall("c", "f", "{}", SIG).to_dict()["extra_content"] == SIG
    assert "extra_content" not in ToolCall("c", "f", "{}").to_dict()


def _history(first_extra=None):
    tc = [{"id": "call_1", "type": "function", "function": {"name": "get_weather", "arguments": '{"city":"Paris"}'}},
          {"id": "call_2", "type": "function", "function": {"name": "get_weather", "arguments": '{"city":"Rome"}'}}]
    if first_extra:
        tc[0]["extra_content"] = first_extra
    return [{"role": "user", "content": "weather?"},
            {"role": "assistant", "content": "", "tool_calls": tc},
            {"role": "tool", "tool_call_id": "call_1", "name": "get_weather", "content": "18C"},
            {"role": "tool", "tool_call_id": "call_2", "name": "get_weather", "content": "18C"}]


def test_signature_replays_as_it_came():
    calls = _gemini()._sanitize_messages(_history(SIG))[1]["tool_calls"]
    assert calls[0]["extra_content"] == SIG and "extra_content" not in calls[1]


def test_unsigned_calls_get_the_skip_value():
    """History from Claude / a local model / before this fix (probe: passes)."""
    calls = _gemini()._sanitize_messages(_history())[1]["tool_calls"]
    assert calls[0]["extra_content"] == {"google": {"thought_signature": SKIP_SIGNATURE}}


def test_25_gets_no_skip_value():
    calls = _gemini("gemini-2.5-flash")._sanitize_messages(_history())[1]["tool_calls"]
    assert "extra_content" not in calls[0]


def test_other_providers_never_send_extra_content():
    p = OpenAICompatProvider({"provider": "openai", "base_url": "http://localhost:1234/v1",
                              "api_key": "k", "model": "qwen3-32b"})
    calls = p._sanitize_messages(_history(SIG))[1]["tool_calls"]
    assert all("extra_content" not in c for c in calls)


# ── non-streaming ───────────────────────────────────────────────────────────

def test_non_stream_splits_thoughts_and_keeps_signature():
    resp = construct_type(type_=ChatCompletion, value={
        "id": "x", "object": "chat.completion", "created": 0, "model": "gemini",
        "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
            "role": "assistant", "content": "<thought>plan</thought>Checking.",
            "tool_calls": [{"id": "c1", "type": "function", "extra_content": SIG,
                            "function": {"name": "f", "arguments": "{}"}}]}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 40}})
    out = _gemini()._parse_response(resp)
    assert out.thinking == "plan"
    assert out.content == "<think>plan</think>\n\nChecking."
    assert out.tool_calls[0].extra_content == SIG
    assert out.usage["completion_tokens"] == 30 and out.usage["reasoning_tokens"] == 25
