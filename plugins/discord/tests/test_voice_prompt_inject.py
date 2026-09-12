from unittest.mock import MagicMock, patch

from plugins.discord.hooks.voice_prompt_inject import prompt_inject
from plugins.discord.sapphire.voice_prompt import VOICE_CONTEXT_MARKER


def test_prompt_inject_adds_voice_context_for_discord_chat():
    event = MagicMock()
    event.context_parts = []
    with patch('plugins.discord.hooks.voice_prompt_inject._effective_chat_name', return_value='discord_111_222'):
        prompt_inject(event)
    assert len(event.context_parts) == 1
    assert VOICE_CONTEXT_MARKER in event.context_parts[0]


def test_prompt_inject_skips_non_voice_chat():
    event = MagicMock()
    event.context_parts = []
    with patch('plugins.discord.hooks.voice_prompt_inject._effective_chat_name', return_value='testasdfg'):
        prompt_inject(event)
    assert event.context_parts == []
