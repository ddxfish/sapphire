# core/modules/continuity/__init__.py
"""
Continuity - Scheduled autonomous AI tasks.
Wake the AI on a schedule to run prompts, use tools, and speak.
"""

from .scheduler import ContinuityScheduler
from .executor import ContinuityExecutor

__all__ = ['ContinuityScheduler', 'ContinuityExecutor']