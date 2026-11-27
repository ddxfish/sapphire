"""What a turn may put in the log.

A private chat's words are encrypted at rest; the log is plaintext and kept
for 30 days. Text that can quote a turn (a malformed tool call's arguments,
a tool's exception, a model server's error body echoing the request) is
logged as its size or kind when the turn is private, and as it always was
otherwise. Found live 2026-11-26: the speech pump logged every sentence it
voiced, and a private reply sat in user/logs.

Only for code that runs inside the turn (the tool door, the provider call,
the streaming loop): there the turn's own flag is readable. Anything logged
outside a turn must not carry content at all.
"""


def private_turn() -> bool:
    """Is this a private chat's turn? Cannot tell = yes."""
    try:
        from core.chat.function_manager import scope_private
        return bool(scope_private.get())
    except Exception:
        return True


def words(text) -> str:
    """Text for a log line: itself, or only its size in a private turn."""
    if not private_turn():
        return repr(text)
    return f"<{len(str(text or ''))} chars, withheld: private chat>"


def err(e) -> str:
    """An exception for a log line: its words, or only its kind and HTTP
    status in a private turn."""
    if not private_turn():
        return str(e)
    status = getattr(e, 'status_code', None)
    return f"{type(e).__name__}{f' (HTTP {status})' if status else ''} <words withheld: private chat>"


def exc(e):
    """The exc_info to log with: True, or in a private turn the same stack
    under a stand-in exception, because a traceback ends with the words."""
    if not private_turn():
        return True
    return (Exception, Exception(f"{type(e).__name__} <words withheld: private chat>"), e.__traceback__)
