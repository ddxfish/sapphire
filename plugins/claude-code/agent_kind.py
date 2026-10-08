# plugins/claude-code/agent_kind.py - the `claude_code` agent kind: a Claude Code session
#
# One agent = one Claude Code session on the claude-agent-sdk, alive between
# turns (conversational). Sapphire spawns it with a mission; it works; its
# report lands in her chat through the inbox. When Claude Code hits a fork it
# asks with AskUserQuestion, and that - and only that - reaches her as a
# question she answers with agent_action(..., 'answer', ...). Permissions are
# never routed to her (Krem, 2026-10-06: "catastrophic for all of us"): the
# session runs in the CLI's `auto` mode, where a classifier approves or denies
# each tool call; what it does not approve is denied here with a reason.
#
# Proven live by scout D (tmp/agents-v2.md §19): the callback fires for
# AskUserQuestion under bypass AND auto; a 620 s pending answer reaches Claude;
# the session resumes by id after a disconnect; interrupt leaves it usable.
#
# Modes: project (a workspace under workspace_dir), plugin (user/plugins/<name>,
# with the plugin-author docs injected and a validation pass after), core (the
# Sapphire root itself). Replaces plugins/claude-code's headless workers and
# the Trinity tmux plugin.
import asyncio
import sys
import logging
import os
import queue
import threading
import time
import warnings
from pathlib import Path

from core.agents.base import Agent as BaseAgent

logger = logging.getLogger(__name__)

STORE = 'claude-code'
ASK_FORK_S = 600               # a fork waits this long for the director
_ROOT = str(Path(__file__).absolute().parent.parent.parent)
_HELPERS = None


def _helpers():
    """The plugin's tool module (validation, CLAUDE.md, env, binary)."""
    global _HELPERS
    if _HELPERS is None:
        import importlib
        _HELPERS = importlib.import_module('plugins.claude-code.tools.claude_code_tools')
    return _HELPERS


def _settings():
    try:
        from core.plugin_loader import plugin_loader
        return plugin_loader.get_plugin_settings(STORE) or {}
    except Exception:
        return {}


def _sdk():
    try:
        import claude_agent_sdk as sdk
        return sdk
    except ImportError:
        raise RuntimeError("claude-agent-sdk is not installed in Sapphire's environment. "
                           "Install it there: pip install -r install/requirements-agents.txt")


def _head(text, n=120):
    s = ' '.join(str(text or '').split())
    return s if len(s) <= n else s[:n - 1] + '…'


class Agent(BaseAgent):
    """A Claude Code session. run(mission) opens it and takes the first turn;
    say(text) sends another; stop() interrupts and closes it. Idle past
    idle_timeout_min it rests - the row keeps the session id, and the next
    say() wakes it with --resume."""

    def __init__(self, row, engine):
        super().__init__(row, engine)
        h = _helpers()
        o = self.options
        self.mode = str(o.get('mode') or 'project').strip().lower()
        if self.mode not in ('project', 'plugin', 'core'):
            self.mode = 'project'
        # The workspace name sticks to the ROW once chosen: a wake passes the
        # say text as the mission, and re-deriving the name from it moved a
        # project-mode session into a fresh empty folder (two scouts, 2026-10-07).
        self.wsname = (str(row.get('wsname') or '').strip()
                       or h._safe_dir_name(o.get('name') or '', default='') or h._slugify(self.mission))
        if o.get('context'):
            self.mission = f"{self.mission}\n\n---\n\nContext:\n{o['context']}"
        self._say_q = queue.Queue()
        self._loop = None
        self._client = None
        self._cost = 0.0
        self._api_key = None            # apiKeySource when a key is billed; None = the login
        self.workspace = None

    # --- the thread -----------------------------------------------------------

    def run(self, mission):
        sdk = _sdk()
        warnings.filterwarnings('ignore', category=sdk.CanUseToolShadowedWarning)  # wrong for AskUserQuestion (verified)
        if self._cancelled.is_set():
            return                                   # stopped before it began: no CLI is ever spawned
        settings = _settings()
        self.workspace = self._workspace(settings)
        self.remember(wsname=self.wsname)            # the row keeps the name a wake must reuse
        self._prepare_workspace(settings)
        options = self._options(sdk, settings)
        self.event('note', f"{self.mode} mode in {self.workspace}" + (' (resumed)' if self.resume_token else ''))
        try:
            self._run_loop(self._main(sdk, options, mission))
        except (sdk.ProcessError, sdk.CLIConnectionError) as e:
            self.error = str(e)
            self.status = 'failed'
            self.report(f"[{self.name} stopped: {_head(e, 300)}]")
            return
        if self._cancelled.is_set():
            return
        # the loop ended on the idle timeout: rest, with the session to pick up
        self.status = 'resting' if self.resume_token else 'done'

    def _run_loop(self, coro):
        """The session's own event loop on this thread. On Windows sapphire.py
        pins the Selector policy process-wide, and a Selector loop cannot spawn
        a subprocess - asyncio.run() inherited it and the SDK could not start
        the CLI at all (windows scout, 2026-10-07). A Proactor loop built here
        needs no signal handling; the global policy stays untouched - the same
        pattern as plugins/mcp_client. No asyncio.run() either: its executor
        shutdown waited on a blocked ask() for the full fork timeout."""
        loop = asyncio.ProactorEventLoop() if sys.platform == 'win32' else asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(coro)
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:
                pass
            asyncio.set_event_loop(None)
            loop.close()

    async def _main(self, sdk, options, mission):
        self._loop = asyncio.get_running_loop()
        idle_s = max(60, int(float(_settings().get('idle_timeout_min') or 60) * 60))
        try:
            async with sdk.ClaudeSDKClient(options) as client:
                self._client = client
                # Stop arrived while the CLI was starting (the × in the first
                # seconds): the mission must not run anyway (race scout).
                if self._cancelled.is_set():
                    return
                await self._turn(sdk, mission)
                while not self._cancelled.is_set():
                    self.status = 'idle'
                    text = await self._loop.run_in_executor(None, self._next_say, idle_s)
                    if text is None:
                        # the idle timeout: from here on say() is refused, so a say
                        # that slipped in meanwhile is taken, not lost on a queue
                        # nothing reads (race scout, 2026-10-07)
                        self.status = 'resting'
                        text = self._next_say(0)
                        if text is None or self._cancelled.is_set():
                            break
                    await self._turn(sdk, text)
        finally:
            self._client = None
            # a fork question still waiting when the session dies would hold
            # its thread for the whole timeout; the answer is None now
            self._end_question(None)

    def _next_say(self, idle_s):
        try:
            return self._say_q.get(timeout=idle_s) if idle_s > 0 else self._say_q.get_nowait()
        except queue.Empty:
            return None

    async def _turn(self, sdk, text):
        self.status = 'running'
        buf = []
        await self._client.query(text)
        async for msg in self._client.receive_response():
            if isinstance(msg, sdk.SystemMessage) and msg.subtype == 'init':
                self.resume_token = msg.data.get('session_id') or self.resume_token
                src = msg.data.get('apiKeySource')
                if src not in (None, 'none'):
                    self._api_key = src
                    self.event('note', f"billing an API key ({src}), not the login")
            elif isinstance(msg, sdk.AssistantMessage):
                for b in msg.content:
                    if isinstance(b, sdk.TextBlock) and b.text:
                        buf.append(b.text)
                        self.event('text', _head(b.text, 200))
                    elif isinstance(b, sdk.ToolUseBlock):
                        self.event('tool_use', {'name': b.name, 'input': _input_head(b.input)})
            elif isinstance(msg, sdk.UserMessage) and isinstance(msg.content, list):
                for b in msg.content:
                    if isinstance(b, sdk.ToolResultBlock):
                        self.event('tool_result', {'id': b.tool_use_id, 'is_error': bool(b.is_error)})
            elif isinstance(msg, sdk.ResultMessage):
                self.resume_token = msg.session_id or self.resume_token
                if msg.total_cost_usd is not None:
                    self._cost = float(msg.total_cost_usd)
                body = '\n'.join(buf).strip()
                if msg.terminal_reason in ('aborted_streaming', 'aborted_tools'):
                    self.report((body + '\n\n' if body else '') + '[stopped by the director]')
                elif msg.is_error:
                    why = '; '.join(msg.errors or []) or (msg.result or msg.subtype or 'error')
                    self.report((body + '\n\n' if body else '') + f"[ended: {msg.subtype} — {_head(why, 300)}]")
                else:
                    # total_cost_usd is the CLI's estimate at API list price, computed
                    # whether or not anyone is billed. On the login (Pro/Max) it is
                    # not a bill, so it only shows when a key is (verified 2026-10-07).
                    cost = f"cost so far ${self._cost:.2f} ({self._api_key}) · " if self._api_key else ''
                    tail = f"\n\n({cost}session {str(self.resume_token or '')[:8]})"
                    denied = _denials(msg)
                    if denied:
                        # auto mode's classifier said no to these; the director was not
                        # asked (by rule) - but should know what was refused
                        tail = f"\n\n[not allowed by the permission classifier: {denied}]" + tail
                    if self.mode == 'plugin':
                        body = (body or msg.result or '') + self._plugin_check()
                    self.report((body or msg.result or '(no text)') + tail)
                    if self.mode == 'project':
                        self._workspace_ready()

    # --- what the director does -------------------------------------------------

    def say(self, text):
        if self._client is None or self.status != 'idle':
            return f"{self.name} is {self.status}; it takes a say only while idle.", False
        self._say_q.put(text)
        return f"{self.name} took your message.", True

    def stop(self):
        super().stop()
        loop, client = self._loop, self._client
        if loop is not None and client is not None:
            try:
                loop.call_soon_threadsafe(lambda: asyncio.ensure_future(client.interrupt()))
            except Exception as e:
                logger.debug(f"[claude-code] interrupt not sent: {e}")
        self._say_q.put(None)

    # --- the gate: AskUserQuestion to her, nothing else -------------------------

    async def _gate(self, name, inp, ctx):
        sdk = _sdk()
        if name != 'AskUserQuestion':
            # auto mode's classifier did not approve this call. The director is
            # never asked per command (Krem's ruling): say no, with a reason.
            return sdk.PermissionResultDeny(
                message="This call was not approved automatically, and the director is not asked "
                        "per command. Take another route, or report what you need and why.")
        questions = inp.get('questions') or []
        answers = await self._loop.run_in_executor(None, self.ask, {'questions': questions}, ASK_FORK_S)
        if not answers:
            return sdk.PermissionResultDeny(
                message="No answer from the director in time; choose the safe default and continue.")
        if isinstance(answers, str):
            answers = {q.get('question', ''): answers for q in questions}
        return sdk.PermissionResultAllow(updated_input={'questions': questions, 'answers': answers})

    # --- setup ------------------------------------------------------------------------

    def _workspace(self, settings):
        h = _helpers()
        if self.mode == 'core':
            ws = _ROOT
        elif self.mode == 'plugin':
            if not self.wsname:
                raise RuntimeError("plugin mode needs a name: the plugin's directory under user/plugins.")
            base = Path(_ROOT) / 'user' / 'plugins'
            ws_path = (base / self.wsname).resolve()
            try:
                ws_path.relative_to(base.resolve())
            except ValueError:
                raise RuntimeError(f"Invalid plugin name (path escape rejected): {self.wsname!r}")
            ws = str(ws_path)
            if not self.resume_token and os.path.isdir(ws) and os.listdir(ws):
                # Chaos #5: never silently build over an existing plugin
                raise RuntimeError(f"Plugin directory '{self.wsname}' already exists with contents. "
                                   f"Pick a unique name, or say() to the agent that built it.")
            os.makedirs(ws, exist_ok=True)
        else:
            ws, err = h._resolve_workspace(settings, self.wsname or 'project')
            if err:
                raise RuntimeError(err)
        err = h._sanity_check(ws, mode=self.mode, root=_ROOT)
        if err:
            raise RuntimeError(err)
        return ws

    def _prepare_workspace(self, settings):
        """project/plugin: a CLAUDE.md from the textareas if the workspace has
        none. core: the root's own CLAUDE.md stays; the core textarea rides
        the system prompt's append instead."""
        h = _helpers()
        if self.mode == 'core' or self.resume_token:
            return
        base = settings.get('coder_instructions', '')
        if self.mode == 'plugin':
            caps = self.options.get('capabilities') or []
            addendum = h._build_plugin_addendum(self.wsname, self.mission, caps, None)
            h._write_claude_md(self.workspace, base, settings.get('plugin_instructions', ''),
                               self.wsname, addendum=addendum)
        else:
            h._write_claude_md(self.workspace, base, settings.get('project_instructions', ''), self.wsname)

    def _options(self, sdk, settings):
        h = _helpers()
        clean = h._clean_env()
        env = {k: '' for k in ('CONDA_PREFIX', 'CONDA_DEFAULT_ENV', 'CONDA_PROMPT_MODIFIER', 'CONDA_SHLVL',
                               'CONDA_PYTHON_EXE', 'CONDA_EXE', 'VIRTUAL_ENV', 'UV_VIRTUALENV')}
        env['PATH'] = clean.get('PATH', '')
        env['ANTHROPIC_API_KEY'] = ''              # the login, never an inherited key
        cli_path = None
        wanted = str(settings.get('claude_binary') or '').strip().strip('"').strip("'")   # Explorer's "Copy as path" quotes
        if wanted:
            if os.path.isfile(wanted):
                cli_path = wanted
            else:
                # a command NAME ('claude', 'claude.exe') is looked up as typed; the
                # setting used to ignore the value and always look up 'claude'
                resolved, err = h._resolve_claude_executable(clean, name=os.path.basename(wanted) or 'claude')
                if err:
                    raise RuntimeError(err)
                cli_path = resolved
        append = ''
        if self.mode == 'core':
            parts = [str(settings.get('coder_instructions') or '').strip(),
                     str(settings.get('core_instructions') or '').strip()]
            append = '\n\n'.join(p for p in parts if p)
        add_dirs = []
        if self.mode in ('plugin', 'core'):
            # docs for both; the logs only in core mode (ruled unrestricted). A
            # plugin-mode session is the narrower one, and the logs carry chat
            # names and exception text (privacy scout, 2026-10-07).
            rels = (('docs',), ('user', 'logs')) if self.mode == 'core' else (('docs',),)
            for rel in rels:
                d = os.path.join(_ROOT, *rel)
                if os.path.isdir(d):
                    add_dirs.append(d)
            if self.mode == 'plugin':
                ref = os.path.join(_ROOT, 'plugins', 'elevenlabs')
                if os.path.isdir(ref):
                    add_dirs.append(ref)
        model = str(self.options.get('model') or settings.get('model') or '').strip() or None
        effort = str(self.options.get('effort') or settings.get('effort') or '').strip() or None
        try:
            budget = float(settings.get('max_budget_usd') or 0) or None
        except (TypeError, ValueError):
            budget = None
        mode = str(settings.get('permission_mode') or 'auto')
        if mode not in ('auto', 'bypassPermissions'):
            mode = 'auto'
        system_prompt = {'type': 'preset', 'preset': 'claude_code'}
        if append:
            system_prompt['append'] = append
        return sdk.ClaudeAgentOptions(
            cwd=self.workspace,
            permission_mode=mode,
            can_use_tool=self._gate,
            system_prompt=system_prompt,                       # None would mean an EMPTY prompt (scout D)
            setting_sources=None if self.mode == 'core' else ['project'],   # not the operator's ~/.claude
            add_dirs=add_dirs,
            model=model,
            effort=effort,
            max_budget_usd=budget,
            resume=self.resume_token or None,
            cli_path=cli_path,
            env=env,
            strict_mcp_config=(self.mode != 'core'),
            mcp_servers={} if self.mode != 'core' else {},
        )

    def _workspace_ready(self):
        """Project mode: an index.html or a runnable .py in the workspace gets the
        Open/Run pill above the chat (WORKSPACE_READY), as the old worker did."""
        try:
            _helpers()._publish_workspace_ready(self.wsname or 'project', self.workspace)
        except Exception as e:
            logger.debug(f"[claude-code] workspace_ready not published: {e}")

    def _plugin_check(self):
        """Plugin mode: the structural validation after a turn, as report lines."""
        h = _helpers()
        try:
            v = h._validate_plugin(self.workspace)
        except Exception as e:
            return f"\n\nValidation did not run: {e}"
        public = {k: x for k, x in v.items() if not k.startswith('_')}
        lines = ['', '', 'Validation:'] + [f"  {'✓' if ok else '✗'} {k}" for k, ok in public.items()]
        if all(public.values()):
            lines.append(f"Next: activate_plugin({self.wsname!r}) to enable it.")
        else:
            missing = v.get('_missing_files') or []
            if missing:
                lines.append(f"Missing files: {', '.join(missing)}")
            lines.append(f"Say the failures back to {self.name} (agent_action('{self.name}', 'say', ...)).")
        return '\n'.join(lines)


def _denials(msg):
    """The tool calls auto mode's classifier refused this turn, as one short line."""
    try:
        items = list(getattr(msg, 'permission_denials', None) or [])
    except Exception:
        return ''
    names = []
    for d in items[:6]:
        name = d.get('tool_name') if isinstance(d, dict) else getattr(d, 'tool_name', None)
        inp = d.get('tool_input') if isinstance(d, dict) else getattr(d, 'tool_input', None)
        names.append(f"{name or '?'} {_input_head(inp)}".strip())
    more = f" (+{len(items) - 6} more)" if len(items) > 6 else ''
    return ', '.join(n for n in names if n) + more


def _input_head(inp):
    if not isinstance(inp, dict):
        return _head(inp, 100)
    for k in ('file_path', 'command', 'path', 'pattern', 'query', 'url', 'description'):
        if inp.get(k):
            return _head(inp[k], 100)
    return ''
