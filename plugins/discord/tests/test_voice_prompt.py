from plugins.discord.sapphire.voice_prompt import (
    VOICE_CONTEXT_MARKER,
    build_voice_conversation_context,
    default_conversation_prompt_template,
    format_voice_turn_text,
    is_voice_conversation_chat,
    merge_voice_context,
    strip_voice_context,
)
from plugins.discord.voice.voice_addressing import should_address_bot


def test_build_voice_conversation_context_is_brief_and_covers_stt():
    block = build_voice_conversation_context(bot_names=['Remmi'])
    assert VOICE_CONTEXT_MARKER in block
    assert 'ONE or TWO' in block
    assert 'Remmi' in block
    assert 'Remy' in block
    assert 'misheard' in block or 'garbles' in block


def test_merge_voice_context_is_idempotent():
    first = merge_voice_context('', bot_names=['Remmi'])
    second = merge_voice_context(first, bot_names=['Remmi'])
    assert first == second


def test_format_voice_turn_text_prefixes_speaker():
    assert format_voice_turn_text('hey Remmi', speaker_name='Zeebie') == 'Zeebie: hey Remmi'
    assert format_voice_turn_text('plain text', speaker_name='') == 'plain text'
    assert format_voice_turn_text('Zeebie: already labeled', speaker_name='Zeebie') == 'Zeebie: already labeled'


def test_is_voice_conversation_chat():
    assert is_voice_conversation_chat('discord_111_222')
    assert not is_voice_conversation_chat('general')


def test_should_address_bot_with_speaker_prefix():
    assert should_address_bot('Zeebie: hey Remmi', ['Remmi'])
    assert not should_address_bot('Alice: pass the salt', ['Remmi'])


def test_merge_voice_context_replaces_stale_voice_block():
    first = merge_voice_context('', bot_names=['Remmi'], prompt_template='Custom for {primary}')
    second = merge_voice_context(
        first,
        bot_names=['Remmi'],
        prompt_template='Updated for {primary}',
    )
    assert 'Updated for Remmi' in second
    assert 'Custom for Remmi' not in second


def test_custom_prompt_template_uses_placeholders():
    block = build_voice_conversation_context(
        bot_names=['Remmi', 'Remy'],
        prompt_template='Hello {primary}{alias_line}.',
    )
    assert 'Hello Remmi (also known as Remy).' in block


def test_default_conversation_prompt_template_matches_builtin():
    assert '{primary}' in default_conversation_prompt_template()
    assert 'ONE or TWO' in default_conversation_prompt_template()


def test_strip_voice_context_removes_marker_block():
    merged = merge_voice_context('Other notes', bot_names=['Remmi'])
    assert strip_voice_context(merged) == 'Other notes'


def test_build_voice_conversation_context_mentions_multi_speaker():
    block = build_voice_conversation_context(bot_names=['Remmi'])
    assert 'Multiple people' in block
    assert 'prefixed' in block.lower() or 'prefix' in block.lower()
