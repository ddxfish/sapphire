"""Easy setup (2026-11-15, tmp/easy-mode-plan.md): what core/setup_easy.py picks
for a computer, what a second run keeps, the Brain row's words, the page's
speaker and microphone tests, and the switch job's wake step (a provider that is
not there yet is installed and turned on first, only when asked)."""
from types import SimpleNamespace

import numpy as np
import pytest

import core.api_fastapi  # noqa: F401 - the app wires the routes; a route module imported first is a circular import
from core import provider_switch, setup_easy


@pytest.fixture
def box(monkeypatch):
    """A computer described by a dict: no probe, no settings file, no plugins."""
    b = {'gpu': (True, 'RTX 4090', 'ok'), 'piper': {'ready': False, 'note': 'Installs piper-tts, then turns on the Piper plugin.'},
         'mic': True, 'settings': {}, 'managed': False, 'docker': False}
    from core.settings_manager import settings
    monkeypatch.setattr(settings, 'get', lambda key, default=None: b['settings'].get(key, default))
    monkeypatch.setattr(settings, 'is_managed', lambda: b['managed'])
    monkeypatch.setattr(settings, 'is_docker', lambda: b['docker'])
    monkeypatch.setattr(setup_easy, '_gpu', lambda: b['gpu'])
    monkeypatch.setattr(setup_easy, '_piper', lambda: b['piper'])
    monkeypatch.setattr(setup_easy, '_has_microphone', lambda: b['mic'])
    monkeypatch.setattr(setup_easy, '_whisper_can_use_a_card', lambda: b.get('cublas', True))
    monkeypatch.setattr(setup_easy, '_free', lambda card: b.get('free'))
    monkeypatch.setattr(setup_easy, '_has_speakers', lambda: True)
    monkeypatch.setattr(setup_easy, '_no_sound', lambda: b.get('no_sound', ''))
    monkeypatch.setattr(setup_easy, '_label', lambda key, value: str(value).title())
    monkeypatch.setattr(setup_easy, '_labels', lambda: {'tts': {}, 'stt': {}})
    monkeypatch.setattr(setup_easy, '_phrase', lambda model: str(model).replace('_', ' '))
    monkeypatch.setattr(setup_easy, '_sound', lambda: True)
    return b


def _rows(plan):
    return {r['id']: r for r in plan['rows']}


def test_a_usable_card_gets_kokoro_and_whisper_on_it(box):
    rows = _rows(setup_easy.plan())
    assert rows['voice']['settings'] == {'TTS_PROVIDER': 'kokoro', 'KOKORO_DEVICE': 'cuda'}
    assert 'RTX 4090' in rows['voice']['text'] and rows['voice']['tab'] == 'tts'
    assert rows['ears']['settings'] == {'STT_PROVIDER': 'faster_whisper', 'FASTER_WHISPER_DEVICE': 'cuda'}
    assert rows['wake']['settings']['WAKE_WORD_ENABLED'] is True and 'Hey Sapphire' in rows['wake']['text']
    assert all(r['why'] for r in rows.values())


def test_no_usable_card_gets_piper_and_whisper_on_the_processor(box):
    box['gpu'] = (False, '', 'no_nvidia')
    rows = _rows(setup_easy.plan())
    assert rows['voice']['settings'] == {'TTS_PROVIDER': 'piper'} and 'piper-tts' in rows['voice']['note']
    assert rows['ears']['settings']['FASTER_WHISPER_DEVICE'] == 'cpu'
    box['gpu'] = (False, 'RTX 4090', 'cpu_torch')          # a card torch cannot use: the same picks, said honestly
    voice = _rows(setup_easy.plan())['voice']
    assert voice['settings'] == {'TTS_PROVIDER': 'piper'} and 'RTX 4090' in voice['why']
    box['piper'] = None                                     # an install without the Piper plugin
    assert _rows(setup_easy.plan())['voice']['settings'] == {'TTS_PROVIDER': 'kokoro', 'KOKORO_DEVICE': 'cpu'}


def test_wake_word_needs_a_microphone_and_is_off_in_a_container(box):
    box['mic'] = False
    wake = _rows(setup_easy.plan())['wake']
    assert wake['settings'] == {'WAKE_WORD_ENABLED': False} and 'microphone' in wake['why'].lower()
    box.update(mic=True, docker=True)
    assert _rows(setup_easy.plan())['wake']['settings'] == {'WAKE_WORD_ENABLED': False}


def test_a_second_run_says_what_it_would_change(box):
    box['gpu'] = (False, '', 'no_nvidia')
    box['settings'] = {'TTS_PROVIDER': 'kokoro', 'STT_PROVIDER': 'faster_whisper', 'SETUP_COMPLETE': True}
    rows = _rows(setup_easy.plan())
    assert rows['voice']['was'] == 'Kokoro' and rows['ears']['was'] == ''
    assert rows['wake']['was'] == 'Off'                     # off after a finished setup is a choice
    box['settings'] = {'TTS_PROVIDER': 'kokoro'}            # never finished, but set up by hand: the same
    assert _rows(setup_easy.plan())['wake']['was'] == 'Off'
    box['settings'] = {}
    assert _rows(setup_easy.plan())['wake']['was'] == ''    # a fresh install's off is only the default


def test_a_second_run_keeps_what_is_set_and_offers_the_pick(box):
    box['settings'] = {'TTS_PROVIDER': 'audio8', 'STT_PROVIDER': 'faster_whisper', 'FASTER_WHISPER_DEVICE': 'cuda',
                       'WAKE_WORD_ENABLED': False, 'SETUP_COMPLETE': True}
    plan = setup_easy.plan()
    rows = _rows(plan)
    assert plan['rerun'] is True
    assert rows['voice']['current'] == 'Audio8' and rows['voice']['settings']['TTS_PROVIDER'] == 'kokoro'
    assert rows['ears']['current'] == ''                    # already the pick: nothing to offer
    assert rows['wake']['current'] == 'Off' and rows['wake']['settings']['WAKE_WORD_ENABLED'] is True
    box['settings'].update(TTS_PROVIDER='kokoro', KOKORO_DEVICE='cpu', WAKE_WORD_ENABLED=True, WAKEWORD_MODEL='hey_sapphire')
    rows = _rows(setup_easy.plan())
    assert rows['voice']['current'] == 'Kokoro on the processor'      # the same engine, said apart from the pick
    assert rows['wake']['current'] == ''
    box['settings']['SETUP_COMPLETE'] = False               # a first setup: the picks are the page
    plan = setup_easy.plan()
    assert plan['rerun'] is False and all(r['current'] == '' for r in plan['rows'])


def test_managed_has_no_voice_rows(box):
    box['managed'] = True
    assert setup_easy.plan()['rows'] == []


@pytest.fixture
def launcher(monkeypatch):
    from core import launcher_llm
    from core.chat.llm_providers import resolve as r
    from core.settings_manager import settings
    st = {'status': {'provider': None, 'state': None}, 'sel': None, 'asked': 0, 'cli': None}
    monkeypatch.setattr(launcher_llm, 'adopt', lambda: None)
    monkeypatch.setattr(launcher_llm, 'cli_status', lambda: st['cli'])
    monkeypatch.setattr(launcher_llm, 'status', lambda: st['status'])
    monkeypatch.setattr(settings, 'is_managed', lambda: False)
    monkeypatch.setattr(settings, 'is_docker', lambda: False)

    def fake(primary, **kw):
        st['asked'] += 1
        if st['sel'] is None:
            raise r.NoProvidersAvailable('nothing')
        return st['sel']
    monkeypatch.setattr(r, 'resolve', fake)
    return st


class _Sel:
    def __init__(self, key, name, model):
        self.key, self.display_name, self.effective_model = key, name, model


def test_brain_row_words(launcher):
    assert setup_easy.brain()['state'] == 'none' and 'Launcher' in setup_easy.brain()['hint']
    launcher['status'] = {'provider': 'sapphire_launcher', 'state': 'stopped'}
    assert setup_easy.brain()['state'] == 'stopped'
    launcher['status'] = {'provider': 'sapphire_launcher', 'state': 'loading'}
    assert setup_easy.brain()['state'] == 'loading'
    launcher['status'] = {'provider': 'sapphire_launcher', 'state': 'running', 'model': 'qwen-4b', 'n_ctx': 8192}
    launcher['sel'] = _Sel('sapphire_launcher', 'Sapphire Launcher', 'qwen-4b')
    b = setup_easy.brain()
    assert b['state'] == 'running' and 'qwen-4b' in b['text'] and '8K' in b['text']
    launcher['sel'] = _Sel('claude', 'Claude', 'claude-x')        # Auto answers with another provider
    assert setup_easy.brain() == {'state': 'ok', 'hint': '', 'text': 'Claude: claude-x'}


def test_the_light_poll_asks_auto_only_once_the_launcher_runs(launcher):
    launcher['status'] = {'provider': 'sapphire_launcher', 'state': 'stopped'}
    assert setup_easy.brain(light=True)['state'] == 'stopped' and launcher['asked'] == 0
    launcher['status'] = {'provider': 'sapphire_launcher', 'state': 'running', 'model': 'm'}
    launcher['sel'] = _Sel('sapphire_launcher', 'Sapphire Launcher', 'm')
    assert setup_easy.brain(light=True)['state'] == 'running' and launcher['asked'] == 1


# ── the switch job's wake step ──

@pytest.fixture
def wake(monkeypatch):
    """extras, the loader and the toggle as dicts: nothing is installed or toggled."""
    from core import extras
    from core.plugin_loader import plugin_loader
    import core.routes.plugins as plugin_routes
    from core.tts.providers import tts_registry
    st = {'installed': set(), 'fail': None, 'enabled': False, 'toggled': [], 'started': [], 'missing': ['piper-tts']}
    manifest = {'pip_dependencies': ['piper-tts'], 'capabilities': {'providers': {'tts': {'key': 'piper'}}}}

    def start(name):
        st['started'].append(name)
        if st['fail']:
            raise extras.ExtraError(st['fail'])
        st['installed'].add(name)
        st['missing'] = []
    monkeypatch.setattr(extras, 'installed', lambda name: name in st['installed'])
    monkeypatch.setattr(extras, 'start', start)
    monkeypatch.setattr(extras, 'state', lambda name: {'state': 'done'})
    monkeypatch.setattr(plugin_loader, 'get_all_plugin_info', lambda: [{'name': 'piper', 'manifest': manifest, 'enabled': st['enabled']}])
    monkeypatch.setattr(plugin_loader, 'get_plugin_info', lambda name: {'name': name, 'manifest': manifest, 'enabled': st['enabled']})
    monkeypatch.setattr(plugin_loader, '_check_dependencies', lambda m: list(st['missing']))

    def toggle(name, request, _=None):
        st['toggled'].append(name)
        st['enabled'] = True
        return {'enabled': True}
    monkeypatch.setattr(plugin_routes, 'toggle_plugin', toggle)
    monkeypatch.setattr(tts_registry, 'has_key', lambda key: st['enabled'])
    return st


def test_wake_installs_a_plugins_packages_and_turns_it_on(wake):
    assert provider_switch._wake('TTS_PROVIDER', 'piper') == (True, '')
    assert wake['started'] == ['plugin:piper'] and wake['toggled'] == ['piper']
    assert provider_switch._wake('TTS_PROVIDER', 'piper') == (True, '')      # already there: nothing again
    assert wake['started'] == ['plugin:piper'] and wake['toggled'] == ['piper']


def test_wake_says_why_when_the_install_fails(wake):
    wake['fail'] = 'pip failed (exit 1).'
    ok, why = provider_switch._wake('TTS_PROVIDER', 'piper')
    assert ok is False and 'pip failed' in why and wake['toggled'] == []


def test_wake_leaves_core_providers_and_off_alone(wake):
    wake['installed'] = {'tts', 'stt', 'wakeword'}
    for key, value in (('TTS_PROVIDER', 'kokoro'), ('STT_PROVIDER', 'faster_whisper'), ('WAKE_WORD_ENABLED', True),
                       ('TTS_PROVIDER', 'none'), ('WAKEWORD_MODEL', 'hey_sapphire'), ('EMBEDDING_PROVIDER', 'local')):
        assert provider_switch._wake(key, value) == (True, '')
    assert wake['started'] == [] and wake['toggled'] == []
    wake['installed'] = set()                                               # a minimal install: the engine's own set
    assert provider_switch._wake('STT_PROVIDER', 'faster_whisper') == (True, '')
    assert wake['started'] == ['stt']


def test_a_switch_wakes_only_when_asked(monkeypatch, wake):
    seen = []
    monkeypatch.setattr(provider_switch, '_wake', lambda key, value, rid=None: seen.append(key) or (True, ''))
    monkeypatch.setattr(provider_switch, '_apply', lambda system, key, value, rid=None: (True, ''))
    monkeypatch.setattr(provider_switch, '_system', lambda: object())
    monkeypatch.setattr(provider_switch, '_reapply_chat', lambda system: None)
    monkeypatch.setattr(provider_switch, '_publish', lambda *a, **k: None)
    monkeypatch.setattr(provider_switch.notify, 'finish', lambda *a, **k: None)
    from core.settings_manager import settings
    monkeypatch.setattr(settings, 'get', lambda key, default=None: 'none')
    monkeypatch.setattr(settings, 'set', lambda *a, **k: None)
    monkeypatch.setattr(settings, 'validate_tier', lambda key: 'hot')
    provider_switch._run_one('TTS_PROVIDER', 'piper', 'r1', False)
    assert seen == []
    provider_switch._run_one('TTS_PROVIDER', 'piper', 'r1', False, True)
    assert seen == ['TTS_PROVIDER']


# ── the page's tests: this computer's speakers and microphone ──

@pytest.fixture
def sound(monkeypatch):
    """A computer with a voice, ears, speakers and a microphone, all as fakes."""
    from core.settings_manager import settings
    import core.audio
    import core.audio.backend
    import core.routes.system as system_routes
    import core.stt.utils
    import core.tts.tts_client
    import core.voice_privacy
    st = {'settings': {'TTS_PROVIDER': 'kokoro', 'STT_PROVIDER': 'faster_whisper'}, 'busy': set(), 'tones': [],
          'here': True, 'voice_ok': True, 'spoke': [], 'ears': (True, ''), 'loud': 5, 'reads': 0, 'clock': 0.0,
          'open_fails': False, 'wake': [], 'gate': '', 'plays': True, 'stopped': 0, 'quiet': 10, 'scene': [], 'alerts': []}
    monkeypatch.setattr(settings, 'get', lambda key, default=None: st['settings'].get(key, default))
    monkeypatch.setattr(provider_switch, 'busy', lambda key: {'value': 'x'} if key in st['busy'] else None)
    monkeypatch.setattr(system_routes, 'play_test_tone',
                        lambda system, index=None, *a, **k: (st['tones'].append(index), st['scene'].append(k.get('frequency', 'tone')))
                        and {'success': True})
    monkeypatch.setattr(system_routes, '_quiesce_wakeword', lambda system: st['wake'].append('paused') or (True, False))
    monkeypatch.setattr(system_routes, '_resume_wakeword', lambda system, a, b: st['wake'].append('resumed'))
    monkeypatch.setattr(core.tts.tts_client, 'local_playback_on', lambda: st['here'])
    monkeypatch.setattr(core.stt.utils, 'can_transcribe', lambda client: st['ears'])
    monkeypatch.setattr(core.voice_privacy, 'stt_gate_reason', lambda chat_settings: '')
    monkeypatch.setattr(core.voice_privacy, 'tts_gate_reason', lambda chat_settings: st['gate'])
    monkeypatch.setattr(setup_easy, 'SPEAK_S', 0.2)
    monkeypatch.setattr(setup_easy, 'time', SimpleNamespace(monotonic=lambda: st['clock'], sleep=lambda s: None))

    class Stream:
        def __init__(self, **kw):
            if st['open_fails']:
                raise OSError('Device unavailable')
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def read(self, n):              # each block is a tenth of a second: the first `loud` carry a voice
            st['reads'] += 1
            st['clock'] += 0.1
            return np.full((n, 1), 9000 if st['reads'] <= st['loud'] else st['quiet'], dtype=np.int16), False

    mic = SimpleNamespace(device_index=1, sample_rate=16000, channels=1, blocksize=1600, device_name='Desk Mic',
                          needs_stereo_downmix=False)
    monkeypatch.setattr(core.audio, 'get_device_manager', lambda: SimpleNamespace(
        find_output_device=lambda: (3, 48000, 'Speakers'), find_input_device=lambda **kw: mic))
    monkeypatch.setattr(core.audio.backend, 'sd', SimpleNamespace(InputStream=Stream), raising=False)
    def speak(text):                    # the real one starts a thread; playback starting is the proof
        from core.event_bus import publish, Events
        st['spoke'].append(text)
        if st['plays']:
            publish(Events.TTS_PLAYING, {'surface': 'local'})
        return True

    def speak_sync(text):               # blocks while she says it
        from core.event_bus import publish, Events
        st['alerts'].append(text)
        st['scene'].append('spoken')
        if st['plays']:
            publish(Events.TTS_PLAYING, {'surface': 'local'})
        return True

    st['system'] = SimpleNamespace(
        tts=SimpleNamespace(provider=SimpleNamespace(is_available=lambda: st['voice_ok'], _health_why='its model is loading'),
                            speak=speak, speak_sync=speak_sync, stop=lambda: st.__setitem__('stopped', st['stopped'] + 1),
                            output_device_name='Speakers'),
        whisper_client=SimpleNamespace(transcribe_file=lambda path: 'hello there'),
        llm_chat=SimpleNamespace(session_manager=SimpleNamespace(get_chat_settings=lambda: {})))
    return st


def test_the_speaker_test_is_her_voice_when_she_has_one(sound):
    r = setup_easy.say(sound['system'])
    assert r['ok'] and r['kind'] == 'voice' and r['device'] == 'Speakers'
    assert sound['spoke'] == [setup_easy.LINE] and sound['tones'] == []


def test_the_speaker_test_is_a_tone_and_a_reason_until_then(sound):
    sound['settings']['TTS_PROVIDER'] = 'none'
    r = setup_easy.say(sound['system'])
    assert r['ok'] and r['kind'] == 'tone' and r['why'] == 'Her voice is not set up yet.' and r['busy'] is False
    assert sound['tones'] == [3] and sound['spoke'] == []       # the speakers she will use, nothing installed
    sound['busy'].add('TTS_PROVIDER')
    r = setup_easy.say(sound['system'])
    assert r['busy'] is True and 'still being set up' in r['why']
    sound['busy'].clear()
    sound['settings']['TTS_PROVIDER'] = 'kokoro'
    sound['voice_ok'] = False
    assert setup_easy.say(sound['system'])['why'] == 'Her voice is not ready: its model is loading.'
    sound['voice_ok'], sound['here'] = True, False
    assert 'Play speech here' in setup_easy.say(sound['system'])['why'] and sound['spoke'] == []


def test_a_sound_servers_name_is_said_as_the_default_device():
    assert setup_easy._device('pipewire', 'microphone') == 'the default microphone'
    assert setup_easy._device('default', 'speakers') == 'the default speakers'
    assert setup_easy._device('Desk Mic', 'microphone') == 'Desk Mic'


def _hear(sound):
    events = []
    setup_easy._listen(sound['system'], events.append)
    return events


def test_the_microphone_test_ends_after_speech_and_says_the_words(sound):
    events = _hear(sound)
    assert events[:2] == [{'warn': True, 'spoken': True}, {'count': 3}] and {'thinking': True} in events
    assert any(e.get('level', 0) > 0.2 for e in events)
    assert events[-1] == {'heard': True, 'text': 'hello there', 'ears': '', 'busy': False, 'done': True}
    assert sound['reads'] < 20                                  # speech, a second of quiet, done: not the full take
    assert sound['wake'] == ['paused', 'resumed']


def test_the_microphone_test_says_silence_and_unready_ears_apart(sound):
    sound['loud'] = 0
    assert _hear(sound)[-1] == {'heard': False, 'done': True}
    assert sound['reads'] >= int(setup_easy.LISTEN_S / 0.1) - 1   # the whole take, then the verdict
    sound.update(loud=5, reads=0, clock=0.0, ears=(False, 'loading speech model'))
    sound['settings']['STT_PROVIDER'] = 'none'
    last = _hear(sound)[-1]
    assert last['heard'] is True and last['text'] == '' and last['ears'] == 'Her ears are not set up yet.'
    sound['settings']['STT_PROVIDER'] = 'faster_whisper'
    sound['busy'].add('STT_PROVIDER')
    sound.update(reads=0, clock=0.0)
    last = _hear(sound)[-1]
    assert last['busy'] is True and 'still being set up' in last['ears']


def test_a_microphone_that_will_not_open_still_gives_the_wake_word_back(sound):
    sound['open_fails'] = True
    last = _hear(sound)[-1]
    assert last['done'] is True and last['error']
    assert sound['wake'] == ['paused', 'resumed']
    assert setup_easy._LISTENING.acquire(blocking=False)        # and the next test may listen
    setup_easy._LISTENING.release()


def test_listen_streams_until_the_closing_event(sound):
    events = list(setup_easy.listen(sound['system']))
    assert events[:2] == [{'warn': True, 'spoken': True}, {'count': 3}] and events[-1]['done'] is True and events[-1]['text'] == 'hello there'
    assert sum(1 for e in events if e.get('done')) == 1


# ── the hunt (2026-11-15): every computer the page can meet, every way a test can end ──

GPUS = ((True, 'RTX 4090', 'ok'), (False, 'RTX 4090', 'cpu_torch'), (False, '', 'no_nvidia'), (False, '', 'unknown'))
PIPERS = (None, {'ready': True, 'note': ''}, {'ready': False, 'note': 'Turns on the Piper plugin.'})
HAD = ({},
       {'TTS_PROVIDER': 'audio8', 'STT_PROVIDER': 'none', 'WAKE_WORD_ENABLED': True, 'WAKEWORD_MODEL': 'computer'},
       {'TTS_PROVIDER': 'kokoro', 'KOKORO_DEVICE': 'cpu', 'STT_PROVIDER': 'faster_whisper', 'FASTER_WHISPER_DEVICE': 'cpu'},
       {'TTS_PROVIDER': 'kokoro', 'KOKORO_DEVICE': 'cuda', 'STT_PROVIDER': 'faster_whisper', 'FASTER_WHISPER_DEVICE': 'cuda'},
       {'TTS_PROVIDER': 'piper', 'STT_PROVIDER': 'faster_whisper'})


def _computers(box):
    """Every card, Piper state, microphone, container and earlier setup, on the real defaults."""
    import itertools
    from core.settings_manager import settings
    for gpu, piper, mic, docker, had in itertools.product(GPUS, PIPERS, (True, False), (False, True), HAD):
        box.update(gpu=gpu, piper=piper, mic=mic, docker=docker, settings={**settings._defaults, **had})
        yield f"gpu={gpu[2]} piper={piper and piper['ready']} mic={mic} docker={docker} had={had}"


def _free():
    """The microphone test's lock is not held."""
    ok = setup_easy._LISTENING.acquire(blocking=False)
    if ok:
        setup_easy._LISTENING.release()
    return ok


def test_every_pick_is_a_real_setting_this_computer_can_run(box):
    from core.settings_manager import settings
    for what in _computers(box):
        for r in setup_easy.plan()['rows']:
            assert r['title'] and r['text'] and r['why'] and r['settings'], what
            for key, value in r['settings'].items():
                assert key in settings._defaults, (what, key)
                assert type(value) is type(settings._defaults[key]), (what, key)
                assert box['gpu'][0] or value != 'cuda', (what, key)       # never the card torch cannot use
            if r['id'] == 'wake' and (box['docker'] or not box['mic']):
                assert r['settings'] == {'WAKE_WORD_ENABLED': False}, what


def test_after_finish_a_second_run_offers_nothing(box):
    for what in _computers(box):
        first = setup_easy.plan()
        assert first['rerun'] is False and all(r['current'] == '' for r in first['rows']), what
        for r in first['rows']:                                 # Finish saves every row
            box['settings'].update(r['settings'])
        box['settings']['SETUP_COMPLETE'] = True
        again = setup_easy.plan()
        assert again['rerun'] is True, what
        assert [(r['id'], r['current'], r['was']) for r in again['rows']] == [(r['id'], '', '') for r in first['rows']], what
        assert [r['settings'] for r in again['rows']] == [r['settings'] for r in first['rows']], what


def test_use_it_settles_its_own_row_and_no_other(box):
    for what in _computers(box):
        box['settings']['SETUP_COMPLETE'] = True
        before = _rows(setup_easy.plan())
        for rid, r in before.items():
            if not r['current']:
                continue
            assert r['current'] != r['text'], (what, rid)       # never "X. I'd pick: X"
            kept = dict(box['settings'])
            box['settings'].update(r['settings'])
            after = _rows(setup_easy.plan())
            assert after[rid]['current'] == '', (what, rid)
            assert all(after[o]['current'] == before[o]['current'] for o in before if o != rid), (what, rid)
            box['settings'] = kept


def test_a_second_microphone_test_is_refused_while_one_runs(sound, monkeypatch):
    import threading
    import core.audio
    inside, go = threading.Event(), threading.Event()
    dm = core.audio.get_device_manager()

    def find(**kw):
        inside.set()
        go.wait(5)
        return dm.find_input_device(**kw)
    monkeypatch.setattr(core.audio, 'get_device_manager', lambda: SimpleNamespace(find_input_device=find))
    first = []
    t = threading.Thread(target=setup_easy._listen, args=(sound['system'], first.append))
    t.start()
    assert inside.wait(5)
    assert _hear(sound) == [{'error': "She is already listening for a test.", 'done': True}]
    assert sound['wake'] == ['paused']                          # the refused one touched nothing
    go.set()
    t.join(5)
    assert first[-1]['text'] == 'hello there' and sound['wake'] == ['paused', 'resumed'] and _free()


@pytest.mark.parametrize('where', ['pause', 'resume', 'read'])
def test_the_microphone_test_ends_once_and_frees_the_microphone_however_it_fails(sound, monkeypatch, where):
    import core.audio.backend
    import core.routes.system as system_routes

    def boom(*a, **k):
        raise RuntimeError('boom')
    if where == 'pause':
        monkeypatch.setattr(system_routes, '_quiesce_wakeword', boom)
    if where == 'resume':
        monkeypatch.setattr(system_routes, '_resume_wakeword', boom)
    if where == 'read':
        class Unplugged(core.audio.backend.sd.InputStream):
            def read(self, n):
                if sound['reads'] >= 2:
                    raise OSError('Input overflowed')
                return super().read(n)
        monkeypatch.setattr(core.audio.backend, 'sd', SimpleNamespace(InputStream=Unplugged))
    events = _hear(sound)
    assert sum(1 for e in events if e.get('done')) == 1 and events[-1].get('done') and events[-1]['error']
    assert _free()
    if where == 'read':
        assert sound['wake'] == ['paused', 'resumed']


def test_a_page_that_leaves_mid_take_still_gets_the_wake_word_back(sound):
    import threading
    stream = setup_easy.listen(sound['system'])
    assert next(stream) == {'warn': True, 'spoken': True}
    stream.close()
    for t in threading.enumerate():
        if t.name == 'easy-listen':
            t.join(5)
    assert sound['wake'] == ['paused', 'resumed'] and _free()


def test_a_private_chat_with_cloud_ears_keeps_the_take_on_this_computer(sound, monkeypatch):
    import core.voice_privacy
    sent = []
    sound['system'].whisper_client.transcribe_file = lambda path: sent.append(path) or 'leaked'
    monkeypatch.setattr(core.voice_privacy, 'stt_gate_reason', lambda chat_settings: 'This chat is private.')
    assert _hear(sound)[-1] == {'heard': True, 'text': '', 'ears': 'This chat is private.', 'busy': False, 'done': True}
    assert sent == []


def test_the_speaker_test_falls_to_a_tone_whatever_her_voice_does(sound, monkeypatch):
    import core.audio
    import core.routes.system as system_routes
    sound['system'].tts.speak = lambda text: False
    r = setup_easy.say(sound['system'])
    assert r['ok'] and r['kind'] == 'tone' and 'made no sound' in r['why'] and sound['tones'] == [3]

    def boom(text):
        raise RuntimeError('no model')
    sound['system'].tts.speak = boom
    r = setup_easy.say(sound['system'])
    assert r['kind'] == 'tone' and 'no model' in r['why']
    monkeypatch.setattr(core.audio, 'get_device_manager', lambda: SimpleNamespace(find_output_device=boom))
    r = setup_easy.say(sound['system'])
    assert r['ok'] and r['device'] == 'the default speakers' and sound['tones'][-1] is None
    monkeypatch.setattr(system_routes, 'play_test_tone', lambda *a, **k: {'success': False, 'error': 'No speakers.'})
    r = setup_easy.say(sound['system'])
    assert r['ok'] is False and r['error'] == 'No sound could be played on the default speakers.'


@pytest.fixture
def sound_routes(client, sound, monkeypatch):
    """The two sound routes on the fake computer, with a rate limiter of their own."""
    import collections
    from core import auth
    from core.api_fastapi import app
    from core.routes import setup as setup_routes
    monkeypatch.setattr(auth, '_endpoint_limits', collections.defaultdict(list))
    monkeypatch.setattr(setup_routes, '_at_the_page', lambda request: True)
    app.dependency_overrides[setup_routes._system] = lambda: sound['system']
    yield client
    app.dependency_overrides.pop(setup_routes._system, None)


def test_the_sound_routes_answer_and_share_one_rate_limit(sound_routes, sound):
    import json
    c, csrf = sound_routes
    head = {'X-CSRF-Token': csrf}
    r = c.post('/api/setup/easy/say', headers=head)
    assert r.status_code == 200 and r.json()['kind'] == 'voice'
    r = c.post('/api/setup/easy/listen', headers=head)
    assert r.status_code == 200 and r.headers['content-type'].startswith('application/x-ndjson')
    events = [json.loads(line) for line in r.text.splitlines()]
    assert events[:2] == [{'warn': True, 'spoken': True}, {'count': 3}] and events[-1]['done'] is True and events[-1]['text'] == 'hello there'
    for _ in range(28):
        assert c.post('/api/setup/easy/say', headers=head).status_code == 200
    assert c.post('/api/setup/easy/say', headers=head).status_code == 429
    assert c.post('/api/setup/easy/listen', headers=head).status_code == 429
    assert len(sound['spoke']) == 29 and sound['wake'] == ['paused', 'resumed']     # a refused call made no sound


def test_an_api_token_cannot_listen_to_the_room(client, sound, monkeypatch):
    """Logged in, but not a browser session at the page: the microphone stays shut."""
    from core.api_fastapi import app
    from core.routes import setup as setup_routes
    c, csrf = client
    app.dependency_overrides[setup_routes._system] = lambda: sound['system']
    try:
        r = c.post('/api/setup/easy/listen', headers={'X-CSRF-Token': csrf})
    finally:
        app.dependency_overrides.pop(setup_routes._system, None)
    assert r.status_code == 403 and sound['wake'] == [] and sound['reads'] == 0


def test_the_sound_routes_do_nothing_for_a_stranger(sound, monkeypatch):
    from fastapi.testclient import TestClient
    from core.api_fastapi import app
    from core.routes import setup as setup_routes
    app.dependency_overrides[setup_routes._system] = lambda: sound['system']
    try:
        c = TestClient(app, follow_redirects=False)
        for url in ('/api/setup/easy/say', '/api/setup/easy/listen'):
            assert c.post(url).status_code in (401, 403)
            assert c.post(url, headers={'X-CSRF-Token': 'guess'}).status_code in (401, 403)
    finally:
        app.dependency_overrides.pop(setup_routes._system, None)
    assert sound['spoke'] == [] and sound['tones'] == [] and sound['wake'] == []


def test_she_spoke_only_when_her_voice_reached_the_speakers(sound):
    """speak() says True the moment its thread starts: a voice that never plays is not "She spoke"."""
    sound['plays'] = False
    r = setup_easy.say(sound['system'])
    assert r['kind'] == 'tone' and 'made no sound' in r['why'] and sound['tones'] == [3]
    assert sound['spoke'] == [setup_easy.LINE] and sound['stopped'] == 1     # and it will not start after the tone
    sound['plays'], sound['gate'] = True, 'Private chat: a cloud voice stays silent'
    r = setup_easy.say(sound['system'])
    assert r['kind'] == 'tone' and r['why'].startswith('Private chat') and len(sound['spoke']) == 1
    assert 'sapphire' not in setup_easy.LINE.lower()        # her own wake word must not hear the line


def test_the_microphone_test_waits_while_she_is_speaking(sound):
    """Its warning would cut her reply short, and the take would transcribe her own voice (hunt 2026-11-16)."""
    sound['system'].tts._is_playing = True
    events = _hear(sound)
    assert events == [{'error': "She is speaking right now. Try again when she is done.", 'done': True}]
    assert sound['wake'] == [] and sound['alerts'] == [] and sound['reads'] == 0        # nothing paused, said or opened
    assert setup_easy._LISTENING.acquire(blocking=False)                                 # and the next test may run
    setup_easy._LISTENING.release()


def test_sound_in_a_browser_tab_or_on_a_call_is_not_these_speakers(sound):
    """tts_playing is published for the web page and for phone calls too: one of those inside the
    wait read as "She spoke" with silent speakers (hunt 2026-11-16)."""
    from core.event_bus import publish, Events
    said = []

    def speak(text):
        said.append(text)
        publish(Events.TTS_PLAYING, {'surface': 'web'})     # somebody's browser tab, at that moment
        return True
    sound['system'].tts.speak = speak
    r = setup_easy.say(sound['system'])
    assert said == [setup_easy.LINE] and r['kind'] == 'tone' and 'made no sound' in r['why']


def test_one_test_at_a_time(sound):
    assert setup_easy._LISTENING.acquire(blocking=False)
    try:
        r = setup_easy.say(sound['system'])
    finally:
        setup_easy._LISTENING.release()
    assert r['ok'] is False and 'Another test' in r['error'] and sound['spoke'] == [] and sound['tones'] == []
    assert setup_easy.say(sound['system'])['kind'] == 'voice' and _free()


def test_she_says_she_will_listen_and_beeps_three_times_first(sound):
    """Krem 2026-11-15: one sentence in her voice, a beep a second for three seconds, then
    the microphone; a ping and "Done" when it is closed again."""
    events = _hear(sound)
    assert sound['scene'] == ['spoken', 880, 880, 880,          # the warning, then the take,
                              880, 'spoken']                     # then a ping and "Done"
    assert sound['alerts'] == [setup_easy.WARN_LINE, setup_easy.DONE_LINE] and '3 seconds' in setup_easy.WARN_LINE
    names = [next(iter(e)) for e in events]
    assert names[:5] == ['warn', 'count', 'count', 'count', 'mic'] and [e['count'] for e in events[1:4]] == [3, 2, 1]
    assert names.index('clear') > max(i for i, n in enumerate(names) if n == 'level')   # said when the microphone is closed,
    assert names.index('clear') < names.index('thinking')                               # before the words are made
    assert sound['wake'] == ['paused', 'resumed']               # the wake word does not hear the warning
    assert 'sapphire' not in setup_easy.WARN_LINE.lower()


def test_with_no_voice_the_beeps_stand_alone(sound):
    sound['settings']['TTS_PROVIDER'] = 'none'
    _hear(sound)
    assert sound['scene'] == [880, 880, 880, 880, 660] and sound['alerts'] == []     # three beeps; then a ping and a falling tone
    sound['settings']['TTS_PROVIDER'] = 'kokoro'
    sound.update(plays=False, scene=[], reads=0, clock=0.0)
    _hear(sound)
    assert sound['scene'] == ['spoken', 880, 880, 880, 880, 'spoken', 660]           # asked to say "Done", nothing played


def test_a_refused_take_gives_no_warning(sound):
    assert setup_easy._LISTENING.acquire(blocking=False)
    try:
        _hear(sound)
    finally:
        setup_easy._LISTENING.release()
    assert sound['scene'] == [] and sound['wake'] == []


def test_a_microphone_that_sends_pure_silence_is_muted_or_blocked(sound):
    sound.update(loud=0, quiet=0)
    last = _hear(sound)[-1]
    assert last['heard'] is False and 'muted or blocked' in last['why'] and 'Desk Mic' in last['why']


def test_trouble_after_a_good_take_is_her_ears_not_the_microphone(sound):
    def boom(path):
        raise TimeoutError('the model timed out')
    sound['system'].whisper_client.transcribe_file = boom
    last = _hear(sound)[-1]
    assert last['heard'] is True and 'error' not in last and 'not your microphone' in last['ears']
    assert sound['wake'] == ['paused', 'resumed'] and _free()


def test_only_real_inputs_count_as_a_microphone(monkeypatch):
    import core.audio.device_manager as dm
    names = []
    monkeypatch.setattr(dm, 'get_device_manager', lambda: SimpleNamespace(
        get_input_devices=lambda: [SimpleNamespace(name=n) for n in names]))
    names[:] = ['default', 'pipewire', 'pulse', 'Microsoft Sound Mapper - Input', 'Primary Sound Capture Driver']
    assert setup_easy._has_microphone() is False            # the sound server's doors, nothing plugged in
    names.append('Speakers (Realtek(R) Audio) [Loopback]')  # Windows lists what the speakers play as an input
    assert setup_easy._has_microphone() is False            # a wake word there would listen to her own voice
    names.append('Microphone (Realtek(R) Audio)')
    assert setup_easy._has_microphone() is True
    assert setup_easy._device('Microsoft Sound Mapper - Output', 'speakers') == 'the default speakers'


def test_whisper_goes_on_the_card_only_where_its_library_is(box):
    """Linux with PyPI's torch (CUDA 13): Kokoro can use the card, Whisper's engine cannot. The row said
    "on your card", it landed on the processor, and a warning fired right after Finish (hunt 2026-11-16)."""
    box['gpu'] = (True, 'RTX 3070', 'ok')
    assert _rows(setup_easy.plan())['ears']['text'] == 'Whisper on your RTX 3070'
    box['cublas'] = False
    rows = _rows(setup_easy.plan())
    assert rows['ears']['text'] == 'Whisper on the processor'
    assert rows['ears']['settings'] == {'STT_PROVIDER': 'faster_whisper', 'FASTER_WHISPER_DEVICE': 'cpu'}     # no warning row
    assert '550 MB' in rows['ears']['note'] and 'Settings › STT' in rows['ears']['note']
    assert rows['voice']['settings']['KOKORO_DEVICE'] == 'cuda'            # her voice is torch's: still on the card


def test_no_sound_system_says_so_and_a_waiting_card_keeps_its_reminder(box):
    box['no_sound'] = "Sapphire cannot reach this computer's sound system (PortAudio library not found)."
    wake = _rows(setup_easy.plan())['wake']
    assert wake['settings'] == {'WAKE_WORD_ENABLED': False} and 'sound system' in wake['why']
    box['no_sound'] = ''
    box['gpu'] = (False, 'RTX 4070', 'cpu_torch')           # a card that only waits for its fix
    rows = _rows(setup_easy.plan())
    assert rows['ears']['settings'] == {'STT_PROVIDER': 'faster_whisper'}       # 'cpu' would read as a choice
    assert 'RTX 4070' in rows['voice']['why'] and 'torch' not in rows['voice']['why']
    box['gpu'] = (False, '', 'unknown')
    assert 'could not be checked' in _rows(setup_easy.plan())['voice']['why']
    box.update(gpu=(False, '', 'no_nvidia'), docker=True)   # a container keeps no pip install
    assert _rows(setup_easy.plan())['voice']['settings'] == {'TTS_PROVIDER': 'kokoro', 'KOKORO_DEVICE': 'cpu'}


def test_a_running_launcher_is_never_called_stopped(launcher):
    launcher['status'] = {'provider': 'sapphire_launcher', 'state': 'running', 'model': 'm'}
    b = setup_easy.brain()                                  # Auto's quick look missed it
    assert b['state'] == 'loading' and 'starting' in b['text']


def test_a_failed_install_leaves_the_running_provider_alone(monkeypatch, wake):
    """Its packages never arrived, so nothing was switched: no re-make of what works."""
    recovered, saved, rows = [], [], []
    monkeypatch.setattr(provider_switch, '_wake', lambda key, value, rid=None: (False, 'pip failed (exit 1).'))
    monkeypatch.setattr(provider_switch, '_recover', lambda *a: recovered.append(a) or 'none')
    monkeypatch.setattr(provider_switch, '_system', lambda: object())
    monkeypatch.setattr(provider_switch, '_publish', lambda *a, **k: None)
    monkeypatch.setattr(provider_switch.notify, 'finish', lambda rid, **k: rows.append(k))
    from core.settings_manager import settings
    monkeypatch.setattr(settings, 'get', lambda key, default=None: 'audio8')
    monkeypatch.setattr(settings, 'set', lambda *a, **k: saved.append(a))
    monkeypatch.setattr(settings, 'validate_tier', lambda key: 'hot')
    provider_switch._run_one('TTS_PROVIDER', 'piper', 'r1', True, True)
    assert recovered == [] and saved == []
    assert rows[0]['failed'] is True and rows[0]['body'].startswith('pip failed (exit 1). Kept ')


def test_ears_are_set_up_before_the_voice_and_the_wake_word_last(monkeypatch):
    ran = []
    monkeypatch.setattr(provider_switch, '_run', lambda fresh, persist, wake=False: ran.extend(k for k, _, _ in fresh))
    monkeypatch.setattr(provider_switch.notify, 'begin', lambda *a, **k: 'row')
    monkeypatch.setattr(provider_switch, '_inflight', {})
    provider_switch.start([('TTS_PROVIDER', 'kokoro'), ('WAKE_WORD_ENABLED', True), ('STT_PROVIDER', 'faster_whisper')])
    for t in __import__('threading').enumerate():
        if t.name == 'provider-switch':
            t.join(5)
    assert ran == ['STT_PROVIDER', 'TTS_PROVIDER', 'WAKE_WORD_ENABLED']
    with pytest.raises(provider_switch.SwitchInProgress) as e:
        provider_switch.check([('TTS_PROVIDER', 'piper')])
    assert 'TTS_PROVIDER' not in str(e.value) and 'Voice' in str(e.value)       # words, not a key name


def test_a_full_card_sends_her_voice_then_her_ears_to_the_processor(box):
    """Her LLM is loaded by the time the page opens: what is free NOW decides (measured 2026-11-15)."""
    box['free'] = 24000
    rows = _rows(setup_easy.plan())
    assert rows['voice']['settings']['TTS_PROVIDER'] == 'kokoro' and rows['ears']['settings']['FASTER_WHISPER_DEVICE'] == 'cuda'
    box['free'] = setup_easy.KOKORO_MIB + 100               # room for her voice, not for her ears as well
    rows = _rows(setup_easy.plan())
    assert rows['voice']['settings'] == {'TTS_PROVIDER': 'kokoro', 'KOKORO_DEVICE': 'cuda'}
    assert rows['ears']['settings']['FASTER_WHISPER_DEVICE'] == 'cpu' and 'GB free' in rows['ears']['note']
    box['free'] = 1500                                      # no room for her voice: the ears still fit
    plan = setup_easy.plan()
    rows = _rows(plan)
    assert rows['voice']['settings'] == {'TTS_PROVIDER': 'piper'} and 'only 1.5 GB free' in rows['voice']['why']
    assert rows['ears']['settings']['FASTER_WHISPER_DEVICE'] == 'cuda' and plan['gpu']['free_mib'] == 1500
    box['free'] = 300
    rows = _rows(setup_easy.plan())
    assert rows['voice']['settings'] == {'TTS_PROVIDER': 'piper'} and rows['ears']['settings']['FASTER_WHISPER_DEVICE'] == 'cpu'
    # already on the card: their memory is theirs, a second run must not offer the processor
    box['settings'] = {'TTS_PROVIDER': 'kokoro', 'KOKORO_DEVICE': 'cuda', 'STT_PROVIDER': 'faster_whisper',
                       'FASTER_WHISPER_DEVICE': 'cuda', 'WAKE_WORD_ENABLED': True, 'WAKEWORD_MODEL': 'hey_sapphire',
                       'SETUP_COMPLETE': True}
    assert [(r['id'], r['current']) for r in setup_easy.plan()['rows']] == [('voice', ''), ('ears', ''), ('wake', '')]
    box.update(free=None, settings={})                      # unreadable: as before, the card is trusted
    assert _rows(setup_easy.plan())['voice']['settings']['TTS_PROVIDER'] == 'kokoro'


def test_free_memory_is_read_for_the_card_that_was_picked(monkeypatch):
    from core import gpu_status
    out = "NVIDIA GeForce GTX 1050, 1900\nNVIDIA GeForce RTX 4070, 9000\n"
    monkeypatch.setattr(gpu_status.shutil, 'which', lambda name: '/usr/bin/nvidia-smi')
    monkeypatch.setattr(gpu_status.subprocess, 'run', lambda *a, **k: SimpleNamespace(stdout=out, stderr=''))
    assert gpu_status.free_mib('NVIDIA GeForce RTX 4070') == 9000
    assert gpu_status.free_mib() == 1900 and gpu_status.free_mib('gone') == 1900
    out = "[N/A]\n"
    assert gpu_status.free_mib() is None
    monkeypatch.setattr(gpu_status.shutil, 'which', lambda name: None)
    assert gpu_status.free_mib() is None


def test_done_is_said_even_when_the_microphone_would_not_open(sound):
    """She said she would listen: the room is told it is over, however it went."""
    sound['open_fails'] = True
    events = _hear(sound)
    assert sound['alerts'] == [setup_easy.WARN_LINE, setup_easy.DONE_LINE] and {'clear': True} in events
    assert events[-1]['error'] and sound['wake'] == ['paused', 'resumed']


# ── each row's own test, and its light (Krem 2026-11-15) ──

def test_the_brain_test_is_her_llm_saying_hello(monkeypatch):
    import core.chat.llm_providers as providers
    from core.chat.llm_providers import resolve as r
    asked = []

    def complete(messages, generation_params=None):
        asked.append((messages[0]['content'], generation_params.get('model')))
        return SimpleNamespace(content='<think>keep it short</think> Hello there!')
    sel = SimpleNamespace(key='lm', display_name='LM Studio', effective_model='qwen', provider=SimpleNamespace(chat_completion=complete))
    monkeypatch.setattr(r, 'resolve', lambda primary, **kw: sel)
    monkeypatch.setattr(r, 'providers_config', lambda: {})
    monkeypatch.setattr(providers, 'get_generation_params', lambda key, model, cfg: {'max_tokens': 99})
    assert setup_easy.hello() == {'ok': True, 'text': 'Hello there!', 'name': 'LM Studio: qwen', 'why': ''}
    assert asked == [(setup_easy.HELLO, 'qwen')]
    sel.provider.chat_completion = lambda *a, **k: SimpleNamespace(content='')
    assert setup_easy.hello()['ok'] is False and 'nothing' in setup_easy.hello()['why']

    def down(*a, **k):
        raise r.NoProvidersAvailable('No LLM that is turned on answered.')
    monkeypatch.setattr(r, 'resolve', down)
    assert setup_easy.hello() == {'ok': False, 'text': '', 'name': '', 'why': 'No LLM that is turned on answered.'}


def test_each_rows_light_is_what_is_live_behind_it(sound, monkeypatch):
    failed = {}
    monkeypatch.setattr(setup_easy, '_failed', lambda row_key: failed.get(row_key, ''))
    sound['settings']['WAKE_WORD_ENABLED'] = False
    st = setup_easy.status(sound['system'])
    ok, off = {'state': 'ok', 'why': '', 'busy': False}, {'state': 'off', 'why': '', 'busy': False}
    assert st == {'voice': ok, 'ears': ok, 'wake': off}
    sound['busy'].add('TTS_PROVIDER')                               # a switch runs: yellow, and the page is told a job runs
    v = setup_easy.status(sound['system'])['voice']
    assert v['state'] == 'loading' and v['busy'] is True
    sound['busy'].clear()
    sound['voice_ok'] = False                                       # set, its engine still starting: yellow, with why
    v = setup_easy.status(sound['system'])['voice']
    assert v['state'] == 'loading' and 'model is loading' in v['why'] and v['busy'] is False
    sound['voice_ok'] = True
    sound['here'] = False                                           # it works, and a setting keeps it from these speakers:
    v = setup_easy.status(sound['system'])['voice']                 # nothing is on its way, so the page must not wait on it
    assert v['state'] == 'blocked' and 'Play speech here' in v['why']
    sound['here'] = True
    sound['settings']['TTS_PROVIDER'] = 'none'                      # nothing set: grey, or red when its switch failed
    assert setup_easy.status(sound['system'])['voice'] == off
    failed['switch-tts'] = 'pip failed (exit 1). Kept off.'
    assert setup_easy.status(sound['system'])['voice'] == {'state': 'failed', 'why': 'pip failed (exit 1). Kept off.', 'busy': False}
    sound['ears'] = (False, 'loading speech model')                 # her ears are there and still loading: yellow
    assert setup_easy.status(sound['system'])['ears']['state'] == 'loading'
    from core.stt.stt_null import NullWhisperClient
    real = sound['system'].whisper_client
    sound['system'].whisper_client = NullWhisperClient()            # set, and nothing loaded: it failed at start, in the boot's words
    sound['system'].stt_why = "Speech-to-text couldn't start (no model)."
    assert setup_easy.status(sound['system'])['ears'] == {'state': 'failed', 'why': "Speech-to-text couldn't start (no model).", 'busy': False}
    sound['system'].whisper_client, sound['ears'] = real, (True, '')
    # the wake word: on, and really listening
    sound['settings']['WAKE_WORD_ENABLED'] = True
    assert setup_easy.status(sound['system'])['wake']['state'] == 'failed'      # on, with nothing behind it
    sound['system'].wake_detector = SimpleNamespace(running=True)
    sound['system'].wake_word_recorder = SimpleNamespace(stream=object())
    assert setup_easy.status(sound['system'])['wake'] == {'state': 'ok', 'why': '', 'busy': False}
    sound['system'].wake_word_recorder.stream = None                # the microphone did not open
    assert 'did not open' in setup_easy.status(sound['system'])['wake']['why']


def test_the_wake_word_test_only_proves_it_was_heard(sound, monkeypatch):
    import threading
    monkeypatch.setattr(setup_easy, 'WAKE_S', 0.3)
    assert setup_easy.hear_wake(sound['system']) == {'ok': False, 'heard': False, 'busy': False, 'why': 'The wake word is not on yet.'}
    sound['settings']['WAKE_WORD_ENABLED'] = True
    det = sound['system'].wake_detector = SimpleNamespace(running=True, probe=None)
    threading.Timer(0.05, lambda: det.probe.set()).start()          # the detector hears its phrase
    assert setup_easy.hear_wake(sound['system']) == {'ok': True, 'heard': True, 'busy': False, 'why': ''}
    assert det.probe is None and _free()
    r = setup_easy.hear_wake(sound['system'])                       # nobody says it
    assert r['ok'] is True and r['heard'] is False and det.probe is None and _free()
    assert sound['wake'] == [] and sound['reads'] == 0 and sound['scene'] == []     # no microphone of its own, no alert


def test_a_wake_word_heard_by_the_test_records_nothing(monkeypatch):
    import threading
    import config
    from core.wakeword import wake_detector as wd
    monkeypatch.setattr(config, 'WAKE_WORD_ENABLED', True, raising=False)
    told = []
    monkeypatch.setattr(wd, 'publish', lambda *a, **k: told.append(a))
    det = wd.WakeWordDetector.__new__(wd.WakeWordDetector)
    det.system = SimpleNamespace(_web_active=False)
    det.callbacks = []
    det._reset_detection_state = lambda: told.append('reset')
    det.wake_word_detected = lambda: told.append('RECORDING')
    det._stop_her_voice = lambda: False
    det.probe = threading.Event()
    probe = det.probe
    det._on_activation()
    assert probe.is_set() and det.probe is None and told == ['reset']       # heard, and nothing else happened
    det._on_activation()                                                    # the next one is a real wake again
    assert 'RECORDING' in told


def test_the_brain_row_says_what_the_launcher_knows_when_its_port_is_silent(launcher):
    """Today all of these read "No LLM yet": the launcher's own --llm-status tells them apart."""
    launcher['cli'] = {'ok': True, 'phase': 'downloading', 'job': {'label': 'qwen3.5-4b', 'percent': 42.6}}
    b = setup_easy.brain(light=True)
    assert b['state'] == 'loading' and b['text'] == 'Downloading qwen3.5-4b: 43%' and launcher['asked'] == 0
    launcher['cli'] = {'ok': True, 'phase': 'downloading', 'job': None}
    assert setup_easy.brain()['text'] == 'Downloading her LLM...'
    launcher['cli'] = {'ok': True, 'phase': 'stopped'}
    b = setup_easy.brain()
    assert b['state'] == 'stopped' and 'installed, but not running' in b['text'] and 'Launcher' in b['hint']
    launcher['cli'] = {'ok': True, 'phase': 'none', 'last_job': {'state': 'failed', 'label': 'qwen3.5-4b', 'error': 'Disk full.'}}
    b = setup_easy.brain()
    assert b['state'] == 'none' and b['text'] == 'The download of qwen3.5-4b failed.' and b['hint'].startswith('Disk full. Try again')
    launcher['cli'] = {'ok': True, 'phase': 'none', 'last_job': {'state': 'interrupted', 'label': 'qwen3.5-4b'}}
    assert 'did not finish' in setup_easy.brain()['text']
    launcher['cli'] = {'ok': True, 'phase': 'none', 'last_job': {'state': 'done'}}
    assert setup_easy.brain()['text'] == 'No LLM yet.'             # nothing to add: the words core always had
    launcher['cli'] = {'ok': True, 'phase': 'running'}             # it runs one core has no provider for:
    b = setup_easy.brain()                                         # "starting..." never ended, and Finish took it for a brain
    assert b['state'] == 'none' and 'not turned on here' in b['text']
    launcher['status'] = {'provider': 'sapphire_launcher', 'state': 'stopped'}
    assert setup_easy.brain()['state'] == 'loading'                # set up here, and not answering core yet
    launcher['status'] = {'provider': None, 'state': None}
    # junk from the launcher never breaks the row
    for pct, far in ((float('nan'), '...'), (float('inf'), '...'), (250, ': 100%'), (-3, ': 0%'), ('43', '...'), (True, '...')):
        launcher['cli'] = {'ok': True, 'phase': 'downloading', 'job': {'label': 'q' * 500, 'percent': pct}}
        text = setup_easy.brain()['text']
        assert text.endswith(far) and len(text) < 90, (pct, text)
    # an LLM that answers is never second-guessed by the launcher's word
    launcher['cli'] = {'ok': True, 'phase': 'stopped'}
    launcher['sel'] = _Sel('claude', 'Claude', 'claude-x')
    assert setup_easy.brain()['state'] == 'ok'
