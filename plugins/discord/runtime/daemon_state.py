"""Process-wide daemon handle shared across import paths."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Optional

from plugins.discord.runtime.container import RuntimeContainer


@dataclass
class RuntimeHandle:
    plugin_name: str
    plugin_loader: object
    settings: dict
    loop: object | None = None
    thread: threading.Thread | None = None
    container: RuntimeContainer | None = None
    started: threading.Event = field(default_factory=threading.Event)
    failed: threading.Event = field(default_factory=threading.Event)
    startup_error: BaseException | None = None


handle: RuntimeHandle | None = None
lifecycle_lock = threading.Lock()
