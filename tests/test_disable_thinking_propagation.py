"""disable_thinking propagation (B2, 2026-07-09).

The Settings "disable thinking" checkbox lives on provider config
(llm_config['disable_thinking']). It must reach the actual request for providers
that support suppression. This is the regression guard for the split-brain where
only OpenAICompat read the config flag while Claude and Anthropic-compatible
providers silently ignored it — the checkbox was a no-op on Claude, its primary
use.

Method: construct the provider (inert — the Anthropic client makes no network call
at construction), swap its client for a stub that records the messages.create()
kwargs, then assert whether a `thinking` block is present. No network, no SDK
mocking beyond the client swap.
"""
from unittest.mock import MagicMock

from core.chat.llm_providers.claude import ClaudeProvider
from core.chat.llm_providers.anthropic_compat import AnthropicCompatProvider


def _run(provider, messages, gen_params=None):
    """Capture the kwargs the provider hands to Anthropic's messages.create."""
    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        return MagicMock()          # response parsing may choke on the mock; we don't care

    provider._client = MagicMock()
    provider._client.messages.create = create
    try:
        provider.chat_completion(messages, generation_params=gen_params)
    except Exception:
        pass
    assert captured, "messages.create was never called"
    return captured


def _claude(**over):
    return ClaudeProvider({"provider": "claude", "api_key": "x", "model": "claude-test",
                           "thinking_enabled": True, **over})


def _anthropic_compat(**over):
    return AnthropicCompatProvider({"provider": "anthropic", "api_key": "x",
                                    "base_url": "https://example.invalid/v1",
                                    "model": "claude-test", "thinking_enabled": True, **over})


MSGS = [{"role": "user", "content": "hi"}]


def _off(kw):
    """2026-09-20: on the adaptive family (Opus 5 / 4.x, Sonnet) "off" is an
    EXPLICIT {type: "disabled"} with no output_config — Opus 5 thinks when the
    param is omitted, so absence is no longer suppression (see the family
    table in claude.py). No effort may ride with disabled (accepted ≤ high only)."""
    return kw.get("thinking") == {"type": "disabled"} and "output_config" not in kw


# ── ClaudeProvider (the primary path) ────────────────────────────────────────

def test_claude_thinking_on_when_not_disabled():
    """Baseline: thinking_enabled + no disable → a thinking block IS sent. Proves the
    suppression tests below aren't vacuously passing."""
    kw = _run(_claude(disable_thinking=False), MSGS)
    assert kw["thinking"]["type"] == "adaptive" and "output_config" in kw


def test_claude_config_disable_thinking_suppresses():
    """THE bridge: config disable_thinking=True suppresses thinking even with
    thinking_enabled=True and no per-request param. This was the silent no-op."""
    assert _off(_run(_claude(disable_thinking=True, reasoning_effort="max"), MSGS))


def test_claude_per_request_param_still_wins():
    """A per-request disable param (continue/prefill) overrides config-enabled."""
    assert _off(_run(_claude(disable_thinking=False),
                     MSGS, gen_params={"disable_thinking": True}))


# ── AnthropicCompatProvider (custom Claude-template providers) ────────────────

def test_anthropic_compat_thinking_on_when_not_disabled():
    assert "thinking" in _run(_anthropic_compat(disable_thinking=False), MSGS)


def test_anthropic_compat_config_disable_thinking_suppresses():
    """The custom-provider case Scout 4 flagged: the checkbox is the only thinking
    control offered, and it must not lie."""
    assert "thinking" not in _run(_anthropic_compat(disable_thinking=True), MSGS)
