"""Words for the HTTP errors the screen shows (FastAPI's `detail`).

A route's unexpected failure logs the raw exception and tells the person what
didn't happen — never str(e), a class name or a path. Worded refusals a route
raises on purpose (RoomError, FlashError, ValueError...) keep their own text.
"""
import logging

from pathlib import Path

from fastapi import HTTPException

logger = logging.getLogger(__name__)

AGAIN = "Try again. If it keeps happening, restart Sapphire."
_HOME = str(Path.home())


def server_error(doing, e=None, why=True):
    """HTTPException(500) saying "Sapphire couldn't {doing}." The full error goes
    to the log; a short tail of its text rides last (fix first, details last —
    a toast has no Details fold, and the log isn't the person's to read).
    why=False for a route whose error text is never the person's business."""
    if e is not None:
        logger.error(f"[HTTP] couldn't {doing}: {type(e).__name__}: {e}", exc_info=e)
    tail = ""
    if why and e is not None and str(e).strip("'\" "):
        tail = f" ({str(e).replace(_HOME, '~')[:150]})"      # a path names the person: ~ will do
    return HTTPException(status_code=500, detail=f"Sapphire couldn't {doing}. {AGAIN}{tail}")


def invalid_request(errors):
    """FastAPI's 422 body (a list of dicts) as one sentence — the browser
    showed "[object Object]"."""
    bits = []
    for err in errors[:3]:
        loc = [str(p) for p in err.get('loc', ()) if p not in ('body', 'query', 'path')]
        msg = str(err.get('msg') or 'is not valid').removeprefix('Value error, ')
        bits.append(f"{'.'.join(loc)}: {msg}" if loc else msg)
    return "Sapphire couldn't use what was sent" + (f" ({'; '.join(bits)})." if bits else ".") + \
        " Reload the page and try again."
