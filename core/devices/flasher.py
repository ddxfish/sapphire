# core/devices/flasher.py - a board plugged into THIS computer, written and
# set up by Sapphire herself (tmp/board-flash-web-plan.md, the server lane)
#
# The Devices page's flasher has two lanes. The browser lane writes a board
# plugged into the browser's computer (Web Serial, Chrome). This is the
# other: the board is plugged into the computer Sapphire runs on, and these
# functions do what the browser would, with esptool's own Python API and
# pyserial here. The page drives them through core/routes/devices.py, so the
# wizard is the same in both lanes: ports, chip, start/status, ask, close.
#
# One write at a time, on its own thread; esptool verifies the hash of what
# it wrote. The console stays open between asks (a UART-bridge board resets
# whenever its port opens) and closes itself when idle.
import logging
import os
import threading
import time

logger = logging.getLogger(__name__)

BAUD = 460800
CONSOLE_BAUD = 115200
BOOT_WAIT = 2.5                  # seconds a board takes to boot after its port opens
IDLE = 180                       # seconds an unused console stays open
USB_SERIAL = {0x1A86: 'CH340', 0x303A: 'Espressif', 0x10C4: 'CP210x', 0x0403: 'FTDI'}

_lock = threading.Lock()
_job = None                      # the one write in flight (or the last one), a dict the page polls
_consoles = {}                   # port -> _Console


class FlashError(Exception):
    """A reason fit to show as it is."""


def _esptool():
    try:
        from esptool import cmds
        from esptool.logger import EspLog, EspLogBase
    except ImportError:
        raise FlashError("esptool is not installed in Sapphire's environment: pip install -r install/requirements-flash.txt, "
                         "then restart her. Or flash from Chrome or Edge, which needs nothing.")
    return cmds, EspLog, EspLogBase


def _no_access(port):
    if os.name != 'nt' and not os.path.exists(port):
        raise FlashError(f"There is no {port}. Is the board plugged into Sapphire's computer?")
    if os.name != 'nt' and not os.access(port, os.R_OK | os.W_OK):
        raise FlashError(f"Sapphire may not use {port}. On Linux, put the user she runs as in the dialout "
                         f"group (sudo usermod -aG dialout <user>) and restart her.")


def _plain(e):
    """esptool's reasons, in one line fit to show."""
    text = str(e).strip().splitlines()
    text = text[-1] if text else type(e).__name__
    if 'Failed to connect' in text or 'No serial data received' in text:
        return 'No bootloader answered. Hold the BOOT button while plugging the board in, then try again.'
    if 'could not open port' in text or 'Permission denied' in text:
        return f'The port could not be opened ({text}).'
    return text[:300]


def _quiet_logger():
    """esptool's logger base, when esptool is there. Its words are kept, not
    printed, and its die() raises instead of exiting Sapphire's thread."""
    _, _, EspLogBase = _esptool()

    class Quiet(EspLogBase):
        def __init__(self):
            self.lines = []

        def _take(self, *args):
            message = ' '.join(str(a) for a in args).strip()
            if message:
                self.lines.append(message)
                del self.lines[:-200]

        def print(self, *args, **kwargs): self._take(*args)
        def note(self, *args): self._take(*args)
        def hint(self, *args): self._take(*args)
        def debug(self, *args): pass
        def warn(self, *args, suggestion=None): self._take('warning:', *args)
        def err(self, *args, suggestion=None): self._take('error:', *args)
        def die(self, *args, exit_code=1, suggestion=None): raise FlashError(' '.join(str(a) for a in args))
        def progress_bar(self, cur_iter, total_iters, prefix='', suffix='', bar_length=30): pass
        def set_verbosity(self, mode): pass
    return Quiet


def _Quiet():
    return _quiet_logger()()


def _JobLog(job):
    Quiet = _quiet_logger()

    class JobLog(Quiet):
        """...and its progress into the job the page polls."""
        def progress_bar(self, cur_iter, total_iters, prefix='', suffix='', bar_length=30):
            if total_iters:
                job['percent'] = min(100, int(100 * cur_iter / total_iters))
                job['text'] = f"Writing, {job['percent']}%"

        def _take(self, *args):
            super()._take(*args)
            if any('Hash of data verified' in str(a) for a in args):
                job['verified'] = True
    return JobLog()


def ports():
    """USB serial ports on this computer: [{port, name, chip}]."""
    try:
        from serial.tools import list_ports
    except ImportError:
        raise FlashError("pyserial is not installed in Sapphire's environment: pip install -r install/requirements-flash.txt.")
    out = []
    for p in list_ports.comports():
        if p.vid is None:
            continue                                  # a motherboard UART, not a board
        out.append({'port': p.device, 'name': (p.description or p.device)[:60],
                    'bridge': USB_SERIAL.get(p.vid, f"{p.vid:04x}:{(p.pid or 0):04x}")})
    return sorted(out, key=lambda x: x['port'])


def chip(port):
    """What is on that port: {'family': 'ESP32', 'text': 'ESP32-D0WD-V3 (revision 3)', 'mac'}."""
    cmds, EspLog, _ = _esptool()
    _no_access(port)
    _drop_console(port)
    with _lock:
        EspLog.set_logger(_Quiet())
        try:
            esp = cmds.detect_chip(port, baud=CONSOLE_BAUD)
        except Exception as e:
            raise FlashError(_plain(e))
        finally:
            EspLog.instance = None
        try:
            mac = ':'.join(f'{b:02x}' for b in esp.read_mac())
            out = {'family': esp.CHIP_NAME, 'text': esp.get_chip_description(), 'mac': mac}
            cmds.reset_chip(esp, 'hard-reset')         # back to its program, not left in the loader
            return out
        except Exception as e:
            raise FlashError(_plain(e))
        finally:
            _close_port(esp)


def _close_port(esp):
    try:
        esp._port.close()
    except Exception:
        pass


# --- writing ------------------------------------------------------------------

def start(port, board_id):
    """Write a board's firmware on `port`, in the background. Returns the job
    as status() will show it. One write at a time."""
    global _job
    from core.devices import firmware
    _no_access(port)
    with _lock:
        if _job and _job['state'] in ('getting', 'writing'):
            raise FlashError("A board is being written already. Wait for it.")
        boards, error = firmware._boards()
        b = boards.get(str(board_id or '').strip().lower())
        if not b:
            raise FlashError(error or f"No such board: {board_id}.")
        _job = {'state': 'getting', 'percent': 0, 'text': 'Getting the firmware...', 'error': '',
                'port': port, 'board': b['id'], 'version': b['version'], 'verified': False, 'started': time.time()}
        job = _job
    _drop_console(port)
    threading.Thread(target=_write, args=(job, b), name='flash-write', daemon=True).start()
    return status()


def _write(job, b):
    from core.devices import firmware
    cmds, EspLog, _ = _esptool()
    esp = None
    try:
        parts = [(p['offset'], str(firmware.part(b['id'], p['path']))) for p in b['parts']]
        job.update(state='writing', text='Connecting to the board...')
        EspLog.set_logger(_JobLog(job))
        flash = b['flash']
        esp = cmds.detect_chip(job['port'], baud=CONSOLE_BAUD)
        esp = cmds.run_stub(esp)
        esp.change_baud(BAUD)
        cmds.attach_flash(esp)
        job['text'] = 'Erasing and writing...'
        cmds.write_flash(esp, parts, flash_freq=flash['freq'], flash_mode=flash['mode'], flash_size=flash['size'],
                         erase_all=True, compress=True)
        if not job['verified']:
            raise FlashError("The write finished but esptool did not verify its hash.")
        cmds.reset_chip(esp, 'hard-reset')
        job.update(state='done', percent=100, text='Written and verified.')
        logger.info(f"[DEVICES] flashed {b['id']} {b['version']} on {job['port']}")
    except Exception as e:
        job.update(state='failed', error=_plain(e) if not isinstance(e, (FlashError, firmware.FirmwareError)) else str(e), text='')
        logger.error(f"[DEVICES] flash of {b['id']} on {job['port']} failed: {e}")
    finally:
        EspLog.instance = None
        if esp is not None:
            _close_port(esp)


def status():
    """The write in flight, or the last one: {state, percent, text, error, ...}. state: idle, getting, writing, done, failed."""
    with _lock:
        return dict(_job) if _job else {'state': 'idle', 'percent': 0, 'text': '', 'error': ''}


# --- the board's console ----------------------------------------------------------

class _Console:
    """The firmware's console over its serial line, kept open. One line in;
    log lines out until one `>> {json}` answers (the same protocol the
    browser lane speaks in device-flash.js)."""

    def __init__(self, port):
        import serial
        self.port = port
        s = serial.Serial()
        s.port, s.baudrate, s.timeout = port, CONSOLE_BAUD, 0.25
        s.dtr = s.rts = False                  # never lower only one: that is the bootloader dance
        s.open()
        # the classic run-mode reset, so the board boots its program, not its ROM loader
        s.rts = True
        time.sleep(0.1)
        s.rts = False
        time.sleep(BOOT_WAIT)
        s.reset_input_buffer()
        self.s = s
        self.used = time.time()
        self.said = []

    def ask(self, line, wait=15.0):
        import json
        self.used = time.time()
        self.s.reset_input_buffer()
        self.s.write((line + '\n').encode('utf-8'))
        self.s.flush()
        until = time.time() + wait
        while time.time() < until:
            raw = self.s.readline()
            if not raw:
                continue
            text = raw.decode('utf-8', 'replace').rstrip('\r\n')
            if text.startswith('>> '):
                try:
                    return json.loads(text[3:])
                except ValueError:
                    continue                   # cut by a log line: wait for the next
            if text.strip():
                self.said.append(text)
                del self.said[:-60]
        raise FlashError(f'The board did not answer "{line.split(" ")[0]}".')

    def close(self):
        try:
            self.s.close()
        except Exception:
            pass


def ask(port, line, wait=15.0):
    """One console line to the board on `port`, opened (and the board
    booted) if it is not open yet. Returns {'answer': ..., 'said': [...]}."""
    import serial
    _no_access(port)
    with _lock:
        con = _consoles.get(port)
        if con is None:
            try:
                con = _consoles[port] = _Console(port)
            except serial.SerialException as e:
                raise FlashError(f"Could not open {port}: {e}")
    try:
        answer = con.ask(line, wait)
    except serial.SerialException as e:
        _drop_console(port)
        raise FlashError(f"The port went away ({e}). A board with native USB comes back after its reset; ask again.")
    return {'answer': answer, 'said': list(con.said[-20:])}


def close(port):
    _drop_console(port)
    return {'closed': port}


def _drop_console(port):
    with _lock:
        con = _consoles.pop(port, None)
    if con:
        con.close()


def tend():
    """Close consoles nobody has used for a while. Called by the routes now and then."""
    now = time.time()
    for port, con in list(_consoles.items()):
        if now - con.used > IDLE:
            _drop_console(port)
