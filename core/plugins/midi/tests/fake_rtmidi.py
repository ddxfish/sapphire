"""A stand-in for python-rtmidi: the ports that are here (per direction),
what was sent, and keys to inject. A rig installs it as sys.modules['rtmidi']
and midi_core, which imports the library when first used, finds this instead.

    import fake_rtmidi as fake
    fake.box = fake.Box(ins=[AKM], outs=[FM1])      # or callables, for a listing that changes
    monkeypatch.setitem(sys.modules, 'rtmidi', fake)
    fake.box.key(AKM, [0x90, 60, 100])              # someone presses a key
    fake.box.sent                                   # [(port, [status, d1, d2]), ...]
"""
API_UNSPECIFIED, API_LINUX_ALSA, API_WINDOWS_MM, API_MACOSX_CORE = 0, 1, 2, 3
_NAMES = {0: 'unspecified', 1: 'alsa', 2: 'winmm', 3: 'coremidi'}


class Box:
    def __init__(self, ins=(), outs=(), api=API_LINUX_ALSA):
        self._ins, self._outs, self.api = ins, outs, api
        self.sent = []            # (port, message)
        self.opened = []          # (direction, port)
        self.closed = []          # (direction, port)
        self.fail_open = set()    # ports that refuse to open
        self.readers = {}         # port -> set of open MidiIn objects

    @property
    def ins(self):
        return list(self._ins() if callable(self._ins) else self._ins)

    @property
    def outs(self):
        return list(self._outs() if callable(self._outs) else self._outs)

    def key(self, port, msg):
        """Someone plays `msg` on `port`: every open reader of it is called."""
        for r in list(self.readers.get(port, ())):
            r._fire((list(msg), 0.0), None)

    def open(self, direction):
        """How many ports of that direction are open right now."""
        return sum(1 for d, _ in self.opened if d == direction) - sum(1 for d, _ in self.closed if d == direction)


box = Box()


def get_compiled_api():
    return [box.api]


def get_api_name(api):
    return _NAMES.get(api, 'unspecified')


def get_rtmidi_version():
    return 'fake'


class _Port:
    direction = ''

    def __init__(self, rtapi=API_UNSPECIFIED, name=None, **_):
        self.name, self.port = name, None

    def get_current_api(self):
        return box.api

    def get_ports(self, encoding='auto'):
        return box.ins if self.direction == 'in' else box.outs

    def open_port(self, idx, name=None):
        port = self.get_ports()[idx]
        if port in box.fail_open:
            raise RuntimeError(f'error creating port {port!r}')
        self.port = port
        box.opened.append((self.direction, port))
        if self.direction == 'in':
            box.readers.setdefault(port, set()).add(self)

    def is_port_open(self):
        return self.port is not None

    def close_port(self):
        if self.port is None:
            return
        box.closed.append((self.direction, self.port))
        if self.direction == 'in':
            box.readers.get(self.port, set()).discard(self)
        self.port = None

    def delete(self):
        self.close_port()


class MidiIn(_Port):
    direction = 'in'
    _cb = None

    def set_callback(self, fn, data=None):
        self._cb = fn

    def cancel_callback(self):
        self._cb = None

    def ignore_types(self, **_):
        pass

    def _fire(self, event, data):
        if self._cb:
            self._cb(event, data)


class MidiOut(_Port):
    direction = 'out'

    def send_message(self, msg):
        if self.port is None:
            raise RuntimeError('port not open')
        box.sent.append((self.port, list(msg)))
