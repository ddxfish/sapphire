# core/agents/manager.py — Agent lifecycle manager with type registry
import logging
import uuid
import threading

from core.event_bus import publish, Events

logger = logging.getLogger(__name__)

# Default names if an agent type doesn't specify its own
DEFAULT_NAMES = ['Alpha', 'Bravo', 'Charlie', 'Delta', 'Echo']


class AgentManager:
    """Manages a pool of background agent workers with a pluggable type registry."""

    def __init__(self, max_concurrent=3):
        self.max_concurrent = max_concurrent
        self._agents = {}  # id -> BaseWorker
        self._types = {}   # type_key -> {display_name, spawn_args, factory, names}
        self._lock = threading.Lock()
        self._name_counters = {}  # type_key -> int

    # --- Type registry ---

    def register_type(self, type_key, display_name, factory, spawn_args=None, names=None):
        """Register an agent type.

        Args:
            type_key: Unique key like 'llm' or 'claude_code'
            display_name: Human-readable name like 'LLM Agent' or 'Code (Claude Code)'
            factory: callable(agent_id, name, mission, chat_name, on_complete, **kwargs) -> BaseWorker
            spawn_args: dict of extra args beyond 'mission' this type accepts
                        e.g. {'project_name': {'type': 'string', 'description': '...', 'required': False}}
            names: list of names for this type (defaults to NATO phonetic)
        """
        self._types[type_key] = {
            'display_name': display_name,
            'factory': factory,
            'spawn_args': spawn_args or {},
            'names': names or DEFAULT_NAMES,
        }
        self._name_counters[type_key] = 0
        logger.info(f"Agent type registered: {type_key} ({display_name})")

    def unregister_type(self, type_key):
        """Unregister an agent type. Running agents of this type are not affected."""
        self._types.pop(type_key, None)
        self._name_counters.pop(type_key, None)
        logger.info(f"Agent type unregistered: {type_key}")

    def get_types(self):
        """Return registered types with their metadata (no factory)."""
        return {
            key: {
                'display_name': info['display_name'],
                'spawn_args': info['spawn_args'],
            }
            for key, info in self._types.items()
        }

    # --- Lifecycle ---

    def _next_name(self, type_key):
        names = self._types.get(type_key, {}).get('names', DEFAULT_NAMES)
        # Reset counter when no agents of this type are active
        active_of_type = any(
            a.status == 'running' for a in self._agents.values()
            if hasattr(a, '_agent_type') and a._agent_type == type_key
        )
        if not active_of_type:
            self._name_counters[type_key] = 0
        idx = self._name_counters.get(type_key, 0)
        name = names[idx % len(names)]
        self._name_counters[type_key] = idx + 1
        return name

    def _active_count(self):
        return sum(1 for a in self._agents.values() if a.status == 'running')

    def spawn(self, agent_type, mission, chat_name='', **kwargs) -> dict:
        """Spawn a new agent. Returns {id, name} or {error}."""
        type_info = self._types.get(agent_type)
        if not type_info:
            available = ', '.join(self._types.keys()) or 'none'
            return {'error': f"Unknown agent type '{agent_type}'. Available: {available}"}

        with self._lock:
            if self._active_count() >= self.max_concurrent:
                return {'error': f'Agent limit reached ({self.max_concurrent}). Dismiss or wait for an agent to finish.'}

            agent_id = uuid.uuid4().hex[:8]
            name = self._next_name(agent_type)

        worker = type_info['factory'](
            agent_id=agent_id,
            name=name,
            mission=mission,
            chat_name=chat_name,
            on_complete=self._check_batch_complete,
            **kwargs
        )
        worker._agent_type = agent_type

        with self._lock:
            self._agents[agent_id] = worker

        worker.start()

        publish(Events.AGENT_SPAWNED, {
            'id': agent_id,
            'name': name,
            'mission': mission,
            'chat_name': chat_name,
            'agent_type': agent_type,
        })

        logger.info(f"Agent {name} ({agent_id}) spawned: type={agent_type}, mission={mission[:80]}")
        return {'id': agent_id, 'name': name}

    def check_all(self, chat_name='') -> list:
        """Return status of agents, optionally filtered by chat."""
        with self._lock:
            agents = self._agents.values()
            if chat_name:
                agents = [a for a in agents if a.chat_name == chat_name]
            return [a.to_dict() for a in agents]

    def recall(self, agent_id) -> dict:
        """Get an agent's report."""
        with self._lock:
            agent = self._agents.get(agent_id)
        if not agent:
            return {'error': f'Agent {agent_id} not found.'}
        if agent.status == 'running':
            return {'name': agent.name, 'status': 'running', 'result': 'Agent is still running.'}
        return {
            'name': agent.name,
            'status': agent.status,
            'result': agent.result or agent.error or 'No result.',
            'elapsed': agent.elapsed,
            'tool_log': agent.tool_log,
        }

    def dismiss(self, agent_id) -> dict:
        """Cancel/cleanup an agent."""
        with self._lock:
            agent = self._agents.get(agent_id)
            if not agent:
                return {'error': f'Agent {agent_id} not found.'}
            if agent.status == 'running':
                agent.cancel()
            self._agents.pop(agent_id, None)

        publish(Events.AGENT_DISMISSED, {'id': agent_id, 'name': agent.name})
        logger.info(f"Agent {agent.name} ({agent_id}) dismissed")
        return {'name': agent.name, 'status': 'dismissed', 'last_result': agent.result}

    def _check_batch_complete(self, agent_id, chat_name):
        """Called when an agent finishes. If all agents for this chat are done, publish batch report."""
        with self._lock:
            chat_agents = [a for a in self._agents.values() if a.chat_name == chat_name]
            if not chat_agents:
                return
            if any(a.status == 'running' for a in chat_agents):
                return

            report_lines = []
            for a in chat_agents:
                icon = '\u2713' if a.status == 'done' else '\u2717'
                body = a.result or a.error or 'No result.'
                report_lines.append(f"{icon} Agent {a.name} ({a.status}, {a.elapsed}s):\n{body}")

            report = "[AGENT REPORT \u2014 All agents complete]\n\n" + "\n\n".join(report_lines)

            dismissed_ids = []
            for a in chat_agents:
                dismissed_ids.append((a.id, a.name))
                self._agents.pop(a.id, None)

            # Publish events inside lock to prevent new agents from racing
            for aid, aname in dismissed_ids:
                publish(Events.AGENT_DISMISSED, {'id': aid, 'name': aname})

            publish(Events.AGENT_BATCH_COMPLETE, {
                'chat_name': chat_name,
                'report': report,
                'agent_count': len(dismissed_ids),
            })

        logger.info(f"Agent batch complete for chat '{chat_name}': {len(dismissed_ids)} agents reported")

    def shutdown(self, timeout=10):
        """Cancel all running agents and wait for threads to finish. Called on app shutdown."""
        with self._lock:
            running = [(aid, a) for aid, a in self._agents.items() if a.status == 'running']
        for aid, agent in running:
            logger.info(f"Shutting down agent {agent.name} ({aid})")
            agent.cancel()
        for aid, agent in running:
            if agent._thread and agent._thread.is_alive():
                agent._thread.join(timeout=timeout)
        with self._lock:
            self._agents.clear()
