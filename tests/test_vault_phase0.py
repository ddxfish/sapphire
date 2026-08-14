"""[REGRESSION_GUARD] Prompt-vault phase 0: piece funnels, lock-ordering
contract, continuity privacy carrier.

Phase 0 of tmp/prompt-vault-plan.md (2026-08-09) — shipped BEFORE any vault
code so the vault lands on proven rails:
  0a  item-level component funnels in prompt_crud (save_component /
      delete_component / save_components_batch) — the single write path the
      vault's per-item routing will hook.
  0b  the lock-then-PUT ordering contract on the chat-settings privacy guard:
      privacy_required prompt blocks PUT private_chat:false with 409; a name
      that no longer RESOLVES passes — that pass is what the vault's eyeball
      client sequences against (lock first, then PUT).
  0c  task-level privacy carrier: privacy_required persisted on continuity
      tasks, OR-ed with the live prompt-derived flag at run time, so an
      unresolvable pinned prompt (deleted / locked vault) can no longer
      silently drop a task onto cloud providers.
"""
import asyncio
import threading

import pytest
from fastapi import HTTPException

import core.api_fastapi  # noqa: F401  (route modules trip circular imports alone)
import core.prompt_crud as pc


class FakePM:
    """Mirrors the real PromptManager contract the funnels rely on: reads via
    the merged `.components` property, writes via `._components`, mutations
    under `._lock`, savers return bool."""
    def __init__(self, save_result=True):
        self._components = {}
        self._lock = threading.RLock()
        self.save_calls = 0
        self._save_result = save_result

    @property
    def components(self):
        return self._components

    def save_components(self, reason=None):
        self.save_calls += 1
        return self._save_result


@pytest.fixture
def fake_pm(monkeypatch):
    fake = FakePM()
    monkeypatch.setattr(pc, "prompt_manager", fake)
    return fake


# ── 0a: save_component ──

class TestSaveComponent:
    def test_creates_and_updates(self, fake_pm):
        ok, msg = pc.save_component("character", "pirate", "Arr.")
        assert ok
        assert fake_pm._components["character"]["pirate"] == "Arr."
        ok, _ = pc.save_component("character", "pirate", "Arr v2.")
        assert ok
        assert fake_pm._components["character"]["pirate"] == "Arr v2."
        assert fake_pm.save_calls == 2

    def test_non_string_refused(self, fake_pm):
        ok, msg = pc.save_component("character", "bad", {"not": "a string"})
        assert not ok
        assert "string" in msg
        assert fake_pm.save_calls == 0
        assert "character" not in fake_pm._components

    def test_store_latch_propagates(self, monkeypatch):
        fake = FakePM(save_result=False)
        monkeypatch.setattr(pc, "prompt_manager", fake)
        ok, msg = pc.save_component("character", "x", "y")
        assert not ok
        assert "refused" in msg.lower()


# ── 0a: delete_component ──

class TestDeleteComponent:
    def test_deletes_user_entry(self, fake_pm):
        fake_pm._components["mood"] = {"grumpy": "..."}
        ok, code = pc.delete_component("mood", "grumpy")
        assert ok and code == ''
        assert "grumpy" not in fake_pm._components["mood"]

    def test_not_found_code(self, fake_pm, monkeypatch):
        import core.prompt_packs as packs
        monkeypatch.setattr(packs, "piece_source", lambda t, k: None)
        ok, code = pc.delete_component("mood", "ghost")
        assert not ok and code == 'not_found'

    def test_pack_owned_code(self, fake_pm, monkeypatch):
        import core.prompt_packs as packs
        monkeypatch.setattr(packs, "piece_source", lambda t, k: "some-plugin")
        ok, code = pc.delete_component("mood", "packpiece")
        assert not ok and code == 'pack_owned'

    def test_store_latch_code(self, monkeypatch):
        fake = FakePM(save_result=False)
        fake._components["mood"] = {"grumpy": "..."}
        monkeypatch.setattr(pc, "prompt_manager", fake)
        ok, code = pc.delete_component("mood", "grumpy")
        assert not ok and code == 'store_latch'


# ── 0a: save_components_batch ──

class TestBatch:
    def test_one_save_many_writes(self, fake_pm):
        ok, msg = pc.save_components_batch({
            "character": {"a": "1", "b": "2"},
            "mood": {"c": "3"},
        })
        assert ok
        assert fake_pm.save_calls == 1
        assert fake_pm._components["character"] == {"a": "1", "b": "2"}
        assert fake_pm._components["mood"] == {"c": "3"}

    def test_keep_set_skips(self, fake_pm):
        fake_pm._components["character"] = {"keepme": "LOCAL"}
        ok, _ = pc.save_components_batch(
            {"character": {"keepme": "NEW", "other": "NEW2"}},
            keep={("character", "keepme")})
        assert ok
        assert fake_pm._components["character"]["keepme"] == "LOCAL"
        assert fake_pm._components["character"]["other"] == "NEW2"

    def test_no_overwrite_skips_existing(self, fake_pm):
        fake_pm._components["character"] = {"have": "LOCAL"}
        ok, _ = pc.save_components_batch(
            {"character": {"have": "NEW", "fresh": "NEW2"}}, overwrite=False)
        assert ok
        assert fake_pm._components["character"]["have"] == "LOCAL"
        assert fake_pm._components["character"]["fresh"] == "NEW2"

    def test_non_string_and_non_dict_skipped(self, fake_pm):
        ok, _ = pc.save_components_batch({
            "character": {"good": "text", "bad": 42},
            "junk": "not-a-dict",
        })
        assert ok
        assert fake_pm._components["character"] == {"good": "text"}
        assert "junk" not in fake_pm._components

    def test_empty_batch_no_save(self, fake_pm):
        ok, _ = pc.save_components_batch({})
        assert ok
        assert fake_pm.save_calls == 0


# ── 0b: lock-then-PUT ordering contract ──

class _FakeRequest:
    def __init__(self, payload):
        self._payload = payload
        self.headers = {}

    async def json(self):
        return self._payload


class _FakeSessionManager:
    def __init__(self, stored):
        self.stored = stored
        self.active_chat_name = "other-chat"
        self.written = None

    def read_chat_settings(self, name):
        return dict(self.stored)

    def get_active_chat_name(self):
        return self.active_chat_name

    def set_named_chat_settings(self, name, settings):
        self.written = settings
        return True


class _FakeSystem:
    def __init__(self, sm):
        self.llm_chat = type("L", (), {"session_manager": sm})()


def _put_settings(chat_settings, payload, monkeypatch, resolver):
    """Drive the PUT /api/chats/{name}/settings route directly (non-active
    chat branch — storage write, no live apply)."""
    from core.routes.chat import update_chat_settings
    from core import prompts as prompts_mod
    monkeypatch.setattr(prompts_mod, "get_prompt", resolver)
    sm = _FakeSessionManager(chat_settings)
    return asyncio.run(update_chat_settings(
        "pinned-chat", _FakeRequest({"settings": payload}),
        None, _FakeSystem(sm))), sm


class TestLockOrderingContract:
    def test_privacy_required_prompt_blocks_put_false(self, monkeypatch):
        """(i) While the pinned prompt RESOLVES privacy_required, disabling
        private_chat 409s — the eyeball client cannot PUT first."""
        with pytest.raises(HTTPException) as ei:
            _put_settings(
                {"prompt": "moonlight", "private_chat": True},
                {"private_chat": False}, monkeypatch,
                lambda name: {"name": name, "privacy_required": True})
        assert ei.value.status_code == 409

    def test_unresolved_prompt_passes_put_false(self, monkeypatch):
        """(ii) THE lock-then-PUT contract: once the name no longer resolves
        (vault locked / prompt gone), the same PUT succeeds. The vault's
        eyeball flow depends on this exact pass — lock first, then PUT."""
        result, sm = _put_settings(
            {"prompt": "moonlight", "private_chat": True},
            {"private_chat": False}, monkeypatch,
            lambda name: None)
        assert result["status"] == "success"
        assert sm.written == {"private_chat": False}


# ── 0c: continuity privacy carrier ──

def _bare_scheduler():
    """ContinuityScheduler without boot wiring — just what create/update use."""
    from core.continuity.scheduler import ContinuityScheduler
    s = ContinuityScheduler.__new__(ContinuityScheduler)
    s._lock = threading.RLock()
    s._tasks = {}
    s._task_pending = {}
    s._task_last_matched = {}
    s._save_tasks = lambda: None
    return s


@pytest.fixture
def private_prompts(monkeypatch):
    """Resolver where 'moonlight' is privacy_required and 'sunny' is not."""
    from core import prompts as prompts_mod

    def resolver(name):
        if name == "moonlight":
            return {"name": name, "privacy_required": True}
        if name == "sunny":
            return {"name": name, "privacy_required": False}
        return None
    monkeypatch.setattr(prompts_mod, "get_prompt", resolver)


class TestCarrierStamp:
    def test_create_stamps_from_private_prompt(self, private_prompts):
        t = _bare_scheduler().create_task({"prompt": "moonlight"})
        assert t["privacy_required"] is True

    def test_create_public_prompt_unstamped(self, private_prompts):
        t = _bare_scheduler().create_task({"prompt": "sunny"})
        assert t["privacy_required"] is False

    def test_create_explicit_flag_survives_public_prompt(self, private_prompts):
        t = _bare_scheduler().create_task({"prompt": "sunny", "privacy_required": True})
        assert t["privacy_required"] is True

    def test_update_prompt_swap_restamps(self, private_prompts):
        s = _bare_scheduler()
        t = s.create_task({"prompt": "sunny"})
        updated = s.update_task(t["id"], {"prompt": "moonlight"})
        assert updated["privacy_required"] is True

    def test_update_cannot_unflag_private_prompt(self, private_prompts):
        """Same rule as the chat-settings 409 guard: switch prompts first."""
        s = _bare_scheduler()
        t = s.create_task({"prompt": "moonlight"})
        updated = s.update_task(t["id"], {"privacy_required": False})
        assert updated["privacy_required"] is True

    def test_update_explicit_clear_on_public_prompt(self, private_prompts):
        s = _bare_scheduler()
        t = s.create_task({"prompt": "moonlight", "privacy_required": True})
        updated = s.update_task(t["id"], {"prompt": "sunny", "privacy_required": False})
        assert updated["privacy_required"] is False

    def test_stale_true_is_fail_closed(self, private_prompts):
        """Prompt goes public later, user touches an unrelated field — the
        carrier stays True (stale-CLOSED) until explicitly cleared."""
        s = _bare_scheduler()
        t = s.create_task({"prompt": "moonlight"})
        updated = s.update_task(t["id"], {"name": "renamed"})
        assert updated["privacy_required"] is True


class TestCarrierRuntime:
    def _build_flag(self, task_settings, monkeypatch, resolver):
        from core.continuity.execution_context import ExecutionContext
        from core import prompts as prompts_mod
        monkeypatch.setattr(prompts_mod, "get_prompt", resolver)
        ctx = ExecutionContext.__new__(ExecutionContext)
        ctx.task_settings = task_settings
        ExecutionContext._build_prompt(ctx)
        return ctx._prompt_privacy_required

    def test_carrier_survives_unresolved_prompt(self, monkeypatch):
        """THE 3am fix: pinned prompt asleep in a locked vault (resolves
        None) — the carrier keeps the run local-only."""
        flag = self._build_flag(
            {"prompt": "moonlight", "privacy_required": True},
            monkeypatch, lambda name: None)
        assert flag is True

    def test_no_carrier_unresolved_is_public(self, monkeypatch):
        flag = self._build_flag(
            {"prompt": "typo-name"}, monkeypatch, lambda name: None)
        assert flag is False

    def test_live_derivation_still_wins(self, monkeypatch):
        """Prompt became private AFTER the task was saved — live half of the
        OR catches it with no carrier present."""
        flag = self._build_flag(
            {"prompt": "moonlight"}, monkeypatch,
            lambda name: {"name": name, "content": "x", "privacy_required": True})
        assert flag is True

    def test_extract_task_settings_carries_flag(self):
        from core.continuity.executor import ContinuityExecutor
        settings = ContinuityExecutor._extract_task_settings(
            {"prompt": "moonlight", "privacy_required": True})
        assert settings["privacy_required"] is True


# ── vaulted-chats Phase 0: the CHAT half of the privacy carrier ──

class _CtxAbort(Exception):
    """Raised by the capture-fake so the run stops before any LLM work."""


class TestChatCarrierForeground:
    """_run_foreground ORs the TARGET CHAT's private_chat flag into the same
    privacy carrier the prompt gate uses (vaulted-chats Phase 0, 2026-08-13).
    Before this, only a privacy_required PROMPT forced local-only in the
    continuity lane — a cron/agent/daemon task with chat_target=<private
    chat> plus a cloud provider shipped that chat's transcript out."""

    def _foreground_settings(self, monkeypatch, task, chat_settings):
        from core.continuity import executor as ex_mod
        import core.continuity.execution_context as ec_mod
        captured = {}

        class _CaptureCtx:
            def __init__(self, fm, engine, task_settings):
                captured['ts'] = task_settings
                raise _CtxAbort()

        monkeypatch.setattr(ec_mod, "ExecutionContext", _CaptureCtx)

        class _SM:
            def list_chat_files(self):
                return [{"name": "secret_chat"}]

            def read_chat_settings(self, name):
                return dict(chat_settings)

        ex = ex_mod.ContinuityExecutor.__new__(ex_mod.ContinuityExecutor)
        ex._voice_lock = threading.RLock()
        ex._snapshot_voice = lambda: {}
        ex._apply_voice = lambda t: None
        ex._restore_voice = lambda snap: None
        ex.system = type("S", (), {"llm_chat": type("L", (), {
            "session_manager": _SM(),
            "function_manager": None,
            "tool_engine": None})()})()
        result = {"errors": [], "responses": []}
        ex._run_foreground(dict(task), result)   # _CtxAbort lands in errors
        assert 'ts' in captured, f"ExecutionContext never built: {result['errors']}"
        return captured['ts']

    def test_private_target_chat_stamps_carrier(self, monkeypatch):
        ts = self._foreground_settings(
            monkeypatch,
            {"chat_target": "secret_chat", "name": "t", "prompt": "sunny"},
            {"private_chat": True})
        assert ts["privacy_required"] is True

    def test_public_target_chat_unstamped(self, monkeypatch):
        ts = self._foreground_settings(
            monkeypatch,
            {"chat_target": "secret_chat", "name": "t", "prompt": "sunny"},
            {})
        assert ts["privacy_required"] is False

    def test_task_carrier_not_cleared_by_public_chat(self, monkeypatch):
        """The stamp only ever sets True — a task-carried flag survives a
        public target chat."""
        ts = self._foreground_settings(
            monkeypatch,
            {"chat_target": "secret_chat", "name": "t", "prompt": "sunny",
             "privacy_required": True},
            {})
        assert ts["privacy_required"] is True

    def test_webhook_payload_branch_stamps(self, monkeypatch):
        """chat_from_payload (an EXTERNAL caller names the chat) rides the
        same stamp — the remote-reachable variant of the leak."""
        ts = self._foreground_settings(
            monkeypatch,
            {"chat_target": "secret_chat", "name": "t",
             "trigger_config": {"chat_from_payload": True}},
            {"private_chat": True, "persona": "rose", "toolset": "none"})
        assert ts["privacy_required"] is True
