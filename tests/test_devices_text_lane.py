# tests/test_devices_text_lane.py - a device with a keyboard types at her
# (core/devices/voice.py typed / Reply / reply_text, the two /text doors).
# Same rig as test_devices_voice: real engine, real satellite driver, the
# turn engine faked. The board under test is a pocket terminal that said
# has: keyboard, screen, light, power - no mic.
import asyncio
import importlib
import types
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from core.devices import engine, voice
import core.devices.health as health
from core.devices.drivers import satellite as sat
from core.routes import devices as routes

POCKET_HAS = ['keyboard', 'screen', 'light', 'power']


class FakeStore:
    def __init__(self):
        self.d = {}

    def get(self, k, default=None):
        return self.d.get(k, default)

    def update_with_lock(self, k, mutator, default=None):
        self.d[k] = mutator(self.d.get(k, default))
        return self.d[k]


@pytest.fixture
def home(tmp_path):
    from core.credentials_manager import CredentialsManager
    import core.devices.registry as reg
    import core.devices.secret_store as sec
    store = FakeStore()
    sm = MagicMock()
    sm.get_active_chat_name.return_value = 'open-chat'
    sm.get_settings_for.side_effect = lambda chat: None if chat == 'ghost' else {'chat': chat}
    system = SimpleNamespace(llm_chat=SimpleNamespace(session_manager=sm), whisper_client=MagicMock(), tts=MagicMock())
    with patch('core.credentials_manager.CREDENTIALS_FILE', tmp_path / 'credentials.json'), \
         patch('core.credentials_manager.SCRAMBLE_SALT_FILE', tmp_path / '.scramble_salt'), \
         patch('core.credentials_manager.CONFIG_DIR', tmp_path):
        mgr = CredentialsManager()
        with patch.object(sec, 'SECRETS_FILE', tmp_path / 'device_secrets.json'), \
             patch.object(sec, 'CONFIG_DIR', tmp_path), \
             patch.object(sec, '_crypto', lambda: mgr), \
             patch.object(engine, '_store', lambda: store), \
             patch.object(health, '_store', lambda: types.SimpleNamespace(get=lambda k, d=None: d, save=lambda k, v: None)), \
             patch.object(engine, '_all_plugin_info', lambda: []), \
             patch.object(engine, '_managed', lambda: False), \
             patch.object(voice, '_system', lambda: system), \
             patch.object(voice, '_spawn', lambda fn, *args: fn(*args)), \
             patch.object(voice, 'DOORBELL_GAP', 0):
            sec.reload()
            importlib.reload(reg)
            health._belief.clear(); health._saved = None
            for held in (voice._waiting, voice._showing, voice._listeners, voice._replies, sat._about):
                held.clear()
            engine.add('pocket', 'Pocket', 'satellite',
                       {'url': 'http://192.168.1.50', 'token': 'board-key-abcdefgh', 'voice_key': 'pocket-key-12345678'})
            row = engine.get('pocket')
            engine._learn(row, row['parts'][0], sat.SPEC, POCKET_HAS)    # what its /health said
            yield SimpleNamespace(system=system, sm=sm, store=store)
            for held in (voice._waiting, voice._showing, voice._listeners, voice._replies, sat._about):
                held.clear()
            importlib.reload(reg)
            sec.reload()


HEADER = '[Typed on device "pocket" (Pocket). Your reply is shown on its small screen.]'


def _cues():
    seen = []
    real = voice.cue

    def record(device_id, state, **more):
        seen.append((device_id, state, more))
        real(device_id, state, **more)
    return patch.object(voice, 'cue', record), seen


def _streaming(pieces, final=None, tool=False):
    """A fake run_turn that streams `pieces` as content events to on_event
    and returns `final` (the pieces joined when None)."""
    def run(chat, text, images=None, speak=None, source=None, on_event=None, stream_speech=False):
        for p in pieces:
            on_event({'type': 'content', 'text': p})
        if tool:
            on_event({'type': 'tool_start', 'name': 'get_time'})
            on_event({'type': 'tool_end'})
        return ''.join(pieces) if final is None else final
    return patch('core.cadence.run_turn', side_effect=run)


# --- the device, as the engine sees it ---------------------------------------------

def test_a_pocket_shows_only_what_it_has_and_the_key_fields_ride_on_the_keyboard(home):
    row = engine.get('pocket')
    assert [c['capability'] for c in engine.describe(row)] == ['light', 'power', 'screen', 'keyboard']
    view = engine.public(row)
    fields = {f['key']: f for f in view['parts'][0]['schema']}
    assert fields['chat']['capability'] == 'keyboard'          # the page puts it on the Keyboard tab
    assert fields['voice_key']['capability'] == 'keyboard'
    assert 'look_resting' in fields and 'keep_daily' not in fields      # no card, no backup fields
    assert view['parts'][0]['values']['voice_key'] == 'set'


def test_the_key_fields_still_ride_on_the_mic_for_a_pi(home):
    engine.add('pi2', 'Kitchen', 'satellite',
               {'url': 'http://192.168.1.100:8090', 'token': 'body-key-abcdefgh', 'voice_key': 'voice-key-12345678'})
    fields = {f['key']: f for f in engine.public(engine.get('pi2'))['parts'][0]['schema']}
    assert fields['chat']['capability'] == 'mic' and fields['voice_key']['capability'] == 'mic'


def test_field_capability_rules():
    every = ['mic', 'keyboard', 'light']
    assert engine.field_capability({'key': 'url'}, ['light'], every) == ''
    assert engine.field_capability({'capability': 'mic'}, ['mic'], every) == 'mic'
    assert engine.field_capability({'capability': 'mic'}, ['light'], every) is None
    assert engine.field_capability({'capability': ('mic', 'keyboard')}, ['keyboard'], every) == 'keyboard'
    assert engine.field_capability({'capability': ('mic', 'keyboard')}, ['light'], every) is None
    assert engine.field_capability({'capability': 'odd'}, [], every) == 'odd'       # never declared: shown, as before


def test_the_pocket_key_opens_the_doors_without_a_mic(home):
    assert voice.key_ok('pocket', 'pocket-key-12345678')
    assert not voice.key_ok('pocket', 'wrong')
    assert voice.hear('pocket', b'RIFF')['error'] == "'pocket' has no microphone."


# --- typed -----------------------------------------------------------------------------

def test_typed_runs_a_turn_with_the_typed_line_and_nothing_is_spoken(home):
    with _streaming(['It is ', 'noon.']) as run_turn, patch.object(voice, 'say') as say:
        out = voice.typed('pocket', '  what time is it ')
    assert out == {'ok': True, 'accepted': True, 'chat': 'open-chat', 'msg': out['msg']}
    assert len(out['msg']) == 12
    run_turn.assert_called_once()
    args, kw = run_turn.call_args
    assert args[:2] == ('open-chat', HEADER + '\nwhat time is it')
    assert kw['speak'] is None and kw['stream_speech'] is False and kw['source'] == 'device:pocket'
    say.assert_not_called()
    assert voice.reply_text('pocket', out['msg']) == {'msg': out['msg'], 'rev': 0, 'from': 0, 'text': 'It is noon.',
                                                      'have': 11, 'done': True}


def test_doorbells_ring_as_she_writes_and_once_more_with_done(home):
    rec, seen = _cues()
    with rec, _streaming(['One. ', 'Two. ', 'Three.']):
        out = voice.typed('pocket', 'count')
    bells = [(s, m) for d, s, m in seen if s == 'text']
    assert [m['have'] for s, m in bells] == [4, 9, 16, 16]        # a trailing space waits for the next piece
    assert [m['done'] for s, m in bells] == [False, False, False, True]
    assert all(m['msg'] == out['msg'] and m['rev'] == 0 for s, m in bells)
    assert [s for d, s, m in seen if s != 'text'] == ['thinking', 'idle']


def test_thinking_never_reaches_the_screen(home):
    """A model that thinks inside its text streams the tags too. Krem saw
    them on the glass (2026-10-06): only what she says is published, and
    the revision does not move for it."""
    rec, seen = _cues()
    with rec, _streaming(['<think>let me', ' see</think>', 'Noon', '.'], final='Noon.'):
        out = voice.typed('pocket', 'time?')
    bells = [m for d, s, m in seen if s == 'text']
    assert [m['have'] for m in bells] == [4, 5, 5]              # nothing rang while only thinking was there
    assert all(m['rev'] == 0 for m in bells)
    assert voice.reply_text('pocket', out['msg'])['text'] == 'Noon.'


def test_a_final_text_that_differs_bumps_the_revision_but_more_of_it_does_not(home):
    rec, seen = _cues()
    with rec, _streaming(['Noon'], final='Noon, sharp.'):           # more of the same: no new revision
        out = voice.typed('pocket', 'time?')
    assert [m for d, s, m in seen if s == 'text'][-1] == {'msg': out['msg'], 'rev': 0, 'have': 12, 'done': True}
    rec, seen = _cues()
    with rec, _streaming(['Looking. ', 'Noon.'], final='Looking.\n\nNoon.'):    # a tool round joined: new revision
        out = voice.typed('pocket', 'time?')
    assert [m for d, s, m in seen if s == 'text'][-1] == {'msg': out['msg'], 'rev': 1, 'have': 15, 'done': True}
    assert voice.reply_text('pocket', out['msg'])['text'] == 'Looking.\n\nNoon.'


def test_pulls_slice_from_where_the_board_is_and_cap_at_max(home):
    with _streaming(['abcdefghij'] * 3):
        out = voice.typed('pocket', 'letters')
    msg = out['msg']
    assert voice.reply_text('pocket', msg, 0, 10) == {'msg': msg, 'rev': 0, 'from': 0, 'text': 'abcdefghij', 'have': 30, 'done': True}
    assert voice.reply_text('pocket', msg, 25)['text'] == 'fghij'
    assert voice.reply_text('pocket', msg, 99)['text'] == '' and voice.reply_text('pocket', msg, 99)['from'] == 30
    assert voice.reply_text('pocket', msg, 0, 10 ** 9)['text'] == 'abcdefghij' * 3     # under PULL_MAX anyway
    assert voice.reply_text('pocket')['msg'] == msg                                     # no msg: the latest
    assert voice.reply_text('pocket', 'gone')['msg'] == msg                             # unknown: the latest, from 0
    assert voice.reply_text('nobody') == {'msg': '', 'rev': 0, 'from': 0, 'text': '', 'have': 0, 'done': True}


def test_only_the_last_few_replies_are_kept(home):
    ids = []
    for i in range(voice.TEXT_KEEP + 2):
        with _streaming([f'reply {i}']):
            ids.append(voice.typed('pocket', f'q{i}')['msg'])
    kept = [r.msg for r in voice._replies['pocket']]
    assert kept == ids[-voice.TEXT_KEEP:]


def test_the_tool_cues_still_reach_the_light_on_a_typed_turn(home):
    rec, seen = _cues()
    with rec, _streaming(['Looking. '], tool=True):
        voice.typed('pocket', 'what time')
    assert [s for d, s, m in seen if s != 'text'] == ['thinking', 'tool', 'thinking', 'idle']


def test_a_turn_with_no_words_ends_on_error_for_the_light(home):
    rec, seen = _cues()
    with rec, _streaming([], final=''):
        out = voice.typed('pocket', 'hello')
    assert out['accepted'] is True
    assert [s for d, s, m in seen if s != 'text'] == ['thinking', 'error']
    assert voice.reply_text('pocket', out['msg'])['done'] is True


def test_a_turn_that_blows_up_still_closes_the_reply(home):
    with patch('core.cadence.run_turn', side_effect=RuntimeError('provider down')):
        out = voice.typed('pocket', 'hello')
    assert out['accepted'] is True
    assert voice.reply_text('pocket', out['msg']) == {'msg': out['msg'], 'rev': 0, 'from': 0, 'text': '', 'have': 0, 'done': True}
    assert voice._waiting == {}


def test_typed_refusals(home):
    assert voice.typed('pocket', '   ') == {'ok': False, 'error': 'Nothing was typed.'}
    assert voice.typed('pocket', 'x' * (voice.TYPED_MAX + 1))['error'].startswith('That is more than')
    assert voice.typed('nobody', 'hi')['error'].startswith('There is no device')
    engine.add('pi2', 'Kitchen', 'satellite',
               {'url': 'http://192.168.1.100:8090', 'token': 'body-key-abcdefgh', 'voice_key': 'voice-key-12345678'})
    pi = engine.get('pi2')
    engine._learn(pi, pi['parts'][0], sat.SPEC, ['speaker', 'mic', 'light', 'wake'])   # a Pi says what it has
    assert voice.typed('pi2', 'hi') == {'ok': False, 'error': "'pi2' has no keyboard."}
    engine.update('pocket', parts={'satellite': {'chat': 'ghost'}})
    assert "does not exist" in voice.typed('pocket', 'hi')['error']


def test_a_text_doorbell_does_not_wipe_the_thinking_cue_for_a_late_stream(home):
    loop = asyncio.new_event_loop()
    try:
        voice.cue('pocket', 'thinking')
        voice.cue('pocket', 'text', msg='m', rev=0, have=3, done=False)
        q = asyncio.Queue()
        now = voice.listen('pocket', loop, q)
        assert now and now['state'] == 'thinking'
        voice.unlisten('pocket', loop, q)
        voice.cue('pocket', 'idle')
        assert voice.listen('pocket', loop, q) is None
    finally:
        voice.unlisten('pocket', loop, q)
        loop.close()


# --- the doors ------------------------------------------------------------------------

def _req(method, path, body=b'', content_type='text/plain', key='pocket-key-12345678', query=''):
    from starlette.requests import Request
    headers = [(b'authorization', f'Bearer {key}'.encode()), (b'content-type', content_type.encode())]
    scope = {'type': 'http', 'method': method, 'path': path, 'raw_path': path.encode(), 'query_string': query.encode(),
             'headers': headers, 'client': ('192.168.1.50', 5000), 'server': ('127.0.0.1', 8073), 'scheme': 'http'}
    sent = {'done': False}

    async def receive():
        if sent['done']:
            return {'type': 'http.disconnect'}
        sent['done'] = True
        return {'type': 'http.request', 'body': body, 'more_body': False}
    return Request(scope, receive)


def test_the_post_door_takes_plain_text_and_json(home):
    with _streaming(['Noon.']):
        out = asyncio.run(routes.devices_text('pocket', _req('POST', '/api/devices/pocket/text', 'what time?'.encode())))
        assert out['accepted'] is True
        out2 = asyncio.run(routes.devices_text('pocket', _req('POST', '/api/devices/pocket/text',
                                                              b'{"text": "and now?"}', 'application/json')))
        assert out2['accepted'] is True and out2['msg'] != out['msg']
    with pytest.raises(HTTPException) as e:
        asyncio.run(routes.devices_text('pocket', _req('POST', '/api/devices/pocket/text', b'not json', 'application/json')))
    assert e.value.status_code == 422
    with pytest.raises(HTTPException) as e:
        asyncio.run(routes.devices_text('pocket', _req('POST', '/api/devices/pocket/text', b'hi', key='wrong')))
    assert e.value.status_code == 401


def test_the_get_door_pulls_a_piece(home):
    with _streaming(['Hello there.']):
        msg = voice.typed('pocket', 'hi')['msg']
    out = asyncio.run(routes.devices_text_read('pocket', _req('GET', '/api/devices/pocket/text',
                                                              query=f'msg={msg}&from=6&max=5')))
    assert out == {'msg': msg, 'rev': 0, 'from': 6, 'text': 'there', 'have': 12, 'done': True}
    out = asyncio.run(routes.devices_text_read('pocket', _req('GET', '/api/devices/pocket/text', query='from=junk')))
    assert out['from'] == 0 and out['msg'] == msg


# --- the driver's screen ---------------------------------------------------------------------

def test_screen_show_posts_the_line_and_its_seconds(home):
    calls = []

    def fake(method, url, **kw):
        calls.append((method, url, kw.get('json')))
        return SimpleNamespace(status_code=200, json=lambda: {'ok': True, 'seconds': kw['json'].get('seconds', 20)})
    with patch('core.net.request', side_effect=fake):
        said, ok = engine.run('pocket', 'screen', 'show', 'Dinner in ten minutes seconds=60')
        assert ok and said == 'On the screen of pocket for 60 seconds: "Dinner in ten minutes".'
        said, ok = engine.run('pocket', 'screen', 'show', 'Hi')
        assert ok and calls[-1] == ('POST', 'http://192.168.1.50/screen', {'text': 'Hi', 'seconds': 20})
        said, ok = engine.run('pocket', 'screen', 'clear', '')
        assert ok and calls[-1][2] == {'clear': True}
        said, ok = engine.run('pocket', 'screen', 'show', '')
        assert ok and said.startswith('show: the text')


def test_rgb565_fits_turns_landscape_and_packs_big_endian():
    import io as _io
    from PIL import Image
    img = Image.new('RGB', (640, 200), (255, 0, 0))              # landscape, pure red
    buf = _io.BytesIO(); img.save(buf, format='PNG')
    data, w, h = sat.rgb565(buf.getvalue(), 320, 480)
    assert (w, h) == (150, 480)                                  # turned a quarter: 200x640 fitted into 320x480
    assert len(data) == w * h * 2
    assert data[:2] == b'\xf8\x00'                             # red, MSB first
    img = Image.new('RGB', (100, 300), (0, 0, 255))              # portrait blue: no turn, no scale up
    buf = _io.BytesIO(); img.save(buf, format='PNG')
    data, w, h = sat.rgb565(buf.getvalue(), 320, 480)
    assert (w, h) == (100, 300) and data[:2] == b'\x00\x1f'


def test_screen_picture_resolves_fits_and_posts(home):
    import io as _io
    from PIL import Image
    from core import images
    img = Image.new('RGB', (64, 32), (0, 255, 0))
    buf = _io.BytesIO(); img.save(buf, format='PNG')
    calls = []

    def fake(method, url, **kw):
        calls.append((method, url, kw))
        if url.endswith('/health'):
            return SimpleNamespace(status_code=200, json=lambda: {'ok': True, 'has': POCKET_HAS, 'screen': {'w': 320, 'h': 480}})
        return SimpleNamespace(status_code=200, json=lambda: {'ok': True, 'seconds': 120})
    with patch('core.net.request', side_effect=fake), \
         patch.object(images, 'resolve', return_value=images.Resolved(buf.getvalue(), 'image/png', 'a green one', 'chat')), \
         patch.object(images, 'last_image_id', return_value='ab12'):
        said, ok = engine.run('pocket', 'screen', 'picture', 'last seconds=120')
        assert ok and said == 'On the screen of pocket: a green one (32x64), for 120 seconds or until a tap.'
        method, url, kw = calls[-1]
        assert (method, url) == ('POST', 'http://192.168.1.50/screen/picture?w=32&h=64&seconds=120')
        assert len(kw['data']) == 32 * 64 * 2 and kw['data'][:2] == b'\x07\xe0'
        assert kw['headers']['Content-Type'] == 'application/octet-stream'
    with patch('core.net.request', side_effect=fake), patch.object(images, 'last_image_id', return_value=None):
        said, ok = engine.run('pocket', 'screen', 'picture', '')
        assert ok and said.startswith('There is no image in this chat yet')      # an answer, not a fault
