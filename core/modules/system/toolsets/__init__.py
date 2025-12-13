# core/modules/system/toolsets/__init__.py
"""
Toolsets module - manages AI tool/function sets.

Usage:
    from core.modules.system.toolsets import get_toolset, get_all_toolsets, toolset_exists
"""

from .toolset_manager import toolset_manager

# Convenience functions (mirror the prompts pattern)
def get_toolset(name: str) -> dict:
    """Get a toolset by name."""
    return toolset_manager.get_toolset(name)

def get_toolset_functions(name: str) -> list:
    """Get function list for a toolset."""
    return toolset_manager.get_toolset_functions(name)

def get_all_toolsets() -> dict:
    """Get all toolsets."""
    return toolset_manager.get_all_toolsets()

def get_toolset_names() -> list:
    """Get list of toolset names."""
    return toolset_manager.get_toolset_names()

def toolset_exists(name: str) -> bool:
    """Check if toolset exists."""
    return toolset_manager.toolset_exists(name)

def save_toolset(name: str, functions: list) -> bool:
    """Save or update a toolset."""
    return toolset_manager.save_toolset(name, functions)

def delete_toolset(name: str) -> bool:
    """Delete a toolset."""
    return toolset_manager.delete_toolset(name)

def reload():
    """Reload toolsets from disk."""
    toolset_manager.reload()