"""core/ui_markers.py - the ONE list of markers that are for the browser, not the model.

A tool result (or a turn's text) can carry a marker the chat renders as a
widget: <<IMG::id>> / <<FILE::id>> (an image or file by id), the GALLERY tile
strip, the FILES player/download row, the ASK question card, the STAGE card (an
app opened beside the chat, functions/stage.py). They stay in
history for the browser and are stripped from every copy the model reads -
live (chat_tool_calling.strip_ui_markers) and on replay (history). Until
2026-10-07 the pattern was copied into both files; a new marker had to be added
twice (and the FILES one nearly wasn't).
"""
import json
import re

UI_MARKER_PATTERN = (r'<<[A-Z]+::[^>]+>>\s*'
                     r'|<!--GALLERY:[\[{][^\n]*[\]}]-->\s*'
                     r'|<!--FILES:\{[^\n]*\}-->\s*'
                     r'|<!--ASK:\{[^\n]*\}-->\s*'
                     r'|<!--STAGE:\{[^\n]*\}-->\s*')
UI_MARKER_RE = re.compile(UI_MARKER_PATTERN)

# History-less lanes (ExecutionContext) keep the wire copy AS the persisted
# copy, so there the browser's markers must survive: only the other <<X::…>>
# markers go. IMG, GALLERY, FILES and ASK ride along as bounded noise within
# the one run; the history read path cleans them before the next run's wire.
UI_MARKER_KEEP_IMG_RE = re.compile(r'<<(?!IMG::)[A-Z]+::[^>]+>>\s*')


def has_marker(text) -> bool:
    return isinstance(text, str) and ('<<' in text or '<!--' in text)


def no_surface() -> str:
    """Why no card can be drawn for THIS turn, or ''. An agent's run, a
    chatless background task and a turn that came in over MCP have no chat
    bubble for a card to land in (seam scout, 2026-10-07; shared by ask_user
    and open_stage since 2026-10-25)."""
    try:
        from core import mcp_server
        if mcp_server.answering.get():
            return "this turn came in over MCP - the client sees text, not a card"
    except Exception:
        pass
    try:
        from core.chat.stream_brain import get_override
        o = get_override() or {}
        if o.get('agent'):
            return "an agent's run has no chat bubble for a card"
        if o.get('ephemeral'):
            return "a chatless background turn has no chat bubble for a card"
    except Exception:
        pass
    try:
        from core.api_fastapi import get_system
        if not (get_system().llm_chat.session_manager._effective_chat_name() or ''):
            return "there is no chat for a card to land in"
    except Exception:
        pass
    return ''


_STAGE_RE = re.compile(r'<!--STAGE:(\{[^\n]*?\})-->')


def stage_cards(text) -> list:
    """The STAGE payloads in a tool result ({app, label, icon} dicts). The
    stream strips every marker from the live tool_end, so these ride it as
    their own field: the tab that ran the turn opens the stage at once."""
    out = []
    for m in _STAGE_RE.finditer(text if isinstance(text, str) else ''):
        try:
            c = json.loads(m.group(1))
        except ValueError:
            continue
        if isinstance(c, dict) and isinstance(c.get('app'), str):
            out.append({k: str(c.get(k) or '')[:64] for k in ('app', 'label', 'icon')})
    return out
