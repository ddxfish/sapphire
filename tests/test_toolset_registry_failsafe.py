"""REGRESSION GUARDS — 2026-08-19 toolset registry fail-safe.

ToolsetManager._load() on a parse failure used to wipe the in-memory
registry to {} and fall through to the seed migrations. Two consequences:

  1. LIVE WIPE — the file watcher's reload() catching a mid-write /
     transiently corrupt user/toolsets.json emptied the registry, so every
     toolset name dangled; the next update_enabled_functions() fell to the
     'none' fallback → zero tools, cemented until restart, while the UI
     still showed the chat's stored toolset.
  2. DISK CLOBBER — with _toolsets == {}, the 'default'/'limited_web' seed
     migration ran and _save_to_user() REPLACED the unreadable file with
     two factory toolsets, destroying user data that might have been
     hand-recoverable.

Contract now: a parse failure returns early — keep the previous in-memory
registry when there is one, never run migrations or write to disk over a
file we couldn't read.
"""
import inspect
import threading
from pathlib import Path

from core.toolsets.toolset_manager import ToolsetManager


def _mk_manager(tmp_path: Path) -> ToolsetManager:
    """Build a manager pointed at a temp user dir WITHOUT running __init__
    (the real constructor resolves the project's live user/toolsets.json)."""
    tm = ToolsetManager.__new__(ToolsetManager)
    tm.BASE_DIR = Path(inspect.getfile(ToolsetManager)).parent
    tm.USER_DIR = tmp_path
    tm._toolsets = {}
    tm._lock = threading.Lock()
    tm._watcher_thread = None
    tm._watcher_running = False
    tm._last_mtimes = {}
    return tm


def test_reload_parse_failure_keeps_previous_registry(tmp_path):
    tm = _mk_manager(tmp_path)
    (tmp_path / "toolsets.json").write_text(
        '{"mine": {"functions": ["save_memory"]}}', encoding="utf-8")
    tm._load()
    assert tm.toolset_exists("mine")

    # Simulate the watcher catching a mid-write file
    (tmp_path / "toolsets.json").write_text('{"mine": {"functi', encoding="utf-8")
    tm.reload()

    assert tm.toolset_exists("mine"), (
        "Parse failure on reload wiped the in-memory registry — every "
        "toolset name now dangles and the next apply falls to 'none' "
        "(zero tools) until restart. Keep the previous registry."
    )
    assert tm.get_toolset_functions("mine") == ["save_memory"]


def test_load_failure_never_clobbers_file_on_disk(tmp_path):
    corrupt = '{"custom_stuff": {"functi'
    (tmp_path / "toolsets.json").write_text(corrupt, encoding="utf-8")

    tm = _mk_manager(tmp_path)
    tm._load()

    assert tm.get_toolset_names() == []
    assert (tmp_path / "toolsets.json").read_text(encoding="utf-8") == corrupt, (
        "_load() wrote over an unreadable toolsets.json (seed migration + "
        "_save_to_user ran on the empty registry). The file must be left "
        "untouched for hand recovery."
    )
