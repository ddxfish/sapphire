# core/ir/library.py - the IR code library.
#
# Appliances in a room (a TV, a speaker), each with named commands (power,
# vol up...), each a code a satellite's IR blaster sends. Sapphire resolves a
# name like "tv power" to the code and hands it to the board; the board blasts
# it. Stored as one JSON file, user/ir/library.json.
#
# Today a code is NEC: {"protocol": "nec", "addr", "cmd"} (two bytes). Learned
# raw codes ({"protocol": "raw", "us": [...], "carrier"}) and other protocols
# extend the shape; resolve() just returns the code dict and the driver decides
# what it can send.
#
# A small built-in BRANDS table seeds / imports known codes (an LG TV today);
# the user's own library is theirs to edit in Settings, or to learn into once a
# board has an IR receiver.
import json
import logging
import re
import threading

from core.fs_utils import replace_with_retry
from core.paths import PROJECT_ROOT

logger = logging.getLogger(__name__)

LIB = PROJECT_ROOT / 'user' / 'ir' / 'library.json'
_lock = threading.RLock()
NAME_RE = re.compile(r'[a-z0-9][a-z0-9 _+-]{0,31}')   # an appliance or command name, lowercased

# Known codes shipped with Sapphire, to import from without an IR receiver. LG
# TVs are plain NEC (address 0x04); these four are the common, well-documented
# ones. Add brands here as they are confirmed.
BRANDS = {
    'LG TV': [
        {'name': 'power', 'protocol': 'nec', 'addr': 0x04, 'cmd': 0x08},
        {'name': 'vol up', 'protocol': 'nec', 'addr': 0x04, 'cmd': 0x02},
        {'name': 'vol down', 'protocol': 'nec', 'addr': 0x04, 'cmd': 0x03},
        {'name': 'mute', 'protocol': 'nec', 'addr': 0x04, 'cmd': 0x09},
    ],
    # TCL Android TVs are NEC-extended (16-bit address 0x57E3). TCL ROKU sets use
    # a different protocol and will NOT answer these - a Roku TV is best run over
    # its network API (ECP), not IR.
    'TCL TV (Android)': [
        {'name': 'power', 'protocol': 'nec', 'addr': 0x57E3, 'cmd': 0xE8},
        {'name': 'vol up', 'protocol': 'nec', 'addr': 0x57E3, 'cmd': 0xF0},
        {'name': 'vol down', 'protocol': 'nec', 'addr': 0x57E3, 'cmd': 0x08},
        {'name': 'mute', 'protocol': 'nec', 'addr': 0x57E3, 'cmd': 0x04},
    ],
    # TCL Roku TVs (NECext, address 0xC7EA), from the Flipper-IRDB TCL_ROKU_US set.
    # Volume isn't on the Roku TV remote; nav + power are.
    'TCL Roku TV': [
        {'name': 'power', 'protocol': 'nec', 'addr': 0xC7EA, 'cmd': 0x17},
        {'name': 'home', 'protocol': 'nec', 'addr': 0xC7EA, 'cmd': 0x03},
        {'name': 'back', 'protocol': 'nec', 'addr': 0xC7EA, 'cmd': 0x66},
        {'name': 'up', 'protocol': 'nec', 'addr': 0xC7EA, 'cmd': 0x19},
        {'name': 'down', 'protocol': 'nec', 'addr': 0xC7EA, 'cmd': 0x33},
        {'name': 'left', 'protocol': 'nec', 'addr': 0xC7EA, 'cmd': 0x1E},
        {'name': 'right', 'protocol': 'nec', 'addr': 0xC7EA, 'cmd': 0x2D},
        {'name': 'select', 'protocol': 'nec', 'addr': 0xC7EA, 'cmd': 0x2A},
    ],
}


def _read():
    with _lock:
        try:
            data = json.loads(LIB.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            data = None
        if not isinstance(data, dict) or not isinstance(data.get('appliances'), list):
            data = {'appliances': []}
        return data


def _write(data):
    with _lock:
        LIB.parent.mkdir(parents=True, exist_ok=True)
        tmp = LIB.with_suffix('.json.tmp')
        tmp.write_text(json.dumps(data, indent=2), encoding='utf-8')
        replace_with_retry(tmp, LIB)     # Windows: AV or an editor holding the file


def _norm(s):
    return re.sub(r'\s+', ' ', str(s or '').strip().lower())


def appliances():
    """[{name, brand, commands:[{name, protocol, ...}]}]."""
    return _read().get('appliances', [])


def command_names():
    """'tv power, tv mute, speaker power' - for an error or a prompt."""
    out = []
    for a in appliances():
        for c in a.get('commands', []):
            out.append(f"{a.get('name','?')} {c.get('name','?')}")
    return ', '.join(out)


def resolve(text):
    """A spoken name -> (appliance, command) or (None, None). Matches
    '<appliance> <command>' by the appliance's name or brand, and a bare
    command when there is only one appliance. Command names may be two words."""
    text = _norm(text)
    apps = appliances()
    for a in apps:
        for key in (_norm(a.get('name')), _norm(a.get('brand'))):
            if key and text.startswith(key + ' '):
                want = text[len(key) + 1:].strip()
                for c in a.get('commands', []):
                    if _norm(c.get('name')) == want:
                        return a, c
    if len(apps) == 1:
        for c in apps[0].get('commands', []):
            if _norm(c.get('name')) == text:
                return apps[0], c
    return None, None


# --- editing (the Settings UI calls these) ---------------------------------

class LibraryError(Exception):
    """A reason fit to show as it is."""


def _find(data, appliance):
    name = _norm(appliance)
    return next((a for a in data['appliances'] if _norm(a.get('name')) == name), None)


def add_appliance(name, brand=''):
    name = _norm(name)
    if not NAME_RE.fullmatch(name):
        raise LibraryError("An appliance name is letters, numbers, spaces, 1-32 long.")
    data = _read()
    if _find(data, name):
        raise LibraryError(f"There is already an appliance called '{name}'.")
    data['appliances'].append({'name': name, 'brand': str(brand or '')[:32], 'commands': []})
    _write(data)
    return name


def set_command(appliance, command, code):
    """Add or replace one command's code. `code` is a dict with 'protocol' and
    its fields (nec: addr, cmd)."""
    cmd = _norm(command)
    if not NAME_RE.fullmatch(cmd):
        raise LibraryError("A command name is letters, numbers, spaces, 1-32 long.")
    if not isinstance(code, dict) or not code.get('protocol'):
        raise LibraryError("A command needs a code with a protocol.")
    data = _read()
    a = _find(data, appliance)
    if not a:
        raise LibraryError(f"No appliance called '{_norm(appliance)}'.")
    a.setdefault('commands', [])
    entry = {'name': cmd, **code}
    for i, c in enumerate(a['commands']):
        if _norm(c.get('name')) == cmd:
            a['commands'][i] = entry
            break
    else:
        a['commands'].append(entry)
    _write(data)


def remove(appliance, command=None):
    """Drop a command, or a whole appliance when command is None."""
    data = _read()
    a = _find(data, appliance)
    if not a:
        return
    if command is None:
        data['appliances'] = [x for x in data['appliances'] if x is not a]
    else:
        cmd = _norm(command)
        a['commands'] = [c for c in a.get('commands', []) if _norm(c.get('name')) != cmd]
    _write(data)


def import_brand(brand, appliance=None):
    """Seed an appliance from the built-in BRANDS table. Returns the name made."""
    codes = BRANDS.get(brand)
    if not codes:
        raise LibraryError(f"No built-in codes for '{brand}'. Known: {', '.join(BRANDS)}.")
    name = _norm(appliance or brand)
    data = _read()
    if not _find(data, name):
        data['appliances'].append({'name': name, 'brand': brand, 'commands': []})
    _write(data)
    for c in codes:
        set_command(name, c['name'], {k: v for k, v in c.items() if k != 'name'})
    return name
