"""Test-time containment for the story engine's on-disk state.

Every module-level path in gameroom_story.state points at the developer's
REAL user/story_saves/ by default. A fixture that forgets even one of them
lets a test write live data — on 2026-08-05 a test that redirected
SAVES_ROOT and ACTIVE_FILE but not DYNAMIC_FILE quarantined the running
instance's story costumes and left junk prompt entries in the real sidecar.

This autouse guard redirects all of them to a per-test tmp dir BEFORE any
fixture runs, so forgetting one is no longer possible. Individual fixtures
still redirect explicitly (clearer at the call site); this is the floor.
"""
import sys
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).parent.parent
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))


@pytest.fixture(autouse=True)
def _never_touch_real_saves(tmp_path, monkeypatch):
    from gameroom_story import rooms, state as st
    base = tmp_path / "story_saves"
    base.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(rooms, "SAVES_ROOT", base, raising=False)
    monkeypatch.setattr(st, "SAVES_ROOT", base, raising=False)
    monkeypatch.setattr(st, "ACTIVE_FILE", base / "active.json", raising=False)
    monkeypatch.setattr(st, "DYNAMIC_FILE", base / "_dynamic_monoliths.json",
                        raising=False)
    yield
