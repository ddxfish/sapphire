# core/tool_context.py — Shared context header for tool output
#
# Tools can prepend this to their results so the LLM always knows
# the current time without burning a separate tool call.
#
# Usage:
#   from core.tool_context import context_header
#   return context_header() + actual_output

from datetime import datetime


def context_header():
    """Return a short context block with current date/time."""
    now = datetime.now()
    return f"── {now.strftime('%A %B %d, %Y  %I:%M %p')} ──\n\n"
