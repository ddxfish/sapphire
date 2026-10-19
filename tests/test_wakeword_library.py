# tests/test_wakeword_library.py - the wake word library (core/wakeword/library.py) and its doors
# (core/routes/wakeword.py). The disk is a temp folder; the devices and the detector are stubbed.
import json
from types import SimpleNamespace

import pytest

from core.wakeword import library as lib

TFL = b'\0\0\0\0TFL3' + b'\0' * 5000
ONNX = b'\x08\x07' + b'\0' * 5000
ESPHOME = {"type": "micro", "wake_word": "hey marcus", "author": "Wakeword Maker", "model": "model.tflite", "version": 2,
           "micro": {"probability_cutoff": 0.9, "feature_step_size": 10, "sliding_window_size": 5, "tensor_arena_size": 24000}}
MAKER_DESKTOP = {"name": "hey_marcus", "phrase": "hey marcus", "threshold": 0.45, "framework": "onnx", "run": "r1",
                 "label": "rnn 100k", "installed": 1791000000.0, "made_by": "wakeword-maker"}


@pytest.fixture
def home(tmp_path, monkeypatch):
    user = tmp_path / 'user' / 'wakeword' / 'models'
    core = tmp_path / 'core' / 'models'
    core.mkdir(parents=True)
    monkeypatch.setattr(lib, 'USER_DIR', user)
    monkeypatch.setattr(lib, 'CORE_DIR', core)
    monkeypatch.setattr(lib, 'listeners', lambda: [
        {'id': 'desktop', 'kind': 'desktop', 'family': 'onnx', 'model': 'hey_sapphire', 'wish': None, 'online': True},
        {'id': 'esp', 'kind': 'device', 'family': 'tflite', 'model': 'hey_marcus', 'wish': 'hey_marcus', 'online': True},
    ])
    return SimpleNamespace(user=user, core=core)


# --- names and shapes ---------------------------------------------------------------------------

def test_slug_and_family():
    assert lib.slug('Hey Marcus!') == 'hey_marcus'
    assert lib.slug('../x') == 'x'
    assert lib.slug('') == ''
    assert lib.family_of('a.TFLITE') == 'tflite' and lib.family_of('b.onnx') == 'onnx' and lib.family_of('c.bin') == ''


def test_normalize_reads_an_esphome_manifest_for_the_tflite_only():
    t = lib.normalize(ESPHOME, 'tflite')
    assert t['threshold'] == 0.9 and t['phrase'] == 'hey marcus'
    assert t['micro'] == {'sliding_window': 5, 'step_ms': 10, 'arena': 24000}
    o = lib.normalize(ESPHOME, 'onnx')               # named for the other family: only the phrase carries
    assert o['threshold'] == lib.USUAL['onnx'] and o['phrase'] == 'hey marcus'


def test_normalize_reads_the_makers_desktop_sidecar_and_firmware_style_keys():
    o = lib.normalize(MAKER_DESKTOP, 'onnx')
    assert (o['threshold'], o['run'], o['label'], o['made_by'], o['added']) == (0.45, 'r1', 'rnn 100k', 'wakeword-maker', 1791000000.0)
    assert lib.normalize(MAKER_DESKTOP, 'tflite')['threshold'] == lib.USUAL['tflite']
    f = lib.normalize({'name': 'x', 'threshold': 0.93, 'sliding_window': 7, 'step_ms': 20}, 'tflite')
    assert f['threshold'] == 0.93 and f['micro'] == {'sliding_window': 7, 'step_ms': 20}


def test_normalize_a_block_wins_over_the_top_and_bad_numbers_fall_to_the_usual():
    both = {'phrase': 'hey nova', 'threshold': 0.4, 'tflite': {'threshold': 0.95, 'micro': {'sliding_window': 99, 'step_ms': 2}}}
    assert lib.normalize(both, 'onnx')['threshold'] == 0.4
    t = lib.normalize(both, 'tflite')
    assert t['threshold'] == 0.95 and t['micro'] == {'sliding_window': 5, 'step_ms': 10}
    assert lib.normalize({'threshold': 'high'}, 'onnx')['threshold'] == 0.5
    assert lib.normalize({'threshold': 1.5}, 'onnx')['threshold'] == 0.5


# --- add, entries, remove -----------------------------------------------------------------------

def test_add_checks_the_bytes_and_the_name(home):
    with pytest.raises(lib.LibraryError, match='TFL3'):
        lib.add('hey_marcus', b'\0' * 5000, 'tflite')
    with pytest.raises(lib.LibraryError, match='4 KB to 1.5 MB'):
        lib.add('hey_marcus', TFL[:100], 'tflite')
    with pytest.raises(lib.LibraryError, match='4 KB to 1.5 MB'):
        lib.add('hey_marcus', TFL + b'\0' * (1536 * 1024), 'tflite')
    with pytest.raises(lib.LibraryError, match='name'):
        lib.add('!!!', TFL, 'tflite')
    with pytest.raises(lib.LibraryError, match='onnx'):
        lib.add('hey_marcus', TFL, 'bin')
    with pytest.raises(lib.LibraryError, match='manifest'):          # a satellite model needs the board's numbers
        lib.add('hey_marcus', TFL, 'tflite')
    with pytest.raises(lib.LibraryError, match='manifest'):
        lib.add('hey_marcus', TFL, 'tflite', {'threshold': 0.8})
    assert not home.user.exists()
    assert lib.has_micro(ESPHOME) and lib.has_micro({'sliding_window': 5, 'step_ms': 10}) and lib.has_micro({'type': 'micro'})
    assert lib.has_micro({'tflite': {'micro': {'sliding_window': 5}}}) and not lib.has_micro(MAKER_DESKTOP) and not lib.has_micro(None)
    lib.add('hey_nova', ONNX, 'onnx')                                 # a PC model's manifest is optional
    assert lib.get('hey_nova', 'onnx')['threshold'] == lib.USUAL['onnx']


def test_add_writes_the_model_and_a_sidecar_block_and_refuses_a_second_without_overwrite(home):
    e = lib.add('Hey Marcus', TFL, 'tflite', ESPHOME, made_by='wakeword-maker')
    assert e['name'] == 'hey_marcus' and e['family'] == 'tflite' and e['source'] == 'user'
    assert e['threshold'] == 0.9 and e['phrase'] == 'hey marcus' and e['micro']['sliding_window'] == 5
    assert (home.user / 'hey_marcus.tflite').read_bytes() == TFL
    side = json.loads((home.user / 'hey_marcus.json').read_text())
    assert side['name'] == 'hey_marcus' and side['phrase'] == 'hey marcus'
    assert side['tflite']['threshold'] == 0.9 and side['tflite']['made_by'] == 'wakeword-maker' and side['tflite']['added'] > 0
    assert 'onnx' not in side
    with pytest.raises(lib.Exists):
        lib.add('hey_marcus', TFL, 'tflite', ESPHOME)
    e2 = lib.add('hey_marcus', TFL, 'tflite', {'threshold': 0.8, 'micro': {'sliding_window_size': 5, 'feature_step_size': 10}}, overwrite=True)
    assert e2['threshold'] == 0.8
    assert not list(home.user.glob('*.tmp'))


def test_the_other_family_keeps_its_block_even_from_the_makers_old_flat_sidecar(home):
    home.user.mkdir(parents=True)
    (home.user / 'hey_marcus.onnx').write_bytes(ONNX)
    (home.user / 'hey_marcus.json').write_text(json.dumps(MAKER_DESKTOP))      # what the Maker's desktop install wrote
    assert lib.get('hey_marcus', 'onnx')['threshold'] == 0.45
    lib.add('hey_marcus', TFL, 'tflite', ESPHOME)
    side = json.loads((home.user / 'hey_marcus.json').read_text())
    assert side['onnx']['threshold'] == 0.45 and side['onnx']['run'] == 'r1'
    assert side['tflite']['threshold'] == 0.9
    assert lib.get('hey_marcus', 'onnx')['threshold'] == 0.45 and lib.get('hey_marcus', 'tflite')['threshold'] == 0.9
    lib.add('hey_marcus', ONNX, 'onnx', {'threshold': 0.55}, overwrite=True)
    side = json.loads((home.user / 'hey_marcus.json').read_text())
    assert side['onnx']['threshold'] == 0.55 and side['tflite']['threshold'] == 0.9


def test_entries_user_first_then_core_then_builtin_and_a_user_file_hides_a_shipped_one(home):
    (home.core / 'hey_sapphire.onnx').write_bytes(ONNX)
    (home.core / 'hey_sapphire.json').write_text(json.dumps({'onnx': {'threshold': 0.6}}))
    lib.add('hey_sapphire', ONNX, 'onnx', {'threshold': 0.42})
    lib.add('hey_jarvis', TFL, 'tflite', dict(ESPHOME, wake_word='hey jarvis'))   # the ESP32's word: not the builtin onnx
    rows = [(e['name'], e['family'], e['source']) for e in lib.entries()]
    assert rows[:2] == [('hey_jarvis', 'tflite', 'user'), ('hey_sapphire', 'onnx', 'user')]
    assert ('hey_sapphire', 'onnx', 'core') not in rows
    assert ('hey_jarvis', 'onnx', 'builtin') in rows and ('alexa', 'onnx', 'builtin') in rows
    assert lib.get('hey_sapphire', 'onnx')['threshold'] == 0.42
    assert lib.threshold_of('hey_sapphire') == 0.42 and lib.threshold_of('alexa') == 0.5 and lib.threshold_of('nope') is None


def test_remove_drops_the_file_and_the_block_and_names_who_used_it(home):
    lib.add('hey_marcus', TFL, 'tflite', ESPHOME)
    lib.add('hey_marcus', ONNX, 'onnx', {'threshold': 0.4})
    assert lib.remove('hey_marcus', 'tflite') == ['esp']
    assert not (home.user / 'hey_marcus.tflite').exists() and (home.user / 'hey_marcus.onnx').exists()
    side = json.loads((home.user / 'hey_marcus.json').read_text())
    assert 'tflite' not in side and side['onnx']['threshold'] == 0.4
    assert lib.remove('hey_marcus', 'onnx') == []
    assert not (home.user / 'hey_marcus.json').exists()
    with pytest.raises(lib.LibraryError, match='no onnx model'):
        lib.remove('hey_marcus', 'onnx')
    with pytest.raises(lib.LibraryError, match='stays'):
        lib.remove('alexa', 'onnx')


def test_the_desktop_list_offers_onnx_only(home):
    lib.add('hey_marcus', TFL, 'tflite', ESPHOME)
    lib.add('hey_nova', ONNX, 'onnx')
    from core.wakeword import get_available_models
    got = get_available_models()
    assert 'hey_nova' in got['all'] and 'hey_marcus' not in got['all'] and 'alexa' in got['all']
    assert {(m['name'], m['type']) for m in got['custom']} == {('hey_marcus', 'tflite'), ('hey_nova', 'onnx')}


# --- listeners and the switch -------------------------------------------------------------------

def test_listeners_this_computer_and_every_satellite_with_a_wake_word(tmp_path, monkeypatch):
    import config
    from core.devices import engine
    from core.devices.drivers import satellite
    monkeypatch.setattr(config, 'WAKEWORD_MODEL', 'hey_sapphire', raising=False)
    monkeypatch.setattr(config, 'WAKE_WORD_ENABLED', True, raising=False)
    monkeypatch.setattr(engine, 'refusal', lambda: '')
    monkeypatch.setattr(engine, 'statuses', lambda: {'esp': {'online': False}, 'pi': {'online': True}, 'typer': {'online': True}})
    monkeypatch.setattr(engine, 'rows', lambda: {
        'esp': {'id': 'esp', 'label': 'Kitchen box', 'location': 'Kitchen', 'fingerprint': 'aa:bb',
                'parts': [{'driver': 'satellite', 'has': ['speaker', 'mic', 'wake', 'firmware'], 'config': {'wake_model': 'hey_marcus'}}]},
        'pi': {'id': 'pi', 'parts': [{'driver': 'satellite', 'has': ['speaker', 'mic', 'wake'], 'config': {}}]},
        'typer': {'id': 'typer', 'parts': [{'driver': 'satellite', 'has': ['keyboard'], 'config': {}}]},
        'lamp': {'id': 'lamp', 'parts': [{'driver': 'lamp', 'config': {}}]},
        'off': {'id': 'off', 'enabled': False, 'parts': [{'driver': 'satellite', 'has': ['wake'], 'config': {}}]},
    })
    monkeypatch.setattr(satellite, 'wake_state', lambda d: {'model': 'hey_sapphire', 'format': 'onnx', 'enabled': True} if d == 'pi' else {})
    who = lib.listeners()
    assert [w['id'] for w in who] == ['desktop', 'esp', 'pi']
    assert who[0] == {'id': 'desktop', 'label': 'This computer', 'kind': 'desktop', 'location': '', 'family': 'onnx',
                      'model': 'hey_sapphire', 'wish': None, 'online': True, 'enabled': True,
                      'threshold': getattr(config, 'WAKEWORD_THRESHOLD', None)}
    esp = who[1]
    assert (esp['family'], esp['model'], esp['wish'], esp['online'], esp['label']) == ('tflite', None, 'hey_marcus', False, 'Kitchen box')
    assert (who[2]['family'], who[2]['model'], who[2]['online']) == ('onnx', 'hey_sapphire', True)


def test_listeners_is_only_this_computer_when_the_devices_cannot_be_listed(monkeypatch):
    from core.devices import engine
    monkeypatch.setattr(engine, 'refusal', lambda: 'hosted')
    assert [w['id'] for w in lib.listeners()] == ['desktop']
    monkeypatch.setattr(engine, 'refusal', lambda: (_ for _ in ()).throw(RuntimeError('boom')))
    assert [w['id'] for w in lib.listeners()] == ['desktop']


def test_switch_desktop_sets_both_settings_then_swaps_live(home, monkeypatch):
    lib.add('hey_nova', ONNX, 'onnx', {'threshold': 0.42})
    from core import settings_manager, api_fastapi
    calls = []
    monkeypatch.setattr(settings_manager.settings, 'set', lambda k, v, **kw: calls.append((k, v, kw.get('persist'))))
    monkeypatch.setattr(api_fastapi, 'get_system', lambda: SimpleNamespace(reload_wakeword_model=lambda: calls.append('reload') or True))
    out = lib.switch('desktop', 'hey_nova')
    assert calls == [('WAKEWORD_MODEL', 'hey_nova', True), ('WAKEWORD_THRESHOLD', 0.42, True), 'reload']
    assert out == {'listener': 'desktop', 'model': 'hey_nova', 'threshold': 0.42, 'sent': True, 'warning': ''}
    with pytest.raises(lib.LibraryError, match='no desktop'):
        lib.switch('desktop', 'hey_marcus')
    with pytest.raises(lib.LibraryError, match='no desktop'):
        lib.switch('desktop', 'own')


def test_switch_a_device_stores_the_wish_and_tells_the_board(home, monkeypatch):
    lib.add('hey_marcus', TFL, 'tflite', ESPHOME)
    from core.devices import engine
    from core.devices.drivers import satellite
    row = {'id': 'esp', 'parts': [{'driver': 'satellite', 'config': {}}], 'enabled': True}
    seen = {}
    monkeypatch.setattr(engine, 'get', lambda d: row)
    monkeypatch.setattr(engine, 'update', lambda d, **kw: seen.update(kw) or (row, []))
    monkeypatch.setattr(engine, 'tell', lambda r: seen.setdefault('told', []) or [])
    monkeypatch.setattr(satellite, 'wake_state', lambda d: {'format': 'tflite', 'model': 'hey_jarvis'})
    out = lib.switch('esp', 'hey_marcus')
    assert seen['parts'] == {'satellite': {'wake_model': 'hey_marcus'}}
    assert out == {'listener': 'esp', 'model': 'hey_marcus', 'threshold': 0.9, 'sent': True, 'warning': ''}
    with pytest.raises(lib.LibraryError, match='no microWakeWord model'):
        lib.switch('esp', 'hey_nova')
    out = lib.switch('esp', 'own')
    assert seen['parts'] == {'satellite': {'wake_model': ''}} and out['model'] == '' and out['threshold'] is None
    monkeypatch.setattr(engine, 'tell', lambda r: ['satellite: Could not reach 192.168.1.100.'])
    out = lib.switch('esp', 'hey_marcus')
    assert out['sent'] is False and 'Could not reach' in out['warning']       # the wish is kept; told when it is seen


# --- the doors ----------------------------------------------------------------------------------

def test_the_doors(home, client, monkeypatch):
    c, csrf = client
    h = {'X-CSRF-Token': csrf}
    r = c.get('/api/wakeword/library')
    assert r.status_code == 200
    assert [w['id'] for w in r.json()['listeners']] == ['desktop', 'esp'] and r.json()['families'] == lib.FAMILIES
    assert ('alexa', 'onnx') in [(m['name'], m['family']) for m in r.json()['models']]
    files = {'file': ('hey_marcus.tflite', TFL, 'application/octet-stream'),
             'manifest': ('hey_marcus.json', json.dumps(ESPHOME).encode(), 'application/json')}
    r = c.post('/api/wakeword/library', files=files, data={'name': ''}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()['model']['name'] == 'hey_marcus' and r.json()['model']['threshold'] == 0.9
    r = c.post('/api/wakeword/library', files={'file': ('hey_marcus.tflite', TFL, 'application/octet-stream')}, headers=h)
    assert r.status_code == 400 and 'manifest' in r.json()['detail']          # a satellite model without its manifest
    r = c.post('/api/wakeword/library', files=files, headers=h)
    assert r.status_code == 409
    r = c.post('/api/wakeword/library', files={'file': ('hey_marcus.tflite', TFL, 'application/octet-stream'),
                                               'manifest': ('m.json', json.dumps({'type': 'micro', 'micro': {'probability_cutoff': 0.8}}).encode(), 'application/json')},
               data={'overwrite': 'true'}, headers=h)
    assert r.status_code == 200 and r.json()['model']['threshold'] == 0.8
    r = c.post('/api/wakeword/library', files={'file': ('x.bin', TFL, 'application/octet-stream')}, headers=h)
    assert r.status_code == 400
    r = c.post('/api/wakeword/library', files={'file': ('bad.tflite', b'\0' * 5000, 'application/octet-stream')}, headers=h)
    assert r.status_code == 400 and 'TFL3' in r.json()['detail']
    r = c.post('/api/wakeword/library', files={'file': ('m.onnx', ONNX, 'application/octet-stream'),
                                               'manifest': ('m.json', b'not json', 'application/json')}, headers=h)
    assert r.status_code == 400
    r = c.get('/api/wakeword/library/tflite/hey_marcus/file')
    assert r.status_code == 200 and r.content == TFL
    assert c.get('/api/wakeword/library/onnx/alexa/file').status_code == 404
    r = c.delete('/api/wakeword/library/tflite/hey_marcus', headers=h)
    assert r.status_code == 200 and r.json()['used_by'] == ['esp']
    assert c.delete('/api/wakeword/library/tflite/hey_marcus', headers=h).status_code == 400
    assert c.delete('/api/wakeword/library/onnx/alexa', headers=h).status_code == 400
    r = c.post('/api/wakeword/switch', json={'listener': 'desktop', 'model': 'nope'}, headers=h)
    assert r.status_code == 400 and 'no desktop' in r.json()['detail']
    monkeypatch.setattr(lib, 'switch', lambda l, m: {'listener': l, 'model': m, 'sent': True, 'warning': '', 'threshold': 0.5})
    r = c.post('/api/wakeword/switch', json={'listener': 'esp', 'model': 'hey_marcus'}, headers=h)
    assert r.status_code == 200 and r.json()['model'] == 'hey_marcus'
