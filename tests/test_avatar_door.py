"""The avatar feed's browser door (core/avatar_door.py): who gets her voice, what a
viewer that falls behind loses, and that the hub hands the door what it is handed."""
import json
import queue
import struct

import pytest

from core import avatar_door


def head(pkt):
    n = struct.unpack(">I", pkt[:4])[0]
    return json.loads(pkt[4:4 + n]), pkt[4 + n:]


@pytest.fixture
def door():
    del avatar_door._viewers[:]
    yield avatar_door
    del avatar_door._viewers[:]


def slice_(taken=True, kind="speech"):
    return {"kind": kind, "fps": 25, "sr": 24000, "sid": "s1", "taken": taken}, [b"JPEG1", b"JP2"], b"\x01\x00" * 8


def test_a_taken_slice_is_voiced_by_the_newest_viewer_with_sound_alone(door):
    old, silent, new = door.join(True), door.join(False), door.join(True)
    door.give(*slice_())
    h_old, body_old = head(old.get(0.1))
    h_silent, _ = head(silent.get(0.1))
    h_new, body_new = head(new.get(0.1))
    assert h_new["audio"] == 16 and body_new == b"\x01\x00" * 8 + b"JPEG1JP2" and "mute" not in h_new
    assert h_old["audio"] == 0 and h_old["mute"] and body_old == b"JPEG1JP2"      # frames, no voice: two tabs never echo
    assert h_silent["audio"] == 0 and h_silent["frames"] == [5, 3]


def test_a_slice_core_plays_itself_is_mouthed_in_silence_by_everyone(door):
    v = door.join(True)
    door.give(*slice_(taken=False))
    h, body = head(v.get(0.1))
    assert h["audio"] == 0 and h["mute"] and body == b"JPEG1JP2"


def test_idle_never_carries_sound_and_voiced_says_who_can_take_her_voice(door):
    assert not door.voiced() and door.viewers() == 0
    v = door.join(False)
    assert not door.voiced() and door.viewers() == 1
    w = door.join(True)
    assert door.voiced()
    door.give({"kind": "idle", "fps": 25, "sr": 24000, "taken": True}, [b"J"], b"\x00\x00")
    assert head(w.get(0.1))[0]["audio"] == 0
    door.leave(w)
    assert not door.voiced()
    door.leave(v)
    door.leave(v)                                   # twice is no error
    assert door.viewers() == 0


def test_flush_and_gone_reach_every_viewer_with_their_words(door):
    a, b = door.join(True), door.join(False)
    door.tell("flush", sid="s9")
    door.tell("gone", why="vidavatar let her face go")
    for v in (a, b):
        assert head(v.get(0.1))[0] == {"kind": "flush", "sid": "s9", "audio": 0, "frames": []}
        assert head(v.get(0.1))[0]["why"] == "vidavatar let her face go"


def test_a_viewer_that_falls_behind_loses_its_oldest_slice_and_nobody_waits(door):
    v = door.join(True)
    for i in range(avatar_door.QUEUE + 5):
        door.give({"kind": "idle", "fps": 25, "sr": 24000, "n": i}, [b"J"], b"")
    assert v.q.qsize() == avatar_door.QUEUE and v.dropped == 5
    assert head(v.get(0.1))[0]["n"] == 5            # the five oldest went
    assert door.status() == {"viewers": 1, "sound": 1, "dropped": 5}
    with pytest.raises(queue.Empty):
        door.join(True).get(0.01)


def test_no_viewer_costs_nothing_and_a_bad_slice_never_raises(door):
    door.give(*slice_())                            # nobody: nothing built, nothing kept
    v = door.join(True)
    door.give({"kind": "speech", "taken": True}, [object()], b"x")    # not bytes: said in the log, never raised
    with pytest.raises(queue.Empty):
        v.get(0.01)


def test_the_hub_hands_the_door_each_slice_flush_and_release(door, monkeypatch):
    from core import avatar_feed
    monkeypatch.setattr(avatar_feed, "_seat", lambda: "")
    monkeypatch.setattr(avatar_feed, "_hub", None)
    v = door.join(True)
    stopped = []
    assert avatar_feed.register("door-test", stop=lambda sid: stopped.append(sid) or True) == ""
    try:
        assert avatar_feed.packet("door-test", "speech", [b"JPEG"], 25, pcm=b"\x02\x00" * 4, sid="r1", taken=True)
        h, body = head(v.get(0.5))
        assert (h["kind"], h["sid"], h["taken"], h["audio"], h["frames"]) == ("speech", "r1", True, 8, [4])
        assert body == b"\x02\x00" * 4 + b"JPEG"
        assert not avatar_feed.packet("somebody-else", "idle", [b"J"], 25)      # not her face: the door hears nothing
        avatar_feed.flush("door-test", "r1")
        assert head(v.get(0.5))[0]["kind"] == "flush"
        assert avatar_feed.stop("r1") is True and stopped == ["r1"]            # a tap on her face reaches the source
    finally:
        avatar_feed.release("door-test")
    assert head(v.get(0.5))[0]["kind"] == "gone"
    assert avatar_feed.stop("r1") is False                                     # no face, nothing to stop


def test_a_small_screen_frames_her_head_and_shoulders_when_the_source_says_where_her_face_is():
    from core import avatar_feed
    # her face: centred at 50% across, 13% down, 17% of a 704x1280 picture tall; a 320x240 screen that fills its glass
    l, t, r, b = avatar_feed._window((0.5, 0.13, 0.17), 704, 1280, 320, 240)
    assert (r - l) * 240 == pytest.approx((b - t) * 320, rel=0.02)               # the screen's own shape: nothing is squashed
    assert (l, r) == (0, 704) and b - t == 528                                   # three faces tall would be wider than the picture: its full width, then
    l2, t2, r2, b2 = avatar_feed._window((0.5, 0.13, 0.17), 704, 1280, 240, 240)
    assert b2 - t2 == pytest.approx(3.0 * 0.17 * 1280, abs=2) and abs((r2 - l2) - (b2 - t2)) <= 1   # a square screen: three faces tall
    face_y = 0.13 * 1280
    assert t <= face_y - 0.17 * 1280 / 2 and b >= face_y + 0.17 * 1280 / 2       # her whole face is in it
    assert l >= 0 and t >= 0 and r <= 704 and b <= 1280
    # a face in a corner: the window is pushed back inside the picture, never past its edge
    assert avatar_feed._window((0.02, 0.02, 0.2), 704, 1280, 240, 240)[:2] == (0, 0)
    l, t, r, b = avatar_feed._window((0.98, 0.98, 0.2), 704, 1280, 240, 240)
    assert (r, b) == (704, 1280)
    # a face that fills the picture: the window is the picture, cut to the screen's shape
    l, t, r, b = avatar_feed._window((0.5, 0.5, 0.9), 512, 512, 320, 240)
    assert (l, r) == (0, 512) and b - t == 384


def test_a_source_that_gives_no_focus_changes_nothing_and_a_bad_one_is_ignored(door, monkeypatch):
    from core import avatar_feed
    monkeypatch.setattr(avatar_feed, "_seat", lambda: "")
    monkeypatch.setattr(avatar_feed, "_hub", None)
    assert avatar_feed.register("focus-test") == ""
    try:
        assert avatar_feed.packet("focus-test", "idle", [b"J"], 25)
        assert avatar_feed._hub.focus is None                                    # FlashHead, the 3D avatar: as before
        assert avatar_feed.packet("focus-test", "idle", [b"J"], 25, focus="nonsense")
        assert avatar_feed._hub.focus is None
        assert avatar_feed.packet("focus-test", "idle", [b"J"], 25, focus=[0.5, 0.13, 0.17])
        assert avatar_feed._hub.focus == (0.5, 0.13, 0.17)
        assert avatar_feed.packet("focus-test", "idle", [b"J"], 25)              # a slice without one: the last stands
        assert avatar_feed._hub.focus == (0.5, 0.13, 0.17)
    finally:
        avatar_feed.release("focus-test")


def test_the_avatar_whose_body_a_chat_shows_is_the_one_that_claims_it(monkeypatch):
    from core import avatar_feed
    monkeypatch.setattr(avatar_feed, "_bodies", {})
    assert avatar_feed.body_for("any chat") is None                              # nobody claims: the 3D avatar's, as ever
    avatar_feed.body_claim("vidavatar", lambda chat: chat == "hers")
    assert avatar_feed.body_for("hers") == "vidavatar" and avatar_feed.body_for("his") is None
    avatar_feed.body_claim("broken", lambda chat: 1 / 0)                         # a claim that throws claims nothing
    assert avatar_feed.body_for("his") is None and avatar_feed.body_for("hers") == "vidavatar"
    avatar_feed.body_claim("vidavatar", None)
    assert avatar_feed.body_for("hers") is None


def test_the_3d_avatar_leaves_her_prompt_alone_in_a_chat_that_shows_another_body(monkeypatch):
    import importlib.util, types
    from pathlib import Path
    from core import avatar_feed
    path = Path(__file__).resolve().parent.parent / "core" / "plugins" / "avatar" / "hooks" / "prompt_inject.py"
    spec = importlib.util.spec_from_file_location("avatar3d_prompt_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    from core.plugin_loader import plugin_loader
    monkeypatch.setattr(plugin_loader, "get_plugin_state", lambda name: {})      # its switches at their defaults: the real install's state is never read
    monkeypatch.setattr(mod, "_get_active_model", lambda: "test.glb")
    monkeypatch.setattr(mod, "_get_track_names", lambda p: ["wave", "nod"])
    monkeypatch.setattr(mod, "state_stage", lambda: {})
    monkeypatch.setattr(avatar_feed, "_bodies", {"vidavatar": lambda chat: chat == "video chat"})
    ev = types.SimpleNamespace(chat_name="video chat", context_parts=[])
    mod.prompt_inject(ev)
    assert ev.context_parts == []                                                # her video body's plugin tells her its own names
    ev = types.SimpleNamespace(chat_name="another chat", context_parts=[])
    mod.prompt_inject(ev)
    assert len(ev.context_parts) == 1 and "wave, nod" in ev.context_parts[0]    # every other chat: the 3D avatar, as ever
