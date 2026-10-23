"""plugin_loader.fire_task / tasks_for_source — a daemon fires ONE task it owns.

S0 doors (2026-09-21). emit_daemon_event fans out to every listener of a
source; fire_task targets a task (the Discord daemon's clock firing the
greetings task whose greeting_time just passed). Ownership: the task's source
must belong to the calling plugin; the reply routes to that plugin's handler.
"""
import json

from core.plugin_loader import PluginLoader


class FakeScheduler:
    def __init__(self, tasks):
        self.tasks = {t["id"]: t for t in tasks}
        self.fired = []

    def get_task(self, task_id):
        t = self.tasks.get(task_id)
        return dict(t) if t else None

    def find_tasks_by_event(self, source):
        return [dict(t) for t in self.tasks.values()
                if t.get("type") == "daemon" and t.get("enabled", True)
                and t.get("trigger_config", {}).get("source") == source]

    def fire_event_task(self, task_id, event_data, reply_callback=None, skip_filter=False):
        # fire_task is the owner's own clock — the task's MESSAGE filter must not apply
        assert skip_filter is True
        self.fired.append((task_id, event_data, reply_callback))
        return {"success": True}


def _loader(tasks):
    loader = PluginLoader()
    loader._scheduler = FakeScheduler(tasks)
    loader._event_sources = {
        "discord": [
            {"name": "discord_greetings", "plugin": "discord", "realtime": False},
            {"name": "discord_voice", "plugin": "discord", "realtime": True},
        ],
        "telegram": [{"name": "telegram_message", "plugin": "telegram", "realtime": False}],
    }
    return loader


TASKS = [
    {"id": "t-greet", "type": "daemon", "enabled": True,
     "trigger_config": {"source": "discord_greetings", "account": "sapph",
                        "greeting_time": "08:00", "goodnight_time": "22:30"}},
    {"id": "t-off", "type": "daemon", "enabled": False,
     "trigger_config": {"source": "discord_greetings", "account": "sapph"}},
    {"id": "t-voice", "type": "daemon", "enabled": True,
     "trigger_config": {"source": "discord_voice", "account": "sapph"}},
    {"id": "t-tg", "type": "daemon", "enabled": True,
     "trigger_config": {"source": "telegram_message", "account": "bot"}},
]


def test_fire_task_routes_to_the_owning_plugins_reply_handler():
    loader = _loader(TASKS)
    handler = lambda task, ev, text: None  # noqa: E731
    loader.register_reply_handler("discord", handler)

    result = loader.fire_task("t-greet", {"account": "sapph", "proactive_kind": "greeting"},
                              plugin="discord")

    assert result == {"success": True}
    task_id, data, cb = loader._scheduler.fired[0]
    assert task_id == "t-greet"
    assert json.loads(data) == {"account": "sapph", "proactive_kind": "greeting"}
    assert cb is handler


def test_fire_task_accepts_a_prebuilt_json_string():
    loader = _loader(TASKS)
    loader.fire_task("t-greet", '{"account": "sapph"}', plugin="discord")
    assert loader._scheduler.fired[0][1] == '{"account": "sapph"}'


def test_fire_task_refuses_unknown_task_and_foreign_source():
    loader = _loader(TASKS)
    assert loader.fire_task("nope", {}, plugin="discord")["success"] is False
    # telegram's task is not discord's to fire
    res = loader.fire_task("t-tg", {}, plugin="discord")
    assert res["success"] is False and "not owned" in res["error"]
    assert loader._scheduler.fired == []


def test_fire_task_without_plugin_still_needs_a_declared_source():
    loader = _loader(TASKS)
    loader._event_sources.pop("telegram")
    assert loader.fire_task("t-tg", {})["success"] is False
    assert loader.fire_task("t-greet", {})["success"] is True


def test_fire_task_refuses_realtime_gates():
    loader = _loader(TASKS)
    res = loader.fire_task("t-voice", {"account": "sapph"}, plugin="discord")
    assert res["success"] is False and "realtime" in res["error"]
    assert loader._scheduler.fired == []


def test_fire_task_without_scheduler_is_a_clean_no():
    loader = PluginLoader()
    assert loader.fire_task("t-greet", {})["success"] is False
    assert loader.tasks_for_source("discord_greetings") == []


def test_tasks_for_source_returns_enabled_tasks_with_their_trigger_config():
    loader = _loader(TASKS)
    tasks = loader.tasks_for_source("discord_greetings")
    assert [t["id"] for t in tasks] == ["t-greet"]          # disabled one filtered
    assert tasks[0]["trigger_config"]["greeting_time"] == "08:00"
    assert tasks[0]["trigger_config"]["goodnight_time"] == "22:30"
