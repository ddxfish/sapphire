# tests/test_prompt_packs.py
"""Prompt-pack registry (capabilities.prompts) — mirror-only merge rules.

Covers the rails from tmp/story-engine-v2.md:
  - mirror-only: pack entries never reach user/prompts files on save
  - user wins name collisions (property merge order)
  - pack prompts are read-only: delete refuses, user shadow deletes cleanly
  - unregister goes dark + active-preset handoff falls back loudly
"""
import json

import pytest

from core import prompt_packs
from core.prompt_manager import prompt_manager
from core import prompt_crud, prompt_state


PLUGIN = "test-pack-plugin"


@pytest.fixture(autouse=True)
def clean_registry():
    """Every test starts and ends with no test pack registered."""
    prompt_packs.unregister_plugin(PLUGIN)
    yield
    prompt_packs.unregister_plugin(PLUGIN)


def _register(monoliths=None, pieces=None):
    return prompt_packs.register_pack(
        PLUGIN,
        monoliths=monoliths or {
            "pack_mono": {"content": "Pack monolith text", "privacy_required": False}
        },
        pieces=pieces or {
            "components": {"emotions": {"pack_emotion": "Pack emotion text"}},
            "scenario_presets": {},
        },
    )


def test_register_counts_and_overlay():
    counts = _register()
    assert counts == {"monoliths": 1, "components": 1, "scenario_presets": 0}
    assert prompt_packs.overlay_monoliths()["pack_mono"]["content"] == "Pack monolith text"
    assert prompt_packs.overlay_components()["emotions"]["pack_emotion"] == "Pack emotion text"
    assert prompt_packs.get_sources() == {"pack_mono": PLUGIN}
    assert prompt_packs.piece_source("emotions", "pack_emotion") == PLUGIN


def test_string_monolith_normalized():
    _register(monoliths={"plain": "Just a string"})
    mono = prompt_packs.overlay_monoliths()["plain"]
    assert mono == {"content": "Just a string", "privacy_required": False}


def test_manager_properties_merge_pack_entries():
    _register()
    assert "pack_mono" in prompt_manager.monoliths
    assert "pack_emotion" in prompt_manager.components["emotions"]
    # And crud sees them
    assert "pack_mono" in prompt_crud.list_prompts()
    got = prompt_crud.get_prompt("pack_mono")
    assert got["type"] == "monolith" and got["content"] == "Pack monolith text"


def test_user_wins_collision():
    _register(monoliths={"pack_mono": "Pack version"})
    prompt_manager._monoliths["pack_mono"] = {"content": "User version", "privacy_required": False}
    try:
        assert prompt_manager.monoliths["pack_mono"]["content"] == "User version"
    finally:
        del prompt_manager._monoliths["pack_mono"]
    # Shadow gone — pack shows through again
    assert prompt_manager.monoliths["pack_mono"]["content"] == "Pack version"


def test_unregister_goes_dark():
    _register()
    assert "pack_mono" in prompt_manager.monoliths
    prompt_packs.unregister_plugin(PLUGIN)
    assert "pack_mono" not in prompt_manager.monoliths
    assert prompt_packs.get_sources() == {}


def test_save_monoliths_never_writes_pack_entries(tmp_path, monkeypatch):
    _register()
    assert "pack_mono" in prompt_manager.monoliths  # merged view sees it
    monkeypatch.setattr(prompt_manager, "USER_DIR", tmp_path)
    prompt_manager.save_monoliths()
    on_disk = json.loads((tmp_path / "prompt_monoliths.json").read_text(encoding="utf-8"))
    assert "pack_mono" not in on_disk


def test_delete_pack_prompt_refuses():
    _register()
    assert prompt_crud.delete_prompt("pack_mono") is False
    # Still there — mirror untouched
    assert "pack_mono" in prompt_manager.monoliths


def test_delete_user_shadow_reveals_pack(tmp_path, monkeypatch):
    _register(monoliths={"pack_mono": "Pack version"})
    monkeypatch.setattr(prompt_manager, "USER_DIR", tmp_path)
    prompt_manager._monoliths["pack_mono"] = {"content": "User version", "privacy_required": False}
    assert prompt_crud.delete_prompt("pack_mono") is True
    assert prompt_manager.monoliths["pack_mono"]["content"] == "Pack version"


def test_assemble_uses_pack_pieces():
    _register(pieces={
        "components": {
            "emotions": {"pack_emotion": "Pack emotion text"},
            "character": {"pack_char": "Pack character text"},
        },
        "scenario_presets": {},
    })
    text = prompt_manager.assemble_from_components(
        {"character": "pack_char", "emotions": ["pack_emotion"]})
    assert "Pack character text" in text
    assert "Pack emotion text" in text


def test_active_pack_prompt_handoff_on_unregister():
    _register()
    prev = prompt_state.get_active_preset_name()
    try:
        prompt_state.set_active_preset_name("pack_mono")
        current = prompt_state.get_current_prompt()
        assert "Pack monolith text" in current["content"]
        prompt_packs.unregister_plugin(PLUGIN)
        # Handoff reset the active preset — no stale pack name left behind
        assert prompt_state.get_active_preset_name() == "default"
        # And rendering still works (assembled default fallback)
        fallback = prompt_state.get_current_prompt()
        assert "Pack monolith text" not in fallback["content"]
    finally:
        prompt_state.set_active_preset_name(prev)


def test_user_shadow_survives_unregister_handoff():
    _register(monoliths={"pack_mono": "Pack version"})
    prompt_manager._monoliths["pack_mono"] = {"content": "User version", "privacy_required": False}
    prev = prompt_state.get_active_preset_name()
    try:
        prompt_state.set_active_preset_name("pack_mono")
        prompt_packs.unregister_plugin(PLUGIN)
        # User shadow exists — active preset must NOT be reset
        assert prompt_state.get_active_preset_name() == "pack_mono"
        assert "User version" in prompt_state.get_current_prompt()["content"]
    finally:
        prompt_state.set_active_preset_name(prev)
        prompt_manager._monoliths.pop("pack_mono", None)
