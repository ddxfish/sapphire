# tests/test_devices_av_claim.py - one door per reply to a board that takes her voice and face as
# one stream: the stream's feeder claims a reply (core/devices/glass.py av_claim), core's sentence
# door skips that reply there and nothing else (drivers/satellite.py play), the claim ends with the
# reply, a stop, or the feeder (scouts 2026-11-05: three seconds of recency stood in for this).
import base64
from unittest.mock import patch

import pytest

from core.devices import glass, voice
from core.devices.drivers import satellite


@pytest.fixture(autouse=True)
def _clean():
    with glass._lock:
        glass._av_claim.clear()
        glass._av_last.clear()
    yield
    with glass._lock:
        glass._av_claim.clear()
        glass._av_last.clear()


def test_a_claim_is_for_one_reply_on_one_board():
    glass.av_claim('pendant', 'abc')
    assert glass.av_carried('pendant', 'abc')
    assert not glass.av_carried('pendant', 'other')      # another reply: core's door
    assert not glass.av_carried('pendant', None)         # a say or a reminder: always core's door
    assert not glass.av_carried('cyd', 'abc')            # another board


def test_the_claim_ends_with_the_reply_the_feeder_or_a_stop():
    glass.av_claim('pendant', 'abc')
    glass.av_release(sid='abc')
    assert not glass.av_carried('pendant', 'abc')
    glass.av_claim('pendant', 'abc')
    glass.av_release(device_id='pendant')
    assert not glass.av_carried('pendant', 'abc')
    glass.av_claim('pendant', 'abc')
    with patch.object(glass, '_lookup', return_value={'config': {}, 'secrets': {}, 'av': True}), \
         patch('core.devices.drivers.satellite._call', side_effect=RuntimeError('no board')):
        assert glass.av_clear('pendant', upstream=False) is True   # a reply was on its way: "Stopped" is honest
    assert not glass.av_carried('pendant', 'abc')


def test_play_skips_only_the_claimed_reply():
    device = {'id': 'pendant'}
    glass.av_claim('pendant', 'abc')
    said = satellite.play(b'ogg', 'audio/ogg', device, {}, {}, sid='abc')
    assert said.get('skipped')
    with patch.object(satellite, '_health', side_effect=satellite.Problem('asked the board')):
        with pytest.raises(satellite.Problem):                  # another reply, or no stream id: it goes on to the board
            satellite.play(b'ogg', 'audio/ogg', device, {}, {}, sid='other')
        with pytest.raises(satellite.Problem):
            satellite.play(b'ogg', 'audio/ogg', device, {}, {})


def test_the_sentence_player_hands_the_reply_id_to_the_board():
    seen = []

    class Door:
        @staticmethod
        def play(audio, kind, device, config, secrets, sid=None):
            seen.append((audio, kind, sid))
            return {'ok': True}

    class Engine:
        @staticmethod
        def speaker(device_id):
            return Door, {'id': device_id}, {}, {}

    with patch.object(voice, '_engine', return_value=Engine):
        sp = voice.Speech('pendant')
        sp.feed({'type': 'tts_chunk', 'audio_b64': base64.b64encode(b'ogg').decode(), 'content_type': 'audio/ogg', 'stream_id': 'abc'})
        sp.finish()
        assert sp.wait(5)
    assert seen == [(b'ogg', 'audio/ogg', 'abc')]
