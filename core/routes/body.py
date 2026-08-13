# core/routes/body.py — endpoints the Pi-based body service POSTs to.
#
# LAN-only by design. The body posts audio captured after a wakeword fire;
# brain transcribes, routes through process_llm_query (same path as the
# brain's own wakeword detector), and sends the response back through the
# body_speak tool so TTS plays on the Pi's BT speaker.
#
# Endpoint visibility + auth are both driven by the body plugin's settings
# (user/webui/plugins/body.json) — not env vars. When the plugin is not
# loaded, all endpoints return 404 (invisible to non-body installs).
# When loaded, auth is determined by the plugin's `lan_auto_accept` and
# `brain_token` settings; defaults fail closed with a 503 pointing the
# user back to the Settings UI. 2026-05-20.

import asyncio
import json
import logging
import os
import tempfile
from typing import Optional

from fastapi import APIRouter, Depends, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse

from core.api_fastapi import get_system

logger = logging.getLogger(__name__)
router = APIRouter()


def _is_body_plugin_loaded() -> bool:
    """Cheap check — is the body plugin currently active?
    Hot-disable via Settings → Plugins removes endpoints on the next request."""
    try:
        from core.plugin_loader import plugin_loader
        return "body" in plugin_loader.get_loaded_plugins()
    except Exception:
        return False


def _require_body_plugin():
    """Make all body endpoints 404 when the body plugin isn't loaded —
    a fresh-install user who never enables body sees no body surface at all."""
    if not _is_body_plugin_loaded():
        raise HTTPException(404)


def _body_settings() -> dict:
    """Read live body plugin settings. Re-read each call so the Settings UI
    takes effect without a brain restart (matches RECOVERY.md lesson #10)."""
    try:
        from core.plugin_loader import plugin_loader
        return plugin_loader.get_plugin_settings("body") or {}
    except Exception:
        return {}


def _verify_brain_auth(authorization: Optional[str]):
    """Settings-driven auth gate:
      - `lan_auto_accept` True  → no auth required (LAN trust)
      - `brain_token` set       → require Authorization: Bearer <brain_token>
      - neither                 → 503, server intentionally not configured
    503 (not 401) when neither is set — distinguishes "configure me" from
    "you sent wrong creds." User-fixable via Settings → Plugins → Body."""
    settings = _body_settings()

    if settings.get("lan_auto_accept"):
        return

    brain_token = (settings.get("brain_token") or "").strip()
    if not brain_token:
        raise HTTPException(
            503,
            "body plugin auth not configured — enable lan_auto_accept "
            "OR set brain_token in body plugin settings",
        )

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "missing bearer token")
    if authorization.split(" ", 1)[1].strip() != brain_token:
        raise HTTPException(401, "invalid bearer token")


@router.post("/api/body/wake")
async def handle_body_wake(
    audio: UploadFile = File(...),
    x_body_name: Optional[str] = Header(default="sapphire-pi", alias="X-Body-Name"),
    authorization: Optional[str] = Header(default=None),
    system=Depends(get_system),
):
    """Pi posts audio captured immediately after a wakeword fire. Brain:
      1. transcribes via whisper
      2. routes through process_llm_query (LLM + plugins)
      3. sends the response back via body_speak tool → TTS on Pi BT speaker
    Returns JSON status. The Pi logs the response but doesn't act on it
    further — the body_speak side-effect is the actual user-facing reply."""
    _require_body_plugin()
    _verify_brain_auth(authorization)
    logger.info(f"[body/wake] from {x_body_name!r}, content-type={audio.content_type}")

    # Voice privacy gate (vault v1.1): body audio routes into the active
    # chat — same rule as the web mic.
    from core.voice_privacy import stt_gate_reason
    _gate = stt_gate_reason(system.llm_chat.session_manager.get_chat_settings())
    if _gate:
        raise HTTPException(403, _gate)

    # Stick the most-recently-woken body name onto `system` so the body
    # plugin's `_resolve_body` picks the right destination on any later
    # body_speak/body_ring/body_health call that DOESN'T explicitly name
    # one. Reset on brain restart — fresh boot falls back to the
    # `default_body_name` plugin setting. 2026-05-19 multi-body fix.
    try:
        system.last_used_body = x_body_name
    except Exception:
        pass

    audio_bytes = await audio.read()
    if not audio_bytes:
        raise HTTPException(400, "empty audio upload")

    # Spool to disk — whisper transcribe_file expects a path
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp.write(audio_bytes)
        tmp_path = tmp.name

    try:
        if not getattr(system, "whisper_client", None):
            raise HTTPException(503, "STT not available on brain")

        # Transcribe — runs in a thread to keep the event loop free
        text = await asyncio.to_thread(system.whisper_client.transcribe_file, tmp_path)
        if not text or not text.strip():
            logger.info("[body/wake] no speech transcribed (silence/hallucination)")
            return {"ok": True, "transcribed": "", "response": None}
        logger.info(f"[body/wake] transcribed {len(text)} chars")

        # LLM dispatch — skip local TTS, we'll route to body. Same processing
        # path the on-brain wakeword detector uses; just different output sink.
        response = await asyncio.to_thread(
            system.process_llm_query, text.strip(), True  # skip_tts=True
        )
        if not response:
            logger.info("[body/wake] no LLM response")
            return {"ok": True, "transcribed": text, "response": None}

        # Route response back to body via the body_speak tool. We invoke it
        # explicitly through function_manager so the call works even when
        # body tools aren't in the active toolset (e.g. a non-body chat).
        try:
            fm = system.llm_chat.function_manager
            speak_result = await asyncio.to_thread(
                fm.execute_function,
                "body_speak",
                {"text": response, "body_name": x_body_name},
                None,            # scopes
                {"body_speak"},  # allowed_tools — force-allow regardless of toolset
            )
            logger.info(f"[body/wake] body_speak: {str(speak_result)[:120]}")
        except Exception as e:
            # Don't fail the request — text was processed; the TTS routing
            # just didn't make it back. Logged for debug.
            logger.error(f"[body/wake] body_speak dispatch failed: {e!r}")

        return {"ok": True, "transcribed": text, "response": response[:400]}
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


@router.get("/api/body/health")
async def body_health(_system=Depends(get_system)):
    """Lightweight liveness probe for body → brain reachability.
    Plugin-gated (404 when body plugin disabled) but intentionally
    NOT auth-gated — the Pi probes this before it knows whether auth
    is configured, and the response carries no info beyond 'I exist'."""
    _require_body_plugin()
    return {"ok": True}


# ─── SSE event stream to body (step 7) ────────────────────────────────────────
# Body subscribes once on boot, holds connection. Brain filters internal
# event_bus events and emits a stripped-down semantic stream — only the
# things a body cares about. No LLM chunks, no settings updates, no chat
# history events. Just animation cues + lifecycle markers.

# Brain event type → body-state mapping. Anything not listed is dropped.
# Body decides the visual; brain just communicates semantic state.
_BODY_STATE_MAP = {
    "ai_typing_start":           {"state": "thinking"},
    "ai_typing_end":             {"state": "idle"},
    "tool_executing":            {"state": "tool"},        # carries data.name
    "tool_complete":             {"state": "thinking"},    # back to LLM
    "tts_playing":               {"state": "speaking"},
    "tts_stopped":               {"state": "idle"},
    "continuity_task_starting":  {"state": "thinking"},
    "continuity_task_complete":  {"state": "idle"},
    "continuity_task_error":     {"state": "error"},
    "stt_processing":            {"state": "thinking"},
}


def _to_body_event(event: dict) -> Optional[dict]:
    """Brain event → body state payload. Returns None if irrelevant."""
    et = event.get("type")
    if et not in _BODY_STATE_MAP:
        return None
    payload = dict(_BODY_STATE_MAP[et])
    # Pass through useful metadata where applicable
    data = event.get("data") or {}
    if et == "tool_executing" and data.get("name"):
        payload["tool_name"] = data["name"]
    payload["src"] = et
    payload["ts"] = event.get("timestamp")
    return payload


@router.get("/api/body/events")
async def body_events_stream(
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    """SSE stream of LED-relevant state cues for body subscribers.
    Stripped subset of the brain's event bus — no chat content, no settings,
    just semantic state cues the body maps to LED animations.

    Body is expected to subscribe once on boot and hold the connection.
    Auto-reconnect logic lives on the body side."""
    _require_body_plugin()
    _verify_brain_auth(authorization)
    logger.info("[body/events] subscriber connected")

    async def generate():
        from core.event_bus import get_event_bus
        bus = get_event_bus()
        # Send an immediate connected event so subscribers know the channel
        # is alive (don't have to wait for the first real event).
        yield f"data: {json.dumps({'state': 'connected', 'src': 'sse_open'})}\n\n"
        try:
            async for event in bus.async_subscribe(replay=False):
                if await request.is_disconnected():
                    break
                payload = _to_body_event(event)
                if payload is None:
                    continue
                yield f"data: {json.dumps(payload)}\n\n"
        finally:
            logger.info("[body/events] subscriber disconnected")

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
