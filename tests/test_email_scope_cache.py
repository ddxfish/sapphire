"""N1 regression (negspace hunt 2026-08-31): a chat with email OFF must never
read another scope's mail. The old plugin kept fetched mail in a module-level
cache keyed by scope and once laundered a None scope into the shared 'default'
bucket. The rebuilt plugin (2.0) keeps no mail in memory at all, so the guard
is now two things: the executor refuses every tool while the scope is None,
and no module of the plugin holds a mail cache to leak from."""
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))


def test_executor_refuses_all_email_tools_when_scope_none(monkeypatch):
    from plugins.email.tools import email_tool as et
    from plugins.email import accounts
    monkeypatch.setattr(et, "_get_current_email_scope", lambda: None)
    reached = []
    monkeypatch.setattr(accounts, "get_creds", lambda scope: reached.append(scope) or (None, "x"))
    for fn in et.AVAILABLE_FUNCTIONS:
        msg, ok = et.execute(fn, {"id": "inbox:1", "ids": ["inbox:1"], "to": "trash", "body": "x"}, None)
        assert ok is False, f"{fn} returned ok=True with scope=None"
        assert "disabled" in msg.lower()
    assert reached == []            # refused at the door, before any credential path


def test_plugin_keeps_no_mail_in_memory():
    from plugins.email.tools import email_tool as et
    from plugins.email import mailbox
    assert not hasattr(et, "_inbox_cache")
    # The one module-level cache is the folder-name map: names only, per scope.
    caches = [n for n in vars(mailbox) if n.endswith("_cache")]
    assert caches == ["_folder_cache"]
