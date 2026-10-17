"""Regression guard for the 2026-04-22 day-ruiner fix in email_tool.py.

Pre-fix precedence was `address > reply_to_index > recipient_id`. If the AI
passed BOTH `recipient_id` and `reply_to_index` (confused or trying to
combine intents), reply_to_index silently won. Email went to whoever sent
the referenced inbox entry, NOT to the named contact. With body content
possibly quoted back from the original message, this is a wrong-recipient
+ potential data leak in one shot.

Fix: require exactly one target mode, reject ambiguous calls loudly.
"""
import pytest


def test_mutex_reply_to_index_and_recipient_id_rejected():
    """The specific day-ruiner scenario — both targeting args passed."""
    from plugins.email.tools.email_tool import _send_email

    result, ok = _send_email(
        recipient_id=5,
        reply_to_index=3,
        subject="Test",
        body="Hello",
    )
    assert not ok, "Should reject multi-mode call"
    assert "EXACTLY ONE" in result or "exactly one" in result.lower(), \
        f"Error message should name the mutex: {result!r}"


def test_mutex_address_and_recipient_id_rejected():
    """Address + recipient_id combo also rejected."""
    from plugins.email.tools.email_tool import _send_email

    result, ok = _send_email(
        recipient_id=5,
        address="x@y.com",
        subject="Test",
        body="Hello",
    )
    assert not ok, "Should reject multi-mode call"
    assert "EXACTLY ONE" in result or "exactly one" in result.lower()


def test_mutex_address_and_reply_to_index_rejected():
    """Address + reply_to_index also rejected."""
    from plugins.email.tools.email_tool import _send_email

    result, ok = _send_email(
        reply_to_index=1,
        address="x@y.com",
        subject="Test",
        body="Hello",
    )
    assert not ok, "Should reject multi-mode call"


def test_mutex_all_three_rejected():
    """All three at once — definitely rejected."""
    from plugins.email.tools.email_tool import _send_email

    result, ok = _send_email(
        recipient_id=1,
        reply_to_index=1,
        address="x@y.com",
        subject="Test",
        body="Hello",
    )
    assert not ok


def test_mutex_none_rejected():
    """Zero target args rejected with helpful message."""
    from plugins.email.tools.email_tool import _send_email

    result, ok = _send_email(subject="Test", body="Hello")
    assert not ok, "Body-only call must be rejected"
    assert "No recipient" in result or "no recipient" in result.lower(), \
        f"Error should say what's missing: {result!r}"


def test_single_mode_passes_mutex():
    """Single targeting arg should get PAST the mutex check (may fail later
    for unrelated reasons like missing creds — that's OK, we're only
    testing the mutex gate)."""
    from plugins.email.tools.email_tool import _send_email

    # Each of these should get past the mutex. Fail after for other reasons
    # (scope/creds), which is fine — we only assert we're past the mutex.
    for args in [
        {"recipient_id": 1, "subject": "T", "body": "b"},
        {"reply_to_index": 1, "subject": "T", "body": "b"},
        {"address": "x@y.com", "subject": "T", "body": "b"},
    ]:
        result, ok = _send_email(**args)
        # If it fails, it must NOT be the mutex rejection message
        if not ok:
            assert "EXACTLY ONE" not in result and "exactly one" not in result.lower(), \
                f"Single-mode call wrongly hit mutex: {args!r} -> {result!r}"
