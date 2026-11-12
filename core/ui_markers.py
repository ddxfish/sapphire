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

# A <<X::id>> marker is a short token (an id, `tool:abc`): `[^>]+` also matched
# across newlines, so a web page's `<<AB::` + 300K of text + `>>` counted as
# one "marker" - hidden from her copy, and never capped in the live tool_end
# (tool_end scout, 2026-11-12).
_TOKEN = r'[^>\s]{1,128}'
UI_MARKER_PATTERN = (rf'<<[A-Z]+::{_TOKEN}>>\s*'
                     r'|<!--GALLERY:[\[{][^\n]*[\]}]-->\s*'
                     r'|<!--FILES:\{[^\n]*\}-->\s*'
                     r'|<!--ASK:\{[^\n]*\}-->\s*'
                     r'|<!--STAGE:\{[^\n]*\}-->\s*')
UI_MARKER_RE = re.compile(UI_MARKER_PATTERN)

# History-less lanes (ExecutionContext) keep the wire copy AS the persisted
# copy, so there the browser's markers must survive: only the other <<X::…>>
# markers go. IMG, GALLERY, FILES and ASK ride along as bounded noise within
# the one run; the history read path cleans them before the next run's wire.
UI_MARKER_KEEP_IMG_RE = re.compile(rf'<<(?!IMG::)[A-Z]+::{_TOKEN}>>\s*')

# The live tool_end's words are capped (a result can run to 200K and the event
# rides the reattach ring and every bus listener); the browser shows 500-1500
# with a Show-more, and the history swap brings the whole text.
LIVE_RESULT_CHARS = 4000


def for_browser(text, limit=LIVE_RESULT_CHARS) -> str:
    """The live tool_end's copy of a tool result: the BROWSER form history keeps,
    every marker whole, the words capped. Until 2026-11-12 the stream sent the
    model's copy (markers stripped, loop-warn added, cut at 500), so a tool's
    pictures, rows and cards showed only after the history swap."""
    if not isinstance(text, str):
        return ''
    words = UI_MARKER_RE.sub('', text)
    if len(words) <= limit:
        return text
    # one marker per line: the browser's GALLERY/FILES readers take a marker to
    # the end of its line, so two glued together lost both widgets
    markers = ''.join(m.group(0).rstrip() + '\n' for m in UI_MARKER_RE.finditer(text))
    return markers + words[:limit] + '...'


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


def widget(kind: str, payload, ensure_ascii: bool = True) -> str:
    """`<!--KIND:{json}-->`, minted. Every `>` in the JSON is written \\u003e
    (the same value once parsed), so a title holding `-->` can't end the
    marker early and leave the rest as stray text (hunt 20)."""
    body = json.dumps(payload, ensure_ascii=ensure_ascii).replace('>', '\\u003e')
    return mint(f"<!--{kind}:{body}-->")


def only_minted(result):
    """A tool's result with every widget marker core didn't make removed
    (str, or the {'text', 'images'} dict shape)."""
    def clean(text):
        if not isinstance(text, str) or '<!--' not in text:
            return text
        now = time.monotonic()
        keep = lambda m: m.group(0) if now - _minted.get(m.group(0), -1e9) < _MINT_TTL else ''
        # Until nothing changes: cutting a forged inner marker joins the words
        # around it, so `<!--GALLER<!--ASK:x-->Y:[…]-->` became a whole
        # GALLERY one pass never saw (privacy scout, hunt 19). Each pass that
        # changes the text shortens it, so this ends.
        with _mint_lock:
            while True:
                out = _WIDGET_RE.sub(keep, text)
                if out == text:
                    return out
                text = out
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
    """The STAGE payloads in a tool result ({app, label, icon} dicts). They ride
    the live tool_end as their own field: opening the stage is an ACTION for the
    tab that ran the turn (api.js), apart from the card drawn from the marker."""
    out = []
    for m in _STAGE_RE.finditer(text if isinstance(text, str) else ''):
        try:
            c = json.loads(m.group(1))
        except ValueError:
            continue
        if isinstance(c, dict) and isinstance(c.get('app'), str):
            out.append({k: str(c.get(k) or '')[:64] for k in ('app', 'label', 'icon')})
    return out
