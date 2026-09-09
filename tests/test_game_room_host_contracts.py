"""Source tripwires for the Game Room room host (F6, 2026-09-09).

The host is ONE lifecycle every room rides; these pin the shape so a later
edit can't quietly grow a second copy of the chrome or send the sealed
seat back into the chat.
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "plugins" / "game-room" / "app"
HOST = (APP / "room-host.js").read_text(encoding="utf-8")
STORY = (APP / "story-room.js").read_text(encoding="utf-8")
INDEX = (APP / "index.js").read_text(encoding="utf-8")
CSS = (APP / "game-room.css").read_text(encoding="utf-8")
MANIFEST = json.loads((ROOT / "plugins" / "game-room" / "plugin.json").read_text(encoding="utf-8"))


def test_old_room_module_is_gone():
    assert not (APP / "room.js").exists()
    assert "./room.js" not in INDEX and "./room.js" not in STORY


def test_host_is_the_one_door():
    for name in ("openRoom", "openGame", "close", "listSessions", "createSession",
                 "ensureSession", "createPrivateSession", "activateSession", "esc"):
        assert re.search(rf"export (async )?function {name}\b", HOST), name
    # the story adapter rides the host — no chrome of its own
    for banned in ("claimOrgans", "releaseOrgans", "claimBackground", "renderSurface",
                   "activateChat(", "BUS_CONNECTED", "PROMPT_CHANGED"):
        assert banned not in STORY, banned
    assert "./room-host.js?v=" in STORY and "./room-host.js?v=" in INDEX


def test_rooms_bind_the_real_rail_by_name():
    assert "claimOrgans({" in HOST and "OWNER, sessionName)" in HOST
    assert "restoreBackdrop(me);\n    releaseOrgans(OWNER);" in HOST   # backdrop BEFORE organs


def test_moves_carry_no_talk_line():
    poker = (ROOT / "plugins" / "game-holdem" / "app" / "poker.js").read_text(encoding="utf-8")
    assert "say" not in poker.split("renderActions")[1]
    assert "clearComposer" not in poker and "clearComposer" not in HOST


def test_one_steer_off_owner():
    assert "activateSession(target)" not in INDEX
    chat = (ROOT / "interfaces" / "web" / "static" / "views" / "chat.js").read_text(encoding="utf-8")
    assert "steerOffGameChat" in chat


def test_manifest_wires_the_mirror():
    hooks = MANIFEST["capabilities"]["hooks"]
    assert hooks["pre_chat"] == "hooks/mirror.py" and hooks["post_chat"] == "hooks/mirror.py"
    assert hooks["ghost_inject"] == "hooks/ghost.py"


def test_stage_modes_have_css():
    for mode in ("side", "stack", "rail", "fullscreen"):
        assert f".gr-mode-{mode}" in CSS, mode
    assert ".gr-talk" not in CSS                       # the talk-blob rail is dead
    assert "[data-keeps-focus]" in (ROOT / "interfaces" / "web" / "static" / "shared" / "dom-guard.js").read_text(encoding="utf-8")


def test_subtitle_shows_only_while_the_rail_is_folded():
    # the coffee-sip rule (2026-09-09): exactly one surface carries her line
    assert 'id="gr-caption"' in HOST
    body = HOST.split("function showCaption")[1].split("function hideCaption")[0]
    assert "railFolded(me)" in body
    assert "hideCaption(me)" in HOST.split("const toggleRail")[1].split("$('#gr-rail-toggle')")[0]
    assert ".gr-caption" in CSS and ".gr-quips" not in CSS


def test_settings_spine_has_one_kit_and_one_key_table():
    # 2026-09-09: room defaults ⊕ game overrides ⊕ session overrides — every
    # key declared once (ROOM_KEYS), every layer edited with the same rows.
    core = (ROOT / "plugins" / "game-room" / "gameroom_core.py").read_text(encoding="utf-8")
    assert "ROOM_KEYS = [" in core and "def effective(" in core and "def coerce_field(" in core
    modal = (APP / "settings-modal.js").read_text(encoding="utf-8")
    for name in ("layerRowsHtml", "wireLayer", "layerAccordionsHtml"):
        assert f"export function {name}" in modal, name
    # Krem's ruling: NO override switches — pre-filled fields, a dot + ↺
    assert "grs-ovr" not in modal and 'type="checkbox" class="grs-ovr' not in modal
    assert "grs-reset-key" in modal and "overridden" in modal
    assert "gr-room-model" not in INDEX and "gr-player-name" not in INDEX   # hand-rolled pickers folded into the kit
    assert "layerAccordionsHtml" in INDEX and "room/settings" in INDEX      # library = room defaults
    assert "initRoomKeys" in HOST and "room_overrides" in HOST               # game room = the game's layer
    assert "room/session-settings" not in HOST                              # no session fields UI (F3's pause writes that layer)
    assert "birthSettings" in HOST                                           # new_session_toolset rides the create
    hooks = MANIFEST["capabilities"]["hooks"]
    assert hooks["prompt_inject"] == "hooks/costume.py"
    routes = {r["path"] for r in MANIFEST["capabilities"]["routes"]}
    assert {"room/settings", "room/effective", "room/session-settings"} <= routes
