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
