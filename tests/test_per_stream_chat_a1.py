"""A1 — per-stream chat session (concurrent conversation in any chat).

Two families:
  OFF-path invariant — with no stream override, ChatSessionManager must behave
    EXACTLY as before: all reads/writes hit the active chat singleton. These must
    pass both before AND after the A1 change (byte-identical guarantee).
  A1 behavior — with a per-context stream override set, reads/writes route to the
    OVERRIDE's chat, and the active chat is left completely untouched (the
    concurrency isolation that lets a phone call run in its own chat while the web
    UI sits on another).
"""
import pytest

from core.chat.history import ChatSessionManager
from core.chat import stream_brain


@pytest.fixture
def sm(tmp_path):
    m = ChatSessionManager(history_dir=str(tmp_path))
    # 'default' is the active chat on a fresh manager
    m.create_chat("phone")          # a second, non-active chat
    yield m
    # never leak an override between tests
    stream_brain.set_override(None)


@pytest.fixture(autouse=True)
def _clean_override():
    stream_brain.set_override(None)
    yield
    stream_brain.set_override(None)


def _texts(messages):
    return " ".join(str(m.get("content", m)) for m in messages)


# ─────────────────────────── OFF-path invariant ───────────────────────────

def test_get_chat_settings_no_override_is_active(sm):
    assert sm.get_chat_settings() == sm.current_settings


def test_effective_chat_no_override_is_singleton(sm):
    assert sm._effective_chat() is sm.current_chat
    assert sm._effective_chat_name() == sm.active_chat_name


def test_add_user_message_no_override_hits_active(sm):
    active = sm.get_active_chat_name()
    sm.add_user_message("hello-active")
    assert "hello-active" in _texts(sm.read_chat_messages(active))
    # the non-active 'phone' chat is untouched
    assert "hello-active" not in _texts(sm.read_chat_messages("phone"))


def test_add_assistant_final_no_override_hits_active(sm):
    active = sm.get_active_chat_name()
    sm.add_user_message("u1")
    sm.add_assistant_final("a1")
    joined = _texts(sm.read_chat_messages(active))
    assert "u1" in joined and "a1" in joined


def test_get_messages_for_llm_no_override_reads_active(sm):
    sm.add_user_message("ctx-active")
    msgs = sm.get_messages_for_llm(1000)
    assert "ctx-active" in _texts(msgs)


# ─────────────────────────── A1 behavior ───────────────────────────

def test_override_settings_win(sm):
    sm.set_named_chat_settings("phone", {"llm_primary": "claude", "persona": "phone_persona"})
    sess = sm.make_stream_session("phone")
    tok = stream_brain.set_override(sess)
    try:
        s = sm.get_chat_settings()
        assert s.get("llm_primary") == "claude"
        assert s.get("persona") == "phone_persona"
    finally:
        stream_brain.reset_override(tok)
    # after reset, back to active
    assert sm.get_chat_settings() == sm.current_settings


def test_effective_chat_follows_override(sm):
    sess = sm.make_stream_session("phone")
    tok = stream_brain.set_override(sess)
    try:
        assert sm._effective_chat() is sess["history"]
        assert sm._effective_chat_name() == "phone"
    finally:
        stream_brain.reset_override(tok)


def test_writes_route_to_override_chat_active_untouched(sm):
    active = sm.get_active_chat_name()
    sess = sm.make_stream_session("phone")
    tok = stream_brain.set_override(sess)
    try:
        sm.add_user_message("from-phone")
        sm.add_assistant_final("phone-reply")
    finally:
        stream_brain.reset_override(tok)

    phone = _texts(sm.read_chat_messages("phone"))
    assert "from-phone" in phone and "phone-reply" in phone
    # THE isolation guarantee: the active/default chat saw none of it
    assert "from-phone" not in _texts(sm.read_chat_messages(active))
    assert "phone-reply" not in _texts(sm.read_chat_messages(active))


def test_get_messages_for_llm_reads_override_history(sm):
    sess = sm.make_stream_session("phone")
    tok = stream_brain.set_override(sess)
    try:
        sm.add_user_message("phone-ctx")
        msgs = sm.get_messages_for_llm(1000)
        assert "phone-ctx" in _texts(msgs)
    finally:
        stream_brain.reset_override(tok)


def test_make_stream_session_seeds_existing_history(sm):
    # seed 'phone' with prior turns via a first override write
    sess1 = sm.make_stream_session("phone")
    tok = stream_brain.set_override(sess1)
    try:
        sm.add_user_message("earlier-call")
        sm.add_assistant_final("earlier-reply")
    finally:
        stream_brain.reset_override(tok)
    # a NEW session for the same chat must load that prior history
    sess2 = sm.make_stream_session("phone")
    assert "earlier-call" in _texts(sess2["history"].messages)


def test_interleaved_active_and_override_no_bleed(sm):
    """Simulate concurrency: write to active, then to override chat, then active
    again — each chat accumulates only its own, none bleeds."""
    active = sm.get_active_chat_name()
    sm.add_user_message("A1")                      # active
    sess = sm.make_stream_session("phone")
    tok = stream_brain.set_override(sess)
    try:
        sm.add_user_message("B1")                  # phone
        sm.add_assistant_final("B2")               # phone
    finally:
        stream_brain.reset_override(tok)
    sm.add_user_message("A2")                       # active again

    active_txt = _texts(sm.read_chat_messages(active))
    phone_txt = _texts(sm.read_chat_messages("phone"))
    assert "A1" in active_txt and "A2" in active_txt
    assert "B1" not in active_txt and "B2" not in active_txt
    assert "B1" in phone_txt and "B2" in phone_txt
    assert "A1" not in phone_txt and "A2" not in phone_txt


def test_override_reset_restores_active_writes(sm):
    active = sm.get_active_chat_name()
    sess = sm.make_stream_session("phone")
    tok = stream_brain.set_override(sess)
    stream_brain.reset_override(tok)
    # immediately after reset, a write must go to the active chat again
    sm.add_user_message("back-to-active")
    assert "back-to-active" in _texts(sm.read_chat_messages(active))


# ─────────────── A1 write-routing (2026-07-09): clear / settings / tool-cycle ───────────────
# These three methods READ the override but historically WROTE the active chat.
# reset_chat wiped the operator's web chat; switch_model moved the wrong chat's
# provider; a shared _in_tool_cycle bool dropped Claude thinking_raw mid-cycle (400).

def test_clear_routes_to_override_chat_active_untouched(sm):
    """reset_chat from a phone stream clears the CALL's chat, never the operator's
    active web chat (the wrong-chat-wipe: data loss on a bystander chat)."""
    active = sm.get_active_chat_name()
    sm.add_user_message("active-keepsafe")             # active/default
    sess = sm.make_stream_session("phone")
    tok = stream_brain.set_override(sess)
    try:
        sm.add_user_message("phone-msg")
        assert "phone-msg" in _texts(sm.read_chat_messages("phone"))
        sm.clear()                                     # AI calls reset_chat mid-call
    finally:
        stream_brain.reset_override(tok)
    assert sm.read_chat_messages("phone") == []        # the call's chat emptied
    assert "active-keepsafe" in _texts(sm.read_chat_messages(active))  # web chat intact


def test_clear_no_override_clears_active(sm):
    """OFF-path: clear with no override empties the active chat (unchanged)."""
    active = sm.get_active_chat_name()
    sm.add_user_message("doomed")
    sm.clear()
    assert sm.read_chat_messages(active) == []


def test_update_chat_settings_routes_to_override_active_untouched(sm):
    """switch_model / switch_toolset / set_voice from a phone stream write the CALL's
    settings, not the operator's active chat (the wrong-chat model/privacy switch)."""
    active = sm.get_active_chat_name()
    active_model_before = sm.current_settings.get("llm_primary")
    sess = sm.make_stream_session("phone")
    tok = stream_brain.set_override(sess)
    try:
        assert sm.update_chat_settings({"llm_primary": "local_only", "toolset": "none"})
        # in-turn snapshot reflects the change immediately (same-turn reads consistent)
        assert sm.get_chat_settings().get("llm_primary") == "local_only"
    finally:
        stream_brain.reset_override(tok)
    assert sm.read_chat_settings("phone")["llm_primary"] == "local_only"
    assert sm.read_chat_settings("phone")["toolset"] == "none"
    # the active/default chat kept its provider — no silent cross-chat switch
    assert sm.current_settings.get("llm_primary") == active_model_before
    assert sm.read_chat_settings(active)["llm_primary"] == active_model_before


def test_update_chat_settings_no_override_writes_active(sm):
    """OFF-path: settings write with no override hits the active chat (unchanged)."""
    active = sm.get_active_chat_name()
    assert sm.update_chat_settings({"llm_primary": "web_pick"})
    assert sm.current_settings["llm_primary"] == "web_pick"
    assert sm.read_chat_settings(active)["llm_primary"] == "web_pick"


def test_in_tool_cycle_is_per_chat_no_cross_stream_leak(sm):
    """The tool-cycle flag lives on each chat's history, so a web turn completing
    can't clear a concurrent phone turn's flag mid-cycle (the provider-400 root)."""
    sess = sm.make_stream_session("phone")
    tok = stream_brain.set_override(sess)
    try:
        sm._in_tool_cycle = True
        assert sm._in_tool_cycle is True               # reads the phone chat's flag
    finally:
        stream_brain.reset_override(tok)
    assert sm._in_tool_cycle is False                  # active chat's own flag, independent
    assert sess["history"]._in_tool_cycle is True      # phone's flag was not clobbered


def test_in_tool_cycle_active_and_override_independent(sm):
    """A flag set on the active chat does not bleed into an override chat's view."""
    sm._in_tool_cycle = True                           # active/default enters a cycle
    sess = sm.make_stream_session("phone")
    tok = stream_brain.set_override(sess)
    try:
        assert sm._in_tool_cycle is False              # phone chat: fresh, own flag
        sm._in_tool_cycle = True
    finally:
        stream_brain.reset_override(tok)
    assert sm.current_chat._in_tool_cycle is True      # active flag survived intact
    assert "back-to-active" not in _texts(sm.read_chat_messages("phone"))


# ─────────────── Incomplete override (missing 'history') ───────────────
# The continuity executor shipped its override without a 'history' key until
# 2026-08-17. update_chat_settings then fell through to the active branch and
# _save_current_chat persisted the ACTIVE chat's settings+messages under the
# override NAME — a cross-chat clobber. These pin the fix: settings writes
# route by the override's chat NAME, and a name-diverted save with no history
# object is dropped loudly, never written.

def test_incomplete_override_settings_write_routes_to_named_chat(sm):
    active = sm.get_active_chat_name()
    tok = stream_brain.set_override({"chat": "phone", "settings": {}})
    try:
        assert sm.update_chat_settings({"motion": "fireflies"}) is True
    finally:
        stream_brain.reset_override(tok)
    assert sm.read_chat_settings("phone").get("motion") == "fireflies"
    assert "motion" not in sm.current_settings
    assert (sm.read_chat_settings(active) or {}).get("motion") is None


def test_incomplete_override_save_never_clobbers_named_chat(sm):
    sm.set_named_chat_settings("phone", {"persona": "phone_persona"})
    sm.add_user_message("active-only-text")        # no override: hits active
    tok = stream_brain.set_override({"chat": "phone", "settings": {}})
    try:
        sm._save_current_chat()                    # invariant breach → dropped
    finally:
        stream_brain.reset_override(tok)
    assert sm.read_chat_settings("phone").get("persona") == "phone_persona"
    assert "active-only-text" not in _texts(sm.read_chat_messages("phone"))
