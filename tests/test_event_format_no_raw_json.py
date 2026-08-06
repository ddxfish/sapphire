"""Event formatter must never hand the AI raw payload JSON.

2026-08-06 incident: a text-less discord message (media processing off) made
_format_event_data fall through to the raw event payload — llm_primary /
llm_model routing metadata entered the prompt AND persisted into chat history
as the user turn, where the AI later quoted the stale provider as its current
model. Messaging-shaped payloads now format with a placeholder; generic
payloads keep raw-JSON visibility minus the LLM routing keys.
"""
import json

from core.continuity.executor import ContinuityExecutor

fmt = ContinuityExecutor._format_event_data


def test_messaging_payload_empty_content_never_dumps_raw_json():
    payload = json.dumps({
        "account": "mybot",
        "channel_id": "123456789",
        "content": "",
        "llm_primary": "lmstudionothink",
        "llm_model": "qwen-3",
        "display_name": "TestUser",
        "channel_name": "general",
    })
    out = fmt(payload)
    assert "llm_primary" not in out
    assert "lmstudionothink" not in out
    assert not out.strip().startswith("{")
    # Still a real formatted message: routing context + placeholder trigger
    assert "channel: general" in out
    assert "no text" in out


def test_messaging_payload_with_text_unchanged():
    payload = json.dumps({
        "account": "mybot",
        "channel_id": "42",
        "content": "hello there",
        "llm_primary": "claude",
        "display_name": "TestUser",
    })
    out = fmt(payload)
    assert ">>> TestUser: hello there" in out
    assert "llm_primary" not in out


def test_generic_event_keeps_data_but_redacts_llm_routing():
    payload = json.dumps({
        "sensor": "front_door",
        "state": "open",
        "llm_primary": "lmstudionothink",
        "llm_model": "qwen-3",
    })
    out = fmt(payload)
    # Generic (non-messaging) events keep raw-JSON visibility for their data…
    assert "front_door" in out
    assert "open" in out
    # …but the routing keys are never content.
    assert "llm_primary" not in out
    assert "lmstudionothink" not in out


def test_non_json_event_passes_through():
    assert fmt("plain text event") == "plain text event"
