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


def test_wait_never_speaks_the_fallback_after_an_interrupt():
    # A barge-in cancels the stream before any TTS chunk lands; the old wait()
    # read that as "TTS produced nothing" and spoke the partial row aloud.
    source, playback = _source()
    source._running = True
    source._audio_bytes_fed = 0
    source.speech_bridge = MagicMock()
    source.voice_transport = MagicMock()
    source._latest_assistant_text = lambda: 'Honestly, K'

    source.interrupt_playback()
    source.wait(timeout=0.01)

    source.speech_bridge.synthesize_speech.assert_not_called()
    source.voice_transport.play_audio_sync.assert_not_called()


def test_wait_still_falls_back_when_streaming_tts_truly_produced_nothing():
    source, playback = _source()
    source._running = True
    source._audio_bytes_fed = 0
    source.speech_bridge = MagicMock()
    source.speech_bridge.synthesize_speech.return_value = {'audio_bytes': b'wav'}
    source.voice_transport = MagicMock()
    source.voice_transport.play_audio_sync.return_value = {'status': 'playing'}
    source._latest_assistant_text = lambda: 'A whole reply nobody heard.'

    source.wait(timeout=0.01)

    source.speech_bridge.synthesize_speech.assert_called_once()


def test_reply_end_fires_after_drain_and_on_interrupt():
    ends = []
    source, playback = _source()
    source.on_reply_end = lambda: ends.append('end')
    source._running = True
    source._audio_bytes_fed = 3

    source.wait(timeout=0.01)
    source.interrupt_playback()

    assert ends == ['end', 'end']
