# core/plugins/browser/routes/pipe.py - a paired browser's pipe (/ws/plugin/browser/pipe)
#
# The extension in a person's own browser dials this socket. Core has already
# checked its key (the manifest's "door": "device", core/devices/door.py) and
# says which device it is. Frames are JSON both ways; ../bridge.py has what
# they mean. This handler runs on the web server's loop and the bridge lives
# on the engine's, so frames are posted across, in order.
import asyncio
import json
import logging

logger = logging.getLogger(__name__)


def _label(device):
    try:
        from core.devices import engine as devices
        return str(devices.get(device).get('label') or device)
    except Exception:
        return device


async def pipe(websocket, settings, device):
    from plugins.browser import bridge, engine
    engine.apply(settings)
    loop = asyncio.get_running_loop()
    out = asyncio.Queue()

    def send(frame):                       # the engine's loop -> this one
        try:
            loop.call_soon_threadsafe(out.put_nowait, frame)
        except RuntimeError:
            pass                           # this loop is closing; the socket goes with it

    async def sender():
        try:
            while True:
                frame = await out.get()
                if frame is None:          # the bridge hung up (replaced by a newer pipe, or the plugin is stopping)
                    return await websocket.close(code=1000)
                await websocket.send_text(json.dumps(frame))
        except Exception:
            pass                           # the socket closed under us; receive ends the handler

    label = await asyncio.to_thread(_label, device)
    b = bridge.Bridge(device, send, lambda: send(None), label)
    out_task = loop.create_task(sender())
    try:
        # posted, never awaited, here and in the finally: a cancel (the plugin
        # unloading, the device unpaired) finds no gap to leave a bridge behind in
        engine.post(bridge.register, device, b)
        send({'cmd': 'welcome', 'name': device, 'label': label})
        while True:
            try:
                msg = json.loads(await websocket.receive_text())
            except ValueError:
                continue
            if isinstance(msg, dict):
                engine.post(b.feed, msg)
    except Exception:
        pass                               # closed, by them or by us (the error type varies by server)
    finally:
        out_task.cancel()
        try:
            engine.post(bridge.unregister, device, b)
        except Exception as e:             # the engine is stopped: its shutdown closed every bridge
            logger.debug(f"[BROWSER] pipe cleanup for {device}: {e!r}")
