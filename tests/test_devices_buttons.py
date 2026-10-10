# tests/test_devices_buttons.py - a board's buttons (core/devices/voice.py
# pressed / bound / chat_for, the bindings field of the engine, the /press
# door). The rig of test_devices_text_lane: real engine, real satellite
# driver, the turn engine faked. The board is the pocket terminal, which
# now says it has a button too.
import asyncio
import json
from unittest.mock import patch

from core.devices import engine, voice
from core.devices.drivers import satellite as sat
from core.routes import devices as routes
from test_devices_text_lane import home, _streaming, _cues, POCKET_HAS   # noqa: F401  (the fixture rides along)

SAID = {'buttons': {'list': [{'name': 'boot', 'short': 'keyboard', 'long': 'clear'}],
                    'can': {'keyboard': 'Show or hide the keyboard', 'clear': 'Clear the chat window'}}}


def _with_button(*has):
    row = engine.get('pocket')
    engine._learn(row, row['parts'][0], sat.SPEC, list(has or POCKET_HAS + ['buttons']))
    sat._about['pocket'] = (0, SAID)
    return engine.get('pocket')


def _bind(**picks):
    engine.update('pocket', parts={'satellite': {'buttons': {k.replace('_', '.'): v for k, v in picks.items()}}})
    return engine.get('pocket')['parts'][0]['config']['buttons']


# --- the setting ---------------------------------------------------------------------

def test_a_binding_is_kept_as_a_job_and_its_words(home):
    _with_button()
    kept = _bind(boot_short={'do': 'tell', 'text': '  lights   off please '}, boot_long={'do': 'clear', 'text': 'x'},
                 boot_double={'do': ''}, boot_triple={'do': 'tell'})
    assert kept == {'boot.short': {'do': 'tell', 'text': 'lights off please'},
                    'boot.long': {'do': 'clear', 'text': ''}}       # only a message has words; no job, no slot; triple is no press


def test_junk_in_a_bindings_field_is_dropped(home):
    assert engine._bound('nope') == {} and engine._bound({'a b': {'do': 'tell'}, 'ok.short': {'do': 'X Y'}}) == {}
    assert engine._bound({'Boot.Short': 'Tell'}) == {'boot.short': {'do': 'tell', 'text': ''}}
    many = {f'b{i}.short': {'do': 'tell'} for i in range(engine.MAX_SLOTS + 5)}
    assert len(engine._bound(many)) == engine.MAX_SLOTS


def test_a_save_that_does_not_send_the_buttons_keeps_them(home):
    _with_button()
    _bind(boot_short={'do': 'tell', 'text': 'hi'})
    engine.update('pocket', parts={'satellite': {'chat': 'den'}})
    assert engine.get('pocket')['parts'][0]['config']['buttons'] == {'boot.short': {'do': 'tell', 'text': 'hi'}}


def test_the_page_gets_the_boards_own_menu(home):
    _with_button()
    field = next(f for f in engine.public(engine.get('pocket'))['parts'][0]['schema'] if f['key'] == 'buttons')
    menu = field['menu']
    assert [s['key'] for s in menu['slots']] == ['boot.short', 'boot.long', 'boot.double']
    assert [s['own'] for s in menu['slots']] == ['Show or hide the keyboard', 'Clear the chat window', 'nothing']
    assert [c['value'] for c in menu['choices']] == ['', 'tell', 'keyboard', 'clear', 'none']
    assert next(c for c in menu['choices'] if c['value'] == 'tell')['text'] is True
    assert 'buttons' in [c['capability'] for c in engine.describe(engine.get('pocket'))]


def test_a_board_that_has_not_said_its_buttons_offers_nothing_yet(home):
    _with_button()
    sat._about.clear()
    menu = sat.bindings({'id': 'pocket'}, {}, 'buttons')
    assert menu['slots'] == [] and 'has not said' in menu['note']


def test_a_board_without_buttons_has_no_buttons_field_and_is_told_nothing(home):
    assert 'buttons' not in [f['key'] for f in engine.public(engine.get('pocket'))['parts'][0]['schema']]
    assert voice.bound('pocket') is None and voice.bound('nobody') is None
    assert voice.pressed('pocket', 'boot', 'short') == {'ok': False, 'error': "'pocket' has no buttons."}


# --- what the board is told -------------------------------------------------------------

def test_bound_is_what_the_stream_carries(home):
    _with_button()
    assert voice.bound('pocket') == {}
    _bind(boot_short={'do': 'tell', 'text': 'hi'}, boot_double={'do': 'clear'})
    assert voice.bound('pocket') == {'boot': {'short': 'tell', 'double': 'clear'}}


def test_a_save_rings_the_boards_stream_with_its_buttons(home):
    _with_button()
    spy, seen = _cues()
    with spy, patch.object(sat, '_call', side_effect=sat.Missing('no door')):
        engine.tell(engine.update('pocket', parts={'satellite': {'buttons': {'boot.long': {'do': 'tell'}}}})[0])
    assert ('pocket', 'buttons', {'bound': {'boot': {'long': 'tell'}}}) in seen
    assert 'pocket' not in voice._showing                    # not a light state


def test_the_stream_opens_with_the_buttons(home):
    _with_button()
    _bind(boot_short={'do': 'tell'})

    async def first_lines():
        async def gone():
            return False
        stream = routes.light_stream('pocket', 'pocket-key-12345678', gone)
        with patch('core.devices.glass.sync_soon'):
            got = [json.loads((await stream.__anext__())[6:]) for _ in range(2)]
        await stream.aclose()
        return got
    connected, buttons = asyncio.run(first_lines())
    assert connected['state'] == 'connected'
    assert buttons == {'state': 'buttons', 'bound': {'boot': {'short': 'tell'}}, 'src': 'device'}


# --- a press ------------------------------------------------------------------------------

def test_a_press_set_to_send_lands_in_the_chat_and_her_reply_goes_to_the_screen(home):
    _with_button()
    _bind(boot_short={'do': 'tell', 'text': 'Goodnight, lights off.'})
    with _streaming(['Done. ', 'Sleep well.']) as run_turn:
        out = voice.pressed('pocket', 'boot', 'short')
    assert out['ok'] and out['accepted'] and out['chat'] == 'open-chat' and out['text'] == 'Goodnight, lights off.'
    chat, text = run_turn.call_args.args[:2]
    assert chat == 'open-chat'
    assert text == ('[Button pressed on device "pocket" (Pocket). Your reply is shown on its small screen.]\n'
                    'Goodnight, lights off.')
    assert run_turn.call_args.kwargs['speak'] is None
    assert voice.reply_text('pocket', out['msg'])['text'] == 'Done. Sleep well.'


def test_a_press_with_no_words_says_which_button(home):
    _with_button()
    _bind(boot_long={'do': 'tell'}, boot_double={'do': 'tell'})
    with _streaming(['ok']) as run_turn:
        assert voice.pressed('pocket', 'boot', 'long')['text'] == 'The boot button on pocket was pressed and held.'
        assert voice.pressed('pocket', 'BOOT', 'double')['text'] == 'The boot button on pocket was pressed twice.'
    assert run_turn.call_count == 2


def test_a_press_left_to_the_board_starts_no_turn(home):
    _with_button()
    _bind(boot_long={'do': 'clear'})
    with _streaming(['ok']) as run_turn:
        assert voice.pressed('pocket', 'boot', 'long') == {'ok': True, 'accepted': False}
        assert voice.pressed('pocket', 'boot', 'short') == {'ok': True, 'accepted': False}
        assert 'short, long, double' in voice.pressed('pocket', 'boot', 'triple')['error']
    run_turn.assert_not_called()


def test_a_board_with_a_speaker_and_no_keyboard_hears_the_reply(home):
    _with_button('speaker', 'light', 'buttons')
    _bind(boot_short={'do': 'tell', 'text': 'hello'})
    with _streaming(['hi']) as run_turn, patch.object(voice, 'Speech') as speech:
        speech.return_value.ready = False
        speech.return_value.problem = None
        speech.return_value.spoken = 0
        out = voice.pressed('pocket', 'boot', 'short')
    assert out['accepted'] and 'msg' not in out
    assert run_turn.call_args.args[1].startswith('[Button pressed on device "pocket" (Pocket). Your reply is spoken aloud there.]')
    assert run_turn.call_args.kwargs['speak'] == 'device:pocket'


def test_a_board_with_only_a_button_keeps_the_reply_in_the_chat(home):
    _with_button('light', 'buttons')
    _bind(boot_short={'do': 'tell', 'text': 'hello'})
    spy, seen = _cues()
    with spy, _streaming(['hi']) as run_turn, patch.object(voice, 'Speech') as speech:
        out = voice.pressed('pocket', 'boot', 'short')
    speech.assert_not_called()
    assert out['accepted'] and 'msg' not in out
    assert 'It can neither speak nor show a reply.]' in run_turn.call_args.args[1]
    assert run_turn.call_args.kwargs['speak'] is None
    assert [s for _, s, _ in seen] == ['thinking', 'idle']


def test_the_try_button_presses_from_the_page(home):
    _with_button()
    _bind(boot_short={'do': 'tell', 'text': 'hello'})
    with _streaming(['hi']):
        said, ok = engine.run('pocket', 'buttons', 'press', 'boot', owner=True)
        assert ok and said == 'Sent to the chat \'open-chat\': "hello"'
        said, ok = engine.run('pocket', 'buttons', 'press', 'boot long', owner=True)
        assert ok and said.startswith('Nothing is set to be sent for boot long')
        said, ok = engine.run('pocket', 'buttons', 'press', 'boot')
        assert not ok and 'only' in said                      # hers to see, not to press


# --- the chat it talks in --------------------------------------------------------------

def test_a_chat_that_is_not_one_yet_is_made(home):
    _with_button()
    made = []
    home.sm.chat_exists.side_effect = lambda name: name in made
    home.sm.create_chat.side_effect = lambda name: made.append(name) or True
    home.sm.get_settings_for.side_effect = lambda chat: {'chat': chat} if chat in made else None
    engine.update('pocket', parts={'satellite': {'chat': 'Living Room', 'buttons': {'boot.short': {'do': 'tell'}}}})
    assert engine.get('pocket')['parts'][0]['config']['chat'] == 'living_room'      # kept as the chat will be named
    with _streaming(['hi']) as run_turn:
        out = voice.pressed('pocket', 'boot', 'short')
    assert out['chat'] == 'living_room' and made == ['living_room']
    assert run_turn.call_args.args[0] == 'living_room'
    with _streaming(['hi']):
        assert voice.typed('pocket', 'again')['chat'] == 'living_room'
    assert made == ['living_room']                                                # made once


def test_a_chat_that_exists_keeps_its_name_as_typed(home):
    home.sm.chat_exists.side_effect = lambda name: name == 'Old Name'
    assert voice.chat_named('  Old Name ') == 'Old Name'
    assert voice.chat_named('New One!') == 'new_one' and voice.chat_named('') == ''


def test_a_locked_chat_is_not_made_again(home):
    _with_button()
    home.sm.chat_exists.side_effect = lambda name: True
    home.sm.get_settings_for.side_effect = lambda chat: None
    _bind(boot_short={'do': 'tell'})
    engine.update('pocket', parts={'satellite': {'chat': 'vault'}})
    assert voice.pressed('pocket', 'boot', 'short') == {'ok': False, 'error': "The chat 'vault' set for 'pocket' cannot be opened."}
    home.sm.create_chat.assert_not_called()


# --- the door --------------------------------------------------------------------------------

def test_the_press_door_takes_the_boards_key(home):
    _with_button()
    _bind(boot_short={'do': 'tell', 'text': 'hello'})

    class Req:
        def __init__(self, key, body):
            self.headers = {'authorization': f'Bearer {key}'}
            self.client = type('C', (), {'host': '192.168.1.50'})()
            self._body = body

        async def json(self):
            return self._body

    with _streaming(['hi']), patch.object(routes, 'check_endpoint_rate'), patch.object(routes, 'get_client_ip', lambda r: '192.168.1.50'), \
            patch('core.devices.health.seen'), patch.object(engine, 'learned'):
        out = asyncio.run(routes.devices_press('pocket', Req('pocket-key-12345678', {'button': 'boot', 'how': 'short'})))
        assert out['accepted'] and out['text'] == 'hello'
        try:
            asyncio.run(routes.devices_press('pocket', Req('wrong', {'button': 'boot'})))
            assert False, 'a wrong key got in'
        except routes.HTTPException as e:
            assert e.status_code == 401


# --- a press that is Sapphire's work, with no chat turn (the backup stick) ---------------

def test_a_press_the_board_calls_backup_starts_one_and_no_turn(home):
    """The stick's own short press is 'backup': it names the job, nothing is
    set on the page, and no chat is touched."""
    _with_button('light', 'buttons', 'storage')
    with _streaming(['ok']) as run_turn, patch('core.devices.storage.target_for', return_value=object()) as target, \
         patch('core.devices.storage.backup_now', return_value=('Backing up to pocket now', True)) as now:
        out = voice.pressed('pocket', 'boot', 'short', 'backup')
    assert out == {'ok': True, 'accepted': True, 'job': 'backup', 'said': 'Backup is on its way'}
    target.assert_called_once_with('pocket')
    now.assert_called_once()
    run_turn.assert_not_called()


def test_what_the_page_set_wins_over_the_boards_word(home):
    _with_button('light', 'buttons', 'storage')
    _bind(boot_short={'do': 'screen'})
    with patch('core.devices.storage.backup_now') as now:
        assert voice.pressed('pocket', 'boot', 'short', 'backup') == {'ok': True, 'accepted': False}
    now.assert_not_called()
    _bind(boot_long={'do': 'backup'})                       # set on the page: the board need not say it
    with patch('core.devices.storage.target_for', return_value=object()), \
         patch('core.devices.storage.backup_now', return_value=('A backup to pocket is already on its way (3s in).', True)):
        assert voice.pressed('pocket', 'boot', 'long')['said'] == 'A backup is already on its way'


def test_a_backup_press_with_no_card_says_so_and_a_made_up_job_does_nothing(home):
    _with_button('light', 'buttons')
    with patch('core.devices.storage.target_for', return_value=None):
        assert voice.pressed('pocket', 'boot', 'short', 'backup') == \
            {'ok': True, 'accepted': False, 'job': 'backup', 'said': 'No card to back up to'}
    assert voice.pressed('pocket', 'boot', 'short', 'format') == {'ok': True, 'accepted': False}


def test_a_board_with_only_buttons_still_shows_its_key_field(home):
    field = next(f for f in sat.SPEC['config_schema'] if f['key'] == 'voice_key')
    assert 'buttons' in field['capability']


def test_the_card_check_is_among_the_readings(home):
    said = {'ok': True, 'has': ['storage'], 'storage': {'mounted': True, 'free_bytes': 2 ** 34, 'total_bytes': 2 ** 35, 'count': 14,
            'checks': {'running': True, 'done': 3, 'of': 14, 'damaged': 1, 'unchecked': 0}}}
    with patch.object(sat, '_health', return_value=said):
        r = sat.status({'id': 'pocket'}, {'url': 'http://10.0.0.9'}, {})['readings']
    assert r['backups'] == '14 on the card' and r['damaged backups'].startswith('1:')
    assert r['card check'] == 'reading every backup back, 3 of 14'
