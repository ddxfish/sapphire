"""Broadsword hunt fix wave — core (2026-09-18). Record: tmp/broadsword-hunt-20260918.md.

One test per shipped row so a regression names its bug. Source tripwires
pin the shape where a functional test would need a live system.
"""
import ast
import inspect
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _src(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


# ── H1: cloud tools must not declare themselves local ──────────────────────

_CLOUD_TOOLS = {
    "plugins/email/tools/email_tool.py": ["get_inbox", "read_email", "archive_emails", "delete_emails",
                                          "search_emails", "forward_email", "get_recipients", "send_email"],
    "plugins/twilio-voice/tools/phone_tool.py": ["phone_call"],
    "plugins/claude-code/tools/claude_code_tools.py": ["code_session"],
    "plugins/sapphire-store/tools/store_tools.py": ["store_browse", "store_install"],
    "plugins/bitcoin/tools/bitcoin_tool.py": ["get_wallet", "send_bitcoin", "get_transactions"],
}


def _is_local_by_name(path):
    """{tool name: is_local literal} for every tool decl dict in a module, via ast."""
    out = {}
    for node in ast.walk(ast.parse(_src(path))):
        if not isinstance(node, ast.Dict):
            continue
        keys = [k.value if isinstance(k, ast.Constant) else None for k in node.keys]
        if "function" not in keys or "is_local" not in keys:
            continue
        fn = node.values[keys.index("function")]
        name = None
        if isinstance(fn, ast.Dict):
            for k, v in zip(fn.keys, fn.values):
                if isinstance(k, ast.Constant) and k.value == "name" and isinstance(v, ast.Constant):
                    name = v.value
        loc = node.values[keys.index("is_local")]
        out[name] = loc.value if isinstance(loc, ast.Constant) else "?"
    return out


@pytest.mark.parametrize("path,tools", list(_CLOUD_TOOLS.items()))
def test_h1_cloud_tools_are_not_local(path, tools):
    """The private-chat gate consults ONLY is_local. True here = send_email /
    phone_call / a Claude Code session / a store GET from a private chat."""
    decl = _is_local_by_name(path)
    for t in tools:
        assert t in decl, f"{t} not found in {path}"
        assert decl[t] is not True, f"{t} in {path} declares is_local True"


def test_h1_mail_protocol_modules_declare_no_local_tool():
    for path in (ROOT / "plugins").glob("*/tools/*.py"):
        src = path.read_text(encoding="utf-8")
        if "import smtplib" in src or "import imaplib" in src:
            assert '"is_local": True' not in src, f"{path} talks SMTP/IMAP yet flags a tool local"


# ── H2: local_view_images takes files, never URLs ──────────────────────────

def test_h2_local_view_images_refuses_urls():
    from plugins.mindpalace.tools import library_tools
    out, ok = library_tools._local_view_images({"paths": ["https://evil.example/leak.png"]})
    assert ok is False
    assert "absolute" in out.lower()


# ── H10: the provider-reset leg imports a name that exists ─────────────────

def test_h10_plugin_loader_imports_settings_not_settings_manager():
    import core.settings_manager as sm
    assert hasattr(sm, "settings")
    assert not hasattr(sm, "settings_manager")
    src = _src("core/plugin_loader.py")
    assert "from core.settings_manager import settings_manager" not in src
    assert "from core.settings_manager import settings\n" in src


# ── H11 / M-C9: one funnel for service keys + the embedding gate ───────────

def test_h11_store_service_key_routes_to_credentials():
    import core.api_fastapi  # noqa: F401 — the app imports it first; routes import it back (cycle-safe only in that order)
    from core.routes import settings as routes
    fake = MagicMock()
    with patch("core.credentials_manager.credentials", fake):
        assert routes._store_service_key("STT_FIREWORKS_API_KEY", " abc ") is True
        fake.set_service_api_key.assert_called_once_with("stt_fireworks", "abc")
        assert routes._store_service_key("TTS_VOICE", "x") is False
        assert routes._store_service_key("EMBEDDING_API_KEY", "") is False


def test_h11_single_key_put_shares_both_rules():
    src = _src("core/routes/settings.py")
    put = src[src.index('@router.put("/api/settings/{key}")'):]
    assert "_store_service_key(key, value)" in put
    assert "_guard_embedding_swap(settings, value" in put
    assert src.count("embedding_swap_requires_confirmation") == 1, "the gate must exist exactly once"


# ── H13: backup exclusion matches posix paths on every OS ──────────────────

def test_h13_snapshot_lane_feeds_posix_paths():
    from core.backup import _is_excluded
    assert _is_excluded("history/sapphire_history.db", ["history/*"]) is True
    assert _is_excluded("history\\sapphire_history.db", ["history/*"]) is False, "the bug shape"
    src = _src("core/backup.py")
    assert "_is_excluded(_rel.as_posix(), merged_patterns)" in src
    assert "_is_excluded(str(_rel)" not in src


# ── H16: the LLM window always starts on a user row ────────────────────────

@patch("core.chat.history.config")
@patch("core.chat.history.count_tokens", return_value=50)
def test_h16_token_trim_lands_on_a_user_row(_count, cfg):
    cfg.CONTEXT_LIMIT = 1000     # effective = 1000 - (10 + 512) = 478; 85% target = 406
    cfg.LLM_MAX_HISTORY = 0
    from core.chat.history import ConversationHistory
    h = ConversationHistory()
    for i in range(6):
        h.add_user_message(f"u{i}")
        h.add_assistant_final(f"a{i}")
    h.add_user_message("u6")          # 13 rows = 650 tokens → 5 popped → would start on a3
    msgs = h.get_messages_for_llm()
    assert msgs[0]["role"] == "user"
    assert len(msgs) == 7             # a3 dropped too → u4..u6


@patch("core.chat.history.config")
@patch("core.chat.history.count_tokens", return_value=50)
def test_h16c_trim_goes_to_85_percent_for_a_stable_prefix(_count, cfg):
    cfg.CONTEXT_LIMIT = 1000
    cfg.LLM_MAX_HISTORY = 0
    from core.chat.history import ConversationHistory
    h = ConversationHistory()
    for i in range(6):
        h.add_user_message(f"u{i}")
        h.add_assistant_final(f"a{i}")   # 12 rows = 600 tokens
    msgs = h.get_messages_for_llm()
    assert len(msgs) == 8             # 400 ≤ 406; the old one-row trim left 9 (450 ≤ 478)
    assert msgs[0]["role"] == "user"


@patch("core.chat.history.config")
@patch("core.chat.history.count_tokens", return_value=1)
def test_h16_turn_trim_lands_on_a_user_row(_count, cfg):
    cfg.CONTEXT_LIMIT = 999999
    cfg.LLM_MAX_HISTORY = 2           # one pair
    from core.chat.history import ConversationHistory
    h = ConversationHistory()
    for i in range(3):
        h.add_user_message(f"u{i}")
        h.add_assistant_final(f"a{i}")
    msgs = h.get_messages_for_llm()
    assert msgs[0]["role"] == "user"  # the turn trim used to leave a2 at the front
    assert [m["content"] for m in msgs] == ["u2", "a2"]


@patch("core.chat.history.config")
@patch("core.chat.history.count_tokens", return_value=1)
def test_h16_untrimmed_history_keeps_an_opening_assistant_row(_count, cfg):
    """A phone greeting recorded before the caller speaks opens the chat on
    an assistant row — with NO trim, it stays (the Claude belt handles the
    wire). Only a trimmed window is forced onto a user row."""
    cfg.CONTEXT_LIMIT = 999999
    cfg.LLM_MAX_HISTORY = 0
    from core.chat.history import ConversationHistory
    h = ConversationHistory()
    h.add_assistant_final("Hello, this is Sapphire.")
    h.add_user_message("hi")
    h.add_assistant_final("How can I help?")
    msgs = h.get_messages_for_llm()
    assert [m["role"] for m in msgs] == ["assistant", "user", "assistant"]


def test_h16b_claude_provider_never_opens_on_an_assistant_row():
    from core.chat.llm_providers.claude import ClaudeProvider
    p = ClaudeProvider({"base_url": "https://api.anthropic.com", "api_key": "k",
                        "model": "claude-sonnet-4", "timeout": 60, "enabled": True})
    _sys, msgs, *_ = p._convert_messages([{"role": "assistant", "content": "hello"},
                                          {"role": "user", "content": "hi"}])
    assert msgs[0] == {"role": "user", "content": "[conversation started]"}
    assert msgs[1]["role"] == "assistant"


# ── H17: count_tokens is memoised once the tokenizer is up ─────────────────

def test_h17_count_tokens_memoised():
    from core.chat import history
    tok = MagicMock()
    tok.encode.side_effect = lambda text, **kw: list(text.split())
    with patch.object(history, "get_tokenizer", return_value=tok):
        history._count_tokens_cached.cache_clear()
        text = "one two three " * 50
        assert history.count_tokens(text) == 150
        assert history.count_tokens(text) == 150
        assert history._count_tokens_cached.cache_info().hits == 1
        assert tok.encode.call_count == 1
        assert history.count_tokens("") == 0
        assert history.count_tokens(12345) == 1     # coerced, not raised
    with patch.object(history, "get_tokenizer", return_value=None):
        assert history.count_tokens("abcdef") == 2  # estimate, never cached


def test_h17_history_route_counts_thinking_once():
    src = _src("core/routes/chat.py")
    start = src.index('@router.get("/api/history")')
    hist = src[start:src.index('@router.', start + 10)]
    assert 'count_tokens(m.get("thinking"' not in hist


# ── H3 / H8 / H9: voice exits ───────────────────────────────────────────────

def test_h3_speak_doors_take_the_producing_chat():
    from core.tts.tts_client import TTSClient
    from core.tts.stream_pump import StreamingTTSPump
    assert "chat_settings" in inspect.signature(TTSClient.speak).parameters
    assert "chat_settings" in inspect.signature(TTSClient.speak_sync).parameters
    assert "chat_settings" in inspect.signature(StreamingTTSPump.__init__).parameters
    assert "chat_settings=gate_settings" in _src("core/cadence.py")
    assert "tts_gate_reason(" not in _src("core/continuity/executor.py").split("speak_sync(response, chat_settings=")[0][-600:]


def test_h3_route_gate_settings_fail_closed_on_unknown_chat():
    from core.routes.tts import _gate_settings_for
    system = MagicMock()
    system.llm_chat.session_manager.get_settings_for.return_value = None
    assert _gate_settings_for(system, "") is None
    assert _gate_settings_for(system, "ghost") == {"private_chat": True}
    system.llm_chat.session_manager.get_settings_for.return_value = {"private_chat": False}
    assert _gate_settings_for(system, "room") == {"private_chat": False}


def test_h8_cadence_turn_end_survives_an_engine_raise():
    src = _src("core/cadence.py")
    body = src[src.index("def run_turn("):src.index("def fire_once(")]
    assert "except Exception as e:" in body and body.index("except Exception as e:") < body.index("finally:")
    assert "publish(Events.VOICE_TURN_END" in body


def test_h9_barge_in_cancel_is_scoped_to_one_chat():
    from core.conversation.driver import ConversationDriver
    system = MagicMock()
    system.llm_chat.session_manager.get_active_chat_name.return_value = "mine"
    d = ConversationDriver(system, transcribe_fn=lambda p: "", sink_factory=lambda: MagicMock())
    d._active_sink = None
    d._on_barge_in()
    system.cancel_generation.assert_called_once_with(chat_name="mine", exclude_chats=None)

    system2 = MagicMock()
    system2.llm_chat.session_manager.get_active_chat_name.side_effect = RuntimeError("no store")
    system2.get_conversation_manager.return_value.external_chats.return_value = {"phone_1"}
    d2 = ConversationDriver(system2, transcribe_fn=lambda p: "", sink_factory=lambda: MagicMock())
    d2._active_sink = None
    d2._on_barge_in()
    system2.cancel_generation.assert_called_once_with(chat_name=None, exclude_chats=["phone_1"])

    d3 = ConversationDriver(MagicMock(), transcribe_fn=lambda p: "", sink_factory=lambda: MagicMock(),
                            chat_name="phone_9")
    d3._active_sink = None
    d3._on_barge_in()
    d3.system.cancel_generation.assert_called_once_with(chat_name="phone_9", exclude_chats=None)


# ── H12: one toolset rule ───────────────────────────────────────────────────

def _tool(name):
    return {"type": "function", "function": {"name": name, "parameters": {}}}


def _fm():
    from core.chat.function_manager import FunctionManager
    fm = FunctionManager.__new__(FunctionManager)
    fm.all_possible_tools = [_tool("a"), _tool("hidden_x"), _tool("b"), _tool("c")]
    fm._hidden_tools = {"hidden_x"}
    fm.function_modules = {"mod": {"available_functions": ["a", "hidden_x"]}}
    fm._mode_filters = {}
    fm._settings_gates = {}
    fm._enabled_tools = []
    return fm


def test_h12_resolver_applies_the_hidden_rule_everywhere():
    fm = _fm()
    with patch("core.chat.function_manager.toolset_manager") as tm:
        tm.toolset_exists.side_effect = lambda n: n == "saved"
        tm.get_toolset_functions.return_value = ["hidden_x", "b"]
        assert fm.resolve_tool_names(["all"]) == (["a", "b", "c"], "all", None)
        assert fm.resolve_tool_names(["none"]) == ([], "none", None)
        assert fm.resolve_tool_names(["mod"]) == (["a"], "mod", None)
        assert fm.resolve_tool_names(["saved"]) == (["hidden_x", "b"], "saved", None)   # intent
        assert fm.resolve_tool_names(["ghost"]) == ([], "none", "ghost")
        assert fm.resolve_tool_names(["a", "hidden_x", "c"]) == (["a", "c"], "custom", None)
        # the read-only lane sees exactly what the setter would enable
        names = {t["function"]["name"] for t in fm.resolve_tools("all")}
        assert names == {"a", "b", "c"}
        names = {t["function"]["name"] for t in fm.resolve_tools("none", extra_toolsets=["mod"])}
        assert names == {"a"}
        assert fm.resolve_tools("none") is None
        assert fm.resolve_tools("ghost") is None


def test_h12_mirrors_are_delegates():
    assert "self.function_manager.resolve_tools(toolset_name, extra_toolsets)" in _src("core/chat/chat.py")
    assert "self.fm.resolve_tools(toolset_name, extra_toolsets)" in _src("core/continuity/execution_context.py")
    for rel in ("core/chat/chat.py", "core/continuity/execution_context.py"):
        assert "def _fn_names(name)" not in _src(rel)


# ── H15: renderer reconciles instead of rebuilding ─────────────────────────

def test_h15_render_history_reconciles():
    src = _src("interfaces/web/static/ui.js")
    assert "const historyKey = (m) =>" in src
    assert "existing[keep].dataset.key" in src
    assert "el.dataset.key = key" in src
    assert "chat.querySelectorAll('.message:not(.status):not(.error)').forEach(msg => msg.remove())" not in src


# ── M-C6 / M-C7 ─────────────────────────────────────────────────────────────

def test_mc6_tool_end_error_key_has_one_spelling():
    src = _src("core/chat/chat_streaming.py")
    assert '"is_error"' not in src, "tool_end carries `error`; `is_error` had no consumer"


def test_mc7_birth_funnel_refuses_a_sealed_prompt(tmp_path):
    from core.chat.history import ChatSessionManager
    sm = ChatSessionManager(history_dir=str(tmp_path))
    with patch("core.chat.history.sealed_prompt_name", side_effect=lambda n: n == "vault_secret"):
        sm.create_chat("born", settings={"prompt": "vault_secret", "toolset": "none"})
    got = sm.read_chat_settings("born") or {}
    assert got.get("prompt") != "vault_secret"
    assert got.get("toolset") == "none"
