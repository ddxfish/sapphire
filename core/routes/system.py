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

    # Hold the backup lock across create + rotate so this manual trigger
    # can't interleave with the 3am scheduled cycle. Witch-hunt 2026-04-21
    # finding R5 — without this, overlapping runs could delete an in-flight
    # partial via rotation mtime sort.
    # Health gate: don't create new backups while corruption sentinels are
    # active — the whole point is to preserve last-known-good. R1.
    if backup_manager._active_corruption_sentinels():
        raise HTTPException(status_code=409, detail=(
            "Corruption sentinel active — backup creation halted to preserve "
            "last-known-good tarballs. See user/health/CORRUPT_*.flag."
        ))
    with backup_manager._backup_op_lock:
        filename = backup_manager.create_backup(backup_type)
        if filename:
            backup_manager.rotate_backups()
            return {"status": "success", "filename": filename}
    # Explain the most common failure: nothing left after exclusions.
    try:
        if backup_manager.estimate_size().get("total_bytes", 1) == 0:
            raise HTTPException(status_code=400, detail="Backup would be empty — your exclude "
                                "patterns match every file. Check 'Exclude from backups'.")
    except HTTPException:
        raise
    except Exception:
        pass
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


@router.post("/api/backup/estimate")
async def estimate_backup(request: Request, _=Depends(require_login)):
    """Estimate the uncompressed backup size with the given (live/unsaved)
    exclude patterns + a per-folder breakdown — for tuning excludes on the page."""
    from core.backup import backup_manager
    data = await request.json() or {}
    patterns = data.get("patterns")
    if patterns is not None and not isinstance(patterns, list):
        patterns = None
    extra = data.get("extra_patterns")
    if not isinstance(extra, list):
        extra = []
    clean = None if patterns is None else [str(p).strip() for p in patterns if str(p).strip()]
    extra = [str(p).strip() for p in extra if str(p).strip()]
    return backup_manager.estimate_size(patterns=clean, extra_patterns=extra)


def _schedule_restart():
    """Trigger a restart shortly after the response flushes (mirrors the update
    route). main.py applies the staged restore before re-spawning sapphire.py."""
    import asyncio
    from core.api_fastapi import get_restart_callback
    cb = get_restart_callback()
    if not cb:
        raise HTTPException(status_code=503, detail="Restart not available — cannot apply restore")

    async def _delayed():
        await asyncio.sleep(0.8)
        try:
            cb()
        except Exception:
            pass
    asyncio.create_task(_delayed())


def _stage_restore_from(source_path, password: str, source_label: str, trusted: bool = False):
    """Decrypt (if needed), validate (real tar.gz rooted at user/), and stage the
    backup for a restart-time swap. Returns the archive's top-level entries.
    `trusted` = the user's own backup (faithful extract); False = uploaded (strict).
    Raises HTTPException on any problem (wrong password, not a backup, etc.)."""
    import tarfile
    import uuid
    from core import backup_crypto
    from core import restore as restore_mod
    restore_mod.RESTORE_DIR.mkdir(parents=True, exist_ok=True)
    tar_path = restore_mod.RESTORE_DIR / f"staging.{uuid.uuid4().hex}.tar.gz"

    try:
        if backup_crypto.is_encrypted_backup(source_path):
            if not password:
                raise HTTPException(status_code=400, detail="This backup is encrypted — a password is required")
            try:
                backup_crypto.decrypt_file(source_path, tar_path, password)
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e))
        else:
            import shutil as _sh
            _sh.copy2(source_path, tar_path)
        try:
            tar_path.chmod(0o600)  # plaintext at rest — restrict before it lingers
        except OSError:
            pass

        try:
            roots = restore_mod.validate_tar(tar_path)
        except (ValueError, tarfile.TarError) as e:
            raise HTTPException(status_code=400, detail=f"Not a valid Sapphire backup: {e}")

        restore_mod.request_restore(tar_path, source=source_label, trusted=trusted)
        return roots
    except HTTPException:
        raise
    except Exception as e:
        # Any unexpected failure (corrupt header, disk, etc.) → clean 400, not 500.
        raise HTTPException(status_code=400, detail=f"Could not read backup: {e}")
    finally:
        # Never leak the plaintext staging file on a failure (success moved it away).
        if tar_path.exists():
            tar_path.unlink(missing_ok=True)


@router.post("/api/backup/restore")
async def restore_backup(request: Request, _=Depends(require_login)):
    """Restore one of the existing backups over user/. Validates + decrypts (if
    encrypted), stages it, and restarts — main.py swaps user/ before sapphire
    re-spawns (the current user/ is kept as user.old for rollback)."""
    from core.backup import backup_manager
    data = await request.json() or {}
    filename = data.get("filename")
    password = data.get("password", "")
    if not filename:
        raise HTTPException(status_code=400, detail="filename required")
    path = backup_manager.get_backup_path(filename)
    if not path:
        raise HTTPException(status_code=404, detail="Backup not found")
    roots = _stage_restore_from(path, password, f"backup:{filename}", trusted=True)
    _schedule_restart()
    return {"status": "restoring", "roots": roots}


@router.post("/api/backup/restore-upload")
async def restore_backup_upload(request: Request, file: UploadFile = File(...),
                                password: str = Form(""), _=Depends(require_login)):
    """Restore from an uploaded .tar.gz / .sapphirebak of a user/ folder."""
    import uuid
    from core import restore as restore_mod
    restore_mod.RESTORE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = restore_mod.RESTORE_DIR / f"upload.{uuid.uuid4().hex}.tmp"   # per-request → no collision
    try:
        with open(tmp, "wb") as f:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                f.write(chunk)
        try:
            tmp.chmod(0o600)
        except OSError:
            pass
        roots = _stage_restore_from(tmp, password, f"upload:{file.filename}")
    finally:
        tmp.unlink(missing_ok=True)
    _schedule_restart()
    return {"status": "restoring", "roots": roots}


@router.get("/api/backup/restore-result")
async def get_restore_result(request: Request, _=Depends(require_login)):
    """Outcome of the last restore, for the post-reboot banner. {} if none."""
    from core import restore as restore_mod
    return restore_mod.read_restore_result(clear=False) or {}


@router.delete("/api/backup/restore-result")
async def clear_restore_result(request: Request, _=Depends(require_login)):
    """Dismiss the restore-result banner."""
    from core import restore as restore_mod
    restore_mod.read_restore_result(clear=True)
    return {"status": "cleared"}


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
    """Return cached update status. Fires a background GitHub check if cache is stale.
    Non-blocking: the dashboard shouldn't wait 4s on a network round-trip.
    Users who want a fresh check can call ?force=1 (or POST /api/system/update-check-now)."""
    from core.updater import updater
    from core.settings_manager import settings
    force = request.query_params.get('force') in ('1', 'true', 'yes')
    if force:
        status = updater.check_for_update(force=True)
    else:
        updater.check_for_update_async()
        status = updater.status()
    status['docker'] = settings.is_docker()
    status['managed'] = settings.is_managed()
    return status


@router.post("/api/system/update")
async def do_update(request: Request, _=Depends(require_login)):
    """Schedule a deferred update. Pre-flights everything; refuses with a
    specific reason if anything's weird. On success, writes a pending-update
    marker and requests restart — main.py runs the pull + pip install before
    re-spawning sapphire.py. Result is readable via /api/system/last-update-result.
    """
    from core.updater import updater
    from core.settings_manager import settings
    import asyncio

    if settings.is_docker() or settings.is_managed():
        raise HTTPException(status_code=403, detail="Use docker compose pull to update Docker installations")

    success, message = updater.do_update()
    if not success:
        raise HTTPException(status_code=400, detail=message)

    # Return the HTTP response BEFORE triggering the restart — otherwise the
    # socket can be torn down mid-response and the client sees "update failed"
    # when it actually scheduled fine. Schedule restart on a short delay so
    # the response has time to flush.
    from core.api_fastapi import get_restart_callback
    callback = get_restart_callback()
    if callback:
        async def _delayed_restart():
            await asyncio.sleep(0.5)
            try:
                callback()
            except Exception:
                pass
        asyncio.create_task(_delayed_restart())

    return {"status": "scheduled", "message": message}


@router.get("/api/system/last-update-result")
async def last_update_result(request: Request, _=Depends(require_login)):
    """Return the result of the most recent deferred update attempt, then
    clear it so the UI only shows the toast once per update cycle."""
    from core.updater import read_last_update_result
    clear = request.query_params.get('clear', '1') in ('1', 'true', 'yes')
    result = read_last_update_result(clear=clear)
    return {"result": result}


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
    # persist — a privacy-adjacent toggle silently reverting on restart is
    # the wrong kind of surprise (HDF scout, 2026-07-19).
    settings.set("METRICS_ENABLED", enabled, persist=True)
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


# =============================================================================
# DASHBOARD HERO — system-info readout
# =============================================================================

def _format_uptime(seconds: float) -> str:
    """Compact uptime string. e.g. '3d 4h 12m', '5h 22m', '14m', '47s'."""
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m}m"
    h, m = divmod(m, 60)
    if h < 24:
        return f"{h}h {m}m"
    d, h = divmod(h, 24)
    return f"{d}d {h}h {m}m"


@router.get("/api/dashboard/system-info")
async def dashboard_system_info(_=Depends(require_login)):
    """Lightweight stats for the dashboard hero: process memory, disk usage
    on the volume Sapphire's user/ lives on, thread count, uptime.
    Cross-platform via psutil (Linux/Mac/Windows). Cheap to call (~ms)."""
    try:
        import psutil
    except ImportError:
        raise HTTPException(status_code=500, detail="psutil not installed")

    proc = psutil.Process()
    user_dir = PROJECT_ROOT / "user"
    # Anchor disk_usage on a path that exists; fall back to PROJECT_ROOT if
    # user/ is somehow missing (fresh install before first boot).
    target = user_dir if user_dir.exists() else PROJECT_ROOT
    try:
        du = psutil.disk_usage(str(target))
    except Exception:
        du = None

    mem_mb = round(proc.memory_info().rss / 1024 / 1024)
    uptime_seconds = max(0.0, time.time() - proc.create_time())

    # Pull the backup schedule hour so the Backups panel can show
    # "Daily 03:00" without a second round-trip.
    try:
        backups_hour = int(getattr(config, 'BACKUPS_HOUR', 3))
    except Exception:
        backups_hour = 3

    display_name = (getattr(config, 'DASHBOARD_DISPLAY_NAME', '') or 'Sapphire').strip() or 'Sapphire'

    return {
        "mem_mb": mem_mb,
        "threads": proc.num_threads(),
        "uptime_seconds": int(uptime_seconds),
        "uptime_str": _format_uptime(uptime_seconds),
        "disk_used_gb": round(du.used / 1024 ** 3, 1) if du else None,
        "disk_total_gb": round(du.total / 1024 ** 3, 1) if du else None,
        "disk_free_gb": round(du.free / 1024 ** 3, 1) if du else None,
        "disk_pct": round(du.percent, 1) if du else None,
        "backups_hour": backups_hour,
        "display_name": display_name,
    }


@router.get("/api/dashboard/component-status")
async def dashboard_component_status(_=Depends(require_login)):
    """Health pills for the dashboard hero — embeddings, TTS, STT, wakeword.
    Each returns: 'ok' (configured & alive), 'idle' (configured off / Null
    provider), 'warn' (configured but provider unhealthy), 'error' (system
    expects it but it's missing). Frontend mood-derives off this set."""
    system = get_system()

    def _tts_status() -> str:
        try:
            tts = getattr(system, 'tts', None)
            if tts is None:
                return 'idle' if not getattr(config, 'TTS_ENABLED', False) else 'error'
            provider_name = type(getattr(tts, '_provider', None)).__name__.lower()
            if 'null' in provider_name:
                return 'idle'
            return 'ok'
        except Exception:
            return 'warn'

    def _stt_status() -> str:
        try:
            if not getattr(config, 'STT_ENABLED', False):
                return 'idle'
            recorder = getattr(system, 'whisper_recorder', None)
            if recorder is None:
                return 'error'
            if 'null' in type(recorder).__name__.lower():
                return 'idle'
            return 'ok'
        except Exception:
            return 'warn'

    def _wakeword_status() -> str:
        try:
            if not getattr(config, 'WAKEWORD_ENABLED', False):
                return 'idle'
            detector = getattr(system, 'wake_detector', None)
            if detector is None:
                return 'error'
            if 'null' in type(detector).__name__.lower():
                return 'idle'
            return 'ok'
        except Exception:
            return 'warn'

    def _embedding_status() -> str:
        try:
            provider = (getattr(config, 'EMBEDDING_PROVIDER', '') or '').lower()
            if not provider or provider == 'none':
                return 'idle'
            return 'ok'
        except Exception:
            return 'warn'

    return {
        'tts': _tts_status(),
        'stt': _stt_status(),
        'ww':  _wakeword_status(),
        'emb': _embedding_status(),
    }


# =============================================================================
# API TOKENS — named bearer tokens for external integrations
# =============================================================================
# Added 2026-05-21. Lets admins mint per-integration tokens (Valheim mod,
# scripts, etc.) instead of sharing the bcrypt password hash as an API key.
# Full token value is returned ONCE at creation; subsequent reads show only
# the name + last4. See core/api_tokens.py for the manager.


@router.get("/api/system/api-tokens")
async def list_api_tokens(_=Depends(require_login)):
    """List API tokens (safe — full token values are MASKED, only last4 shown)."""
    from core.api_tokens import api_tokens
    return {"tokens": api_tokens.list_safe()}


@router.post("/api/system/api-tokens")
async def create_api_token(request: Request, _=Depends(require_login)):
    """Mint a new API token. Returns the FULL token in the response body —
    this is the ONLY moment it's exposed. The frontend must show-and-copy
    it to the user; subsequent list_safe() calls only return last4."""
    from core.api_tokens import api_tokens
    data = await request.json()
    name = (data.get('name') or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Token name required")
    try:
        entry = api_tokens.create(name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    # Return the full token + metadata. Frontend MUST show-once.
    return {
        "id": entry["id"],
        "name": entry["name"],
        "token": entry["token"],            # <-- one-time reveal
        "created_at": entry["created_at"],
        "last_used_at": entry["last_used_at"],
    }


@router.delete("/api/system/api-tokens/{token_id}")
async def revoke_api_token(token_id: str, _=Depends(require_login)):
    """Revoke a token by ID."""
    from core.api_tokens import api_tokens
    if api_tokens.revoke(token_id):
        return {"status": "revoked"}
    raise HTTPException(status_code=404, detail="Token not found")


@router.get("/api/system/integrity")
async def system_integrity(_=Depends(require_login)):
    """Verify the core install against the shipped manifest (SHA256). No git needed."""
    from core.integrity import verify
    return verify()


@router.post("/api/system/integrity/repair")
async def system_integrity_repair(_=Depends(require_login)):
    """Restore files that don't match the manifest (git installs), with per-file status."""
    from core.integrity import repair
    return repair()


@router.put("/api/runtime/true-speech")
async def toggle_true_speech(request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Enter/exit true speech mode (conversation mode — continuous listen, no wakeword).

    System-level + ephemeral. Local mic, headphone tier (open-speaker AEC = later).
    FAIL-SAFE: if the mic can't be acquired, wakeword is left intact and we report
    failure — true speech mode simply doesn't engage.
    """
    data = await request.json()
    enabled = bool(data.get("enabled", False))
    mgr = system.get_conversation_manager()
    if enabled:
        # Switching endpoints: an active BROWSER session yields to local (its
        # source close sends "bye" so the tab shuts down cleanly). Browser mode
        # itself engages via the WS connect (/ws/conversation), not this PUT.
        if system.conversation_mode_enabled and getattr(system, "conversation_source", None) == "browser":
            mgr.stop()
        ok = mgr.start_local()
        return {
            "status": "ok" if ok else "failed",
            "active": mgr.active,
            "note": "" if ok else "could not acquire mic — wakeword intact",
        }
    mgr.stop()
    return {"status": "ok", "active": mgr.active}


@router.get("/api/runtime/true-speech")
async def get_true_speech(_=Depends(require_login), system=Depends(get_system)):
    """Current true speech mode state (system-level, ephemeral) — for UI load-state."""
    return {"enabled": bool(getattr(system, "conversation_mode_enabled", False)),
            "source": getattr(system, "conversation_source", None)}
