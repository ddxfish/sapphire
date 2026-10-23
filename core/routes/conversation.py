"""Browser conversation-mode WebSocket (v3) — /ws/conversation.

The WS connection IS the mode switch (plan pitfall 5, tmp/v3-conversation-
websocket.md): browser conversation starts on connect (after auth) and ends on
disconnect — tab close, crash, and network drop all land in the same teardown,
so the fail-safe needs no rendezvous with a separate HTTP call.

Wire format: binary frames = 16k mono int16 PCM upstream; JSON text frames =
control both ways + tts_chunk downstream (protocol table in the plan doc).

Async/thread bridge: inbound PCM goes straight into the source's thread-safe
queue (push_pcm never blocks); outbound messages come from driver threads via
send_fn -> call_soon_threadsafe -> asyncio.Queue -> the sender task below.
"""
import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from core.auth import require_login_ws
from core.api_fastapi import get_system

logger = logging.getLogger(__name__)
router = APIRouter()


@router.websocket("/ws/conversation")
async def conversation_ws(websocket: WebSocket):
    if not await require_login_ws(websocket):
        return                                       # closed 4401 inside

    system = get_system()
    mgr = system.get_conversation_manager()

    # Mutual exclusion: local mode wins (user is at the box — don't yank it from
    # a tab); a prior BROWSER session gets replaced (new tab takes over — its
    # source's close() sends "bye" so the old tab shuts its side down).
    if system.conversation_mode_enabled:
        if getattr(system, "conversation_source", None) == "browser":
            logger.info("[CONV] new browser WS replacing the active one")
            mgr.stop()
        else:
            await websocket.accept()
            await websocket.send_json({"type": "error", "msg": "local conversation mode is active"})
            await websocket.close(code=4409)
            return

    await websocket.accept()
    loop = asyncio.get_running_loop()
    out_q = asyncio.Queue()

    def send_fn(msg):
        """Thread-safe, never raises — driver threads call this mid-turn."""
        try:
            loop.call_soon_threadsafe(out_q.put_nowait, msg)
        except RuntimeError:
            pass                                     # loop shutting down; teardown handles it

    src = mgr.start_browser(send_fn)
    if src is None:
        await websocket.send_json({"type": "error", "msg": "could not start conversation mode"})
        await websocket.close(code=1011)
        return

    async def _pump_out():
        while True:
            await websocket.send_json(await out_q.get())

    sender = asyncio.create_task(_pump_out())
    try:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break
            data = message.get("bytes")
            if data:
                src.push_pcm(data)
                continue
            text = message.get("text")
            if not text:
                continue
            try:
                ctl = json.loads(text)
            except ValueError:
                continue
            t = ctl.get("type")
            if t == "playback_done":
                src.on_playback_done()
            elif t == "bye":
                break
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.warning(f"[CONV] browser WS error: {e}")
    finally:
        sender.cancel()
        # Tear down the mode only if WE still own it — a replacing tab may have
        # already taken over, in which case just make sure our source is dead.
        if system.conversation_session is src:
            mgr.stop()
        else:
            try:
                src.close()
            except Exception:
                pass
        logger.info("[CONV] browser conversation WS closed")
