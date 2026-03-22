# plugins/claude-code/tools/claude_code_tools.py
# Single blocking tool + registers claude_code agent type with AgentManager
import logging
import os
import shutil
import sys
import time

logger = logging.getLogger(__name__)

ENABLED = True
EMOJI = '\u26a1'
AVAILABLE_FUNCTIONS = ['code_session']

TOOLS = [
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "code_session",
            "description": "Run a BLOCKING Claude Code session — you wait for it to finish. Call with no arguments to list recent projects/sessions. Call with a mission to start or resume. For anything that takes more than a few seconds, prefer spawn_agent(agent_type='claude_code') via the agents plugin — call agent_options() first to check availability.",
            "parameters": {
                "type": "object",
                "properties": {
                    "mission": {
                        "type": "string",
                        "description": "What to build or do. Omit to list recent sessions instead."
                    },
                    "project_name": {
                        "type": "string",
                        "description": "Workspace directory name. Auto-generated from mission if not provided."
                    },
                    "session_id": {
                        "type": "string",
                        "description": "Resume a previous session by ID (from listing). Continues with full context preserved."
                    }
                },
                "required": []
            }
        }
    },
]


# --- Claude Code Worker (inlined for agent registration) ---

def _create_code_worker():
    """Create a CodeWorker class for the agent registry."""
    from core.agents.base_worker import BaseWorker

    class CodeWorker(BaseWorker):
        """Runs a Claude Code session in a background thread."""

        def __init__(self, agent_id, name, mission, chat_name='', on_complete=None,
                     project_name='', session_id='', **kwargs):
            super().__init__(agent_id, name, mission, chat_name, on_complete)
            self.project_name = project_name or _slugify(mission)
            self._session_id = session_id
            self.tool_log = ['claude-code']

        def run(self):
            settings = _get_settings()

            # Resume: resolve workspace from saved session
            if self._session_id:
                workspace = _resolve_session_workspace(self._session_id, settings)
                if not workspace:
                    self.error = f"Session {self._session_id} not found or workspace gone."
                    self.status = 'failed'
                    return
            else:
                workspace, err = _resolve_workspace(settings, self.project_name)
                if err:
                    self.error = err
                    self.status = 'failed'
                    return

            safety_err = _sanity_check(workspace)
            if safety_err:
                self.error = safety_err
                self.status = 'failed'
                return

            coder_instructions = settings.get('coder_instructions', '')
            _write_claude_md(workspace, coder_instructions, self.project_name)

            if self._cancelled.is_set():
                self.status = 'cancelled'
                return

            args = _build_claude_args(self.mission, settings, session_id=self._session_id)
            args.extend(['--name', self.project_name])

            data, err = _run_claude(args, workspace)
            if err:
                self.error = err
                self.status = 'failed' if not self._cancelled.is_set() else 'cancelled'
                return

            session_id = data.get('session_id', '')

            # Track session
            if session_id:
                _save_session(session_id, self.project_name, workspace, self.mission)

            result_text = data.get('result', str(data))
            file_listing = _list_workspace_files(workspace)

            lines = [
                f"**Code Agent {self.name} \u2014 Complete**",
                f"- Project: `{self.project_name}`",
                f"- Workspace: `{workspace}`",
            ]
            if session_id:
                lines.append(f"- Session ID: `{session_id}` (resumable)")
            if os.path.isfile(os.path.join(workspace, 'index.html')):
                lines.append(f"- **[Open App](/workspace/{self.project_name}/index.html)**")
            lines.append(f"\n**Files:**\n{file_listing}")
            lines.append(f"\n**Result:**\n{result_text}")

            self.result = '\n'.join(lines)

            # Notify frontend about runnable project
            _publish_workspace_ready(self.project_name, workspace)

    return CodeWorker


# --- Agent type registration ---

def _register_code_type(mgr):
    """Register claude_code agent type with the given AgentManager."""
    if 'claude_code' in mgr.get_types():
        return

    CodeWorker = _create_code_worker()

    def code_factory(agent_id, name, mission, chat_name='', on_complete=None, **kwargs):
        return CodeWorker(agent_id, name, mission, chat_name=chat_name, on_complete=on_complete, **kwargs)

    mgr.register_type(
        type_key='claude_code',
        display_name='Code (Claude Code)',
        factory=code_factory,
        spawn_args={
            'project_name': {'type': 'string', 'description': 'Workspace directory name for the project.'},
            'session_id': {'type': 'string', 'description': 'Resume a previous session by ID (from code_session listing).'},
        },
        names=['Forge', 'Anvil', 'Crucible', 'Hammer', 'Spark'],
    )

# Register at load time via module singleton
try:
    from core.agents import agent_manager as _mgr
    if _mgr is not None:
        _register_code_type(_mgr)
except Exception as e:
    logger.warning(f"Failed to register claude_code agent type at load: {e}")


# --- Claude runner functions (self-contained) ---

from pathlib import Path
import json
import re
import subprocess

_SAPPHIRE_ROOT = str(Path(__file__).resolve().parent.parent.parent.parent)

_CLAUDE_MD_TEMPLATE = """# Project: {project_name}

## Instructions
{coder_instructions}

## Constraints
- Work only within this directory
- Do not access files outside the workspace
- Do not install system-wide packages
- Test your code before reporting done
- Keep dependencies minimal
- For web apps: build as a self-contained `index.html` with inline JS/CSS (no build step, no npm). This file gets served directly to the user's browser

## Context
Dispatched by Sapphire AI on behalf of the user.
"""

_DEFAULT_CODER_INSTRUCTIONS = """You are a code builder. Write clean, working code.
- Test your work by running it before reporting done
- Include a README.md with usage instructions
- Keep it simple and minimal — no over-engineering
- If you hit a problem you can't solve, describe it clearly in your final response"""


def _clean_env():
    env = os.environ.copy()
    for key in ['CONDA_PREFIX', 'CONDA_DEFAULT_ENV', 'CONDA_PROMPT_MODIFIER',
                'CONDA_SHLVL', 'CONDA_PYTHON_EXE', 'CONDA_EXE']:
        env.pop(key, None)
    env.pop('VIRTUAL_ENV', None)
    env.pop('UV_VIRTUALENV', None)
    path_dirs = env.get('PATH', '').split(os.pathsep)
    clean_path = [d for d in path_dirs
                  if f'{os.sep}envs{os.sep}' not in d and f'{os.sep}conda' not in d.lower()
                  and f'{os.sep}.venv{os.sep}' not in d and f'{os.sep}virtualenvs{os.sep}' not in d]
    env['PATH'] = os.pathsep.join(clean_path)
    return env


def _sanity_check(workspace_path):
    ws = str(Path(workspace_path).resolve())
    if ws.startswith(_SAPPHIRE_ROOT):
        return f"SAFETY: Workspace '{ws}' is inside Sapphire's project directory. Use an external directory."
    for marker in ['/envs/', '/conda', '/.venv/', '/virtualenvs/']:
        if marker in ws.lower():
            return f"SAFETY: Workspace '{ws}' appears to be inside a Python environment."
    clean = _clean_env()
    if not shutil.which('claude', path=clean.get('PATH', '')):
        return "Claude Code command not found. Install globally: npm install -g @anthropic-ai/claude-code"
    return None


def _slugify(text, max_len=40):
    words = re.sub(r'[^a-zA-Z0-9\s]', '', text).split()[:6]
    slug = '-'.join(w.lower() for w in words)
    return slug[:max_len] or 'project'


def _resolve_workspace(settings, project_name):
    base = settings.get('workspace_dir', '~/claude-workspaces')
    base = os.path.expanduser(base)
    workspace = os.path.join(base, project_name)
    try:
        os.makedirs(workspace, exist_ok=True)
    except OSError as e:
        return None, f"Cannot create workspace '{workspace}': {e}"
    return workspace, None


def _write_claude_md(workspace, coder_instructions=None, project_name='project'):
    claude_md_path = os.path.join(workspace, 'CLAUDE.md')
    if os.path.exists(claude_md_path):
        return
    instructions = coder_instructions or _DEFAULT_CODER_INSTRUCTIONS
    content = _CLAUDE_MD_TEMPLATE.format(project_name=project_name, coder_instructions=instructions.strip())
    try:
        with open(claude_md_path, 'w', encoding='utf-8') as f:
            f.write(content)
    except OSError as e:
        logger.warning(f"[claude-code] Could not write CLAUDE.md: {e}")


def _build_claude_args(mission, settings, session_id=None):
    mode = settings.get('mode', 'standard')
    max_turns = int(settings.get('max_turns', 50))
    args = ['claude', '-p', mission, '--output-format', 'json']
    if session_id:
        args.extend(['--resume', session_id])
    args.extend(['--max-turns', str(max_turns)])
    if mode == 'strict':
        args.extend(['--allowedTools', 'Read,Edit,Write,Glob,Grep'])
    elif mode == 'system_killer':
        args.extend(['--allowedTools', 'Read,Edit,Write,Glob,Grep,Bash,NotebookEdit,WebFetch,WebSearch'])
    else:
        args.extend(['--allowedTools', 'Read,Edit,Write,Glob,Grep,Bash,NotebookEdit'])
    return args


_IS_WINDOWS = sys.platform == 'win32'


def _run_claude(args, workspace, timeout_minutes=30):
    env = _clean_env()
    timeout_sec = timeout_minutes * 60
    logger.info(f"[claude-code] Running: {' '.join(args[:6])}... in {workspace}")
    try:
        popen_kwargs = dict(
            cwd=workspace, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL, text=True,
        )
        if not _IS_WINDOWS:
            popen_kwargs['start_new_session'] = True
        proc = subprocess.Popen(args, **popen_kwargs)
        try:
            stdout, stderr = proc.communicate(timeout=timeout_sec)
        except subprocess.TimeoutExpired:
            if not _IS_WINDOWS:
                import signal
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            proc.wait(timeout=5)
            return None, f"Claude Code session timed out after {timeout_minutes} minutes."
        result = subprocess.CompletedProcess(args, proc.returncode, stdout, stderr)
    except FileNotFoundError:
        return None, "Claude Code command not found. Install globally: npm install -g @anthropic-ai/claude-code"
    except Exception as e:
        return None, f"Failed to run Claude Code: {e}"

    if result.returncode != 0 and not result.stdout.strip():
        stderr_tail = (result.stderr or '')[-500:]
        return None, f"Claude Code exited with error (code {result.returncode}): {stderr_tail}"

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        for line in result.stdout.strip().split('\n'):
            line = line.strip()
            if line.startswith('{'):
                try:
                    data = json.loads(line)
                    break
                except json.JSONDecodeError:
                    continue
        else:
            return {'result': result.stdout.strip(), 'session_id': None}, None
    return data, None


def _list_workspace_files(workspace, max_files=20):
    try:
        files = []
        ws = Path(workspace)
        for f in sorted(ws.rglob('*')):
            if f.is_file() and '.git' not in f.parts and '__pycache__' not in f.parts:
                rel = f.relative_to(ws)
                size = f.stat().st_size
                if size > 1024 * 1024:
                    size_str = f"{size / (1024*1024):.1f}MB"
                elif size > 1024:
                    size_str = f"{size / 1024:.1f}KB"
                else:
                    size_str = f"{size}B"
                files.append(f"  {rel} ({size_str})")
                if len(files) >= max_files:
                    files.append(f"  ... and more")
                    break
        return '\n'.join(files) if files else '  (empty)'
    except Exception:
        return '  (could not list files)'


# --- Helpers ---

def _get_settings():
    from core.plugin_loader import plugin_loader
    return plugin_loader.get_plugin_settings("claude-code") or {}


def _get_sessions():
    """Get saved sessions dict from plugin state."""
    try:
        from core.plugin_loader import plugin_loader
        state = plugin_loader.get_plugin_state("claude-code")
        return state.get('sessions', {}), state
    except Exception:
        return {}, None


def _save_session(session_id, project_name, workspace, mission):
    """Save or update a session in plugin state."""
    try:
        sessions, state = _get_sessions()
        if not state:
            return
        existing = sessions.get(session_id)
        if existing:
            existing['last_used'] = time.strftime('%Y-%m-%dT%H:%M:%S')
            existing['turns'] = existing.get('turns', 0) + 1
        else:
            sessions[session_id] = {
                'project': project_name,
                'workspace': workspace,
                'mission': mission[:200],
                'created': time.strftime('%Y-%m-%dT%H:%M:%S'),
                'last_used': time.strftime('%Y-%m-%dT%H:%M:%S'),
                'turns': 1,
            }
        if len(sessions) > 20:
            sorted_ids = sorted(sessions, key=lambda k: sessions[k].get('last_used', ''))
            for old_id in sorted_ids[:-20]:
                del sessions[old_id]
        state.save('sessions', sessions)
    except Exception as e:
        logger.warning(f"[claude-code] Could not save session: {e}")


def _resolve_session_workspace(session_id, settings):
    """Look up workspace for a saved session. Returns path or None."""
    sessions, _ = _get_sessions()
    info = sessions.get(session_id)
    if info and info.get('workspace') and os.path.isdir(info['workspace']):
        return info['workspace']
    # Fallback: try base workspace dir
    base = os.path.expanduser(settings.get('workspace_dir', '~/claude-workspaces'))
    if os.path.isdir(base):
        return base
    return None


def _list_sessions():
    """List recent sessions for the AI."""
    sessions, _ = _get_sessions()
    if not sessions:
        return "No Claude Code sessions yet. Call with a mission to start one.", True

    lines = ["**Recent Claude Code Sessions:**\n"]
    sorted_sessions = sorted(sessions.items(), key=lambda x: x[1].get('last_used', ''), reverse=True)

    for sid, info in sorted_sessions[:10]:
        workspace_exists = os.path.isdir(info.get('workspace', ''))
        status = '\u2713' if workspace_exists else '\u2717 (workspace gone)'
        lines.append(
            f"- **{info.get('project', '?')}** {status}\n"
            f"  ID: `{sid}` | Turns: {info.get('turns', 0)} | "
            f"Last: {info.get('last_used', '?')}\n"
            f"  Mission: {info.get('mission', '?')[:100]}"
        )

    lines.append("\nUse `session_id` to resume any session.")
    return '\n'.join(lines), True


# --- Blocking tool ---

def _code_session(arguments):
    mission = arguments.get('mission', '').strip()
    session_id = arguments.get('session_id', '').strip()

    # No mission = list sessions
    if not mission:
        return _list_sessions()

    project_name = arguments.get('project_name', '').strip()
    if not project_name:
        project_name = _slugify(mission)

    settings = _get_settings()

    # Resume: resolve workspace from saved session
    if session_id:
        workspace = _resolve_session_workspace(session_id, settings)
        if not workspace:
            return f"Session {session_id} not found or workspace gone.", False
    else:
        workspace, err = _resolve_workspace(settings, project_name)
        if err:
            return err, False

    safety_err = _sanity_check(workspace)
    if safety_err:
        return safety_err, False

    coder_instructions = settings.get('coder_instructions', '')
    _write_claude_md(workspace, coder_instructions, project_name)

    args = _build_claude_args(mission, settings, session_id=session_id)
    args.extend(['--name', project_name])

    data, err = _run_claude(args, workspace)
    if err:
        return f"Claude Code error: {err}", False

    new_session_id = data.get('session_id', '')
    result_text = data.get('result', str(data))

    if new_session_id:
        _save_session(new_session_id, project_name, workspace, mission)

    mode = settings.get('mode', 'standard')
    file_listing = _list_workspace_files(workspace)
    lines = [
        f"**Claude Code Session Complete**",
        f"- Project: `{project_name}`",
        f"- Workspace: `{workspace}`",
        f"- Mode: {mode}",
    ]
    if new_session_id:
        lines.append(f"- Session ID: `{new_session_id}` (resumable)")
    if os.path.isfile(os.path.join(workspace, 'index.html')):
        lines.append(f"- **[Open App](/workspace/{project_name}/index.html)**")
    lines.append(f"\n**Files in workspace:**\n{file_listing}")
    lines.append(f"\n**Result:**\n{result_text}")

    _publish_workspace_ready(project_name, workspace)

    return '\n'.join(lines), True


def _publish_workspace_ready(project_name, workspace):
    """Publish SSE event so frontend can show run/open button."""
    try:
        from core.event_bus import publish, Events
        has_html = os.path.isfile(os.path.join(workspace, 'index.html'))
        has_python = any(f.endswith('.py') for f in os.listdir(workspace) if os.path.isfile(os.path.join(workspace, f)))
        if has_html:
            project_type = 'html'
        elif has_python:
            project_type = 'python'
        else:
            return  # Nothing runnable

        publish(Events.WORKSPACE_READY, {
            'project': project_name,
            'type': project_type,
            'url': f'/workspace/{project_name}/index.html' if has_html else None,
        })
    except Exception as e:
        logger.warning(f"[claude-code] Could not publish workspace_ready: {e}")


# --- Main dispatch ---

def execute(function_name, arguments, config):
    try:
        if function_name == 'code_session':
            return _code_session(arguments)
        else:
            return f"Unknown function: {function_name}", False
    except Exception as e:
        logger.error(f"[claude-code] {function_name} failed: {e}", exc_info=True)
        return f"Claude Code error: {e}", False
