from unittest.mock import MagicMock

from plugins.discord.transport.discord_streaming_playback import (
    DISCORD_FRAME_BYTES,
    StreamingVoicePlayback,
)
from plugins.discord.voice.discord_conversation_source import DiscordConversationSource


def _source(*, playback=None):
    playback = playback or MagicMock()
    playback.start.return_value = {'status': 'streaming'}
    playback.feed_chunk.return_value = {'status': 'fed'}
    return DiscordConversationSource(
        MagicMock(),
        MagicMock(),
        playback,
        account_name='remmi',
        channel_id='123',
    ), playback


def test_start_restarts_streaming_playback_on_subsequent_turns():
    source, playback = _source()
    source._running = True

    source.start()

    playback.start.assert_called_once_with('remmi', '123')


def test_feed_chunk_logs_when_not_streaming():
    source, playback = _source()
    playback.feed_chunk.return_value = {'status': 'not_streaming'}

    source.feed_chunk({'audio_b64': 'Zm9v'})

    assert playback.start.call_count == 0
    assert source._audio_bytes_fed == 0


def test_begin_turn_clears_pending():
    playback = StreamingVoicePlayback()
    playback.feed(b'\xff' * DISCORD_FRAME_BYTES * 2)
    playback.begin_turn()
    assert playback.pending_bytes() == 0


def test_interrupt_playback_stops_without_closing_source():
    source, playback = _source()
    source._playing = True
    source._running = True

    source.interrupt_playback()

    playback.stop.assert_called_once_with('remmi', '123')
    assert source._stop_flag.is_set()
    assert source._playing is False
    assert source._running is True
