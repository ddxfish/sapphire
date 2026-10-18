"""MIDI plugin: the pure parts, and her playing and listening on a pretend
machine. No real MIDI, no synth, no Sapphire needed."""
import sys
import threading
import time
from pathlib import Path

import pytest

_ROOT = str(Path(__file__).absolute().parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import midi_core as midi  # noqa: E402
import fake_rtmidi as fake  # noqa: E402

BLE = 'FM-1_BLE:FM-1_BLE Bluetooth 129:0'
USB = 'FM-1:FM-1 MIDI 1 32:0'
AKM = 'AKM320:AKM320 MIDI 1 20:0'
THRU = 'Midi Through:Midi Through Port-0 14:0'
LISTING = [THRU, USB, BLE]


@pytest.fixture
def box(monkeypatch):
    """A pretend machine with the FM-1 on USB and Bluetooth and a keyboard."""
    fake.box = fake.Box(ins=[THRU, AKM, BLE, USB, 'Sapphire MIDI:out 130:0'], outs=LISTING)
    monkeypatch.setitem(sys.modules, 'rtmidi', fake)
    monkeypatch.setattr(midi, '_backend', 'alsa')
    yield fake.box
    midi.shutdown()


def test_port_prefers_first_name_found(box):
    assert midi.find_port(('FM-1_BLE', 'FM-1'), listing=LISTING) == (BLE, 'FM-1_BLE')
    assert midi.find_port(('FM-1',), listing=LISTING) == (USB, 'FM-1')
    assert midi.find_port(('fm-1',)) == (USB, 'fm-1')                   # the machine's own listing, any case


def test_port_name_is_exact_not_prefix(box):
    with pytest.raises(midi.MidiError):
        midi.find_port(('FM-1',), listing=[BLE])


def test_port_missing_says_so(box):
    with pytest.raises(midi.MidiError, match='No synth is connected.*FM-1_BLE, FM-1'):
        midi.find_port(('FM-1_BLE', 'FM-1'), listing=[THRU])


def test_names_per_system(box):
    assert midi.client_name(BLE) == 'FM-1_BLE' and midi.client_name(THRU) == 'Midi Through'
    midi._backend = 'winmm'
    assert midi.client_name('FM-1 0') == 'FM-1' and midi.client_name('Midi Through 1') == 'Midi Through'
    assert midi.find_ports(('FM-1',), listing=['Midi Through 0', 'FM-1 1']) == [('FM-1 1', 'FM-1')]
    midi._backend = 'coremidi'
    assert midi.client_name('IAC Driver Bus 1') == 'IAC Driver Bus 1'


def test_what_is_here_leaves_out_the_plumbing_and_us(box):
    assert midi.names() == ['FM-1', 'FM-1_BLE']
    assert [s['name'] for s in midi.sources()] == ['AKM320', 'FM-1_BLE', 'FM-1']
    fake.box = fake.Box(ins=[AKM, 'AKM320:AKM320 MIDI 1 21:0'])
    assert [s['id'] for s in midi.sources()] == ['AKM320', 'AKM320#2']   # two alike are still two


def test_without_the_library_every_door_says_so(box, monkeypatch):
    monkeypatch.setitem(sys.modules, 'rtmidi', None)
    assert 'Install button' in midi.missing()
    with pytest.raises(midi.MidiError, match='python-rtmidi'):
        midi.find_ports(('FM-1',))


def test_note_numbers_round_trip():
    assert midi.note_num('C4') == 60
    assert midi.note_num('Bb3') == midi.note_num('A#3') == 58
    assert midi.note_name(60) == 'C4'
    for n in range(12, 120):
        assert midi.note_num(midi.note_name(n)) == n


@pytest.mark.parametrize('bad', ['H4', 'C', 'C44', '4C', 'C#'])
def test_bad_note_raises(bad):
    with pytest.raises(midi.MidiError):
        midi.parse(bad)


def test_parse_notes_chords_rests_barlines():
    ev, beats = midi.parse('C4 E4:2 | [C4 E4 G4]:4 R:1 G4:0.5', vel=80)
    assert beats == 8.5
    assert [e[2] for e in ev] == [60, 64, 60, 64, 67, 67]
    assert [e[0] for e in ev] == [0, 1, 3, 3, 3, 8]
    assert all(e[3] == 80 and e[4] == midi.NOTE_CH for e in ev)


@pytest.mark.parametrize('bad', ['C4:0', 'C4:-1', 'C4:x'])
def test_bad_length_raises(bad):
    with pytest.raises(midi.MidiError):
        midi.parse(bad)


def test_rests_alone_are_not_music():
    with pytest.raises(midi.MidiError):
        midi.build_events('R:4')


def test_loop_repeats_to_fill_and_caps():
    ev, beats, reps = midi.build_events('C4 E4', loop_seconds=10, bpm=120)   # 1s phrase
    assert (beats, reps, len(ev)) == (2, 10, 20)
    assert ev[-1][0] == 19
    _, _, capped = midi.build_events('C4 E4', loop_seconds=99999, bpm=120)
    assert capped == midi.MAX_LOOP_MIN * 60


def test_messages():
    assert midi.parse_message([0x90, 60, 100]) == ('on', 60, 100)
    assert midi.parse_message([0x80, 60, 0]) == ('off', 60, 0)
    assert midi.parse_message([0x91, 64, 0]) == ('off', 64, 0)            # a note-on at zero is a release
    assert midi.parse_message([0xB0, 64, 127]) is None                     # a pedal is not a key
    assert midi.parse_message([0xF8]) is None
    assert midi._messages('B1 04 01 B1 07 3C B1 06 64') == [[0xB1, 4, 1], [0xB1, 7, 0x3C], [0xB1, 6, 0x64]]
    assert midi._messages('') == []


def test_notation_reads_back_what_was_played():
    src = 'C4 E4 [C4 E4 G4]:2 R:2 G4:0.5 A4:0.5 A#3'
    ev, _ = midi.parse(src)
    played = [(s * 0.5, d * 0.5, n, v) for s, d, n, v, _ in ev]           # 120 bpm
    assert midi.to_notation(played, 120) == src


def test_notation_loose_timing_lands_on_the_grid():
    played = [(0.00, 0.41, 60, 50), (0.53, 0.44, 64, 50), (0.97, 0.97, 67, 50)]
    assert midi.to_notation(played, 120) == 'C4 E4 G4:2'


def test_notation_chord_window():
    played = [(0.00, 1.0, 60, 90), (0.03, 1.0, 64, 90), (0.05, 1.0, 67, 90), (1.0, 0.5, 72, 90)]
    assert midi.to_notation(played, 120) == '[C4 E4 G4]:2 C5'


def test_heard_text_nothing_and_something():
    assert 'nothing was played' in midi.heard_text([], 120, 60)
    text = midi.heard_text([(0.0, 0.5, 60, 40), (0.5, 0.5, 72, 40)], 120)
    assert '2 notes' in text and 'C4 C5' in text
    assert 'Range C4 to C5' in text and 'soft' in text


import synths  # noqa: E402
import midi_songs as songs  # noqa: E402


def test_fm1_profile_ships_and_is_found_by_port_name():
    prof = synths.for_port('FM-1_BLE')
    assert prof and prof['id'] == 'fm-1' and synths.for_port('FM-1') is prof
    assert len(prof['voices']) == 128 and prof['fx_channel'] == 1
    assert set(prof['effects']) == {'filter', 'reverb', 'delay', 'distortion', 'chorus', 'phaser'}
    assert synths.for_port('AKM320') is None
    assert synths.port_names()[:2] == ('FM-1_BLE', 'FM-1')


def test_voices_and_picking():
    names = synths.voice_names(synths.for_port('FM-1'), songs.INSTRUMENTS)
    assert len(names) == 128
    assert midi.pick_voice(names, '11')[:2] == (11, 'E.PIANO 1')
    n, name, others = midi.pick_voice(names, 'piano')
    assert n == 8 and 'PIANO' in name and others
    for bad in ('0', '129', 'kazoo'):
        with pytest.raises(midi.MidiError):
            midi.pick_voice(names, bad)
    gm = synths.voice_names(None, songs.INSTRUMENTS)
    assert len(gm) == 128 and gm[0] == 'piano' and gm[19] == 'organ' and gm[2] == 'program 3'
    assert midi.pick_voice(gm, 'organ')[:2] == (20, 'organ')


def test_fx_messages(monkeypatch):
    sent = []
    prof = synths.for_port('FM-1')
    fx, ch = prof['effects'], prof['fx_channel']
    monkeypatch.setattr(midi, 'send', lambda port, hexbytes: sent.append((port, hexbytes)))
    assert midi.fx('1:0', fx, ch, 'reverb', True, {'mix': 60, 'decay': 400}) == {'mix': 60, 'decay': 100}
    assert sent == [('1:0', 'B1 04 01 B1 07 3C B1 06 64')]
    midi.fx('1:0', fx, ch, 'delay', False)
    assert sent[-1] == ('1:0', 'B1 08 00')
    with pytest.raises(midi.MidiError):
        midi.fx('1:0', fx, ch, 'reverb', True, {'wobble': 1})
    with pytest.raises(midi.MidiError):
        midi.fx('1:0', fx, ch, 'kazoo')
    with pytest.raises(midi.MidiError, match='Unknown effect'):
        midi.fx('1:0', {}, 0, 'reverb')


def test_program_change(monkeypatch):
    sent = []
    monkeypatch.setattr(midi, 'send', lambda port, hexbytes: sent.append(hexbytes))
    midi.program('1:0', 11)
    assert sent == ['C0 0A']


# -- several keyboards ------------------------------------------------------

def test_find_ports_returns_every_connected_name(box):
    assert midi.find_ports(('AKM320', 'Nope', 'FM-1'), 'in') == [(AKM, 'AKM320'), (USB, 'FM-1')]
    assert midi.find_ports(('Nope',), 'in') == []
    assert midi.find_ports((), 'in') == []


# -- her playing, and the keys, on a pretend machine ------------------------

def _wait(cond, timeout=3):
    end = time.monotonic() + timeout
    while not cond() and time.monotonic() < end:
        time.sleep(0.01)
    assert cond()


def test_a_phrase_plays_in_time_on_a_port_held_only_while_it_sounds(box):
    r = midi.play(USB, 'C4 E4', bpm=400, voice=11)                      # 0.15 s a beat
    assert r['slot'] == 'phrase' and r['notes'] == 2 and midi.playing() == ['phrase']
    assert r['player'].done.wait(3)
    assert [m for _, m in box.sent] == [[0xC0, 10], [0x90, 60, 90], [0x80, 60, 0], [0x90, 64, 90], [0x80, 64, 0]]
    assert all(p == USB for p, _ in box.sent)
    _wait(lambda: box.open('out') == 0)                                  # let go when the music ended
    assert midi.playing() == []


def test_a_loop_and_a_phrase_share_the_one_output(box):
    loop = midi.play(USB, 'C2', bpm=60, loop_minutes=1)
    phrase = midi.play(USB, 'C4', bpm=60)
    assert midi.playing() == ['loop', 'phrase']
    _wait(lambda: len(box.sent) >= 2)
    assert box.open('out') == 1 and box.opened.count(('out', USB)) == 1  # one port, two players
    midi.stop_all(USB)
    assert loop['player'].done.is_set() and phrase['player'].done.is_set()
    offs = [m for _, m in box.sent if m[0] == 0x80]
    assert [0x80, 36, 0] in offs and [0x80, 60, 0] in offs              # what was held is let go
    assert len(offs) >= 128 + 2 and box.open('out') == 0                 # then every note, then the port


def test_a_new_phrase_replaces_the_old(box):
    first = midi.play(USB, 'C4:100', bpm=60)
    second = midi.play(USB, 'E4', bpm=400)
    assert first['player'].done.is_set() and midi.playing() == ['phrase']
    assert second['player'].done.wait(3)


def test_capture_hears_what_is_played(box):
    def play():
        time.sleep(0.1)
        box.key(AKM, [0x90, 60, 100])
        time.sleep(0.2)
        box.key(AKM, [0x90, 64, 80])
        time.sleep(0.1)
        box.key(AKM, [0x80, 60, 0])
        box.key(AKM, [0x90, 64, 0])
    threading.Thread(target=play, daemon=True).start()
    notes = midi.capture([AKM, BLE], max_seconds=5, first_wait=2, quiet_end=0.3)
    assert [(n, v) for _, _, n, v in notes] == [(60, 100), (64, 80)]
    assert 0.12 < notes[1][0] < 0.28 and 0.22 < notes[0][1] < 0.38      # when, and for how long
    assert box.open('in') == 0                                           # both ports let go


def test_capture_gives_up_waiting_and_can_be_cancelled(box):
    assert midi.capture(AKM, first_wait=0.3) == []
    cancel = threading.Event()
    threading.Timer(0.2, cancel.set).start()
    t0 = time.monotonic()
    assert midi.capture(AKM, first_wait=10, cancel=cancel) == [] and time.monotonic() - t0 < 2


def test_one_reader_per_port_however_many_listen(box):
    a, b = midi.subscribe(AKM), midi.subscribe([AKM, BLE])
    assert box.open('in') == 2 and box.opened.count(('in', AKM)) == 1
    box.key(AKM, [0x90, 60, 100])
    assert a.get()[1:] == ('on', 60, 100) and b.get()[1:] == ('on', 60, 100)
    a.close()
    assert box.open('in') == 2                                           # b still listens to the AKM
    b.close()
    assert box.open('in') == 0


def test_a_port_that_is_gone_or_busy_is_said_plainly(box):
    with pytest.raises(midi.MidiError, match='Casio is not connected any more'):
        midi.send('Casio:Casio 40:0', '90 3C 64')
    box.fail_open.add(USB)
    with pytest.raises(midi.MidiError, match='FM-1 did not open'):
        midi.send(USB, '90 3C 64')
    assert box.open('out') == 0 and midi._outputs == {}
    midi._backend = 'winmm'
    with pytest.raises(midi.MidiError, match='one program at a time'):
        midi.send(USB, '90 3C 64')


def test_shutdown_ends_everything_and_lets_go_of_every_port(box):
    midi.play(USB, 'C2', bpm=60, loop_minutes=1)
    sub = midi.subscribe(AKM)
    _wait(lambda: box.open('out') == 1)
    midi.shutdown()
    assert midi.playing() == [] and box.open('out') == 0 and box.open('in') == 0
    assert midi._players == {} and midi._readers == {} and midi._outputs == {}
    sub.close()                                                          # a late close is harmless


# -- saved songs ------------------------------------------------------------

import midi_songs as songs  # noqa: E402


def _no_audio(mid, mp3):
    return 'test: no renderer'


def _fake_audio(mid, mp3):
    mp3.write_bytes(b'ID3fake')
    return None


def test_instruments():
    assert songs.pick_instrument(None) == (1, 'piano')
    assert songs.pick_instrument('Music Box') == (11, 'music box')
    assert songs.pick_instrument('a soft flute') == (74, 'flute')
    assert songs.pick_instrument('41') == (41, 'violin')
    assert songs.pick_instrument(128) == (128, 'program 128')
    for bad in ('0', '129', 'kazoo'):
        with pytest.raises(midi.MidiError):
            songs.pick_instrument(bad)


def test_render_runs_the_two_programs_and_cleans_up(tmp_path, monkeypatch):
    """The real renderer, with fluidsynth and ffmpeg faked: it found the take
    reaching for a constant the core no longer had (live, 2026-10-18)."""
    ran = []

    def run(cmd, **kw):
        ran.append(cmd[0])
        Path(cmd[cmd.index('-F') + 1] if cmd[0] == 'fluidsynth' else cmd[-1]).write_bytes(b'x' * 2000)
        return __import__('types').SimpleNamespace(returncode=0, stderr='')
    monkeypatch.setattr(songs.shutil, 'which', lambda n: f'/usr/bin/{n}')
    monkeypatch.setattr(songs.subprocess, 'run', run)
    monkeypatch.setattr(songs.tempfile, 'gettempdir', lambda: str(tmp_path))
    mp3 = tmp_path / 'song.mp3'
    assert songs.render_mp3(tmp_path / 'song.mid', mp3) is None
    assert ran == ['fluidsynth', 'ffmpeg'] and mp3.exists()
    assert list(tmp_path.glob(f'{songs.TMP_TAG}*.wav')) == []        # the wav is gone


def test_save_writes_midi_and_meta_without_audio(tmp_path):
    m = songs.save(tmp_path, '  Evening   in E minor ', 'E4 G4 B4:2', bpm=60, instrument='harp', render=_no_audio)
    assert m['title'] == 'Evening in E minor'
    assert (m['note_count'], m['beats'], m['seconds']) == (3, 4, 4.0)
    assert (m['low'], m['high'], m['instrument']) == ('E4', 'B4', 'harp')
    assert m['audio'] is False and 'no renderer' in m['audio_problem']
    mid = (tmp_path / f"{m['id']}.mid").read_bytes()
    assert mid[:4] == b'MThd' and bytes([0xC0, 46]) in mid            # harp = GM 47
    assert songs.meta(tmp_path, m['id'])['title'] == 'Evening in E minor'
    assert songs.links(m) == [f"[Download MIDI](/api/plugin/midi/song/{m['id']}.mid)"]


def test_save_with_audio_gives_three_links(tmp_path):
    m = songs.save(tmp_path, 'Waltz [draft] (2)', 'C4 E4 G4', render=_fake_audio)
    got = songs.links(m)
    assert got[0] == f"[Play Waltz draft 2](/api/plugin/midi/song/{m['id']}.mp3)"
    assert got[1].endswith('.mp3?dl=1)') and got[2].endswith('.mid)')
    assert songs.find(tmp_path, f"{m['id']}.mp3") and songs.find(tmp_path, f"{m['id']}.mid")


def test_save_refuses_bad_input(tmp_path):
    for kw in ({'notes': 'R:4'}, {'notes': 'C4', 'bpm': 5}, {'notes': 'C4:9999', 'bpm': 60},
               {'notes': 'C4', 'instrument': 'kazoo'}):
        with pytest.raises(midi.MidiError):
            songs.save(tmp_path, 't', render=_no_audio, **{'notes': 'C4', **kw})
    assert list(tmp_path.glob('*.mid')) == []                          # nothing half-written


@pytest.mark.parametrize('name', ['../secret.mid', '..%2Fx.mid', 'abc.mid', '0123456789.json',
                                  '0123456789.mid/..', '/etc/passwd', '', None, '0123456789.MID'])
def test_find_refuses_anything_but_a_song_file(tmp_path, name):
    (tmp_path / '0123456789.json').write_text('{}')
    assert songs.find(tmp_path, name) is None


def test_slug():
    assert songs.slug('Evening in E minor!') == 'evening-in-e-minor'
    assert songs.slug('') == 'song' and songs.slug(None) == 'song'
    assert songs.slug('../../x') == 'x'


def test_midi_tail_pads_the_end():
    from midi_midifile import write_mid
    import tempfile, os
    with tempfile.TemporaryDirectory() as d:
        a = write_mid(os.path.join(d, 'a.mid'), [(0, 1, 60, 90, 0)])
        b = write_mid(os.path.join(d, 'b.mid'), [(0, 1, 60, 90, 0)], tail=2)
        ra, rb = open(a, 'rb').read(), open(b, 'rb').read()
    assert ra.endswith(b'\x00\xff\x2f\x00')
    assert rb.endswith(b'\x87\x40\xff\x2f\x00')                        # 960 ticks, then end


# -- takes and the chat's files row -----------------------------------------

PLAYED = [(0.0, 0.41, 60, 50), (0.53, 0.44, 64, 72), (0.97, 0.97, 67, 110)]


def test_take_keeps_its_own_timing_and_touch(tmp_path):
    m = songs.save_take(tmp_path, 'Keys take', PLAYED, bpm=120, render=_no_audio)
    assert (m['kind'], m['note_count'], m['instrument']) == ('take', 3, 'fm piano')
    assert m['notes'] == 'C4 E4 G4:2' and m['seconds'] == 1.9
    mid = (tmp_path / f"{m['id']}.mid").read_bytes()
    for vel in (50, 72, 110):                                         # each key's own velocity
        assert bytes([0x90, {50: 60, 72: 64, 110: 67}[vel], vel]) in mid
    assert bytes([0xC0, 5]) in mid                                    # GM 6, the FM electric piano


def test_nothing_played_saves_nothing(tmp_path):
    assert songs.save_take(tmp_path, 't', [], render=_no_audio) is None
    assert list(tmp_path.iterdir()) == []


def test_files_marker_names_the_files(tmp_path):
    attachments = pytest.importorskip('core.attachments')
    m = songs.save(tmp_path, 'Copper Light!', 'C4 E4 G4', render=_fake_audio)
    row = songs.files_marker(m)
    assert attachments.MARKER_RE.fullmatch(row)
    body = __import__('json').loads(row[len('<!--FILES:'):-len('-->')])
    assert body['title'] == 'Copper Light!'
    assert body['items'] == [
        {'url': f"/api/plugin/midi/song/{m['id']}.mp3", 'name': 'copper-light.mp3'},
        {'url': f"/api/plugin/midi/song/{m['id']}.mid", 'name': 'copper-light.mid'}]
    silent = songs.save(tmp_path, 'Quiet', 'C4', render=_no_audio)
    assert [i['name'] for i in __import__('json').loads(songs.files_marker(silent)[10:-3])['items']] == ['quiet.mid']
