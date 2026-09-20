"""Regression tests for the 2026-05-11 DeepSeek-reasoner reasoning_content
round-trip fix.

User report: 400 errors when using DeepSeek's official API with the
`deepseek-reasoner` model + tool calling. Works fine via Fireworks. Error:
"Missing reasoning_content field in the assistant message at message index N".

Root cause: DeepSeek's official API has a SPLIT round-trip rule for the
reasoner model:
  - WITHOUT tool_calls: must NOT pass `reasoning_content` back (400 if you do)
  - WITH tool_calls in history: MUST pass `reasoning_content` back on every
    prior assistant turn that had `tool_calls` (400 if you don't)

Sapphire's `_sanitize_messages` always stripped `reasoning_content` —
correct for case 1, broken for case 2. Fireworks works because their
DeepSeek deployment doesn't enforce the round-trip requirement.

The fix mirrors Claude's `thinking_raw` round-trip pattern (already wired
in `history.py:404`): a provider-specific helper detects the endpoint+model
combination, and the sanitizer emits `reasoning_content` only when both
conditions hold (deepseek-official-reasoner AND assistant-with-tool_calls).

2026-09-20 — DeepSeek V4 update (api-docs.deepseek.com/guides/thinking_mode):
`deepseek-chat` / `deepseek-reasoner` were discontinued 2026-07-24. The
official endpoint now serves `deepseek-v4-pro` and `deepseek-flash`, BOTH
thinking by default (switched via extra_body {"thinking": {"type": ...}}),
and the round-trip rule became: "for requests carrying the tools parameter,
the reasoning_content must be fully passed back in all subsequent requests —
even for turns where the model did not perform a tool call". So the gate is
now endpoint-only and the sanitizer emits `reasoning_content` on any
assistant turn that carries `thinking`. (History still hands `thinking` to
the sanitizer on tool-calling turns only — see section 3.)
"""
from unittest.mock import MagicMock, patch

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# 1. _is_deepseek_official() — endpoint + model gating
# ─────────────────────────────────────────────────────────────────────────────


def _make_provider(base_url, model):
    """Construct an OpenAICompatProvider with the bare minimum config so
    we can probe its detection helpers without hitting the network.

    Bypasses __init__ to skip the OpenAI client setup, then restores the
    instance attributes that downstream code relies on. The empty `config`
    dict is critical — _sanitize_messages reads `self.config.get(...)`
    and would AttributeError without it."""
    from core.chat.llm_providers.openai_compat import OpenAICompatProvider

    with patch.object(OpenAICompatProvider, '__init__', lambda self, *a, **kw: None):
        p = OpenAICompatProvider()
    p.base_url = base_url
    p.model = model
    p.config = {}  # required by _sanitize_messages → self.config.get(...)
    return p


def test_is_deepseek_official_true_for_official_v4_pro():
    """The official endpoint + a V4 model must trigger the round-trip
    behavior. This is the case that 400s without it."""
    p = _make_provider('https://api.deepseek.com/v1', 'deepseek-v4-pro')
    assert p._is_deepseek_official() is True


def test_is_deepseek_official_true_for_every_official_model():
    """V4: every model on api.deepseek.com thinks by default, so the gate is
    endpoint-only. The old `reasoner` name gate matched a model that no
    longer exists and would have silently disarmed the round-trip."""
    for model in ('deepseek-flash', 'deepseek-v4-flash', 'deepseek-reasoner'):
        p = _make_provider('https://api.deepseek.com/v1', model)
        assert p._is_deepseek_official() is True, model


def test_is_deepseek_official_false_for_fireworks_deepseek():
    """Fireworks' DeepSeek deployment doesn't enforce the round-trip rule.
    Sending `reasoning_content` to Fireworks is at best ignored and at worst
    rejected. Must NOT trigger for any non-official endpoint."""
    p = _make_provider(
        'https://api.fireworks.ai/inference/v1',
        'accounts/fireworks/models/deepseek-v3p2'
    )
    assert p._is_deepseek_official() is False


def test_is_deepseek_official_false_for_openrouter_deepseek():
    """OpenRouter wraps DeepSeek but doesn't enforce the round-trip rule."""
    p = _make_provider('https://openrouter.ai/api/v1', 'deepseek/deepseek-r1')
    assert p._is_deepseek_official() is False


def test_is_deepseek_official_handles_empty_or_none_url():
    """Defensive: a partially-initialized provider mustn't crash the helper."""
    p = _make_provider(None, 'deepseek-v4-pro')
    assert p._is_deepseek_official() is False
    p = _make_provider('', 'deepseek-v4-pro')
    assert p._is_deepseek_official() is False


# ─────────────────────────────────────────────────────────────────────────────
# 2. _sanitize_messages — reasoning_content emission
# ─────────────────────────────────────────────────────────────────────────────


def test_sanitize_emits_reasoning_content_for_deepseek_official_with_tool_calls():
    """The exact fix path: assistant message with tool_calls + thinking,
    on the deepseek-official endpoint, must emit `reasoning_content`
    in the outgoing payload. Without this, request 2+ in the tool cycle 400s.
    """
    p = _make_provider('https://api.deepseek.com/v1', 'deepseek-v4-pro')
    messages = [
        {"role": "user", "content": "what time is it?"},
        {
            "role": "assistant",
            "content": "Let me check.",
            "thinking": "I should use the get_time tool here.",
            "tool_calls": [{
                "id": "call_abc",
                "type": "function",
                "function": {"name": "get_time", "arguments": "{}"},
            }],
        },
        {"role": "tool", "tool_call_id": "call_abc",
         "name": "get_time", "content": "09:00"},
    ]

    cleaned = p._sanitize_messages(messages)

    asst = next(m for m in cleaned if m.get("role") == "assistant")
    assert "reasoning_content" in asst, (
        "DeepSeek-reasoner official requires reasoning_content on tool-calling "
        "assistant turns. Without it, request 2+ in the tool cycle returns 400."
    )
    assert asst["reasoning_content"] == "I should use the get_time tool here."


def test_sanitize_emits_reasoning_content_on_tool_free_turns_too():
    """V4 rule: once tools are in play, `reasoning_content` must come back
    on EVERY assistant turn — "even for turns where the model did not
    perform a tool call". The sanitizer no longer requires tool_calls.
    """
    p = _make_provider('https://api.deepseek.com/v1', 'deepseek-v4-pro')
    messages = [
        {"role": "user", "content": "hello"},
        {
            "role": "assistant",
            "content": "Hi there!",
            "thinking": "User said hello, I greet back.",
            # NO tool_calls
        },
    ]

    cleaned = p._sanitize_messages(messages)

    asst = next(m for m in cleaned if m.get("role") == "assistant")
    assert asst.get("reasoning_content") == "User said hello, I greet back.", (
        "V4 official endpoint wants reasoning_content on every assistant "
        "turn that has it, tool call or not."
    )


def test_sanitize_does_not_emit_reasoning_content_for_fireworks():
    """Fireworks doesn't enforce the round-trip — adding `reasoning_content`
    would pollute the payload (best case ignored, worst case rejected by
    their stricter validation). Must only fire for the official endpoint.
    """
    p = _make_provider(
        'https://api.fireworks.ai/inference/v1',
        'accounts/fireworks/models/deepseek-v3p2'
    )
    messages = [
        {
            "role": "assistant",
            "content": "",
            "thinking": "thinking text",
            "tool_calls": [{
                "id": "call_x",
                "type": "function",
                "function": {"name": "f", "arguments": "{}"},
            }],
        },
    ]

    cleaned = p._sanitize_messages(messages)
    asst = cleaned[0]
    assert "reasoning_content" not in asst, (
        "Non-official DeepSeek deployments don't need this field. Adding it "
        "everywhere risks rejection by stricter providers."
    )


def test_sanitize_does_not_emit_empty_reasoning_content():
    """Nothing to pass back → no field. An empty `thinking` (thinking was
    off for that turn) must not become `reasoning_content: ""`.
    """
    p = _make_provider('https://api.deepseek.com/v1', 'deepseek-flash')
    messages = [
        {
            "role": "assistant",
            "content": "ok",
            "thinking": "",  # thinking was off for this turn
            "tool_calls": [{
                "id": "call_y",
                "type": "function",
                "function": {"name": "f", "arguments": "{}"},
            }],
        },
    ]

    cleaned = p._sanitize_messages(messages)
    asst = cleaned[0]
    assert "reasoning_content" not in asst


# ─────────────────────────────────────────────────────────────────────────────
# 3. history.get_messages_for_llm — thinking pass-through on tool turns
# ─────────────────────────────────────────────────────────────────────────────


def test_history_passes_thinking_through_on_tool_calling_assistant_turns():
    """The sanitizer can only emit `reasoning_content` if the upstream
    history layer hands it the `thinking` field. Pre-fix, history dropped
    `thinking` for everyone except Claude's `thinking_raw`. The fix carries
    `thinking` through on any assistant message that has tool_calls.
    """
    from core.chat.history import ConversationHistory

    chat = ConversationHistory()
    chat.messages = [
        {"role": "user", "content": "what time?"},
        {
            "role": "assistant",
            "content": "Let me check.",
            "thinking": "Use the get_time tool.",
            "tool_calls": [{
                "id": "call_t",
                "type": "function",
                "function": {"name": "get_time", "arguments": "{}"},
            }],
        },
        {"role": "tool", "tool_call_id": "call_t",
         "name": "get_time", "content": "09:00"},
    ]

    msgs = chat.get_messages_for_llm(in_tool_cycle=True)

    asst = next(m for m in msgs if m.get("role") == "assistant")
    assert asst.get("thinking") == "Use the get_time tool.", (
        "history.get_messages_for_llm must carry `thinking` on tool-calling "
        "assistant turns so provider sanitizers can decide whether to round-"
        "trip it as reasoning_content. Pre-fix, this field was dropped for "
        "all providers except Claude (which used thinking_raw separately)."
    )


def test_history_does_not_carry_thinking_on_tool_free_assistant_turns():
    """Tool-free turns don't need thinking carried through — the sanitizer's
    DeepSeek branch is gated on tool_calls, and emitting thinking on tool-free
    turns would risk leaking it into responses for providers that don't
    explicitly ignore the field. Stay tight: only carry when load-bearing.
    """
    from core.chat.history import ConversationHistory

    chat = ConversationHistory()
    chat.messages = [
        {"role": "user", "content": "hello"},
        {
            "role": "assistant",
            "content": "Hi!",
            "thinking": "Greeting back.",
            # No tool_calls
        },
    ]

    msgs = chat.get_messages_for_llm()
    asst = next(m for m in msgs if m.get("role") == "assistant")
    assert "thinking" not in asst, (
        "Tool-free turns shouldn't carry thinking through to the LLM payload. "
        "Pre-fix didn't, post-fix shouldn't either — only tool-calling turns "
        "need the round-trip."
    )


# ─────────────────────────────────────────────────────────────────────────────
# 4. Provider preset
# ─────────────────────────────────────────────────────────────────────────────


def test_deepseek_official_preset_exists_and_is_well_formed():
    """The wizard dropdown reads from provider_presets.json; the new
    DeepSeek-official entry must be present and parseable so users can
    pick it without hand-typing the URL.
    """
    import json
    from pathlib import Path

    presets_path = Path(__file__).parent.parent / "core" / "provider_presets.json"
    with open(presets_path, encoding="utf-8") as f:
        data = json.load(f)
    presets = data.get("presets", {})

    assert "deepseek_official" in presets, (
        "Expected a `deepseek_official` preset in provider_presets.json so "
        "users can select DeepSeek's official endpoint from the wizard."
    )
    p = presets["deepseek_official"]
    assert p["template"] == "openai"
    assert "api.deepseek.com" in p["base_url"], (
        "base_url must point at the official endpoint so "
        "`_is_deepseek_official()` matches and the round-trip fix activates."
    )
    model_ids = {m["id"] for m in p.get("suggested_models", [])}
    assert "deepseek-v4-pro" in model_ids, "V4 Pro is the flagship the round-trip exists for."
    assert "deepseek-flash" in model_ids, "Flash (V4.1) should be offered too."
    assert not model_ids & {"deepseek-chat", "deepseek-reasoner"}, (
        "Both legacy names were discontinued 2026-07-24 — offering them is a trap."
    )
