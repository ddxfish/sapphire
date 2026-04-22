"""Regression guards for 2026-04-22 sapphire-killer fix E.

Pre-fix: when _load_* functions hit an exception (corrupt JSON, mid-write
read from the watcher race), they set self._monoliths/_components/_spices
to {}. The next save_* call serialized the empty dict over the user's
file — permanent data loss from a transient read failure.

Post-fix:
  E1 — load functions preserve in-memory state on failure, set a
       _load_failed[<type>] flag True.
  E2 — save functions check the flag and refuse to save when True,
       logging a loud ERROR.
"""
import json
from pathlib import Path
import pytest


@pytest.fixture
def isolated_pm(tmp_path, monkeypatch):
    """PromptManager with USER_DIR pointed at tmp_path/prompts."""
    user_dir = tmp_path / "prompts"
    user_dir.mkdir()

    # Populate with valid data first
    monoliths_file = user_dir / "prompt_monoliths.json"
    monoliths_file.write_text(json.dumps({
        "_comment": "test",
        "sapphire": {"content": "Sapphire prompt content", "privacy_required": False},
        "user-custom": {"content": "User's carefully crafted prompt", "privacy_required": False},
    }))
    pieces_file = user_dir / "prompt_pieces.json"
    pieces_file.write_text(json.dumps({
        "_comment": "pieces",
        "components": {"character": {"sapphire": "sapphire char"}},
        "scenario_presets": {},
    }))
    spices_file = user_dir / "prompt_spices.json"
    spices_file.write_text(json.dumps({"_comment": "spices"}))

    from core.prompt_manager import PromptManager
    mgr = PromptManager.__new__(PromptManager)
    import threading
    mgr._lock = threading.Lock()
    mgr.USER_DIR = user_dir
    mgr.CORE_DIR = tmp_path / "core_prompts"
    mgr._components = {}
    mgr._scenario_presets = {}
    mgr._monoliths = {}
    mgr._spices = {}
    mgr._spice_meta = {}
    mgr._disabled_categories = set()
    mgr._watcher_thread = None
    mgr._watcher_running = False
    mgr._last_mtimes = {}
    mgr._active_preset_name = 'unknown'
    mgr._load_failed = {'pieces': False, 'monoliths': False, 'spices': False}

    mgr._load_all()
    return mgr, user_dir


def test_e1_load_monoliths_failure_preserves_in_memory_state(isolated_pm):
    """Corrupt the file on disk, reload, verify in-memory state is PRESERVED
    (not wiped to empty)."""
    mgr, user_dir = isolated_pm

    # Valid load first → 2 monoliths
    assert len(mgr._monoliths) == 2
    assert 'sapphire' in mgr._monoliths
    assert 'user-custom' in mgr._monoliths

    # Now corrupt the file on disk
    (user_dir / "prompt_monoliths.json").write_text("{ not valid json")

    # Reload — should fail but PRESERVE in-memory state
    mgr._load_monoliths()

    # In-memory state must still have the monoliths
    assert len(mgr._monoliths) == 2, \
        "Failed load must NOT wipe in-memory state — this was the pre-fix bug"
    assert 'user-custom' in mgr._monoliths, \
        "User's custom monolith must survive a failed reload"

    # Flag must be set
    assert mgr._load_failed['monoliths'] is True


def test_e2_save_monoliths_refuses_when_load_failed(isolated_pm):
    """After a failed load, save_monoliths must refuse. Disk file must be
    untouched."""
    mgr, user_dir = isolated_pm
    monoliths_path = user_dir / "prompt_monoliths.json"

    # Corrupt the file + reload → flag goes True
    monoliths_path.write_text("{ not valid json")
    mgr._load_monoliths()
    assert mgr._load_failed['monoliths'] is True

    # Capture current corrupt-file content
    corrupt_bytes = monoliths_path.read_bytes()

    # Attempt to save — should refuse and NOT overwrite the disk file
    mgr.save_monoliths()

    # Disk file must STILL be the corrupt version (not overwritten by save)
    assert monoliths_path.read_bytes() == corrupt_bytes, \
        "save_monoliths must NOT overwrite disk when _load_failed['monoliths'] is set"


def test_e1_e2_successful_reload_clears_flag(isolated_pm):
    """Fix the file on disk + reload → flag clears → save works again."""
    mgr, user_dir = isolated_pm
    monoliths_path = user_dir / "prompt_monoliths.json"

    # Corrupt + reload
    monoliths_path.write_text("{ not valid json")
    mgr._load_monoliths()
    assert mgr._load_failed['monoliths'] is True

    # Fix the file
    monoliths_path.write_text(json.dumps({
        "_comment": "recovered",
        "sapphire": {"content": "recovered content", "privacy_required": False},
    }))
    mgr._load_monoliths()

    # Flag must be cleared on successful load
    assert mgr._load_failed['monoliths'] is False

    # Add a new monolith and save — should work now
    mgr._monoliths["user-edit"] = {"content": "new content", "privacy_required": False}
    mgr.save_monoliths()

    saved = json.loads(monoliths_path.read_text())
    assert "user-edit" in saved
    assert "sapphire" in saved


def test_e1_load_pieces_preserves_on_failure(isolated_pm):
    """Same preservation guarantee for prompt_pieces.json."""
    mgr, user_dir = isolated_pm

    initial_components = dict(mgr._components)
    assert len(initial_components) > 0

    (user_dir / "prompt_pieces.json").write_text("[[ not valid")
    mgr._load_pieces()

    assert mgr._components == initial_components, \
        "Failed pieces load must preserve in-memory state"
    assert mgr._load_failed['pieces'] is True


def test_e2_save_components_refuses_when_pieces_load_failed(isolated_pm):
    """save_components gated on the pieces flag."""
    mgr, user_dir = isolated_pm
    pieces_path = user_dir / "prompt_pieces.json"

    pieces_path.write_text("[[ not valid")
    mgr._load_pieces()
    assert mgr._load_failed['pieces'] is True

    corrupt_bytes = pieces_path.read_bytes()
    mgr.save_components()
    assert pieces_path.read_bytes() == corrupt_bytes


def test_e2_save_scenario_presets_refuses_when_pieces_load_failed(isolated_pm):
    """save_scenario_presets also gated on pieces (they share the file)."""
    mgr, user_dir = isolated_pm
    pieces_path = user_dir / "prompt_pieces.json"

    pieces_path.write_text("[[ not valid")
    mgr._load_pieces()
    assert mgr._load_failed['pieces'] is True

    corrupt_bytes = pieces_path.read_bytes()
    mgr.save_scenario_presets()
    assert pieces_path.read_bytes() == corrupt_bytes


def test_e1_load_spices_preserves_on_failure(isolated_pm):
    """Same preservation guarantee for prompt_spices.json."""
    mgr, user_dir = isolated_pm
    # Start with a valid spices state
    mgr._spices = {"category_a": ["item1", "item2"]}
    mgr._spice_meta = {"key": "val"}

    (user_dir / "prompt_spices.json").write_text("not json")
    mgr._load_spices()

    assert mgr._spices == {"category_a": ["item1", "item2"]}, \
        "Spices in-memory state must survive failed load"
    assert mgr._load_failed['spices'] is True


def test_e2_save_spices_refuses_when_load_failed(isolated_pm):
    """save_spices gated on the spices flag."""
    mgr, user_dir = isolated_pm
    spices_path = user_dir / "prompt_spices.json"

    spices_path.write_text("not json")
    mgr._load_spices()
    assert mgr._load_failed['spices'] is True

    corrupt_bytes = spices_path.read_bytes()
    mgr.save_spices()
    assert spices_path.read_bytes() == corrupt_bytes
