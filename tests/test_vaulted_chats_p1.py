"""[REGRESSION_GUARD] Vaulted chats Phase 1 — the disclosure lock.

Plan: tmp/vaulted-chats-plan.md (rulings 2026-08-13/14). Sealed vault
(exists + locked) ⇒ private chats behave as NONEXISTENT at every
name-resolving store entry point, vanish from the list and search, can't
be created over (plaintext-twin guard), and a private ACTIVE chat is
evicted at lock time. The ACTIVE chat is exempt from by-name hiding (the
privacy gates read its settings to ENFORCE local-only — hiding would fail
open). The conftest `_no_real_vault_seal` autouse pin keeps the dev box's
real vault out; sealed tests monkeypatch `_vault_sealed` explicitly.
"""
import threading
from unittest.mock import patch

import pytest

TEST_DEFAULTS = {"prompt": "default"}


@pytest.fixture
def sm(tmp_path, monkeypatch):
    """Hermetic ChatSessionManager over a tmp DB with two chats:
    'pub' (public) and 'priv' (private, one message)."""
    with patch("core.chat.history.get_system_defaults",
               side_effect=lambda: dict(TEST_DEFAULTS)), \
         patch("core.chat.history.get_user_defaults",
               side_effect=lambda: dict(TEST_DEFAULTS)):
        from core.chat.history import ChatSessionManager
        m = ChatSessionManager(history_dir=str(tmp_path))
        m.create_chat("pub")
        m.create_chat("priv")
        m.set_named_chat_settings("priv", {"private_chat": True})
        m.append_messages_to_chat("priv", [
            {"role": "user", "content": "the secret gravy recipe"},
            {"role": "assistant", "content": "simmer quietly"},
        ])
        yield m


def _seal(monkeypatch, sealed=True):
    import core.chat.history as hist
    monkeypatch.setattr(hist, "_vault_sealed", lambda: sealed)


class TestListAndSearch:
    def test_unsealed_lists_both(self, sm, monkeypatch):
        _seal(monkeypatch, False)
        names = {c["name"] for c in sm.list_chat_files()}
        assert {"pub", "priv"} <= names

    def test_sealed_hides_private(self, sm, monkeypatch):
        _seal(monkeypatch, True)
        names = {c["name"] for c in sm.list_chat_files()}
        assert "pub" in names and "priv" not in names

    def test_include_hidden_ground_truth(self, sm, monkeypatch):
        """The eviction path needs truth even while sealed."""
        _seal(monkeypatch, True)
        names = {c["name"] for c in sm.list_chat_files(include_hidden=True)}
        assert "priv" in names

    def test_sealed_active_private_stays_listed(self, sm, monkeypatch):
        """Failed-eviction edge: the Phase 0 select-invariant needs the
        active chat representable; its name is already on screen."""
        sm.set_active_chat("priv")
        _seal(monkeypatch, True)
        names = {c["name"] for c in sm.list_chat_files()}
        assert "priv" in names

    def test_search_oracle_closed_when_sealed(self, sm, monkeypatch):
        _seal(monkeypatch, False)
        assert "priv" in sm.search_chat_content("gravy")
        _seal(monkeypatch, True)
        assert "priv" not in sm.search_chat_content("gravy")


class TestByNameGates:
    def test_reads_behave_as_nonexistent(self, sm, monkeypatch):
        _seal(monkeypatch, True)
        assert sm.read_chat_settings("priv") is None
        assert sm.get_settings_for("priv") is None
        assert sm.read_chat_messages("priv") == []
        assert sm.export_chat("priv") is None

    def test_writes_behave_as_nonexistent(self, sm, monkeypatch):
        _seal(monkeypatch, True)
        assert sm.set_named_chat_settings("priv", {"voice": "x"}) is False
        assert sm.clear_named_chat_messages("priv") is False
        assert sm.delete_chat("priv") is False
        ok, msg = sm.rename_chat("priv", "exposed")
        assert ok is False and "not found" in msg
        assert sm.append_messages_to_chat(
            "priv", [{"role": "user", "content": "hi"}]) is False
        assert sm.set_active_chat("priv") is False

    def test_trim_rides_export_gate(self, sm, monkeypatch):
        _seal(monkeypatch, True)
        ok, msg = sm.trim_chat("priv", 1, 1, preview=True)
        assert ok is False and "not found" in msg

    def test_everything_returns_on_unlock(self, sm, monkeypatch):
        _seal(monkeypatch, True)
        assert sm.read_chat_settings("priv") is None
        _seal(monkeypatch, False)
        s = sm.read_chat_settings("priv")
        assert s and s.get("private_chat") is True
        assert len(sm.read_chat_messages("priv")) == 2
        assert sm.set_active_chat("priv") is True

    def test_active_exemption_keeps_privacy_gates_sighted(self, sm, monkeypatch):
        """The one chat that must NEVER hide: the active one. voice_privacy
        and the LLM local-clamp read its settings to enforce local-only —
        None here would read as 'not private' and fail OPEN."""
        sm.set_active_chat("priv")
        _seal(monkeypatch, True)
        s = sm.read_chat_settings("priv")
        assert s and s.get("private_chat") is True
        assert sm.get_settings_for("priv").get("private_chat") is True

    def test_public_chats_unaffected_while_sealed(self, sm, monkeypatch):
        _seal(monkeypatch, True)
        assert sm.read_chat_settings("pub") is not None
        assert sm.set_named_chat_settings("pub", {"voice": "x"}) is True
        assert sm.set_active_chat("pub") is True


class TestPlaintextTwinGuard:
    def test_create_over_hidden_name_refused(self, sm, monkeypatch):
        """THE unforgivable outcome: creating a fresh public 'priv' while
        the real one is sealed would shadow it in the clear and collide at
        unlock. The PK row exists either way — create must refuse."""
        _seal(monkeypatch, True)
        assert sm.create_chat("priv") is False

    def test_executor_hidden_target_fails_loudly(self, monkeypatch):
        """Cron ruling (fail loudly, 2026-08-13): a task aimed at a vault-
        hidden chat dies with an error — no twin, no silent-void run."""
        from core.continuity import executor as ex_mod

        class _SM:
            def list_chat_files(self):
                return []          # sealed: the target is invisible

            def create_chat(self, name):
                return False       # ...but its row exists — collision

            def read_chat_settings(self, name):
                raise AssertionError("must fail before reading the chat")

        ex = ex_mod.ContinuityExecutor.__new__(ex_mod.ContinuityExecutor)
        ex._voice_lock = threading.RLock()
        ex._snapshot_voice = lambda: {}
        ex._apply_voice = lambda t: None
        ex._restore_voice = lambda snap: None
        ex.system = type("S", (), {"llm_chat": type("L", (), {
            "session_manager": _SM()})()})()
        result = {"errors": [], "responses": []}
        ex._run_foreground({"chat_target": "priv", "name": "t"}, result)
        assert result["errors"], "task must fail, not run against a void"
        assert "refused" in result["errors"][0]


class TestLockEviction:
    def _evict(self, sm, monkeypatch):
        import core.chat.history as hist
        published = []
        monkeypatch.setattr(hist, "publish",
                            lambda et, data=None: published.append((et, data)))
        landing = sm.evict_private_active()
        return landing, published

    def test_private_active_evicts_to_default(self, sm, monkeypatch):
        sm.set_active_chat("priv")
        _seal(monkeypatch, True)   # eviction runs post-seal inside lock()
        landing, published = self._evict(sm, monkeypatch)
        assert landing == "default"
        assert sm.get_active_chat_name() == "default"
        assert any(et == "chat_switched" and d.get("name") == "default"
                   for et, d in published)

    def test_public_active_untouched(self, sm, monkeypatch):
        sm.set_active_chat("pub")
        _seal(monkeypatch, True)
        landing, published = self._evict(sm, monkeypatch)
        assert landing is None
        assert sm.get_active_chat_name() == "pub"
        assert published == []

    def test_unsealed_never_evicts(self, sm, monkeypatch):
        sm.set_active_chat("priv")
        _seal(monkeypatch, False)
        landing, _ = self._evict(sm, monkeypatch)
        assert landing is None
        assert sm.get_active_chat_name() == "priv"

    def test_private_default_lands_on_freshest_public(self, sm, monkeypatch):
        sm.set_named_chat_settings("default", {"private_chat": True})
        sm.set_active_chat("priv")
        _seal(monkeypatch, True)
        landing, _ = self._evict(sm, monkeypatch)
        assert landing == "pub"
        assert sm.get_active_chat_name() == "pub"

    def test_no_safe_landing_creates_scratch(self, sm, monkeypatch):
        """Krem's ruling refinement: 'empty chat or new chat if default was
        private' — every chat private ⇒ a fresh scratch chat."""
        for name in ("default", "pub"):
            sm.set_named_chat_settings(name, {"private_chat": True})
        sm.set_active_chat("priv")
        _seal(monkeypatch, True)
        landing, _ = self._evict(sm, monkeypatch)
        assert landing == "scratch"
        assert sm.get_active_chat_name() == "scratch"
        assert not (sm.get_settings_for("scratch") or {}).get("private_chat")

    def test_lock_handoff_delegates_to_store(self, sm, monkeypatch):
        """prompt_vault.lock()'s chat handoff is a thin delegation."""
        import core.api_fastapi as api_mod
        from core import prompt_vault as pv
        fake_sys = type("S", (), {"llm_chat": type("L", (), {
            "session_manager": sm})()})()
        monkeypatch.setattr(api_mod, "get_system", lambda: fake_sys)
        sm.set_active_chat("priv")
        _seal(monkeypatch, True)
        pv._handoff_active_chat()
        assert sm.get_active_chat_name() == "default"


class TestBootEviction:
    def test_restart_with_private_active_evicts(self, tmp_path, monkeypatch):
        """THE live-caught hole (2026-08-14): the app died with a private
        chat active, .active_chat survives the restart, and boot comes up
        sealed — without the boot pass, the active-chat exemption
        resurrected the private chat's name, settings, and transcript."""
        import core.chat.history as hist
        with patch("core.chat.history.get_system_defaults",
                   side_effect=lambda: dict(TEST_DEFAULTS)), \
             patch("core.chat.history.get_user_defaults",
                   side_effect=lambda: dict(TEST_DEFAULTS)):
            # Session 1 (unlocked world): private chat active at "crash".
            monkeypatch.setattr(hist, "_vault_sealed", lambda: False)
            m1 = hist.ChatSessionManager(history_dir=str(tmp_path))
            m1.create_chat("priv")
            m1.set_named_chat_settings("priv", {"private_chat": True})
            assert m1.set_active_chat("priv")

            # Session 2: reboot into a sealed world.
            monkeypatch.setattr(hist, "_vault_sealed", lambda: True)
            m2 = hist.ChatSessionManager(history_dir=str(tmp_path))
            assert m2.get_active_chat_name() == "default"
            assert "priv" not in {c["name"] for c in m2.list_chat_files()}
