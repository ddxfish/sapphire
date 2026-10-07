"""core/ui_markers.py - the ONE list of markers that are for the browser, not the model.

A tool result (or a turn's text) can carry a marker the chat renders as a
widget: <<IMG::id>> / <<FILE::id>> (an image or file by id), the GALLERY tile
strip, the FILES player/download row, the ASK question card. They stay in
history for the browser and are stripped from every copy the model reads -
live (chat_tool_calling.strip_ui_markers) and on replay (history). Until
2026-10-07 the pattern was copied into both files; a new marker had to be added
twice (and the FILES one nearly wasn't).
"""
import re

UI_MARKER_PATTERN = (r'<<[A-Z]+::[^>]+>>\s*'
                     r'|<!--GALLERY:[\[{][^\n]*[\]}]-->\s*'
                     r'|<!--FILES:\{[^\n]*\}-->\s*'
                     r'|<!--ASK:\{[^\n]*\}-->\s*')
UI_MARKER_RE = re.compile(UI_MARKER_PATTERN)

# History-less lanes (ExecutionContext) keep the wire copy AS the persisted
# copy, so there the browser's markers must survive: only the other <<X::…>>
# markers go. IMG, GALLERY, FILES and ASK ride along as bounded noise within
# the one run; the history read path cleans them before the next run's wire.
UI_MARKER_KEEP_IMG_RE = re.compile(r'<<(?!IMG::)[A-Z]+::[^>]+>>\s*')


def has_marker(text) -> bool:
    return isinstance(text, str) and ('<<' in text or '<!--' in text)
