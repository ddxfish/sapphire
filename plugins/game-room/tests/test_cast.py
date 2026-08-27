# Character system ("cast") — Waves 1+2 (plan tmp/character-system-plan.md,
# Krem's rulings 2026-08-24/25): characters are EVENT-seeded journal state
# (regen-time-machine-safe by construction); parts are objects that live on
# a character; worn is worn (usable/inspectable, never `has:`); switched
# gear returns to inventory. All pure — no store, no system.
import sys
from pathlib import Path

PLUGIN_DIR = Path(__file__).parent.parent
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

from gameroom_story import referee, render, session, state as st  # noqa: E402


def _state(*events):
    s = st.initial_state()
    for ev in events:
        st.apply_event(s, ev)
    return s

JOIN = {"event": "cast_join", "id": "sapphire", "name": "Sapphire",
        "controlled_by": "dm",
        "parts": {"hands": {"desc": "hers",
                            "interactions": {"hold": {
                                "condition": {"flag": "key_found"},
                                "blocked_message": "Not yet.",
                                "message": "Held.",
                                "part": {"char": "sapphire", "part": "hands",
                                         "key": "held", "value": True}}}}}}
PJOIN = {"event": "cast_join", "id": "player", "name": "Player",
         "controlled_by": "player"}
ROOM = {"id": 1, "title": "Test Room", "template": "a bare room",
        "objects": {}, "exits": []}
STORY = {"meta": {"slug": "t", "title": "T"}, "rooms": {1: ROOM},
         "items": {"spacesuit": {"desc": "white, whole-body", "wears": "body"}}}


# ── The fold ────────────────────────────────────────────────────────────────

class TestFold:
    def test_join_and_started_reset(self):
        s = _state(JOIN)
        assert s["cast"]["sapphire"]["name"] == "Sapphire"
        st.apply_event(s, {"event": "started", "room": 1})
        assert s["cast"] == {}          # fresh run = fresh cast

    def test_wore_swap_sends_old_to_inventory(self):
        s = _state(PJOIN, {"event": "gained", "item": "raincoat"},
                   {"event": "wore", "char": "player", "slot": "body", "item": "raincoat"})
        assert s["cast"]["player"]["wearing"]["body"] == "raincoat"
        assert "raincoat" not in s["inventory"]      # worn is worn (B)
        st.apply_event(s, {"event": "wore", "char": "player",
                           "slot": "body", "item": "spacesuit"})
        assert s["cast"]["player"]["wearing"]["body"] == "spacesuit"
        assert "raincoat" in s["inventory"]          # switched gear → inventory (C)

    def test_unwore(self):
        s = _state(PJOIN, {"event": "wore", "char": "player", "slot": "hat", "item": "cap"},
                   {"event": "unwore", "char": "player", "slot": "hat"})
        assert "hat" not in s["cast"]["player"]["wearing"]
        assert "cap" in s["inventory"]

    def test_part_set_and_fields(self):
        s = _state(JOIN,
                   {"event": "part_set", "char": "sapphire", "part": "hands",
                    "key": "held", "value": True},
                   {"event": "cast_set", "char": "sapphire", "key": "trust", "value": 3},
                   {"event": "cast_adjust", "char": "sapphire", "key": "trust", "delta": 2})
        c = s["cast"]["sapphire"]
        assert c["parts"]["hands"]["state"]["held"] is True
        assert c["fields"]["trust"] == 5

    def test_stub_create_on_missing_join(self):
        s = _state({"event": "wore", "char": "ghost", "slot": "hat", "item": "cap"})
        assert s["cast"]["ghost"]["wearing"]["hat"] == "cap"


# ── Conditions ──────────────────────────────────────────────────────────────

class TestConditions:
    def _suited(self):
        return _state(PJOIN, {"event": "wore", "char": "player",
                              "slot": "body", "item": "spacesuit"})

    def test_wearing_str_and_dict(self):
        s = self._suited()
        assert referee.check_condition({"wearing": "spacesuit"}, s)
        assert referee.check_condition(
            {"wearing": {"char": "player", "slot": "body", "item": "spacesuit"}}, s)
        assert referee.check_condition({"wearing": {"char": "player", "slot": "body"}}, s)
        assert not referee.check_condition(
            {"wearing": {"char": "player", "slot": "hat"}}, s)
        assert not referee.check_condition({"wearing": "top hat"}, s)

    def test_worn_is_not_has(self):
        s = self._suited()
        assert not referee.check_condition({"has": "spacesuit"}, s)   # ruling B

    def test_part_and_gte(self):
        s = _state(JOIN, {"event": "part_set", "char": "sapphire", "part": "hands",
                          "key": "held", "value": True},
                   {"event": "cast_set", "char": "sapphire", "key": "trust", "value": 7})
        assert referee.check_condition(
            {"part": {"char": "sapphire", "part": "hands", "key": "held"}}, s)
        assert referee.check_condition(
            {"cast_gte": {"char": "sapphire", "key": "trust", "n": 5}}, s)
        assert not referee.check_condition(
            {"cast_gte": {"char": "sapphire", "key": "trust", "n": 8}}, s)


# ── Effects → events ────────────────────────────────────────────────────────

def test_effect_events_cast_family():
    evs = referee._effect_events({
        "wear": {"char": "player", "slot": "body", "item": "spacesuit"},
        "part": {"char": "sapphire", "part": "hands", "key": "held"},
        "cast_adjust": {"sapphire": {"trust": 1}}}, st.initial_state())
    kinds = [e["event"] for e in evs]
    assert kinds == ["wore", "part_set", "cast_adjust"]
    assert evs[1]["value"] is True            # omitted value defaults truthy


# ── Referee: dressing + parts as interactables ──────────────────────────────

class TestResolve:
    def test_wear_and_remove(self):
        s = _state(PJOIN, {"event": "gained", "item": "spacesuit"})
        evs, msg, ok = referee.resolve(STORY, s, ROOM, STORY["rooms"], "wear", "spacesuit")
        assert ok and evs[0] == {"event": "wore", "char": "player",
                                 "slot": "body", "item": "spacesuit"}
        for ev in evs:
            st.apply_event(s, ev)
        evs, msg, ok = referee.resolve(STORY, s, ROOM, STORY["rooms"], "remove", "spacesuit")
        assert ok and evs[0]["event"] == "unwore"

    def test_wear_refuses_unwearable_and_uncarried(self):
        s = _state(PJOIN)
        _, msg, ok = referee.resolve(STORY, s, ROOM, STORY["rooms"], "wear", "spacesuit")
        assert not ok and "inventory" in msg
        st.apply_event(s, {"event": "gained", "item": "rock"})
        _, msg, ok = referee.resolve(STORY, s, ROOM, STORY["rooms"], "wear", "rock")
        assert not ok and "wearable" in msg

    def test_part_interaction_gated_then_fires(self):
        s = _state(JOIN)
        _, msg, ok = referee.resolve(STORY, s, ROOM, STORY["rooms"], "hold", "sapphire hands")
        assert not ok and msg == "Not yet."
        st.apply_event(s, {"event": "state_set", "key": "key_found", "value": True})
        evs, msg, ok = referee.resolve(STORY, s, ROOM, STORY["rooms"], "hold", "sapphire_hands")
        assert ok and msg == "Held."
        for ev in evs:
            st.apply_event(s, ev)
        assert s["cast"]["sapphire"]["parts"]["hands"]["state"]["held"] is True

    def test_look_at_part(self):
        s = _state(JOIN)
        _, msg, ok = referee.resolve(STORY, s, ROOM, STORY["rooms"], "look", "sapphire_hands")
        assert ok and "hers" in msg

    def test_worn_item_still_inspectable(self):
        s = _state(PJOIN, {"event": "wore", "char": "player",
                           "slot": "body", "item": "spacesuit"})
        _, msg, ok = referee.resolve(STORY, s, ROOM, STORY["rooms"], "look", "spacesuit")
        assert ok and "white, whole-body" in msg     # ruling B: worn stays usable


# ── Ghost sheet ─────────────────────────────────────────────────────────────

def test_ghost_block_carries_cast_sheet():
    s = _state(JOIN, PJOIN,
               {"event": "wore", "char": "player", "slot": "body", "item": "spacesuit"},
               {"event": "cast_set", "char": "sapphire", "key": "trust", "value": 7})
    block = render.ghost_block(STORY, s, ROOM)
    assert "Cast (" in block
    assert "Sapphire — mine to play" in block
    assert "sapphire_hands (responds to: hold)" in block
    assert "Player — the player's: wearing spacesuit (body)" in block
    assert "trust: 7" in block


# ── Seeding ─────────────────────────────────────────────────────────────────

class TestSeedCast:
    def test_declared_cast_wins(self):
        story = {"meta": {"slug": "t", "cast": [
            {"id": "hero", "name": "Hero", "controlled_by": "player"},
            {"bad": "no id"}]}}
        evs = session._seed_cast(story)
        assert [e["id"] for e in evs] == ["hero"]

    def test_synthesis_from_proto_cast(self):
        story = {"meta": {"slug": "t", "role": {"name": "Rose", "text": "x"},
                          "player_role": "Jack, a drifter."}}
        evs = session._seed_cast(story)
        assert [e["id"] for e in evs] == ["rose", "player"]
        assert evs[0]["controlled_by"] == "dm"
        assert evs[1]["controlled_by"] == "player"
        assert "Jack" in evs[1]["desc"]


# ── Player-side dressing (card write path, Krem's A vote 2026-08-25) ────────

class TestPlayerDress:
    def _setup(self, monkeypatch, chat):
        from gameroom_story import rooms
        monkeypatch.setattr(rooms, "_story_roots",
                            lambda: [Path(__file__).parent / "fixtures" / "stories"])
        st.set_active(chat, "mad-manse", None)
        story = rooms.load_story("mad-manse")
        assert st.append_many("mad-manse", chat, [
            {"event": "started", "story": "mad-manse",
             "room": story["meta"]["start"], "turn": 0},
            {"event": "cast_join", "id": "player", "name": "P",
             "controlled_by": "player", "turn": 0}])
        return story

    def test_wear_remove_roundtrip_and_no_regrant(self, monkeypatch):
        chat = "dress-rt-chat"
        self._setup(monkeypatch, chat)
        msg, ok = session.player_dress(chat, "wear", "cloak")
        assert ok, msg
        _s, state = session.load_active(chat)
        assert state["cast"]["player"]["wearing"]["body"] == "cloak"
        # worn is worn — the always-was grant must NOT re-add it (the
        # load_active re-grant bug this wave caught and fixed)
        assert "cloak" not in state["inventory"]
        assert "wearables" not in state  # engine state stays lean
        msg, ok = session.player_dress(chat, "remove", "cloak")
        assert ok, msg
        _s, state = session.load_active(chat)
        assert "body" not in state["cast"]["player"]["wearing"]
        assert "cloak" in state["inventory"]

    def test_refusals(self, monkeypatch):
        chat = "dress-ref-chat"
        self._setup(monkeypatch, chat)
        msg, ok = session.player_dress(chat, "wear", "locket")   # no wears slot
        assert not ok and "wearable" in msg
        st.update_active(chat, paused=True)
        msg, ok = session.player_dress(chat, "wear", "cloak")
        assert not ok and "paused" in msg
        assert session.player_dress("never-started-chat", "wear", "cloak")[1] is False


def test_overlay_carries_wears_on_shipped_shadow():
    # A user shadow may make a shipped item wearable (B.1, 2026-08-25) —
    # the field-merge law must carry `wears` like desc/hidden.
    target = {"raincoat": {"desc": "yellow", "interactions": {"look": {"message": "m"}}}}
    session._overlay_object_map(target, {"raincoat": {"wears": "body"}})
    assert target["raincoat"]["wears"] == "body"
    assert target["raincoat"]["desc"] == "yellow"          # untouched
    assert "look" in target["raincoat"]["interactions"]    # mechanics survive


# ── Equipment grid (slots + per-slot wearables, Krem 2026-08-26) ────────────

def test_cast_join_carries_slots():
    s = _state({"event": "cast_join", "id": "x", "slots": ["hat", "jacket"]})
    assert s["cast"]["x"]["slots"] == ["hat", "jacket"]


def test_full_state_slots_and_wearables_map(monkeypatch):
    from gameroom_story import rooms
    monkeypatch.setattr(rooms, "_story_roots",
                        lambda: [Path(__file__).parent / "fixtures" / "stories"])
    chat = "grid-chat"
    st.set_active(chat, "mad-manse", None)
    story = rooms.load_story("mad-manse")
    assert st.append_many("mad-manse", chat, [
        {"event": "started", "story": "mad-manse",
         "room": story["meta"]["start"], "turn": 0},
        {"event": "cast_join", "id": "a", "slots": ["jacket", "shirt"],
         "controlled_by": "player", "turn": 0},
        {"event": "cast_join", "id": "b", "turn": 0}])
    full = session.full_state(None, session=chat)
    assert full["cast"]["a"]["slots"] == ["jacket", "shirt"]      # declared
    assert full["cast"]["b"]["slots"] == list(session.WEAR_SLOTS)  # default
    # fixture cloak declares wears: body → the dropdown map carries it
    assert full["wearables"].get("cloak") == "body"


def test_full_state_icons_by_convention(monkeypatch):
    # icon-<name>.webp in the pack's art dir → icons map, no spec field needed
    from gameroom_story import rooms
    monkeypatch.setattr(rooms, "_story_roots",
                        lambda: [Path(__file__).parent / "fixtures" / "stories"])
    chat = "icons-chat"
    st.set_active(chat, "mad-manse", None)
    story = rooms.load_story("mad-manse")
    assert st.append_many("mad-manse", chat, [
        {"event": "started", "story": "mad-manse",
         "room": story["meta"]["start"], "turn": 0}])
    full = session.full_state(None, session=chat)
    assert full["icons"]["cloak"].endswith("/backdrops/icon-cloak.webp")
    assert "locket" not in full["icons"]          # no file → no entry, no broken img


# ── Declarations live at load; state stays journaled (2026-08-26) ───────────

def test_overlay_cast_refreshes_declarations_keeps_state():
    story = {"meta": {"cast": [
        {"id": "s", "name": "New Name", "slots": ["hat", "outer"],
         "image": "s.webp",
         "parts": {"hands": {"desc": "v2", "interactions": {"hold": {"message": "m"}}}}}]}}
    state = _state({"event": "cast_join", "id": "s", "name": "Old", "slots": ["hat", "jacket"],
                    "wearing": {"jacket": "blazer"},
                    "parts": {"hands": {"desc": "v1"}}},
                   {"event": "part_set", "char": "s", "part": "hands", "key": "held", "value": True},
                   {"event": "cast_set", "char": "s", "key": "trust", "value": 4})
    session._overlay_cast(story, state)
    c = state["cast"]["s"]
    assert c["name"] == "New Name" and c["slots"] == ["hat", "outer"] and c["image"] == "s.webp"
    assert c["parts"]["hands"]["desc"] == "v2"                 # spec refreshed
    assert c["parts"]["hands"]["state"]["held"] is True        # live state kept
    assert c["wearing"] == {"jacket": "blazer"}                # state untouched
    assert c["fields"]["trust"] == 4


def test_overlay_cast_creates_missing_with_empty_wearing():
    story = {"meta": {"cast": [{"id": "p", "name": "P", "controlled_by": "player",
                                "wearing": {"outer": "coat"}}]}}
    state = st.initial_state()
    session._overlay_cast(story, state)
    assert state["cast"]["p"]["name"] == "P"
    assert state["cast"]["p"]["wearing"] == {}   # never seeded at a read seam


def test_full_state_layers_by_convention(monkeypatch):
    # layer-<name>.png beside the icons → layers map; worn or carried alike,
    # the card decides what paints. No file → no entry.
    from gameroom_story import rooms
    monkeypatch.setattr(rooms, "_story_roots",
                        lambda: [Path(__file__).parent / "fixtures" / "stories"])
    chat = "layers-chat"
    st.set_active(chat, "mad-manse", None)
    story = rooms.load_story("mad-manse")
    assert st.append_many("mad-manse", chat, [
        {"event": "started", "story": "mad-manse",
         "room": story["meta"]["start"], "turn": 0},
        {"event": "cast_join", "id": "hero", "name": "Hero", "turn": 0},
        {"event": "wore", "char": "hero", "slot": "body", "item": "cloak", "turn": 0}])
    full = session.full_state(None, session=chat)
    assert full["cast"]["hero"]["wearing"] == {"body": "cloak"}
    assert full["layers"]["cloak"].endswith("/backdrops/layer-cloak.png")
    assert "locket" not in full["layers"]
