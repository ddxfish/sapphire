"""N1 regression (negspace hunt 2026-08-31): the email inbox cache must never
launder a None scope into the shared 'default' bucket, and the executor must
refuse every email tool while the scope is unresolved/disabled. Pre-fix, a
chat with email OFF could read another scope's cached mail (full bodies, no
TTL) for the life of the process."""
import importlib.util
import sys
import time
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))


def _load_email_tool():
    path = _root / "plugins" / "email" / "tools" / "email_tool.py"
    spec = importlib.util.spec_from_file_location("email_tool_n1_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _seed_default_bucket(et):
    et._inbox_cache.clear()
    et._inbox_cache["default"] = {
        "folder": "inbox",
        "messages": [{"subject": "LEAKED-SUBJECT"}],
        "raw": ["LEAKED-RAW"],
        "msg_ids": [1],
        "timestamp": time.time(),
    }


def test_none_scope_never_touches_default_bucket(monkeypatch):
    et = _load_email_tool()
    _seed_default_bucket(et)
    monkeypatch.setattr(et, "_get_current_email_scope", lambda: None)
    cache = et._get_cache()
    assert cache["messages"] == []          # throwaway, not the default bucket
    assert cache["raw"] == []
    et._reset_cache()                        # must not clobber 'default'
    assert et._inbox_cache["default"]["messages"]


def test_executor_refuses_all_email_tools_when_scope_none(monkeypatch):
    et = _load_email_tool()
    _seed_default_bucket(et)
    monkeypatch.setattr(et, "_get_current_email_scope", lambda: None)
    for fn, args in [("get_inbox", {}), ("read_email", {"index": 1}),
                     ("search_emails", {"content": "x"})]:
        msg, ok = et.execute(fn, args, None)
        assert ok is False, f"{fn} returned ok=True with scope=None"
        assert "disabled" in msg.lower()
        assert "LEAKED" not in msg


def test_real_scope_uses_its_own_bucket(monkeypatch):
    et = _load_email_tool()
    et._inbox_cache.clear()
    monkeypatch.setattr(et, "_get_current_email_scope", lambda: "work")
    cache = et._get_cache()
    cache["messages"].append({"subject": "mine"})
    assert et._inbox_cache["work"]["messages"] == [{"subject": "mine"}]
    assert "default" not in et._inbox_cache
