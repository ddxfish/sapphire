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
        audio_data = await asyncio.to_thread(system.tts.generate_audio_data, text)
        if audio_data:
            content_type = getattr(system.tts, 'audio_content_type', 'audio/ogg')
            ext = 'mp3' if 'mpeg' in content_type else 'ogg'
            return StreamingResponse(
                io.BytesIO(audio_data),
                media_type=content_type,
                headers={'Content-Disposition': f'attachment; filename="output.{ext}"'}
            )
        else:
            raise HTTPException(status_code=503, detail="TTS generation failed")
    else:
        raise HTTPException(status_code=400, detail="Invalid output_mode")


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
    """Get TTS playback status."""
    playing = getattr(system.tts, '_is_playing', False)
    return {"playing": playing}


@router.post("/api/tts/stop")
async def tts_stop(request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Stop TTS playback."""
    system.tts.stop()
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
        from core.tts.providers.elevenlabs import ElevenLabsTTSProvider
        voices = await asyncio.to_thread(ElevenLabsTTSProvider.list_voices_with_key, api_key)
        return {"voices": voices}

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

    system.web_active_inc()
    fd, temp_path = tempfile.mkstemp(suffix=".wav")
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
