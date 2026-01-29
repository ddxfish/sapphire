# core/state_engine/__init__.py
"""
State Engine - Per-chat state management for games, simulations, and interactive stories.

Provides:
- StateEngine: Main class for state management with SQLite persistence
- State tools: AI-callable functions (get_state, set_state, roll_dice, etc.)
- Presets: JSON templates for different story types
"""

from .engine import StateEngine
from .tools import TOOLS, STATE_TOOL_NAMES, execute

__all__ = ['StateEngine', 'TOOLS', 'STATE_TOOL_NAMES', 'execute']