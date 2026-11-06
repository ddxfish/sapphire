"""One-time toolset additions (2026-11-06): search_help_docs reaches existing
installs' limited_web and personality once, and stays gone if removed after."""
import inspect
import json
import threading
from pathlib import Path

from core.toolsets.toolset_manager import ToolsetManager


def _mgr(tmp_path):
    tm = ToolsetManager.__new__(ToolsetManager)
    tm.BASE_DIR = Path(inspect.getfile(ToolsetManager)).parent
    tm.USER_DIR = tmp_path
    tm._toolsets, tm._lock, tm._last_mtimes = {}, threading.Lock(), {}
    tm._watcher_thread, tm._watcher_running = None, False
    return tm


def _write(tmp_path, data):
    (tmp_path / "toolsets.json").write_text(json.dumps(data), encoding="utf-8")


def _read(tmp_path):
    return json.loads((tmp_path / "toolsets.json").read_text(encoding="utf-8"))


def test_an_existing_install_gets_the_tool_once_and_keeps_a_removal(tmp_path):
    _write(tmp_path, {"limited_web": {"functions": ["web_search"]},
                      "personality": {"functions": ["ask_user"]},
                      "default": {"functions": ["get_time"]},
                      "mine": {"functions": ["web_search"]}})
    tm = _mgr(tmp_path)
    tm._load()
    on_disk = _read(tmp_path)
    assert on_disk["limited_web"]["functions"] == ["web_search", "search_help_docs"]
    assert on_disk["personality"]["functions"] == ["ask_user", "search_help_docs"]
    assert on_disk["mine"]["functions"] == ["web_search"]          # user toolsets untouched
    assert "help_docs_2026_11" in on_disk["_migrations"]
    assert "_migrations" not in tm._toolsets                         # never a toolset

    # The user takes it out again: the next boot leaves it out
    on_disk["limited_web"]["functions"] = ["web_search"]
    _write(tmp_path, on_disk)
    tm2 = _mgr(tmp_path)
    tm2._load()
    assert _read(tmp_path)["limited_web"]["functions"] == ["web_search"]


def test_a_deleted_toolset_is_not_recreated(tmp_path):
    _write(tmp_path, {"limited_web": {"functions": []}, "default": {"functions": []}})
    tm = _mgr(tmp_path)
    tm._load()
    assert "personality" not in _read(tmp_path)


def test_first_run_ledgers_everything_as_done(tmp_path):
    tm = _mgr(tmp_path)
    tm._load()
    on_disk = _read(tmp_path)
    assert set(ToolsetManager._ONCE_ADDS) <= set(on_disk["_migrations"])
    assert "search_help_docs" in on_disk["limited_web"]["functions"]
