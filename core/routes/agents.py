# core/routes/agents.py — Agent status + workspace runner API
import logging
import os
import signal
import subprocess
import sys
from pathlib import Path

_IS_WINDOWS = sys.platform == 'win32'

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from core.auth import require_login
from core.api_fastapi import get_system

logger = logging.getLogger(__name__)
router = APIRouter()

# --- Running processes ---
_running = {}  # key -> {proc, workspace, command, project}


def _get_workspace_base():
    try:
        from core.plugin_loader import plugin_loader
        settings = plugin_loader.get_plugin_settings("claude-code") or {}
        return os.path.expanduser(settings.get('workspace_dir', '~/claude-workspaces'))
    except Exception:
        return os.path.expanduser('~/claude-workspaces')


def _validate_workspace(project):
    """Resolve and validate a project workspace. Returns path or raises."""
    base = Path(_get_workspace_base()).resolve()
    ws = (base / project).resolve()
    if not str(ws).startswith(str(base)) or not ws.is_dir():
        raise HTTPException(404, "Workspace not found")
    return str(ws)


# --- Agent status routes ---

@router.get("/api/agents/status")
async def agent_status(chat: str = Query('', description="Filter by chat name"), _=Depends(require_login)):
    system = get_system()
    if not hasattr(system, 'agent_manager'):
        return {"agents": []}
    return {"agents": system.agent_manager.check_all(chat_name=chat)}


@router.get("/api/agents/providers")
async def agent_providers(_=Depends(require_login)):
    import config as cfg
    from core.chat.llm_providers import PROVIDER_METADATA
    providers = []
    for key, pconf in getattr(cfg, 'LLM_PROVIDERS', {}).items():
        if not pconf.get('enabled'):
            continue
        meta = PROVIDER_METADATA.get(key, {})
        models = meta.get('model_options') or {}
        current = pconf.get('model', '')
        providers.append({
            'key': key,
            'name': pconf.get('display_name', meta.get('display_name', key)),
            'current_model': current,
            'models': models,
        })
    return {"providers": providers}


@router.post("/api/agents/{agent_id}/dismiss")
async def dismiss_agent(agent_id: str, _=Depends(require_login)):
    system = get_system()
    if not hasattr(system, 'agent_manager'):
        raise HTTPException(404, "Agent system not available")
    result = system.agent_manager.dismiss(agent_id)
    if 'error' in result:
        raise HTTPException(404, result['error'])
    return result


# --- Workspace runner routes ---

class RunRequest(BaseModel):
    project: str
    command: str = ''


@router.post("/api/workspace/run")
async def workspace_run(req: RunRequest, _=Depends(require_login)):
    """Run a command in a workspace. Auto-detects if no command given."""
    workspace = _validate_workspace(req.project)

    # Already running?
    existing = _running.get(req.project)
    if existing and existing['proc'].poll() is None:
        return {"status": "already_running", "pid": existing['proc'].pid, "project": req.project}

    # Detect command
    command = req.command.strip()
    if not command:
        command = _detect_run_command(workspace)
        if not command:
            raise HTTPException(400, "Could not detect how to run this project. No main.py, app.py, or index.html found.")

    # Run it
    try:
        popen_kwargs = dict(
            shell=True, cwd=workspace,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
        )
        if not _IS_WINDOWS:
            popen_kwargs['start_new_session'] = True
        proc = subprocess.Popen(command, **popen_kwargs)
    except Exception as e:
        raise HTTPException(500, f"Failed to start: {e}")

    _running[req.project] = {
        'proc': proc,
        'workspace': workspace,
        'command': command,
        'project': req.project,
    }

    logger.info(f"[workspace] Started '{command}' in {workspace} (pid {proc.pid})")
    return {"status": "started", "pid": proc.pid, "project": req.project, "command": command}


@router.post("/api/workspace/stop")
async def workspace_stop(req: RunRequest, _=Depends(require_login)):
    """Stop a running workspace process."""
    entry = _running.get(req.project)
    if not entry:
        return {"status": "not_running"}

    proc = entry['proc']
    if proc.poll() is not None:
        _running.pop(req.project, None)
        return {"status": "already_stopped", "returncode": proc.returncode}

    try:
        if _IS_WINDOWS:
            proc.terminate()
        else:
            os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        try:
            if _IS_WINDOWS:
                proc.kill()
            else:
                os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    _running.pop(req.project, None)
    logger.info(f"[workspace] Stopped {req.project} (pid {proc.pid})")
    return {"status": "stopped", "project": req.project}


@router.get("/api/workspace/status")
async def workspace_status(_=Depends(require_login)):
    """Get status of all running workspace processes."""
    # Clean up dead processes
    dead = [k for k, v in _running.items() if v['proc'].poll() is not None]
    for k in dead:
        _running.pop(k, None)

    return {
        "running": [
            {"project": k, "pid": v['proc'].pid, "command": v['command']}
            for k, v in _running.items()
        ]
    }


def _detect_run_command(workspace):
    """Heuristic: figure out what to run in a workspace."""
    ws = Path(workspace)

    # Check for index.html first (shouldn't hit this path normally, but just in case)
    if (ws / 'index.html').exists():
        return None  # HTML projects use the link, not subprocess

    # Python entry points in priority order
    for name in ['main.py', 'app.py', 'server.py', 'run.py', 'game.py']:
        if (ws / name).exists():
            return f'python {name}'

    # Single .py file
    py_files = [f.name for f in ws.iterdir() if f.suffix == '.py' and f.is_file()]
    if len(py_files) == 1:
        return f'python {py_files[0]}'

    # Look for the biggest .py file (likely the main one)
    if py_files:
        biggest = max(py_files, key=lambda f: (ws / f).stat().st_size)
        return f'python {biggest}'

    return None
