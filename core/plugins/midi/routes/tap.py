# GET /api/plugin/midi/tap        : the keys she hears, live, as server-sent events.
# GET /api/plugin/midi/tap/clock  : this machine's clock, so a page can speak it.
#
# A page on another machine (the Game Room's piano board) cannot hear a keyboard
# that is linked to the machine she runs on. This is its ear: every note on and
# off, stamped with THIS machine's monotonic clock in milliseconds. A page
# learns the clock once from tap/clock and judges timing in it, so the time a
# note spends on the wire changes when it is drawn, never whether it counted.
#
# Nothing is kept: it is the live keys to a logged-in page, and ends with it.
# Login is enforced by the framework.
import asyncio
import importlib
import json
import logging
import os
import select
import subprocess
import sys
import threading
import time
from pathlib import Path

# .absolute(), never .resolve(): symlinked plugin dirs must not escape.
_PLUGIN_ROOT = str(Path(__file__).absolute().parent.parent)
if _PLUGIN_ROOT not in sys.path:
    sys.path.insert(0, _PLUGIN_ROOT)

import midi_core as midi

logger = logging.getLogger(__name__)

MAX_TAPS = 4          # pages listening at once; a tap is one aseqdump each
HEARTBEAT = 15        # seconds between ": still here" lines on a quiet stream
_open = 0
_lock = threading.Lock()


def _tools():
    return importlib.import_module('plugins.midi.tools.midi_tools')


def now_ms():
    """This machine's clock for the tap: monotonic milliseconds, 0.1 ms steps."""
    return time.monotonic_ns() // 100_000 / 10


def _ports(settings):
    """-> (ports, names) or (None, why)."""
    try:
        return _tools()._listen_ports(settings)
    except midi.MidiError as e:
        return None, str(e)
    except Exception as e:
        logger.warning(f"[midi] tap could not look for keyboards: {e}")
        return None, "Could not look for keyboards on the machine she runs on."


def clock(settings=None, **_):
    ports, names = _ports(settings)
    return {'now': now_ms(), 'ok': ports is not None,
            'ports': names if ports else '', 'detail': '' if ports else names}


def read_keys(ports, emit, stopped):
    """Run aseqdump on `ports` and hand every note to emit(event) until
    stopped is set or the dump ends. Raw reads behind select, as capture()
    does: no hidden buffer to starve the select on."""
    proc = subprocess.Popen(['aseqdump', '-p', ports], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    fd, buf = proc.stdout.fileno(), b''
    try:
        while not stopped.is_set():
            if not select.select([fd], [], [], 0.25)[0]:
                if proc.poll() is not None:
                    return
                continue
            chunk = os.read(fd, 4096)
            if not chunk:
                return
            t = now_ms()
            buf += chunk
            *lines, buf = buf.split(b'\n')
            for raw in lines:
                ev = midi.parse_dump_line(raw.decode('ascii', 'replace'))
                if ev:
                    kind, note, vel = ev
                    emit({'type': 'note', 'on': kind == 'on', 'n': note, 'v': vel, 't': t})
    finally:
        midi._end(proc)
        try:
            proc.stdout.close()
        except Exception:
            pass


async def stream(settings=None, request=None, **_):
    from fastapi.responses import JSONResponse, StreamingResponse
    global _open
    if midi.missing_tools():
        return JSONResponse({'error': "This machine is missing alsa-utils, so the keys can't be heard."},
                            status_code=503)
    ports, names = _ports(settings)
    if ports is None:
        return JSONResponse({'error': names}, status_code=409)
    with _lock:
        if _open >= MAX_TAPS:
            return JSONResponse({'error': f"{MAX_TAPS} pages are already listening to the keys."},
                                status_code=429)
        _open += 1

    loop = asyncio.get_running_loop()
    queue = asyncio.Queue()
    stopped = threading.Event()

    def emit(ev):
        try:
            loop.call_soon_threadsafe(queue.put_nowait, ev)
        except RuntimeError:                     # the loop closed under a shutdown
            stopped.set()

    def run():
        try:
            read_keys(ports, emit, stopped)
        except Exception as e:
            logger.warning(f"[midi] tap reader ended: {e}")
        finally:
            emit(None)

    thread = threading.Thread(target=run, name='midi-tap', daemon=True)

    async def body():
        global _open
        thread.start()
        logger.info(f"[midi] tap opened on {names}")
        try:
            yield f"data: {json.dumps({'type': 'hello', 'now': now_ms(), 'ports': names})}\n\n"
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT)
                except asyncio.TimeoutError:
                    if request is not None and await request.is_disconnected():
                        break
                    yield ": still here\n\n"
                    continue
                if item is None:                 # the dump ended under us
                    yield f"data: {json.dumps({'type': 'gone', 'detail': 'The keys went away.'})}\n\n"
                    break
                yield f"data: {json.dumps(item)}\n\n"
        finally:
            stopped.set()
            with _lock:
                _open -= 1
            logger.info("[midi] tap closed")

    return StreamingResponse(body(), media_type='text/event-stream',
                             headers={'Cache-Control': 'no-cache', 'Connection': 'keep-alive',
                                      'X-Accel-Buffering': 'no'})
