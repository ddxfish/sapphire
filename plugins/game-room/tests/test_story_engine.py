# Story engine (gameroom_story) — engine core tests (referee, journal, render
# rails). Moved from the story_engine plugin 2026-08-03 (single-engine merge).
# Run: conda run -n sapphire python -m pytest user/plugins/game-room/tests/ -q
import json
import sys
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(PLUGIN_DIR))

from gameroom_story import rooms, referee, render, state as st  # noqa: E402


@pytest.fixture
def story(monkeypatch):
    # goblin-den retired from the shipping app (packaging 2026-08-04); it
    # lives on here as the engine's canonical test pack.
    monkeypatch.setattr(rooms, "_story_roots",
                        lambda: [Path(__file__).parent / "fixtures" / "stories"])
    return rooms.load_story("goblin-den")


@pytest.fixture
def tmp_saves(tmp_path, monkeypatch):
    # DYNAMIC_FILE included — see conftest's autouse guard for why.
    monkeypatch.setattr(st, "SAVES_ROOT", tmp_path)
    monkeypatch.setattr(st, "ACTIVE_FILE", tmp_path / "active.json")
    monkeypatch.setattr(st, "DYNAMIC_FILE", tmp_path / "_dynamic_monoliths.json")
    return tmp_path


def _fresh(story):
    state = st.initial_state()
    state["room"] = story["meta"]["start"]
    return state


# ── Loader ───────────────────────────────────────────────────────────────────

def test_demo_story_loads(story):
    assert story["meta"]["slug"] == "goblin-den"
    assert set(story["rooms"]) == {1, 2, 3}
    assert story["rooms"][1]["title"] == "Cave Mouth"


def test_room_validation_rejects_bad_shapes():
    ok, _ = rooms.validate_room({"id": "x", "title": "t", "template": "y"})
    assert not ok
    ok, _ = rooms.validate_room({"id": 1, "title": "t", "template": "y",
                                 "exits": [{"label": "up", "to": None}]})
    assert not ok  # no destination and no generate flag
    ok, _ = rooms.validate_room({"id": 1, "title": "t", "template": "y",
                                 "exits": [{"label": "up", "to": None, "generate": True}]})
    assert ok


# ── Referee ──────────────────────────────────────────────────────────────────

def test_move_valid_exit(story):
    state = _fresh(story)
    events, msg, ok = referee.resolve(story, state, story["rooms"][1], story["rooms"], "move", "down the tunnel")
    assert ok and events[0] == {"event": "moved", "to": 2}
    # on_enter of room 2 rides along (dread in, wary out)
    kinds = [e["event"] for e in events]
    assert "emotions" in kinds


def test_move_unknown_exit_refused(story):
    state = _fresh(story)
    events, msg, ok = referee.resolve(story, state, story["rooms"][1], story["rooms"], "move", "the moon")
    assert not ok and not events and "No exit" in msg


def test_locked_exit_needs_key(story):
    state = _fresh(story)
    state["room"] = 2
    events, msg, ok = referee.resolve(story, state, story["rooms"][2], story["rooms"], "move", "the iron door")
    assert not ok and "locked" in msg
    state["inventory"].append("rusty_key")
    events, msg, ok = referee.resolve(story, state, story["rooms"][2], story["rooms"], "move", "the iron door")
    assert ok and events[0]["to"] == 3


def test_generate_exit_politely_deferred(story):
    state = _fresh(story)
    events, msg, ok = referee.resolve(story, state, story["rooms"][1], story["rooms"], "move", "the crack in the east wall")
    assert not ok and "generation" in msg


def test_search_reveals_hidden_and_gives_item(story):
    state = _fresh(story)
    events, msg, ok = referee.resolve(story, state, story["rooms"][1], story["rooms"], "search")
    assert ok
    found = [e for e in events if e["event"] == "found"]
    assert found and found[0]["target"] == "loose_stone" and found[0]["item"] == "rusty_key"
    # Second search after applying: nothing new
    for e in events:
        st.apply_event(state, e)
    events2, msg2, ok2 = referee.resolve(story, state, story["rooms"][1], story["rooms"], "search")
    assert ok2 and not events2 and "nothing" in msg2


def test_solve_wrong_then_right(story):
    state = _fresh(story)
    state["room"] = 3
    room = story["rooms"][3]
    events, msg, ok = referee.resolve(story, state, room, story["rooms"], "solve", "casket", "compass")
    assert ok and events[0]["event"] == "attempted" and "doesn't work" in msg
    events, msg, ok = referee.resolve(story, state, room, story["rooms"], "solve", "casket", "  MAP ")
    assert ok
    kinds = {e["event"] for e in events}
    assert {"solved", "gained", "emotions", "state_set"} <= kinds
    for e in events:
        st.apply_event(state, e)
    assert "moonstone_amulet" in state["inventory"]
    assert "triumphant" in state["emotions"]
    # Solving again: already solved
    _, msg, ok = referee.resolve(story, state, room, story["rooms"], "solve", "casket", "map")
    assert ok and "already" in msg


def test_declared_interaction(story):
    state = _fresh(story)
    state["emotions"] = ["tired"]
    events, msg, ok = referee.resolve(story, state, story["rooms"][1], story["rooms"], "sleep", "pallet")
    assert ok
    for e in events:
        st.apply_event(state, e)
    assert state["flags"]["rested"] is True
    assert "tired" not in state["emotions"]


def test_dice_roll_journals_and_once_gates(story):
    # Dice (2026-08-03): the rolled value is a journal event — replay never
    # re-rolls; once:true spends the chance permanently.
    import random
    room = {"id": 99, "title": "T", "template": "t", "exits": [],
            "objects": {"gate": {"desc": "iron", "interactions": {
                "break": {"roll": {"sides": 100, "beat": 60, "once": True,
                                   "success": {"message": "open", "set": {"gate_open": True}},
                                   "failure": {"message": "held"}}}}}}}
    state = _fresh(story)
    random.seed(7)
    events, msg, ok = referee.resolve(story, state, room, {}, "break", "gate")
    assert ok and events and events[0]["event"] == "rolled"
    assert "Rolled" in msg and str(events[0]["value"]) in msg
    for ev in events:
        st.apply_event(state, ev)
    # Keys are room-scoped since 2026-08-05 (finding 3.2) — an unscoped
    # 'gate:break' would spend the chance in every room that has a gate.
    assert "99:gate:break" in state["rolled"]
    events2, msg2, ok2 = referee.resolve(story, state, room, {}, "break", "gate")
    assert not ok2 and not events2 and "spent" in msg2
    # ...and the SAME object/verb in another room is its own chance.
    other = dict(room, id=100)
    events3, _msg3, ok3 = referee.resolve(story, state, other, {}, "break", "gate")
    assert ok3 and events3[0]["event"] == "rolled"


def test_legacy_unscoped_roll_key_still_gates(story):
    """Journals written before room scoping keep their spent-chance."""
    room = {"id": 99, "title": "T", "template": "t", "exits": [],
            "objects": {"gate": {"desc": "iron", "interactions": {
                "break": {"roll": {"sides": 100, "beat": 60, "once": True,
                                   "success": {"message": "open"},
                                   "failure": {"message": "held"}}}}}}}
    state = _fresh(story)
    st.apply_event(state, {"event": "rolled", "target": "gate", "verb": "break",
                           "value": 80, "beat": 60, "sides": 100, "success": True})
    assert "gate:break" in state["rolled"]          # no room → legacy key
    _ev, msg, ok = referee.resolve(story, state, room, {}, "break", "gate")
    assert not ok and "spent" in msg


def _sealed_room():
    return {"id": 98, "title": "T", "template": "t", "exits": [],
            "objects": {"chest": {"desc": "barnacled", "interactions": {
                "open": {"sealed": {"ask": "What's inside?",
                                    "fallback": "old charts"},
                         "set": {"chest_opened": True},
                         "aliases": ["pry"]}}}}}


def test_sealed_holds_until_player_writes(story):
    # Sealed blanks (2026-08-04): unfilled = the act HOLDS honestly — even
    # with a fallback (Skip is the only road to it; a fast open must not
    # burn the blank before the player's shot). The held attempt journals
    # so the client can re-raise the popup with urgency.
    room = _sealed_room()
    state = _fresh(story)
    events, msg, ok = referee.resolve(story, state, room, {}, "open", "chest")
    # target/verb/wait ride the event since the live wait (2026-08-06) —
    # session.act needs them to block-and-re-resolve without a spec re-walk.
    assert not ok and events == [{"event": "seal_held", "key": "98:chest:open",
                                  "target": "chest", "verb": "open", "wait": None}]
    assert "player" in msg and "charts" not in msg      # honest, never the content


def test_seals_do_not_collide_across_rooms(story):
    """'chest:open' in two rooms used to share ONE seal — room 1's words
    surfaced as room 5's surprise (finding 3.2)."""
    room_a, room_b = _sealed_room(), dict(_sealed_room(), id=97)
    state = _fresh(story)
    st.apply_event(state, {"event": "sealed",
                           "key": referee.seal_key(room_a, "chest", "open"),
                           "text": "room A's secret"})
    _ev, msg_a, ok_a = referee.resolve(story, state, room_a, {}, "open", "chest")
    assert ok_a and "room A's secret" in msg_a
    # Room B's identically-named blank is still the player's to write.
    ev_b, msg_b, ok_b = referee.resolve(story, state, room_b, {}, "open", "chest")
    assert not ok_b and ev_b[0]["event"] == "seal_held"
    assert "room A's secret" not in msg_b


def test_object_names_match_forgivingly(story):
    """Our own tap-to-draft prettifies 'crew-gate' → 'crew gate', and
    capitalized keys were mechanically dead forever (finding 4.1)."""
    room = {"id": 96, "title": "T", "template": "t", "exits": [],
            "objects": {"Crew-Gate": {"desc": "iron", "interactions": {
                "Force_Open": {"message": "it groans open",
                               "set": {"gate_open": True}}}}}}
    state = _fresh(story)
    for spelling in ("crew gate", "Crew-Gate", "crew_gate", "CREW GATE"):
        events, _msg, ok = referee.resolve(story, state, room, {}, "force open", spelling)
        assert ok, f"{spelling!r} should resolve"
        assert {"event": "state_set", "key": "gate_open", "value": True} in events
        # Journals under the AUTHOR's key, not the player's spelling.
        assert events[0]["target"] == "Crew-Gate"


def test_string_aliases_do_not_become_letter_verbs(story):
    """'aliases': 'drink' iterated per character and registered d, r, i, n, k
    as verbs (finding 4.13)."""
    room = {"id": 95, "title": "T", "template": "t", "exits": [],
            "objects": {"cup": {"desc": "tin", "interactions": {
                "sip": {"message": "you sip", "aliases": "drink"}}}}}
    state = _fresh(story)
    _ev, _msg, ok = referee.resolve(story, state, room, {}, "drink", "cup")
    assert ok, "the whole string should work as one alias"
    _ev2, msg2, ok2 = referee.resolve(story, state, room, {}, "d", "cup")
    assert "No mechanical hook" in msg2, "single letters must not be verbs"


def test_sealed_reveals_player_text_verbatim(story):
    room = _sealed_room()
    state = _fresh(story)
    st.apply_event(state, {"event": "sealed", "key": "chest:open",
                           "text": "a brass time machine, pocket-watch sized"})
    events, msg, ok = referee.resolve(story, state, room, {}, "pry", "chest")  # alias works
    assert ok and "a brass time machine, pocket-watch sized" in msg
    assert "player" in msg                               # origin framing
    kinds = [e["event"] for e in events]
    assert kinds == ["interacted", "revealed", "state_set"]  # effects still fire
    for e in events:
        st.apply_event(state, e)
    assert state["flags"]["chest_opened"] is True
    # Second open: canon echo, no re-fire (no events)
    events2, msg2, ok2 = referee.resolve(story, state, room, {}, "open", "chest")
    assert ok2 and not events2 and "Already revealed" in msg2 and "time machine" in msg2


def test_sealed_skip_uses_author_fallback(story):
    room = _sealed_room()
    state = _fresh(story)
    st.apply_event(state, {"event": "seal_skipped", "key": "chest:open"})
    events, msg, ok = referee.resolve(story, state, room, {}, "open", "chest")
    assert ok and "old charts" in msg and "author" in msg


def test_sealed_replay_and_revert_law(tmp_saves):
    # Same law as dice: the fill is journaled at typing time; replay never
    # re-prompts, and revert to before the fill re-opens the blank.
    chat = "sealtest"
    st.append("goblin-den", chat, {"event": "started", "story": "goblin-den", "room": 1, "turn": 0})
    st.append("goblin-den", chat, {"event": "sealed", "key": "chest:open", "text": "rare pepes", "turn": 2})
    st.append("goblin-den", chat, {"event": "revealed", "key": "chest:open", "turn": 3})
    state = st.replay("goblin-den", chat)
    assert state["seals"]["chest:open"] == "rare pepes"
    assert "chest:open" in state["revealed"]
    st.truncate("goblin-den", chat, 1)
    state = st.replay("goblin-den", chat)
    assert state["seals"] == {} and state["revealed"] == []   # blank re-opened


def test_sealed_never_leaks_into_ghost_block(story):
    # The entire mechanic: content reaches her ONLY through the reveal.
    story2 = {**story, "rooms": {**story["rooms"], 98: _sealed_room()}}
    state = _fresh(story2)
    state["room"] = 98
    st.apply_event(state, {"event": "sealed", "key": "chest:open", "text": "SECRETPAYLOAD"})
    block = render.ghost_block(story2, state, story2["rooms"][98])
    assert "SECRETPAYLOAD" not in block
    assert "What's inside?" not in block                 # the ask stays player-side
    assert "old charts" not in block                     # fallback too


def test_undeclared_verb_licenses_improvisation(story):
    # Off-script acts are a LICENSE, not a refusal (bracelet-overboard ruling
    # 2026-08-03): ok=True, no events, narrate-freely message + the hooks.
    state = _fresh(story)
    events, msg, ok = referee.resolve(story, state, story["rooms"][1], story["rooms"], "burn", "pallet")
    assert ok and not events and "narrate" in msg and "sleep" in msg


# ── Journal + replay ─────────────────────────────────────────────────────────

def test_journal_replay_and_revert(tmp_saves, story):
    chat = "testchat"
    st.append("goblin-den", chat, {"event": "started", "story": "goblin-den", "room": 1, "turn": 0})
    st.append("goblin-den", chat, {"event": "turn_tick", "turn": 1})
    st.append("goblin-den", chat, {"event": "found", "target": "loose_stone", "item": "rusty_key", "turn": 1})
    st.append("goblin-den", chat, {"event": "turn_tick", "turn": 2})
    st.append("goblin-den", chat, {"event": "moved", "to": 2, "turn": 2})
    st.append("goblin-den", chat, {"event": "turn_tick", "turn": 3})

    state = st.replay("goblin-den", chat)
    assert state["room"] == 2 and state["turn"] == 3
    assert "rusty_key" in state["inventory"]
    assert state["turns_in_room"] == 1  # reset on move, one tick after

    dropped = st.truncate("goblin-den", chat, 1)
    assert dropped == 3
    state = st.replay("goblin-den", chat)
    assert state["room"] == 1 and state["turn"] == 1
    assert "rusty_key" in state["inventory"]  # found at turn 1, survives
    # Dead branch archived
    assert (st.journal_path("goblin-den", chat).with_suffix(".reverted.jsonl")).exists()


def test_replay_is_pure_and_repeatable(tmp_saves):
    chat = "purity"
    for i in range(1, 501):
        st.append("goblin-den", chat, {"event": "turn_tick", "turn": i})
    a = st.replay("goblin-den", chat)
    b = st.replay("goblin-den", chat)
    assert a == b and a["turn"] == 500


# ── Render rails ─────────────────────────────────────────────────────────────

def test_ghost_block_never_leaks_solution(story):
    state = _fresh(story)
    state["room"] = 3
    block = render.ghost_block(story, state, story["rooms"][3])
    assert "map" not in block.lower().replace("mountains", "")  # solution absent
    assert "cities" in block  # riddle text present
    assert "Turn: 0" in block


def test_ghost_block_hides_unfound_hidden_objects(story):
    state = _fresh(story)
    block = render.ghost_block(story, state, story["rooms"][1])
    assert "loose_stone" not in block
    state["found"].append("loose_stone")
    block = render.ghost_block(story, state, story["rooms"][1])
    assert "loose_stone" in block


def test_ghost_hint_gated_on_turns_in_room(story):
    state = _fresh(story)
    block = render.ghost_block(story, state, story["rooms"][1])
    assert "Hint" not in block
    state["turns_in_room"] = 4
    block = render.ghost_block(story, state, story["rooms"][1])
    assert "Hint" in block and "torchlight" in block


def test_ghost_block_under_cap(story):
    state = _fresh(story)
    state["found"] = ["loose_stone"]
    state["inventory"] = ["rusty_key", "moonstone_amulet"]
    state["turns_in_room"] = 10
    for rid in (1, 2, 3):
        block = render.ghost_block(story, state, story["rooms"][rid])
        assert len(block) <= 2048, f"room {rid} block {len(block)} chars — over the ghost cap"


def test_emotion_piece_resolution():
    comps = {"emotions": {
        "story_goblin-den_dread": "story-specific dread",
        "story_engine_dread": "generic dread",
        "story_engine_wary": "generic wary",
    }}
    assert render.resolve_emotion_piece("goblin-den", "dread", comps)[1] == "story-specific dread"
    assert render.resolve_emotion_piece("goblin-den", "wary", comps)[1] == "generic wary"
    assert render.resolve_emotion_piece("goblin-den", "nonexistent", comps) is None


# ── Titanic-scene dynamics (2026-07-14 additions) ────────────────────────────

def test_adjust_effect_numeric_stats():
    state = st.initial_state()
    st.apply_event(state, {"event": "state_set", "key": "jack_hp", "value": 10})
    st.apply_event(state, {"event": "state_adjust", "key": "jack_hp", "delta": -2})
    assert state["flags"]["jack_hp"] == 8
    st.apply_event(state, {"event": "state_adjust", "key": "gold", "delta": 5})
    assert state["flags"]["gold"] == 5  # adjust on unset key starts from 0


def test_effect_events_adjust():
    events = referee._effect_events({"adjust": {"hp": -1}}, st.initial_state())
    assert events == [{"event": "state_adjust", "key": "hp", "delta": -1}]


def test_forgiving_answer_matching():
    assert referee._norm_answer("  A Map! ") == "map"
    assert referee._norm_answer("The  MAP") == "map"
    puzzle = {"solutions": ["map", "an atlas"]}
    assert referee._norm_answer("Atlas") in referee.answers_of(puzzle)
    assert referee._norm_answer("globe") not in referee.answers_of(puzzle)


def test_blockers_derived_from_locked_exits(story):
    state = _fresh(story)
    state["room"] = 2
    blocked = referee.blockers(story["rooms"][2], state)
    assert len(blocked) == 1 and "iron door" in blocked[0]
    state["inventory"].append("rusty_key")
    assert referee.blockers(story["rooms"][2], state) == []


def test_declared_blockers_until_condition():
    room = {"id": 9, "title": "t", "template": "x", "exits": [],
            "blockers": [{"until": {"flags": {"chose_partner": True}},
                          "text": "choose a dance partner first"}]}
    state = st.initial_state()
    assert referee.blockers(room, state) == ["choose a dance partner first"]
    state["flags"]["chose_partner"] = True
    assert referee.blockers(room, state) == []


def test_ghost_block_shows_blockers(story):
    state = _fresh(story)
    state["room"] = 2
    block = render.ghost_block(story, state, story["rooms"][2])
    assert "Blocked paths:" in block and "iron door" in block


def test_story_prompt_assembled_composition(story):
    comps = {
        "character": {"sapphire": "You are Sapphire, curious and warm."},
        "location": {"story_engine_scene": "inside an interactive story scene"},
        "format": {"story_engine_format": "Use story_act for mechanics."},
        "emotions": {"story_engine_dread": "Dread colors your narration."},
        "extras": {"olde_english": "Speak in olde English."},
    }
    state = _fresh(story)
    state["emotions"] = ["dread"]
    state["extras"] = ["olde_english"]
    text = render.story_prompt(story, state, comps, "sapphire")
    assert text.startswith("You are Sapphire")          # character FIRST — persona preserved
    assert "interactive story scene" in text            # story-owned location
    assert story["meta"]["premise"][:30] in text        # premise as scenario
    assert "story_act for mechanics" in text            # format = operating manual
    assert "olde English" in text                       # extras layer in
    assert "Dread colors" in text                       # emotions layer in


def test_restart_after_end_resets_state(tmp_saves):
    """The 'story won't turn back on' bug (2026-07-14): an old 'ended' event
    must not poison a later playthrough in the same journal."""
    chat = "story"
    st.append("goblin-den", chat, {"event": "started", "story": "goblin-den", "room": 1, "turn": 0})
    st.append("goblin-den", chat, {"event": "gained", "item": "rusty_key", "turn": 1})
    st.append("goblin-den", chat, {"event": "ended", "turn": 1})
    st.append("goblin-den", chat, {"event": "started", "story": "goblin-den", "room": 1, "turn": 0})
    state = st.replay("goblin-den", chat)
    assert state["ended"] is False
    assert state["inventory"] == []  # fresh run, old loot gone
    assert state["room"] == 1
