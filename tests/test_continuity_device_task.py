# tests/test_continuity_device_task.py — the Task editor's "Device" radio
# (2026-10-06): a scheduled task that runs ONE device action with no LLM.
#
# - the scheduler checks device_action against what the device describes
#   at save time: unknown device / capability / action, a device turned
#   off, and any `danger` action (card format) are ValueErrors (400s)
# - create/update store it; {} on update takes a task back to Chat
# - the executor routes a device task to engine.run(owner=True) before any
#   LLM machinery; ok=False is a task error that publishes the toast event
# - backup_targets.ship() alerts per failed target (was log-only)
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from core.continuity.scheduler import ContinuityScheduler
from core.continuity.executor import ContinuityExecutor
from core.devices import engine


ROW = {"id": "esp32", "label": "Sapph", "enabled": True, "parts": [{"driver": "satellite"}]}
CAPS = [
    {"capability": "storage", "label": "Backup", "help": "", "driver": "satellite", "error": "",
     "lockable": True, "locked": False,
     "actions": {"backup": {"help": "ship one now", "example": "", "values": "", "owner": False, "danger": ""},
                 "list": {"help": "", "example": "", "values": "", "owner": False, "danger": ""},
                 "format": {"help": "", "example": "", "values": "", "owner": True,
                            "danger": "Everything on the card is erased."}}},
    {"capability": "camera", "label": "Camera", "help": "", "driver": "satellite", "error": "no lens",
     "lockable": True, "locked": False, "actions": {}},
]


def _get(device_id):
    if device_id != "esp32":
        raise engine.DeviceError(f"There is no device named '{device_id}'.")
    return dict(ROW)


@pytest.fixture
def fake_engine():
    with patch.object(engine, "get", side_effect=_get), \
         patch.object(engine, "describe", return_value=CAPS):
        yield


def _sched(tmp_path):
    s = ContinuityScheduler.__new__(ContinuityScheduler)
    s._base_dir = tmp_path
    s._tasks_path = tmp_path / "tasks.json"
    s._activity_path = tmp_path / "activity.json"
    s._lock = threading.Lock()
    s._tasks = {}
    s._activity = []
    s._task_pending, s._task_running, s._task_last_matched = {}, {}, {}
    s._vault_ref_sync = lambda *a, **k: None
    return s


# --- the save-time check ---------------------------------------------------

def test_empty_is_chat(fake_engine):
    assert ContinuityScheduler._check_device_action({}) == {}
    assert ContinuityScheduler._check_device_action({"device_action": {}}) == {}
    assert ContinuityScheduler._check_device_action({"device_action": None}) == {}


def test_good_action_comes_back_clean(fake_engine):
    out = ContinuityScheduler._check_device_action(
        {"device_action": {"device": " esp32 ", "capability": "storage", "action": "backup", "value": None}})
    assert out == {"device": "esp32", "capability": "storage", "action": "backup", "value": ""}


@pytest.mark.parametrize("da,words", [
    ({"device": "nope", "capability": "storage", "action": "backup"}, "no device named"),
    ({"device": "esp32", "capability": "mic", "action": "backup"}, "has no 'mic'"),
    ({"device": "esp32", "capability": "storage", "action": "wipe"}, "no action 'wipe'"),
    ({"device": "esp32", "capability": "camera", "action": "picture"}, "no lens"),
    ({"device": "esp32", "capability": "storage", "action": ""}, "needs a device"),
    ("backup", "must be an object"),
])
def test_bad_actions_are_400s(fake_engine, da, words):
    with pytest.raises(ValueError, match=words):
        ContinuityScheduler._check_device_action({"device_action": da})


def test_danger_actions_never_schedule(fake_engine):
    with pytest.raises(ValueError, match="I UNDERSTAND"):
        ContinuityScheduler._check_device_action(
            {"device_action": {"device": "esp32", "capability": "storage", "action": "format"}})


def test_disabled_device_refused():
    off = dict(ROW, enabled=False)
    with patch.object(engine, "get", return_value=off), patch.object(engine, "describe", return_value=CAPS):
        with pytest.raises(ValueError, match="turned off"):
            ContinuityScheduler._check_device_action(
                {"device_action": {"device": "esp32", "capability": "storage", "action": "backup"}})


# --- create / update -----------------------------------------------------------

def test_create_stores_it_and_update_clears_it(tmp_path, fake_engine):
    s = _sched(tmp_path)
    da = {"device": "esp32", "capability": "storage", "action": "backup", "value": ""}
    t = s.create_task({"name": "Card backup", "schedule": "0 4 * * 0", "device_action": da})
    assert t["device_action"] == da
    assert t["initial_message"] == ""          # not "Hello." — nobody reads it
    assert t["type"] == "task"                 # cron, columns, timeline unchanged

    s.update_task(t["id"], {"device_action": {}})
    assert s._tasks[t["id"]]["device_action"] == {}

    with pytest.raises(ValueError, match="I UNDERSTAND"):
        s.update_task(t["id"], {"device_action": dict(da, action="format")})
    assert s._tasks[t["id"]]["device_action"] == {}      # the bad edit changed nothing


def test_chat_task_unchanged(tmp_path, fake_engine):
    s = _sched(tmp_path)
    t = s.create_task({"name": "Morning", "schedule": "0 9 * * *"})
    assert t["device_action"] == {}
    assert t["initial_message"] == "Hello."


# --- the executor ------------------------------------------------------------------

def _executor():
    ex = ContinuityExecutor.__new__(ContinuityExecutor)
    ex.system = None
    return ex


def test_device_task_runs_as_owner_and_skips_the_llm():
    ex = _executor()
    seen = {}
    task = {"id": "1", "name": "Card backup",
            "device_action": {"device": "esp32", "capability": "storage", "action": "backup", "value": ""}}
    got = []
    with patch.object(engine, "run", side_effect=lambda *a, **k: (seen.update(args=a, kw=k) or ("Backing up to Sapph now", True))), \
         patch.object(ContinuityExecutor, "_run_background", side_effect=AssertionError("LLM path touched")), \
         patch.object(ContinuityExecutor, "_run_foreground", side_effect=AssertionError("LLM path touched")):
        r = ex.run(task, response_callback=got.append)
    assert r["success"] is True
    assert r["responses"] == [{"output": "Backing up to Sapph now"}]
    assert seen["args"] == ("esp32", "storage", "backup", "")
    assert seen["kw"] == {"owner": True}
    assert got == ["Backing up to Sapph now"]


def test_device_task_failure_is_loud():
    ex = _executor()
    task = {"id": "1", "name": "Card backup",
            "device_action": {"device": "esp32", "capability": "storage", "action": "backup"}}
    published = []
    with patch.object(engine, "run", return_value=("esp32 is offline", False)), \
         patch("core.continuity.executor.publish", side_effect=lambda ev, data: published.append((ev, data))):
        r = ex.run(task)
    assert r["success"] is False
    assert r["errors"] == ["esp32 is offline"]
    assert published and published[0][1] == {"task": "Card backup", "error": "esp32 is offline"}


def test_device_task_crash_is_a_result_not_an_exception():
    ex = _executor()
    task = {"id": "1", "name": "Card backup",
            "device_action": {"device": "esp32", "capability": "storage", "action": "backup"}}
    with patch.object(engine, "run", side_effect=RuntimeError("boom")), \
         patch("core.continuity.executor.publish"):
        r = ex.run(task)
    assert r["success"] is False
    assert "RuntimeError" in r["errors"][0]


# --- ship failures alert ---------------------------------------------------------------

def test_ship_alerts_per_failed_target(tmp_path):
    from core import backup_targets as bt
    from core.backup import backup_manager

    class Bad(bt.Target):
        kind, remote, label = "fake", False, "stick"
        encrypt = False
        def names(self): return []
        def put(self, path, name): raise OSError("no space left")

    class Good(Bad):
        label = "card"
        def __init__(self): self.have = {}
        def names(self): return list(self.have)
        def sizes(self): return dict(self.have)
        def put(self, path, name): self.have[name] = path.stat().st_size
        def delete(self, name): self.have.pop(name, None)
        def put_openers(self): pass

    plain = tmp_path / "sapphire_backup_manual_20261006_010000.tar.gz"
    plain.write_bytes(b"x" * 10)
    alerts = []
    with patch.object(backup_manager, "get_backup_path", return_value=plain), \
         patch.object(backup_manager, "_alert", side_effect=lambda kind, **d: alerts.append((kind, d))):
        results = bt.ship(plain.name, to=[Bad(), Good()])
    by = {r["target"]: r for r in results}
    assert by["card"]["ok"] and not by["stick"]["ok"]
    assert alerts == [("backup_ship_failed", {"target": "stick", "reason": by["stick"]["msg"]})]


# --- the Send-to-devices button (2026-10-06) -------------------------------------------

def _good_target(label="card"):
    from core import backup_targets as bt

    class Good(bt.Target):
        kind, remote, encrypt = "fake", False, False
        def __init__(self): self.have = {}; self.label = label
        def names(self): return list(self.have)
        def sizes(self): return dict(self.have)
        def put(self, path, name): self.have[name] = path.stat().st_size
        def delete(self, name): self.have.pop(name, None)
        def put_openers(self): pass
    return Good()


def test_send_async_refusals(tmp_path):
    from core import backup_targets as bt
    from core.backup import backup_manager, BackupRefused
    with patch.object(bt, "targets", return_value=[]):
        with pytest.raises(BackupRefused, match="No device holds backups"):
            bt.send_async()
    t = _good_target()
    with patch.object(bt, "targets", return_value=[t]), \
         patch.object(backup_manager, "newest_backup", return_value=None):
        with pytest.raises(BackupRefused, match="No backup to send"):
            bt.send_async()
    with patch.object(bt, "targets", return_value=[t]), \
         patch.object(backup_manager, "get_backup_path", return_value=None):
        with pytest.raises(BackupRefused, match="not found"):
            bt.send_async("sapphire_backup_manual_x.tar.gz")
    with patch.object(bt, "targets", return_value=[t]), patch.object(bt, "shipping", "busy.tar.gz"):
        with pytest.raises(BackupRefused, match="Already sending"):
            bt.send_async("whatever.tar.gz")


def test_send_async_ships_newest_and_status_tracks_it(tmp_path):
    from core import backup_targets as bt
    from core.backup import backup_manager
    plain = tmp_path / "sapphire_backup_daily_20261006_030000.tar.gz"
    plain.write_bytes(b"y" * 7)
    t = _good_target()
    with patch.object(bt, "targets", return_value=[t]), \
         patch.object(backup_manager, "newest_backup", return_value={"filename": plain.name}), \
         patch.object(backup_manager, "get_backup_path", return_value=plain), \
         patch.object(backup_manager, "_alert"):
        fn, labels = bt.send_async()
        assert (fn, labels) == (plain.name, ["card"])
        for _ in range(100):               # the thread lands within a moment
            if bt.status()["shipping"] is None and bt.last_ship and bt.last_ship["filename"] == plain.name:
                break
            threading.Event().wait(0.02)
        st = bt.status()
    assert st["shipping"] is None
    assert st["targets"] == ["card"]
    assert st["last_ship"]["results"][0]["ok"] and "verified" in st["last_ship"]["results"][0]["msg"]
    assert t.have == {plain.name: 7}


def test_ship_refusal_lands_in_last_ship(tmp_path):
    from core import backup_targets as bt
    from core.backup import backup_manager, BackupRefused
    with patch.object(backup_manager, "get_backup_path", return_value=None):
        with pytest.raises(BackupRefused):
            bt.ship("gone.tar.gz", to=[_good_target()])
    assert bt.shipping is None
    assert bt.last_ship["filename"] == "gone.tar.gz" and "not found" in bt.last_ship["error"]
