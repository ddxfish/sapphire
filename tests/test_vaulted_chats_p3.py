"""[REGRESSION_GUARD] Vaulted chats Phase 3 — sidecars & tunnels.

Plan: tmp/vaulted-chats-plan.md §9 (rulings F1-F5, 2026-08-15). The DB
boundary shipped in P0-P2; this phase closes what lives OUTSIDE it: the
hook withhold gate (F2), story/game × private refusal (F1), the
.active_chat marker, token-metrics name deposits, stale plaintext exports,
the tool-image hidden-owner gate, the Schedule route mask, replay-ring
ephemerality, and the agent-lane privacy carrier (F4).
"""
import base64
import json
import logging
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

TEST_DEFAULTS = {"prompt": "default"}
_KEY = bytes(range(32))
ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def sm(tmp_path, monkeypatch):
    """Hermetic ChatSessionManager: 'pub' (public, 2 msgs + 1 tool image)."""
    with patch("core.chat.history.get_system_defaults",
               side_effect=lambda: dict(TEST_DEFAULTS)), \
         patch("core.chat.history.get_user_defaults",
               side_effect=lambda: dict(TEST_DEFAULTS)):
        from core.chat.history import ChatSessionManager
        m = ChatSessionManager(history_dir=str(tmp_path))
        m.create_chat("pub")
        m.append_messages_to_chat("pub", [
            {"role": "user", "content": "the chutney secret <<IMG::tool:img1>>"},
            {"role": "assistant", "content": "simmer quietly"},
        ])
        m.save_tool_image("img1", b"\x89PNG-fake-bytes", "image/png", chat_name="pub")
        yield m


def _key_on(monkeypatch):
    from core import prompt_vault as pv
    monkeypatch.setattr(pv, "chat_data_key", lambda: _KEY)


def _seal(monkeypatch, sealed=True):
    import core.chat.history as hist
    monkeypatch.setattr(hist, "_vault_sealed", lambda: sealed)


def raw(tmp_path, sql, params=()):
    conn = sqlite3.connect(str(tmp_path / "sapphire_history.db"))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return rows


def marker(tmp_path):
    p = tmp_path / ".active_chat"
    return p.read_text(encoding="utf-8") if p.exists() else None


# ── F2: the hook withhold gate ──────────────────────────────────────────────

class TestHookWithholdGate:
    def _runner(self):
        from core.hooks import HookRunner
        return HookRunner()

    def test_private_turn_withheld_from_unaware(self):
        from core.hooks import HookEvent
        r = self._runner()
        fired = []
        r.register("post_chat", lambda e: fired.append("unaware"), plugin_name="plain")
        r.register("post_chat", lambda e: fired.append("aware"), plugin_name="careful")
        r.mark_privacy_aware("careful")
        r.set_privacy_resolver(lambda: ("secret", True))
        ev = r.fire("post_chat", HookEvent(input="x", response="y"))
        assert fired == ["aware"]
        assert ev.chat_name == "secret" and ev.chat_private is True

    def test_public_turn_reaches_everyone(self):
        from core.hooks import HookEvent
        r = self._runner()
        fired = []
        r.register("post_chat", lambda e: fired.append("unaware"), plugin_name="plain")
        r.set_privacy_resolver(lambda: ("open", False))
        r.fire("post_chat", HookEvent(input="x"))
        assert fired == ["unaware"]

    def test_always_deliver_bypasses_the_gate(self):
        from core.hooks import HookEvent
        r = self._runner()
        fired = []
        r.register("chat_renamed", lambda e: fired.append("unaware"), plugin_name="plain")
        r.set_privacy_resolver(lambda: ("secret", True))
        # Even an explicit private stamp must not withhold housekeeping —
        # a skipped rename strands a private chat's sidecars (carve-out).
        r.fire("chat_renamed", HookEvent(metadata={"old": "a", "new": "b"},
                                         chat_private=True))
        assert fired == ["unaware"]

    def test_resolver_failure_fails_closed(self):
        from core.hooks import HookEvent
        r = self._runner()
        fired = []
        r.register("post_chat", lambda e: fired.append(1), plugin_name="plain")

        def boom():
            raise RuntimeError("db down")
        r.set_privacy_resolver(boom)
        ev = r.fire("post_chat", HookEvent(input="x"))
        assert fired == [] and ev.chat_private is True

    def test_no_resolver_is_legacy_open(self):
        from core.hooks import HookEvent
        r = self._runner()
        fired = []
        r.register("post_chat", lambda e: fired.append(1), plugin_name="plain")
        ev = r.fire("post_chat", HookEvent(input="x"))
        assert fired == [1] and ev.chat_private is False

    def test_explicit_stamp_wins_over_resolver(self):
        from core.hooks import HookEvent
        r = self._runner()
        fired = []
        r.register("chat_vaulted", lambda e: fired.append(e.metadata["name"]),
                   plugin_name="careful")
        r.mark_privacy_aware("careful")
        r.set_privacy_resolver(lambda: ("otherchat", False))  # must NOT be consulted
        r.fire("chat_vaulted", __import__("core.hooks", fromlist=["HookEvent"])
               .HookEvent(metadata={"name": "s"}, chat_name="s", chat_private=True))
        assert fired == ["s"]

    def test_prestamped_name_resolves_that_chats_privacy(self):
        """S1 #7 / wave-3 B2 (2026-08-31): a fire site that pre-stamps
        chat_name (stream-brain override lane) must get THAT chat's privacy —
        the resolver used to overwrite the stamp with the ACTIVE chat's."""
        from core.hooks import HookEvent
        r = self._runner()
        seen = []
        r.register("post_chat", lambda e: seen.append(1), plugin_name="plain")

        def resolver(name=None):
            if name is not None:
                return (name, name == "vaulted-call")   # that chat is private
            return ("active-open", False)
        r.set_privacy_resolver(resolver)

        # Pre-stamped PRIVATE chat: withheld though the ACTIVE chat is open
        ev = r.fire("post_chat", HookEvent(input="x", chat_name="vaulted-call"))
        assert seen == [] and ev.chat_private is True
        assert ev.chat_name == "vaulted-call"           # stamp survives

        # Pre-stamped PUBLIC chat: delivered
        ev2 = r.fire("post_chat", HookEvent(input="x", chat_name="open-call"))
        assert seen == [1] and ev2.chat_private is False

    def test_prestamped_name_with_zero_arg_resolver_fails_closed(self):
        """A resolver without per-name support + a pre-stamped name must fail
        CLOSED — never mislabel the event from the active chat."""
        from core.hooks import HookEvent
        r = self._runner()
        seen = []
        r.register("post_chat", lambda e: seen.append(1), plugin_name="plain")
        r.set_privacy_resolver(lambda: ("active-open", False))
        ev = r.fire("post_chat", HookEvent(input="x", chat_name="other"))
        assert seen == [] and ev.chat_private is True

    def test_clear_resets_privacy_registry(self):
        r = self._runner()
        r.mark_privacy_aware("careful")
        r.set_privacy_resolver(lambda: ("x", True))
        r.clear()
        assert not r._privacy_aware and r._privacy_resolver is None

    def test_unregister_plugin_drops_awareness(self):
        r = self._runner()
        r.mark_privacy_aware("careful")
        r.unregister_plugin("careful")
        assert "careful" not in r._privacy_aware


# ── F1 LIFTED (v1.3, 2026-08-15): story/game chats vault like any other ─────
# The v1.2 refusal existed because engines kept plaintext files outside the
# vault. v1.3 moved those deposits into plugin_chat_data rows that seal with
# the chat — mode-tagged chats flip, seal, and release like any chat now.

class TestStoryGameVaulting:
    def test_named_flip_allowed_on_mode_chat(self, sm, tmp_path, monkeypatch):
        _key_on(monkeypatch)
        sm.create_chat("poker")
        assert sm.set_named_chat_settings("poker", {"mode": "game"})
        assert sm.set_named_chat_settings("poker", {"private_chat": True})
        assert raw(tmp_path, "SELECT vaulted FROM chats WHERE name='poker'")[0][0] == 1

    def test_active_flip_allowed_on_mode_chat(self, sm, monkeypatch):
        _key_on(monkeypatch)
        sm.create_chat("story1")
        sm.set_named_chat_settings("story1", {"mode": "game"})
        assert sm.set_active_chat("story1")
        assert sm.update_chat_settings({"private_chat": True})
        assert sm.current_settings.get("private_chat")

    def test_plugin_rows_seal_with_mode_chat(self, sm, tmp_path, monkeypatch):
        """The reason the lift is safe: a story journal on the chat's rows
        goes '@enc1:' the moment the chat flips."""
        _key_on(monkeypatch)
        sm.create_chat("gm")
        sm.set_named_chat_settings("gm", {"mode": "game"})
        sm.plugin_data_append("game-room", "gm", "story:journal:x",
                              {"event": "started", "turn": 0})
        assert sm.set_named_chat_settings("gm", {"private_chat": True})
        vals = [r[0] for r in raw(
            tmp_path, "SELECT value FROM plugin_chat_data WHERE chat_name='gm'")]
        assert vals and all(v.startswith("@enc1:") for v in vals)

    def test_plain_chat_still_flips(self, sm, tmp_path, monkeypatch):
        _key_on(monkeypatch)
        assert sm.set_named_chat_settings("pub", {"private_chat": True})
        assert raw(tmp_path, "SELECT vaulted FROM chats WHERE name='pub'")[0][0] == 1

    def test_private_mode_chat_still_releasable(self, sm, monkeypatch):
        _key_on(monkeypatch)
        sm.create_chat("legacy")
        sm.set_named_chat_settings("legacy", {"private_chat": True})
        sm.set_named_chat_settings("legacy", {"mode": "game"})
        assert sm.set_named_chat_settings("legacy", {"private_chat": False})


# ── T11/F5: the .active_chat marker never holds a private name ──────────────

class TestMarkerHygiene:
    def test_switch_to_private_blanks_marker(self, sm, tmp_path, monkeypatch):
        _key_on(monkeypatch)
        sm.set_active_chat("pub")
        assert marker(tmp_path) == "pub"
        sm.create_chat("priv")
        sm.set_named_chat_settings("priv", {"private_chat": True})
        assert sm.set_active_chat("priv")
        assert marker(tmp_path) == ""

    def test_flip_of_active_chat_blanks_marker(self, sm, tmp_path, monkeypatch):
        _key_on(monkeypatch)
        sm.set_active_chat("pub")
        assert marker(tmp_path) == "pub"
        assert sm.update_chat_settings({"private_chat": True})
        assert marker(tmp_path) == ""

    def test_public_switch_still_recorded(self, sm, tmp_path):
        sm.set_active_chat("pub")
        assert marker(tmp_path) == "pub"


# ── T12a/F5: token-metrics deposits ─────────────────────────────────────────

class TestMetricsPrivacy:
    def test_label_masks_private_active_chat(self, sm, monkeypatch):
        _key_on(monkeypatch)
        sm.set_active_chat("pub")
        assert sm.metrics_chat_label() == "pub"
        sm.update_chat_settings({"private_chat": True})
        assert sm.metrics_chat_label() == "__private__"

    def test_scrub_chat_rekeys_rows(self, tmp_path, monkeypatch):
        import core.metrics as m
        m.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        m.metrics._init_db()
        m.metrics.record("chutneychat", "prov", "model", "conversation",
                         {"tokens": {"prompt": 1, "total": 2}})
        m.metrics.record("other", "prov", "model", "conversation",
                         {"tokens": {"prompt": 1, "total": 2}})
        assert m.metrics.scrub_chat("chutneychat") == 1
        conn = sqlite3.connect(str(m.DB_PATH))
        names = sorted(r[0] for r in conn.execute(
            "SELECT chat_name FROM token_usage"))
        conn.close()
        assert names == ["__private__", "other"]

    def test_vault_chat_runs_the_scrub(self, sm, monkeypatch):
        import core.metrics as m
        m.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        m.metrics._init_db()
        m.metrics.record("pub", "prov", "model", "conversation",
                         {"tokens": {"total": 2}})
        _key_on(monkeypatch)
        ok, err = sm.vault_chat("pub")
        assert ok, err
        conn = sqlite3.connect(str(m.DB_PATH))
        names = [r[0] for r in conn.execute("SELECT chat_name FROM token_usage")]
        conn.close()
        assert names == ["__private__"]


# ── T13/F5: stale plaintext compress exports die at vault time ──────────────

class TestExportScrub:
    def test_exact_stamp_match_only(self, sm, tmp_path, monkeypatch):
        _key_on(monkeypatch)
        exp = tmp_path / "exports"
        exp.mkdir()
        mine = exp / "pub_20260101_120000.json"
        neighbor = exp / "pub_backup_20260101_120000.json"   # chat 'pub_backup'
        odd = exp / "pub_notastamp.json"
        for f in (mine, neighbor, odd):
            f.write_text("{}")
        ok, err = sm.vault_chat("pub")
        assert ok, err
        assert not mine.exists()
        assert neighbor.exists() and odd.exists()

    def test_chat_vaulted_hook_fires_to_aware_only(self, sm, monkeypatch):
        _key_on(monkeypatch)
        from core.hooks import hook_runner
        seen, unaware = [], []
        hook_runner.register("chat_vaulted",
                             lambda e: seen.append(e.metadata.get("name")),
                             plugin_name="_t_aware")
        hook_runner.register("chat_vaulted",
                             lambda e: unaware.append(1),
                             plugin_name="_t_plain")
        hook_runner.mark_privacy_aware("_t_aware")
        try:
            ok, err = sm.vault_chat("pub")
            assert ok, err
        finally:
            hook_runner.unregister_plugin("_t_aware")
            hook_runner.unregister_plugin("_t_plain")
        assert seen == ["pub"] and unaware == []


# ── T4: tool images — hidden owner + effective-chat keying ──────────────────

class TestToolImageGate:
    def test_legacy_plaintext_image_hidden_while_sealed(self, sm, tmp_path, monkeypatch):
        # legacy shape: private_chat=1 but vaulted=0 — image bytes PLAINTEXT
        conn = sqlite3.connect(str(tmp_path / "sapphire_history.db"))
        conn.execute("UPDATE chats SET settings='{\"private_chat\": true}' "
                     "WHERE name='pub'")
        conn.commit()
        conn.close()
        _seal(monkeypatch, True)
        assert sm.get_tool_image("img1") is None
        _seal(monkeypatch, False)
        got = sm.get_tool_image("img1")
        assert got and got[0] == b"\x89PNG-fake-bytes"

    def test_streaming_lane_saves_under_effective_chat(self):
        from core.chat import chat_tool_calling as ctc

        class FakeHist:
            def __init__(self):
                self.owners = []

            def save_tool_image(self, full_id, data, media_type, chat_name=None):
                self.owners.append(chat_name)
                return True

            def _effective_chat_name(self):
                return "phonechat"

        fh = FakeHist()
        img = {"data": base64.b64encode(b"png").decode(), "media_type": "image/png"}
        assert ctc._save_tool_image(img, fh)
        assert fh.owners == ["phonechat"]


# ── T14: Schedule route masks a hidden chat_target ──────────────────────────

class TestScheduleMask:
    def _system(self, hidden_names):
        return SimpleNamespace(llm_chat=SimpleNamespace(
            session_manager=SimpleNamespace(
                is_chat_hidden=lambda n: n in hidden_names)))

    @staticmethod
    def _routes():
        import core.api_fastapi  # noqa: F401 — break the route-module cycle
        from core.routes import system as sys_routes
        return sys_routes

    def test_hidden_target_masked_copy(self):
        sys_routes = self._routes()
        t = {"chat_target": "secret", "name": "nightly"}
        out = sys_routes._mask_locked_target(self._system({"secret"}), t)
        assert out["chat_target"] == "__locked__"
        assert t["chat_target"] == "secret"   # stored task untouched

    def test_visible_target_passes_through(self):
        sys_routes = self._routes()
        t = {"chat_target": "open", "name": "nightly"}
        out = sys_routes._mask_locked_target(self._system({"secret"}), t)
        assert out is t and out["chat_target"] == "open"


# ── T15: replay-ring ephemerality ───────────────────────────────────────────

class TestEventBusEphemeral:
    def test_ephemeral_flag_skips_replay(self):
        from core.event_bus import EventBus
        bus = EventBus(replay_size=10)
        bus.publish("plugin_custom", {"text": "secret words"}, ephemeral=True)
        assert len(bus._replay_buffer) == 0
        bus.publish("plugin_custom", {"text": "public words"})
        assert len(bus._replay_buffer) == 1

    def test_tts_speak_never_replayed(self):
        from core.event_bus import EventBus
        bus = EventBus(replay_size=10)
        bus.publish("tts_speak", {"text": "task response"})
        assert len(bus._replay_buffer) == 0


# ── T2/F4: the agent lane carries privacy ───────────────────────────────────

class _FakeAgentMgr:
    def __init__(self):
        self.calls = []

    def spawn(self, agent_type, mission, chat_name='', **kwargs):
        self.calls.append((agent_type, kwargs))
        return {"id": "a1", "name": "Alpha"}

    def get_types(self):
        return {}


class TestAgentPrivacyCarrier:
    def _spawn(self, arguments, private):
        from core.chat.function_manager import scope_private
        from plugins.agents.tools import agent_tools
        mgr = _FakeAgentMgr()
        token = scope_private.set(private)
        try:
            msg, ok = agent_tools._spawn_agent(mgr, arguments, {})
        finally:
            scope_private.reset(token)
        return msg, ok, mgr

    def test_claude_code_refused_from_private_chat(self):
        msg, ok, mgr = self._spawn(
            {"mission": "research", "agent_type": "claude_code"}, private=True)
        assert not ok and "private" in msg.lower()
        assert mgr.calls == []

    def test_llm_agent_inherits_privacy(self):
        msg, ok, mgr = self._spawn({"mission": "sort files"}, private=True)
        assert ok, msg
        agent_type, kwargs = mgr.calls[0]
        assert agent_type == "llm" and kwargs.get("_privacy_required") is True

    def test_public_chat_carries_nothing(self):
        msg, ok, mgr = self._spawn({"mission": "sort files"}, private=False)
        assert ok, msg
        _t, kwargs = mgr.calls[0]
        assert "_privacy_required" not in kwargs

    def test_worker_accepts_and_stores_the_flag(self):
        from plugins.agents.tools.agent_tools import _create_llm_worker
        W = _create_llm_worker()
        w = W(agent_id="a", name="A", mission="m", _privacy_required=True)
        assert w._privacy_required is True


# ── is_chat_hidden: the explicit plugin seam ────────────────────────────────

class TestIsChatHidden:
    def test_sealed_private_hidden(self, sm, monkeypatch):
        _key_on(monkeypatch)
        sm.set_named_chat_settings("pub", {"private_chat": True})
        _seal(monkeypatch, True)
        assert sm.is_chat_hidden("pub") is True

    def test_unsealed_visible(self, sm, monkeypatch):
        _key_on(monkeypatch)
        sm.set_named_chat_settings("pub", {"private_chat": True})
        assert sm.is_chat_hidden("pub") is False

    def test_active_chat_exempt(self, sm, monkeypatch):
        _key_on(monkeypatch)
        sm.set_active_chat("pub")
        sm.update_chat_settings({"private_chat": True})
        _seal(monkeypatch, True)
        assert sm.is_chat_hidden("pub") is False

    def test_nonexistent_not_hidden(self, sm, monkeypatch):
        _seal(monkeypatch, True)
        assert sm.is_chat_hidden("ghost") is False


# ── T7/T8: vault-labeled log lines carry no names ───────────────────────────

class TestLogHygiene:
    def test_vault_ops_never_log_the_chat_name(self, sm, monkeypatch, caplog):
        _key_on(monkeypatch)
        sm.create_chat("xq_hidden_xq")
        sm.append_messages_to_chat("xq_hidden_xq",
                                   [{"role": "user", "content": "hello"}])
        # Setup logs ('Created new chat: X') are GENERIC name lines — allowed
        # under ruling F3 (pragmatic). The assertion covers only the
        # vault-op window below, where the name is privacy-labeled.
        caplog.clear()
        with caplog.at_level(logging.INFO, logger="core.chat.history"):
            sm.set_named_chat_settings("xq_hidden_xq", {"private_chat": True})
            _seal(monkeypatch, True)
            sm.set_active_chat("xq_hidden_xq")       # refused — sealed
            sm.append_messages_to_chat("xq_hidden_xq",
                                       [{"role": "user", "content": "more"}])
            _seal(monkeypatch, False)
            sm.unvault_chat("xq_hidden_xq")
        assert "xq_hidden_xq" not in caplog.text


# ── T1: boot RAG cleanup must see hidden chats (wiring pin) ─────────────────

class TestRagCleanupWiring:
    def test_boot_cleanup_lists_include_hidden(self):
        """Source pin, deliberately: the destructive bug was the boot wiring
        (boot is always sealed → vaulted chats vanished from the valid list →
        their RAG scopes were 'orphans' and got DELETED every boot). A full
        VoiceChatSystem boot is too heavy for the suite; pin the call site."""
        src = (ROOT / "sapphire.py").read_text(encoding="utf-8")
        seg = src.split("def _cleanup_orphaned_rag")[1].split("\n    def ")[0]
        assert "include_hidden=True" in seg

    def test_hidden_chats_present_in_housekeeping_list(self, sm, monkeypatch):
        _key_on(monkeypatch)
        sm.set_named_chat_settings("pub", {"private_chat": True})
        _seal(monkeypatch, True)
        plain = {c["name"] for c in sm.list_chat_files()}
        full = {c["name"] for c in sm.list_chat_files(include_hidden=True)}
        assert "pub" not in plain and "pub" in full
