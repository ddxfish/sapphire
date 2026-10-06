# tests/test_devices_wake.py - one wake, one answer (core/devices/wake.py), and
# her voice on every device at once (`all`). The room arbiter on its own,
# then through the real voice door with two satellites and the main app,
# then the main app's own hook points, then `all` and `stop`.
import os
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from core.devices import engine, voice, wake
from core.devices.drivers import satellite as sat
from test_devices_voice import home, ACCEPTED, _turn, _speaking_device   # noqa: F401  (the fixture rides along)


@pytest.fixture(autouse=True)
def quiet_room():
    wake.clear()
    yield
    wake.clear()


def _clock(start=1000.0):
    """A monotonic clock the test moves by hand."""
    now = [start]
    return now, patch.object(wake.time, 'monotonic', lambda: now[0])


# --- the arbiter -----------------------------------------------------------------

def test_the_first_claim_holds_the_room_and_the_main_app_takes_it():
    now, clock = _clock()
    with clock:
        assert wake.claim('pi2') == (True, None)
        assert wake.claim('esp') == (False, 'pi2')               # within CLAIM: the room is taken
        assert wake.claim(wake.MAIN) == (True, None)             # the main app always gets it
        assert wake.claim('esp') == (False, wake.MAIN)
        wake.heard(wake.MAIN, 'what time is it')                 # its question is in
        now[0] += wake.CLAIM + 0.1
        assert wake.claim('esp') == (True, None)                 # a later wake is a new one


def test_a_long_question_keeps_the_room_until_its_words_are_in():
    """Krem, 2026-10-04: the ESP32 recorded 14 s of 'hey Sapphire's; the Pi
    re-armed, fired on a later one, was granted a 'new' wake, and both
    answered. While the holder is still listening, the room is held."""
    now, clock = _clock()
    with clock:
        assert wake.claim('esp') == (True, None)
        now[0] += 13
        assert wake.claim('pi2') == (False, 'esp')               # the ESP32 is still recording: same question
        assert [h['who'] for h in wake.shadowed('pi2')] == ['esp']
        now[0] += 1.5
        wake.heard('esp', 'hey sapphire hey sapphire what time is it')   # 14.5 s in, its upload landed
        now[0] += 2
        assert wake.claim('pi2') == (True, None)                 # the question is in: a wake now is a new one
        assert [h['who'] for h in wake.shadowed('pi2')] == ['esp']   # but its words still shadow an arrival
        now[0] += wake.SHADOW
        assert wake.shadowed('pi2') == []                        # SHADOW runs from the words, not the claim


def test_a_wake_that_never_sends_anything_lets_go_of_the_room_in_time():
    now, clock = _clock()
    with clock:
        wake.claim('esp')                                        # woke, heard nothing, sent nothing
        now[0] += wake.LISTEN_MAX - 0.1
        assert wake.claim('pi2') == (False, 'esp')
        now[0] += 0.2
        assert wake.claim('pi2') == (True, None)
        assert [h['who'] for h in wake.shadowed('main')] == ['pi2']


def test_a_released_claim_no_longer_blocks_a_new_wake():
    assert wake.claim('pi2') == (True, None)
    wake.release('pi2')                                          # it heard nothing, or its turn ended
    assert wake.claim('esp') == (True, None)


def test_shadowed_lists_the_others_newest_first_within_the_shadow():
    now, clock = _clock()
    with clock:
        wake.claim('pi2'); wake.heard('pi2', 'one'); now[0] += 1
        wake.claim(wake.MAIN); wake.heard(wake.MAIN, 'two'); now[0] += 1
        assert [h['who'] for h in wake.shadowed('esp')] == [wake.MAIN, 'pi2']
        assert [h['who'] for h in wake.shadowed('pi2')] == [wake.MAIN]   # never its own hearing
        now[0] += wake.SHADOW
        assert wake.shadowed('esp') == []                        # all of it is old news


def test_words_of_waits_for_the_holder_and_gives_up_when_it_failed():
    wake.claim(wake.MAIN)
    held = wake.shadowed('pi2')[0]
    threading.Timer(0.15, wake.heard, args=(wake.MAIN, 'what time is it')).start()
    t0 = time.monotonic()
    assert wake.words_of(held) == 'what time is it'
    assert time.monotonic() - t0 < 1.5                           # woken, not waited out
    wake.clear()
    wake.claim(wake.MAIN)
    held = wake.shadowed('pi2')[0]
    threading.Timer(0.1, wake.release, args=(wake.MAIN,)).start()  # released with no words: it failed
    assert wake.words_of(held) is None
    wake.clear()
    wake.claim(wake.MAIN)
    assert wake.words_of(wake.shadowed('pi2')[0], timeout=0.05) is None   # nobody ever spoke


@pytest.mark.parametrize('a, b, one', [
    ('What time is it?', 'what time is it', True),
    ('Hey Sapphire, what time is it in Buffalo', 'what time is it in Buffalo?', True),   # one cut short by a faster endpoint
    ('turn the kitchen light on', 'turn the kitchen lights on', True),
    ('what time is it', 'play some jazz in the bedroom', False),
    ('on', 'turn the kitchen light on', False),                 # too short to count as "inside"
    ('', 'what time is it', False),
])
def test_same_knows_one_utterance_from_two(a, b, one):
    assert wake.same(a, b) is one


# --- through the voice door --------------------------------------------------------

@pytest.fixture
def two(home):
    engine.add('esp', 'Desk', 'satellite',
               {'url': 'http://192.168.0.4:80', 'token': 'board-key-abcdefgh', 'voice_key': 'voice-key-esp32-11'})
    return home


def test_two_satellites_that_heard_the_same_words_give_one_answer(two):
    with _turn() as run_turn:
        assert voice.hear('esp', b'RIFFaudio') == dict(ACCEPTED)   # the faster endpoint arrives first
        assert voice.hear('pi2', b'RIFFaudio') == {'ok': True, 'heard': '', 'accepted': False,
                                                   'chat': 'open-chat', 'taken_by': 'esp'}
    assert run_turn.call_count == 1
    assert len(two.heard) == 2                                   # the second WAS transcribed: that is how it was known


def test_two_satellites_with_different_words_are_two_questions(two):
    two.stt.transcribe_file.side_effect = ['what time is it', 'play some jazz']
    with _turn() as run_turn:
        assert voice.hear('esp', b'RIFFaudio')['accepted'] is True
        assert voice.hear('pi2', b'RIFFaudio')['accepted'] is True
    assert run_turn.call_count == 2


def test_the_main_app_wins_over_a_satellite_that_heard_the_same(home):
    wake.claim(wake.MAIN)                                        # its wake word fired, in-process
    wake.heard(wake.MAIN, 'What time is it?')
    with _turn() as run_turn:
        out = voice.hear('pi2', b'RIFFaudio')
    assert out == {'ok': True, 'heard': '', 'accepted': False, 'chat': 'open-chat', 'taken_by': wake.MAIN}
    run_turn.assert_not_called()


def test_a_satellite_waits_for_the_main_apps_words_rather_than_guessing(home):
    wake.claim(wake.MAIN)                                        # still recording or transcribing
    threading.Timer(0.2, wake.heard, args=(wake.MAIN, 'what time is it')).start()
    with _turn() as run_turn:
        t0 = time.monotonic()
        out = voice.hear('pi2', b'RIFFaudio')
    assert out['accepted'] is False and out['taken_by'] == wake.MAIN
    assert time.monotonic() - t0 < 2                             # the words came, it did not wait the whole WORDS_WAIT
    run_turn.assert_not_called()


def test_a_main_app_that_failed_does_not_silence_the_room(home):
    wake.claim(wake.MAIN)
    wake.release(wake.MAIN)                                      # its recording failed: no words, done
    with _turn() as run_turn:
        assert voice.hear('pi2', b'RIFFaudio') == ACCEPTED
    run_turn.assert_called_once()


def test_a_second_person_at_a_satellite_is_still_answered(home):
    wake.claim(wake.MAIN)
    wake.heard(wake.MAIN, 'set a timer for ten minutes')
    with _turn() as run_turn:
        assert voice.hear('pi2', b'RIFFaudio') == ACCEPTED       # "what time is it": other words
    run_turn.assert_called_once()


def test_a_satellite_holds_the_room_from_arrival_until_its_turn_ends(home):
    seen = {}

    def run_turn(chat, text, **kw):
        seen['held'] = [h for h in wake._recent if h['who'] == 'pi2' and not h['done']]
        return 'Noon.'
    with patch('core.cadence.run_turn', side_effect=run_turn):
        voice.hear('pi2', b'RIFFaudio')
    assert len(seen['held']) == 1 and seen['held'][0]['words'] == 'what time is it'   # claimed before the turn, words known
    assert all(h['done'] for h in wake._recent)                  # released when the turn ended


def test_silence_and_refusals_let_go_of_the_room(home):
    home.stt.transcribe_file.side_effect = None
    home.stt.transcribe_file.return_value = ''
    assert voice.hear('pi2', b'RIFFaudio')['accepted'] is False
    assert all(h['done'] for h in wake._recent)
    wake.clear()
    home.stt.transcribe_file.side_effect = RuntimeError('deaf')
    assert voice.hear('pi2', b'RIFFaudio')['ok'] is False
    assert all(h['done'] for h in wake._recent)


# --- the main app's hook points ------------------------------------------------------

def _detector(tmp_path, text='what time is it'):
    from core.wakeword.wake_detector import WakeWordDetector
    det = WakeWordDetector.__new__(WakeWordDetector)
    det.audio_recorder = None
    det.running = False
    det.listen_thread = None
    det._play_tone = lambda: None
    recording = tmp_path / 'heard.wav'
    recording.write_bytes(b'RIFF')
    sm = MagicMock()
    sm.get_chat_settings.return_value = {}
    det.system = SimpleNamespace(
        whisper_client=SimpleNamespace(transcribe_file=lambda path: text),
        whisper_recorder=SimpleNamespace(record_audio=lambda: str(recording), last_failure_reason=''),
        llm_chat=SimpleNamespace(session_manager=sm),
        process_llm_query=MagicMock(), speak_error=MagicMock(), tts=SimpleNamespace(wait=lambda timeout=0: None))
    return det


def test_the_main_app_claims_the_room_tells_its_words_and_lets_go(tmp_path):
    det = _detector(tmp_path)
    seen = {}

    def ask(text, voice_turn=False):
        seen['during'] = [(h['who'], h['words'], h['done']) for h in wake._recent]
    det.system.process_llm_query.side_effect = ask
    with patch('core.stt.utils.can_transcribe', return_value=(True, '')), \
         patch('core.voice_privacy.stt_gate_reason', return_value=''), \
         patch('core.hooks.hook_runner.has_handlers', return_value=False):
        det.wake_word_detected()
    assert seen['during'] == [(wake.MAIN, 'what time is it', False)]    # held, with its words, while she answers
    assert [(h['who'], h['done']) for h in wake._recent] == [(wake.MAIN, True)]


def test_a_gated_main_app_claims_nothing(tmp_path):
    det = _detector(tmp_path)
    with patch('core.stt.utils.can_transcribe', return_value=(False, 'no STT')):
        det.wake_word_detected()
    assert wake._recent == []                                     # the room stays free for a satellite
    det.system.process_llm_query.assert_not_called()


def test_a_main_app_whose_recording_failed_lets_go_without_words(tmp_path):
    det = _detector(tmp_path)
    det.system.whisper_recorder = SimpleNamespace(record_audio=lambda: None, last_failure_reason='mic')
    with patch('core.stt.utils.can_transcribe', return_value=(True, '')), \
         patch('core.voice_privacy.stt_gate_reason', return_value=''):
        det.wake_word_detected()
    assert [(h['who'], h['words'], h['done']) for h in wake._recent] == [(wake.MAIN, None, True)]


# --- all: her voice on every device that can speak -------------------------------------

def test_all_appears_once_two_devices_can_speak(two):
    assert [d['id'] for d in engine.fleet()] == ['esp', 'pi2', 'all']
    assert engine.fleet()[-1] == {'id': 'all', 'location': '', 'online': True, 'caps': ['speaker'], 'every': True}
    text, ok = engine.list_text()
    assert ok and 'all' in text and 'every device that can' in text
    engine.update('esp', enabled=False)
    assert [d['id'] for d in engine.fleet()] == ['pi2']           # one speaker: no `all`


def test_no_device_may_be_named_all(home):
    with pytest.raises(engine.DeviceError):
        engine.add('all', 'Everyone', 'satellite', {'url': 'http://192.168.0.9:80', 'token': 'k' * 12})


def test_all_only_speaks(two):
    text, ok = engine.run('all')
    assert ok and 'esp, pi2' in text and 'device_action("all","speaker","say"' in text
    text, ok = engine.run('all', 'light', 'look', 'purple')
    assert not ok and 'name one device' in text
    text, ok = engine.run('all', 'speaker', 'volume', '50')
    assert not ok
    text, ok = engine.status_text('all')
    assert not ok and 'every device that can speak' in text


def test_say_on_all_renders_once_and_plays_on_every_device_at_once(two):
    door, sent = _speaking_device()
    two.system.tts.render.return_value = (b'OggS....', 'audio/ogg')
    with door:
        text, ok = engine.run('all', 'speaker', 'say', 'Dinner is ready')
    assert ok
    assert text.splitlines() == ['Said on 2 device(s): "Dinner is ready"', 'esp: said', 'pi2: said']
    two.system.tts.render.assert_called_once()
    posts = [url for m, url, kw in sent if url.endswith('/audio/speak')]
    assert sorted(posts) == ['http://192.168.0.4:80/audio/speak', 'http://192.168.1.100:8090/audio/speak']


def test_say_on_all_skips_a_device_believed_offline_but_tries_one_never_asked(two):
    door, sent = _speaking_device()
    two.system.tts.render.return_value = (b'OggS....', 'audio/ogg')
    beliefs = {'esp': {'online': False, 'ts': 5.0}, 'pi2': {'online': False, 'ts': 0.0}}   # asked and gone; never asked
    with door, patch.object(engine, 'statuses', return_value=beliefs):
        text, ok = engine.run('all', 'speaker', 'say', 'Dinner is ready')
    assert ok and text.splitlines() == ['Said on 1 device(s): "Dinner is ready"', 'pi2: said']


def test_a_button_stop_on_one_device_stops_the_others(two):
    answers = {'http://192.168.1.100:8090/audio/speak': {'ok': True, 'stopped': True}}
    sent = []

    def request(method, url, **kw):
        sent.append((method, url))
        if url.endswith('/health'):
            return SimpleNamespace(status_code=200, content=b'{}', json=lambda: {'ok': True})
        if url.endswith('/audio/speak') and url not in answers:
            time.sleep(0.3)                                        # the other device is still talking
        said = answers.get(url, {'ok': True, 'stopped': url.endswith('/audio/stop')})
        return SimpleNamespace(status_code=200, content=b'{}', json=lambda: said)
    two.system.tts.render.return_value = (b'OggS....', 'audio/ogg')
    with patch.object(sat.net, 'request', request):
        text, ok = engine.run('all', 'speaker', 'say', 'One. Two. Three.')
    assert ok and 'pi2: stopped there, so stopped everywhere' in text
    assert ('POST', 'http://192.168.0.4:80/audio/stop') in sent   # the other was told to cut it
    assert ('POST', 'http://192.168.1.100:8090/audio/stop') not in sent   # not the one whose button it was


def test_say_on_all_with_nothing_to_say_or_no_voice(two):
    assert engine.run('all', 'speaker', 'say', '')[0].startswith('say: the value')
    two.system.tts.render.return_value = (None, 'Speech is off.')
    text, ok = engine.run('all', 'speaker', 'say', 'Hello')
    assert not ok and text == 'Nothing was said: Speech is off.'


# --- stop ------------------------------------------------------------------------------

def test_stop_drops_her_next_sentences_and_tells_the_device(home):
    door, sent = _speaking_device(answers=[{'ok': True}, {'ok': True, 'stopped': True}])
    with door:
        speech = voice.Speech('pi2')
        speech.feed({'type': 'tts_chunk', 'audio_b64': 'T25lLg==', 'content_type': 'audio/ogg'})
        assert voice._live['pi2'] == [speech]
        text, ok = engine.run('pi2', 'speaker', 'stop')
    assert ok and text == 'Stopped.'
    assert speech.stopped is True                                  # core's half: nothing more is sent
    speech.finish(); speech.wait()
    assert 'pi2' not in voice._live
    assert [url for m, url, kw in sent if url.endswith('/audio/stop')] == ['http://192.168.1.100:8090/audio/stop']


def test_stop_on_a_satellite_without_the_door_is_said_plainly(home):
    def request(method, url, **kw):
        if url.endswith('/audio/stop'):
            return SimpleNamespace(status_code=404, content=b'{}', json=lambda: {'detail': 'Not Found'})
        return SimpleNamespace(status_code=200, content=b'{}', json=lambda: {'ok': True})
    with patch.object(sat.net, 'request', request):
        text, ok = engine.run('pi2', 'speaker', 'stop')
    assert ok and 'no stop door yet' in text


def test_stop_on_all_reaches_every_speaker(two):
    door, sent = _speaking_device(answers=[{'ok': True, 'stopped': True}, {'ok': True, 'stopped': False}])
    with door:
        text, ok = engine.run('all', 'speaker', 'stop')
    assert ok
    assert sorted(url for m, url, kw in sent if url.endswith('/audio/stop')) == [
        'http://192.168.0.4:80/audio/stop', 'http://192.168.1.100:8090/audio/stop']
    assert set(text.splitlines()) <= {'esp: Stopped.', 'pi2: Stopped.', 'esp: Nothing was playing there.',
                                      'pi2: Nothing was playing there.'}


def test_halt_is_true_only_while_something_is_being_said(home):
    assert voice.halt('pi2') is False
    door, _ = _speaking_device()
    with door:
        speech = voice.Speech('pi2')
        speech.feed({'type': 'tts_chunk', 'audio_b64': 'T25lLg==', 'content_type': 'audio/ogg'})
        assert voice.halt('pi2') is True and speech.stopped
        speech.finish(); speech.wait()
    assert voice.halt('pi2') is False


# --- the claim at the wake itself (the lock, §10 B) -----------------------------------

def test_a_device_asks_for_the_room_at_its_wake(two):
    assert voice.woke('esp') == {'ok': True, 'yours': True}
    assert voice.woke('pi2') == {'ok': True, 'yours': False, 'taken_by': 'esp'}    # the same wake, 0.3 s later
    assert voice.woke('nope')['ok'] is False
    engine.update('pi2', enabled=False)
    assert voice.woke('pi2')['ok'] is False


def test_a_device_that_claimed_at_its_wake_is_not_claimed_twice_on_arrival(home):
    voice.woke('pi2')
    with _turn():
        assert voice.hear('pi2', b'RIFFaudio') == ACCEPTED
    assert [h['who'] for h in wake._recent] == ['pi2']           # one hearing, not two
    assert wake._recent[0]['words'] == 'what time is it' and wake._recent[0]['done']


def test_the_main_app_takes_a_satellites_claim_and_the_satellite_is_told(home):
    from test_devices_voice import _cues
    watch, seen = _cues()
    with watch:
        assert voice.woke('pi2') == {'ok': True, 'yours': True}
        assert wake.claim(wake.MAIN) == (True, None)             # 0.4 s later, the desk heard it too
    assert seen == [('pi2', 'standdown')]                        # over its light stream: record nothing
    assert not wake.holds('pi2') and wake.holds(wake.MAIN)
    wake.heard(wake.MAIN, 'what time is it')
    with _turn() as run_turn:
        out = voice.hear('pi2', b'RIFFaudio')                    # its upload came anyway (the cue was late)
    assert out['accepted'] is False and out['taken_by'] == wake.MAIN
    run_turn.assert_not_called()


def test_standdown_is_a_cue_a_stream_can_carry(home):
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        queue = asyncio.Queue()
        voice.listen('pi2', loop, queue)
        voice.cue('pi2', 'standdown')
        loop.run_until_complete(asyncio.sleep(0.05))
        item = queue.get_nowait()
        assert item['state'] == 'standdown'
        assert voice.listen('pi2', loop, asyncio.Queue()) is None   # not remembered for a late stream, unlike thinking
    finally:
        voice._listeners.clear()
        loop.close()


def test_the_wake_door(home):
    from test_devices_voice import _request
    import asyncio
    from core.routes import devices as routes
    res = asyncio.run(routes.devices_wake('pi2', _request(key='voice-key-12345678')))
    assert res == {'ok': True, 'yours': True}
    with pytest.raises(HTTPException) as e:
        asyncio.run(routes.devices_wake('pi2', _request(key='wrong-key-00000000')))
    assert e.value.status_code == 401
