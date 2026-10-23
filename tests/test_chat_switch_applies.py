"""[REGRESSION_GUARD] SWITCH MEANS APPLY (2026-08-22).

A chat switch is two halves: the store flips the active chat
(ChatSessionManager.set_active_chat) and the runtime applies that chat's
stored settings to the brain (_apply_chat_settings: prompt, toolset,
voice, spice, scopes). The activate route did both; the vault-lock
eviction did only the store half — after a lock, the sidebar painted the
landing chat's toolset while the FM still held the private chat's (Prime,
2026-08-22: "toolset Sapphire shown, toolset none in fact").

Contract now: set_active_chat fires `on_switched(name, settings, gen)` on
EVERY True return (same-chat no-op included — re-activating is the user's
repair), outside the store lock, with a snapshot captured under it. The
system installs the hook post-plugin-scan (sapphire.py) → apply_on_switch,
which serializes on _apply_lock and drops stale generations.
Plan: tmp/chat-switch-primitive-plan.md.
"""
import re
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
TEST_DEFAULTS = {"prompt": "default", "toolset": "defset"}


@pytest.fixture
def sm(tmp_path):
    """Bare store over a tmp DB: 'pub' (toolset pubset) and 'priv'
    (private, toolset none)."""
    with patch("core.chat.history.get_system_defaults",
               side_effect=lambda: dict(TEST_DEFAULTS)), \
         patch("core.chat.history.get_user_defaults",
               side_effect=lambda: dict(TEST_DEFAULTS)):
        from core.chat.history import ChatSessionManager
        m = ChatSessionManager(history_dir=str(tmp_path))
        m.create_chat("pub")
        m.set_named_chat_settings("pub", {"toolset": "pubset"})
        m.create_chat("priv")
        m.set_named_chat_settings("priv", {"private_chat": True, "toolset": "none"})
        yield m


def _seal(monkeypatch, sealed=True):
    import core.chat.history as hist
    monkeypatch.setattr(hist, "_vault_sealed", lambda: sealed)


def _spy(sm):
    calls = []
    sm.on_switched = lambda name, settings, gen: calls.append((name, settings, gen))
    return calls


class TestStoreFiresHook:
    def test_bare_store_has_no_hook(self, sm):
        assert sm.on_switched is None
        assert sm.set_active_chat("pub") is True

    def test_real_switch_fires_with_landing_snapshot(self, sm):
        calls = _spy(sm)
        assert sm.set_active_chat("pub")
        assert len(calls) == 1
        name, settings, gen = calls[0]
        assert name == "pub"
        assert settings["toolset"] == "pubset"
        assert isinstance(gen, int)

    def test_snapshot_is_a_copy(self, sm):
        calls = _spy(sm)
        sm.set_active_chat("pub")
        calls[0][1]["toolset"] = "tampered"
        assert sm.get_chat_settings()["toolset"] == "pubset"

    def test_same_chat_noop_fires_too(self, sm):
        """Re-activating the active chat is the documented user repair —
        the route used to apply unconditionally; the hook must as well."""
        calls = _spy(sm)
        sm.set_active_chat("pub")
        sm.set_active_chat("pub")
        assert [c[0] for c in calls] == ["pub", "pub"]

    def test_gen_increments_every_fire(self, sm):
        calls = _spy(sm)
        sm.set_active_chat("pub")
        sm.set_active_chat("pub")
        sm.set_active_chat("default")
        gens = [c[2] for c in calls]
        assert gens == sorted(gens) and len(set(gens)) == 3
        assert sm._switch_gen == gens[-1]

    def test_refused_while_streaming_does_not_fire(self, sm):
        calls = _spy(sm)
        sm._is_streaming = True
        assert sm.set_active_chat("pub") is False
        assert calls == []

    def test_refused_sealed_target_does_not_fire(self, sm, monkeypatch):
        _seal(monkeypatch, True)
        calls = _spy(sm)
        assert sm.set_active_chat("priv") is False
        assert calls == []

    def test_hook_failure_never_fails_switch(self, sm):
        def boom(*a):
            raise RuntimeError("apply exploded")
        sm.on_switched = boom
        assert sm.set_active_chat("pub") is True
        assert sm.get_active_chat_name() == "pub"

    def test_hook_fires_outside_store_lock(self, sm):
        """Inside the lock = real A→B/B→A deadlock against a plugin scan
        holding fm._tools_lock while reaching back into the store."""
        seen = {}
        def probe(name, settings, gen):
            # RLock: acquire(blocking=False) from the SAME thread succeeds
            # even if held, so probe from another thread.
            def other():
                seen["free"] = sm._lock.acquire(blocking=False)
                if seen["free"]:
                    sm._lock.release()
            t = threading.Thread(target=other)
            t.start(); t.join(2)
        sm.on_switched = probe
        sm.set_active_chat("pub")
        assert seen.get("free") is True


class TestEvictionApplies:
    """THE bug: vault-lock eviction must apply the landing chat."""

    def test_eviction_fires_hook_with_landing_settings(self, sm, monkeypatch):
        sm.set_active_chat("priv")
        _seal(monkeypatch, True)
        calls = _spy(sm)
        import core.chat.history as hist
        monkeypatch.setattr(hist, "publish", lambda *a, **k: None)
        assert sm.evict_private_active() == "default"
        assert len(calls) == 1
        name, settings, _ = calls[0]
        assert name == "default"
        assert settings["toolset"] == "defset"        # not priv's 'none'
        assert not settings.get("private_chat")

    def test_eviction_applies_before_chat_switched_publish(self, sm, monkeypatch):
        """Clients refetch on CHAT_SWITCHED — the brain must already match."""
        sm.set_active_chat("priv")
        _seal(monkeypatch, True)
        order = []
        sm.on_switched = lambda n, s, g: order.append(("apply", n))
        import core.chat.history as hist
        monkeypatch.setattr(hist, "publish",
                            lambda et, data=None, **k: order.append((et, (data or {}).get("name"))))
        sm.evict_private_active()
        assert order == [("apply", "default"), ("chat_switched", "default")]


class TestApplyOnSwitch:
    def _system(self, sm):
        system = MagicMock()
        system.llm_chat.session_manager = sm
        return system

    def test_stale_generation_is_skipped(self, sm):
        from core import api_fastapi
        system = self._system(sm)
        sm.set_active_chat("pub")          # bumps gen
        with patch.object(api_fastapi, "_apply_chat_settings_unlocked") as applied:
            api_fastapi.apply_on_switch(system, "default", {"toolset": "x"}, sm._switch_gen - 1)
            applied.assert_not_called()
            api_fastapi.apply_on_switch(system, "pub", {"toolset": "y"}, sm._switch_gen)
            applied.assert_called_once_with(system, {"toolset": "y"})

    def test_apply_holds_the_apply_lock(self, sm):
        """All six pre-existing call sites were serialized by the event
        loop by accident; the hook fires from Timer threads — the lock
        replaces the accident."""
        from core import api_fastapi
        system = self._system(sm)
        held = {}
        def probe(system_, settings_):
            held["owned"] = api_fastapi._apply_lock._is_owned()
        with patch.object(api_fastapi, "_apply_chat_settings_unlocked", side_effect=probe):
            api_fastapi.apply_on_switch(system, "pub", {}, sm._switch_gen)
            assert held["owned"] is True
            held.clear()
            api_fastapi._apply_chat_settings(system, {})
            assert held["owned"] is True

    def test_apply_serializes_across_threads(self, sm):
        from core import api_fastapi
        system = self._system(sm)
        inside = {"n": 0, "max": 0}
        gate = threading.Lock()
        def slow(system_, settings_):
            with gate:
                inside["n"] += 1
                inside["max"] = max(inside["max"], inside["n"])
            threading.Event().wait(0.05)
            with gate:
                inside["n"] -= 1
        with patch.object(api_fastapi, "_apply_chat_settings_unlocked", side_effect=slow):
            ts = [threading.Thread(target=api_fastapi._apply_chat_settings, args=(system, {}))
                  for _ in range(4)]
            [t.start() for t in ts]; [t.join(5) for t in ts]
        assert inside["max"] == 1


class TestWiring:
    def test_boot_installs_hook_after_post_scan_toolset_apply(self):
        """Pre-scan apply = the 2026-08-19 'Enabled: []' class. The hook
        must be installed AFTER the one post-scan toolset apply, and never
        inside _apply_initial_chat_settings."""
        src = (ROOT / "sapphire.py").read_text(encoding="utf-8")
        post_scan = src.index("Apply the toolset now that plugin tools are registered")
        install = src.index("session_manager.on_switched =")
        assert install > post_scan
        m = re.search(r"def _apply_initial_chat_settings\(self\):[\s\S]+?\n    def ", src)
        assert m and "on_switched" not in m.group(0)

    def test_activate_route_no_longer_applies_itself(self):
        src = (ROOT / "core/routes/chat.py").read_text(encoding="utf-8")
        m = re.search(r"async def activate_chat\([\s\S]+?\n@router", src)
        assert m and "_apply_chat_settings(" not in m.group(0)

    def test_delete_path_keeps_its_explicit_apply(self):
        """delete_chat rebinds the active chat by hand — the hook can't see
        it, so the route's apply must stay."""
        src = (ROOT / "core/routes/chat.py").read_text(encoding="utf-8")
        m = re.search(r"def _delete_one_chat\([\s\S]+?# Cleanup per-chat RAG", src)
        assert m and "_apply_chat_settings(system, settings)" in m.group(0)
