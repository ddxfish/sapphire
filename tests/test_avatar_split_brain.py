"""Avatar split brain (2026-11-09): tags ride their chunk, segments get their
own words, and the server brain owns rails 3-6."""
import pytest

from core.event_bus import Events
from core.tts.streaming import SpeechChunker


def _chunks(tokens, **kw):
    c = SpeechChunker(min_chars=3, **kw)
    out = []
    for t in tokens:
        out += c.push(t)
    out += c.flush()
    return out, c.tail


# ── chunker: tags ride the chunk that carried them ──────────────────────────
def test_tag_rides_its_sentence_and_never_reaches_the_synth():
    out, tail = _chunks(["<<avatar: wave>> Hey babe! How", " are you <<avatar: happy once>> today?"],
                        split_mode="sentence")
    assert [c["text"] for c in out] == ["Hey babe!", "How are you today?"]
    assert out[0]["avatar"] == [{"track": "wave", "mode": "once", "pos": 0}]
    assert out[1]["avatar"] == [{"track": "happy", "mode": "once", "pos": 12}]
    assert tail == []
    assert not any(0xE000 <= ord(ch) < 0xF000 for c in out for ch in c["text"])


def test_tag_split_across_tokens_is_held_whole():
    out, _ = _chunks(["Split <<avatar: wave>", "> tag here."])
    assert out[0]["text"] == "Split tag here."
    assert out[0]["avatar"][0]["track"] == "wave"


def test_tag_after_last_words_lands_in_tail():
    out, tail = _chunks(["Hello there friend.\n\n<<avatar: blowkiss loop>>"])
    assert [c["text"] for c in out] == ["Hello there friend."]
    assert "avatar" not in out[0]
    assert tail == [{"track": "blowkiss", "mode": "loop", "pos": 0}]


def test_no_tags_no_field():
    out, tail = _chunks(["Plain text, no tags. Second one."], split_mode="sentence")
    assert all("avatar" not in c for c in out) and tail == []


def test_prose_double_angle_does_not_hold_forever():
    out, _ = _chunks(["a << b and more"])
    assert out[0]["text"] == "a << b and more"


# ── segments: each audio piece gets its own words ───────────────────────────
TEXT = ("Seventeen hours later, hey babe. Sure, TTS test. Here's a story with some range "
        "to it, okay? It starts in a garage full of pine smell. The rain hits the roof hard. "
        "And then she laughs, and it all gets quiet again. The end of this part is here.")


def test_segments_cut_at_sentence_ends_and_cover_the_text():
    from plugins.avatar.mouth import Segments
    s = Segments()
    spans = [s.take("x", 0, i, TEXT, d) for i, d in enumerate([6.0, 6.0, 5.5])]
    assert spans[0][0] == 0 and spans[-1][1] == len(TEXT)
    for (a, b), (c, _) in zip(spans, spans[1:]):
        assert b == c and TEXT[b - 2] in ".!?"


def test_single_segment_takes_everything():
    from plugins.avatar.mouth import Segments
    assert Segments().take("y", 0, 0, "Hey babe, can you hear me?", 1.9) == (0, 26)


def test_cues_land_in_their_segment():
    from plugins.avatar.mouth import cues
    tags = [{"track": "wave", "pos": 0}, {"track": "happy", "pos": 150}]
    assert [c["track"] for c in cues(tags, 0, 93, 243, 6.0)] == ["wave"]
    mid = cues(tags, 93, 164, 243, 5.0)
    assert mid[0]["track"] == "happy" and 0 < mid[0]["t"] < 5.0


# ── brain: rails 3-6 ────────────────────────────────────────────────────────
@pytest.fixture
def brain(monkeypatch):
    from plugins.avatar import brain as mod

    def fake_load(self):
        self.track_map = dict(mod.FALLBACK_TRACK_MAP)
        self.pool = [{"track": "idle", "weight": 1, "oneshot": False, "variety": False, "base": ["day", "night"]},
                     {"track": "wave", "weight": 1, "oneshot": True, "variety": True, "base": []}]
        self.stage_on, self.seats = False, []
        self.buckets = [{"name": "day", "start": 0, "quiet": False}]
        self.interval = 60.0
        self.oneshots = mod.ONESHOT_KNOWN | {"wave"}
        self.clips = {"idle": 4.0, "wave": 2.5, "happy": 1.5, "defaultanim": 3.0,
                      "thinking": 3.0, "listening": 3.0, "attention2": 2.0}
        self.enabled = True
    monkeypatch.setattr(mod.Brain, "_load", fake_load)
    b = mod.Brain()
    b._write(6, b._base(), "loop")
    b._arm()
    return b


def test_typing_then_happy_oneshot_clears_on_clip_length(brain):
    brain._on_event(Events.AI_TYPING_START, {})
    assert brain.rails[3]["track"] == "defaultanim" and brain._winning() == 3
    brain._on_event(Events.AI_TYPING_END, {})
    assert brain.rails[3] == {"track": "happy", "mode": "oneshot", "id": brain._ids}
    assert "r3" in brain._due
    brain._tick("r3")
    brain._arm()
    assert brain.rails[3] is None and brain._winning() == 6 and "variety" in brain._due


def test_phone_turn_is_ignored(brain):
    assert brain._on_event(Events.AI_TYPING_START, {"foreign": True}) is False
    assert brain.rails[3] is None


def test_reach_is_answered_by_her_turn(brain):
    brain._on_event(Events.STT_PROCESSING, {})
    assert brain.rails[4]["track"] == "thinking" and "r4" in brain._due
    brain._on_event(Events.AI_TYPING_START, {})
    assert brain.rails[4] is None


def test_variety_gesture_then_back_to_base(brain):
    brain._tick("variety")
    brain._arm()
    assert brain.rails[5]["track"] == "wave" and brain.rails[5]["mode"] == "oneshot"
    assert "variety" not in brain._due        # busy with a gesture
    brain._tick("r5")
    brain._arm()
    assert brain._winning() == 6 and "variety" in brain._due


def test_rewrite_gets_a_new_id(brain):
    brain._on_event(Events.AI_TYPING_END, {})
    first = brain.rails[3]["id"]
    brain._on_event(Events.AI_TYPING_END, {})
    assert brain.rails[3]["id"] != first


# ── her place: go tags and the idle stroll ──────────────────────────────────
def test_go_grammar():
    from plugins.avatar.brain import parse_go
    assert parse_go("go_left") == {"x": 6, "spot": None}
    assert parse_go("GO_37") == {"x": 37, "spot": None}
    assert parse_go("go_back") == {"d": 100, "spot": None}
    assert parse_go("go_30_60") == {"x": 30, "d": 60, "spot": None}
    assert parse_go("go_couch", ["couch"]) == {"spot": "couch"}
    assert parse_go("go_sit", ["chair"]) == {"spot": "chair"}
    assert parse_go("go_desk", ["couch"]) is None       # no desk out
    assert parse_go("go_moon") is None and parse_go("wave") is None


def test_her_go_tag_updates_x_without_a_new_walk(brain):
    before = brain.place["id"]
    brain.went("go_back")
    brain.went("go_left")
    assert brain.place == {"x": 6, "d": 100, "spot": None, "id": before}
    brain.seats = ["couch"]
    brain.went("go_couch")
    assert brain.place["spot"] == "couch" and brain.place["id"] == before


def test_stroll_moves_her_instead_of_a_gesture(brain, monkeypatch):
    from plugins.avatar import brain as mod
    brain.stage_on, brain.seats = True, ["chair"]
    monkeypatch.setattr(mod.random, "random", lambda: 0.0)
    was = dict(brain.place)
    brain._tick("variety")
    assert brain.place["id"] != was["id"]
    assert {k: brain.place[k] for k in ("x", "d", "spot")} != {k: was[k] for k in ("x", "d", "spot")}
    assert brain.rails[5] is None
    assert brain.snapshot()["place"] == brain.place


def test_no_base_ticked_rests_on_idle_not_a_hidden_key(brain):
    brain.pool = [p for p in brain.pool if p["track"] != "idle"]
    assert brain._base() == "idle"


def test_real_work_sends_her_to_her_desk(brain):
    brain.stage_on, brain.seats = True, ["desk"]
    brain._on_event(Events.AI_TYPING_START, {})
    assert brain.place["spot"] is None              # a plain reply: she stays put
    brain._on_event(Events.TOOL_EXECUTING, {})
    assert brain.place["spot"] == "desk"
    at = brain.place["id"]
    brain._on_event(Events.TOOL_EXECUTING, {})
    assert brain.place["id"] == at                  # already there


def test_the_middle_is_a_visit_she_steps_aside(brain):
    brain.stage_on = True
    brain.went("go_40")
    assert "aside" in brain._due                    # over the text: the clock runs
    brain.rails[3] = {"track": "typing", "mode": "loop", "id": 1}
    brain._tick("aside")
    assert brain.place["x"] == 40 and "aside" in brain._due     # mid-turn she stays
    brain.rails[3] = None
    at = brain.place["id"]
    brain._tick("aside")
    assert brain.place["x"] == 6 and brain.place["id"] != at and "aside" not in brain._due
    brain.went("go_right")
    assert "aside" not in brain._due                # an edge is a place to stay
    brain.seats = ["chair"]
    brain.went("go_50")
    brain.went("go_chair")
    assert "aside" not in brain._due                # so is a seat
