"""The video avatar's clip maker: the queue, the thumbs, the AI's turn, and the checks.
maker.py is loaded by path with the plugin's lib pointed at a temp dir; ComfyUI, the avatar
service and the vision model are stand-ins. The real install is never read or written."""
import importlib.util
import io
import json
import sys
import types
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parent.parent / "user" / "plugins" / "vidavatar"
pytestmark = pytest.mark.skipif(not (PLUGIN / "maker.py").exists(), reason="the video avatar plugin is not here")
GOOD = {"start": 0.0015, "end": 0.002, "seam": 0.001, "face_seam": 4.0, "face_home": 6.5, "peak": 0.03, "wrist": 0.23,
        "mouth": 0.01, "lost": 0.0, "face_px": 105, "frames": 121}


def _png():
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", (288, 512), (9, 9, 9)).save(b, "PNG")
    return b.getvalue()


PNG = _png()


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def mk(monkeypatch, tmp_path):
    lib = _load("vidavatar_lib_for_maker", PLUGIN / "lib.py")
    monkeypatch.setattr(lib, "CLIPS", tmp_path)
    lib._names.clear()
    pkg = types.ModuleType("plugins.vidavatar")
    pkg.lib = lib
    monkeypatch.setitem(sys.modules, "plugins.vidavatar", pkg)
    monkeypatch.setitem(sys.modules, "plugins.vidavatar.lib", lib)
    m = _load("vidavatar_maker_under_test", PLUGIN / "maker.py")
    m.world = {"numbers": dict(GOOD), "judge": (True, "she waves", "Eye"), "fail": None, "renders": [], "plans": []}
    monkeypatch.setattr(m, "workflows", lambda: [{"name": "flf", "slots": 2, "usable": True, "words": "loops"},
                                                 {"name": "i2v", "slots": 1, "usable": True, "words": "one picture"},
                                                 {"name": "t2v", "slots": 0, "usable": False, "words": "takes no picture"}])
    monkeypatch.setattr(m, "stills", lambda: m.world.get("stills", []))      # no image workflow, unless a test puts one in
    monkeypatch.setattr(m, "_start", lambda: None)            # the test turns the worker's crank itself
    monkeypatch.setattr(m, "_rescan", lambda persona: None)

    def render(persona, st, name, row):
        if m.world["fail"]:
            raise m.MakerError(m.world["fail"])
        flow, pictures, frame, mode = m._plan_for(persona, st, row)
        m.world["renders"].append((name, m.words_for(frame, row["action"]), row["seed"]))
        m.world["plans"].append((name, flow, [p.stem for p in pictures], mode))
        f = m.pending(persona) / (f"{name}.{row['tries']}.png" if mode == "still" else f"{name}.{row['tries']}.mp4")
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(PNG if mode == "still" else b"clip")
        return f.name, mode

    class Answer:
        status_code = 200

        def __init__(self, path, kw):
            self.path, self.kw = path, kw
            self.content = PNG

        def json(self):
            if self.path == "/measure" and not (self.kw.get("json") or {}).get("clip"):
                return {"face": True, "mouth": 0.0, "turn": 0.05, "face_px": 120}
            m.world["asked"] = self.kw.get("json")
            return m.world["numbers"]
    monkeypatch.setattr(m, "_render", render)
    monkeypatch.setattr(m, "_service", lambda method, path, **kw: Answer(path, kw))
    monkeypatch.setattr(m, "_judge", lambda persona, file, action, key, one_way=False: m.world["judge"])
    m.home_path("ada").parent.mkdir(parents=True)
    m.home_path("ada").write_bytes(PNG)
    m.setup("ada", {"workflow": "flf", "workflow_one": "i2v"})
    m.tmp = tmp_path
    return m


def crank(m, times=1):
    for _ in range(times):
        job = m._take()
        if not job:
            return False
        m._one(*job)
    return True


def row(m, name, persona="ada"):
    return m.load(persona)["rows"][name]


# ── the checks ──────────────────────────────────────────────────────────────
def test_a_clip_with_pinned_ends_passes_the_checks(mk):
    assert mk.flags(GOOD) == []


def test_a_face_that_comes_back_different_is_caught_though_her_pose_is_home(mk):
    words = mk.flags({**GOOD, "end": 0.009, "face_seam": 30.0})
    assert words == [["her face does not come back the way it started", True]]


def test_a_clip_that_ends_away_from_home_is_not_a_loop(mk):
    assert mk.flags({**GOOD, "end": 0.05})[0] == ["does not start and end on her picture's pose", True]


def test_nothing_happening_is_a_miss_except_in_an_idle(mk):
    still = {**GOOD, "peak": 0.006, "wrist": 0.006}
    assert ["she barely moves", True] in mk.flags(still)
    assert mk.flags(still, idle=True) == []


def test_her_own_mouth_moving_is_a_miss_and_a_hidden_face_is_only_said(mk):
    assert mk.flags({**GOOD, "mouth": 0.05})[0][1] is True
    assert mk.flags({**GOOD, "lost": 0.7}) == [["her face is out of view for most of it: her lips rest there", False]]


@pytest.mark.parametrize("text, want", [
    ('{"ok": true, "why": "she waves"}', (True, "she waves")),
    ('Here you go:\n```json\n{"ok": false, "why": "a third arm\\nappears"}\n```', (False, "a third arm appears")),
    ('{"ok": "false", "why": "wrong action"}', (False, "wrong action")),
    ('{"ok": "maybe"}', (None, "")),
    ("she waves nicely", (None, "its answer could not be read")),
    ("", (None, "its answer could not be read")),
])
def test_a_judges_answer_is_read_or_owned_up_to(mk, text, want):
    assert mk.read_verdict(text) == want


def test_every_takes_prompt_carries_the_rows_action(mk):
    assert mk.words_for("The person {action}, then stops.", "waves") == "The person waves, then stops."
    assert mk.words_for("Fixed camera.", "waves") == "Fixed camera. waves"
    assert "{action}" not in mk.words_for("", "waves") and "waves" in mk.words_for("", "waves")


# ── the home picture ────────────────────────────────────────────────────────
def test_a_see_through_picture_is_cut_to_her_on_a_backdrop(mk):
    from PIL import Image
    im = Image.new("RGBA", (1200, 1200), (0, 0, 0, 0))
    im.paste((200, 50, 50, 255), (500, 100, 700, 1100))           # her: 200 wide, 1000 tall
    out = mk._fit(im)
    assert out.mode == "RGB" and out.size == (round(1110 * 9 / 16), 1110)
    assert out.getpixel((5, 5)) == mk.BACKDROP
    assert out.getpixel((out.width // 2, out.height // 2)) == (200, 50, 50)
    assert out.getpixel((out.width // 2, 60)) == mk.BACKDROP      # room over her head
    plain = Image.new("RGB", (640, 360), (9, 9, 9))
    assert mk._fit(plain).size == (640, 360)


def test_a_file_that_is_no_picture_is_refused_in_words(mk):
    with pytest.raises(mk.MakerError):
        mk.pose_put("ada", "", b"not a picture")


def test_what_is_wrong_with_a_home_picture_is_said(mk):
    assert "mouth is open" in mk.home_words({"face": True, "mouth": 0.067, "turn": 0.04, "face_px": 104})[0]
    assert mk.home_words({"face": True, "mouth": 0.005, "turn": 0.05, "face_px": 160}) == ["Looks good: face to the camera, mouth closed."]
    assert "No face" in mk.home_words({"face": False})[0]
    assert "Not checked" in mk.home_words(None)[0]


# ── the list ────────────────────────────────────────────────────────────────
def test_a_persona_starts_with_the_shipped_list(mk):
    rows = mk.view("ada")["rows"]
    assert len(rows) >= 90 and {"idle", "wave", "nod", "shrug"} <= {r["name"] for r in rows}
    assert all(r["state"] == "new" and r["action"] for r in rows)
    ends = {r["name"]: (r["from"], r["to"]) for r in rows}
    assert ends["wave"] == ("home", "home")
    assert (ends["sit"], ends["idle_sit"], ends["stand"]) == (("home", "sit"), ("sit", "sit"), ("sit", "home"))      # a sitting position ships with the list
    import re
    assert not any(re.search(r"\b(her|she|his|he|herself|himself)\b", r["action"]) for r in rows)      # any persona, not one


def test_rows_are_added_changed_and_removed(mk):
    mk.row_put("ada", "Moon Walk!", "  slides   backward ")
    assert row(mk, "moon_walk")["action"] == "slides backward"
    mk.row_put("ada", "moon_walk", "glides backward")
    assert row(mk, "moon_walk")["action"] == "glides backward"
    mk.row_del("ada", "moon_walk")
    assert "moon_walk" not in mk.load("ada")["rows"]
    for bad in ("", "con", "!!!"):
        with pytest.raises(mk.MakerError):
            mk.row_put("ada", bad, "waves")
    with pytest.raises(mk.MakerError):
        mk.row_put("ada", "wave_9", "   ")


def test_generate_needs_a_home_picture_and_a_workflow_that_can_loop(mk):
    mk.home_path("ada").unlink()
    with pytest.raises(mk.MakerError, match="Add her picture"):
        mk.go("ada")
    mk.home_path("ada").write_bytes(PNG)
    mk.setup("ada", {"workflow": "t2v"})
    with pytest.raises(mk.MakerError, match="takes no picture"):
        mk.go("ada", ["wave", "nod"])
    v = mk.go("ada")                                           # Generate all: what can be made is, and why the rest is not is said
    assert mk.load("ada")["order"] == ["sit"] and "takes no picture" in v["note"] and "could not be queued" in v["note"]
    mk.stop("ada")
    mk.load("ada")["rows"]["sit"]["state"] = "new"
    mk.load("ada")["order"].clear()
    mk.setup("ada", {"workflow": ""})                          # nothing picked: the first workflow that loops is used
    mk.go("ada", ["wave"])
    crank(mk)
    assert mk.world["plans"][-1] == ("wave", "flf", ["home", "home"], "")
    assert mk.view("ada")["using"] == ["flf", "i2v", ""]        # and no image workflow that edits a picture


def test_generate_leaves_out_what_she_already_has(mk):
    d = mk.tmp / "ada" / "clips"
    d.mkdir()
    (d / "wave.mp4").write_bytes(b"x")
    mk.go("ada")
    order = mk.load("ada")["order"]
    assert "wave" not in order and "nod" in order and row(mk, "wave")["state"] == "new"


# ── by hand: every clip waits for a thumb ───────────────────────────────────
def test_a_made_clip_waits_in_the_deck_and_a_thumbs_up_files_it(mk):
    mk.go("ada", ["wave"])
    assert crank(mk)
    v = mk.view("ada")
    assert v["deck"] == ["wave"] and row(mk, "wave")["state"] == "review"
    assert next(r for r in v["rows"] if r["name"] == "wave")["url"] == "maker/file/ada/wave.1.mp4"
    assert mk.world["renders"][0][1].startswith("The person raises one hand and waves at the camera, then returns")
    mk.verdict("ada", "wave", "keep")
    assert (mk.tmp / "ada" / "clips" / "wave.mp4").read_bytes() == b"clip"
    assert not list(mk.pending("ada").glob("wave.*"))
    assert row(mk, "wave")["state"] == "kept" and row(mk, "wave")["by"] == "you" and mk.view("ada")["deck"] == []


def test_a_thumbs_down_goes_to_the_front_of_the_queue_with_new_words(mk):
    mk.go("ada", ["wave", "nod", "shrug", "bow"])
    crank(mk)                                                  # wave is made; nod, shrug, bow wait
    mk.verdict("ada", "wave", "again", "waves with both hands")
    st = mk.load("ada")
    assert st["order"] == ["wave", "nod", "shrug", "bow"] and st["running"]
    assert row(mk, "wave")["action"] == "waves with both hands" and not list(mk.pending("ada").glob("wave.*"))
    crank(mk)
    assert mk.world["renders"][-1][0] == "wave" and "both hands" in mk.world["renders"][-1][1]
    assert row(mk, "wave")["tries"] == 1                       # a person's ask starts the count again


def test_a_dropped_action_is_gone_and_not_made_again(mk):
    mk.go("ada", ["wave"])
    crank(mk)
    mk.verdict("ada", "wave", "drop")
    assert row(mk, "wave")["state"] == "dropped" and not list(mk.pending("ada").glob("wave.*"))
    mk.go("ada")
    assert "wave" not in mk.load("ada")["order"]
    mk.go("ada", ["wave"])                                     # asked for by name: a person changed their mind
    assert mk.load("ada")["order"][-1] == "wave"


def test_a_thumb_on_a_clip_that_is_not_waiting_is_refused(mk):
    with pytest.raises(mk.MakerError):
        mk.verdict("ada", "wave", "keep")
    with pytest.raises(mk.MakerError):
        mk.verdict("ada", "no_such_row", "again")


def test_by_hand_the_judge_is_never_asked(mk, monkeypatch):
    monkeypatch.setattr(mk, "_judge", lambda *a, **k: pytest.fail("the vision model was asked with auto off"))
    mk.setup("ada", {"judge": "eye"})
    mk.go("ada", ["wave"])
    crank(mk)
    assert row(mk, "wave")["state"] == "review" and row(mk, "wave")["judge"] is None


# ── auto: the AI first, a person after ──────────────────────────────────────
def test_the_ai_keeps_a_clip_and_it_still_waits_for_a_look(mk):
    mk.setup("ada", {"auto": True, "judge": "eye"})
    mk.go("ada", ["wave"])
    crank(mk)
    r = row(mk, "wave")
    assert r["state"] == "kept" and r["by"] == "ai" and r["seen"] is False
    assert (mk.tmp / "ada" / "clips" / "wave.mp4").exists()
    assert r["judge"] == {"ok": True, "why": "she waves", "by": "Eye"}
    v = mk.view("ada")
    assert v["deck"] == ["wave"] and next(x for x in v["rows"] if x["name"] == "wave")["url"].startswith("clip/ada/wave")
    mk.verdict("ada", "wave", "keep")
    assert row(mk, "wave")["seen"] is True and mk.view("ada")["deck"] == []


def test_a_person_overrules_the_ai_and_she_keeps_the_clip_until_a_better_one(mk):
    mk.setup("ada", {"auto": True, "judge": "eye"})
    mk.go("ada", ["wave", "nod"])
    crank(mk)
    clip = mk.tmp / "ada" / "clips" / "wave.mp4"
    clip.write_bytes(b"the first take")
    mk.setup("ada", {"auto": False})
    mk.verdict("ada", "wave", "again")
    assert clip.read_bytes() == b"the first take"              # made again: she is not left without it meanwhile
    assert mk.load("ada")["order"] == ["wave", "nod"] and row(mk, "wave")["state"] == "queued"
    crank(mk)
    assert row(mk, "wave")["state"] == "review" and mk.view("ada")["rows"][[r["name"] for r in mk.view("ada")["rows"]].index("wave")]["has"]
    mk.verdict("ada", "wave", "old")                           # the new take is no better: back to the one she has
    assert row(mk, "wave")["state"] == "kept" and clip.read_bytes() == b"the first take" and not list(mk.pending("ada").glob("wave.*"))
    mk.verdict("ada", "wave", "again")
    crank(mk)
    mk.verdict("ada", "wave", "keep")                          # this one is: it takes the old one's place
    assert clip.read_bytes() == b"clip"
    with pytest.raises(mk.MakerError, match="no earlier clip"):
        mk.verdict("ada", "wave", "old")


def test_the_ai_makes_a_miss_again_three_times_then_asks(mk):
    mk.setup("ada", {"auto": True, "judge": "eye"})
    mk.world["judge"] = (False, "she salutes instead", "Eye")
    mk.go("ada", ["wave", "nod"])
    crank(mk)
    assert mk.load("ada")["order"] == ["wave", "nod"] and row(mk, "wave")["last"] == "she salutes instead"
    crank(mk, 2)
    r = row(mk, "wave")
    assert r["state"] == "review" and r["tries"] == mk.MAX_TRIES and r["judge"]["ok"] is False
    assert [n for n, _, _ in mk.world["renders"]] == ["wave", "wave", "wave"]
    assert len({seed for _, _, seed in mk.world["renders"]}) == 3
    assert mk.view("ada")["deck"] == ["wave"] and (mk.pending("ada") / "wave.3.mp4").exists()


def test_the_checks_decide_before_the_judge_is_asked(mk, monkeypatch):
    monkeypatch.setattr(mk, "_judge", lambda *a, **k: pytest.fail("the vision model was asked about a clip the checks failed"))
    mk.setup("ada", {"auto": True, "judge": "eye"})
    mk.world["numbers"] = {**GOOD, "face_seam": 30.0}
    mk.go("ada", ["wave"])
    crank(mk)
    assert row(mk, "wave")["state"] == "queued" and row(mk, "wave")["last"].startswith("her face does not come back")


def test_with_no_judge_picked_the_checks_alone_decide(mk):
    mk.setup("ada", {"auto": True, "judge": ""})
    mk.go("ada", ["wave"])
    crank(mk)
    assert row(mk, "wave")["state"] == "kept" and "no vision model" in row(mk, "wave")["judge"]["why"]


def test_an_ai_that_cannot_say_leaves_it_to_a_person(mk):
    mk.setup("ada", {"auto": True, "judge": "eye"})
    mk.world["judge"] = (None, "its answer could not be read", "Eye")
    mk.go("ada", ["wave"])
    crank(mk)
    assert row(mk, "wave")["state"] == "review" and not (mk.tmp / "ada" / "clips" / "wave.mp4").exists()


def test_a_clip_nobody_measured_is_never_kept_by_the_checks_alone(mk):
    mk.setup("ada", {"auto": True, "judge": ""})
    mk.world["numbers"] = {"error": "offline"}
    mk.go("ada", ["wave"])
    crank(mk)
    assert row(mk, "wave")["state"] == "review" and row(mk, "wave")["flags"][0][0].startswith("not measured")


def test_keep_all_marks_every_ai_clip_as_looked_at(mk):
    mk.setup("ada", {"auto": True, "judge": "eye"})
    mk.go("ada", ["wave", "nod", "bow"])
    crank(mk, 3)
    assert len(mk.view("ada")["deck"]) == 3
    mk.verdict("ada", "", "keep_all")
    assert mk.view("ada")["deck"] == [] and (mk.tmp / "ada" / "clips" / "bow.mp4").exists()


# ── when things go wrong ────────────────────────────────────────────────────
def test_two_failures_in_a_row_stop_the_batch_with_the_reason(mk):
    mk.world["fail"] = "ComfyUI is not answering."
    mk.go("ada", ["wave", "nod", "bow", "shrug"])
    crank(mk, 4)
    st = mk.load("ada")
    assert not st["running"] and "ComfyUI is not answering" in st["note"]
    assert [row(mk, n)["state"] for n in ("wave", "nod", "bow", "shrug")] == ["failed", "failed", "queued", "queued"]
    mk.world["fail"] = None
    mk.go("ada")                                               # Generate again: the failed ones too
    assert mk.load("ada")["running"] and {"wave", "nod"} <= set(mk.load("ada")["order"])


def test_a_batch_outlives_a_restart(mk):
    mk.go("ada", ["wave", "nod", "bow"])
    job = mk._take()                                           # wave is being made when Sapphire stops
    assert job == ("ada", "wave")
    saved = json.loads((mk.tmp / "ada" / "maker.json").read_text())
    assert saved["rows"]["wave"]["state"] == "rendering" and saved["running"]
    mk._states.clear()
    st = mk.load("ada")
    assert st["order"] == ["wave", "nod", "bow"] and st["rows"]["wave"]["state"] == "queued" and st["running"]


def test_a_row_removed_while_its_clip_renders_leaves_nothing_behind(mk):
    mk.go("ada", ["wave"])
    job = mk._take()
    mk.row_del("ada", "wave")
    mk._one(*job)                                              # removed before the take began: nothing is made
    assert "wave" not in mk.load("ada")["rows"] and mk.world["renders"] == []
    mk.row_put("ada", "wave", "waves")
    mk.go("ada", ["wave"])
    job = mk._take()
    real = mk._render

    def render_then_removed(persona, st, name, row):
        file, mode = real(persona, st, name, row)
        mk.row_del(persona, name)                              # removed while ComfyUI was working
        (mk.pending(persona) / file).write_bytes(b"clip")       # the file lands after the row went
        return file, mode
    mk._render = render_then_removed
    mk._one(*job)
    assert "wave" not in mk.load("ada")["rows"] and not list(mk.pending("ada").glob("wave.*"))


def test_a_broken_state_file_is_set_aside_not_lost(mk):
    f = mk.tmp / "ada" / "maker.json"
    f.write_text("{not json")
    mk._states.clear()
    assert mk.load("ada")["rows"]["wave"]["state"] == "new"
    assert (mk.tmp / "ada" / "maker.json.bad").read_text() == "{not json"


def test_the_queue_running_dry_ends_the_batch(mk):
    mk.go("ada", ["wave"])
    assert crank(mk) and not crank(mk)
    assert not mk.load("ada")["running"]


# ── poses: she sits, and stays sitting ──────────────────────────────────────
def sits(m):
    for name in ("sit", "idle_sit", "stand"):                 # the list ships with these: taken off, to add them the way a person would
        m.row_del("ada", name)
    m.pose_new("ada", "sit", "sits down on the floor, cross-legged", "stands back up")


def test_a_new_pose_is_three_rows(mk):
    sits(mk)
    rows = mk.load("ada")["rows"]
    assert (rows["sit"].get("from"), rows["sit"]["to"]) == ("home", "sit")
    assert (rows["idle_sit"]["from"], rows["idle_sit"]["to"]) == ("sit", "sit")
    assert (rows["stand"]["from"], rows["stand"].get("to")) == ("sit", "home")
    v = mk.view("ada")
    sit = next(p for p in v["poses"] if p["name"] == "sit")
    assert not sit["has"] and "last frame of the clip 'sit'" in sit["words"][0] and sit["rows"] == 3
    assert next(r for r in v["rows"] if r["name"] == "idle_sit")["needs"] == "sit"
    with pytest.raises(mk.MakerError, match="already an animation"):
        mk.pose_new("ada", "sit", "sits down", "stands back up")
    for bad in ("home", "idle", "sit_2", ""):
        with pytest.raises(mk.MakerError):
            mk.pose_new("ada", bad, "x", "y")
    with pytest.raises(mk.MakerError, match="how she gets"):
        mk.pose_new("ada", "kneel", "kneels", "")


def test_the_clip_that_leads_into_a_pose_finds_it_and_the_rest_wait(mk):
    sits(mk)
    mk.go("ada", ["sit", "idle_sit", "stand"])
    st = mk.load("ada")
    assert st["order"] == ["sit"] and row(mk, "idle_sit")["asked"] and row(mk, "stand")["asked"]
    crank(mk)
    assert mk.world["plans"] == [("sit", "i2v", ["home"], "finds")]          # from her first frame alone, on the one-picture workflow
    assert "holds still in that position" in mk.world["renders"][0][1] and "returns to" not in mk.world["renders"][0][1]
    assert mk.world["asked"] == {"home": "ada/poses/home.png", "clip": "ada/pending/sit.1.mp4"}      # no end picture to hold it against yet
    assert row(mk, "sit")["state"] == "review" and row(mk, "sit")["finds"] is True
    assert not mk.pose_path("ada", "sit").exists()
    mk.verdict("ada", "sit", "keep")                                      # a person keeps it: its last frame IS the pose
    assert mk.pose_path("ada", "sit").read_bytes() == PNG
    pose = mk.load("ada")["poses"]["sit"]
    assert pose["by"] == "peg" and pose["clip"] == "sit" and pose["size"] == [288, 512]
    assert mk.load("ada")["order"] == ["idle_sit", "stand"] and mk.load("ada")["running"]
    crank(mk, 2)
    assert mk.world["plans"][1:] == [("idle_sit", "flf", ["sit", "sit"], ""), ("stand", "flf", ["sit", "home"], "")]
    assert "returns to exactly the starting pose" in mk.world["renders"][1][1]        # her rest in the pose is a loop
    assert "holds still in that position" in mk.world["renders"][2][1]                 # the way back is not
    assert mk.world["asked"]["home"] == "ada/poses/sit.png" and mk.world["asked"]["to"] == "ada/poses/home.png"


def test_the_ai_never_decides_a_pose(mk, monkeypatch):
    monkeypatch.setattr(mk, "_judge", lambda *a, **k: pytest.fail("the vision model was asked to pick a pose"))
    mk.setup("ada", {"auto": True, "judge": "eye"})
    sits(mk)
    mk.go("ada", ["sit"])
    crank(mk)
    assert row(mk, "sit")["state"] == "review" and row(mk, "sit")["judge"] is None


def test_finding_a_pose_needs_a_one_picture_workflow(mk, monkeypatch):
    sits(mk)
    monkeypatch.setattr(mk, "workflows", lambda: [{"name": "flf", "slots": 2, "usable": True, "words": "loops"}])
    with pytest.raises(mk.MakerError, match="one picture"):
        mk.go("ada", ["sit"])
    monkeypatch.setattr(mk, "workflows", lambda: [{"name": "flf", "slots": 2, "usable": True, "words": "loops"},
                                                  {"name": "i2v", "slots": 1, "usable": True, "words": "one picture"}])
    mk.setup("ada", {"workflow_one": ""})                                 # none picked: the one that takes one picture is used
    mk.go("ada", ["sit"])
    assert mk.load("ada")["order"] == ["sit"]


def test_a_way_between_poses_needs_a_first_and_a_last_frame(mk):
    sits(mk)
    mk.pose_put("ada", "sit", PNG)                                        # a person's own picture of her sitting
    mk.setup("ada", {"workflow": "i2v"})
    with pytest.raises(mk.MakerError, match="first and a last frame"):
        mk.go("ada", ["stand"])
    mk.go("ada", ["idle_sit"])                                            # a loop in the pose still can: the prompt asks her back
    crank(mk)
    assert mk.world["plans"][-1] == ("idle_sit", "i2v", ["sit"], "")


def test_a_pose_with_a_picture_of_its_own_is_not_found_but_led_to(mk):
    sits(mk)
    with pytest.raises(mk.MakerError, match="comes first"):
        mk.go("ada", ["idle_sit"])
    assert row(mk, "idle_sit")["asked"] and mk.load("ada")["order"] == []
    mk.pose_put("ada", "sit", PNG)
    assert mk.load("ada")["order"] == ["idle_sit"]                        # the row that waited for the pose goes at once
    mk.go("ada", ["sit"])
    crank(mk, 2)
    assert ("sit", "flf", ["home", "sit"], "") in mk.world["plans"]


def test_the_way_in_made_again_ends_on_the_frame_she_already_stays_in(mk):
    sits(mk)
    mk.go("ada", ["sit"])
    crank(mk)
    mk.verdict("ada", "sit", "keep")
    assert mk.pose_path("ada", "sit").exists()
    mk.verdict("ada", "sit", "again")                                     # a better way in, to the same seated frame
    assert mk.pose_path("ada", "sit").exists() and mk.load("ada")["order"][0] == "sit"
    crank(mk)
    assert mk.world["plans"][-1] == ("sit", "flf", ["home", "sit"], "")      # pinned at both ends: the loop and the way out still fit
    mk.verdict("ada", "sit", "drop")                                      # given up altogether: the seated frame goes with it
    assert not mk.pose_path("ada", "sit").exists() and "sit" not in mk.load("ada")["poses"]


def test_the_checks_hold_each_end_against_its_own_pose(mk):
    assert mk.flags(GOOD, start="home", end="sit") == []
    assert mk.flags({**GOOD, "end": 0.2}, start="home", end="sit")[0] == ["does not end in the 'sit' position", True]
    assert mk.flags({**GOOD, "end": 0.2}, start="home", end="sit", finds=True) == []          # nothing to hold its end against yet
    assert mk.flags({**GOOD, "start": 0.2}, start="sit", end="home")[0] == ["does not start in the 'sit' position", True]
    assert mk.flags({**GOOD, "face_seam": 30.0}, start="sit", end="home")[0][0] == "her face does not end as it is on her picture's pose"
    assert mk.flags({**GOOD, "end": 0.2}, start="sit", end="sit")[0] == ["does not start and end in the 'sit' position", True]
    still = {**GOOD, "peak": 0.006, "wrist": 0.006}
    assert mk.flags(still, idle=True, start="sit", end="sit") == []


def test_a_pose_in_a_clips_name_folds_into_the_animation(mk):
    lib = sys.modules["plugins.vidavatar"].lib
    d = mk.tmp / "ada" / "clips"
    d.mkdir()
    for f in ("idle.mp4", "idle_sit.mp4", "idle_sit_2.mp4", "wave.mp4", "wave_sit.mp4", "sit.mp4", "stand.mp4", "transit.mp4"):
        (d / f).write_bytes(b"x")
    assert lib.names("ada") == ["idle_sit", "sit", "stand", "transit", "wave", "wave_sit"]      # no pose called sit yet
    mk.pose_put("ada", "sit", PNG)
    lib._names.clear()
    assert lib.names("ada") == ["sit", "stand", "transit", "wave"]


def test_the_home_picture_of_the_first_version_moves_with_her(mk):
    mk.home_path("ada").replace(mk.tmp / "ada" / "home.png")
    (mk.tmp / "ada" / "maker.json").write_text(json.dumps({"home": {"words": ["Looks good"], "at": 5}, "rows": {}}))
    mk._states.clear()
    st = mk.load("ada")
    assert mk.home_path("ada").exists() and st["poses"]["home"]["words"] == ["Looks good"] and "home" not in st


# ── how long a clip is ──────────────────────────────────────────────────────
DURATION = [{"name": "Duration", "value": 5, "whole": True, "auto": [1, 20], "min": 1, "max": 20, "off": False},
            {"name": "Frame Rate", "value": 24, "whole": False, "auto": [6, 96], "min": 6, "max": 96, "off": False},
            {"name": "Enable Prompt Enhance", "value": False, "whole": True, "auto": None, "off": False}]
FRAMES = [{"name": "Frames", "value": 121, "whole": True, "auto": [31, 484], "min": 31, "max": 257, "off": False},
          {"name": "Frame Rate", "value": 25, "whole": False, "auto": [6, 100], "min": 6, "max": 100, "off": False}]


def test_a_workflows_own_length_control_is_found(mk):
    d = mk.length_of(DURATION)
    assert (d["name"], d["unit"], d["value"], d["min"], d["max"]) == ("Duration", "seconds", 5, 1, 20)
    f = mk.length_of(FRAMES)
    assert (f["name"], f["unit"], f["value"], f["max"]) == ("Frames", "frames", 4.8, 10.3)
    assert mk.length_of([DURATION[2]]) is None                                  # no length control: its clips are as long as it was saved
    assert mk.length_of([{**DURATION[0], "off": True}]) is None                # a person switched it off in the ComfyUI panel


def test_seconds_become_what_the_workflow_takes(mk):
    d, f = mk.length_of(DURATION), mk.length_of(FRAMES)
    assert mk.extras_for(d, 10) == {"Duration": 10}
    assert mk.extras_for(d, 45) == {"Duration": 20}                             # the nearest it allows
    assert mk.extras_for(d, 0) is None and mk.extras_for(None, 10) is None      # nothing asked, or nothing to set
    assert mk.extras_for(f, 5) == {"Frames": 129}                               # frames in eights plus one
    assert mk.extras_for(f, 60) == {"Frames": 257}
    assert mk.extras_for(f, 0.5) == {"Frames": 33}                              # never under its least, still eights plus one


def test_a_length_for_all_and_one_for_a_row(mk):
    mk.setup("ada", {"seconds": 10})
    mk.row_put("ada", "nod", "nods twice", seconds=3)
    v = mk.view("ada")
    assert v["seconds"] == 10 and next(r for r in v["rows"] if r["name"] == "nod")["seconds"] == 3
    mk.row_put("ada", "nod", "nods twice", seconds=0)                           # emptied: the page's length again
    assert "seconds" not in mk.load("ada")["rows"]["nod"]
    mk.row_put("ada", "nod", "nods three times")                                # its words alone: its length is left be
    for bad in ("long", -1, 500):
        with pytest.raises(mk.MakerError):
            mk.setup("ada", {"seconds": bad})


def test_generate_all_makes_the_sitting_clips_in_the_order_they_need(mk):
    for name in list(mk.load("ada")["rows"]):
        if name not in ("wave", "sit", "idle_sit", "stand"):
            mk.row_del("ada", name)
    mk.go("ada")                                               # Generate all, on a list that has the sitting position
    assert mk.load("ada")["order"] == ["wave", "sit"]          # the two that start from sitting wait for it
    crank(mk, 2)
    mk.verdict("ada", "sit", "keep")
    assert mk.load("ada")["order"] == ["idle_sit", "stand"]
    crank(mk, 2)
    assert [n for n, *_ in mk.world["plans"]] == ["wave", "sit", "idle_sit", "stand"]


def test_a_full_card_is_a_wait_not_a_failure(mk, monkeypatch):
    monkeypatch.setattr(mk, "FULL_WAIT_S", 0)
    mk.world["fail"] = "ComfyUI failed in its CLIPTextEncode node: Allocation on device 0 would exceed allowed memory. (out of memory)"
    mk.go("ada", ["wave", "nod"])
    crank(mk, 3)
    st = mk.load("ada")
    assert st["running"] and st["order"] == ["wave", "nod"] and row(mk, "wave")["state"] == "queued"      # it keeps its place, the batch goes on
    assert "ran out of video memory" in st["note"] and row(mk, "wave")["tries"] == 0
    mk.world["fail"] = None                                    # the card came free
    crank(mk)
    assert row(mk, "wave")["state"] == "review" and "full" not in row(mk, "wave") and mk.load("ada")["note"] == ""
    mk.world["fail"] = "CUDA out of memory"
    crank(mk, mk.FULL_WAITS + 1)                               # it never comes free: said as a failure in the end
    assert row(mk, "nod")["state"] == "failed" and "out of memory" in row(mk, "nod")["error"]


# ── a loop inside an animation: the checkbox on its row ──────────────────────
def test_ticking_loop_inside_makes_an_animation_three_clips(mk):
    mk.row_put("ada", "kneel", "kneels down on one knee")
    mk.loop_set("ada", "kneel", True)
    rows = mk.load("ada")["rows"]
    assert (rows["kneel"].get("from"), rows["kneel"]["to"]) == (None, "kneel")
    assert (rows["idle_kneel"]["from"], rows["idle_kneel"]["to"]) == ("kneel", "kneel")
    assert (rows["kneel_out"]["from"], rows["kneel_out"].get("to")) == ("kneel", None)
    mk.loop_set("ada", "kneel", True)                          # ticked twice: nothing more
    assert len([n for n in rows if "kneel" in n]) == 3
    for bad in ("idle", "idle_2", "idle_sit"):                 # her rest, a numbered take, a clip that starts seated: no place to stay
        with pytest.raises(mk.MakerError):
            mk.loop_set("ada", bad, True)
    with pytest.raises(mk.MakerError, match="no longer in the list"):
        mk.loop_set("ada", "nothing_here", True)


def test_a_clip_made_before_the_tick_is_made_again(mk):
    mk.go("ada", ["bow"])
    crank(mk)
    mk.verdict("ada", "bow", "keep")                           # accepted as a clip that comes back to standing
    assert (mk.tmp / "ada" / "clips" / "bow.mp4").exists()
    mk.loop_set("ada", "bow", True)
    assert row(mk, "bow")["state"] == "new" and not (mk.tmp / "ada" / "clips" / "bow.mp4").exists()
    mk.go("ada", ["bow", "idle_bow", "bow_out"])
    crank(mk)
    assert mk.world["plans"][-1] == ("bow", "i2v", ["home"], "finds")       # now it ends where she stays


def test_unticking_takes_the_loop_and_its_clips_away(mk):
    mk.go("ada", ["sit", "idle_sit", "stand"])                 # the sitting the list ships with
    crank(mk)
    mk.verdict("ada", "sit", "keep")
    crank(mk, 2)
    mk.verdict("ada", "idle_sit", "keep")
    mk.verdict("ada", "stand", "keep")
    clips = mk.tmp / "ada" / "clips"
    assert {p.name for p in clips.iterdir()} == {"sit.mp4", "idle_sit.mp4", "stand.mp4"} and mk.pose_path("ada", "sit").exists()
    mk.loop_set("ada", "sit", False)
    rows = mk.load("ada")["rows"]
    assert "idle_sit" not in rows and "stand" not in rows and "to" not in rows["sit"] and rows["sit"]["state"] == "new"
    assert not list(clips.iterdir()) and not mk.pose_path("ada", "sit").exists() and "sit" not in mk.load("ada")["poses"]
    mk.go("ada", ["sit"])
    crank(mk)
    assert mk.world["plans"][-1] == ("sit", "flf", ["home", "home"], "")      # one clip again: down and back up


def test_any_clip_can_be_rated_out_of_turn(mk):
    mk.go("ada", ["wave", "nod", "bow"])
    crank(mk, 3)
    assert mk.view("ada")["deck"] == ["wave", "nod", "bow"]
    mk.verdict("ada", "bow", "keep")                           # the last one first
    assert row(mk, "bow")["state"] == "kept" and mk.view("ada")["deck"] == ["wave", "nod"]
    mk.verdict("ada", "bow", "again")                          # and an accepted one, looked at again, is made again
    assert mk.load("ada")["order"][0] == "bow" and (mk.tmp / "ada" / "clips" / "bow.mp4").exists()


def test_asking_for_the_loop_before_the_way_in_is_accepted_says_what_to_do(mk):
    with pytest.raises(mk.MakerError, match="Generate 'sit' and accept it"):
        mk.go("ada", ["idle_sit", "stand"])                    # nothing of sit made yet
    assert row(mk, "idle_sit")["asked"] and row(mk, "stand")["asked"]      # the ask is remembered all the same
    mk.go("ada", ["sit"])
    crank(mk)                                                  # sit is made and waits for a thumb
    with pytest.raises(mk.MakerError, match="waits for your thumbs up"):
        mk.go("ada", ["idle_sit", "stand"])
    mk.verdict("ada", "sit", "keep")
    assert mk.load("ada")["order"] == ["idle_sit", "stand"]


def test_accepting_the_way_in_makes_the_loop_and_the_way_out_unasked(mk):
    mk.row_put("ada", "wave_sit", "waves at the camera", "sit", "sit")
    mk.go("ada", ["sit"])                                      # only the way in was asked for
    crank(mk)
    mk.verdict("ada", "sit", "keep")
    assert mk.load("ada")["order"] == ["idle_sit", "stand"]    # the animation is those three; a take made while seated is not
    assert row(mk, "wave_sit")["state"] == "new"


# ── a still of her in a new position, before any clip of it ──────────────────
EDIT = [{"name": "edit", "kind": "image", "slots": 2, "usable": True, "words": "edits a picture"}]


def test_with_an_image_workflow_her_position_starts_as_a_picture_to_accept(mk):
    mk.world["stills"] = EDIT
    mk.setup("ada", {"auto": True, "judge": "eye"})            # even then: the picture of where she stays is a person's
    mk.go("ada", ["sit", "idle_sit", "stand"])
    crank(mk)
    assert mk.world["plans"] == [("sit", "edit", ["home"], "still")]
    assert "a moment after this: the person sits down" in mk.world["renders"][0][1] and "identity unchanged" in mk.world["renders"][0][1]
    r = row(mk, "sit")
    assert r["state"] == "review" and r["still"] is True and r["file"] == "sit.1.png" and r["judge"] is None
    v = mk.view("ada")
    assert v["deck"] == ["sit"] and v["using"] == ["flf", "i2v", "edit"]
    assert next(x for x in v["rows"] if x["name"] == "sit")["url"] == "maker/file/ada/sit.1.png"
    mk.verdict("ada", "sit", "again")                          # not her: another picture
    crank(mk)
    assert mk.world["plans"][-1] == ("sit", "edit", ["home"], "still") and not mk.pose_path("ada", "sit").exists()
    mk.setup("ada", {"auto": False})
    mk.verdict("ada", "sit", "keep")                           # that is her: it becomes the position's picture
    assert mk.pose_path("ada", "sit").exists() and mk.load("ada")["poses"]["sit"]["by"] == "still"
    assert mk.load("ada")["order"] == ["sit", "idle_sit", "stand"] and row(mk, "sit")["state"] == "queued" and "still" not in row(mk, "sit")
    crank(mk, 3)
    assert mk.world["plans"][-3:] == [("sit", "flf", ["home", "sit"], ""), ("idle_sit", "flf", ["sit", "sit"], ""), ("stand", "flf", ["sit", "home"], "")]
    assert row(mk, "sit")["state"] == "review" and not row(mk, "sit").get("finds")       # every clip pinned at both ends: none finds anything


def test_dropping_the_way_in_takes_the_stills_picture_too(mk):
    mk.world["stills"] = EDIT
    mk.go("ada", ["sit"])
    crank(mk)
    mk.verdict("ada", "sit", "keep")
    assert mk.pose_path("ada", "sit").exists()
    mk.verdict("ada", "sit", "again") if row(mk, "sit")["state"] == "review" else None
    crank(mk)
    mk.verdict("ada", "sit", "drop")
    assert not mk.pose_path("ada", "sit").exists() and row(mk, "sit")["state"] == "dropped"


def test_a_clip_with_a_free_end_is_checked_for_zoom_and_a_drifted_face(mk):
    assert mk.flags({**GOOD, "zoom": 1.01, "face_end": 9.0}, start="home", end="sit", finds=True) == []
    zoomed = mk.flags({**GOOD, "zoom": 2.36, "face_end": 9.0}, start="home", end="sit", finds=True)
    assert zoomed == [["the camera moved in: her face is 2.4 times as wide at the end", True]]
    drift = mk.flags({**GOOD, "zoom": 0.99, "face_end": 30.8}, start="home", end="sit", finds=True)
    assert drift[0][0].startswith("her face has drifted from her picture") and drift[0][1] is True
    assert mk.flags({**GOOD, "zoom": 2.36, "face_end": 30.8}) == []          # a clip pinned at both ends is held to its pictures instead
