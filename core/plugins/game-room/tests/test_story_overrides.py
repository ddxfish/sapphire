# Story text overrides (storycfg:{slug}) — the DM-guide pattern extended to
# role_text / premise / player_role (plan tmp/story-prompt-kinds-plan.md).
# Shipped story.json is signed and read-only; user edits live in the plugin
# store and merge in rooms.load_story(). raw=True skips the merge (settings
# routes need shipped text for defaults + verbatim-equals-shipped resets).
import sys
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).parent.parent
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

from gameroom_story import rooms  # noqa: E402


FIXTURES = Path(__file__).parent / "fixtures" / "stories"


class FakeStore:
    def __init__(self):
        self.d = {}

    def get(self, k):
        return self.d.get(k)

    def save(self, k, v):
        self.d[k] = v


@pytest.fixture
def store(monkeypatch):
    fake = FakeStore()
    monkeypatch.setattr(rooms, "_story_roots", lambda: [FIXTURES])
    from core.plugin_loader import plugin_loader
    monkeypatch.setattr(plugin_loader, "get_plugin_state",
                        lambda name: fake)
    return fake


# ── load_story merge ─────────────────────────────────────────────────────────

def test_no_override_passthrough(store):
    meta = rooms.load_story("roled-tale")["meta"]
    assert meta["role"]["text"] == "Shipped backstory for Vera."
    assert meta["premise"] == "Shipped premise text."
    assert meta["player_role"] == "Shipped player role."


def test_overrides_apply(store):
    store.save("storycfg:roled-tale", {"role_text": "Her real story.",
                                       "premise": "New premise.",
                                       "player_role": "A stowaway."})
    meta = rooms.load_story("roled-tale")["meta"]
    assert meta["role"]["text"] == "Her real story."
    assert meta["role"]["name"] == "Vera"          # names are not overridable
    assert meta["premise"] == "New premise."
    assert meta["player_role"] == "A stowaway."


def test_raw_skips_overrides(store):
    store.save("storycfg:roled-tale", {"role_text": "Her real story."})
    meta = rooms.load_story("roled-tale", raw=True)["meta"]
    assert meta["role"]["text"] == "Shipped backstory for Vera."


def test_empty_override_means_shipped(store):
    store.save("storycfg:roled-tale", {"role_text": "", "premise": "   "})
    meta = rooms.load_story("roled-tale")["meta"]
    assert meta["role"]["text"] == "Shipped backstory for Vera."
    assert meta["premise"] == "Shipped premise text."


def test_role_text_ignored_without_shipped_role(store):
    # goblin-den ships NO role — half a role (text without a name) would
    # flip the identity-mode fallbacks, so the override must not apply.
    store.save("storycfg:goblin-den", {"role_text": "Ghost role."})
    meta = rooms.load_story("goblin-den")["meta"]
    assert meta.get("role") is None


def test_store_failure_falls_back_to_shipped(store, monkeypatch):
    from core.plugin_loader import plugin_loader
    monkeypatch.setattr(plugin_loader, "get_plugin_state",
                        lambda name: (_ for _ in ()).throw(RuntimeError("db down")))
    meta = rooms.load_story("roled-tale")["meta"]
    assert meta["role"]["text"] == "Shipped backstory for Vera."


def test_dm_guide_not_merged_into_meta(store):
    # dm_guide keeps its own lane (session.conduct_for reads the store
    # directly) — merging it here too would double-apply and break the
    # settings modal's shipped-default display.
    store.save("storycfg:roled-tale", {"dm_guide": "House rules."})
    meta = rooms.load_story("roled-tale")["meta"]
    assert meta["dm_guide"] == "Shipped DM guide."


def test_tiles_show_overridden_premise(store):
    store.save("storycfg:roled-tale", {"premise": "New premise.",
                                       "player_role": "A stowaway."})
    entry = rooms.list_stories()["roled-tale"]
    assert entry["premise"] == "New premise."
    assert entry["player_role"] == "A stowaway."
    assert entry["role"] == "Vera"


# ── settings routes: verbatim-equals-shipped reset ───────────────────────────

@pytest.fixture
def route_env(store, monkeypatch):
    from gameroom_story import session
    monkeypatch.setattr(session, "_cfg_store", store, raising=False)
    from routes import story_routes
    return story_routes


def test_settings_roundtrip_and_reset(route_env, store):
    r = route_env.get_story_settings("roled-tale")
    assert r["settings"]["role_text"] == "Shipped backstory for Vera."
    keys = [f["key"] for f in r["schema"]]
    assert "role_text" in keys and "premise" in keys and "player_role" in keys

    route_env.set_story_settings("roled-tale",
                                 body={"settings": {"role_text": "Her real story."}})
    assert store.d["storycfg:roled-tale"]["role_text"] == "Her real story."
    assert route_env.get_story_settings("roled-tale")["settings"]["role_text"] \
        == "Her real story."

    # Saving the SHIPPED text verbatim clears the override — pack updates
    # keep flowing (the dm_guide rule, applied uniformly).
    route_env.set_story_settings(
        "roled-tale", body={"settings": {"role_text": "Shipped backstory for Vera."}})
    assert store.d["storycfg:roled-tale"]["role_text"] == ""
    assert rooms.load_story("roled-tale")["meta"]["role"]["text"] \
        == "Shipped backstory for Vera."


def test_settings_no_role_field_for_roleless_story(route_env):
    r = route_env.get_story_settings("goblin-den")
    keys = [f["key"] for f in r["schema"]]
    assert "role_text" not in keys
    assert "premise" in keys and "dm_guide" in keys
