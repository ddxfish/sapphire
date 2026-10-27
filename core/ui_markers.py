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
import threading
import time

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


# --- provenance: a widget marker in a tool result must be one core MADE ------
# A tool's text can carry words from outside - a web page, a search hit, an
# email body. Written as `<!--ASK:{...}-->`, those words would draw her own
# question card in the user's chat (or a GALLERY tile hot-linking a stranger's
# server) while her copy, stripped of markers, never shows it (chaos scout
# 2026-10-27). So the builders (ask_card, attachments, images.gallery,
# open_stage) mint() what they make, and execute_function keeps only minted
# markers. Process-wide, not per-context: a builder may run on a worker thread.
_WIDGET_RE = re.compile(r'<!--(?:GALLERY|FILES|ASK|STAGE):[^\n]*?-->')
_MINT_TTL = 1800           # seconds a made marker stays good: longer than any one tool call
_minted = {}               # marker text -> when it was made
_mint_lock = threading.Lock()


def mint(marker: str) -> str:
    """Record a marker core built; returns it unchanged."""
    if marker:
        now = time.monotonic()
        with _mint_lock:
            _minted[marker] = now
            if len(_minted) > 512:
                for k in [k for k, t in _minted.items() if now - t > _MINT_TTL]:
                    del _minted[k]
    return marker


def only_minted(result):
    """A tool's result with every widget marker core didn't make removed
    (str, or the {'text', 'images'} dict shape)."""
    def clean(text):
        if not isinstance(text, str) or '<!--' not in text:
            return text
        now = time.monotonic()
        with _mint_lock:
            return _WIDGET_RE.sub(lambda m: m.group(0) if now - _minted.get(m.group(0), -1e9) < _MINT_TTL else '', text)
    if isinstance(result, dict) and 'text' in result:
        return {**result, 'text': clean(result['text'])}
    return clean(result)


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
