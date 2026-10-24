"""Event-loop stall watch: say when uvicorn's loop stops turning, and who held it.

A loop blocked past the WebSocket keepalive (20 s) silently drops every live
socket (browser conversation mode first) — websockets logs the missed pong at
DEBUG only. Two halves:
  - a ticker ON the loop logs how long a stall lasted once the loop is back;
  - a watcher THREAD logs the loop thread's stack while it is still stuck.
The watcher also notices its own oversleep: if it was held too, the whole
process wasn't running (host overload, GIL) — not our code blocking the loop.
"""
import asyncio
import logging
import sys
import threading
import time
import traceback

logger = logging.getLogger(__name__)

TICK_S = 1.0
STALL_S = 2.0       # a stall this long is logged once the loop is back
DUMP_S = 10.0       # still blocked this long: log where the loop thread is

_beat = time.monotonic()
_watching = False


async def run():
    global _beat, _watching
    if not _watching:
        _watching = True
        threading.Thread(target=_watch, args=(threading.get_ident(),),
                         daemon=True, name='loop-watch').start()
    while True:
        _beat = time.monotonic()
        await asyncio.sleep(TICK_S)
        late = time.monotonic() - _beat - TICK_S
        if late > STALL_S:
            logger.warning(f"[LOOP] event loop stalled {late:.1f}s")


def _watch(tid):
    last = time.monotonic()
    dumped = None
    while True:
        time.sleep(TICK_S)
        now = time.monotonic()
        held = now - last - TICK_S
        last = now
        beat = _beat
        if held > STALL_S:
            logger.warning(f"[LOOP] the whole process was held {held:.1f}s "
                           "(not scheduled: host overload or GIL) — not a blocked loop")
            dumped = beat           # the loop was frozen with us; its stack says nothing
            continue
        if now - beat > DUMP_S and dumped != beat:
            dumped = beat
            frame = sys._current_frames().get(tid)
            stack = ''.join(traceback.format_stack(frame)) if frame else '(no frame)\n'
            logger.warning(f"[LOOP] event loop blocked {now - beat:.0f}s so far — it is here:\n{stack}")
