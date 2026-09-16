"""[REGRESSION_GUARD] A sealed vault prompt name never lands on a chat (2026-09-15).

Prime: after a vault lock the Default chat's sidebar showed a vault prompt's
name, get_self_info agreed (it reports the STORED setting), prompt_view ran
the fallback (the LIVE prompt), and a hard refresh changed nothing (server-
held). The sidebar's full-snapshot save had shipped the evicted private
chat's paint onto the public landing chat: chat-select had already moved to
the landing chat while the seven-fetch repaint hadn't, so the target NAME
was right (R5 passed) and the VALUES were another chat's.

Three fixes, three guards:
  A  client saves are per-key patches captured at the gesture — source
     tripwires on views/chat.js
  Y  the store's two settings funnels + the PUT/persona routes refuse a
     prompt that names a sealed vault prompt (refs index = the one sealed-
     state oracle; `sealed_prompt_name` in core/chat/history.py)
  C  a sealed name never renders — placeholder label in the three dangling-
     name synthesizers (chat sidebar, persona editor, trigger editor)
"""
from pathlib import Path
from unittest.mock import patch

import pytest

STATIC = Path(__file__).resolve().parent.parent / "interfaces" / "web" / "static"
TEST_DEFAULTS = {"prompt": "default"}
SEALED = {"sapph_home": "preset"}


def _src(rel):
    return (STATIC / rel).read_text(encoding="utf-8")


def _between(src, start, end):
    i = src.index(start)
    return src[i:src.index(end, i)]


@pytest.fixture
def sm(tmp_path):
    with patch("core.chat.history.get_system_defaults",
               side_effect=lambda: dict(TEST_DEFAULTS)), \
         patch("core.chat.history.get_user_defaults",
               side_effect=lambda: dict(TEST_DEFAULTS)):
        from core.chat.history import ChatSessionManager
        m = ChatSessionManager(history_dir=str(tmp_path))
        m.create_chat("pub")
        m.create_chat("other")
        m.set_active_chat("pub")
        yield m


def _seal(monkeypatch, sealed=True, refs=None):
    import core.chat.history as hist
    from core import prompt_vault
    monkeypatch.setattr(hist, "_vault_sealed", lambda: sealed)
    monkeypatch.setattr(prompt_vault, "refs_names", lambda: dict(SEALED if refs is None else refs))


# ── Y: the one rule ──────────────────────────────────────────────────────────

class TestSealedPromptName:
    def test_sealed_and_referenced(self, monkeypatch):
        from core.chat.history import sealed_prompt_name
        _seal(monkeypatch, True)
        assert sealed_prompt_name("sapph_home") is True

    def test_unsealed_never(self, monkeypatch):
        from core.chat.history import sealed_prompt_name
        _seal(monkeypatch, False)
        assert sealed_prompt_name("sapph_home") is False

    def test_unreferenced_or_empty_never(self, monkeypatch):
        from core.chat.history import sealed_prompt_name
        _seal(monkeypatch, True)
        assert sealed_prompt_name("sapphire") is False
        assert sealed_prompt_name("ghost_unknown") is False   # Sapph-not-Rose class stays allowed
        assert sealed_prompt_name(None) is False
        assert sealed_prompt_name("") is False

    def test_unreadable_index_answers_false(self, monkeypatch):
        """Refusing every prompt write on a glitch would be the bigger failure."""
        import core.chat.history as hist
        from core import prompt_vault
        monkeypatch.setattr(hist, "_vault_sealed", lambda: True)

        def boom():
            raise RuntimeError("index unreadable")
        monkeypatch.setattr(prompt_vault, "refs_names", boom)
        assert hist.sealed_prompt_name("sapph_home") is False


# ── Y: both store funnels refuse ─────────────────────────────────────────────

class TestStoreFunnels:
    def test_active_funnel_refuses_sealed_name(self, sm, monkeypatch):
        _seal(monkeypatch, True)
        assert sm.update_chat_settings({"prompt": "sapph_home"}) is False
        assert sm.get_chat_settings()["prompt"] == "default"
        assert sm.read_chat_settings("pub")["prompt"] == "default"

    def test_active_funnel_refuses_even_inside_a_wider_patch(self, sm, monkeypatch):
        """The Prime shape: one stale prompt riding a larger payload. Nothing
        from that payload lands — a partial merge would still be a foreign
        write."""
        _seal(monkeypatch, True)
        assert sm.update_chat_settings({"prompt": "sapph_home", "pitch": 1.3}) is False
        assert sm.get_chat_settings().get("pitch") != 1.3

    def test_named_funnel_refuses_sealed_name(self, sm, monkeypatch):
        _seal(monkeypatch, True)
        assert sm.set_named_chat_settings("other", {"prompt": "sapph_home"}) is False
        assert sm.read_chat_settings("other")["prompt"] == "default"

    def test_unsealed_stamp_allowed(self, sm, monkeypatch):
        _seal(monkeypatch, False)
        assert sm.update_chat_settings({"prompt": "sapph_home"}) is True
        assert sm.get_chat_settings()["prompt"] == "sapph_home"

    def test_sealed_non_vault_names_and_other_keys_flow(self, sm, monkeypatch):
        _seal(monkeypatch, True)
        assert sm.update_chat_settings({"prompt": "sapphire"}) is True
        assert sm.update_chat_settings({"pitch": 1.1}) is True
        assert sm.set_named_chat_settings("other", {"prompt": "sapphire"}) is True
        assert sm.get_chat_settings()["prompt"] == "sapphire"


# ── Y: the routes answer 409, honestly and name-free ─────────────────────────

class TestRoutes:
    def test_settings_put_409(self, client, mock_system, monkeypatch):
        c, csrf = client
        _seal(monkeypatch, True)
        sm = mock_system.llm_chat.session_manager
        sm.get_active_chat_name.return_value = "default"
        sm.active_chat_name = "default"
        r = c.put("/api/chats/default/settings",
                  json={"settings": {"prompt": "sapph_home", "llm_primary": "lmstudio"}},
                  headers={"X-CSRF-Token": csrf})
        assert r.status_code == 409, r.text
        assert "vault" in r.json()["detail"]
        assert "sapph_home" not in r.text
        sm.update_chat_settings.assert_not_called()
        sm.set_named_chat_settings.assert_not_called()

    def test_settings_put_non_vault_name_passes(self, client, mock_system, monkeypatch):
        c, csrf = client
        _seal(monkeypatch, True)
        sm = mock_system.llm_chat.session_manager
        sm.get_active_chat_name.return_value = "default"
        sm.active_chat_name = "default"
        sm.set_named_chat_settings.return_value = True
        r = c.put("/api/chats/other/settings",
                  json={"settings": {"prompt": "sapphire"}},
                  headers={"X-CSRF-Token": csrf})
        assert r.status_code == 200, r.text

    def test_persona_load_409(self, client, mock_system, monkeypatch):
        c, csrf = client
        _seal(monkeypatch, True)
        from core.personas import persona_manager
        monkeypatch.setattr(persona_manager, "get",
                            lambda name: {"name": name, "settings": {"prompt": "sapph_home"}})
        sm = mock_system.llm_chat.session_manager
        r = c.post("/api/personas/nova/load", headers={"X-CSRF-Token": csrf})
        assert r.status_code == 409, r.text
        assert "vault" in r.json()["detail"]
        sm.update_chat_settings.assert_not_called()


# ── A: the sidebar ships only what the gesture touched ───────────────────────

class TestSidebarPerKeySave:
    def test_every_control_maps_to_its_key(self):
        src = _src("views/chat.js")
        table = _between(src, "const _KEY_BY_ID = {", "};")
        for cid, key in [("sb-prompt", "prompt"), ("sb-toolset", "toolset"),
                         ("sb-spice-set", "spice_set"), ("sb-voice", "voice"),
                         ("sb-pitch", "pitch"), ("sb-speed", "speed"),
                         ("sb-spice-toggle", "spice_enabled"), ("sb-spice-turns", "spice_turns"),
                         ("sb-datetime-toggle", "inject_datetime"),
                         ("sb-custom-context", "custom_context"), ("sb-ghost-context", "ghost_context"),
                         ("sb-llm-model", "llm_model"), ("sb-llm-model-custom", "llm_model"),
                         ("sb-rag-context", "rag_context")]:
            assert f"'{cid}': ['{key}']" in table, cid
        assert "'sb-llm-primary': ['llm_primary', 'llm_model']" in table
        assert "/^sb-(.+)-scope$/.exec(id)" in src   # scope dropdowns by id scheme
        # every save door hands the control's keys over — no bare snapshot save
        assert "debouncedSave(container, keysFor(el));" in src
        assert "debouncedSave(container, keysFor(btn));" in src
        assert "debouncedSave(container, ['voice']);" in src
        assert "debouncedSave(container);" not in src
        assert "saveSettings(container, n);" not in src

    def test_save_is_patch_only_and_never_reads_the_dom_at_fire(self):
        src = _src("views/chat.js")
        save = _between(src, "async function saveSettings(", "function collectSettings(")
        assert "patch = null)" in save
        assert "const settings = patch || {};" in save
        assert "if (!Object.keys(settings).length) return;" in save
        assert "collectSettings(container)" not in save   # values come from the captured patch
        deb = _between(src, "function debouncedSave(container, keys = []) {", "/** Cancel any pending")
        assert "Object.assign(pendingPatch, pickSettings(container, keys));" in deb
        assert "saveSettings(container, n, patch);" in deb
        assert "if (!keys.length) return;" in deb
        flush = _between(src, "export async function flushPendingSave() {", "function openAppearanceModal")
        assert "const patch = pendingPatch;" in flush and "saveSettings(container, chatName, patch);" in flush
        cancel = _between(src, "export function cancelPendingSave() {", "/** Flush any pending")
        assert "pendingPatch = {};" in cancel

    def test_save_failure_is_never_silent(self):
        src = _src("views/chat.js")
        save = _between(src, "async function saveSettings(", "function collectSettings(")
        assert "ui.showToast(e?.message || 'Setting not saved', 'error', 4000);" in save

    def test_every_collected_key_is_reachable_by_a_control(self):
        """collectSettings is the one reader; every key it knows must be ownable
        by some control, else a change there could never be saved."""
        src = _src("views/chat.js")
        collect = _between(src, "function collectSettings(container) {", "/** The subset of collectSettings()")
        table = _between(src, "const _KEY_BY_ID = {", "};")
        import re
        keys = set(re.findall(r"^\s{8}([a-z_]+):", collect, re.M))
        mapped = set(re.findall(r"'([a-z_]+)'", table)) - set(re.findall(r"'(sb-[a-z-]+)'", table))
        assert keys <= mapped, keys - mapped


# ── C: a sealed name never renders ───────────────────────────────────────────

class TestSealedLabel:
    LABEL = "'\\u{1F5DD} vault prompt (locked)'"

    def test_chat_sidebar(self):
        src = _src("views/chat.js")
        assert f"const SEALED_PROMPT_LABEL = {self.LABEL};" in src
        assert "opt.textContent = SEALED_PROMPT_LABEL;" in src
        assert "${cur} \\u{1F5DD} (vault)" not in src

    def test_persona_editor(self):
        src = _src("views/personas.js")
        assert f"current in vrefs ? {self.LABEL}" in src
        assert "${current} \\u{1F5DD} (vault)" not in src

    def test_trigger_editor(self):
        src = _src("shared/trigger-editor/ai-config.js")
        assert f"? {self.LABEL} : `${{_esc(t.prompt)}} (missing)`" in src
        assert "${_esc(t.prompt)} \\u{1F5DD} (vault)" not in src
