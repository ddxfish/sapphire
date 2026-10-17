"""Regression guard for 2026-04-22 day-ruiner H10.

Pre-fix: archive_emails called imap.uid('copy', ...) + imap.uid('store', ...)
without checking return status. If the message was already archived
externally (user archived on phone), IMAP returns NO but no exception.
archive_emails still reported 'Archived N emails' — a LIE. User acting on
the false success (e.g. deleting from phone trusting Sapphire's archive)
could lose the email.

Fix: check copy_status / store_status, report skipped entries explicitly,
return False if nothing actually archived.
"""
from unittest.mock import patch, MagicMock
import pytest


def _make_cache_with_messages(count=3):
    """Build a cache dict with N messages loaded."""
    return {
        'folder': 'inbox',
        'msg_ids': [f'uid{i}' for i in range(count)],
        'messages': [
            {'subject': f'Subject {i}', 'unread': True}
            for i in range(count)
        ],
        'raw': [None] * count,
        'timestamp': 0,
    }


def test_archive_reports_skipped_when_copy_returns_no():
    """IMAP COPY returns NO — fix reports skipped, not success."""
    from plugins.email.tools import email_tool

    cache = _make_cache_with_messages(3)

    mock_imap = MagicMock()
    # COPY returns NO for all 3 (already archived externally)
    mock_imap.uid.return_value = ('NO', [b'not found'])

    with patch.object(email_tool, '_get_cache', return_value=cache):
        with patch.object(email_tool, '_get_email_creds', return_value={'address': 'x@y.com'}):
            with patch.object(email_tool, '_imap_connect', return_value=mock_imap):
                with patch.object(email_tool, '_reset_cache'):
                    result, ok = email_tool._archive_emails([1, 2, 3])

    assert not ok, "No emails actually archived — should return False"
    assert "Skipped" in result, f"Output should name the skipped class: {result!r}"
    assert "Archived 3" not in result, f"Must NOT falsely claim 3 archived: {result!r}"


def test_archive_mixed_partial_success():
    """Some copies succeed, some fail — both reported, overall ok=True."""
    from plugins.email.tools import email_tool

    cache = _make_cache_with_messages(3)

    mock_imap = MagicMock()

    # First UID: copy OK, store OK. Second: copy NO. Third: copy OK, store OK.
    def uid_side_effect(op, uid, *args):
        if op == 'copy':
            return ('OK' if uid != 'uid1' else 'NO', [b''])
        if op == 'store':
            return ('OK', [b''])
        return ('OK', [b''])

    mock_imap.uid.side_effect = uid_side_effect

    with patch.object(email_tool, '_get_cache', return_value=cache):
        with patch.object(email_tool, '_get_email_creds', return_value={'address': 'x@y.com'}):
            with patch.object(email_tool, '_imap_connect', return_value=mock_imap):
                with patch.object(email_tool, '_reset_cache'):
                    result, ok = email_tool._archive_emails([1, 2, 3])

    assert ok, "Partial success — at least one archived"
    assert "Archived 2" in result, f"Should report 2 archived: {result!r}"
    assert "Skipped 1" in result, f"Should report 1 skipped: {result!r}"


def test_archive_full_success_reports_cleanly():
    """All copies succeed — clean success message, no skipped section."""
    from plugins.email.tools import email_tool

    cache = _make_cache_with_messages(2)

    mock_imap = MagicMock()
    mock_imap.uid.return_value = ('OK', [b''])

    with patch.object(email_tool, '_get_cache', return_value=cache):
        with patch.object(email_tool, '_get_email_creds', return_value={'address': 'x@y.com'}):
            with patch.object(email_tool, '_imap_connect', return_value=mock_imap):
                with patch.object(email_tool, '_reset_cache'):
                    result, ok = email_tool._archive_emails([1, 2])

    assert ok
    assert "Archived 2" in result
    assert "Skipped" not in result, "No skipped section when all succeeded"


def test_archive_exception_on_single_uid_reported_as_skipped():
    """An exception on one UID is caught, reported as skipped, others proceed."""
    from plugins.email.tools import email_tool

    cache = _make_cache_with_messages(2)

    mock_imap = MagicMock()
    call_count = [0]

    def uid_side_effect(op, uid, *args):
        call_count[0] += 1
        if uid == 'uid0' and op == 'copy':
            raise RuntimeError("network blip")
        return ('OK', [b''])

    mock_imap.uid.side_effect = uid_side_effect

    with patch.object(email_tool, '_get_cache', return_value=cache):
        with patch.object(email_tool, '_get_email_creds', return_value={'address': 'x@y.com'}):
            with patch.object(email_tool, '_imap_connect', return_value=mock_imap):
                with patch.object(email_tool, '_reset_cache'):
                    result, ok = email_tool._archive_emails([1, 2])

    assert ok, "One succeeded — partial success still ok"
    assert "Archived 1" in result
    assert "Skipped 1" in result
    assert "exception" in result.lower() or "network blip" in result
