# core/agents - the agent engine (tmp/agents-v2.md). Kinds are declared by
# plugins (capabilities.agents, core/agents/registry.py), each a subclass of
# core.agents.base.Agent; the engine (core/agents/engine.py) spawns, keeps and
# delivers. The four tools are core: functions/agents.py.
from .engine import AgentManager, AgentError
from .base import Agent

# Module-level singleton - created once in sapphire.py, reachable by plugins during scan
agent_manager = None

__all__ = ['AgentManager', 'AgentError', 'Agent', 'agent_manager']
