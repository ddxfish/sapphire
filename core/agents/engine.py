# core/agents/engine.py - the agent engine (tmp/agents-v2.md §3.5)
#
# One AgentManager per Sapphire (system.agent_manager). It knows the kinds the
# plugins declared (core/agents/registry.py), makes an Agent per spawn bound to
# a chat, keeps a row per agent, answers the four tools (functions/agents.py)
# and the routes, and delivers every report and question into the chat
# through the inbox (core/chat/inbox.py) - no browser involved.
#
# Privacy, in one place (_private): a spawn, a say, an answer or a wake is
# private when the caller's turn is private, when the agent's chat is private
# NOW, when the agent was spawned private, or when nothing can be read. A
# private action refuses a cloud kind. Lookups are chat-local: an agent is
# found only from the chat it belongs to, and anything else reads exactly like
# not-found (scout C, 2026-10-06).
#
# Rows: user/plugin_state/agents.json holds metadata only (id, name, kind,
# plugin, chat, status, started, ended, resume_token, privacy). Mission,
# pending question and the last report live in plugin_chat_data under the
# agent's chat - encrypted when the chat is vaulted, gone when it is deleted,
# following a rename.
import importlib
import json
import logging
import sys
import threading
import time
import uuid

from core.event_bus import publish, Events

logger = logging.getLogger(__name__)

# The engine's state is CORE state: its own plugin_state file and its own
# plugin_chat_data owner, in a namespace no plugin card can purge. It used to
# squat in the `agents` plugin's namespace - "Purge data" on that card wiped
# every kind's missions, options and reports (Claude Code sessions included)
# and the engine then wrote its in-memory rows straight back (chaos scout,
# 2026-10-07). Not `agents-*`: the purge sweeps a plugin's `{name}-*` siblings.
STORE = 'core-agents'            # plugin_state file and plugin_chat_data owner
LEGACY_STORE = 'agents'          # where rows and content lived before 2026-10-08; read through, migrated once
SETTINGS_PLUGIN = 'agents'       # the Agents plugin's settings (max concurrent, roster) stay its own
ROWS_KEEP = 60                   # rows kept; the oldest terminal ones go first
ROWS_TTL_S = 7 * 86400           # a terminal row older than this is forgotten
RECENT_S = 86400                 # "recently finished elsewhere" window
EVENT_THROTTLE_S = 1.0           # agent_event at most this often per agent
DEFAULT_NAMES = ['Alpha', 'Bravo', 'Charlie', 'Delta', 'Echo']
LIVE = ('pending', 'running', 'waiting', 'idle')
TERMINAL = ('done', 'failed', 'stopped', 'lost')   # mirrors core.agents.base.TERMINAL (imported lazily there)
KIND_ENUM = 10                   # kinds named as an enum in the tool schema when this few


class AgentError(Exception):
    """A message fit to show her as it is."""


# --- doors to core (patched in tests) -------------------------------------------

def _system():
    try:
        from core.api_fastapi import get_system
        return get_system()
    except Exception:
        return None


def _sm():
    s = _system()
    return getattr(getattr(s, 'llm_chat', None), 'session_manager', None) if s else None


def _store():
    from core.plugin_loader import plugin_loader
    return plugin_loader.get_plugin_state(STORE)


def _legacy_store():
    from core.plugin_loader import plugin_loader
    return plugin_loader.get_plugin_state(LEGACY_STORE)


def _registry():
    from core.agents import registry
    return registry


def _plugin_info(name):
    from core.plugin_loader import plugin_loader
    return plugin_loader.get_plugin_info(name)


def _settings():
    try:
        from core.plugin_loader import plugin_loader
        return plugin_loader.get_plugin_settings(SETTINGS_PLUGIN) or {}
    except Exception:
        return {}


def _now():
    return time.time()


def retell():
    """The agent tools' descriptions name the kinds (functions/agents.py
    get_tools). Rebuild them when a kind comes or goes. Never raises."""
    try:
        fm = _system().llm_chat.function_manager
        fm.refresh_core_tool_descriptions()
    except Exception as e:
        logger.debug(f"[AGENTS] tool descriptions not rebuilt: {e}")


class AgentManager:
    """The engine. Constructed once in sapphire.py as system.agent_manager."""

    def __init__(self, max_concurrent=3):
        self.max_concurrent = max_concurrent
        self._agents = {}            # id -> Agent (live objects)
        self._lock = threading.RLock()
        self._modules = {}           # kind -> imported module
        self._modules_gen = -1
        self._name_counters = {}
        self._last_event_at = {}     # agent id -> monotonic
        self._rows = None            # loaded on first use
        self._shutting_down = False  # finished() rests conversational sessions instead of stopping them

    # --- kinds ------------------------------------------------------------------

    def kinds(self):
        """Every kind: the usable ones, and the ones whose plugin is off."""
        ready = {k['kind']: k for k in _registry().list_kinds()}
        out = [dict(k, available=True, note='') for k in ready.values()]
        try:
            from core.plugin_loader import plugin_loader
            for info in plugin_loader.get_all_plugin_info():
                manifest = (info or {}).get('manifest') or {}
                for kd in (manifest.get('capabilities', {}).get('agents') or []):
                    kid = str(kd.get('kind') or '').strip().lower() if isinstance(kd, dict) else ''
                    if not kid or kid in ready or any(o['kind'] == kid for o in out):
                        continue
                    title = manifest.get('short_display_name') or info.get('name')
                    out.append({'kind': kid, 'label': str(kd.get('label') or kid), 'icon': str(kd.get('icon') or ''),
                                'description': str(kd.get('description') or ''), 'cloud': kd.get('cloud') is not False,
                                'conversational': kd.get('conversational') is True, 'spawn_schema': [],
                                'names': [], 'plugin_name': info.get('name'), 'available': False,
                                'note': f"Enable the {title} plugin to use this"})
        except Exception as e:
            logger.debug(f"[AGENTS] could not list kinds of disabled plugins: {e}")
        return sorted(out, key=lambda k: (not k['available'], k['label'].lower()))

    def kind(self, kind_id):
        return _registry().get_kind(kind_id)

    def _module(self, spec):
        """The kind's module, imported from its plugin, re-imported when the
        registry changed (a plugin came, went, or reloaded). The plugin must
        be enabled and loaded on every call."""
        info = _plugin_info(spec['plugin_name'])
        if not info or not (info.get('enabled') and info.get('loaded')):
            raise AgentError(f"The {spec['plugin_name']} plugin is off, so its '{spec['kind']}' agents cannot run.")
        with self._lock:
            gen = _registry().generation()
            if gen != self._modules_gen:
                for mod in self._modules.values():
                    sys.modules.pop(mod.__name__, None)
                self._modules.clear()
                self._modules_gen = gen
            mod = self._modules.get(spec['kind'])
            if mod is None:
                dotted = spec['module'][:-3].replace('/', '.')
                try:
                    mod = importlib.import_module(f"plugins.{spec['plugin_name']}.{dotted}")
                except Exception as e:
                    logger.error(f"[AGENTS] kind '{spec['kind']}' failed to import: {e}", exc_info=True)
                    raise AgentError(f"The '{spec['kind']}' kind failed to load: {e}")
                if not isinstance(getattr(mod, 'Agent', None), type):
                    raise AgentError(f"The '{spec['kind']}' kind's module has no Agent class.")
                self._modules[spec['kind']] = mod
        return mod

    # --- rows -----------------------------------------------------------------------

    def _load_rows(self):
        if self._rows is not None:
            return self._rows
        try:
            rows = _store().get('rows', []) or []
        except Exception as e:
            logger.warning(f"[AGENTS] rows unreadable, starting empty: {e}")
            rows = []
        changed = False
        if not rows:
            # one-time move out of the Agents plugin's namespace (2026-10-08)
            try:
                old = _legacy_store()
                legacy = old.get('rows', []) or []
                if legacy:
                    rows, changed = list(legacy), True
                    old.save('rows', [])
                    logger.info(f"[AGENTS] {len(rows)} row(s) moved to the core store")
            except Exception as e:
                logger.debug(f"[AGENTS] legacy rows not read: {e}")
        for r in rows:
            # a restart: nothing that was live survived. A conversational kind
            # with a session to pick up rests; the rest are lost.
            if r.get('status') in LIVE:
                spec = _registry().get_kind(r.get('kind'))
                conv = bool(spec and spec.get('conversational')) if spec else bool(r.get('conversational'))
                r['status'] = 'resting' if (conv and r.get('resume_token')) else 'lost'
                r['ended'] = r.get('ended') or _now()
                changed = True
        self._rows = rows
        if changed:
            self._save_rows()
        return rows

    def _save_rows(self):
        rows = self._rows or []
        cut = _now() - ROWS_TTL_S
        keep = [r for r in rows if not (r.get('status') in ('done', 'failed', 'stopped', 'lost')
                                        and (r.get('ended') or 0) < cut)]
        if len(keep) > ROWS_KEEP:
            terminal = [r for r in keep if r.get('status') in ('done', 'failed', 'stopped', 'lost')]
            terminal.sort(key=lambda r: r.get('ended') or 0)
            drop = {id(r) for r in terminal[:len(keep) - ROWS_KEEP]}
            keep = [r for r in keep if id(r) not in drop]
        self._rows = keep
        try:
            _store().save('rows', keep)
        except Exception as e:
            logger.warning(f"[AGENTS] rows not saved: {e}")

    def _row(self, agent_id):
        for r in self._load_rows():
            if r.get('id') == agent_id:
                return r
        return None

    def _row_update(self, agent_id, **fields):
        # under the lock (RLock): _save_rows rebinds the list, and an unlocked
        # update from an agent's thread racing a spawn lost the new row - and
        # with it the resume token (race + day-ruiner scouts, 2026-10-07)
        with self._lock:
            r = self._row(agent_id)
            if r is not None:
                r.update(fields)
                self._save_rows()
            return r

    def _content_put(self, chat, agent_id, **fields):
        """Mission, question, report: under the chat, not in the row."""
        sm = _sm()
        if sm is None or not chat:
            return
        try:
            cur = (sm.plugin_data_get(STORE, chat, f'agent:{agent_id}', default={})
                   or sm.plugin_data_get(LEGACY_STORE, chat, f'agent:{agent_id}', default={}) or {})
            cur.update(fields)
            sm.plugin_data_put(STORE, chat, f'agent:{agent_id}', cur)
        except Exception as e:
            logger.debug(f"[AGENTS] content for {agent_id} not saved: {e}")

    def _content_get(self, chat, agent_id):
        sm = _sm()
        if sm is None or not chat:
            return {}
        try:
            return (sm.plugin_data_get(STORE, chat, f'agent:{agent_id}', default={})
                    or sm.plugin_data_get(LEGACY_STORE, chat, f'agent:{agent_id}', default={}) or {})
        except Exception:
            return {}

    # --- privacy, in one place -------------------------------------------------------

    @staticmethod
    def _chat_private(chat):
        sm = _sm()
        if sm is None or not chat:
            return False if sm is None else True
        try:
            s = sm.get_settings_for(chat)
        except Exception:
            return True
        return s is None or bool(s.get('private_chat'))

    def _private(self, chat, row=None):
        """True when an action on `chat` must stay local: the caller's turn is
        private, the chat is private now, the agent was spawned private, or
        nothing can be read (fail closed)."""
        try:
            from core.chat.function_manager import scope_private
            cv = bool(scope_private.get())
        except Exception:
            cv = True
        return cv or self._chat_private(chat) or bool(row and row.get('privacy'))

    def _gate(self, spec, chat, row=None, what='dispatched'):
        if self._private(chat, row) and spec.get('cloud', True):
            raise AgentError(f"This chat is private - '{spec['kind']}' agents run through a cloud "
                             f"service and can't be {what} from here.")

    # --- lookups, chat-local ---------------------------------------------------------

    def _live_by_id(self, agent_id):
        return self._agents.get(agent_id)

    def _live_in(self, chat):
        with self._lock:
            return [a for a in self._agents.values() if a.chat == chat]

    def _rows_in(self, chat):
        return [r for r in self._load_rows() if r.get('chat') == chat]

    def _resolve(self, chat, name):
        """(live Agent or None, row) for `name` in `chat` - by name (case
        folded) or id. Anything in another chat is not found."""
        key = str(name or '').strip().lower()
        if not key:
            raise AgentError("Which agent? agent_list names them.")
        live = [a for a in self._live_in(chat) if a.name.lower() == key or a.id == key]
        if live:
            a = live[0]
            return a, self._row(a.id)
        rows = [r for r in self._rows_in(chat) if str(r.get('name', '')).lower() == key or r.get('id') == key]
        if rows:
            rows.sort(key=lambda r: r.get('started') or 0, reverse=True)
            return None, rows[0]
        raise AgentError(f"There is no agent named '{name}' here. agent_list names the ones in this chat.")

    def _next_name(self, spec):
        names = spec.get('names') or DEFAULT_NAMES
        if isinstance(names, str):
            names = [names]
        with self._lock:
            # live agents AND rows still reachable by name (resting ones wake
            # on `say`): a second 'Forge' while Forge rested sent her say to the
            # wrong one, and the counters reset on restart (chaos scout).
            live = {a.name.lower() for a in self._agents.values()}
            live |= {str(r.get('name') or '').lower() for r in self._load_rows()
                     if r.get('status') in LIVE or r.get('status') == 'resting'
                     or (r.get('resume_token') and r.get('conversational'))}
            idx = self._name_counters.get(spec['kind'], 0)
            for i in range(len(names) * 4):
                base = names[(idx + i) % len(names)]
                n = i // len(names)
                cand = base if n == 0 else f"{base}-{n + 1}"
                if cand.lower() not in live:
                    self._name_counters[spec['kind']] = (idx + i + 1) % len(names)
                    return cand
            return f"{names[0]}-{uuid.uuid4().hex[:4]}"

    # --- the carrier --------------------------------------------------------------------

    def install_carrier(self, agent):
        """The stream-brain override for an agent's thread, from its ROW."""
        sm = _sm()
        if sm is None:
            return None
        try:
            from core.chat import stream_brain
            if agent.chat:
                ov = sm.make_agent_override(agent.chat, privacy_required=bool(agent.privacy))
            else:
                ov = sm.make_ephemeral_override(privacy_required=bool(agent.privacy))
            return stream_brain.set_override(ov)
        except Exception as e:
            logger.warning(f"[AGENTS] carrier for {agent.name} not installed: {e}")
            return None

    def release_carrier(self, token):
        if token is None:
            return
        try:
            from core.chat import stream_brain
            stream_brain.reset_override(token)
        except Exception as e:
            logger.debug(f"[AGENTS] carrier release failed: {e}")

    # --- spawn ------------------------------------------------------------------------------

    def spawn(self, kind, mission, chat='', options=None):
        """Make and start an agent. Returns {'id', 'name', 'kind'} or {'error'}."""
        try:
            spec = _registry().get_kind(kind)
            if not spec:
                have = ', '.join(k['kind'] for k in _registry().list_kinds()) or 'none loaded'
                return {'error': f"There is no agent kind '{kind}'. Kinds: {have}."}
            if not isinstance(mission, str):
                mission = json.dumps(mission, ensure_ascii=False)
            mission = (mission or '').strip()
            if not mission:
                return {'error': 'An agent needs a mission.'}
            chat = str(chat or '').strip()
            if not chat:
                # Krem (2026-10-07): no ronin. A chatless lane (a continuity task
                # with no chat) pooled every such agent together and their
                # reports went nowhere (privacy + chaos scouts).
                return {'error': "An agent needs a chat to report into; this lane has none (no ronin agents). "
                                 "Give the task a chat, or do the work in this turn."}
            self._gate(spec, chat)
            mod = self._module(spec)
            ps = _settings()
            try:
                self.max_concurrent = max(1, min(5, int(ps.get('max_concurrent', self.max_concurrent) or 3)))
            except (TypeError, ValueError):
                pass
            with self._lock:
                running = sum(1 for a in self._agents.values() if a.status == 'running')
                if running >= self.max_concurrent:
                    return {'error': f"Agent limit reached ({self.max_concurrent} running). Wait, or stop one."}
                row = {
                    'id': uuid.uuid4().hex[:8], 'name': self._next_name(spec), 'kind': spec['kind'],
                    'plugin': spec['plugin_name'], 'chat': chat, 'status': 'pending',
                    'started': _now(), 'ended': None, 'resume_token': None,
                    'privacy': self._private(chat), 'conversational': spec.get('conversational', False),
                    'mission': mission, 'options': dict(options or {}),
                }
                agent = mod.Agent(row, self)
                self._agents[agent.id] = agent
                agent.status = 'running'
                rows = self._load_rows()
                rows.append({k: v for k, v in row.items() if k not in ('mission', 'options')})
                self._save_rows()
            self._content_put(chat, agent.id, mission=mission, options=dict(options or {}))
            agent.start()
            # ephemeral: live tabs hear it; a tab opened after a vault lock must
            # not get a private chat's name replayed (privacy scout, 2026-10-07)
            publish(Events.AGENT_SPAWNED, {'id': agent.id, 'name': agent.name, 'chat_name': chat,
                                           'agent_type': spec['kind']}, ephemeral=True)
            logger.info(f"Agent {agent.name} ({agent.id}) spawned: kind={spec['kind']} "
                        f"chat={'set' if chat else 'none'} mission {len(mission)} chars")
            return {'id': agent.id, 'name': agent.name, 'kind': spec['kind']}
        except AgentError as e:
            return {'error': str(e)}
        except Exception as e:
            logger.error(f"[AGENTS] spawn({kind}) failed: {e}", exc_info=True)
            return {'error': f"spawn failed: {type(e).__name__}: {e}"}

    def _revive(self, row, text):
        """Wake a resting conversational agent from its row with `text` as the
        next turn. The kind sees resume_token and continues its session."""
        spec = _registry().get_kind(row.get('kind'))
        if not spec:
            raise AgentError(f"The '{row.get('kind')}' kind is not loaded; {row.get('name')} cannot wake.")
        if not spec.get('conversational') or not row.get('resume_token'):
            raise AgentError(f"{row.get('name')} cannot be woken - nothing to resume.")
        self._gate(spec, row.get('chat', ''), row, what='woken')
        mod = self._module(spec)
        content = self._content_get(row.get('chat', ''), row['id'])
        # The agent is rebuilt from its ROW plus the stored options, and `text`
        # is the next TURN, not a new mission: the kind derived its workspace
        # from the mission, so a wake on "fix the tests" moved a project-mode
        # session into an empty ~/claude-workspaces/fix-the-tests (two scouts,
        # 2026-10-07). `context` rode along again on every wake; it is in the
        # session's history already.
        options = {k: v for k, v in (content.get('options') or {}).items() if k != 'context'}
        full = dict(row, mission=text, options=options, wake_text=text)
        with self._lock:
            if self._live_by_id(row['id']) is not None:
                raise AgentError(f"{row.get('name')} is already awake.")
            agent = mod.Agent(full, self)
            self._agents[agent.id] = agent
            agent.status = 'running'
            self._row_update(agent.id, status='running', ended=None)
        agent.start()
        publish(Events.AGENT_SPAWNED, {'id': agent.id, 'name': agent.name, 'chat_name': agent.chat,
                                       'agent_type': spec['kind'], 'woken': True}, ephemeral=True)
        return agent

    # --- what agents call back -----------------------------------------------------------

    def question_out(self, agent, q):
        from core.chat import inbox
        summary = agent.pending_question or {}
        self._row_update(agent.id, status='waiting')
        self._content_put(agent.chat, agent.id, pending_question=summary)
        if not self._hidden(agent.chat):
            publish(Events.AGENT_WAITING, {'id': agent.id, 'name': agent.name, 'chat_name': agent.chat}, ephemeral=True)
        if not agent.chat:
            return None
        text = _question_text(agent, summary)
        # coalesce=False (Krem 2026-10-06): each agent's return is one event from
        # one author and gets her dedicated reply; only a person's typed turns fold.
        # The ticket goes back to the agent: a question answered from the pill
        # (or timed out) before its turn ran is withdrawn, not asked anyway.
        return inbox.tell(agent.chat, text, source=f'agent:{agent.kind}', coalesce=False,
                          header_line=inbox.header(f'Agent {agent.name}', agent.kind, 'asks'))

    def question_withdrawn(self, agent, ticket, why):
        """The question was answered (or given up on) before its turn in the
        chat came: take the item back so she is not asked about a settled
        fork (race + chaos scouts, 2026-10-07)."""
        from core.chat import inbox
        try:
            if ticket and agent.chat and inbox.drop(agent.chat, ticket, why):
                logger.info(f"Agent {agent.name}: queued question withdrawn ({why})")
        except Exception as e:
            logger.debug(f"[AGENTS] question not withdrawn: {e}")

    def report_out(self, agent, text):
        from core.chat import inbox
        self._content_put(agent.chat, agent.id, last_report=text, pending_question=None)
        if not agent.chat or not text:
            return
        agent.chat_heard = True
        spec = _registry().get_kind(agent.kind) or {}
        what = f"reports ({_fmt(agent.elapsed)} in)" if spec.get('conversational') else f"done in {_fmt(agent.elapsed)}"
        try:
            inbox.tell(agent.chat, text, source=f'agent:{agent.kind}', coalesce=False,
                       header_line=inbox.header(f'Agent {agent.name}', agent.kind, what))
        except inbox.InboxRefused as e:
            # only a chat that is GONE refuses a machine's report now (a sealed
            # one holds it until the unlock); nothing is kept anywhere
            logger.warning(f"[AGENTS] {agent.name}: report lost, the chat refused it: {e}")

    def _hidden(self, chat):
        sm = _sm()
        try:
            return sm is not None and sm.is_chat_hidden(chat) is True
        except Exception:
            return True

    def event_out(self, agent, kind):
        now = time.monotonic()
        if kind not in ('ask', 'answer') and now - self._last_event_at.get(agent.id, 0) < EVENT_THROTTLE_S:
            return
        self._last_event_at[agent.id] = now
        if self._hidden(agent.chat):
            return                     # a sealed chat's agent: its name stays off a wire every tab hears
        publish(Events.AGENT_EVENT, {'id': agent.id, 'kind': kind, 'chat_name': agent.chat,
                                     'tool_count': agent.tool_count}, ephemeral=True)

    def finished(self, agent):
        """The agent's thread is done (any terminal status, or a conversational
        kind going to rest)."""
        status = agent.status
        if self._shutting_down and status == 'stopped' and agent.resume_token and self._is_conversational(agent):
            status = 'resting'       # the process is ending, not the session: `say` resumes it after the restart
        with self._lock:
            # Incarnation fence (chaos scout, 2026-10-07): an agent stays in
            # `_agents` until ITS thread ends, so a `say` right after a `stop`
            # cannot revive a second CLI on the same session while the first is
            # still winding down - and a thread that was superseded anyway (a
            # newer object owns the id) writes nothing over the newer row.
            if self._agents.get(agent.id) is not agent:
                logger.info(f"Agent {agent.name} ({agent.id}): an earlier incarnation ended ({status}); the row is the newer one's")
                return
            self._row_update(agent.id, status=status, ended=_now(), resume_token=agent.resume_token)
            if status not in LIVE:
                self._agents.pop(agent.id, None)
        if status == 'failed' and not getattr(agent, '_reported', False):
            # the kind never got to report (its setup raised, the SDK is missing,
            # a bad setting): she said "it reports back when done" - say so
            # (chaos scout, 2026-10-07). An SSE event alone reaches only the UI.
            try:
                self.report_out(agent, f"[{agent.name} failed: {_short(agent.error or 'unknown error', 300)}]")
            except Exception as e:
                logger.debug(f"[AGENTS] failure report for {agent.name} not sent: {e}")
        elif status == 'done' and not agent.chat_heard and agent.chat:
            # it ended without a word (the llm kind's tool loop ran out, the
            # context overflowed, an empty reply): she said "it reports back when
            # done" and nothing was coming (chaos scout, 2026-10-07)
            why = agent.warning or 'it produced no answer'
            try:
                self.report_out(agent, f"[{agent.name} finished without an answer: {_short(why, 300)}]")
            except Exception as e:
                logger.debug(f"[AGENTS] empty-finish notice for {agent.name} not sent: {e}")
        if not self._hidden(agent.chat):
            publish(Events.AGENT_COMPLETED, {
                'id': agent.id, 'name': agent.name, 'status': status, 'elapsed': agent.elapsed,
                'warning': agent.warning, 'error': (agent.error or '')[:200] or None,
                'agent_type': agent.kind, 'chat_name': agent.chat,
            }, ephemeral=True)
        logger.info(f"Agent {agent.name} ({agent.id}) {status} after {agent.elapsed}s")

    def resting(self, agent):
        """A conversational kind went idle past its timeout: its thread ends,
        its row rests with the token to pick up."""
        agent.status = 'resting'

    # --- a chat renamed or deleted -----------------------------------------------------------

    def chat_renamed(self, old, new):
        """Rows and live agents follow the chat (scout C M1: a resting row bound
        to a dead name would otherwise report into a NEW chat of that name)."""
        old, new = str(old or '').strip(), str(new or '').strip()
        if not old or not new or old == new:
            return
        with self._lock:
            for a in self._agents.values():
                if a.chat == old:
                    a.chat = new
            changed = False
            for r in self._load_rows():
                if r.get('chat') == old:
                    r['chat'] = new
                    changed = True
            if changed:
                self._save_rows()

    def chat_deleted(self, chat):
        """Stop the chat's live agents and forget its rows (their content went
        with the chat's plugin_chat_data)."""
        chat = str(chat or '').strip()
        if not chat:
            return
        for a in list(self._live_in(chat)):
            self.dismiss(a.id)
        with self._lock:
            rows = self._load_rows()
            keep = [r for r in rows if r.get('chat') != chat]
            if len(keep) != len(rows):
                self._rows = keep
                self._save_rows()

    # --- compat: routes, shutdown, old plugins --------------------------------------------

    def check_all(self, chat_name=None):
        """LIVE agents as dicts. None = every chat (UI); '' = chatless only
        (the ephemeral lane); a name = that chat."""
        with self._lock:
            agents = list(self._agents.values())
        if chat_name is not None:
            agents = [a for a in agents if a.chat == chat_name]
        return [a.to_dict() for a in agents]

    def recall(self, agent_id):
        with self._lock:
            a = self._agents.get(agent_id)
        if a is None:
            row = self._row(agent_id)
            if row is None:
                return {'error': f'Agent {agent_id} not found.'}
            content = self._content_get(row.get('chat', ''), agent_id)
            return {'name': row.get('name'), 'status': row.get('status'),
                    'result': content.get('last_report') or 'No result.', 'elapsed': 0, 'tool_log': []}
        if a.status in ('running', 'waiting'):
            return {'name': a.name, 'status': a.status, 'result': 'Agent is still running.'}
        return {'name': a.name, 'status': a.status, 'result': a.result or a.error or 'No result.',
                'elapsed': a.elapsed, 'tool_log': [e['data'] for e in a.transcript(0) if e['kind'] == 'tool_use']}

    def dismiss(self, agent_id):
        with self._lock:
            a = self._agents.get(agent_id)
            # a finished object still lingering (its thread is gone) leaves now;
            # a LIVE one stays registered until finished() - its thread is still
            # winding the CLI down, and `say` must see it (incarnation fence)
            if a is not None and a.status not in LIVE:
                self._agents.pop(agent_id, None)
        if a is None:
            row = self._row(agent_id)
            if row is None:
                return {'error': f'Agent {agent_id} not found.'}
            self._row_update(agent_id, status='stopped', ended=_now())
            publish(Events.AGENT_DISMISSED, {'id': agent_id, 'name': row.get('name')})
            return {'name': row.get('name'), 'status': 'dismissed', 'last_result': None}
        if a.status in LIVE:
            try:
                a.stop()
            except Exception as e:
                logger.warning(f"[AGENTS] {a.name}.stop() raised: {e}")
        self._row_update(agent_id, status='stopped', ended=_now())
        publish(Events.AGENT_DISMISSED, {'id': agent_id, 'name': a.name})
        logger.info(f"Agent {a.name} ({agent_id}) dismissed")
        return {'name': a.name, 'status': 'dismissed', 'last_result': a.result}

    def _is_conversational(self, agent):
        spec = _registry().get_kind(agent.kind)
        if spec is not None:
            return bool(spec.get('conversational'))
        return bool((self._row(agent.id) or {}).get('conversational'))

    def shutdown(self, timeout=10):
        self._shutting_down = True
        with self._lock:
            live = [(aid, a) for aid, a in self._agents.items() if a.status in LIVE]
        for aid, a in live:
            logger.info(f"Shutting down agent {a.name} ({aid})")
            try:
                a.stop()
            except Exception as e:
                logger.warning(f"[AGENTS] {a.name}.stop() raised at shutdown: {e}")
            # the row says so now, whether or not the thread gets to finished()
            # before the process ends - a restart must not read it as 'running'.
            # A conversational kind with a session RESTS: `say` resumes it after
            # the restart (Krem: sessions persist). 'stopped' here orphaned every
            # Claude Code session on a graceful restart (day-ruiner, 2026-10-07).
            status = 'resting' if (a.resume_token and self._is_conversational(a)) else 'stopped'
            self._row_update(aid, status=status, ended=_now(), resume_token=a.resume_token)
        for aid, a in live:
            if a._thread and a._thread.is_alive():
                a._thread.join(timeout=timeout)
        with self._lock:
            self._agents.clear()

    def get_types(self):
        """Old name for the kinds, as a dict (kept for plugins and tests)."""
        return {k['kind']: {'display_name': k['label'], 'spawn_args': {f['key']: f for f in k.get('spawn_schema', [])}}
                for k in _registry().list_kinds()}

    def register_type(self, type_key, display_name, factory, spawn_args=None, names=None):
        """The pre-2026-10 way plugins added agent types. Gone: kinds are
        declared in plugin.json (capabilities.agents). Logged, ignored."""
        logger.warning(f"[AGENTS] register_type('{type_key}') is retired - declare the kind in "
                       f"plugin.json capabilities.agents (see docs/plugin-author/agents.md). Ignored.")

    def unregister_type(self, type_key):
        pass

    # --- the four tools -----------------------------------------------------------------------

    def list_text(self, chat):
        lines = []
        kinds = self.kinds()
        if kinds:
            lines.append('Kinds you can spawn:')
            for k in kinds:
                cloud = ' (cloud - not from a private chat)' if k.get('cloud') else ''
                if k['available']:
                    lines.append(f"  - {k['kind']}: {k.get('description') or k['label']}{cloud}")
                else:
                    lines.append(f"  - {k['kind']}: {k['note']}")
        else:
            lines.append('No agent kinds are loaded. Enable the Agents or Claude Code plugin in Settings > Plugins.')
        live = self._live_in(chat)
        rested = [r for r in self._rows_in(chat) if r.get('status') in ('resting',)
                  and not any(a.id == r['id'] for a in live)]
        if live or rested:
            lines.append('\nAgents in this chat:')
            for a in live:
                q = a.pending_question
                line = f"  - {a.name} ({a.kind}) — {a.progress()}"
                if q:
                    line += f"\n      asks: {_q_one_line(q)}"
                lines.append(line)
            for r in rested:
                lines.append(f"  - {r['name']} ({r['kind']}) — resting; agent_action({r['name']!r}, 'say', ...) wakes it")
        else:
            lines.append('\nNo agents in this chat right now.')
        try:
            from core.chat import inbox
            n = inbox.depth(chat) if chat else 0
            if n:
                qn, rn = inbox.pending_counts(chat)
                lines.append(f"\nInbox: {n} waiting ({qn} question{'s' if qn != 1 else ''}, {rn} report{'s' if rn != 1 else ''}).")
        except Exception:
            pass
        elsewhere = self._recent_elsewhere(chat)
        if elsewhere:
            lines.append('\nRecently finished elsewhere: ' + ' · '.join(elsewhere))
        return '\n'.join(lines), True

    def _recent_elsewhere(self, chat):
        """Public rows from other chats, last 24 h: name, kind, status only.
        Information flows into a private chat, never out of one."""
        out = []
        cut = _now() - RECENT_S
        for r in self._load_rows():
            if r.get('chat') == chat or (r.get('ended') or 0) < cut:
                continue
            if r.get('privacy') or self._chat_private(r.get('chat', '')):
                continue
            sm = _sm()
            try:
                if sm is not None and sm.is_chat_hidden(r.get('chat', '')):
                    continue
            except Exception:
                continue
            # name, kind, status - never the other chat's NAME (docs/AGENTS.md
            # promises as much, and this text goes to this chat's provider)
            out.append(f"{r.get('name')} ({r.get('kind')}) {r.get('status')}")
        return out[:8]

    def peek_text(self, chat, name, what=''):
        agent, row = self._resolve(chat, name)
        what = str(what or '').strip().lower()
        content = self._content_get(chat, (agent or row)['id'] if agent is None else agent.id)
        if what == 'report':
            text = (agent.result if agent and agent.result else None) or content.get('last_report')
            return (text or f"{(agent or row)['name'] if agent is None else agent.name} has not reported yet."), True
        if agent is None:
            lines = [f"{row['name']} ({row['kind']}) — {row.get('status')}"]
            if content.get('mission'):
                lines.append(f"Mission: {_short(content['mission'], 200)}")
            if row.get('status') == 'resting':
                lines.append(f"Resting; agent_action({row['name']!r}, 'say', '...') wakes it.")
            if content.get('last_report'):
                lines.append(f"Last report: {_short(content['last_report'], 300)} (agent_peek(..., 'report') for all of it)")
            return '\n'.join(lines), True
        lines = [f"{agent.name} ({agent.kind}) — {agent.progress()}", f"Mission: {_short(agent.mission, 200)}"]
        q = agent.pending_question
        if q:
            lines.append('Asks:\n' + _question_text(agent, q, indent='  '))
            lines.append(f"Answer with agent_action({agent.name!r}, 'answer', '<a letter, a label, or text>').")
        n = 60 if what == 'transcript' else 12
        tr = agent.transcript(n)
        if tr:
            lines.append('Transcript (latest last):')
            for e in tr:
                lines.append('  ' + _event_line(e))
        if agent.result:
            lines.append(f"Last report: {_short(agent.result, 300)} (agent_peek(..., 'report') for all of it)")
        return '\n'.join(lines), True

    def spawn_text(self, chat, kind, mission, options):
        kind = str(kind or '').strip().lower()
        if not kind:
            return self.list_text(chat)
        spec = _registry().get_kind(kind)
        if not spec:
            return (f"There is no agent kind '{kind}'.\n" + self.list_text(chat)[0]), False
        if not str(mission or '').strip():
            return self._kind_screen(spec), True
        opts = options if isinstance(options, dict) else {}
        if isinstance(options, str) and options.strip():
            try:
                opts = json.loads(options)
                if not isinstance(opts, dict):
                    opts = {}
            except ValueError:
                return f"options must be a JSON object, e.g. {_example_options(spec)}", False
        bad = [k for k in opts if k not in {f['key'] for f in spec.get('spawn_schema', [])} | {'name', 'model', 'context'}]
        if bad:
            return f"'{kind}' has no option(s) {', '.join(bad)}.\n{self._kind_screen(spec)}", False
        r = self.spawn(kind, mission, chat=chat, options=opts)
        if 'error' in r:
            return r['error'], False
        return (f"{r['name']} dispatched ({spec['label']}). It reports into this chat when it is done, "
                f"and asks here if it needs you. agent_peek({r['name']!r}) looks in on it."), True

    def _kind_screen(self, spec):
        lines = [f"{spec['kind']} — {spec.get('description') or spec['label']}"
                 + (' · cloud (not from a private chat)' if spec.get('cloud') else '')
                 + (' · conversational: agent_action(name, \'say\', ...) continues it' if spec.get('conversational') else '')]
        lines.append("agent_spawn(kind, mission, options). Common options: name (a workspace or session DIRECTORY name - "
                     "not what the agent is called; the engine names agents), model, context (extra material appended to the mission).")
        if spec.get('spawn_schema'):
            lines.append('Its own options:')
            for f in spec['spawn_schema']:
                opts = ''
                if f.get('options'):
                    opts = ' one of ' + ', '.join(str(o.get('value') if isinstance(o, dict) else o) for o in f['options'])
                dflt = f" (default {f['default']!r})" if f.get('default') not in (None, '') else ''
                lines.append(f"  - {f['key']}{opts}{dflt}: {f.get('help') or f.get('label') or ''}".rstrip(': '))
        lines.append(f"Example: agent_spawn({spec['kind']!r}, 'what to do', {_example_options(spec)})")
        return '\n'.join(lines)

    def action_text(self, chat, name, action, value, question=None):
        try:
            return self._action_text(chat, name, action, value, question)
        except AgentError as e:
            return str(e), False

    def _action_text(self, chat, name, action, value, question=None):
        agent, row = self._resolve(chat, name)
        action = str(action or '').strip().lower()
        who = agent.name if agent else row['name']
        if not action:
            acts = self._actions_for(agent, row)
            return f"{who}: " + (' · '.join(f"{a} — {h}" for a, h in acts) if acts else 'nothing to do right now'), True
        if action == 'answer':
            if agent is None:
                return f"{who} is not waiting on anything.", False
            spec = _registry().get_kind(agent.kind) or {'kind': agent.kind, 'cloud': True}
            self._gate(spec, chat, row, what='answered')
            if value is None or str(value).strip() == '':
                return "answer needs a value: a letter (a/b), an option label, or your words.", False
            return agent.answer(value if isinstance(value, (dict, list)) else str(value), question_id=question)
        if action == 'say':
            text = str(value or '').strip()
            if not text:
                return "say needs the text of the follow-up turn.", False
            if agent is None:
                # resting, or stopped/failed with a session to pick up (plan §12:
                # `say` may try --resume); the whole check-and-wake is one critical
                # section so two says at once can't resume the same session twice
                with self._lock:
                    if self._live_by_id(row.get('id')) is not None:
                        return f"{who} is already awake.", False
                    if not row.get('resume_token'):
                        return f"{who} is {row.get('status')}; nothing to say to.", False
                    a = self._revive(row, text)
                return f"{a.name} woke and took your message.", True
            spec = _registry().get_kind(agent.kind) or {'kind': agent.kind, 'cloud': True}
            self._gate(spec, chat, row, what='spoken to')
            if agent.status in ('running', 'waiting'):
                return (f"{who} is still working ({agent.progress()}) — wait, answer its question, or stop it.", False)
            if agent.status in TERMINAL:
                # stopped (or failed) but its thread has not ended yet: waking the
                # session now would run two CLIs on it (chaos scout, 2026-10-07)
                return f"{who} is still winding down — a moment, then say it again.", False
            return agent.say(text)
        if action == 'stop':
            if agent is None:
                self._row_update(row['id'], status='stopped', ended=_now())
                return f"{who} is stopped (it was {row.get('status')}).", True
            r = self.dismiss(agent.id)
            return (r.get('error') or f"{who} stopped."), 'error' not in r
        acts = self._actions_for(agent, row)
        return f"'{action}' is not something {who} does. " + ' · '.join(a for a, _ in acts), False

    def _actions_for(self, agent, row):
        acts = []
        if agent is not None:
            if agent.pending_question:
                acts.append(('answer <value>', 'resolve its pending question'))
            if agent.status in ('idle',):
                acts.append(('say <text>', 'a follow-up turn'))
            acts.append(('stop', 'interrupt and end it'))
        elif row.get('status') == 'resting':
            acts.append(('say <text>', 'wake it with a follow-up turn'))
            acts.append(('stop', 'forget it'))
        elif row.get('resume_token') and row.get('status') in ('stopped', 'failed'):
            acts.append(('say <text>', f"wake it ({row.get('status')}, its session is still there)"))
        return acts


# --- text helpers ------------------------------------------------------------------------

def _fmt(s):
    s = int(s or 0)
    return f"{s}s" if s < 60 else f"{s // 60}m{s % 60:02d}s"


def _short(text, n):
    t = ' '.join(str(text or '').split())
    return t if len(t) <= n else t[:n - 1] + '…'


def _q_one_line(q):
    if q.get('questions'):
        qs = q['questions']
        first = qs[0]
        opts = ' / '.join(f"({chr(97 + i)}) {o['label']}" for i, o in enumerate(first.get('options') or []))
        more = f" (+{len(qs) - 1} more)" if len(qs) > 1 else ''
        return f"{first.get('question', '')} {opts}{more}".strip()
    return _short(q.get('text'), 160)


def _question_text(agent, q, indent=''):
    """The question as she reads it: one line per question, options lettered,
    each on one line."""
    lines = []
    if q.get('questions'):
        for x in q['questions']:
            head = f"{indent}{x.get('header') + ': ' if x.get('header') else ''}{x.get('question', '')}"
            lines.append(head)
            for i, o in enumerate(x.get('options') or []):
                d = f" — {o['description']}" if o.get('description') else ''
                lines.append(f"{indent}  ({chr(97 + i)}) {o['label']}{d}")
            if x.get('multiSelect'):
                lines.append(f"{indent}  (several may be chosen)")
    else:
        lines.append(f"{indent}{q.get('text', '')}")
    lines.append(f"{indent}Answer with agent_action({agent.name!r}, 'answer', '<a letter, a label, or your words>')"
                 f" — or leave it while you ask the user; it waits.")
    return '\n'.join(lines)


def _event_line(e):
    k, d = e.get('kind'), e.get('data')
    if k == 'tool_use' and isinstance(d, dict):
        return f"{d.get('name', '?')} {_short(_arg_head(d.get('input')), 100)}".rstrip()
    if k == 'tool_result' and isinstance(d, dict):
        return f"  -> {'error' if d.get('is_error') else 'ok'}"
    if k in ('ask', 'answer'):
        return f"{k}: {_short(_q_one_line(d) if isinstance(d, dict) else d, 120)}"
    return f"{k}: {_short(d, 160)}"


def _arg_head(x):
    if isinstance(x, dict):
        for k in ('file_path', 'command', 'path', 'pattern', 'query', 'url'):
            if x.get(k):
                return str(x[k])
        return ''
    return str(x or '')


def _example_options(spec):
    ex = {}
    for f in spec.get('spawn_schema', [])[:2]:
        if f.get('options'):
            o = f['options'][0]
            ex[f['key']] = o.get('value') if isinstance(o, dict) else o
        elif f.get('default') not in (None, ''):
            ex[f['key']] = f['default']
    if not ex:
        ex = {'name': 'my-task'}
    return json.dumps(ex)
