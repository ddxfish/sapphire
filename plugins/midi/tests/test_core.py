"""FM-1 plugin: the pure parts. No ALSA, no synth, no Sapphire needed."""
import sys
from pathlib import Path

import pytest

_ROOT = str(Path(__file__).absolute().parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import midi_core as midi  # noqa: E402

LISTING = ("client 0: 'System' [type=kernel]\n"
           "client 32: 'FM-1' [type=kernel,card=4]\n"
           "    0 'FM-1 MIDI 1     '\n"
           "client 128: 'FM-1_BLE' [type=user,pid=2469]\n")


def test_port_prefers_first_name_found():
    assert midi.find_port(('FM-1_BLE', 'FM-1'), LISTING) == ('128:0', 'FM-1_BLE')
    assert midi.find_port(('FM-1',), LISTING) == ('32:0', 'FM-1')


def test_port_name_is_exact_not_prefix():
    only_ble = "client 128: 'FM-1_BLE' [type=user,pid=1]\n"
    with pytest.raises(midi.MidiError):
        midi.find_port(('FM-1',), only_ble)


def test_port_missing_says_so():
    with pytest.raises(midi.MidiError, match='No synth is connected.*FM-1_BLE, FM-1'):
        midi.find_port(('FM-1_BLE', 'FM-1'), "client 0: 'System' [type=kernel]\n")


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


def test_dump_lines():
    assert midi.parse_dump_line(' 14:0   Note on                 0, note 60, velocity 100') == ('on', 60, 100)
    assert midi.parse_dump_line(' 14:0   Note off                0, note 60, velocity 0') == ('off', 60, 0)
    assert midi.parse_dump_line(' 14:0   Note off                0, note 64') == ('off', 64, 0)
    assert midi.parse_dump_line(' 14:0   Note on                 0, note 64, velocity 0') == ('off', 64, 0)
    assert midi.parse_dump_line(' 14:0   Control change          0, controller 64, value 127') is None
    assert midi.parse_dump_line('Source  Event                  Ch  Data') is None


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

def test_find_ports_returns_every_connected_name():
    listing = LISTING + "client 20: 'AKM320' [type=kernel,card=1]\n"
    assert midi.find_ports(('AKM320', 'Nope', 'FM-1'), listing) == [('20:0', 'AKM320'), ('32:0', 'FM-1')]
    assert midi.find_ports(('Nope',), listing) == []
    assert midi.find_ports((), listing) == []


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
