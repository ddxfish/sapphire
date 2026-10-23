# Open-mansion v1 — Mad-Libs slots, object visibility conditions, the user
# layer (placed objects + room-text overrides), object sets, presets.
# Plan: tmp/open-mansion-plan.md. Fixture pack: fixtures/stories/mad-manse.
import sys
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).parent.parent
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

from gameroom_story import rooms, referee, render, session, state as st  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures" / "stories"
CHAT = "manse-test-chat"


class FakeStore:
    def __init__(self):
        self.d = {}

    def get(self, k):
        return self.d.get(k)

    def save(self, k, v):
        self.d[k] = v


@pytest.fixture
def story(monkeypatch):
    monkeypatch.setattr(rooms, "_story_roots", lambda: [FIXTURES])
    return rooms.load_story("mad-manse")


@pytest.fixture
def cfg_store(monkeypatch):
    fake = FakeStore()
    monkeypatch.setattr(session, "_cfg_store", fake, raising=False)
    from core.plugin_loader import plugin_loader
    monkeypatch.setattr(plugin_loader, "get_plugin_state", lambda name: fake)
    return fake


def _fresh(story):
    state = st.initial_state()
    state["room"] = story["meta"]["start"]
    return state


# ── flag_gte condition ───────────────────────────────────────────────────────

def test_did_and_solved_conditions():
    # Editor pair (2026-08-20): has_opened → {did}, password → {solved}.
    state = st.initial_state()
    assert not referee.check_condition({"did": "door1"}, state)
    state["used"].append("door1")
    assert referee.check_condition({"did": "Door 1"}, state)    # forgiving
    assert not referee.check_condition({"solved": "vault"}, state)
    state["solved"].append("vault")
    assert referee.check_condition({"solved": "VAULT"}, state)   # case/sep only


def test_password_gates_verb_via_solved(story):
    # The exact spec the Add Object form compiles (the flip, 2026-08-21:
    # object-level Riddle section + 'riddle solved' req row on an action):
    # puzzle on the object + {solved} condition on its verbs. No flag wiring.
    room = {"id": 9, "title": "Vault room", "objects": {
        "vault": {"desc": "A steel vault.",
                  "puzzle": {"riddle": "It wants a code.", "solution": "1234"},
                  "interactions": {"open": {
                      "message": "It swings wide.",
                      "condition": {"solved": "vault"},
                      "blocked_message": "The dial spins uselessly.",
                      "set": {"vault_opened": True}}}}}}
    state = st.initial_state()
    state["room"] = 9
    _, msg, ok = referee.resolve(story, state, room, {9: room}, "open", "vault")
    assert not ok and "dial spins" in msg
    events, msg, ok = referee.resolve(story, state, room, {9: room},
                                      "solve", "vault", answer="1234")
    assert ok
    for ev in events:
        st.apply_event(state, ev)
    events, msg, ok = referee.resolve(story, state, room, {9: room}, "open", "vault")
    assert ok and "swings wide" in msg
    assert any(e["event"] == "state_set" and e["key"] == "vault_opened"
               for e in events)


def test_ghost_block_self_budgets_to_cap(story):
    # Core truncates ghost contributions blind at 2048 (qwen finding,
    # 2026-08-21) — the block must fit ITSELF, mechanics intact.
    room = {"id": 7, "title": "Big hall",
            "template": "x" * 890,
            "exits": [{"label": f"door {i}", "to": i, "desc": "d" * 40,
                       "condition": {"flag": "no"},
                       "blocked_message": "m" * 80} for i in range(2, 10)],
            "objects": {f"obj_{i}": {"desc": "o" * 60,
                                     "interactions": {"poke": {"message": "p"}}}
                        for i in range(8)},
            "ghost_hints": [{"after_turns": 0, "whisper": "w" * 200}]}
    state = st.initial_state()
    state["room"] = 7
    block = render.ghost_block(rooms.load_story("mad-manse"), state, room)
    assert len(block) <= 2048
    for must in ("Exits:", "Objects:", "Inventory:"):
        assert must in block                      # mechanics never cut
    assert "obj_7" in block                       # objects list intact


def test_blockers_dedupe_same_message(story):
    # Six lockboxed doors read as ONE fact (Krem 2026-08-20).
    room = {"id": 7, "exits": [
        {"label": "the library", "to": 2, "condition": {"flag": "x"},
         "blocked_message": "A steel lockbox hangs on the handle."},
        {"label": "the kitchen", "to": 3, "condition": {"flag": "x"},
         "blocked_message": "A steel lockbox hangs on the handle."},
        {"label": "the stair", "to": 4, "condition": {"flag": "x"},
         "blocked_message": "A velvet rope closes the stair."}]}
    out = referee.blockers(room, st.initial_state())
    assert out == [
        "'the library', 'the kitchen' — A steel lockbox hangs on the handle.",
        "'the stair' — A velvet rope closes the stair."]


def test_take_moves_object_to_inventory(story):
    room = {"id": 8, "title": "Table room", "objects": {
        "bronze_key": {"desc": "A small key.", "takeable": True},
        "lamp": {"desc": "A lamp.", "takeable": True},
        "vase": {"desc": "A vase."}}}
    state = st.initial_state()
    state["room"] = 8
    events, msg, ok = referee.resolve(story, state, room, {8: room},
                                      "take", "bronze_key")
    assert ok and events == [{"event": "taken", "target": "bronze_key"}]
    for ev in events:
        st.apply_event(state, ev)
    assert "bronze_key" in state["inventory"]
    assert "bronze_key" not in referee._visible_objects(room, state)
    assert referee.check_condition({"has": "bronze_key"}, state)
    # grab is an alias
    events, _, ok = referee.resolve(story, state, room, {8: room}, "grab", "lamp")
    assert ok and events[0]["event"] == "taken"
    # non-takeable falls to the off-script license (narrate freely, no events)
    events, msg, ok = referee.resolve(story, state, room, {8: room}, "take", "vase")
    assert ok and not events and "narrate" in msg
    # declared take interactions COMPOSE with the pocket (2026-08-23 ruling —
    # the look-shadow's mirror): the authored line speaks AND it's taken
    room["objects"]["coin"] = {"desc": "A coin.", "takeable": True,
                               "interactions": {"take": {"message": "It bites you!"}}}
    events, msg, ok = referee.resolve(story, state, room, {8: room}, "take", "coin")
    assert ok and "bites" in msg
    assert any(e["event"] == "taken" and e["target"] == "coin" for e in events)


def test_flag_gte_threshold():
    state = st.initial_state()
    cond = {"flag_gte": {"love": 50}}
    assert not referee.check_condition(cond, state)          # absent → 0
    state["flags"]["love"] = 49
    assert not referee.check_condition(cond, state)
    state["flags"]["love"] = 50
    assert referee.check_condition(cond, state)


def test_flag_gte_junk_threshold_stays_closed():
    state = st.initial_state()
    state["flags"]["love"] = 99
    assert not referee.check_condition({"flag_gte": {"love": "plenty"}}, state)


# ── Object visibility conditions (the zork-line) ────────────────────────────

def test_gated_object_does_not_exist_before_flag(story):
    state = _fresh(story)
    hall = story["rooms"][1]
    assert "ghost_bell" not in referee._visible_objects(hall, state)
    assert "ghost_bell" not in render.ghost_block(story, state, hall)
    _, msg, ok = referee.resolve(story, state, hall, story["rooms"],
                                 "ring", "ghost_bell")
    assert "tracked" in msg           # off-script: nothing by that name
    _, msg, _ = referee.resolve(story, state, hall, story["rooms"], "search")
    assert "ghost_bell" not in msg


def test_gated_object_materializes_on_flag(story):
    state = _fresh(story)
    state["flags"]["chest_opened"] = True
    hall = story["rooms"][1]
    assert "ghost_bell" in referee._visible_objects(hall, state)
    assert "ghost_bell" in render.ghost_block(story, state, hall)
    events, msg, ok = referee.resolve(story, state, hall, story["rooms"],
                                      "ring", "ghost_bell")
    assert ok and "answers" in msg


def test_flag_gte_gated_object(story):
    state = _fresh(story)
    hall = story["rooms"][1]
    assert "warm_feeling" not in referee._visible_objects(hall, state)
    state["flags"]["love"] = 60
    assert "warm_feeling" in referee._visible_objects(hall, state)


# ── look room ────────────────────────────────────────────────────────────────

def test_look_room_reads_current_truth(story):
    state = _fresh(story)
    hall = story["rooms"][1]
    for target in (None, "room", "around"):
        _, msg, ok = referee.resolve(story, state, hall, story["rooms"],
                                     "look", target)
        assert ok and "The Hall" in msg and "chest" in msg and "Exits" in msg
        assert "ghost_bell" not in msg      # still gated


def test_look_named_object_still_works(story):
    state = _fresh(story)
    _, msg, ok = referee.resolve(story, state, story["rooms"][1],
                                 story["rooms"], "look", "chest")
    assert ok and "brass dial" in msg


# ── Slots: declaration, cleaning, substitution ──────────────────────────────

def test_story_slots_normalized(story):
    slots = rooms.story_slots(story["meta"])
    keys = [s["key"] for s in slots]
    assert keys == ["relationship", "watchword", "combo"]   # malformed dropped
    rel = slots[0]
    assert rel["default"] == "partner" and rel["options"] == ["partner", "rival"]
    assert slots[2]["sealed"] and slots[2]["seal_key"] == "1:chest:open"
    # Layout hints (2026-08-20): section/width pass through, width clamps,
    # an explicitly EMPTY label survives (section header carries it).
    assert rel["section"] == "Characters" and rel["width"] == 30
    assert slots[1]["label"] == "" and slots[1]["width"] == 100   # 999 clamped
    assert slots[2]["section"] == "" and slots[2]["width"] == 100  # defaults


def test_clean_slots_defaults_and_hygiene(story):
    vals = session._clean_slots(story, {"relationship": " {ai_name} wife ",
                                        "unknown": "x", "combo": "1234"})
    assert vals["relationship"] == "ai_name wife"       # braces stripped
    assert vals["watchword"] == "lantern"               # default fills
    assert "unknown" not in vals and "combo" not in vals  # sealed excluded


def test_clean_slots_cap_scales_with_rows():
    # Long-text slots (rows>0 → textarea: scenario, goals) hold real prose —
    # the flat 1000 cap silently amputated them at entry (Krem 2026-08-23).
    s = {"meta": {"slots": [
        {"key": "scenario", "rows": 6},
        {"key": "name"},
    ]}}
    vals = session._clean_slots(s, {"scenario": "x" * 9000, "name": "y" * 9000})
    assert len(vals["scenario"]) == 8000    # textarea slots: room to breathe
    assert len(vals["name"]) == 1000        # one-liners keep the tight cap


def test_apply_slots_substitutes_everywhere(story):
    session._apply_slots(story, {"relationship": "wife", "watchword": "ember"})
    assert story["meta"]["role"]["text"] == \
        "I am Vex, your wife. Our watchword is ember."
    assert "as wife" in story["meta"]["premise"]
    assert "My wife stands" in story["rooms"][1]["template"]
    assert "whispered ember" in story["rooms"][2]["template"]
    assert story["meta"]["role"]["name"] == "Vex"       # literal names untouched


def test_role_assembled_from_slots(story):
    # The setup-popup-IS-the-assembled-prompt pattern (Krem 2026-08-20):
    # role name/text may be slot tokens — the form output becomes the costume.
    story["meta"]["role"] = {"name": "{ai_character}", "text": "{ai_backstory}"}
    session._apply_slots(story, {"ai_character": "Vera",
                                 "ai_backstory": "A runaway circus walrus, an alien, an AI, a time traveler."})
    assert story["meta"]["role"] == {
        "name": "Vera",
        "text": "A runaway circus walrus, an alien, an AI, a time traveler."}


# ── User layer: placed objects + room text ──────────────────────────────────

def test_user_object_merges_and_acts(story):
    msg, ok = session.upsert_user_object(
        CHAT, "mad-manse", 1, "rare_pepes",
        {"desc": "A display case of rare pepes.",
         "interactions": {"look_inside": {"message": "You see a paper..."}}})
    assert ok
    fresh = rooms.load_story("mad-manse")
    state = _fresh(fresh)
    session._merge_user_layer(fresh, "mad-manse", CHAT, state)
    hall = fresh["rooms"][1]
    assert "rare_pepes" in hall["objects"]
    events, msg, ok = referee.resolve(fresh, state, hall, fresh["rooms"],
                                      "look_inside", "rare_pepes")
    assert ok and "You see a paper" in msg


def test_shadow_merges_over_shipped(story):
    # Shadow law (editor v2, 2026-08-20): a user entry on a shipped name
    # field-merges — desc replaces, verb messages overlay, MECHANICS SURVIVE.
    session.upsert_user_object(CHAT, "mad-manse", 1, "chest",
                               {"desc": "a neon party chest",
                                "interactions": {"open": {"message": "Confetti!"},
                                                 "kick": {"message": "Ow."}}})
    fresh = rooms.load_story("mad-manse")
    state = _fresh(fresh)
    session._merge_user_layer(fresh, "mad-manse", CHAT, state)
    chest = fresh["rooms"][1]["objects"]["chest"]
    assert chest["desc"] == "a neon party chest"
    assert chest["interactions"]["open"]["message"] == "Confetti!"
    assert chest["interactions"]["open"]["set"] == {"chest_opened": True}  # kept
    assert chest["interactions"]["kick"]["message"] == "Ow."               # added
    session.delete_user_object(CHAT, "mad-manse", 1, "chest")


def test_tombstone_removes_shipped_and_restores(story):
    session.upsert_user_object(CHAT, "mad-manse", 1, "chest", {"_removed": True})
    fresh = rooms.load_story("mad-manse")
    session._merge_user_layer(fresh, "mad-manse", CHAT, _fresh(fresh))
    assert "chest" not in fresh["rooms"][1]["objects"]
    session.delete_user_object(CHAT, "mad-manse", 1, "chest")   # restore
    fresh2 = rooms.load_story("mad-manse")
    session._merge_user_layer(fresh2, "mad-manse", CHAT, _fresh(fresh2))
    assert fresh2["rooms"][1]["objects"]["chest"]["desc"] == \
        "An old chest with a brass dial."


def test_object_replace_shadow_wholesale(story):
    # Fidelity-gate lane (2026-08-21): _replace swaps the shipped object
    # wholesale at merge — machinery included; restore lifts it.
    session.upsert_user_object(CHAT, "mad-manse", 1, "chest",
                               {"_replace": True, "desc": "a plain crate",
                                "interactions": {"open": {"message": "It creaks open."}}})
    fresh = rooms.load_story("mad-manse")
    session._merge_user_layer(fresh, "mad-manse", CHAT, _fresh(fresh))
    chest = fresh["rooms"][1]["objects"]["chest"]
    assert chest["desc"] == "a plain crate"
    assert "_replace" not in chest
    assert set(chest["interactions"]) == {"open"}
    assert chest["interactions"]["open"] == {"message": "It creaks open."}
    session.delete_user_object(CHAT, "mad-manse", 1, "chest")
    fresh2 = rooms.load_story("mad-manse")
    session._merge_user_layer(fresh2, "mad-manse", CHAT, _fresh(fresh2))
    assert fresh2["rooms"][1]["objects"]["chest"]["desc"] == \
        "An old chest with a brass dial."


def test_added_exits_ungated_and_deduped(story):
    # F2 (2026-08-21): user exits merge like objects — no implicit zork
    # gate; authors gate explicitly via visible_when. Dedup-by-to holds.
    session.set_room_text(CHAT, "mad-manse", 1,
                          add_exits=[{"label": "the parlor again", "to": 2},
                                     {"label": "the void", "to": 99}])
    fresh = rooms.load_story("mad-manse")
    state = _fresh(fresh)                                # pre-line: flag unset
    session._merge_user_layer(fresh, "mad-manse", CHAT, state)
    exits = fresh["rooms"][1]["exits"]
    assert len(exits) == 2                               # UNGATED; to=2 deduped
    assert exits[-1] == {"label": "the void", "to": 99}
    session.set_room_text(CHAT, "mad-manse", 1, add_exits=[])


def test_exit_shadow_tombstone_restore(story):
    # Exits editor (2026-08-21): shadow text over a shipped door, mechanics
    # ride; tombstone walls it off; clearing the shadow restores the pack.
    session.set_exit_shadow(CHAT, "mad-manse", 1, 2, {"label": "the red door"})
    fresh = rooms.load_story("mad-manse")
    session._merge_user_layer(fresh, "mad-manse", CHAT, _fresh(fresh))
    ex = fresh["rooms"][1]["exits"][0]
    assert ex["label"] == "the red door" and ex["to"] == 2
    session.set_exit_shadow(CHAT, "mad-manse", 1, 2, {"_removed": True})
    fresh2 = rooms.load_story("mad-manse")
    session._merge_user_layer(fresh2, "mad-manse", CHAT, _fresh(fresh2))
    assert fresh2["rooms"][1]["exits"] == []
    session.set_exit_shadow(CHAT, "mad-manse", 1, 2, None)   # restore
    fresh3 = rooms.load_story("mad-manse")
    session._merge_user_layer(fresh3, "mad-manse", CHAT, _fresh(fresh3))
    assert fresh3["rooms"][1]["exits"][0]["to"] == 2


def test_exit_shadow_mechanics_replace(story):
    # Mechanics shadow (2026-08-21): a shadow carrying a `mechanics` unit
    # REPLACES the shipped door's machinery wholesale — {} strips it bare,
    # a dict swaps it; text fields keep field-merging beside it.
    session.set_exit_shadow(CHAT, "mad-manse", 1, 2, {"mechanics": {}})
    fresh = rooms.load_story("mad-manse")
    state = _fresh(fresh)                            # chest_opened unset
    session._merge_user_layer(fresh, "mad-manse", CHAT, state)
    ex = fresh["rooms"][1]["exits"][0]
    assert "condition" not in ex                     # stripped: door swings free
    assert referee.blockers(fresh["rooms"][1], state) == []
    session.set_exit_shadow(CHAT, "mad-manse", 1, 2,
                            {"label": "the bone door",
                             "mechanics": {"condition": {"has": "skeleton_key"}}})
    fresh2 = rooms.load_story("mad-manse")
    state2 = _fresh(fresh2)
    session._merge_user_layer(fresh2, "mad-manse", CHAT, state2)
    ex2 = fresh2["rooms"][1]["exits"][0]
    assert ex2["label"] == "the bone door"
    assert ex2["condition"] == {"has": "skeleton_key"}   # replaced, not merged
    assert referee.blockers(fresh2["rooms"][1], state2)  # new lock bites
    session.set_exit_shadow(CHAT, "mad-manse", 1, 2, None)


def test_visible_when_gates_exits(story):
    # F3: an exit with visible_when doesn't EXIST until the condition holds
    # — not listed, not traversable, never a blocker.
    room = {"id": 7, "title": "Hall", "exits": [
        {"label": "the stair", "to": 2},
        {"label": "shimmer door", "to": 3,
         "visible_when": {"flag": "portal_up"}}]}
    state = st.initial_state()
    state["room"] = 7
    assert [e["label"] for e in referee.visible_exits(room, state)] == ["the stair"]
    _, msg, ok = referee.resolve(story, state, room, {7: room},
                                 "move", "shimmer door")
    assert not ok and "No exit matches" in msg
    assert referee.blockers(room, state) == []           # hidden ≠ blocked
    state["flags"]["portal_up"] = True
    assert len(referee.visible_exits(room, state)) == 2
    dest = {"id": 3, "title": "Beyond", "exits": []}
    _, msg, ok = referee.resolve(story, state, room, {7: room, 3: dest},
                                 "move", "shimmer door")
    assert ok and "Beyond" in msg


def test_exit_dice_and_effects(story, monkeypatch):
    # F1: roll gates traversal — value journaled either way, branch effects
    # fire, per-exit effects ride a successful traverse before on_enter.
    room = {"id": 7, "title": "Ledge", "exits": [
        {"label": "rickety bridge", "to": 3,
         "roll": {"sides": 20, "beat": 11,
                  "failure": {"message": "A plank gives way."}},
         "effects": {"set": {"bridge_crossed": True}}}]}
    dest = {"id": 3, "title": "Far side", "exits": [],
            "on_enter": {"set": {"arrived": True}}}
    state = st.initial_state()
    state["room"] = 7
    monkeypatch.setattr(referee.random, "randint", lambda a, b: 4)
    events, msg, ok = referee.resolve(story, state, room, {7: room, 3: dest},
                                      "move", "rickety bridge")
    assert not ok and "plank gives way" in msg
    assert any(e["event"] == "rolled" and not e["success"] for e in events)
    assert not any(e["event"] == "moved" for e in events)
    monkeypatch.setattr(referee.random, "randint", lambda a, b: 17)
    events, msg, ok = referee.resolve(story, state, room, {7: room, 3: dest},
                                      "move", "rickety bridge")
    assert ok and "Rolled 17" in msg and "Far side" in msg
    kinds = [(e["event"], e.get("key")) for e in events]
    assert ("moved", None) in kinds
    assert ("state_set", "bridge_crossed") in kinds      # per-exit effects
    assert ("state_set", "arrived") in kinds             # dest on_enter


def test_apply_scenario_env_never_stamps(story, cfg_store):
    # Verbatim law: neither shadows nor new names gain a condition on load.
    cfg_store.save("storyscenarios:mad-manse",
                   {"mix": {"objects": {"1": {"chest": {"desc": "gilded"},
                                              "lamp": {"desc": "a lamp"}}}}})
    session._apply_scenario_env(CHAT, "mad-manse", story, "mix")
    layer = st.get_user_layer("mad-manse", CHAT)["objects"]["1"]
    assert "condition" not in layer["chest"]
    assert "condition" not in layer["lamp"]
    session.delete_user_object(CHAT, "mad-manse", 1, "chest")
    session.delete_user_object(CHAT, "mad-manse", 1, "lamp")


def test_room_text_override_gated_by_zork_line(story):
    session.set_room_text(CHAT, "mad-manse", 2,
                          template="A backyard BBQ. Only cheetos remain.")
    fresh = rooms.load_story("mad-manse")
    state = _fresh(fresh)
    session._merge_user_layer(fresh, "mad-manse", CHAT, state)
    assert "parlor" in fresh["rooms"][2]["template"].lower()   # gated: shipped
    state["flags"]["chest_opened"] = True
    fresh2 = rooms.load_story("mad-manse")
    session._merge_user_layer(fresh2, "mad-manse", CHAT, state)
    assert "cheetos" in fresh2["rooms"][2]["template"]


def test_delete_user_object(story):
    session.upsert_user_object(CHAT, "mad-manse", 1, "temp", {"desc": "x"})
    msg, ok = session.delete_user_object(CHAT, "mad-manse", 1, "temp")
    assert ok
    assert (st.get_user_layer("mad-manse", CHAT).get("objects") or {}) \
        .get("1", {}).get("temp") is None


def test_place_object_marks_ai_and_journals(story):
    st.set_active(CHAT, "mad-manse", None)
    st.append("mad-manse", CHAT, {"event": "started", "story": "mad-manse",
                                  "room": 1, "turn": 0})
    msg, ok = session.place_object(None, "the hall", "folded_note",
                                   desc="A folded note.", verb="read",
                                   response="It says: apples.",
                                   session=CHAT)
    assert ok, msg
    layer = st.get_user_layer("mad-manse", CHAT)
    spec = layer["objects"]["1"]["folded_note"]
    assert spec["_author"] == "ai"
    assert any(e.get("event") == "placed"
               for e in st.read_journal("mad-manse", CHAT))
    st.clear_active(CHAT)


# ── Object sets ─────────────────────────────────────────────────────────────

def test_apply_scenario_env_is_pure_swap(story, cfg_store):
    # PURE SWAP (Krem 2026-08-20): canvas BECOMES the scenario VERBATIM —
    # prior content gone, no implicit zork-line stamp (clown_key finding:
    # what you saved is what you get); "" = reset to shipped.
    session.upsert_user_object(CHAT, "mad-manse", 1, "leftover", {"desc": "x"})
    cfg_store.save("storyscenarios:mad-manse", {
        "bbq": {"objects": {"1": {"grill": {"desc": "A hot grill."},
                                  "banner": {"desc": "A banner.",
                                             "condition": {"flag": "own"}}}},
                "rooms": {"2": {"template": "BBQ parlor."}}}})
    session._apply_scenario_env(CHAT, "mad-manse", story, "bbq")
    layer = st.get_user_layer("mad-manse", CHAT)
    assert "leftover" not in layer["objects"]["1"]      # swap, not merge
    assert "condition" not in layer["objects"]["1"]["grill"]   # verbatim
    assert layer["objects"]["1"]["banner"]["condition"] == {"flag": "own"}
    assert layer["rooms"]["2"]["template"] == "BBQ parlor."
    assert layer["scenario"] == "bbq"                   # gear reopens on it
    session.upsert_user_object(CHAT, "mad-manse", 1, "extra", {"desc": "x"})
    assert st.get_user_layer("mad-manse", CHAT)["scenario"] == "bbq"  # survives edits
    session._apply_scenario_env(CHAT, "mad-manse", story, "")
    layer = st.get_user_layer("mad-manse", CHAT)
    assert not layer.get("objects") and not layer.get("rooms")   # reset
    assert layer.get("scenario") == ""


# ── Scenarios (unified: slots + environment, Krem 2026-08-20) ───────────────

def test_scenario_roundtrip_and_ai_filter(story, cfg_store, monkeypatch):
    session.upsert_user_object(CHAT, "mad-manse", 1, "mine", {"desc": "x"},
                               author="player")
    session.upsert_user_object(CHAT, "mad-manse", 1, "hers", {"desc": "y"},
                               author="ai")
    st.set_active(CHAT, "mad-manse", None)
    from routes import story_routes
    monkeypatch.setattr(story_routes, "_system", lambda: None)
    r = story_routes.set_scenario("mad-manse",
                                  body={"name": "snap", "session": CHAT,
                                        "slots": {"relationship": "wife",
                                                  "empty": "  "}})
    assert r["success"], r
    saved = cfg_store.d["storyscenarios:mad-manse"]["snap"]
    assert saved["slots"] == {"relationship": "wife"}     # env + slots in ONE
    assert "mine" in saved["objects"]["1"]
    assert "hers" not in saved["objects"]["1"]            # fork 2: AI excluded
    r = story_routes.set_scenario("mad-manse", body={"name": "snap",
                                                     "delete": True})
    assert r["success"]
    assert cfg_store.d["storyscenarios:mad-manse"] == {}
    st.clear_active(CHAT)
    session.delete_user_object(CHAT, "mad-manse", 1, "mine")
    session.delete_user_object(CHAT, "mad-manse", 1, "hers")


def test_scenario_default_name_reserved(story, cfg_store):
    from routes import story_routes
    r = story_routes.set_scenario("mad-manse", body={"name": "default"})
    assert not r["success"] and "shipped" in r["detail"]


def test_scenario_migration_folds_old_stores(story, cfg_store):
    from routes import story_routes
    cfg_store.save("storypresets:mad-manse",
                   {"ghost-run": {"slots": {"relationship": "wife"}}})
    cfg_store.save("storyobjsets:mad-manse",
                   {"ghost-run": {"objects": {"1": {"orb": {"desc": "o"}}},
                                  "rooms": {}},
                    "bbq-only": {"objects": {}, "rooms": {"2": {"template": "B."}}}})
    scen = story_routes._scenarios(cfg_store, "mad-manse")
    assert scen["ghost-run"]["slots"] == {"relationship": "wife"}   # merged pair
    assert "orb" in scen["ghost-run"]["objects"]["1"]
    assert scen["bbq-only"]["rooms"]["2"]["template"] == "B."
    # A bare READ never writes the store (2026-08-21 fix-wave: the setup
    # GET's side-effect save was the hunt's GET-that-writes finding) —
    # write lanes pass persist=True and land the fold exactly once.
    assert "storyscenarios:mad-manse" not in cfg_store.d
    scen2 = story_routes._scenarios(cfg_store, "mad-manse", persist=True)
    assert scen2 == scen
    assert cfg_store.d["storyscenarios:mad-manse"] == scen          # persisted once


def test_prestart_environment_edits(story, cfg_store, monkeypatch):
    # Pre-start fallback (2026-08-20): the start modal's Environment tab
    # edits the slug+chat user layer BEFORE story/start — an explicit slug
    # resolves when nothing is active; the playthrough merges the rows at
    # load. Without a slug the routes still refuse.
    from routes import story_routes
    monkeypatch.setattr(story_routes, "_system", lambda: None)
    chat = "manse-prestart-chat"
    r = story_routes.set_object(body={"session": chat, "slug": "mad-manse",
                                      "room_id": 1, "name": "welcome_mat",
                                      "spec": {"desc": "a mat"}})
    assert r["success"], r
    world = story_routes.get_objects(query={"session": chat, "slug": "mad-manse"})
    assert world["active"] and world["current_room"] is None
    assert "welcome_mat" in world["objects"]["1"]
    # Author-view room stats (the "In this room" blurb, 2026-08-20):
    # 1-hall ships 1 exit, 3 objects, 2 interactions (chest:open, bell:ring)
    hall = world["rooms"][0]
    assert (hall["exits"], hall["shipped_objects"], hall["shipped_actions"]) == (1, 3, 2)
    # Mechanics ride the rows verbatim (2026-08-21) — the editor's
    # fidelity gate needs the actual machinery, not just a flag.
    assert hall["shipped_exits"][0]["condition"] == {"flag": "chest_opened"}
    assert hall["shipped_objs"]["chest"]["spec"]["desc"] == \
        "An old chest with a brass dial."
    refused = story_routes.get_objects(query={"session": chat})
    assert refused["active"] is False
    bogus = story_routes.get_objects(query={"session": chat, "slug": "no-such"})
    assert bogus["active"] is False


def test_route_shadow_tombstone_restore(story, cfg_store, monkeypatch):
    from routes import story_routes
    monkeypatch.setattr(story_routes, "_system", lambda: None)
    chat = "manse-shadow-chat"
    r = story_routes.set_object(body={"session": chat, "slug": "mad-manse",
                                      "room_id": 1, "name": "chest",
                                      "spec": {"desc": "shadowed"}})
    assert r["success"], r          # shipped names allowed now — shadow path
    r = story_routes.delete_object(body={"session": chat, "slug": "mad-manse",
                                         "room_id": 1, "name": "chest"})
    assert r["success"] and "restorable" in r["detail"]
    assert st.get_user_layer("mad-manse", chat)["objects"]["1"]["chest"]["_removed"]
    r = story_routes.delete_object(body={"session": chat, "slug": "mad-manse",
                                         "room_id": 1, "name": "chest",
                                         "restore": True})
    assert r["success"]
    assert "chest" not in (st.get_user_layer("mad-manse", chat)
                           .get("objects") or {}).get("1", {})


def test_exit_routes_shadow_add_delete(story, cfg_store, monkeypatch):
    from routes import story_routes
    monkeypatch.setattr(story_routes, "_system", lambda: None)
    chat = "manse-exit-chat"
    base = {"session": chat, "slug": "mad-manse", "room_id": 1}
    # shipped destination (1→2) = shadow path, diff-only
    r = story_routes.set_exit(body={**base, "to": 2, "label": "the red door",
                                    "condition": {"has": "key"}})   # no marker: ignored
    assert r["success"], r
    sh = st.get_user_layer("mad-manse", chat)["rooms"]["1"]["exit_shadows"]["2"]
    assert sh == {"label": "the red door"}     # locks shadow only via edit_mechanics
    # verbatim-equals-shipped clears the shadow
    r = story_routes.set_exit(body={**base, "to": 2, "label": "the parlor"})
    assert r["success"]
    assert "exit_shadows" not in (st.get_user_layer("mad-manse", chat)
                                  .get("rooms") or {}).get("1", {})
    # new destination = user exit, full grammar rides verbatim
    r = story_routes.set_exit(body={**base, "to": 3, "label": "hatch",
                                    "visible_when": {"flag": "found_hatch"},
                                    "condition": {"has": "crowbar"},
                                    "effects": {"set": {"below": True}}})
    assert r["success"], r
    ue = st.get_user_layer("mad-manse", chat)["rooms"]["1"]["add_exits"][0]
    assert ue["visible_when"] == {"flag": "found_hatch"}
    assert ue["condition"] == {"has": "crowbar"}
    # guards: self-loop and unknown rooms refused
    assert not story_routes.set_exit(body={**base, "to": 1})["success"]
    assert not story_routes.set_exit(body={**base, "to": 99})["success"]
    # delete: user exit drops; shipped tombstones then restores
    assert story_routes.delete_exit(body={**base, "to": 3})["success"]
    assert not (st.get_user_layer("mad-manse", chat)["rooms"]
                .get("1", {}).get("add_exits"))
    r = story_routes.delete_exit(body={**base, "to": 2})
    assert r["success"] and "restorable" in r["detail"]
    assert st.get_user_layer("mad-manse", chat)["rooms"]["1"]["exit_shadows"]["2"]["_removed"]
    assert story_routes.delete_exit(body={**base, "to": 2, "restore": True})["success"]
    assert "1" not in (st.get_user_layer("mad-manse", chat).get("rooms") or {})


def test_object_route_replace_flag(story, cfg_store, monkeypatch):
    from routes import story_routes
    monkeypatch.setattr(story_routes, "_system", lambda: None)
    chat = "manse-obj-replace-chat"
    base = {"session": chat, "slug": "mad-manse", "room_id": 1}
    # replace flag on a shipped name stamps the wholesale marker
    r = story_routes.set_object(body={**base, "name": "chest", "replace": True,
                                      "spec": {"desc": "a crate"}})
    assert r["success"], r
    ov = st.get_user_layer("mad-manse", chat)["objects"]["1"]["chest"]
    assert ov["_replace"] is True
    # a smuggled marker without the flag is stripped (shadow law holds)
    r = story_routes.set_object(body={**base, "name": "chest",
                                      "spec": {"_replace": True, "desc": "x"}})
    assert r["success"]
    ov = st.get_user_layer("mad-manse", chat)["objects"]["1"]["chest"]
    assert "_replace" not in ov
    # replace flag on a NON-shipped name is inert
    r = story_routes.set_object(body={**base, "name": "crowbar", "replace": True,
                                      "spec": {"desc": "a crowbar"}})
    assert r["success"]
    assert "_replace" not in st.get_user_layer("mad-manse", chat)["objects"]["1"]["crowbar"]


def test_exit_routes_mechanics_shadow(story, cfg_store, monkeypatch):
    # The edit_mechanics marker (2026-08-21): present = the compiled
    # grammar is authoritative and replaces shipped machinery as a unit;
    # absent = any existing mechanics shadow rides forward untouched.
    from routes import story_routes
    monkeypatch.setattr(story_routes, "_system", lambda: None)
    chat = "manse-exit-mech-chat"
    base = {"session": chat, "slug": "mad-manse", "room_id": 1}
    r = story_routes.set_exit(body={**base, "to": 2, "label": "the parlor",
                                    "edit_mechanics": True,
                                    "condition": {"has": "skeleton_key"}})
    assert r["success"], r
    sh = st.get_user_layer("mad-manse", chat)["rooms"]["1"]["exit_shadows"]["2"]
    assert sh == {"mechanics": {"condition": {"has": "skeleton_key"}}}
    # text-only save (no marker) rides the mechanics forward
    r = story_routes.set_exit(body={**base, "to": 2, "label": "the red door"})
    assert r["success"]
    sh = st.get_user_layer("mad-manse", chat)["rooms"]["1"]["exit_shadows"]["2"]
    assert sh == {"label": "the red door",
                  "mechanics": {"condition": {"has": "skeleton_key"}}}
    # marker + empty grammar = door stripped bare ({} is meaningful)
    r = story_routes.set_exit(body={**base, "to": 2, "label": "the red door",
                                    "edit_mechanics": True})
    assert r["success"]
    sh = st.get_user_layer("mad-manse", chat)["rooms"]["1"]["exit_shadows"]["2"]
    assert sh == {"label": "the red door", "mechanics": {}}
    # marker + verbatim-shipped grammar and text = everything clears
    r = story_routes.set_exit(body={**base, "to": 2, "label": "the parlor",
                                    "edit_mechanics": True,
                                    "condition": {"flag": "chest_opened"}})
    assert r["success"]
    assert "1" not in (st.get_user_layer("mad-manse", chat).get("rooms") or {})


def test_user_room_create_merge_travel_delete(story):
    # W2 (2026-08-21): a playthrough-created room is a full citizen —
    # exits reach it, objects live in it, text is UNGATED (it IS the
    # room), and deletion takes its doors and objects with it.
    chat = "manse-room-chat"
    msg, ok, rid = session.create_user_room(chat, "mad-manse", "The Séance Room")
    assert ok and rid >= 100, (msg, rid)
    session.set_room_text(chat, "mad-manse", rid,
                          template="Candles gutter around a bare table.")
    session.set_user_exit(chat, "mad-manse", 1,
                          rid, {"to": rid, "label": "the veiled arch"})
    session.upsert_user_object(chat, "mad-manse", rid, "planchette",
                               {"desc": "A worn planchette."})
    fresh = rooms.load_story("mad-manse")
    state = _fresh(fresh)                                # zork flag UNSET
    session._merge_user_layer(fresh, "mad-manse", chat, state)
    room = fresh["rooms"][rid]
    assert room["title"] == "The Séance Room"
    assert "Candles gutter" in room["template"]          # ungated
    assert "planchette" in room["objects"]
    _, msg2, ok2 = referee.resolve(fresh, state, fresh["rooms"][1],
                                   fresh["rooms"], "move", "the veiled arch")
    assert ok2 and "Séance" in msg2
    # journal is empty here (she never moved) → deletion allowed, and the
    # cleanup sweep takes the arch and the planchette with it
    msg3, ok3 = session.delete_user_room(chat, "mad-manse", rid)
    assert ok3, msg3
    layer = st.get_user_layer("mad-manse", chat)
    assert str(rid) not in (layer.get("rooms") or {})
    assert str(rid) not in (layer.get("objects") or {})
    assert not any(e.get("to") == rid
                   for e in (layer.get("rooms") or {}).get("1", {}).get("add_exits") or [])


def test_user_room_routes_and_guards(story, cfg_store, monkeypatch):
    from routes import story_routes
    monkeypatch.setattr(story_routes, "_system", lambda: None)
    chat = "manse-room-route-chat"
    base = {"session": chat, "slug": "mad-manse"}
    r = story_routes.create_room(body={**base, "title": "Widow's Walk"})
    assert r["success"] and r["id"] >= 100, r
    rid = r["id"]
    # exits validate user rooms at both ends; label defaults to its title
    r2 = story_routes.set_exit(body={**base, "room_id": 1, "to": rid})
    assert r2["success"], r2
    ue = st.get_user_layer("mad-manse", chat)["rooms"]["1"]["add_exits"][0]
    assert ue["label"] == "Widow's Walk"
    r3 = story_routes.set_exit(body={**base, "room_id": rid, "to": 2,
                                     "label": "back down"})
    assert r3["success"], r3
    # world view rows carry the user room
    row = next(x for x in story_routes.get_objects(
        query={"session": chat, "slug": "mad-manse"})["rooms"] if x["id"] == rid)
    assert row["user_room"] and row["title"] == "Widow's Walk"
    assert row["exits"] == 1
    # guards: shipped rooms never delete; she can't stand in a dead room
    assert not story_routes.delete_room(body={**base, "room_id": 1})["success"]
    monkeypatch.setattr(st, "replay", lambda s, c: {"room": rid})
    assert not story_routes.delete_room(body={**base, "room_id": rid})["success"]


def test_look_reaches_carried_objects(story):
    # Krem 2026-08-21 (Sapph's love_letter): a taken object leaves the
    # room's visible set, but look must still find it — here, in another
    # room, and even for bare `gives` tokens with no spec anywhere.
    room = {"id": 7, "title": "Gate", "exits": [], "objects": {
        "love_letter": {"desc": "Folded twice, sealed with wax.",
                        "takeable": True}}}
    far = {"id": 8, "title": "Field", "exits": [], "objects": {}}
    all_rooms = {7: room, 8: far}
    state = st.initial_state()
    state["room"] = 7
    events, msg, ok = referee.resolve(story, state, room, all_rooms,
                                      "take", "love_letter")
    assert ok
    for e in events:
        st.apply_event(state, e)
    assert "love_letter" in state["inventory"]
    _, msg, ok = referee.resolve(story, state, room, all_rooms,
                                 "look", "love_letter")
    assert ok and "sealed with wax" in msg and "inventory" in msg
    state["room"] = 8                                    # carried elsewhere
    _, msg, ok = referee.resolve(story, state, far, all_rooms,
                                 "look", "love_letter")
    assert ok and "sealed with wax" in msg
    state["inventory"].append("rusty_key")               # bare token
    _, msg, ok = referee.resolve(story, state, far, all_rooms,
                                 "look", "rusty_key")
    assert ok and "describe it freely" in msg
    _, msg, ok = referee.resolve(story, state, far, all_rooms,
                                 "look", "ghost_item")
    assert not ok                                        # still honest


def test_carried_verbs_fire_anywhere(story, monkeypatch):
    # Krem 2026-08-21: what she carries, she can use — declared verbs,
    # effects and dice fire from the pocket in any room; once-dice keys
    # anchor to the object's HOME room so a chance can't re-arm by walking.
    wand = {"desc": "A wand of black walnut.", "takeable": True,
            "interactions": {"wave": {
                "message": "The wand hums.",
                "roll": {"sides": 20, "beat": 1, "once": True,
                         "success": {"extras": ["hostile"], "goto": 8,
                                     "message": "The world lurches."}}}}}
    home = {"id": 7, "title": "Study", "exits": [], "objects": {"wand": wand}}
    far = {"id": 8, "title": "Belfry", "exits": [], "objects": {}}
    all_rooms = {7: home, 8: far}
    state = st.initial_state()
    state["room"] = 7
    events, _, ok = referee.resolve(story, state, home, all_rooms, "take", "wand")
    assert ok
    for e in events:
        st.apply_event(state, e)
    state["room"] = 8                                    # walked away
    monkeypatch.setattr(referee.random, "randint", lambda a, b: 20)
    events, msg, ok = referee.resolve(story, state, far, all_rooms, "wave", "wand")
    assert ok and "world lurches" in msg
    kinds = {e["event"]: e for e in events}
    assert kinds["rolled"]["room"] == 7                  # HOME-anchored key
    assert "hostile" in kinds["extras"]["add"]           # prompt piece on
    assert kinds["moved"]["to"] == 8                     # goto teleport
    for e in events:
        st.apply_event(state, e)
    assert "hostile" in state["extras"]
    # once is spent — everywhere, forever
    _, msg, ok = referee.resolve(story, state, far, all_rooms, "wave", "wand")
    assert not ok and "spent" in msg
    # take-again guard
    _, msg, ok = referee.resolve(story, state, far, all_rooms, "take", "wand")
    assert ok and "already in the inventory" in msg


def test_examine_alias_and_declared_examine(story):
    # examine/inspect fall back to LOOK — but an AUTHOR-declared examine
    # (titanic's ice-shavings) still wins over the alias.
    room = {"id": 7, "title": "Deck", "exits": [], "objects": {
        "shavings": {"desc": "Curls of ice.",
                     "interactions": {"examine": {"message": "Cold and fresh — minutes old."}}},
        "railing": {"desc": "White-painted iron, beaded with spray."}}}
    state = st.initial_state()
    state["room"] = 7
    _, msg, ok = referee.resolve(story, state, room, {7: room}, "examine", "shavings")
    assert ok and "minutes old" in msg                   # declared verb wins
    _, msg, ok = referee.resolve(story, state, room, {7: room}, "inspect", "railing")
    assert ok and "beaded with spray" in msg             # alias → look


def test_prompt_pieces_pool_and_registration(story, cfg_store, monkeypatch):
    from routes import story_routes
    monkeypatch.setattr(story_routes, "_system", lambda: None)
    r = story_routes.set_piece("mad-manse", body={"name": "Bat Form!",
                                                  "text": "You are a small bat."})
    assert r["success"] and r["name"] == "bat_form"      # normalized key
    assert r["pieces"] == {"bat_form": "You are a small bat."}
    # the pool folds into the pack's pieces under the story-scoped key —
    # and into BOTH ctypes (one pool, both lanes: Krem's ruling); engine
    # emotion pieces mirror into extras so any name toggles anywhere
    _, pieces = session._manifest_prompts()
    comps = pieces["components"]
    assert comps["extras"]["story_mad-manse_bat_form"] == "You are a small bat."
    assert comps["emotions"]["story_mad-manse_bat_form"] == "You are a small bat."
    assert "story_engine_dread" in comps["extras"]       # emotion → extras mirror
    got = story_routes.get_pieces("mad-manse")
    assert got["pieces"] == r["pieces"]
    assert "dread" in got["builtin"]                     # engine rows listed
    # overriding an engine piece: pool wins at resolve, builtin keeps
    # the SHIPPED text (the ↩ revert target)
    r2 = story_routes.set_piece("mad-manse", body={"name": "dread",
                                                   "text": "Sharper dread."})
    assert r2["success"]
    assert r2["builtin"]["dread"] != "Sharper dread."
    story_routes.set_piece("mad-manse", body={"name": "dread", "text": ""})
    r = story_routes.set_piece("mad-manse", body={"name": "bat_form", "text": ""})
    assert r["success"] and r["pieces"] == {}            # empty text deletes
    _, pieces = session._manifest_prompts()
    assert "story_mad-manse_bat_form" not in (pieces.get("extras") or {})


def test_art_store_ingest_dedup_and_refusals(tmp_path, monkeypatch):
    # W1 (2026-08-21): content-hash store — same image lands on the same
    # file (dedup is the addressing scheme); junk and oversize refused.
    import io
    from PIL import Image
    from gameroom_story import art
    store = tmp_path / "art"
    monkeypatch.setattr(art, "store_dir",
                        lambda: (store.mkdir(exist_ok=True) or store))
    buf = io.BytesIO()
    Image.new("RGB", (2400, 1200), (200, 30, 40)).save(buf, "PNG")
    data = buf.getvalue()
    name, err = art.ingest(data)
    assert err is None and art.ART_NAME_RE.fullmatch(name), (name, err)
    name2, _ = art.ingest(data)
    assert name2 == name                                  # dedup
    assert len(list(store.iterdir())) == 1
    from PIL import Image as I2
    out = I2.open(store / name)
    assert max(out.size) == art.MAX_EDGE                  # long edge capped
    assert art.ingest(b"not an image")[1]
    monkeypatch.setattr(art, "MAX_UPLOAD", 10)
    assert "too large" in art.ingest(data)[1].lower()


def test_backdrop_override_merge_and_route(story, cfg_store, tmp_path, monkeypatch):
    # W1: backdrop override is UNGATED (like objects/exits) and resolves
    # through the store lane; unknown names refused; '' clears to shipped.
    import io
    from PIL import Image
    from gameroom_story import art
    from routes import story_routes
    store = tmp_path / "art"
    monkeypatch.setattr(art, "store_dir",
                        lambda: (store.mkdir(exist_ok=True) or store))
    monkeypatch.setattr(story_routes, "_system", lambda: None)
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), (10, 120, 90)).save(buf, "PNG")
    name, _ = art.ingest(buf.getvalue())
    chat = "manse-backdrop-chat"
    base = {"session": chat, "slug": "mad-manse", "room_id": 1}
    r = story_routes.set_backdrop(body={**base, "name": "nope.webp"})
    assert not r["success"]                               # unknown refused
    r = story_routes.set_backdrop(body={**base, "name": name})
    assert r["success"], r
    fresh = rooms.load_story("mad-manse")
    state = _fresh(fresh)                                 # zork flag UNSET
    session._merge_user_layer(fresh, "mad-manse", chat, state)
    assert fresh["rooms"][1]["backdrop"] == name          # ungated
    assert session._art_url(fresh, name) == \
        f"/api/plugin/game-room/story/art/{name}"
    assert story_routes.get_art(name=name).media_type == "image/webp"
    assert story_routes.get_art(name="zz.webp").status_code == 404
    r = story_routes.set_backdrop(body={**base, "name": ""})
    assert r["success"]
    assert "1" not in (st.get_user_layer("mad-manse", chat).get("rooms") or {})


def test_setup_route_shape(story, cfg_store):
    from routes import story_routes
    r = story_routes.get_setup("mad-manse")
    assert r["open_flag"] == "chest_opened"
    assert [s["key"] for s in r["slots"]] == ["relationship", "watchword", "combo"]
    assert r["scenarios"] == {}


def test_settings_schema_slot_story_gm_only(story, cfg_store):
    # 3-tab ruling (Krem 2026-08-20): slot-assembled stories drop the raw
    # storycfg text editors (they'd show {tokens}) — schema is GM-tab only.
    from routes import story_routes
    r = story_routes.get_story_settings("mad-manse")
    keys = [f["key"] for f in r["schema"]]
    assert "role_text" not in keys and "premise" not in keys \
        and "player_role" not in keys
    assert "dm_guide" in keys and "gm_universal" in keys
    assert all(f["tab"] == "GM" for f in r["schema"])


# ── Pre-seeded seal at turn 0 (the sealed Mad-Libs slot lane) ───────────────

def test_seal_preseed_right_after_start(story):
    st.set_active(CHAT, "mad-manse", None)
    st.append("mad-manse", CHAT, {"event": "started", "story": "mad-manse",
                                  "room": 1, "turn": 0})
    # sealed interaction lives on the chest only for this test's purposes —
    # declare one via the user layer, then pre-seed it
    session.upsert_user_object(
        CHAT, "mad-manse", 1, "letter",
        {"desc": "A wax-sealed letter.",
         "interactions": {"read": {"sealed": {"ask": "What does it say?"}}}})
    msg, ok = session.fill_seal(None, "1:letter:read",
                                text="Free energy is real.", session=CHAT)
    assert ok, msg
    story2, state2 = session.load_active(CHAT)
    events, msg, ok = referee.resolve(story2, state2, story2["rooms"][1],
                                      story2["rooms"], "read", "letter")
    assert ok and "Free energy is real." in msg
    st.clear_active(CHAT)


def test_mid_run_scenario_swap_applies_slots(story, cfg_store, monkeypatch):
    # Prime repro 2026-08-23 (dropdown said test2, fields showed defaults):
    # the swap must be PURE for slots too — mid-run the entry's slots become
    # the scenario's (reset → declared defaults), so the layer's scenario
    # tag can never outrun what she's wearing. Pre-start (no entry) the
    # form seeds from the tag client-side.
    from routes import story_routes
    monkeypatch.setattr(story_routes, "_system", lambda: None)
    monkeypatch.setattr(session, "refresh_prompt", lambda *a, **k: True)
    st.set_active(CHAT, "mad-manse", None)
    st.update_active(CHAT, slots={"relationship": "rivals"})
    cfg_store.save("storyscenarios:mad-manse",
                   {"test2": {"slots": {"relationship": "wife"}, "objects": {}, "rooms": {}}})
    r = story_routes.load_scenario(body={"name": "test2", "session": CHAT})
    assert r["success"], r
    entry = st.get_active_entry(CHAT)
    assert entry["slots"]["relationship"] == "wife"
    assert st.get_user_layer("mad-manse", CHAT)["scenario"] == "test2"
    # reset → declared defaults, tag cleared
    r = story_routes.load_scenario(body={"name": "", "session": CHAT})
    assert r["success"], r
    entry = st.get_active_entry(CHAT)
    assert entry["slots"]["relationship"] != "wife"
    assert st.get_user_layer("mad-manse", CHAT).get("scenario") == ""
    st.clear_active(CHAT)


# ── Per-scenario DM guide (dm_guide as a slot) + scenario-save caps ─────────

def test_dm_slot_owns_the_guide(cfg_store):
    # Pack ships dm_guide as a {token} + a declared slot (2026-08-23): the
    # Story-tab slot is the one editor — _apply_slots marks the meta and
    # conduct_for ignores the stale storycfg override that would stomp it.
    tale = {"meta": {"slug": "dm-slot-tale", "dm_guide": "{dm_style}",
                     "slots": [{"key": "dm_style", "rows": 4,
                                "default": "Default house style."}]},
            "rooms": {}}
    cfg_store.save("storycfg:dm-slot-tale", {"dm_guide": "Stale GM-tab edit."})
    session._apply_slots(tale, {"dm_style": "Run it noir."})
    assert tale["meta"]["dm_guide"] == "Run it noir."
    assert session.conduct_for(tale)[1] == "Run it noir."


def test_pack_gained_slot_backfills_default(story):
    # Old playthrough: entry slots predate a new declaration — the declared
    # default substitutes at load, no raw {token} reaches her prompt.
    session._apply_slots(story, {})
    assert "{relationship}" not in story["meta"]["premise"]
    assert "as partner" in story["meta"]["premise"]


def test_slotted_dm_guide_hidden_from_gm_tab(story, cfg_store, monkeypatch):
    from routes import story_routes
    monkeypatch.setattr(story_routes, "_system", lambda: None)
    orig = rooms.load_story

    def doctored(slug, raw=False):
        d = orig(slug, raw=raw)
        d["meta"]["dm_guide"] = "{dm_style}"
        return d
    monkeypatch.setattr(rooms, "load_story", doctored)
    r = story_routes.get_story_settings("mad-manse")
    keys = [f["key"] for f in r["schema"]]
    assert "dm_guide" not in keys and "dm_guide" not in r["settings"]
    assert "gm_universal" in keys        # the GM tab keeps the global layers


def test_cap_scenario_slots_scales_with_rows(story, monkeypatch):
    from routes import story_routes
    orig = rooms.load_story

    def doctored(slug, raw=False):
        d = orig(slug, raw=raw)
        d["meta"]["slots"] = [{"key": "scenario", "rows": 3}, {"key": "name"}]
        return d
    monkeypatch.setattr(rooms, "load_story", doctored)
    capped = story_routes._cap_scenario_slots("mad-manse", {
        "scenario": "x" * 9000, "name": "y" * 9000, "blank": "  "})
    assert len(capped["scenario"]) == 8000    # textarea slots: real prose
    assert len(capped["name"]) == 1200        # one-liners keep the tight cap
    assert "blank" not in capped
