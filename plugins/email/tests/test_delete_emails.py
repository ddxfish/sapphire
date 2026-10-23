"""delete_emails — move to the real Trash, refuse if none found.

Mirrors the archive partial-success guard (shared _move_to_folder), plus the
delete-specific safety property: if no Trash folder is discoverable, delete must
REFUSE rather than fall through to a permanent \\Deleted+expunge with no copy.
"""
from unittest.mock import patch, MagicMock


def _cache(count=3):
    return {
        'folder': 'inbox',
        'msg_ids': [f'uid{i}' for i in range(count)],
        'messages': [{'subject': f'Subject {i}', 'unread': True} for i in range(count)],
        'raw': [None] * count,
        'timestamp': 0,
    }


def test_delete_moves_to_trash_and_reports():
    from plugins.email.tools import email_tool
    cache = _cache(3)
    imap = MagicMock()
    imap.uid.return_value = ('OK', [b''])
    with patch.object(email_tool, '_get_cache', return_value=cache), \
         patch.object(email_tool, '_get_email_creds', return_value={'address': 'x@y.com'}), \
         patch.object(email_tool, '_imap_connect', return_value=imap), \
         patch.object(email_tool, '_discover_folder', return_value='Trash'), \
         patch.object(email_tool, '_reset_cache'):
        result, ok = email_tool._delete_emails([1, 2, 3])
    assert ok
    assert "Deleted 3" in result
    # Copied to Trash, not Archive
    copies = [c for c in imap.uid.call_args_list if c.args[0] == 'copy']
    assert copies and all(c.args[2] == 'Trash' for c in copies)
    imap.expunge.assert_called_once()


def test_delete_refuses_when_no_trash_folder():
    """No discoverable Trash -> refuse. Must NOT flag-delete or expunge."""
    from plugins.email.tools import email_tool
    cache = _cache(2)
    imap = MagicMock()
    imap.uid.return_value = ('OK', [b''])
    with patch.object(email_tool, '_get_cache', return_value=cache), \
         patch.object(email_tool, '_get_email_creds', return_value={'address': 'x@y.com'}), \
         patch.object(email_tool, '_imap_connect', return_value=imap), \
         patch.object(email_tool, '_discover_folder', return_value=None), \
         patch.object(email_tool, '_reset_cache'):
        result, ok = email_tool._delete_emails([1, 2])
    assert not ok
    assert "Trash" in result
    assert not any(c.args[0] == 'store' for c in imap.uid.call_args_list), \
        "must not flag \\Deleted when refusing"
    imap.expunge.assert_not_called()


def test_delete_reports_skipped_on_copy_no():
    """Server COPY returns NO -> skipped, not a false success (H10 lineage)."""
    from plugins.email.tools import email_tool
    cache = _cache(2)
    imap = MagicMock()
    imap.uid.return_value = ('NO', [b'gone'])
    with patch.object(email_tool, '_get_cache', return_value=cache), \
         patch.object(email_tool, '_get_email_creds', return_value={'address': 'x@y.com'}), \
         patch.object(email_tool, '_imap_connect', return_value=imap), \
         patch.object(email_tool, '_discover_folder', return_value='Trash'), \
         patch.object(email_tool, '_reset_cache'):
        result, ok = email_tool._delete_emails([1, 2])
    assert not ok
    assert "Deleted 2" not in result
    assert "Skipped" in result
