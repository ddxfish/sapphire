# core/routes/system.py - Backup, audio devices, continuity, setup wizard, avatars, system restart/shutdown
import json
import os
import time
import logging
from pathlib import Path

from fastapi import APIRouter, Request, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse

import config
from core.auth import require_login
from core.api_fastapi import get_system

logger = logging.getLogger(__name__)

router = APIRouter()

PROJECT_ROOT = Path(__file__).parent.parent.parent
STATIC_DIR = PROJECT_ROOT / "interfaces" / "web" / "static"


# =============================================================================
# BACKUP ROUTES
# =============================================================================

@router.get("/api/backup/list")
async def list_backups(request: Request, _=Depends(require_login)):
    """List all backups."""
    from core.backup import backup_manager
    return {"backups": backup_manager.list_backups()}


@router.post("/api/backup/create")
async def create_backup(request: Request, _=Depends(require_login)):
    """Create a backup."""
    from core.backup import backup_manager
    data = await request.json() or {}
    backup_type = data.get('type', 'manual')
    if backup_type not in ('daily', 'weekly', 'monthly', 'manual'):
        raise HTTPException(status_code=400, detail="Invalid backup type")

    filename = backup_manager.create_backup(backup_type)
    if filename:
        backup_manager.rotate_backups()
        return {"status": "success", "filename": filename}
    else:
        raise HTTPException(status_code=500, detail="Backup creation failed")


@router.delete("/api/backup/delete/{filename}")
async def delete_backup(filename: str, request: Request, _=Depends(require_login)):
    """Delete a backup."""
    from core.backup import backup_manager
    if backup_manager.delete_backup(filename):
        return {"status": "success", "deleted": filename}
    else:
        raise HTTPException(status_code=404, detail="Backup not found")


@router.get("/api/backup/download/{filename}")
async def download_backup(filename: str, request: Request, _=Depends(require_login)):
    """Download a backup."""
    from core.backup import backup_manager
    filepath = backup_manager.get_backup_path(filename)
    if filepath:
        return FileResponse(filepath, filename=filename, media_type='application/gzip')
    else:
        raise HTTPException(status_code=404, detail="Backup not found")


# =============================================================================
# AUDIO DEVICE ROUTES
# =============================================================================

@router.get("/api/audio/devices")
async def get_audio_devices(request: Request, _=Depends(require_login)):
    """Get audio devices."""
    from core.audio import get_device_manager
    dm = get_device_manager()
    devices = dm.query_devices(force_refresh=True)

    input_devices = []
    output_devices = []

    for dev in devices:
        dev_info = {'index': dev.index, 'name': dev.name}
        if dev.max_input_channels > 0:
            input_devices.append({**dev_info, 'channels': dev.max_input_channels, 'sample_rate': int(dev.default_samplerate), 'is_default': dev.is_default_input})
        if dev.max_output_channels > 0:
            output_devices.append({**dev_info, 'channels': dev.max_output_channels, 'sample_rate': int(dev.default_samplerate), 'is_default': dev.is_default_output})

    return {
        'input': input_devices,
        'output': output_devices,
        'configured_input': getattr(config, 'AUDIO_INPUT_DEVICE', None),
        'configured_output': getattr(config, 'AUDIO_OUTPUT_DEVICE', None),
    }


@router.post("/api/audio/test-input")
async def test_audio_input(request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Test audio input device."""
    import asyncio
    data = await request.json() or {}
    device_index = data.get('device_index')
    duration = min(data.get('duration', 3.0), 5.0)

    if device_index == 'auto' or device_index == '':
        device_index = None
    elif device_index is not None:
        try:
            device_index = int(device_index)
        except (ValueError, TypeError):
            device_index = None

    def _test_input():
        from core.audio import get_device_manager, classify_audio_error
        wakeword_paused = False
        try:
            if hasattr(system, 'wake_word_recorder') and system.wake_word_recorder:
                if hasattr(system.wake_word_recorder, 'pause_recording'):
                    wakeword_paused = system.wake_word_recorder.pause_recording()
                    if wakeword_paused:
                        time.sleep(0.3)
        except Exception:
            pass
        try:
            dm = get_device_manager()
            return dm.test_input_device_safe(device_index=device_index, duration=duration)
        except Exception as e:
            return {'success': False, 'error': classify_audio_error(e)}
        finally:
            if wakeword_paused:
                try:
                    time.sleep(0.2)
                    system.wake_word_recorder.resume_recording()
                except Exception:
                    pass

    import asyncio
    return await asyncio.to_thread(_test_input)


@router.post("/api/audio/test-output")
async def test_audio_output(request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Test audio output device."""
    import asyncio
    data = await request.json() or {}
    device_index = data.get('device_index')
    duration = min(data.get('duration', 0.5), 2.0)
    frequency = data.get('frequency', 440)

    if device_index == 'auto' or device_index == '' or device_index is None:
        device_index = None
    else:
        try:
            device_index = int(device_index)
        except (ValueError, TypeError):
            device_index = None

    def _test_output():
        import numpy as np
        import sounddevice as sd

        # Pause wakeword stream to avoid audio device conflict
        wakeword_paused = False
        try:
            if hasattr(system, 'wake_word_recorder') and system.wake_word_recorder:
                if hasattr(system.wake_word_recorder, 'pause_recording'):
                    wakeword_paused = system.wake_word_recorder.pause_recording()
                    if wakeword_paused:
                        time.sleep(0.3)
        except Exception:
            pass

        try:
            sample_rate = None
            default_rate = 44100
            if device_index is not None:
                try:
                    dev_info = sd.query_devices(device_index)
                    default_rate = int(dev_info['default_samplerate'])
                except Exception:
                    pass

            for rate in [default_rate, 48000, 44100, 32000, 24000, 22050, 16000]:
                try:
                    stream = sd.OutputStream(device=device_index, samplerate=rate, channels=1, dtype=np.float32)
                    stream.close()
                    sample_rate = rate
                    break
                except Exception:
                    continue

            if sample_rate is None:
                return {'success': False, 'error': 'Device does not support any common sample rate'}

            t = np.linspace(0, duration, int(sample_rate * duration), False)
            tone = np.sin(2 * np.pi * frequency * t)
            fade_samples = int(sample_rate * 0.02)
            fade_in = np.linspace(0, 1, fade_samples)
            fade_out = np.linspace(1, 0, fade_samples)
            tone[:fade_samples] *= fade_in
            tone[-fade_samples:] *= fade_out
            tone = (tone * 0.5 * 32767).astype(np.int16)

            sd.play(tone, sample_rate, device=device_index)
            sd.wait()
            return {'success': True, 'duration': duration, 'frequency': frequency, 'sample_rate': sample_rate}
        finally:
            if wakeword_paused:
                try:
                    time.sleep(0.2)
                    system.wake_word_recorder.resume_recording()
                except Exception:
                    pass

    return await asyncio.to_thread(_test_output)


# =============================================================================
# CONTINUITY ROUTES
# =============================================================================

@router.get("/api/continuity/tasks")
async def list_continuity_tasks(request: Request, _=Depends(require_login), system=Depends(get_system)):
    """List continuity tasks. Optional ?heartbeat=true/false or ?type=daemon/webhook filter."""
    if not hasattr(system, 'continuity_scheduler') or not system.continuity_scheduler:
        return {"tasks": []}
    tasks = system.continuity_scheduler.list_tasks()

    # Type filter (new)
    type_filter = request.query_params.get("type")
    if type_filter:
        tasks = [t for t in tasks if t.get("type", "task") == type_filter]

    # Legacy heartbeat filter (backward compat) — excludes daemon/webhook types
    hb_filter = request.query_params.get("heartbeat")
    if hb_filter is not None:
        want_hb = hb_filter.lower() in ("true", "1", "yes")
        tasks = [t for t in tasks if t.get("heartbeat", False) == want_hb
                 and t.get("type", "task") in ("task", "heartbeat")]
    return {"tasks": tasks}


@router.post("/api/continuity/tasks")
async def create_continuity_task(request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Create a continuity task."""
    if not hasattr(system, 'continuity_scheduler') or not system.continuity_scheduler:
        raise HTTPException(status_code=503, detail="Continuity scheduler not available")
    data = await request.json()
    task_id = system.continuity_scheduler.create_task(data)
    return {"status": "success", "task_id": task_id}


@router.get("/api/continuity/tasks/{task_id}")
async def get_continuity_task(task_id: str, request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Get a continuity task."""
    if not hasattr(system, 'continuity_scheduler') or not system.continuity_scheduler:
        raise HTTPException(status_code=503, detail="Continuity scheduler not available")
    task = system.continuity_scheduler.get_task(task_id)
    if task:
        return task
    else:
        raise HTTPException(status_code=404, detail="Task not found")


@router.put("/api/continuity/tasks/{task_id}")
async def update_continuity_task(task_id: str, request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Update a continuity task."""
    if not hasattr(system, 'continuity_scheduler') or not system.continuity_scheduler:
        raise HTTPException(status_code=503, detail="Continuity scheduler not available")
    data = await request.json()
    if system.continuity_scheduler.update_task(task_id, data):
        return {"status": "success"}
    else:
        raise HTTPException(status_code=404, detail="Task not found")


@router.delete("/api/continuity/tasks/{task_id}")
async def delete_continuity_task(task_id: str, request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Delete a continuity task."""
    if not hasattr(system, 'continuity_scheduler') or not system.continuity_scheduler:
        raise HTTPException(status_code=503, detail="Continuity scheduler not available")
    if system.continuity_scheduler.delete_task(task_id):
        return {"status": "success"}
    else:
        raise HTTPException(status_code=404, detail="Task not found")


@router.post("/api/continuity/tasks/{task_id}/run")
def run_continuity_task(task_id: str, request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Manually run a continuity task. Sync so it runs in threadpool, not blocking event loop."""
    if not hasattr(system, 'continuity_scheduler') or not system.continuity_scheduler:
        raise HTTPException(status_code=503, detail="Continuity scheduler not available")
    result = system.continuity_scheduler.run_task_now(task_id)
    return result


@router.get("/api/continuity/status")
async def get_continuity_status(request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Get continuity scheduler status."""
    if not hasattr(system, 'continuity_scheduler') or not system.continuity_scheduler:
        return {"running": False}
    return system.continuity_scheduler.get_status()


@router.get("/api/continuity/activity")
async def get_continuity_activity(request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Get continuity activity log."""
    if not hasattr(system, 'continuity_scheduler') or not system.continuity_scheduler:
        return {"activity": []}
    limit = int(request.query_params.get("limit", 50))
    return {"activity": system.continuity_scheduler.get_activity(limit)}


@router.get("/api/continuity/timeline")
async def get_continuity_timeline(request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Get continuity task timeline (future only, legacy)."""
    if not hasattr(system, 'continuity_scheduler') or not system.continuity_scheduler:
        return {"timeline": []}
    hours = int(request.query_params.get("hours", 24))
    return {"timeline": system.continuity_scheduler.get_timeline(hours)}


@router.get("/api/continuity/merged-timeline")
async def get_continuity_merged_timeline(request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Get merged timeline: past activity + future schedule with NOW marker."""
    if not hasattr(system, 'continuity_scheduler') or not system.continuity_scheduler:
        return {"now": None, "past": [], "future": []}
    hours_back = int(request.query_params.get("hours_back", 12))
    hours_ahead = int(request.query_params.get("hours_ahead", 12))
    return system.continuity_scheduler.get_merged_timeline(hours_back, hours_ahead)


# =============================================================================
# SETUP WIZARD ROUTES
# =============================================================================

@router.get("/api/setup/provider-status")
async def provider_status(request: Request, _=Depends(require_login)):
    """Check if STT/TTS providers are loaded and ready (not null)."""
    system = get_system()
    stt_status = "disabled"
    tts_status = "disabled"
    stt_provider = getattr(config, 'STT_PROVIDER', 'none')
    tts_provider = getattr(config, 'TTS_PROVIDER', 'none')

    if stt_provider and stt_provider != 'none':
        try:
            if hasattr(system, 'whisper_client') and system.whisper_client.is_available():
                stt_status = "ready"
            else:
                stt_status = "loading"
        except Exception:
            stt_status = "loading"

    if tts_provider and tts_provider != 'none':
        try:
            if hasattr(system, 'tts') and hasattr(system.tts, '_provider') and system.tts._provider.is_available():
                tts_status = "ready"
            else:
                tts_status = "loading"
        except Exception:
            tts_status = "loading"

    return {"stt": stt_status, "tts": tts_status}


@router.get("/api/setup/check-packages")
async def check_packages(request: Request, _=Depends(require_login)):
    """Check optional packages. Returns format expected by setup wizard UI."""
    checks = {
        "tts": {"package": "Kokoro TTS", "requirements": "install/requirements-tts.txt", "mod": "kokoro"},
        "stt": {"package": "Faster Whisper", "requirements": "install/requirements-stt.txt", "mod": "faster_whisper"},
        "wakeword": {"package": "OpenWakeWord", "requirements": "install/requirements-wakeword.txt", "mod": "openwakeword"},
    }
    packages = {}
    for key, info in checks.items():
        try:
            __import__(info["mod"])
            installed = True
        except ImportError:
            installed = False
        packages[key] = {"installed": installed, "package": info["package"], "requirements": info["requirements"]}
    return {"packages": packages}


@router.get("/api/setup/wizard-step")
async def get_wizard_step(request: Request, _=Depends(require_login)):
    """Get wizard step."""
    from core.settings_manager import settings as sm
    managed = sm.is_managed()
    docker = sm.is_docker()
    return {"step": getattr(config, 'SETUP_WIZARD_STEP', 'complete'), "managed": managed, "docker": docker}


@router.put("/api/setup/wizard-step")
async def set_wizard_step(request: Request, _=Depends(require_login)):
    """Set wizard step."""
    from core.settings_manager import settings
    data = await request.json()
    step = data.get('step', 'complete')
    settings.set('SETUP_WIZARD_STEP', step, persist=True)
    return {"status": "success", "step": step}


# =============================================================================
# AVATAR ROUTES
# =============================================================================

@router.get("/api/avatars")
async def get_avatars(request: Request, _=Depends(require_login)):
    """Get avatar paths."""
    avatar_dir = PROJECT_ROOT / 'user' / 'public' / 'avatars'
    static_dir = STATIC_DIR / 'users'

    result = {}
    for role in ('user', 'assistant'):
        custom = list(avatar_dir.glob(f'{role}.*')) if avatar_dir.exists() else []
        if custom:
            ext = custom[0].suffix
            result[role] = f"/user-assets/avatars/{role}{ext}"
        else:
            for ext in ('.webp', '.png', '.jpg'):
                if (static_dir / f'{role}{ext}').exists():
                    result[role] = f"/static/users/{role}{ext}"
                    break
            else:
                result[role] = None
    return result


@router.post("/api/avatar/upload")
async def upload_avatar(file: UploadFile = File(...), role: str = Form(...), _=Depends(require_login)):
    """Upload avatar."""
    if role not in ('user', 'assistant'):
        raise HTTPException(status_code=400, detail="Invalid role")

    allowed_ext = {'.png', '.jpg', '.jpeg', '.gif', '.webp'}
    ext = os.path.splitext(file.filename or '')[1].lower()
    if ext not in allowed_ext:
        raise HTTPException(status_code=400, detail="Invalid file type")

    contents = await file.read()
    if len(contents) > 4 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large. Max 4MB")

    avatar_dir = PROJECT_ROOT / 'user' / 'public' / 'avatars'
    avatar_dir.mkdir(parents=True, exist_ok=True)

    # Delete existing
    existing = list(avatar_dir.glob(f'{role}.*'))
    for old_file in existing:
        try:
            old_file.unlink()
        except Exception:
            pass

    save_path = avatar_dir / f'{role}{ext}'
    with open(save_path, 'wb') as f:
        f.write(contents)

    return {"status": "success", "path": f"/user-assets/avatars/{role}{ext}"}


@router.get("/api/avatar/check/{role}")
async def check_avatar(role: str, request: Request, _=Depends(require_login)):
    """Check if custom avatar exists."""
    if role not in ('user', 'assistant'):
        raise HTTPException(status_code=400, detail="Invalid role")

    avatar_dir = PROJECT_ROOT / 'user' / 'public' / 'avatars'
    existing = list(avatar_dir.glob(f'{role}.*')) if avatar_dir.exists() else []

    if existing:
        ext = existing[0].suffix
        return {"exists": True, "path": f"/user-assets/avatars/{role}{ext}"}
    return {"exists": False, "path": None}


# =============================================================================
# SYSTEM MANAGEMENT ROUTES
# =============================================================================

@router.post("/api/system/restart")
async def request_system_restart(request: Request, _=Depends(require_login)):
    """Request system restart."""
    from core.api_fastapi import get_restart_callback
    callback = get_restart_callback()
    if not callback:
        raise HTTPException(status_code=503, detail="Restart not available")
    callback()
    return {"status": "restarting", "message": "Restart initiated"}


@router.post("/api/system/shutdown")
async def request_system_shutdown(request: Request, _=Depends(require_login)):
    """Request system shutdown."""
    from core.api_fastapi import get_shutdown_callback
    callback = get_shutdown_callback()
    if not callback:
        raise HTTPException(status_code=503, detail="Shutdown not available")
    callback()
    return {"status": "shutting_down", "message": "Shutdown initiated"}


# =============================================================================
# UPDATE ROUTES
# =============================================================================

@router.get("/api/system/update-check")
async def check_for_update(request: Request, _=Depends(require_login)):
    """Check GitHub for a newer Sapphire version."""
    from core.updater import updater
    from core.settings_manager import settings
    status = updater.check_for_update(force=True)
    status['docker'] = settings.is_docker()
    status['managed'] = settings.is_managed()
    return status


@router.post("/api/system/update")
async def do_update(request: Request, _=Depends(require_login)):
    """Run git pull to update Sapphire, then restart."""
    from core.updater import updater
    from core.settings_manager import settings

    if settings.is_docker() or settings.is_managed():
        raise HTTPException(status_code=403, detail="Use docker compose pull to update Docker installations")

    success, message = updater.do_update()
    if not success:
        raise HTTPException(status_code=500, detail=message)

    # Trigger restart to load new code
    from core.api_fastapi import get_restart_callback
    callback = get_restart_callback()
    if callback:
        callback()

    return {"status": "updated", "message": message}


# =============================================================================
# METRICS ROUTES
# =============================================================================

@router.get("/api/metrics/enabled")
async def metrics_enabled(request: Request, _=Depends(require_login)):
    """Check if metrics tracking is enabled."""
    return {"enabled": getattr(config, 'METRICS_ENABLED', True)}


@router.put("/api/metrics/enabled")
async def set_metrics_enabled(request: Request, _=Depends(require_login)):
    """Toggle metrics tracking."""
    from core.settings_manager import settings
    data = await request.json()
    enabled = bool(data.get("enabled", True))
    settings.set("METRICS_ENABLED", enabled)
    return {"enabled": enabled}


@router.get("/api/metrics/summary")
async def metrics_summary(request: Request, _=Depends(require_login)):
    """Aggregate token usage summary."""
    from core.metrics import metrics
    days = int(request.query_params.get("days", 30))
    return metrics.summary(days=days)


@router.get("/api/metrics/breakdown")
async def metrics_breakdown(request: Request, _=Depends(require_login)):
    """Token usage broken down by model."""
    from core.metrics import metrics
    days = int(request.query_params.get("days", 30))
    return {"models": metrics.breakdown_by_model(days=days)}


@router.get("/api/metrics/daily")
async def metrics_daily(request: Request, _=Depends(require_login)):
    """Daily token usage for charting."""
    from core.metrics import metrics
    days = int(request.query_params.get("days", 30))
    return {"daily": metrics.daily_usage(days=days)}


# =============================================================================
# EVENT ROUTES (Daemons + Webhooks)
# =============================================================================

@router.get("/api/events/sources")
async def get_event_sources(request: Request, _=Depends(require_login)):
    """Get available daemon event sources from loaded plugins."""
    from core.plugin_loader import plugin_loader
    return {"sources": plugin_loader.get_event_sources()}


@router.post("/api/events/emit/{source_name}")
async def emit_event(source_name: str, request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Emit a daemon event to trigger matching tasks. Used by daemon plugins."""
    if not hasattr(system, 'continuity_scheduler') or not system.continuity_scheduler:
        raise HTTPException(status_code=503, detail="Scheduler not available")
    data = await request.json()
    event_data = json.dumps(data) if isinstance(data, dict) else str(data)

    from core.plugin_loader import plugin_loader
    plugin_loader.emit_daemon_event(source_name, event_data)
    return {"status": "emitted", "source": source_name}


@router.api_route("/api/events/webhook/{path:path}", methods=["GET", "POST", "PUT"])
async def webhook_endpoint(path: str, request: Request, system=Depends(get_system)):
    """Webhook endpoint — no auth required. Matches path to webhook tasks."""
    if not hasattr(system, 'continuity_scheduler') or not system.continuity_scheduler:
        raise HTTPException(status_code=503, detail="Scheduler not available")

    method = request.method
    task = system.continuity_scheduler.find_webhook_task(path, method)
    if not task:
        raise HTTPException(status_code=404, detail=f"No webhook configured for {method} /{path}")

    # Verify secret if task has one configured
    trigger_config = task.get("trigger_config", {})
    webhook_secret = trigger_config.get("secret")
    if webhook_secret:
        import hashlib, hmac
        auth_header = request.headers.get("x-webhook-secret", "")
        sig_header = request.headers.get("x-hub-signature-256", "")
        if sig_header:
            # GitHub-style HMAC: x-hub-signature-256: sha256=<hex>
            raw_body = await request.body()
            expected = "sha256=" + hmac.new(webhook_secret.encode(), raw_body, hashlib.sha256).hexdigest()
            if not hmac.compare_digest(sig_header, expected):
                raise HTTPException(status_code=403, detail="Invalid webhook signature")
        elif auth_header:
            # Simple secret comparison
            if not hmac.compare_digest(auth_header, webhook_secret):
                raise HTTPException(status_code=403, detail="Invalid webhook secret")
        else:
            raise HTTPException(status_code=403, detail="Webhook secret required but not provided")

    # Payload size limit (1MB)
    content_length = int(request.headers.get("content-length", 0))
    if content_length > 1_048_576:
        raise HTTPException(status_code=413, detail="Payload too large (max 1MB)")

    # Build event data from request
    content_type = request.headers.get("content-type", "")
    if "json" in content_type:
        try:
            body = await request.json()
            event_data = json.dumps(body, indent=2)
        except Exception:
            event_data = (await request.body()).decode("utf-8", errors="replace")
    elif method in ("POST", "PUT"):
        raw = await request.body()
        if len(raw) > 1_048_576:
            raise HTTPException(status_code=413, detail="Payload too large (max 1MB)")
        event_data = raw.decode("utf-8", errors="replace")
    else:
        # GET — use query params
        event_data = json.dumps(dict(request.query_params))

    client_ip = request.client.host if request.client else "unknown"
    logger.info(f"[Webhook] {method} /{path} from {client_ip} (task: {task.get('name')})")

    result = system.continuity_scheduler.fire_event_task(task["id"], event_data)

    from core.event_bus import publish, Events
    publish(Events.WEBHOOK_FIRED, {"path": path, "method": method, "task_id": task["id"]})

    return {"status": "triggered", "task": task["name"], "queued": result.get("queued", False)}
