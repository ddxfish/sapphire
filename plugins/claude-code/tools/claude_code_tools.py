# plugins/claude-code/tools/claude_code_tools.py
# The activate_plugin tool and the helpers the `claude_code` agent kind
# (plugins/claude-code/agent_kind.py) shares: plugin validation, CLAUDE.md
# composition, doc injection, the conda-scrubbed env, and the binary resolver.
# The headless `code_session` tool and the CodeWorker/PluginWorker went with
# agents v2 (2026-10-06, tmp/agents-v2.md §5): a coding session is an agent now.
import json
import logging
import os
import re
import shutil
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

ENABLED = True
EMOJI = '\u26a1'
AVAILABLE_FUNCTIONS = ['activate_plugin']

TOOLS = [
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "activate_plugin",
            "description": "Activate a plugin after a claude_code agent built it in plugin mode. Runs structural validation, rescans plugins, and enables the new plugin. Call it with the plugin's directory name once the agent reports done.",
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

# .absolute() not .resolve() — .resolve() follows symlinks/junctions,
# misroots SAPPHIRE_ROOT on Windows dev installs. herring #24.
_SAPPHIRE_ROOT = str(Path(__file__).absolute().parent.parent.parent.parent)
_IS_WINDOWS = sys.platform == 'win32'

_DEFAULT_CODER_INSTRUCTIONS = """You are a code builder. Write clean, working code.
- Test your work by running it before reporting done
- Include a README.md with usage instructions
- Keep it simple and minimal — no over-engineering
- If you hit a problem you can't solve, describe it clearly in your final response
- If you notice anything noteworthy that isn't part of your task, write it to NOTES.md"""


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
        # Atomic write (tmp + rename) so the plugin file watcher can't read
        # a half-written manifest during the rewrite
        try:
            tmp_path = manifest_path + '.tmp'
            with open(tmp_path, 'w', encoding='utf-8') as f:
                json.dump(manifest, f, indent=2, ensure_ascii=False)
            os.replace(tmp_path, manifest_path)
            logger.info(f"[claude-code] Auto-fixed manifest: tools string -> list")
            results['manifest_auto_fixed'] = True
        except Exception:
            try:
                os.remove(tmp_path)
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
        or bool(caps.get('agents'))
        or bool(caps.get('devices'))
        or bool(caps.get('games'))
        or bool(caps.get('memory_layers'))
        or bool(caps.get('prompts'))
        or bool(caps.get('widgets'))
        or bool(caps.get('web'))
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


def _resolve_claude_executable(env, name='claude'):
    """Resolve the `claude` CLI (or the command `name` the user typed) to its
    full path, honoring PATHEXT on Windows.

    Returns (full_path, None) on success, (None, error_message) on failure.

    Why this is its own helper: `subprocess.Popen(['claude', ...])` with
    `shell=False` on Windows uses CreateProcessW, which does NOT search
    PATHEXT for `.cmd`/`.bat` wrappers. Since `npm install -g
    @anthropic-ai/claude-code` installs `claude.cmd` (not `claude.exe`),
    Popen with the bare command silently fails with FileNotFoundError
    even though `shutil.which('claude')` (which DOES honor PATHEXT)
    finds it. Resolving up-front with shutil.which and passing the
    absolute path to Popen sidesteps the issue. 2026-05-14.
    """
    path_env = env.get('PATH', '')
    resolved = shutil.which(name or 'claude', path=path_env) or (shutil.which('claude', path=path_env) if name != 'claude' else None)
    if resolved:
        logger.info(f"[claude-code] Resolved claude -> {resolved}")
        return resolved, None

    # Build a helpful diagnostic. Include the searched PATH (truncated),
    # whether we're on Windows (relevant for npm .cmd wrappers), and
    # platform-specific install hints.
    path_preview = path_env if len(path_env) < 800 else path_env[:800] + '…(truncated)'
    pathext = env.get('PATHEXT', '') if _IS_WINDOWS else None
    platform = 'Windows' if _IS_WINDOWS else ('macOS' if sys.platform == 'darwin' else 'Linux')
    hints = []
    if _IS_WINDOWS:
        hints.append(
            "Windows: npm-installed claude.cmd lives in %APPDATA%\\npm\\ — confirm "
            "that directory is on PATH for the user running Sapphire."
        )
        hints.append(
            "If using the native installer, claude.exe is usually under "
            "%LOCALAPPDATA%\\Programs\\Anthropic\\ or similar."
        )
    else:
        hints.append(
            "Common locations: ~/.nvm/versions/node/vXX/bin/, ~/.local/bin/, "
            "/usr/local/bin/. Ensure your shell startup adds the install dir to PATH."
        )
        hints.append(
            "If Sapphire runs as a systemd service, the unit's PATH may differ "
            "from your interactive shell. Run `systemctl --user show-environment | grep PATH` "
            "to compare."
        )
    diag = (
        f"Claude Code command 'claude' not found on PATH.\n"
        f"  Platform: {platform}\n"
        f"  Searched PATH: {path_preview}\n"
    )
    if pathext is not None:
        diag += f"  PATHEXT: {pathext}\n"
    diag += "\n".join("  Hint: " + h for h in hints)
    diag += (
        "\n  Install: npm install -g @anthropic-ai/claude-code "
        "(or use the official native installer)"
    )
    logger.warning(f"[claude-code] {diag}")
    return None, diag


# Windows reserves these as device names in EVERY directory, case-insensitively
# (with or without an extension): a workspace called `con` or `aux` cannot be
# made, or resolves to the device (windows scout, 2026-10-07).
_RESERVED = {'con', 'prn', 'aux', 'nul'} | {f'com{i}' for i in range(1, 10)} | {f'lpt{i}' for i in range(1, 10)}


def _unreserved(name):
    return f"{name}-ws" if name.split('.')[0].lower() in _RESERVED else name


def _slugify(text, max_len=40):
    words = re.sub(r'[^a-zA-Z0-9\s]', '', text).split()[:6]
    slug = '-'.join(w.lower() for w in words)
    return _unreserved(slug[:max_len] or 'project')


def _safe_dir_name(text, default='project'):
    """Filesystem-safe directory name. Preserves hyphens/underscores, blocks path traversal.
    Strips anything that isn't alnum/hyphen/underscore and forces start with alnum."""
    if not text:
        return default
    cleaned = re.sub(r'[^a-zA-Z0-9_-]', '', str(text)).lower().lstrip('-_')[:64]
    return _unreserved(cleaned) if cleaned else default


def _resolve_workspace(settings, project_name):
    base = settings.get('workspace_dir', '~/claude-workspaces')
    base_path = Path(os.path.expanduser(base)).resolve()
    safe = _safe_dir_name(project_name)
    workspace_path = (base_path / safe).resolve()
    # Defense-in-depth: reject any escape from base even if _safe_dir_name is somehow bypassed
    try:
        workspace_path.relative_to(base_path)
    except ValueError:
        return None, f"Invalid project name (path escape rejected): {project_name!r}"
    workspace = str(workspace_path)
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
        lines.append(f"**To fix:** agent_action('<the agent>', 'say', '<these errors>') on the agent that "
                     f"built it, or agent_spawn('claude_code', '<fix these errors>', {{'mode': 'plugin', 'name': '{name}'}}). "
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

        lines.append(f"\nIf there are runtime bugs after activation, say so to the agent that built it "
                     f"(agent_action(..., 'say', ...)) or agent_spawn('claude_code', '<the bug>', "
                     f"{{'mode': 'plugin', 'name': '{name}'}}). Do NOT troubleshoot with run_command.")

        return '\n'.join(lines), True
    except Exception as e:
        lines.append(f"\n**Activation failed:** {e}")
        return '\n'.join(lines), False


def _sanity_check(workspace_path, mode='project', root=None):
    """Mode-aware (agents v2): a project workspace must sit OUTSIDE Sapphire's
    tree and outside any Python environment; plugin mode lives in
    user/plugins; core mode IS the Sapphire root (`root` overrides the
    module's own, for a caller that knows better)."""
    ws = str(Path(workspace_path).resolve())
    root = str(Path(root or _SAPPHIRE_ROOT).resolve())
    user_plugins = os.path.join(root, 'user', 'plugins')
    if mode == 'project' and ws.startswith(root):
        return f"SAFETY: Workspace '{ws}' is inside Sapphire's project directory. Use an external directory."
    if mode == 'plugin' and not ws.startswith(user_plugins):
        return f"SAFETY: a plugin workspace must live under user/plugins, not '{ws}'."
    if mode == 'core' and ws != root:
        return f"SAFETY: core mode runs at the Sapphire root, not '{ws}'."
    ws_posix = Path(ws).as_posix().lower()        # the markers are '/'-shaped; Windows paths are not
    for marker in ['/envs/', '/conda', '/.venv/', '/virtualenvs/']:
        if mode == 'project' and marker in ws_posix:
            return f"SAFETY: Workspace '{ws}' appears to be inside a Python environment."
    return None


def execute(function_name, arguments, config):
    try:
        if function_name == 'activate_plugin':
            return _activate_plugin(arguments)
        return f"Unknown function: {function_name}", False
    except Exception as e:
        logger.error(f"[claude-code] {function_name} failed: {e}", exc_info=True)
        return f"Claude Code error: {e}", False
