# core/routes/agents.py — Agent status + workspace runner API
import logging
import os
import signal
import subprocess
import sys
from pathlib import Path

_IS_WINDOWS = sys.platform == 'win32'

from fastapi import APIRouter, Depends, HTTPException, Query, Request
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
    # is_relative_to, not startswith: '/w/proj-evil' starts with '/w/proj'
    # (the sibling-prefix class fixed in api_fastapi.py; scout E 2026-10-06)
    if not ws.is_relative_to(base) or ws == base or not ws.is_dir():
        raise HTTPException(404, "Workspace not found")
    return str(ws)


# --- Agent status routes ---

@router.get("/api/agents/status")
async def agent_status(chat: str = Query('', description="Filter by chat name"), _=Depends(require_login)):
    system = get_system()
    if not hasattr(system, 'agent_manager'):
        return {"agents": []}
    # '' (the query default) = unfiltered, matching the old route contract;
    # check_all itself now treats '' as the chatless-agents filter.
    # Same hidden-chat gate as every other agent route: to_dict() carries the
    # mission and the pending question, and this route handed a sealed chat's
    # out in plaintext - by name, or unfiltered (privacy scout, 2026-10-07).
    if chat:
        _chat_or_404(system, chat)
        return {"agents": system.agent_manager.check_all(chat_name=chat)}
    sm = system.llm_chat.session_manager
    out = []
    for a in system.agent_manager.check_all(chat_name=None):
        c = a.get('chat_name') or ''
        try:
            hidden = bool(c) and (sm.is_chat_hidden(c) or sm.read_chat_settings(c) is None)
        except Exception:
            hidden = True
        if not hidden:
            out.append(a)
    return {"agents": out}


@router.get("/api/agents/providers")
async def agent_providers(_=Depends(require_login)):
    import config as cfg
    from core.chat.llm_providers import provider_registry, PROVIDER_METADATA
    core_keys = set(provider_registry.get_core_keys())
    providers = []
    all_providers = {**getattr(cfg, 'LLM_PROVIDERS', {}), **getattr(cfg, 'LLM_CUSTOM_PROVIDERS', {})}
    for key, pconf in all_providers.items():
        if not pconf.get('enabled'):
            continue
        is_core = key in core_keys
        meta = PROVIDER_METADATA.get(key, {})
        models = meta.get('model_options') or {} if is_core else {}
        current = pconf.get('model', '')
        providers.append({
            'key': key,
            'name': pconf.get('display_name', meta.get('display_name', key)),
            'current_model': current,
            'models': models,
            'is_core': is_core,
        })
    return {"providers": providers}


@router.post("/api/agents/{agent_id}/dismiss")
async def dismiss_agent(agent_id: str, request: Request, _=Depends(require_login)):
    """The pill's ×. Chat-local like every other agent door (privacy scout,
    2026-10-07: this one took any id from any chat and returned the report);
    {chat} in the body, the active chat when absent. Answers status only."""
    system = get_system()
    if not hasattr(system, 'agent_manager'):
        raise HTTPException(404, "Agent system not available")
    try:
        body = await request.json()
    except Exception:
        body = {}
    chat = _chat_or_404(system, (body or {}).get('chat') or '')
    text, ok = system.agent_manager.action_text(chat, agent_id, 'stop', '')
    if not ok:
        raise HTTPException(404, text)
    return {"id": agent_id, "status": "dismissed", "message": text}


# --- Agents v2 (tmp/agents-v2.md §3.7): kinds, spawn, and the per-agent doors.
# Every agent door is CHAT-LOCAL: the body names the chat, the engine resolves
# the agent only among that chat's agents, and a hidden chat is a 404 like a
# missing one (scout C H3). No route returns another chat's content.

def _mgr():
    system = get_system()
    mgr = getattr(system, 'agent_manager', None)
    if mgr is None:
        raise HTTPException(404, "Agent system not available")
    return mgr


def _chat_or_404(system, chat):
    chat = str(chat or '').strip()
    sm = system.llm_chat.session_manager
    if not chat or sm.is_chat_hidden(chat) or sm.read_chat_settings(chat) is None:
        raise HTTPException(404, "Chat not found")
    return chat


class AgentBody(BaseModel):
    chat: str
    value: str = ''
    question: str = ''      # answer: the question's id (the card knows it) - a stale one is refused


class SpawnBody(BaseModel):
    chat: str
    kind: str
    mission: str
    options: dict = {}


@router.get("/api/agents/kinds")
async def agent_kinds(_=Depends(require_login)):
    return {"kinds": _mgr().kinds()}


@router.get("/api/agents/chats")
async def agent_chats(_=Depends(require_login)):
    """Chats that have agents - live, resting or recently finished - for the
    Agents page's sidebar. Hidden (sealed) chats are left out."""
    system = get_system()
    mgr = _mgr()
    sm = system.llm_chat.session_manager
    names = {}
    for a in mgr.check_all():
        names.setdefault(a['chat_name'], {'live': 0, 'rows': 0})['live'] += 1
    for r in mgr._load_rows():
        names.setdefault(r.get('chat', ''), {'live': 0, 'rows': 0})['rows'] += 1
    out = []
    for name, n in names.items():
        if not name:
            continue
        try:
            if sm.is_chat_hidden(name) is True or sm.read_chat_settings(name) is None:
                continue
        except Exception:
            continue
        out.append({'chat': name, **n})
    out.sort(key=lambda x: (-x['live'], x['chat']))
    return {"chats": out}


@router.get("/api/agents/list")
async def agent_list(chat: str = Query(''), _=Depends(require_login)):
    """One chat's agents for the Agents page: live ones in full (status,
    progress, pending question, transcript tail) and the chat's rows (resting,
    done, failed, lost) with their report heads. Chat-local; a hidden chat is a 404."""
    system = get_system()
    mgr = _mgr()
    chat = _chat_or_404(system, chat)
    live = []
    for a in mgr._live_in(chat):
        d = a.to_dict()
        d['progress'] = a.progress()
        d['events'] = a.transcript(40)
        d['report_head'] = (a.result or '')[:400]
        live.append(d)
    live_ids = {a['id'] for a in live}
    rows = []
    for r in mgr._rows_in(chat):
        if r['id'] in live_ids:
            continue
        content = mgr._content_get(chat, r['id'])
        rows.append({**{k: r.get(k) for k in ('id', 'name', 'kind', 'status', 'started', 'ended', 'privacy')},
                     'mission': (content.get('mission') or '')[:300],
                     'report_head': (content.get('last_report') or '')[:400],
                     'resumable': bool(r.get('resume_token'))})
    rows.sort(key=lambda r: r.get('ended') or r.get('started') or 0, reverse=True)
    from core.chat import inbox
    return {"chat": chat, "agents": live, "rows": rows[:30], "inbox": inbox.peek(chat)}


@router.post("/api/agents/spawn")
async def agent_spawn(req: SpawnBody, _=Depends(require_login)):
    system = get_system()
    mgr = _mgr()
    chat = _chat_or_404(system, req.chat)
    # the route thread has no turn context: the engine's privacy check ORs in
    # the chat's own stored settings, so a private chat still refuses a cloud kind
    text, ok = mgr.spawn_text(chat, req.kind, req.mission, req.options or {})
    if not ok:
        raise HTTPException(400, text)
    return {"message": text}


@router.get("/api/agents/{agent_id}/transcript")
async def agent_transcript(agent_id: str, chat: str = Query(''), last: int = Query(60), _=Depends(require_login)):
    system = get_system()
    mgr = _mgr()
    chat = _chat_or_404(system, chat)
    from core.agents.engine import AgentError
    try:
        agent, row = mgr._resolve(chat, agent_id)
    except AgentError:
        raise HTTPException(404, "Agent not found")
    if agent is None:
        return {"id": row['id'], "name": row['name'], "status": row.get('status'), "events": [], "pending_question": None}
    return {"id": agent.id, "name": agent.name, "status": agent.status,
            "progress": agent.progress(), "pending_question": agent.pending_question,
            "events": agent.transcript(max(1, min(400, last)))}


@router.post("/api/agents/{agent_id}/answer")
async def agent_answer(agent_id: str, req: AgentBody, _=Depends(require_login)):
    system = get_system()
    mgr = _mgr()
    chat = _chat_or_404(system, req.chat)
    text, ok = mgr.action_text(chat, agent_id, 'answer', req.value, question=(req.question or None))
    if not ok:
        raise HTTPException(400, text)
    return {"message": text}


@router.post("/api/agents/{agent_id}/say")
async def agent_say(agent_id: str, req: AgentBody, _=Depends(require_login)):
    system = get_system()
    mgr = _mgr()
    chat = _chat_or_404(system, req.chat)
    text, ok = mgr.action_text(chat, agent_id, 'say', req.value)
    if not ok:
        raise HTTPException(400, text)
    return {"message": text}


@router.post("/api/agents/{agent_id}/stop")
async def agent_stop(agent_id: str, req: AgentBody, _=Depends(require_login)):
    system = get_system()
    mgr = _mgr()
    chat = _chat_or_404(system, req.chat)
    text, ok = mgr.action_text(chat, agent_id, 'stop', '')
    if not ok:
        raise HTTPException(400, text)
    return {"message": text}


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
            # DEVNULL, not PIPE: nothing ever drained the pipe, so any run
            # emitting >64KB blocked forever on write() and then reported
            # "already_running" until restart (negspace N20, 2026-08-31).
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
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
            # shell=True means proc.pid is cmd.exe — terminate() kills the
            # shell and ORPHANS the real python/node child, which keeps the
            # port while we report "stopped". taskkill /T walks the tree
            # (the killpg equivalent; console apps have no graceful TERM).
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                           capture_output=True)
        else:
            os.killpg(proc.pid, signal.SIGTERM)
    except (ProcessLookupError, OSError):
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

    # Python entry points in priority order. Use sys.executable instead of
    # bare 'python' — on Windows, bare `python` may be the Microsoft Store
    # stub, a py launcher alias, or simply not on PATH (only `python.exe`
    # in a specific venv is). sys.executable is always the interpreter
    # currently running Sapphire — same Python, same venv, same deps.
    # Quote the path because Windows installer paths often contain spaces
    # (C:\Program Files\..., C:\Users\Name With Spaces\...).
    # 2026-05-18 herring-table #23.
    py_exe = f'"{sys.executable}"' if ' ' in sys.executable else sys.executable

    for name in ['main.py', 'app.py', 'server.py', 'run.py', 'game.py']:
        if (ws / name).exists():
            return f'{py_exe} {name}'

    # Single .py file
    py_files = [f.name for f in ws.iterdir() if f.suffix == '.py' and f.is_file()]
    if len(py_files) == 1:
        return f'{py_exe} {py_files[0]}'

    # Look for the biggest .py file (likely the main one)
    if py_files:
        biggest = max(py_files, key=lambda f: (ws / f).stat().st_size)
        return f'{py_exe} {biggest}'

    return None
