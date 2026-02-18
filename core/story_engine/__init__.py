# core/story_engine/__init__.py
"""
Story Engine - Per-chat state management for games, simulations, and interactive stories.

Provides:
- StoryEngine: Main class for state management with SQLite persistence
- Story tools: AI-callable functions (get_state, set_state, roll_dice, etc.)
- Presets: JSON templates for different story types
"""

from .engine import StoryEngine
from .tools import TOOLS, STORY_TOOL_NAMES, execute

# Backward compat aliases
StateEngine = StoryEngine
STATE_TOOL_NAMES = STORY_TOOL_NAMES

__all__ = ['StoryEngine', 'TOOLS', 'STORY_TOOL_NAMES', 'execute',
           'StateEngine', 'STATE_TOOL_NAMES']