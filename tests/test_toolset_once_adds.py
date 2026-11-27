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


# ── a whole toolset shipped later: memory_read_only (2026-11-26) ──

WRITERS = {"save_memory", "update_memory", "delete_memory", "update_self", "create_goal",
           "update_goal", "delete_goal", "notepad_append_lines", "notepad_delete_lines",
           "notepad_insert_line", "memory_save_image", "schedule_task", "prompt_pieces",
           "prompt_edit", "prompt_create", "switch_toolset", "reset_chat"}


def test_an_existing_install_gets_the_read_only_toolset_once(tmp_path):
    _write(tmp_path, {"limited_web": {"functions": ["web_search"]}, "default": {"functions": []}})
    tm = _mgr(tmp_path)
    tm._load()
    on_disk = _read(tmp_path)
    assert "memory_read_only_2026_11" in on_disk["_migrations"]
    funcs = on_disk["memory_read_only"]["functions"]
    assert {"search_memory", "read_self", "notepad_read"} <= set(funcs)
    assert not WRITERS & set(funcs), "nothing in it writes to a shared store"

    # deleted afterwards: it stays deleted
    del on_disk["memory_read_only"]
    _write(tmp_path, on_disk)
    tm2 = _mgr(tmp_path)
    tm2._load()
    assert "memory_read_only" not in _read(tmp_path)


def test_a_toolset_the_user_already_made_under_that_name_is_left_as_it_is(tmp_path):
    mine = {"emoji": "x", "functions": ["search_memory", "my_own_tool"]}
    _write(tmp_path, {"limited_web": {"functions": []}, "default": {"functions": []},
                      "memory_read_only": mine})
    tm = _mgr(tmp_path)
    tm._load()
    on_disk = _read(tmp_path)
    assert on_disk["memory_read_only"] == mine
    assert "memory_read_only_2026_11" in on_disk["_migrations"]


def test_a_fresh_install_has_it_and_ledgers_the_seed(tmp_path):
    tm = _mgr(tmp_path)
    tm._load()
    on_disk = _read(tmp_path)
    assert "memory_read_only" in on_disk
    assert set(ToolsetManager._ONCE_SEEDS) <= set(on_disk["_migrations"])


def test_the_shipped_read_only_toolset_names_only_tools_that_do_not_write():
    """Checked against the tools' own `writes` flag where the source declares
    one, so a tool that starts writing cannot stay in this set unnoticed."""
    import re
    root = Path(inspect.getfile(ToolsetManager)).parent.parent.parent
    shipped = json.loads((root / "core" / "toolsets" / "toolsets.json").read_text(encoding="utf-8"))
    funcs = shipped["memory_read_only"]["functions"]
    assert not WRITERS & set(funcs)
    sources = [*(root / "functions").glob("*.py"), *(root / "core" / "plugins").glob("*/tools/*.py")]
    blocks = []
    for path in sources:
        text = path.read_text(encoding="utf-8")
        blocks += re.split(r'\n    \{\s*\n\s*["\']type["\']: ["\']function["\']', text)
    for name in funcs:
        mine = [b for b in blocks if re.search(rf'["\']name["\']: ["\']{name}["\']', b)]
        assert not any(re.search(r'["\']writes["\']: True', b) for b in mine), f"{name} is marked as a writer"
