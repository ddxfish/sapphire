"""Minimal Standard MIDI File writer, no deps. Played with aplaymidi.

events: list of (start_beat, dur_beats, note, vel, channel). Beats = quarter notes.
"""
import struct


def _vlq(n):
    out = [n & 0x7F]
    n >>= 7
    while n:
        out.append(0x80 | (n & 0x7F))
        n >>= 7
    return bytes(reversed(out))


def write_mid(path, events, bpm=120, ppq=480, program=None, channel=0, tail=0):
    """tail = beats of quiet after the last note, before the file ends."""
    msgs = []
    for start, dur, note, vel, ch in events:
        t0, t1 = round(start * ppq), round((start + dur) * ppq)
        msgs.append((t0, 1, bytes([0x90 | ch, note, vel])))
        msgs.append((t1, 0, bytes([0x80 | ch, note, 0])))   # offs sort before ons at the same tick
    msgs.sort()
    trk = b'\x00\xff\x51\x03' + struct.pack('>I', round(60_000_000 / bpm))[1:]
    if program is not None:
        trk += b'\x00' + bytes([0xC0 | channel, program])
    last = 0
    for t, _, m in msgs:
        trk += _vlq(t - last) + m
        last = t
    trk += _vlq(round(tail * ppq)) + b'\xff\x2f\x00'
    data = b'MThd' + struct.pack('>IHHH', 6, 0, 1, ppq) + b'MTrk' + struct.pack('>I', len(trk)) + trk
    with open(path, 'wb') as f:
        f.write(data)
    return path
