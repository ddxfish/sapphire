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
AVAILABLE_FUNCTIONS = ['code_session', 'activate_plugin']

TOOLS = [
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "code_session",
            "description": "Run a BLOCKING Claude Code session — you wait for it to finish. Call with no arguments to list recent projects/sessions. Call with a mission to start or resume. For anything that takes more than a few seconds, prefer spawn_agent via the agents plugin. Two agent types available: 'claude_code' for general projects (~/claude-workspaces/), 'claude_code_plugin' for building Sapphire plugins (user/plugins/). For plugins, use spawn_agent(agent_type='claude_code_plugin', plugin_name='name') — it auto-injects plugin docs and validates the result.",
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
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "activate_plugin",
            "description": "Activate a plugin after it's been built by a claude_code_plugin agent. Runs AST validation, checks the manifest, rescans plugins, and enables the new plugin. Use check_agents first to confirm the build agent completed, then call this with the plugin name.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Plugin name (directory name in user/plugins/)"
                    }
                },
                "required": ["name"]
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

            base = settings.get('coder_instructions', '')
            mode = settings.get('project_instructions', '')
            _write_claude_md(workspace, base, mode, self.project_name)

            if self._cancelled.is_set():
                self.status = 'cancelled'
                return

            args = _build_claude_args(self.mission, settings, session_id=self._session_id)
            if _HAS_NAME_FLAG:
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

            if session_id:
                lines.append(f"\n**If there are bugs:** Use `spawn_agent(agent_type='claude_code', "
                             f"project_name='{self.project_name}', session_id='{session_id}')` to resume. "
                             f"Describe the error so Claude Code can fix it. "
                             f"Do NOT troubleshoot manually with run_command.")

            self.result = '\n'.join(lines)

            # Notify frontend about runnable project
            _publish_workspace_ready(self.project_name, workspace)

    return CodeWorker


# --- Plugin builder worker ---

def _create_plugin_worker():
    """Create a PluginWorker class for autonomous plugin building."""
    from core.agents.base_worker import BaseWorker

    class PluginWorker(BaseWorker):
        """Builds a Sapphire plugin via Claude Code in headless mode."""

        def __init__(self, agent_id, name, mission, chat_name='', on_complete=None,
                     plugin_name='', capabilities=None, context=None, session_id='', **kwargs):
            super().__init__(agent_id, name, mission, chat_name, on_complete)
            self.plugin_name = plugin_name or _slugify(mission)
            # Coerce capabilities — LLMs send "providers, settings" as string not list
            if isinstance(capabilities, str):
                self._capabilities = [c.strip() for c in capabilities.split(',') if c.strip()]
            else:
                self._capabilities = capabilities or ['tools']
            self._context = context
            self._session_id = session_id
            self.tool_log = ['claude-code-plugin']

        def run(self):
            settings = _get_settings()
            workspace = os.path.join(_SAPPHIRE_ROOT, 'user', 'plugins', self.plugin_name)

            # Resume: resolve workspace from saved session
            if self._session_id:
                saved_ws = _resolve_session_workspace(self._session_id, settings)
                if saved_ws:
                    workspace = saved_ws

            # Chaos #5: refuse silent overwrites of existing plugins when we're
            # not resuming a known session. Without this, the LLM can build
            # a plugin that shares a name with an existing one and stomp files.
            if not self._session_id and os.path.isdir(workspace) and os.listdir(workspace):
                self.error = (
                    f"Plugin directory '{self.plugin_name}' already exists with contents. "
                    f"Pick a unique name, or pass session_id to resume an existing build."
                )
                self.status = 'failed'
                return

            # Chaos #2: same sanity gate CodeWorker uses. Catches missing
            # `claude` CLI up front with an install hint instead of later
            # FileNotFoundError buried inside _run_claude.
            safety_err = _sanity_check(workspace)
            if safety_err:
                self.error = safety_err
                self.status = 'failed'
                return

            try:
                os.makedirs(workspace, exist_ok=True)
            except OSError as e:
                self.error = f"Cannot create plugin dir: {e}"
                self.status = 'failed'
                return

            # Build three-layer CLAUDE.md: base + plugin mode + plugin addendum
            base = settings.get('coder_instructions', '')
            mode = settings.get('plugin_instructions', '')
            addendum = _build_plugin_addendum(
                self.plugin_name, self.mission,
                self._capabilities, self._context
            )
            _write_claude_md(workspace, base, mode, self.plugin_name, addendum=addendum)

            if self._cancelled.is_set():
                self.status = 'cancelled'
                return

            args = _build_claude_args(self.mission, settings, session_id=self._session_id)
            if _HAS_NAME_FLAG:
                args.extend(['--name', f'plugin-{self.plugin_name}'])

            data, err = _run_claude(args, workspace)
            if err:
                self.error = err
                self.status = 'failed' if not self._cancelled.is_set() else 'cancelled'
                return

            session_id = data.get('session_id', '')
            if session_id:
                _save_session(session_id, self.plugin_name, workspace, self.mission)

            result_text = data.get('result', str(data))
            file_listing = _list_workspace_files(workspace)

            # Run validation chain
            validation = _validate_plugin(workspace)

            lines = [
                f"**Plugin Builder {self.name} — Complete**",
                f"- Plugin: `{self.plugin_name}`",
                f"- Workspace: `{workspace}`",
            ]
            if session_id:
                lines.append(f"- Session ID: `{session_id}` (resumable)")
            lines.append(f"\n**Validation:**")
            for check, passed in validation.items():
                icon = '\u2713' if passed else '\u2717'
                lines.append(f"  {icon} {check}")
            lines.append(f"\n**Files:**\n{file_listing}")
            lines.append(f"\n**Result:**\n{result_text}")

            # Read NOTES.md if Claude Code left one
            notes_path = os.path.join(workspace, 'NOTES.md')
            if os.path.isfile(notes_path):
                try:
                    notes = Path(notes_path).read_text(encoding='utf-8').strip()
                    if notes:
                        lines.append(f"\n**Notes from Claude Code:**\n{notes}")
                except Exception:
                    pass

            # Add guidance so Sapphire knows what to do next. We skip the
            # private '_missing_files' key (it's used to format a specific
            # error hint below, not shown as a standalone pass/fail row).
            public_checks = {k: v for k, v in validation.items() if not k.startswith('_')}
            all_passed = all(public_checks.values())
            if all_passed:
                lines.append(f"\n**Next step:** Call `activate_plugin(name='{self.plugin_name}')` to enable it.")
            else:
                failed = [k for k, v in public_checks.items() if not v]
                lines.append(f"\n**Issues found:** {', '.join(failed)}")
                # Chaos #10: surface WHICH files are missing so the follow-up
                # agent doesn't rediscover what the validator already knew.
                missing = validation.get('_missing_files') or []
                if missing:
                    lines.append(f"**Missing files:** {', '.join(missing)}")
                lines.append(f"**To fix:** Use `spawn_agent(agent_type='claude_code_plugin', "
                             f"plugin_name='{self.plugin_name}')` to resume. "
                             f"Describe the specific error so Claude Code can fix it.")
                lines.append(f"Do NOT troubleshoot manually with run_command — Claude Code has "
                             f"the full project context and can fix it faster.")

            self.result = '\n'.join(lines)
            self._validation = validation
            self._all_passed = all_passed
            # If validation failed, mark the agent failed so the batch report's
            # ✓/✗ icon honestly reflects build state. The result body already
            # describes which checks failed; this just stops Sapphire's LLM from
            # glancing at ✓ and missing the "Issues found" text.
            if not all_passed:
                self.status = 'failed'
                self.error = 'plugin validation failed: ' + ', '.join(
                    k for k, v in validation.items() if not v
                )

    return PluginWorker


def _validate_plugin(workspace):
    """Structural validation for Claude Code-built plugins.

    No import blocklist — Claude Code is trusted. This checks structure only:
    manifest shape, file existence, syntax errors.
    """
    results = {}

    # 1. Manifest exists and parses
    manifest_path = os.path.join(workspace, 'plugin.json')
    try:
        with open(manifest_path, encoding='utf-8') as f:
            manifest = json.load(f)
        results['manifest_valid'] = isinstance(manifest, dict) and 'name' in manifest
    except Exception:
        results['manifest_valid'] = False
        return results  # can't continue without manifest

    # Normalize tools — Claude Code sometimes writes a string instead of a list
    tools = manifest.get('capabilities', {}).get('tools', [])
    if isinstance(tools, str):
        tools = [tools]
        manifest['capabilities']['tools'] = tools
        try:
            with open(manifest_path, 'w', encoding='utf-8') as f:
                json.dump(manifest, f, indent=2, ensure_ascii=False)
            logger.info(f"[claude-code] Auto-fixed manifest: tools string → list")
            results['manifest_auto_fixed'] = True
        except Exception:
            pass

    # 2. Declared files exist
    all_files_ok = True
    missing_files = []
    for tool_rel in tools:
        if not os.path.isfile(os.path.join(workspace, tool_rel)):
            all_files_ok = False
            missing_files.append(tool_rel)
    # Check provider entry if declared
    providers = manifest.get('capabilities', {}).get('providers', {})
    for sys_name, prov in providers.items():
        entry = prov.get('entry', 'provider.py')
        if not os.path.isfile(os.path.join(workspace, entry)):
            all_files_ok = False
            missing_files.append(f"{entry} (provider:{sys_name})")
    results['files_exist'] = all_files_ok
    # Chaos #10: name the missing files so a follow-up agent or a human
    # doesn't have to rediscover what the validator already knew.
    if missing_files:
        results['_missing_files'] = missing_files

    # Chaos #4: refuse ghost plugins (manifest exists but declares zero
    # meaningful capabilities). The per-capability loops above vacuously pass
    # when their lists are empty, so an empty plugin.json was landing with
    # all-True validation.
    caps = manifest.get('capabilities', {}) or {}
    has_capability = (
        bool(caps.get('tools'))
        or bool(caps.get('hooks'))
        or bool(caps.get('daemon'))
        or bool(caps.get('routes'))
        or bool(caps.get('providers'))
        or bool(caps.get('scopes'))
        or bool(caps.get('settings'))
        or bool(caps.get('app'))
        or bool(caps.get('schedule'))
    )
    results['has_capability'] = has_capability

    # 3. Syntax check on all .py files (compile only — no import restrictions)
    syntax_ok = True
    for py_file in Path(workspace).rglob('*.py'):
        try:
            source = py_file.read_text(encoding='utf-8')
            compile(source, str(py_file), 'exec')
        except SyntaxError as e:
            syntax_ok = False
            logger.warning(f"[claude-code] Syntax error in {py_file.name}: {e}")
    results['syntax_check'] = syntax_ok

    return results


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


def _register_plugin_type(mgr):
    """Register claude_code_plugin agent type for autonomous plugin building."""
    if 'claude_code_plugin' in mgr.get_types():
        return

    PluginWorker = _create_plugin_worker()

    def plugin_factory(agent_id, name, mission, chat_name='', on_complete=None, **kwargs):
        return PluginWorker(agent_id, name, mission, chat_name=chat_name, on_complete=on_complete, **kwargs)

    mgr.register_type(
        type_key='claude_code_plugin',
        display_name='Plugin Builder (Claude Code)',
        factory=plugin_factory,
        spawn_args={
            'plugin_name': {'type': 'string', 'description': 'Plugin directory name (in user/plugins/).'},
            'capabilities': {'type': 'array', 'description': 'Plugin capabilities: tools, hooks, daemon, routes, settings, providers.'},
            'context': {'type': 'string', 'description': 'Additional context (API docs, format specs, etc.).'},
            'session_id': {'type': 'string', 'description': 'Resume a previous session by ID.'},
        },
        names=['Blueprint', 'Architect', 'Mason', 'Maker', 'Weaver'],
    )


# Register at load time via module singleton
try:
    from core.agents import agent_manager as _mgr
    if _mgr is not None:
        _register_code_type(_mgr)
        _register_plugin_type(_mgr)
except Exception as e:
    logger.warning(f"Failed to register claude_code agent types at load: {e}")


# --- Claude runner functions (self-contained) ---

from pathlib import Path
import json
import re
import subprocess

_SAPPHIRE_ROOT = str(Path(__file__).resolve().parent.parent.parent.parent)

def _claude_supports_name():
    """Check if installed claude CLI supports --name (added ~2.1.76)."""
    try:
        out = subprocess.run(['claude', '--help'], capture_output=True, text=True, timeout=5)
        return '--name' in out.stdout
    except Exception:
        return False

_HAS_NAME_FLAG = _claude_supports_name()

_DEFAULT_CODER_INSTRUCTIONS = """You are a code builder. Write clean, working code.
- Test your work by running it before reporting done
- Include a README.md with usage instructions
- Keep it simple and minimal — no over-engineering
- If you hit a problem you can't solve, describe it clearly in your final response
- If you notice anything noteworthy that isn't part of your task, write it to NOTES.md"""


def _build_claude_md(project_name, base_instructions=None, mode_instructions=None, addendum=None):
    """Build CLAUDE.md content from base + mode instructions + optional addendum.

    Three-layer prompt system:
      Layer 1 (base): Universal instructions from settings — applies to ALL sessions
      Layer 2 (mode): Project or plugin specific instructions from settings
      Layer 3 (addendum): Auto-generated context — plugin docs, task description

    For project mode: base + project_instructions + task
    For plugin mode: base + plugin_instructions + plugin docs + task
    """
    base = base_instructions or _DEFAULT_CODER_INSTRUCTIONS

    parts = [
        f"# {project_name}\n",
        "## Instructions\n",
        base.strip(),
    ]

    if mode_instructions:
        parts.append(f"\n\n## Mode-Specific Rules\n")
        parts.append(mode_instructions.strip())

    parts.append("\n\n## General\n")
    parts.append("- Work only within this directory")
    parts.append("- Do not access files outside the workspace")
    parts.append("- Dispatched by Sapphire AI on behalf of the user.")

    if addendum:
        parts.append("\n\n---\n")
        parts.append(addendum)

    return '\n'.join(parts)


def _build_plugin_addendum(plugin_name, description, capabilities=None, context=None):
    """Build the plugin-mode addendum with capability-aware doc injection.

    Only injects docs for the capabilities the plugin actually needs.
    """
    # Coerce capabilities to list — LLMs often send "providers, settings" as a string
    if isinstance(capabilities, str):
        caps = [c.strip() for c in capabilities.split(',') if c.strip()]
    else:
        caps = capabilities or []
    docs_dir = Path(_SAPPHIRE_ROOT) / "docs" / "plugin-author"

    parts = [
        f"# Plugin Build: {plugin_name}\n",
        f"You are building a Sapphire AI plugin called \"{plugin_name}\".\n",
        f"## Task\n{description}\n",
    ]

    if caps:
        parts.append(f"## Requested Capabilities\n{', '.join(caps)}\n")

    # Handoff checklist
    parts.append("## Handoff Checklist")
    parts.append("Before declaring done, you MUST:")
    parts.append(f'1. Validate manifest: python -c "import json; json.load(open(\'plugin.json\'))"')
    parts.append(f'2. Test tool imports (if tools): python -c "exec(open(\'tools/{plugin_name}_tools.py\').read())"')
    parts.append("3. Run any tests you wrote")
    parts.append("4. Report ALL results in your final message\n")

    # Note: Plugin-specific rules (manifest format, validation, file structure)
    # are in the plugin_instructions settings textarea — user-editable, not hardcoded here.

    # Always inject ai-reference.md — the compact everything reference
    ai_ref = docs_dir / "ai-reference.md"
    if ai_ref.exists():
        parts.append(f"## Plugin System Reference\n{ai_ref.read_text(encoding='utf-8').strip()}\n")

    # Quick start example from README.md
    readme = docs_dir / "README.md"
    if readme.exists():
        readme_text = readme.read_text(encoding='utf-8')
        # Extract Quick Start section
        qs_match = re.search(r'## Quick Start\n(.*?)(?=\n---|\n## )', readme_text, re.DOTALL)
        if qs_match:
            parts.append(f"## Quick Start Example\n{qs_match.group(1).strip()}\n")

    # Tool file format from tools.md
    if not caps or 'tools' in caps:
        tools_doc = docs_dir / "tools.md"
        if tools_doc.exists():
            tools_text = tools_doc.read_text(encoding='utf-8')
            # Extract tool file format section
            tf_match = re.search(r'## Tool File Format\n(.*?)(?=\n### Required Exports|\n## )', tools_text, re.DOTALL)
            if tf_match:
                parts.append(f"## Tool File Format\n{tf_match.group(1).strip()}\n")

    # Capability-specific docs — only inject what's needed
    cap_docs = {
        'hooks': 'hooks.md',
        'routes': 'routes.md',
        'daemon': 'daemons.md',
        'daemons': 'daemons.md',
        'settings': 'settings.md',
        'providers': 'providers.md',
        'schedule': 'schedule.md',
    }
    for cap in caps:
        doc_file = cap_docs.get(cap)
        if doc_file:
            doc_path = docs_dir / doc_file
            if doc_path.exists():
                parts.append(f"## {cap.title()} Reference\n{doc_path.read_text(encoding='utf-8').strip()}\n")

    # Optional freeform context (API docs, format specs, etc.)
    if context:
        parts.append(f"## Additional Context\n{context}\n")

    return '\n'.join(parts)


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
    user_plugins = os.path.join(_SAPPHIRE_ROOT, 'user', 'plugins')
    if ws.startswith(_SAPPHIRE_ROOT) and not ws.startswith(user_plugins):
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


def _write_claude_md(workspace, base_instructions=None, mode_instructions=None,
                     project_name='project', addendum=None):
    """Write CLAUDE.md into workspace. Skips if already exists (resume case).

    Args:
        workspace: Directory path
        base_instructions: Base layer from settings (universal, all sessions)
        mode_instructions: Mode layer from settings (project-specific or plugin-specific)
        project_name: Display name for the project
        addendum: Auto-generated content (plugin docs, task context)
    """
    claude_md_path = os.path.join(workspace, 'CLAUDE.md')
    if os.path.exists(claude_md_path):
        return
    content = _build_claude_md(project_name, base_instructions, mode_instructions, addendum)
    try:
        with open(claude_md_path, 'w', encoding='utf-8') as f:
            f.write(content)
    except OSError as e:
        logger.warning(f"[claude-code] Could not write CLAUDE.md: {e}")


def _build_claude_args(mission, settings, session_id=None, model_override=None):
    mode = settings.get('mode', 'standard')
    max_turns = int(settings.get('max_turns', 50))
    args = ['claude', '-p', mission, '--output-format', 'json']
    if session_id:
        args.extend(['--resume', session_id])
    args.extend(['--max-turns', str(max_turns)])

    # Model override (plugin builds can use cheaper models)
    model = model_override or settings.get('plugin_build_model', '')
    if model:
        args.extend(['--model', model])

    # Budget cap
    budget = settings.get('max_budget_usd')
    if budget and float(budget) > 0:
        args.extend(['--max-budget-usd', str(float(budget))])

    if mode == 'strict':
        args.extend(['--allowedTools', 'Read,Edit,Write,Glob,Grep'])
    elif mode == 'system_killer':
        args.extend(['--allowedTools', 'Read,Edit,Write,Glob,Grep,Bash,NotebookEdit,WebFetch,WebSearch'])
    else:
        args.extend(['--allowedTools', 'Read,Edit,Write,Glob,Grep,Bash,NotebookEdit'])

    # Give Claude Code read access to plugin docs, reference plugins, and logs
    docs_dir = os.path.join(_SAPPHIRE_ROOT, 'docs')
    if os.path.isdir(docs_dir):
        args.extend(['--add-dir', docs_dir])
    # Reference plugin — a real working TTS provider Claude Code can study
    ref_plugin = os.path.join(_SAPPHIRE_ROOT, 'plugins', 'elevenlabs')
    if os.path.isdir(ref_plugin):
        args.extend(['--add-dir', ref_plugin])
    # Sapphire logs — so Claude Code can diagnose runtime errors
    logs_dir = os.path.join(_SAPPHIRE_ROOT, 'user', 'logs')
    if os.path.isdir(logs_dir):
        args.extend(['--add-dir', logs_dir])

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

    if result.returncode != 0:
        # Chaos #6: non-zero exit = error, regardless of whether stdout has
        # parseable JSON. Previously we let returncode!=0 slide when stdout
        # was non-empty, but that swallowed budget-cap-mid-gen and similar
        # partial-success cases where claude produced SOME output but didn't
        # finish the task. Caller retries are cheap; swallowed failures aren't.
        stderr_tail = (result.stderr or '')[-500:]
        stdout_tail = (result.stdout or '').strip()[-300:]
        return None, (
            f"Claude Code exited with error (code {result.returncode}). "
            f"stderr: {stderr_tail!r} stdout_tail: {stdout_tail!r}"
        )

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
            # No parseable JSON anywhere in stdout — treat as failure rather
            # than silently presenting raw text as a "successful" structured
            # result. Caller relies on err=None meaning "claude returned
            # well-formed output"; honoring that contract avoids downstream
            # validation chains accepting garbage.
            tail = (result.stdout or '').strip()[-500:]
            return None, f"Claude Code returned no parseable JSON output (last 500 chars: {tail!r})"
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

    base = settings.get('coder_instructions', '')
    mode = settings.get('project_instructions', '')
    _write_claude_md(workspace, base, mode, project_name)

    args = _build_claude_args(mission, settings, session_id=session_id)
    if _HAS_NAME_FLAG:
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

def _activate_plugin(arguments):
    """Validate and activate a built plugin."""
    name = arguments.get('name', '').strip()
    if not name:
        return "Plugin name is required.", False

    workspace = os.path.join(_SAPPHIRE_ROOT, 'user', 'plugins', name)
    if not os.path.isdir(workspace):
        return f"Plugin directory not found: user/plugins/{name}/", False

    # Run validation. Keys starting with '_' are diagnostic payloads (e.g.
    # `_missing_files`), not checks — skip them for pass/fail logic.
    validation = _validate_plugin(workspace)
    public_checks = {k: v for k, v in validation.items() if not k.startswith('_')}
    all_passed = all(public_checks.values())

    lines = ["**Plugin Validation:**"]
    for check, passed in public_checks.items():
        icon = '\u2713' if passed else '\u2717'
        lines.append(f"  {icon} {check}")

    if not all_passed:
        failed = [k for k, v in public_checks.items() if not v]
        lines.append(f"\n**Cannot activate** — failed checks: {', '.join(failed)}")
        missing = validation.get('_missing_files') or []
        if missing:
            lines.append(f"**Missing files:** {', '.join(missing)}")
        lines.append(f"**To fix:** Use `spawn_agent(agent_type='claude_code_plugin', "
                     f"plugin_name='{name}')` with the error details in the mission. "
                     f"Do NOT troubleshoot with run_command.")
        return '\n'.join(lines), False

    # Rescan and enable
    try:
        from core.plugin_loader import plugin_loader

        # Rescan to discover the new plugin
        plugin_loader.rescan()

        info = plugin_loader.get_plugin_info(name)
        if not info:
            lines.append(f"\n**Plugin not found after rescan.** Check plugin.json 'name' field matches '{name}'.")
            return '\n'.join(lines), False

        # Enable if not already enabled.
        # Chaos #8: reload FIRST, persist to plugins.json only after we've
        # confirmed the plugin actually loaded. Otherwise plugins.json ends up
        # referencing a broken plugin that fails on every boot.
        if not info.get('enabled'):
            # Mark enabled in memory BEFORE reload — rescan set it False
            # because plugins.json wasn't written yet when rescan read it
            if name in plugin_loader._plugins:
                plugin_loader._plugins[name]['enabled'] = True
            plugin_loader.reload_plugin(name)

            # Only persist to plugins.json if the reload actually loaded it
            post_reload = plugin_loader.get_plugin_info(name) or {}
            if post_reload.get('loaded'):
                enabled_path = Path(_SAPPHIRE_ROOT) / 'user' / 'webui' / 'plugins.json'
                try:
                    enabled_data = json.loads(enabled_path.read_text(encoding='utf-8')) if enabled_path.exists() else {}
                except Exception:
                    enabled_data = {}
                enabled_list = enabled_data.get('enabled', [])
                if name not in enabled_list:
                    enabled_list.append(name)
                    enabled_data['enabled'] = enabled_list
                    enabled_path.parent.mkdir(parents=True, exist_ok=True)
                    enabled_path.write_text(json.dumps(enabled_data, indent=2), encoding='utf-8')
            else:
                # Reload failed — roll back in-memory enabled so state matches
                # reality, and DO NOT write plugins.json (would stick a broken
                # plugin in the enabled list across boots).
                if name in plugin_loader._plugins:
                    plugin_loader._plugins[name]['enabled'] = False
                lines.append(f"\n**Reload failed — plugin not loaded.** Not persisting to plugins.json.")
                verify_msg = (post_reload.get('verify_msg') or '').strip()
                if verify_msg and 'unsigned' not in verify_msg:
                    lines.append(f"Reason: {verify_msg}")
                return '\n'.join(lines), False

        info = plugin_loader.get_plugin_info(name)
        loaded = info.get('loaded', False) if info else False

        # Check if this plugin has providers (needs restart to register)
        manifest_info = info.get('manifest', {}) if info else {}
        has_providers = bool(manifest_info.get('capabilities', {}).get('providers'))

        lines.append(f"\n**Plugin activated: {name}**")
        if loaded:
            tool_list = manifest_info.get('capabilities', {}).get('tools', [])
            if isinstance(tool_list, str):
                tool_list = [tool_list]
            lines.append(f"- Status: loaded and enabled")
            if tool_list:
                lines.append(f"- Tool files: {', '.join(tool_list)}")
        else:
            lines.append(f"- Status: enabled but not yet loaded")

        if has_providers:
            lines.append(f"\n**Note:** This plugin provides a TTS/STT/LLM/Embedding provider. "
                         f"A Sapphire restart is needed for the provider to appear in settings. "
                         f"Tell the user: 'The plugin is ready — restart Sapphire to activate the provider.'")

        lines.append(f"\nIf there are runtime bugs after activation, use "
                     f"`spawn_agent(agent_type='claude_code_plugin', plugin_name='{name}')` "
                     f"to fix them. Do NOT troubleshoot with run_command.")

        return '\n'.join(lines), True
    except Exception as e:
        lines.append(f"\n**Activation failed:** {e}")
        return '\n'.join(lines), False


def execute(function_name, arguments, config):
    try:
        if function_name == 'code_session':
            return _code_session(arguments)
        elif function_name == 'activate_plugin':
            return _activate_plugin(arguments)
        else:
            return f"Unknown function: {function_name}", False
    except Exception as e:
        logger.error(f"[claude-code] {function_name} failed: {e}", exc_info=True)
        return f"Claude Code error: {e}", False
