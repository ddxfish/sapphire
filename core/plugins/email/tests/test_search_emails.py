"""search_emails — criteria building + the date-bracket window."""
from unittest.mock import patch, MagicMock


def test_search_requires_at_least_one_param():
    from plugins.email.tools import email_tool
    result, ok = email_tool._search_emails()
    assert not ok
    assert "at least one" in result.lower()


def test_imap_search_str_quotes_and_escapes():
    from plugins.email.tools.email_tool import _imap_search_str
    assert _imap_search_str('fish') == '"fish"'
    assert _imap_search_str('a"b') == '"a\\"b"'
    # non-ASCII returns bytes (paired with CHARSET UTF-8)
    out = _imap_search_str('café')
    assert isinstance(out, bytes)


def test_uid_search_no_charset_uses_none_placeholder():
    from plugins.email.tools import email_tool
    imap = MagicMock()
    imap.uid.return_value = ('OK', [b'10 11 12'])
    out = email_tool._uid_search(imap, None, ['FROM', '"fish"'])
    assert out == [b'10', b'11', b'12']
    # None is passed as the charset placeholder (imaplib skips it)
    assert imap.uid.call_args.args == ('search', None, 'FROM', '"fish"')


def test_uid_search_with_charset_emits_charset_token():
    from plugins.email.tools import email_tool
    imap = MagicMock()
    imap.uid.return_value = ('OK', [b'1'])
    email_tool._uid_search(imap, 'UTF-8', ['BODY', b'"x"'])
    assert imap.uid.call_args.args == ('search', 'CHARSET', 'UTF-8', 'BODY', b'"x"')


def test_date_bracket_combines_older_tail_and_newer_head():
    from plugins.email.tools import email_tool
    imap = MagicMock()

    def uid_side(op, charset, *args):
        joined = ' '.join(str(a) for a in args)
        if 'BEFORE' in joined:
            return ('OK', [b'1 2 3 4 5'])
        if 'SINCE' in joined:
            return ('OK', [b'6 7 8 9 10'])
        return ('OK', [b''])

    imap.uid.side_effect = uid_side
    out = email_tool._search_date_bracket(imap, [], None, '2026-06-01', 2)
    # 2 closest older (tail) + 2 closest newer (head)
    assert out == [b'4', b'5', b'6', b'7']


def test_date_bracket_bad_date_returns_none():
    from plugins.email.tools import email_tool
    out = email_tool._search_date_bracket(MagicMock(), [], None, 'nope', 2)
    assert out is None
