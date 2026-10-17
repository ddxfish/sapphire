"""The voice prompt hook reads the runner's chat_name stamp (M11, hunt 2026-09-12)."""

import logging
from types import SimpleNamespace

from plugins.discord.hooks.voice_prompt_inject import prompt_inject
from plugins.discord.sapphire.voice_prompt import VOICE_CONTEXT_MARKER


def test_prompt_inject_adds_voice_context_for_discord_chat():
    event = SimpleNamespace(chat_name='discord_111_222', context_parts=[])
    prompt_inject(event)
    assert any(VOICE_CONTEXT_MARKER in str(part) for part in event.context_parts)


def test_prompt_inject_is_idempotent():
    event = SimpleNamespace(chat_name='discord_111_222', context_parts=[])
    prompt_inject(event)
    prompt_inject(event)
    assert sum(VOICE_CONTEXT_MARKER in str(part) for part in event.context_parts) == 1


def test_prompt_inject_skips_non_voice_chat():
    event = SimpleNamespace(chat_name='testasdfg', context_parts=[])
    prompt_inject(event)
    assert event.context_parts == []


def test_prompt_inject_bails_loudly_without_a_stamp(caplog):
    event = SimpleNamespace(chat_name=None, context_parts=[])
    with caplog.at_level(logging.WARNING):
        prompt_inject(event)
    assert event.context_parts == []
    assert 'no chat_name' in caplog.text
