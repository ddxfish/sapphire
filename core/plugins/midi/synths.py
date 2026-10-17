"""Synth profiles: what one make of synth calls its voices and how its effects
are switched. One JSON file per synth in synths/:

  {"id": "fm-1", "name": "M-VAVE FM-1", "port_names": ["FM-1_BLE", "FM-1"],
   "voices": ["BRASS 1", ...128 names...],
   "fx_channel": 1, "effects": {"reverb": {"switch": 4, "levels": {"mix": [7, 100]}}}}

A synth with no profile still plays: voices are General MIDI names and it has
no effects we know how to switch. Adding a synth is adding a file.
"""
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_DIR = Path(__file__).absolute().parent / 'synths'
_cache = None


def profiles():
    """Every profile that ships, by id. Read once; a bad file is logged and skipped."""
    global _cache
    if _cache is None:
        found = {}
        for p in sorted(_DIR.glob('*.json')) if _DIR.is_dir() else []:
            try:
                prof = json.loads(p.read_text(encoding='utf-8'))
                prof.setdefault('id', p.stem)
                prof.setdefault('name', prof['id'])
                prof.setdefault('port_names', [])
                prof.setdefault('voices', [])
                prof.setdefault('effects', {})
                prof.setdefault('fx_channel', 0)
                found[prof['id']] = prof
            except Exception as e:
                logger.warning(f"[midi] synth profile {p.name} skipped: {e}")
        _cache = found
    return _cache


def port_names():
    """The port names of every profiled synth, in file order: the default
    list of synths to look for when the user has not named any."""
    return tuple(n for prof in profiles().values() for n in prof['port_names'])


def for_port(name):
    """The profile whose port names include this ALSA port name, or None."""
    for prof in profiles().values():
        if name in prof['port_names']:
            return prof
    return None


def voice_names(prof, gm):
    """The voice list to pick from: the profile's own when it has one, else
    General MIDI's 128 (gm = {name: program} as midi_songs keeps it)."""
    if prof and prof.get('voices'):
        return list(prof['voices'])
    names = [f'program {i}' for i in range(1, 129)]
    for k, v in gm.items():
        names[int(v) - 1] = k
    return names
