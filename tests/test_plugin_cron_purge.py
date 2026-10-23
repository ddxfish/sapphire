# tests/test_plugin_cron_purge.py — dynamic plugin cron, boot-purge half
# (commit b555e3b shipped with zero test delta — scout O4 danger rating 8/10:
# scheduling regressions are silent; heartbeats just stop firing.)
import json

from core.continuity.scheduler import ContinuityScheduler


def _bare_scheduler(tmp_path):
    """Scheduler skeleton pointed at a throwaway dir — only the attrs
    _load_tasks/_save_tasks touch."""
    sched = ContinuityScheduler.__new__(ContinuityScheduler)
    sched._base_dir = tmp_path
    sched._tasks_path = tmp_path / "tasks.json"
    return sched


def test_boot_purges_plugin_tasks_keeps_user_tasks(tmp_path):
    """[REGRESSION_GUARD] Plugin-sourced tasks are purged at boot and
    re-register via set_scheduler() — without the purge every restart
    duplicates them; without the source-prefix guard user tasks vanish."""
    (tmp_path / "tasks.json").write_text(json.dumps({"tasks": [
        {"id": "user1", "name": "mine", "source": "user",
         "type": "task", "trigger_config": {}},
        {"id": "plug1", "name": "beat", "source": "plugin:discord",
         "type": "task", "trigger_config": {}},
    ]}), encoding="utf-8")

    sched = _bare_scheduler(tmp_path)
    sched._load_tasks()

    assert "user1" in sched._tasks              # user task kept
    assert "plug1" not in sched._tasks          # plugin task purged (re-registers on load)
    on_disk = json.loads((tmp_path / "tasks.json").read_text(encoding="utf-8"))
    assert [t["id"] for t in on_disk["tasks"]] == ["user1"]  # purge persisted


def test_legacy_task_migration_and_missing_file(tmp_path):
    """Migration net: heartbeat bool → type field, trigger_config backfilled;
    a missing tasks.json is an empty registry, not a crash."""
    (tmp_path / "tasks.json").write_text(json.dumps({"tasks": [
        {"id": "old1", "name": "legacy", "source": "user", "heartbeat": True},
    ]}), encoding="utf-8")
    sched = _bare_scheduler(tmp_path)
    sched._load_tasks()
    assert sched._tasks["old1"]["type"] == "heartbeat"
    assert sched._tasks["old1"]["trigger_config"] == {}

    empty = _bare_scheduler(tmp_path / "nowhere")
    empty._tasks_path = tmp_path / "nowhere" / "tasks.json"
    empty._load_tasks()
    assert empty._tasks == {}
