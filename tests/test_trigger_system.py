"""Tests for the v2.2.10 trigger system — scheduler type/trigger_config, executor event_data,
plugin daemon capability, and event source registry."""

import json
import uuid
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest


# ============================================================================
# Scheduler: type field, trigger_config, migration
# ============================================================================

class TestSchedulerTypeField:
    """Test that the scheduler handles the new type and trigger_config fields."""

    def _make_scheduler(self, tmp_path, tasks=None):
        """Create a ContinuityScheduler with mocked system/executor and optional seed tasks."""
        from core.continuity.scheduler import ContinuityScheduler

        base_dir = tmp_path / "user" / "continuity"
        base_dir.mkdir(parents=True)

        if tasks is not None:
            (base_dir / "tasks.json").write_text(
                json.dumps({"tasks": tasks}), encoding="utf-8"
            )

        mock_system = MagicMock()
        mock_executor = MagicMock()

        sched = ContinuityScheduler.__new__(ContinuityScheduler)
        sched.system = mock_system
        sched.executor = mock_executor
        sched._running = False
        sched._thread = None
        sched._lock = threading.Lock()
        sched._base_dir = base_dir
        sched._tasks_path = base_dir / "tasks.json"
        sched._activity_path = base_dir / "activity.json"
        sched._tasks = {}
        sched._activity = []
        sched._task_running = {}
        sched._task_pending = {}
        sched._task_last_matched = {}
        sched._task_progress = {}
        sched._load_tasks()
        sched._load_activity()

        return sched

    def test_create_task_default_type(self, tmp_path):
        """New task without explicit type defaults to 'task'."""
        sched = self._make_scheduler(tmp_path)
        task = sched.create_task({"name": "Test", "schedule": "0 9 * * *"})
        assert task["type"] == "task"
        assert task["trigger_config"] == {}

    def test_create_daemon_task(self, tmp_path):
        """Daemon task stores type and trigger_config."""
        sched = self._make_scheduler(tmp_path)
        task = sched.create_task({
            "name": "Discord Bot",
            "type": "daemon",
            "schedule": "0 0 31 2 *",
            "trigger_config": {"source": "discord_message", "filter": {"channel": "general"}},
        })
        assert task["type"] == "daemon"
        assert task["trigger_config"]["source"] == "discord_message"
        assert task["trigger_config"]["filter"]["channel"] == "general"

    def test_create_webhook_task(self, tmp_path):
        """Webhook task stores path and method in trigger_config."""
        sched = self._make_scheduler(tmp_path)
        task = sched.create_task({
            "name": "Deploy Hook",
            "type": "webhook",
            "schedule": "0 0 31 2 *",
            "trigger_config": {"path": "deploy", "method": "POST"},
        })
        assert task["type"] == "webhook"
        assert task["trigger_config"]["path"] == "deploy"

    def test_create_heartbeat_backward_compat(self, tmp_path):
        """heartbeat=True still works, sets type='heartbeat'."""
        sched = self._make_scheduler(tmp_path)
        task = sched.create_task({
            "name": "Health Check",
            "heartbeat": True,
            "schedule": "*/5 * * * *",
        })
        assert task["type"] == "heartbeat"
        assert task["heartbeat"] is True

    def test_migration_adds_type_field(self, tmp_path):
        """Legacy tasks without type get migrated on load."""
        legacy_tasks = [
            {"id": "t1", "name": "Old Task", "heartbeat": False, "schedule": "0 9 * * *",
             "enabled": True, "chance": 100},
            {"id": "t2", "name": "Old HB", "heartbeat": True, "schedule": "*/15 * * * *",
             "enabled": True, "chance": 100},
        ]
        sched = self._make_scheduler(tmp_path, tasks=legacy_tasks)
        assert sched._tasks["t1"]["type"] == "task"
        assert sched._tasks["t2"]["type"] == "heartbeat"
        # Both should have trigger_config now
        assert "trigger_config" in sched._tasks["t1"]
        assert "trigger_config" in sched._tasks["t2"]

    def test_update_task_allows_type_and_trigger_config(self, tmp_path):
        """update_task accepts type and trigger_config in allowed fields."""
        sched = self._make_scheduler(tmp_path)
        task = sched.create_task({"name": "Test", "schedule": "0 9 * * *"})
        updated = sched.update_task(task["id"], {
            "type": "webhook",
            "trigger_config": {"path": "my-hook", "method": "GET"},
        })
        assert updated["type"] == "webhook"
        assert updated["trigger_config"]["path"] == "my-hook"

    def test_daemon_limit(self, tmp_path):
        """Cannot exceed MAX_DAEMONS."""
        sched = self._make_scheduler(tmp_path)
        for i in range(sched.MAX_DAEMONS):
            sched.create_task({
                "name": f"Daemon {i}", "type": "daemon",
                "schedule": "0 0 31 2 *",
                "trigger_config": {"source": f"src_{i}"},
            })
        with pytest.raises(ValueError, match="Maximum daemon"):
            sched.create_task({
                "name": "One Too Many", "type": "daemon",
                "schedule": "0 0 31 2 *",
            })


# ============================================================================
# Scheduler: find_tasks_by_event, find_webhook_task
# ============================================================================

class TestSchedulerEventFinders:
    """Test the event/webhook lookup methods."""

    def _make_scheduler(self, tmp_path):
        from core.continuity.scheduler import ContinuityScheduler
        base_dir = tmp_path / "user" / "continuity"
        base_dir.mkdir(parents=True)

        sched = ContinuityScheduler.__new__(ContinuityScheduler)
        sched.system = MagicMock()
        sched.executor = MagicMock()
        sched._running = False
        sched._thread = None
        sched._lock = threading.Lock()
        sched._base_dir = base_dir
        sched._tasks_path = base_dir / "tasks.json"
        sched._activity_path = base_dir / "activity.json"
        sched._tasks = {}
        sched._activity = []
        sched._task_running = {}
        sched._task_pending = {}
        sched._task_last_matched = {}
        sched._task_progress = {}
        return sched

    def test_find_tasks_by_event(self, tmp_path):
        sched = self._make_scheduler(tmp_path)
        sched.create_task({
            "name": "Discord Handler",
            "type": "daemon",
            "schedule": "0 0 31 2 *",
            "trigger_config": {"source": "discord_message"},
        })
        sched.create_task({
            "name": "Telegram Handler",
            "type": "daemon",
            "schedule": "0 0 31 2 *",
            "trigger_config": {"source": "telegram_message"},
        })
        results = sched.find_tasks_by_event("discord_message")
        assert len(results) == 1
        assert results[0]["name"] == "Discord Handler"

    def test_find_tasks_by_event_disabled(self, tmp_path):
        """Disabled daemon tasks are not returned."""
        sched = self._make_scheduler(tmp_path)
        task = sched.create_task({
            "name": "Disabled",
            "type": "daemon",
            "schedule": "0 0 31 2 *",
            "trigger_config": {"source": "test_src"},
            "enabled": False,
        })
        results = sched.find_tasks_by_event("test_src")
        assert len(results) == 0

    def test_find_webhook_task(self, tmp_path):
        sched = self._make_scheduler(tmp_path)
        sched.create_task({
            "name": "Deploy Hook",
            "type": "webhook",
            "schedule": "0 0 31 2 *",
            "trigger_config": {"path": "deploy", "method": "POST"},
        })
        result = sched.find_webhook_task("deploy", "POST")
        assert result is not None
        assert result["name"] == "Deploy Hook"

    def test_find_webhook_task_wrong_method(self, tmp_path):
        sched = self._make_scheduler(tmp_path)
        sched.create_task({
            "name": "POST Only",
            "type": "webhook",
            "schedule": "0 0 31 2 *",
            "trigger_config": {"path": "deploy", "method": "POST"},
        })
        result = sched.find_webhook_task("deploy", "GET")
        assert result is None

    def test_find_webhook_task_no_match(self, tmp_path):
        sched = self._make_scheduler(tmp_path)
        result = sched.find_webhook_task("nonexistent", "POST")
        assert result is None


# ============================================================================
# Scheduler: fire_event_task with filter matching
# ============================================================================

class TestFireEventTask:
    """Test fire_event_task including filter logic."""

    def _make_scheduler(self, tmp_path):
        from core.continuity.scheduler import ContinuityScheduler
        base_dir = tmp_path / "user" / "continuity"
        base_dir.mkdir(parents=True)

        mock_executor = MagicMock()
        mock_executor.run.return_value = {"success": True, "responses": [{"output": "ok"}], "errors": []}

        sched = ContinuityScheduler.__new__(ContinuityScheduler)
        sched.system = MagicMock()
        sched.executor = mock_executor
        sched._running = False
        sched._thread = None
        sched._lock = threading.Lock()
        sched._base_dir = base_dir
        sched._tasks_path = base_dir / "tasks.json"
        sched._activity_path = base_dir / "activity.json"
        sched._tasks = {}
        sched._activity = []
        sched._task_running = {}
        sched._task_pending = {}
        sched._task_last_matched = {}
        sched._task_progress = {}
        return sched

    def test_fire_event_task_success(self, tmp_path):
        sched = self._make_scheduler(tmp_path)
        task = sched.create_task({
            "name": "Test Daemon",
            "type": "daemon",
            "schedule": "0 0 31 2 *",
            "trigger_config": {"source": "test_src"},
        })
        result = sched.fire_event_task(task["id"], '{"msg": "hello"}')
        assert result["success"] is True

    def test_fire_event_task_filter_match(self, tmp_path):
        sched = self._make_scheduler(tmp_path)
        task = sched.create_task({
            "name": "Filtered",
            "type": "daemon",
            "schedule": "0 0 31 2 *",
            "trigger_config": {"source": "test", "filter": {"channel": "general"}},
        })
        # Matching filter
        result = sched.fire_event_task(task["id"], '{"channel": "general", "text": "hi"}')
        assert result["success"] is True

    def test_fire_event_task_filter_mismatch(self, tmp_path):
        sched = self._make_scheduler(tmp_path)
        task = sched.create_task({
            "name": "Filtered",
            "type": "daemon",
            "schedule": "0 0 31 2 *",
            "trigger_config": {"source": "test", "filter": {"channel": "general"}},
        })
        # Non-matching filter
        result = sched.fire_event_task(task["id"], '{"channel": "random", "text": "hi"}')
        assert result["success"] is False
        assert "filtered" in result["error"].lower()

    def test_fire_rejects_non_event_type(self, tmp_path):
        sched = self._make_scheduler(tmp_path)
        task = sched.create_task({"name": "Regular", "schedule": "0 9 * * *"})
        result = sched.fire_event_task(task["id"], "test data")
        assert result["success"] is False
        assert "not event-triggered" in result["error"]

    def test_fire_rejects_disabled(self, tmp_path):
        sched = self._make_scheduler(tmp_path)
        task = sched.create_task({
            "name": "Disabled",
            "type": "daemon",
            "schedule": "0 0 31 2 *",
            "trigger_config": {"source": "test"},
            "enabled": False,
        })
        result = sched.fire_event_task(task["id"], "data")
        assert result["success"] is False
        assert "disabled" in result["error"].lower()


# ============================================================================
# Scheduler: cron check skips daemon/webhook
# ============================================================================

class TestCronSkipsEventTasks:
    """Verify _check_and_run skips daemon/webhook types."""

    def test_check_and_run_skips_daemons(self, tmp_path):
        from core.continuity.scheduler import ContinuityScheduler
        base_dir = tmp_path / "user" / "continuity"
        base_dir.mkdir(parents=True)

        sched = ContinuityScheduler.__new__(ContinuityScheduler)
        sched.system = MagicMock()
        sched.executor = MagicMock()
        sched._running = False
        sched._thread = None
        sched._lock = threading.Lock()
        sched._base_dir = base_dir
        sched._tasks_path = base_dir / "tasks.json"
        sched._activity_path = base_dir / "activity.json"
        sched._tasks = {}
        sched._activity = []
        sched._task_running = {}
        sched._task_pending = {}
        sched._task_last_matched = {}
        sched._task_progress = {}

        # Create a daemon with a schedule that would match every minute
        sched.create_task({
            "name": "Should Not Fire",
            "type": "daemon",
            "schedule": "* * * * *",  # every minute
            "trigger_config": {"source": "test"},
        })

        sched._check_and_run()

        # Executor should NOT have been called
        sched.executor.run.assert_not_called()


# ============================================================================
# Executor: event_data prepends instructions
# ============================================================================

class TestExecutorEventData:
    """Test that executor prepends initial_message before event data."""

    def test_event_data_prepended(self):
        from core.continuity.executor import ContinuityExecutor

        mock_system = MagicMock()
        mock_system.llm_chat.isolated_chat.return_value = "Response here"
        mock_system.tts = None

        executor = ContinuityExecutor(mock_system)

        task = {
            "id": "test-id",
            "name": "Test Daemon",
            "type": "daemon",
            "initial_message": "Respond to Discord messages casually.",
            "prompt": "default",
            "toolset": "none",
            "provider": "auto",
            "model": "",
            "persona": "",
            "chat_target": "",
            "tts_enabled": False,
            "browser_tts": False,
            "inject_datetime": False,
            "memory_scope": "none",
            "knowledge_scope": "none",
            "people_scope": "none",
            "goal_scope": "none",
        }

        result = executor.run(task, event_data='{"user": "bob", "message": "hello"}')

        # Check isolated_chat was called with combined message
        call_args = mock_system.llm_chat.isolated_chat.call_args
        msg = call_args[0][0]
        assert "Respond to Discord messages casually." in msg
        assert "--- Event Data ---" in msg
        assert '"user": "bob"' in msg

    def test_no_event_data_uses_initial_message(self):
        from core.continuity.executor import ContinuityExecutor

        mock_system = MagicMock()
        mock_system.llm_chat.isolated_chat.return_value = "Hi"
        mock_system.tts = None

        executor = ContinuityExecutor(mock_system)

        task = {
            "id": "test-id",
            "name": "Normal Task",
            "type": "task",
            "initial_message": "Say hello.",
            "prompt": "default",
            "toolset": "none",
            "provider": "auto",
            "model": "",
            "persona": "",
            "chat_target": "",
            "tts_enabled": False,
            "browser_tts": False,
            "inject_datetime": False,
            "memory_scope": "none",
            "knowledge_scope": "none",
            "people_scope": "none",
            "goal_scope": "none",
        }

        result = executor.run(task)
        call_args = mock_system.llm_chat.isolated_chat.call_args
        msg = call_args[0][0]
        assert msg == "Say hello."
        assert "Event Data" not in msg

    def test_event_data_empty_instructions(self):
        """When initial_message is empty, just use event_data."""
        from core.continuity.executor import ContinuityExecutor

        mock_system = MagicMock()
        mock_system.llm_chat.isolated_chat.return_value = "ok"
        mock_system.tts = None

        executor = ContinuityExecutor(mock_system)

        task = {
            "id": "test-id",
            "name": "No Instructions",
            "type": "webhook",
            "initial_message": "",
            "prompt": "default",
            "toolset": "none",
            "provider": "auto",
            "model": "",
            "persona": "",
            "chat_target": "",
            "tts_enabled": False,
            "browser_tts": False,
            "inject_datetime": False,
            "memory_scope": "none",
            "knowledge_scope": "none",
            "people_scope": "none",
            "goal_scope": "none",
        }

        result = executor.run(task, event_data='{"action": "deploy"}')
        call_args = mock_system.llm_chat.isolated_chat.call_args
        msg = call_args[0][0]
        assert msg == '{"action": "deploy"}'
        assert "--- Event Data ---" not in msg


# ============================================================================
# Plugin Loader: daemon capability + event sources
# ============================================================================

class TestPluginDaemonCapability:
    """Test the daemon event source registration in plugin_loader."""

    def test_get_event_sources_empty(self):
        from core.plugin_loader import PluginLoader
        loader = PluginLoader()
        assert loader.get_event_sources() == []

    def test_event_sources_registered(self):
        from core.plugin_loader import PluginLoader
        loader = PluginLoader()
        # Simulate what _load_plugin does for daemon capability
        with loader._lock:
            loader._event_sources["discord"] = [
                {
                    "name": "discord_message",
                    "label": "Discord Message",
                    "plugin": "discord",
                    "filter_fields": [
                        {"key": "channel", "label": "Channel"},
                        {"key": "author", "label": "Author"},
                    ],
                    "description": "Incoming Discord messages",
                }
            ]
        sources = loader.get_event_sources()
        assert len(sources) == 1
        assert sources[0]["name"] == "discord_message"
        assert sources[0]["plugin"] == "discord"
        assert len(sources[0]["filter_fields"]) == 2

    def test_event_sources_cleared_on_unload(self):
        from core.plugin_loader import PluginLoader
        loader = PluginLoader()
        with loader._lock:
            loader._event_sources["test_plugin"] = [{"name": "test_event"}]
            loader._plugins["test_plugin"] = {"loaded": True, "enabled": True}
        # Mock dependencies for unload
        with patch.object(loader, '_unregister_routes'):
            loader.unload_plugin("test_plugin")
        assert "test_plugin" not in loader._event_sources

    def test_emit_daemon_event_triggers_tasks(self):
        from core.plugin_loader import PluginLoader
        loader = PluginLoader()

        mock_scheduler = MagicMock()
        mock_scheduler.find_tasks_by_event.return_value = [
            {"id": "task-1", "name": "Handler 1"},
            {"id": "task-2", "name": "Handler 2"},
        ]
        mock_scheduler.fire_event_task.return_value = {"success": True}
        loader._scheduler = mock_scheduler

        loader.emit_daemon_event("discord_message", '{"text": "hello"}')

        assert mock_scheduler.fire_event_task.call_count == 2
        mock_scheduler.fire_event_task.assert_any_call("task-1", '{"text": "hello"}', reply_callback=None)
        mock_scheduler.fire_event_task.assert_any_call("task-2", '{"text": "hello"}', reply_callback=None)

    def test_emit_daemon_event_no_scheduler(self):
        """Emit with no scheduler should not crash."""
        from core.plugin_loader import PluginLoader
        loader = PluginLoader()
        loader._scheduler = None
        # Should just log a warning, not raise
        loader.emit_daemon_event("test", "data")

    def test_emit_daemon_event_no_tasks(self):
        """Emit with no matching tasks should not crash."""
        from core.plugin_loader import PluginLoader
        loader = PluginLoader()
        mock_scheduler = MagicMock()
        mock_scheduler.find_tasks_by_event.return_value = []
        loader._scheduler = mock_scheduler
        loader.emit_daemon_event("nonexistent_source", "data")
        mock_scheduler.fire_event_task.assert_not_called()


# ============================================================================
# Integration: full flow from emit to executor
# ============================================================================

class TestDaemonIntegration:
    """Integration test: plugin emits event -> scheduler finds task -> executor runs."""

    def test_full_daemon_flow(self, tmp_path):
        from core.continuity.scheduler import ContinuityScheduler
        from core.plugin_loader import PluginLoader

        base_dir = tmp_path / "user" / "continuity"
        base_dir.mkdir(parents=True)

        mock_executor = MagicMock()
        mock_executor.run.return_value = {"success": True, "responses": [{"output": "replied"}], "errors": []}

        sched = ContinuityScheduler.__new__(ContinuityScheduler)
        sched.system = MagicMock()
        sched.executor = mock_executor
        sched._running = False
        sched._thread = None
        sched._lock = threading.Lock()
        sched._base_dir = base_dir
        sched._tasks_path = base_dir / "tasks.json"
        sched._activity_path = base_dir / "activity.json"
        sched._tasks = {}
        sched._activity = []
        sched._task_running = {}
        sched._task_pending = {}
        sched._task_last_matched = {}
        sched._task_progress = {}

        # Create a daemon task
        task = sched.create_task({
            "name": "Discord Responder",
            "type": "daemon",
            "schedule": "0 0 31 2 *",
            "trigger_config": {"source": "discord_message"},
            "initial_message": "Reply casually to Discord messages.",
        })

        # Set up plugin loader with scheduler
        loader = PluginLoader()
        loader._scheduler = sched

        # Emit event (this is what the Discord plugin would call)
        loader.emit_daemon_event("discord_message", '{"user": "Krem", "message": "hey Sapphire"}')

        # Give worker thread a moment to execute
        import time
        time.sleep(0.5)

        # Executor should have been called with event_data
        assert mock_executor.run.called
        call_kwargs = mock_executor.run.call_args
        assert call_kwargs.kwargs.get("event_data") == '{"user": "Krem", "message": "hey Sapphire"}'


class TestReplyHandler:
    """Tests for daemon reply handler plumbing."""

    def test_register_and_lookup_reply_handler(self):
        from core.plugin_loader import PluginLoader
        loader = PluginLoader()

        handler = MagicMock()
        loader._event_sources["telegram"] = [{"name": "telegram_message", "label": "Telegram Message"}]
        loader.register_reply_handler("telegram", handler)

        found = loader._get_reply_handler("telegram_message")
        assert found is handler

    def test_reply_handler_not_found(self):
        from core.plugin_loader import PluginLoader
        loader = PluginLoader()
        assert loader._get_reply_handler("nonexistent") is None

    def test_reply_handler_cleaned_on_unload(self):
        from core.plugin_loader import PluginLoader
        loader = PluginLoader()

        handler = MagicMock()
        loader._reply_handlers["telegram"] = handler
        loader._event_sources["telegram"] = []
        loader._plugins["telegram"] = {"loaded": True, "enabled": True, "manifest": {"capabilities": {}}}
        loader.unload_plugin("telegram")

        assert "telegram" not in loader._reply_handlers

    def test_emit_threads_reply_handler(self):
        from core.plugin_loader import PluginLoader
        loader = PluginLoader()

        handler = MagicMock()
        loader._event_sources["telegram"] = [{"name": "telegram_message", "label": "TM"}]
        loader.register_reply_handler("telegram", handler)

        mock_scheduler = MagicMock()
        mock_scheduler.find_tasks_by_event.return_value = [{"id": "t1", "name": "Test"}]
        loader._scheduler = mock_scheduler

        loader.emit_daemon_event("telegram_message", '{"text": "hi"}')
        mock_scheduler.fire_event_task.assert_called_once_with("t1", '{"text": "hi"}', reply_callback=handler)

    def test_reply_callback_called_on_response(self, tmp_path):
        """Reply callback fires when executor produces a response."""
        from core.continuity.scheduler import ContinuityScheduler

        base_dir = tmp_path / "user" / "continuity"
        base_dir.mkdir(parents=True)

        reply_results = []
        def reply_handler(task, event_dict, response_text):
            reply_results.append({"task_id": task["id"], "event": event_dict, "response": response_text})

        mock_executor = MagicMock()
        mock_executor.run.return_value = {"success": True, "responses": [{"output": "Hello!"}], "errors": []}
        # Simulate executor calling response_callback
        def fake_run(task, event_data=None, progress_callback=None, response_callback=None):
            if response_callback:
                response_callback("Hello from Sapphire!")
            return {"success": True, "responses": [{"output": "Hello!"}], "errors": []}
        mock_executor.run.side_effect = fake_run

        sched = ContinuityScheduler.__new__(ContinuityScheduler)
        sched.system = MagicMock()
        sched.executor = mock_executor
        sched._running = False
        sched._thread = None
        sched._lock = threading.Lock()
        sched._base_dir = base_dir
        sched._tasks_path = base_dir / "tasks.json"
        sched._activity_path = base_dir / "activity.json"
        sched._tasks = {}
        sched._activity = []
        sched._task_running = {}
        sched._task_pending = {}
        sched._task_last_matched = {}
        sched._task_progress = {}

        task = sched.create_task({
            "name": "Telegram Reply Test",
            "type": "daemon",
            "schedule": "0 0 31 2 *",
            "trigger_config": {"source": "telegram_message"},
            "initial_message": "Reply to messages.",
        })

        sched.fire_event_task(task["id"], '{"chat_id": 123, "text": "hey"}', reply_callback=reply_handler)

        import time
        time.sleep(0.5)

        assert len(reply_results) == 1
        assert reply_results[0]["task_id"] == task["id"]
        assert reply_results[0]["event"] == {"chat_id": 123, "text": "hey"}
        assert reply_results[0]["response"] == "Hello from Sapphire!"
