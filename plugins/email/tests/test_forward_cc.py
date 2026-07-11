"""forward_email + CC — both must inherit the send whitelist gate, and
forward must never leak the original sender's address into the body."""
import email as emaillib
from unittest.mock import patch, MagicMock

_PEOPLE = [
    {'id': 1, 'name': 'Alice', 'email': 'alice@x.com', 'email_whitelisted': True},
    {'id': 2, 'name': 'Bob', 'email': 'bob@x.com', 'email_whitelisted': True},
    {'id': 3, 'name': 'Carol', 'email': 'carol@x.com', 'email_whitelisted': False},
]

_CREDS = ({'address': 'me@x.com', 'smtp_server': 'smtp.x.com'}, None)


def test_cc_sets_cc_header_for_whitelisted_contacts():
    from plugins.email.tools import email_tool
    smtp = MagicMock()
    with patch.object(email_tool, '_get_email_creds_detailed', return_value=_CREDS), \
         patch.object(email_tool, '_get_cache', return_value={'raw': [], 'msg_ids': [], 'messages': []}), \
         patch.object(email_tool, '_get_current_people_scope', return_value='default'), \
         patch('core.contacts.get_people', return_value=_PEOPLE), \
         patch.object(email_tool, '_smtp_connect', return_value=smtp):
        result, ok = email_tool._send_email(recipient_id=1, subject='Hi', body='yo', cc=[2])
    assert ok, result
    sent = smtp.send_message.call_args.args[0]
    assert sent['To'] == 'alice@x.com'
    assert sent['Cc'] == 'bob@x.com'
    assert "(cc 1)" in result


def test_cc_unwhitelisted_fails_loud_and_does_not_send():
    from plugins.email.tools import email_tool
    smtp = MagicMock()
    with patch.object(email_tool, '_get_email_creds_detailed', return_value=_CREDS), \
         patch.object(email_tool, '_get_cache', return_value={'raw': [], 'msg_ids': [], 'messages': []}), \
         patch.object(email_tool, '_get_current_people_scope', return_value='default'), \
         patch('core.contacts.get_people', return_value=_PEOPLE), \
         patch.object(email_tool, '_smtp_connect', return_value=smtp):
        result, ok = email_tool._send_email(recipient_id=1, subject='Hi', body='yo', cc=[3])
    assert not ok
    assert "not whitelisted" in result.lower()
    smtp.send_message.assert_not_called()


def _raw():
    return emaillib.message_from_string(
        "From: Jane Doe <jane@secret.com>\r\n"
        "Subject: Quarterly\r\n"
        "Date: Mon, 1 Jun 2026 10:00:00 +0000\r\n\r\n"
        "The numbers are good.\r\n"
    )


def test_forward_builds_fwd_subject_and_body_without_leaking_address():
    from plugins.email.tools import email_tool
    cache = {'raw': [_raw()], 'msg_ids': ['uid0'], 'messages': [{'subject': 'Quarterly'}],
             'folder': 'inbox', 'timestamp': 0}
    smtp = MagicMock()
    with patch.object(email_tool, '_get_cache', return_value=cache), \
         patch.object(email_tool, '_get_email_creds_detailed', return_value=_CREDS), \
         patch.object(email_tool, '_get_current_people_scope', return_value='default'), \
         patch('core.contacts.get_people', return_value=_PEOPLE), \
         patch.object(email_tool, '_smtp_connect', return_value=smtp):
        result, ok = email_tool._forward_email(1, recipient_id=1, note='fyi')
    assert ok, result
    sent = smtp.send_message.call_args.args[0]
    assert sent['Subject'] == 'Fwd: Quarterly'
    assert sent['To'] == 'alice@x.com'
    body = sent.get_payload()
    assert 'Forwarded message' in body
    assert 'Jane Doe' in body          # sender NAME shown
    assert 'secret.com' not in body    # sender ADDRESS never leaked
    assert 'fyi' in body
    assert 'The numbers are good.' in body


def test_forward_address_mode_rejected_without_allow_all():
    from plugins.email.tools import email_tool
    cache = {'raw': [_raw()], 'msg_ids': ['uid0'], 'messages': [{'subject': 'Q'}], 'folder': 'inbox'}
    smtp = MagicMock()
    with patch.object(email_tool, '_get_cache', return_value=cache), \
         patch.object(email_tool, '_allow_all_enabled', return_value=False), \
         patch.object(email_tool, '_smtp_connect', return_value=smtp):
        result, ok = email_tool._forward_email(1, address='stranger@x.com')
    assert not ok
    assert "not allowed" in result.lower()
    smtp.send_message.assert_not_called()
