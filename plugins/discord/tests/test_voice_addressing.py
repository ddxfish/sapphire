from plugins.discord.voice.discord_frame_feed import DiscordFrameFeed, stereo_frame_rms
from plugins.discord.voice.voice_addressing import directed_voice_phrase, is_stop_command, mentions_bot, resolve_bot_names, should_address_bot
from unittest.mock import MagicMock


def test_mentions_bot_by_display_name():
    assert mentions_bot('Hey Leona what do you think?', ['Leona'])
    assert not mentions_bot('pass the salt please', ['Leona'])


def test_mentions_bot_case_insensitive():
    assert mentions_bot('LEONA are you there', ['leona'])


def test_mentions_bot_aliases():
    assert mentions_bot('okay Sapphire help me', ['Sapphire', 'Leona'])


def test_mentions_bot_fuzzy_stt_misspelling():
    assert mentions_bot('Remy, can you hear me?', ['Remmi'])
    assert mentions_bot('hey remi what time is it', ['Remmi'])


def test_directed_voice_phrase_without_bot_name():
    assert directed_voice_phrase('Hello, testing, testing 123, can you hear me?')
    assert directed_voice_phrase('testing testing one two three')
    assert not directed_voice_phrase('pass the salt please')


def test_should_address_bot_name_mode_requires_bot_name():
    assert should_address_bot('Remy, can you hear me?', ['Remmi'])
    assert not should_address_bot('Testing, testing, one, two, three.', ['Remmi'])
    assert not should_address_bot('pass the salt please', ['Remmi'])


def test_is_stop_command():
    assert is_stop_command('Remi, stop talking please')
    assert is_stop_command('shut up')
    assert not is_stop_command('how are you doing today')


def test_should_address_bot_always_mode():
    assert should_address_bot('pass the salt please', ['Remmi'], addressing_mode='always')


def test_resolve_bot_names_from_settings():
    settings = MagicMock()
    settings.voice.addressing_aliases = ['Leona']
    names = resolve_bot_names(settings=settings)
    assert 'Leona' in names


def test_frame_feed_emits_engine_frames():
    frames = []

    def push(frame, is_speech=None):
        frames.append((frame, is_speech))

    feed = DiscordFrameFeed(push)
    stereo = b'\x00\x10' * (48000 // 50 * 2 * 4)
    feed.push_stereo_pcm(stereo, sample_rate=48000, is_speech=True)
    assert len(frames) >= 1
    assert len(frames[0][0]) == 512 * 2
    assert frames[0][1] is True


def test_stereo_frame_rms_positive_for_speech_like_pcm():
    stereo = b'\x00\x10' * 200
    assert stereo_frame_rms(stereo) > 0
