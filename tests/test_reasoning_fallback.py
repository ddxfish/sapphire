"""openai_compat's reasoning-as-content fallback is flagged, and reasoning
token counts reach the usage dict (the LLM log line said reasoning_tok=0 for
every OpenAI-compatible provider). 2026-09-25."""
from types import SimpleNamespace as NS

from core.chat.llm_providers.openai_compat import OpenAICompatProvider


def _prov():
    return OpenAICompatProvider({"provider": "openai", "base_url": "http://t/v1",
                                 "api_key": "k", "model": "m"})


def _resp(content, reasoning=None, reasoning_tokens=None):
    details = NS(reasoning_tokens=reasoning_tokens) if reasoning_tokens is not None else None
    usage = NS(prompt_tokens=10, completion_tokens=5, total_tokens=15,
               prompt_tokens_details=None, completion_tokens_details=details)
    msg = NS(content=content, reasoning_content=reasoning, tool_calls=None)
    return NS(choices=[NS(message=msg, finish_reason="length")], usage=usage)


def test_reasoning_only_reply_is_flagged():
    r = _prov()._parse_response(_resp(None, reasoning="thinking..."))
    assert r.content == "thinking..." and r.content_is_reasoning is True


def test_reasoning_plus_content_is_not_flagged():
    r = _prov()._parse_response(_resp("answer", reasoning="thinking..."))
    assert r.content.endswith("answer") and r.content_is_reasoning is False


def test_plain_reply_is_not_flagged():
    r = _prov()._parse_response(_resp("answer"))
    assert r.content == "answer" and r.content_is_reasoning is False


def test_reasoning_tokens_reach_usage():
    assert _prov()._parse_response(_resp("answer", reasoning_tokens=1234)).usage["reasoning_tokens"] == 1234
    assert "reasoning_tokens" not in _prov()._parse_response(_resp("answer")).usage


def test_stream_reasoning_only_is_flagged():
    p = _prov()
    think = NS(choices=[NS(delta=NS(reasoning_content="thinking", content=None, tool_calls=None),
                           finish_reason=None)], usage=None)
    end = NS(choices=[NS(delta=NS(reasoning_content=None, content=None, tool_calls=None),
                         finish_reason="length")], usage=None)
    p._create = lambda kw: iter([think, end])
    done = list(p.chat_completion_stream([{"role": "user", "content": "hi"}]))[-1]["response"]
    assert done.content == "thinking" and done.content_is_reasoning is True


def test_stream_with_answer_is_not_flagged():
    p = _prov()
    chunk = NS(choices=[NS(delta=NS(reasoning_content="t", content="answer", tool_calls=None),
                           finish_reason="stop")], usage=None)
    p._create = lambda kw: iter([chunk])
    done = list(p.chat_completion_stream([{"role": "user", "content": "hi"}]))[-1]["response"]
    assert done.content == "answer" and done.content_is_reasoning is False
