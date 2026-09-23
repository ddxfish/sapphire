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


# The Discord plugin's own proactive schedule legs left with the proactive
# family (S1, 2026-09-22 — greetings ride the daemon task's own clock now).
# The binding contract they proved lives on against a fixture manifest.
FIXTURE = {
    "settings": [
        {"key": "proactive.outreach_enabled", "type": "boolean", "default": False},
        {"key": "proactive.sleep_schedule_enabled", "type": "boolean", "default": False},
        {"key": "profile.ambient_distill_enabled", "type": "boolean", "default": False},
        {"key": "proactive.greeting_utc_hour", "type": "number", "default": 9},
        {"key": "proactive.sleep_utc_hour", "type": "number", "default": 22},
    ],
    "schedule": [
        {"name": "morning_greeting", "cron": "0 9 * * *", "time_setting": "proactive.greeting_utc_hour", "handler": "h.py"},
        {"name": "quiet_outreach", "cron": "*/15 * * * *", "enabled": False,
         "enabled_setting": "proactive.outreach_enabled", "handler": "h.py"},
        {"name": "sleep_goodnight", "cron": "0 22 * * *", "enabled": False, "time_setting": "proactive.sleep_utc_hour",
         "enabled_setting": "proactive.sleep_schedule_enabled", "handler": "h.py"},
        {"name": "ambient_distill", "cron": "*/15 * * * *", "enabled": False,
         "enabled_setting": "profile.ambient_distill_enabled", "handler": "h.py"},
    ],
}


def test_fixture_manifest_schedules_are_bound():
    settings = {s["key"]: s for s in FIXTURE["settings"]}
    by_name = {s["name"]: s for s in FIXTURE["schedule"]}
    for name, key in [("quiet_outreach", "proactive.outreach_enabled"),
                      ("sleep_goodnight", "proactive.sleep_schedule_enabled"),
                      ("ambient_distill", "profile.ambient_distill_enabled")]:
        s = by_name[name]
        assert s["enabled_setting"] == key and key in settings, name
        assert s["enabled"] is False and settings[key]["default"] is False, name
    for name, key in [("morning_greeting", "proactive.greeting_utc_hour"),
                      ("sleep_goodnight", "proactive.sleep_utc_hour")]:
        s = by_name[name]
        assert s["time_setting"] == key and key in settings, name
        minute, hour = s["cron"].split()[:2]
        assert minute.isdigit() and hour.isdigit(), name
        assert _time_to_cron(settings[key]["default"], "BAD") == s["cron"], name


def test_discord_manifest_has_no_proactive_legs_left():
    caps = json.loads(MANIFEST.read_text(encoding="utf-8"))["capabilities"]
    names = {s["name"] for s in caps["schedule"]}
    assert names == set()   # 2.0: no schedule legs at all — the message store and its purge are gone
    keys = {s["key"] for s in caps["settings"] if "key" in s}
    assert not any(k.startswith(("proactive.", "presence.", "profile.")) for k in keys)
    sources = {s["name"]: s for s in caps["daemon"]["event_sources"]}
    assert set(sources) == {"discord_message", "discord_greetings", "discord_all", "discord_voice"}
    assert sources["discord_voice"].get("realtime") is True
    # S6.1: the voice rule picks its channels with daemon filter rows (blank = all), auto-join is opt-in.
    voice = sources["discord_voice"]
    assert [f["key"] for f in voice["task_fields"]] == ["account", "auto_join", "keep_chat_history"]
    assert next(f for f in voice["task_fields"] if f["key"] == "auto_join")["default"] is False
    assert {"guild_name", "channel_name", "guild_name_not", "channel_name_not", "guild_id", "channel_id"} == {f["key"] for f in voice["filter_fields"]}
    for name in ("discord_greetings", "discord_all"):
        fields = {f["key"]: f for f in sources[name]["task_fields"]}
        assert {"account", "channels", "greeting_time", "goodnight_time"} <= set(fields)
        assert fields["greeting_time"].get("widget") == "time"
    assert "auto_reply" in {f["key"] for f in sources["discord_all"]["task_fields"]}
