"""The process-wide daemon handle.

Core loads daemon.py under its own module name; routes, tools and hooks import
plugins.discord.daemon. Two module objects, one runtime — so the handle lives
HERE, in a module both reach through a normal import, never in daemon.py's
globals (S7 relearned it: a handle in daemon.py left every route saying
"stopped" while the daemon ran).
"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass, field


@dataclass
class RuntimeHandle:
    plugin_name: str
    plugin_loader: object
    settings: dict
    loop: asyncio.AbstractEventLoop | None = None
    thread: threading.Thread | None = None
    container: object | None = None
    started: threading.Event = field(default_factory=threading.Event)
    failed: threading.Event = field(default_factory=threading.Event)
    startup_error: BaseException | None = None


handle: RuntimeHandle | None = None
lifecycle_lock = threading.Lock()
