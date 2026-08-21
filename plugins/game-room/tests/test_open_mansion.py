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


def test_added_exits_gated_and_deduped(story):
    session.set_room_text(CHAT, "mad-manse", 1,
                          add_exits=[{"label": "the parlor again", "to": 2},
                                     {"label": "the void", "to": 99}])
    fresh = rooms.load_story("mad-manse")
    state = _fresh(fresh)
    session._merge_user_layer(fresh, "mad-manse", CHAT, state)
    assert len(fresh["rooms"][1]["exits"]) == 1          # pre-line: gated
    state["flags"]["chest_opened"] = True
    fresh2 = rooms.load_story("mad-manse")
    session._merge_user_layer(fresh2, "mad-manse", CHAT, state)
    exits = fresh2["rooms"][1]["exits"]
    assert len(exits) == 2                               # to=2 deduped, void added
    assert exits[-1] == {"label": "the void", "to": 99}
    session.set_room_text(CHAT, "mad-manse", 1, add_exits=[])


def test_import_scenario_skips_stamp_for_shipped_names(story, cfg_store):
    cfg_store.save("storyscenarios:mad-manse",
                   {"mix": {"objects": {"1": {"chest": {"desc": "gilded"},
                                              "lamp": {"desc": "a lamp"}}}}})
    session._import_scenario_env(CHAT, "mad-manse", story, "mix")
    layer = st.get_user_layer("mad-manse", CHAT)["objects"]["1"]
    assert "condition" not in layer["chest"]             # shadow: no deadlock stamp
    assert layer["lamp"]["condition"] == {"flag": "chest_opened"}
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

def test_import_scenario_env_stamps_zork_condition(story, cfg_store):
    cfg_store.save("storyscenarios:mad-manse", {
        "bbq": {"objects": {"1": {"grill": {"desc": "A hot grill."},
                                  "banner": {"desc": "A banner.",
                                             "condition": {"flag": "own"}}}},
                "rooms": {"2": {"template": "BBQ parlor."}}}})
    session._import_scenario_env(CHAT, "mad-manse", story, "bbq")
    layer = st.get_user_layer("mad-manse", CHAT)
    assert layer["objects"]["1"]["grill"]["condition"] == \
        {"flag": "chest_opened"}                       # implicit stamp
    assert layer["objects"]["1"]["banner"]["condition"] == {"flag": "own"}
    assert layer["rooms"]["2"]["template"] == "BBQ parlor."


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
