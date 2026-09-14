"""Plugin schedules ride their settings instead of polling.

Discord 1.25 declared three ``*/15`` heartbeats plus an hourly one and guarded
the real moment inside each handler: every 15 minutes get_self_info's
"Upcoming" told Sapphire goodnight was due at 10:45 in the morning, and the
tasks ran 96x/day to discover the feature was off. Now ``_time_to_cron``
accepts a bare hour so ``time_setting`` can bind to the existing numeric hour
settings, ``enabled_setting`` gates the polls, and the status view skips
plugin heartbeats (sub-hourly plugin tasks) so her Upcoming shows events.
"""
import importlib
import json
import types
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from core.plugin_loader import PluginLoader, _time_to_cron

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "plugins" / "discord" / "plugin.json"


@pytest.mark.parametrize("value,expected", [
    ("07:30", "30 7 * * *"),
    (9, "0 9 * * *"),
    (9.0, "0 9 * * *"),
    ("22", "0 22 * * *"),
    (0, "0 0 * * *"),
])
def test_time_to_cron_accepts_hhmm_and_bare_hour(value, expected):
    assert _time_to_cron(value, "*/15 * * * *") == expected


@pytest.mark.parametrize("value", [24, -1, "junk", None, "", "25:00", "9:60",
                                   float("nan"), float("inf")])
def test_time_to_cron_falls_back_on_garbage(value):
    assert _time_to_cron(value, "0 22 * * *") == "0 22 * * *"


def _sched_def(settings, sched):
    fake = types.SimpleNamespace(get_plugin_settings=lambda name: settings)
    return PluginLoader._sched_task_def(fake, "discord", sched, Path("/x"))


def test_sched_task_def_binds_hour_and_switch():
    sched = {"name": "sleep_goodnight", "cron": "0 22 * * *", "enabled": False,
             "time_setting": "proactive.sleep_utc_hour",
             "enabled_setting": "proactive.sleep_schedule_enabled", "handler": "h.py"}
    d = _sched_def({"proactive.sleep_utc_hour": 23,
                    "proactive.sleep_schedule_enabled": True}, sched)
    assert d["schedule"] == "0 23 * * *"
    assert d["enabled"] is True
    assert d["source"] == "plugin:discord"
    d = _sched_def({"proactive.sleep_utc_hour": "junk",
                    "proactive.sleep_schedule_enabled": False}, sched)
    assert d["schedule"] == "0 22 * * *"
    assert d["enabled"] is False


def test_discord_manifest_schedules_are_bound():
    caps = json.loads(MANIFEST.read_text(encoding="utf-8"))["capabilities"]
    settings = {s["key"]: s for s in caps["settings"] if "key" in s}
    by_name = {s["name"]: s for s in caps["schedule"]}
    # Gated polls: off by default, bound to the feature switch.
    for name, key in [("quiet_outreach", "proactive.outreach_enabled"),
                      ("sleep_goodnight", "proactive.sleep_schedule_enabled"),
                      ("ambient_distill", "profile.ambient_distill_enabled")]:
        s = by_name[name]
        assert s["enabled_setting"] == key and key in settings, name
        assert s["enabled"] is False and settings[key]["default"] is False, name
    # Timed once a day at the bound hour; manifest cron equals the default hour.
    for name, key in [("morning_greeting", "proactive.greeting_utc_hour"),
                      ("sleep_goodnight", "proactive.sleep_utc_hour")]:
        s = by_name[name]
        assert s["time_setting"] == key and key in settings, name
        minute, hour = s["cron"].split()[:2]
        assert minute.isdigit() and hour.isdigit(), name
        assert _time_to_cron(settings[key]["default"], "BAD") == s["cron"], name
    # Nothing polls sub-hourly without a feature gate.
    for s in caps["schedule"]:
        if not s["cron"].split()[0].isdigit():
            assert s.get("enabled_setting"), f"{s['name']} polls without a gate"


@pytest.mark.parametrize("source,cron,hidden", [
    ("plugin:discord", "*/15 * * * *", True),
    ("plugin:discord", "* * * * *", True),
    ("plugin:discord", "0,30 * * * *", True),
    ("plugin:discord", "0 * * * *", False),
    ("plugin:discord", "30 4 * * *", False),
    ("user", "*/15 * * * *", False),
    ("", "*/15 * * * *", False),
])
def test_is_plugin_heartbeat(source, cron, hidden):
    status = importlib.import_module("plugins.status.routes.status")
    assert status._is_plugin_heartbeat({"source": source}, cron) is hidden


def test_upcoming_skips_plugin_heartbeats_keeps_events_and_user_tasks():
    status = importlib.import_module("plugins.status.routes.status")
    soon = (datetime.now() + timedelta(hours=1)).hour
    tasks = [
        {"name": "user_poll", "enabled": True, "schedule": "*/5 * * * *", "source": "user"},
        {"name": "quiet_outreach", "enabled": True, "schedule": "*/15 * * * *",
         "source": "plugin:discord"},
        {"name": "sleep_goodnight", "enabled": True, "schedule": f"0 {soon} * * *",
         "source": "plugin:discord"},
        {"name": "off_poll", "enabled": False, "schedule": "*/15 * * * *",
         "source": "plugin:discord"},
    ]
    sched = types.SimpleNamespace(list_tasks=lambda: tasks)
    names = [t["name"] for t in status._get_upcoming_tasks(sched, hours=4)]
    assert "user_poll" in names and "sleep_goodnight" in names
    assert "quiet_outreach" not in names and "off_poll" not in names
