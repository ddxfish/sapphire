"""Regression guard for 2026-04-22 day-ruiner H7.

Pre-fix: Telegram client daemon silently skipped accounts whose sessions
had expired. logger.warning fired but no user-facing event. UI still
reported 'Telegram enabled' for the account while the daemon was
actually deaf. User ran for weeks wondering why replies never came.

Fix: mirror the wakeword pattern — publish CONTINUITY_TASK_ERROR with
actionable guidance (re-auth in Settings). UI catches the event and
shows an alert.

Testing the whole daemon is impractical (needs Telethon mock). This
test verifies the event-publish call is reachable from the expected
code path via direct inspection.
"""
from pathlib import Path


def test_daemon_publishes_session_expiry_event_source():
    """Source-level check: the session-expiry branch emits
    CONTINUITY_TASK_ERROR with Telegram context. A full runtime test
    would require mocking Telethon's async client — this guards the
    invariant at source level instead."""
    daemon_src = Path(__file__).parent.parent / "daemon.py"
    source = daemon_src.read_text()

    # Find the session-expired block — should be in _connect_accounts
    expired_check_idx = source.find("session expired — skipping")
    assert expired_check_idx != -1, \
        "Session-expired warning text missing — has the daemon been restructured?"

    # From that point forward, within a reasonable window (~1500 chars),
    # we must see the CONTINUITY_TASK_ERROR publish with 'Telegram' task.
    nearby = source[expired_check_idx:expired_check_idx + 2000]
    assert "CONTINUITY_TASK_ERROR" in nearby, \
        "Session-expired path must publish CONTINUITY_TASK_ERROR event"
    assert '"task": "Telegram"' in nearby or "'task': 'Telegram'" in nearby, \
        "Event must name Telegram as the task for UI routing"
    assert "re-authenticate" in nearby.lower() or "re-auth" in nearby.lower(), \
        "Event error text should guide user to re-authenticate"
