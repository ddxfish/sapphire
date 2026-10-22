"""A plugin schedule handler that returns None did nothing: the run records
no response, so the notification center (core/notify.py _on_task_done) logs
no "complete" row for it. 23 'Task "Remembrance offsite backup" complete' a
day, one per hourly tick, read as 23 backups (2026-10-22)."""
from types import SimpleNamespace

from core.continuity.executor import ContinuityExecutor


def _run(tmp_path, monkeypatch, body):
    (tmp_path / "h.py").write_text(body, encoding="utf-8")
    import core.plugin_loader as pl
    monkeypatch.setattr(pl.plugin_loader, "get_plugin_state", lambda name: None)
    task = {"id": "t1", "name": "tick", "source": "plugin:x", "handler": "h.py", "plugin_dir": str(tmp_path)}
    return ContinuityExecutor._run_plugin_task(SimpleNamespace(system=None), task)


def test_silent_handler_records_no_response(tmp_path, monkeypatch):
    r = _run(tmp_path, monkeypatch, "def run(event):\n    return None\n")
    assert r["success"] is True and r["responses"] == [] and r["errors"] == []


def test_speaking_handler_records_its_line(tmp_path, monkeypatch):
    r = _run(tmp_path, monkeypatch, "def run(event):\n    return 'backed up'\n")
    assert r["success"] is True and r["responses"] == [{"output": "backed up"}]
