"""Optional sampling params (2026-08-23): penalties / top_k are blank-by-
default and never sent unless filled; repeat_penalty + top_k ride extra_body
(not in the SDK signature — a kwarg would TypeError); a strict endpoint that
refuses one is learned ONCE per provider instance — stripped, retried,
warned — and never sent again until restart. stream_options rides the same
law instead of re-paying the double request every streaming call."""
from unittest.mock import MagicMock

import pytest

from core.chat.llm_providers import DEFAULT_GENERATION_PARAMS, provider_registry
from core.chat.llm_providers.openai_compat import OpenAICompatProvider
from core.chat.llm_providers.gemini import GeminiProvider


def _provider(model="qwen3-32b", **extra):
    return OpenAICompatProvider({"provider": "openai", "base_url": "http://localhost:1234/v1",
                                 "api_key": "test", "model": model, "display_name": "LM Studio",
                                 **extra})


class _Refused(Exception):
    status_code = 400

    def __init__(self, msg, body=None):
        super().__init__(msg)
        self.body = body


# ── defaults: blank = never sent ─────────────────────────────────────────────

def test_penalties_are_not_silent_defaults():
    assert set(DEFAULT_GENERATION_PARAMS) == {"temperature", "top_p", "max_tokens"}
    params = provider_registry.get_generation_params(
        "lmstudio", "some-model", {"lmstudio": {"generation_params": {"temperature": 0.6}}})
    assert "presence_penalty" not in params and "repeat_penalty" not in params


def test_filled_knobs_flow_and_local_ones_ride_extra_body():
    p = _provider()
    out = p._transform_params_for_model({
        "temperature": 0.7, "presence_penalty": 1.2, "frequency_penalty": None,
        "repeat_penalty": 1.1, "top_k": 40, "max_tokens": ""})
    assert out["presence_penalty"] == 1.2                     # SDK-typed → top level
    assert "frequency_penalty" not in out and "max_tokens" not in out   # blank/None → absent
    assert out["extra_body"] == {"repeat_penalty": 1.1, "top_k": 40}
    assert "repeat_penalty" not in out and "top_k" not in out


@pytest.mark.parametrize("model", ["gpt-5.2", "o3-mini", "grok-4"])
def test_strict_families_strip_all_knobs(model):
    p = _provider(model=model)
    out = p._transform_params_for_model({
        "presence_penalty": 1.0, "frequency_penalty": 0.5, "repeat_penalty": 1.1,
        "top_k": 40, "max_tokens": 100})
    assert not any(k in out for k in ("presence_penalty", "frequency_penalty",
                                     "repeat_penalty", "top_k", "extra_body"))


def test_gemini_drops_local_knobs_before_the_lift():
    g = GeminiProvider({"provider": "gemini", "base_url": "http://localhost:1/v1",
                        "api_key": "k", "model": "gemini-2.0-flash", "thinking_enabled": False})
    out = g._transform_params_for_model({"temperature": 0.5, "repeat_penalty": 1.1,
                                         "top_k": 5, "presence_penalty": 0.3})
    assert out == {"temperature": 0.5}


# ── learn-once refusal ───────────────────────────────────────────────────────

def test_refused_param_is_stripped_retried_and_remembered(monkeypatch):
    p = _provider()
    notices = []
    monkeypatch.setattr("core.chat.llm_providers.openai_compat._notify",
                        lambda m, severity="warning": notices.append(m))
    create = MagicMock(side_effect=[
        _Refused("Unrecognized request argument supplied: repeat_penalty"), "resp", "resp2"])
    p._client = MagicMock()
    p._client.chat.completions.create = create

    kw = {"model": "m", "messages": [], "presence_penalty": 0.5,
          "extra_body": {"repeat_penalty": 1.1, "top_k": 40}}
    assert p._create(kw) == "resp"
    assert create.call_count == 2
    retry_kw = create.call_args_list[1].kwargs
    assert retry_kw["extra_body"] == {"top_k": 40}            # only the named one left
    assert retry_kw["presence_penalty"] == 0.5
    assert p._rejected_params == {"repeat_penalty"}
    assert len(notices) == 1 and "repeat_penalty" in notices[0] and "LM Studio" in notices[0]

    # next request: stripped pre-emptively, ONE call, no new notice
    kw2 = {"model": "m", "messages": [], "extra_body": {"repeat_penalty": 1.1}}
    assert p._create(kw2) == "resp2"
    assert create.call_count == 3
    assert "extra_body" not in create.call_args_list[2].kwargs   # emptied dict dropped
    assert len(notices) == 1


def test_unrelated_400_is_not_retried():
    p = _provider()
    create = MagicMock(side_effect=_Refused("tool call validation failed: bad schema"))
    p._client = MagicMock()
    p._client.chat.completions.create = create
    with pytest.raises(_Refused):
        p._create({"model": "m", "messages": [], "presence_penalty": 0.5})
    assert create.call_count == 1 and not p._rejected_params


def test_stream_options_legacy_phrasing_learned_once(monkeypatch):
    monkeypatch.setattr("core.chat.llm_providers.openai_compat._notify", lambda *a, **k: None)
    p = _provider()
    create = MagicMock(side_effect=[_Refused("unknown field in request"), "stream", "stream2"])
    p._client = MagicMock()
    p._client.chat.completions.create = create
    kw = {"model": "m", "messages": [], "stream": True,
          "stream_options": {"include_usage": True}, "presence_penalty": 0.2}
    assert p._create(kw) == "stream"
    assert "stream_options" not in create.call_args_list[1].kwargs
    assert create.call_args_list[1].kwargs["presence_penalty"] == 0.2   # unnamed → kept
    assert p._rejected_params == {"stream_options"}
    assert p._create({"model": "m", "messages": [], "stream": True,
                      "stream_options": {"include_usage": True}}) == "stream2"
    assert create.call_count == 3


def test_error_body_is_searched_too(monkeypatch):
    monkeypatch.setattr("core.chat.llm_providers.openai_compat._notify", lambda *a, **k: None)
    p = _provider()
    create = MagicMock(side_effect=[
        _Refused("400 Bad Request", body={"error": {"param": "top_k", "message": "not permitted"}}),
        "ok"])
    p._client = MagicMock()
    p._client.chat.completions.create = create
    assert p._create({"model": "m", "messages": [], "extra_body": {"top_k": 3}}) == "ok"
    assert p._rejected_params == {"top_k"}
