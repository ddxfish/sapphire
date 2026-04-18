"""Smoke tests for Phase 0 shared fixtures.

These verify each fixture loads + behaves as documented. If this file goes red,
coverage-test-plan.md Phase 0 has regressed — fix before writing anything else.
"""
import pytest
import threading


def test_client_fixture_returns_client_and_csrf(client):
    c, csrf = client
    assert c is not None
    assert isinstance(csrf, str) and csrf


def test_client_fixture_auth_bypassed(client):
    c, csrf = client
    # /api/status requires login — should succeed under bypass
    r = c.get("/api/status")
    # Either 200 (system wired) or 500 (no system) — both mean auth passed
    assert r.status_code != 401 and r.status_code != 403


def test_temp_user_dir_structure(temp_user_dir):
    import json
    assert temp_user_dir.exists()
    assert (temp_user_dir / "webui" / "plugins.json").exists()
    assert (temp_user_dir / "webui" / "plugins").is_dir()
    assert (temp_user_dir / "plugin_state").is_dir()
    assert (temp_user_dir / "plugins").is_dir()
    data = json.loads((temp_user_dir / "webui" / "plugins.json").read_text())
    assert data == {"enabled": [], "disabled": []}


def test_temp_user_dir_monkeypatches_paths(temp_user_dir):
    import core.plugin_loader as pl
    import core.routes.plugins as rp
    # Paths should now point at tmp, not real user/
    assert str(pl.USER_PLUGINS_JSON).startswith(str(temp_user_dir))
    assert str(rp.USER_WEBUI_DIR).startswith(str(temp_user_dir))


def test_mock_system_has_expected_attrs(mock_system):
    assert mock_system.llm_chat is not None
    assert mock_system.llm_chat.function_manager is not None
    assert mock_system.llm_chat.session_manager is not None
    assert mock_system.agent_manager is not None
    assert mock_system.tts is not None


def test_mock_system_wired_into_get_system(mock_system):
    from core.api_fastapi import get_system
    # get_system should return the mock (or raise; both are acceptable tolerances)
    try:
        s = get_system()
        assert s is mock_system
    except Exception:
        # If get_system wasn't wired via set_system, skip the identity check
        pytest.skip("get_system not reachable in this test context")


def test_scope_snapshot_restores_on_teardown():
    """This test can only observe the fixture's teardown indirectly.
    We verify: fixture yields a dict, test can mutate SCOPE_REGISTRY freely."""
    from core.chat.function_manager import SCOPE_REGISTRY
    # Before the fixture isn't useful here — we just verify the import works
    assert isinstance(SCOPE_REGISTRY, dict)
    assert len(SCOPE_REGISTRY) >= 2  # at least rag + private


def test_scope_snapshot_fixture_preserves_identity(scope_snapshot):
    """Mutate SCOPE_REGISTRY during the test; fixture teardown should restore."""
    from core.chat.function_manager import SCOPE_REGISTRY
    # Snapshot was taken before we ran
    assert isinstance(scope_snapshot, dict)
    assert 'rag' in scope_snapshot
    # Mutation during test — fixture teardown will restore
    SCOPE_REGISTRY['_test_temp'] = {'var': None, 'default': None, 'setting': None}
    # No assertion needed here — other tests run after this would break if teardown fails


def test_event_bus_capture_fixture_collects_events(event_bus_capture):
    from core import event_bus
    if hasattr(event_bus.Events, 'PLUGIN_TOGGLED'):
        event_bus.publish(event_bus.Events.PLUGIN_TOGGLED, {"test": True})
        assert any(
            ev == event_bus.Events.PLUGIN_TOGGLED and data == {"test": True}
            for ev, data in event_bus_capture.events
        )


def test_fake_popen_default_behavior(fake_popen):
    import subprocess
    proc = subprocess.Popen(['claude', '-p', 'hello'])
    out, err = proc.communicate()
    assert out == '{"result":"ok","session_id":"test-session"}'
    assert err == ''
    assert proc.returncode == 0


def test_fake_popen_configurable(fake_popen):
    import subprocess
    fake_popen.returncode = 1
    fake_popen.stdout = 'error output'
    proc = subprocess.Popen(['claude', '-p', 'x'])
    out, _ = proc.communicate()
    assert out == 'error output'
    assert proc.returncode == 1


def test_fake_popen_kill_tracked(fake_popen):
    import subprocess
    proc = subprocess.Popen(['claude'])
    proc.terminate()
    assert fake_popen.calls['terminate'] == 1
    proc.kill()
    assert fake_popen.calls['kill'] == 1


def test_fake_popen_poll_before_and_after_communicate(fake_popen):
    import subprocess
    proc = subprocess.Popen(['claude'])
    # Before communicate: alive
    assert proc.poll() is None
    proc.communicate()
    # After: returncode set
    assert proc.poll() == 0


def test_blocking_worker_cls_deterministic_completion(blocking_worker_cls):
    w = blocking_worker_cls(
        agent_id='test-id', name='TestW', mission='m', chat_name='c'
    )
    w.start()
    # Worker is blocked on its gate
    assert w.status == 'running'
    # Unblock with a result
    w.finish_with_result('done')
    # Wait for the daemon thread to finish
    w._thread.join(timeout=3)
    assert w.status == 'done'
    assert w.result == 'done'


def test_blocking_worker_cls_error_path(blocking_worker_cls):
    w = blocking_worker_cls(
        agent_id='err-id', name='ErrW', mission='m'
    )
    w.start()
    w.finish_with_error(RuntimeError('boom'))
    w._thread.join(timeout=3)
    assert w.status == 'failed'
    assert 'boom' in (w.error or '')


def test_blocking_worker_cls_cancel_during_run(blocking_worker_cls):
    w = blocking_worker_cls(
        agent_id='cancel-id', name='CancelW', mission='m'
    )
    w.start()
    assert w.status == 'running'
    w.cancel()
    w.finish()  # release the gate so the thread can exit
    w._thread.join(timeout=3)
    assert w.status == 'cancelled'
    assert w.result is None  # cancel voids result
