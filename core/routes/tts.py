# core/routes/tts.py - TTS, transcription, and upload routes
import asyncio
import io
import os
import tempfile
import logging
from pathlib import Path

from fastapi import APIRouter, Request, Depends, HTTPException, UploadFile, File
from fastapi.responses import StreamingResponse

import config
from core.auth import require_login, check_endpoint_rate
from core.api_fastapi import get_system
from core.stt.utils import can_transcribe
from core.tts.utils import validate_voice as _validate_tts_voice, default_voice as _tts_default_voice

logger = logging.getLogger(__name__)

router = APIRouter()

_TTS_MAX_CHARS = 50_000  # ~8,000 words / ~20 pages — generous for stories, blocks book dumps


# ─── VAD (Voice Activity Detection) endpoints ─────────────────────────────────

@router.get("/api/stt/vad-status")
async def vad_status(_=Depends(require_login)):
    """Report silero warmup state for the Settings UI status badge.
    Returns: {state: pending|ready|failed, reason, enabled, available}
    - state: silero system capability (set by boot warmup)
    - enabled: user preference from STT_VAD_ENABLED setting (boolean)
    - available: convenience boolean — true iff state == "ready"
    """
    from core.stt import silero_vad as _svad
    status = _svad.get_warmup_status()
    enabled = bool(getattr(config, 'STT_VAD_ENABLED', True))
    threshold = float(getattr(config, 'STT_VAD_SPEECH_THRESHOLD', 0.5))
    return {
        "state": status["state"],
        "reason": status["reason"],
        "enabled": enabled,
        "available": status["state"] == "ready",
        "threshold": threshold,
    }


@router.post("/api/stt/vad-test")
async def vad_test(request: Request, _=Depends(require_login)):
    """Record ~5s from the mic and run silero on every chunk. Returns the
    score summary + a threshold suggestion. Used by the Settings 'Test my
    voice' button. No end-of-speech cutoff — user can pause and resume to
    test how silero handles their natural speech pattern."""
    check_endpoint_rate(request, 'vad_test', max_calls=10, window=60)

    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        duration_s = float(body.get('duration_s', 5.0))
    except (TypeError, ValueError):
        duration_s = 5.0
    duration_s = max(1.0, min(10.0, duration_s))

    from core.stt import silero_vad as _svad
    # Run the synchronous mic-capture-and-score in a thread so we don't block
    # the FastAPI event loop. asyncio.to_thread is the modern idiom.
    result = await asyncio.to_thread(_svad.run_voice_test, duration_s)
    return result


@router.post("/api/tts")
async def handle_tts_speak(request: Request, _=Depends(require_login), system=Depends(get_system)):
    """TTS speak endpoint."""
    check_endpoint_rate(request, 'tts', max_calls=30, window=60)

    data = await request.json()
    text = data.get('text')
    output_mode = data.get('output_mode', 'play')

    if not text:
        raise HTTPException(status_code=400, detail="No text provided")

    if len(text) > _TTS_MAX_CHARS:
        raise HTTPException(status_code=413, detail=f"Text too long (max {_TTS_MAX_CHARS:,} characters)")

    if not config.TTS_ENABLED:
        return {"status": "success", "message": "TTS disabled"}

    if output_mode == 'play':
        system.tts.speak(text)
        return {"status": "success", "message": "Playback started."}
    elif output_mode == 'file':
        # Voice privacy gate (vault v1.1): play-mode self-gates inside
        # speak(), but file-mode calls generate_audio_data directly — and the
        # browser's non-streaming TTS path ships chat response text through
        # here. Same posture as the vault routes: 403, never 401.
        from core.voice_privacy import tts_gate_reason
        _gate = tts_gate_reason()
        if _gate:
            raise HTTPException(status_code=403, detail=_gate)
        # Optional per-request voice/pitch/speed (browser task-TTS): the
        # TTS_SPEAK payload carries the TASK's values because by the time this
        # request lands, the executor's finally has already restored the
        # operator's voice — reading global state here loses the race, always.
        _voice = data.get('voice') or None
        if _voice:
            _voice = _validate_tts_voice(_voice)
        audio_data = await asyncio.to_thread(
            system.tts.generate_audio_data, text,
            voice=_voice, speed=data.get('speed'), pitch=data.get('pitch'))
        if not audio_data:
            raise HTTPException(status_code=503, detail="TTS generation failed")

        # Optional re-encoding for downstream consumers that can't decode the
        # provider's native format. Added 2026-05-21 — Unity / FMOD can't
        # decode Ogg-Opus (only Ogg-Vorbis), and the Valheim mod needs MP3
        # or WAV for `UnityWebRequestMultimedia.GetAudioClip`. We transcode
        # via ffmpeg subprocess (cheap; ~50-100ms per request).
        out_format = (data.get('format') or '').lower().strip()
        if out_format in ('mp3', 'wav'):
            import subprocess
            content_type = 'audio/mpeg' if out_format == 'mp3' else 'audio/wav'
            ext = out_format
            ffmpeg_codec = ['-codec:a', 'libmp3lame', '-b:a', '128k'] if out_format == 'mp3' else ['-codec:a', 'pcm_s16le']
            try:
                proc = await asyncio.to_thread(
                    subprocess.run,
                    ['ffmpeg', '-loglevel', 'error', '-y', '-i', 'pipe:0', '-f', out_format, *ffmpeg_codec, 'pipe:1'],
                    input=audio_data, capture_output=True, timeout=15,
                )
                if proc.returncode != 0:
                    err = (proc.stderr or b'').decode(errors='replace')[:300]
                    logger.error(f"ffmpeg transcode to {out_format} failed: {err}")
                    raise HTTPException(status_code=503, detail=f"ffmpeg transcode failed: {err}")
                audio_data = proc.stdout
            except subprocess.TimeoutExpired:
                raise HTTPException(status_code=504, detail="ffmpeg transcode timeout")
            except FileNotFoundError:
                raise HTTPException(status_code=503, detail="ffmpeg not available on server")
        else:
            content_type = getattr(system.tts, 'audio_content_type', 'audio/ogg')
            ext = 'mp3' if 'mpeg' in content_type else 'ogg'

        return StreamingResponse(
            io.BytesIO(audio_data),
            media_type=content_type,
            headers={'Content-Disposition': f'attachment; filename="output.{ext}"'}
        )
    else:
        raise HTTPException(status_code=400, detail="Invalid output_mode")


@router.post("/api/tts/stream")
async def handle_tts_stream(request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Streaming TTS for known text (Replay button, future re-synth flows).

    Feeds the full text through SpeechChunker → chunked synth → SSE events
    in the same format chat_streaming emits (tts_stream_start / tts_chunk /
    tts_stream_end). Frontend's existing event-bus subscribers play them.

    Returns 503 when streaming is disabled OR provider lacks support — the
    client should fall back to /api/tts (whole-blob) on either case.
    """
    import json
    check_endpoint_rate(request, 'tts', max_calls=30, window=60)

    if not getattr(config, 'TTS_STREAMING_ENABLED', False):
        raise HTTPException(status_code=503, detail="Streaming TTS disabled in settings")

    if not config.TTS_ENABLED:
        raise HTTPException(status_code=503, detail="TTS disabled")

    tts = getattr(system, 'tts', None)
    provider = getattr(tts, '_provider', None) if tts else None
    if not provider or not getattr(provider, 'supports_streaming', False):
        raise HTTPException(status_code=503, detail="Provider doesn't support streaming")

    data = await request.json()
    text = (data.get('text') or '').strip()
    if not text:
        raise HTTPException(status_code=400, detail="No text provided")
    if len(text) > _TTS_MAX_CHARS:
        raise HTTPException(status_code=413, detail=f"Text too long (max {_TTS_MAX_CHARS:,})")
    voice = (data.get('voice') or '').strip() or None
    if voice:
        voice = _validate_tts_voice(voice)

    def generate():
        from core.tts.stream_pump import StreamingTTSPump
        pump = StreamingTTSPump(system=system, voice_override=voice)
        try:
            # Whole text in one push — chunker splits at sentence boundaries.
            # The final sentence (no trailing uppercase) emerges from flush.
            for ev in pump.push(text):
                yield f"data: {json.dumps(ev)}\n\n"
            for ev in pump.flush_and_close():
                yield f"data: {json.dumps(ev)}\n\n"
        except Exception as e:
            logger.error(f"[TTS-STREAM] generate failed: {e!r}", exc_info=True)
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

    return StreamingResponse(
        generate(),
        media_type='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            'X-Accel-Buffering': 'no',
        },
    )


@router.post("/api/tts/preview")
async def tts_preview(request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Generate TTS audio with custom voice/pitch/speed without changing system state."""
    check_endpoint_rate(request, 'tts', max_calls=30, window=60)  # shares TTS budget

    data = await request.json()
    text = data.get('text', 'Hello!')
    voice = data.get('voice')
    pitch = data.get('pitch')
    speed = data.get('speed')

    if len(text) > _TTS_MAX_CHARS:
        raise HTTPException(status_code=413, detail=f"Text too long (max {_TTS_MAX_CHARS:,} characters)")

    if not config.TTS_ENABLED:
        raise HTTPException(status_code=503, detail="TTS disabled")

    # Voice privacy gate — same posture as /api/tts file-mode above. Preview
    # takes arbitrary user text and hands it straight to the provider; with
    # the active chat private + a cloud TTS provider that's text egress the
    # main lane refuses. 403, never 401.
    from core.voice_privacy import tts_gate_reason
    _gate = tts_gate_reason()
    if _gate:
        raise HTTPException(status_code=403, detail=_gate)

    if voice:
        voice = _validate_tts_voice(voice)
    audio_data = await asyncio.to_thread(
        system.tts.generate_audio_data, text,
        voice=voice, speed=speed, pitch=pitch
    )

    if not audio_data:
        raise HTTPException(status_code=503, detail="TTS generation failed")

    content_type = getattr(system.tts, 'audio_content_type', 'audio/ogg')
    ext = 'mp3' if 'mpeg' in content_type else 'ogg'
    return StreamingResponse(
        io.BytesIO(audio_data),
        media_type=content_type,
        headers={'Content-Disposition': f'inline; filename="preview.{ext}"'}
    )


@router.get("/api/tts/status")
async def tts_status(request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Get TTS playback status (tts_client OR the conversation-mode sink)."""
    playing = getattr(system.tts, '_is_playing', False)
    if not playing and getattr(system, "conversation_mode_enabled", False):
        mgr = getattr(system, "_conversation_manager", None)
        drv = getattr(mgr, "driver", None)
        # Phase I isolation: only the OPERATOR's conversation (local/browser:
        # _chat_name None) counts as "playing" to the web UI. A phone call's
        # sink (explicit chat) must not light the browser's TTS state.
        if drv is not None and getattr(drv, "_chat_name", None) is None:
            sink = getattr(drv, "_active_sink", None)
            if sink is not None and getattr(sink, "_is_playing", False):
                playing = True
    return {"playing": playing, "tts_playing": playing}


@router.post("/api/tts/stop")
async def tts_stop(request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Stop TTS playback (tts_client + the conversation-mode sink)."""
    system.tts.stop()
    # Phase I isolation: this button belongs to the WEB UI — everything it stops
    # must be scoped to the operator's surface. Unscoped, it muted ALL streams,
    # cancelled ALL generation, and flushed the singleton driver's sink — which
    # during a phone call cut the caller off mid-sentence (2026-07-04, twice:
    # the second time because a git revert resurrected this unscoped version).
    try:
        _active = system.llm_chat.session_manager.get_active_chat_name()
    except Exception:
        _active = None
    # Mute the ACTIVE chat's TTS pump only — a phone call's stream (side chat)
    # keeps talking. Without cancelling the LLM (that's the separate Stop button).
    # Phase II follow-up (2026-07-05): when the operator is VIEWING a live call's
    # chat, that chat IS the active chat — but its stream feeds the PHONE surface.
    # Chat-scoping can't tell the surfaces apart, so check ownership explicitly:
    # web stop must never mute a caller mid-sentence.
    try:
        _mgr = getattr(system, "_conversation_manager", None)
        _ext = _mgr.external_chats() if _mgr else set()
    except Exception:
        _ext = set()
    try:
        if _active and _active in _ext:
            logger.info(f"[TTS-STOP] '{_active}' belongs to a live phone call — web stop leaves it alone")
        else:
            # Mirror /api/cancel: an unscoped stop (_active None) must still
            # skip live-call chats, or it mutes callers on the None-active edge.
            system.llm_chat.stop_tts_streams(chat_name=_active, exclude_chats=(None if _active else _ext))
    except Exception:
        pass
    # Conversation mode plays through a separate sink and the LLM may still be
    # streaming — but ONLY when the conversation is the operator's own
    # (local/browser: driver._chat_name None). A phone call's driver is off-limits.
    if getattr(system, "conversation_mode_enabled", False):
        mgr = getattr(system, "_conversation_manager", None)
        drv = getattr(mgr, "driver", None)
        if drv is not None and getattr(drv, "_chat_name", None) is None:
            try:
                system.cancel_generation(chat_name=_active)
            except Exception:
                pass
            sink = getattr(drv, "_active_sink", None)
            if sink is not None:
                try:
                    sink.stop()
                except Exception:
                    pass
    return {"status": "success"}


@router.post("/api/tts/test")
async def test_tts(request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Test current TTS provider availability."""
    import time
    prov_name = getattr(config, 'TTS_PROVIDER', 'none')
    provider = getattr(system.tts, 'provider', None)
    if not provider:
        return {"success": False, "provider": prov_name, "error": "No TTS provider loaded"}
    t0 = time.time()
    try:
        # Force fresh validation for explicit test (bypass cache)
        if hasattr(provider, '_validated'):
            provider._validated = None
        available = await asyncio.to_thread(provider.is_available)
    except Exception as e:
        return {"success": False, "provider": prov_name, "error": str(e)}
    elapsed = round((time.time() - t0) * 1000)
    if not available:
        error = getattr(provider, '_last_error', None) or "Provider not available"
        return {"success": False, "provider": prov_name, "error": error, "ms": elapsed}
    return {"success": True, "provider": prov_name, "ms": elapsed}


@router.get("/api/tts/voices")
async def tts_voices_get(_=Depends(require_login), system=Depends(get_system)):
    """List voices for the active TTS provider."""
    prov_name = getattr(config, 'TTS_PROVIDER', 'none')
    provider = getattr(system.tts, 'provider', None)
    base = {"provider": prov_name, "default_voice": _tts_default_voice(prov_name),
            "speed_min": getattr(provider, 'SPEED_MIN', 0.5),
            "speed_max": getattr(provider, 'SPEED_MAX', 2.5)}
    if provider and hasattr(provider, 'list_voices'):
        voices = await asyncio.to_thread(provider.list_voices)
        return {"voices": voices, **base}
    return {"voices": [], **base}


@router.post("/api/tts/voices")
async def tts_voices_post(request: Request, _=Depends(require_login), system=Depends(get_system)):
    """List voices with optional api_key for pre-save browsing."""
    data = await request.json()
    api_key = data.get('api_key', '').strip()

    # If an API key is provided, fetch voices directly (pre-save browsing)
    if api_key:
        try:
            from plugins.elevenlabs.provider import ElevenLabsTTSProvider
            voices = await asyncio.to_thread(ElevenLabsTTSProvider.list_voices_with_key, api_key)
            return {"voices": voices}
        except ImportError:
            return {"voices": [], "error": "ElevenLabs plugin not available"}

    # Otherwise use the active provider
    provider = getattr(system.tts, '_provider', None)
    if provider and hasattr(provider, 'list_voices'):
        voices = await asyncio.to_thread(provider.list_voices)
        return {"voices": voices}
    return {"voices": []}


# =============================================================================
# TRANSCRIBE / UPLOAD ROUTES
# =============================================================================

@router.post("/api/transcribe")
async def handle_transcribe(request: Request, audio: UploadFile = File(...), _=Depends(require_login), system=Depends(get_system)):
    """Transcribe audio to text."""
    check_endpoint_rate(request, 'transcribe', max_calls=20, window=60)

    ok, reason = can_transcribe(system.whisper_client)
    if not ok:
        raise HTTPException(status_code=400, detail=reason)

    # Voice privacy gate (vault v1.1): private chat + cloud STT = the mic
    # audio would leave the machine. 403 with the reason — the mic button
    # surfaces it as a toast.
    from core.voice_privacy import stt_gate_reason
    gate = stt_gate_reason(system.llm_chat.session_manager.get_chat_settings())
    if gate:
        raise HTTPException(status_code=403, detail=gate)

    # mkstemp BEFORE the inc: a raise between inc and try stranded a permanent
    # +1 on the wakeword-suppression counter -- she stops answering to her name
    # until restart (negspace N3, 2026-08-31).
    fd, temp_path = tempfile.mkstemp(suffix=".wav")
    system.web_active_inc()
    try:
        os.close(fd)
        contents = await audio.read()
        if len(contents) > 25 * 1024 * 1024:  # 25MB max
            raise HTTPException(status_code=413, detail="Audio file too large (max 25MB)")
        with open(temp_path, 'wb') as f:
            f.write(contents)
        try:
            transcribed_text = await asyncio.wait_for(
                asyncio.to_thread(system.whisper_client.transcribe_file, temp_path),
                timeout=90.0
            )
        except asyncio.TimeoutError:
            logger.warning("Transcription timed out (90s) — model may be too slow on CPU")
            raise HTTPException(status_code=504, detail="Transcription timed out — try a smaller model or lower beam size in STT settings")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Transcription error: {e}")
        raise HTTPException(status_code=500, detail="Failed to process audio")
    finally:
        system.web_active_dec()
        try:
            if os.path.exists(temp_path):
                os.unlink(temp_path)
        except Exception:
            pass
    if transcribed_text is None:
        raise HTTPException(status_code=500, detail="Transcription failed — check STT provider logs")

    # post_stt hook — mirror wakeword pipeline. Plugins that correct /
    # translate / normalize transcription need to see ALL STT input, not
    # just the wake path. Before this the browser-mic route silently
    # bypassed post_stt. H8 fix 2026-04-22.
    try:
        from core.hooks import hook_runner, HookEvent
        if hook_runner.has_handlers("post_stt"):
            import config as _cfg
            stt_event = HookEvent(input=transcribed_text, config=_cfg,
                                  metadata={"system": system})
            hook_runner.fire("post_stt", stt_event)
            transcribed_text = stt_event.input
    except Exception as e:
        logger.debug(f"post_stt hook fire failed: {e}")

    return {"text": transcribed_text, "quiet": transcribed_text == ""}


@router.post("/api/mic/active")
async def set_mic_active(request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Signal browser mic open/close to suppress wakeword during web UI recording."""
    data = await request.json()
    if data.get('active'):
        system.web_active_inc()
    else:
        system.web_active_dec()
    return {"ok": True}


@router.post("/api/upload/image")
async def handle_image_upload(image: UploadFile = File(...), _=Depends(require_login), system=Depends(get_system)):
    """Upload an image for chat."""
    import base64
    from io import BytesIO
    from core.settings_manager import settings

    allowed_ext = {'.png', '.jpg', '.jpeg', '.gif', '.webp'}
    ext = os.path.splitext(image.filename or '')[1].lower()
    if ext not in allowed_ext:
        raise HTTPException(status_code=400, detail=f"Invalid file type. Allowed: {', '.join(allowed_ext)}")

    contents = await image.read()
    if len(contents) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large. Max 10MB")

    media_types = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.gif': 'image/gif', '.webp': 'image/webp'}
    media_type = media_types.get(ext, 'image/jpeg')

    # Optional optimization
    max_width = settings.get('IMAGE_UPLOAD_MAX_WIDTH', 0)
    if max_width > 0:
        try:
            from PIL import Image
            # Guard against decompression bombs (e.g. 16k×16k PNG → gigabytes of RAM)
            Image.MAX_IMAGE_PIXELS = 25_000_000  # ~5000x5000
            img = Image.open(BytesIO(contents))
            if img.mode in ('RGBA', 'P'):
                img = img.convert('RGB')
            elif img.mode != 'RGB':
                img = img.convert('RGB')
            if img.width > max_width:
                ratio = max_width / img.width
                new_height = int(img.height * ratio)
                img = img.resize((max_width, new_height), Image.LANCZOS)
            buffer = BytesIO()
            img.save(buffer, format='JPEG', quality=85, optimize=True)
            optimized = buffer.getvalue()
            if len(optimized) < len(contents):
                contents = optimized
                media_type = 'image/jpeg'
        except ImportError:
            pass
        except Exception as e:
            logger.warning(f"Image optimization failed: {e}")

    base64_data = base64.b64encode(contents).decode('utf-8')
    return {"status": "success", "data": base64_data, "media_type": media_type, "filename": image.filename, "size": len(contents)}
