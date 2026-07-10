"""Golden-master tests for chat storage — the rowify gate.

Written 2026-07-09 against the CURRENT blob storage, per
tmp/chat-storage-rowify-plan.md ("STEP 1 — golden-master tests"). These pin
the storage invariants that must survive the blob → row-per-message migration:

  T1 [gates rowify build-step 1] — dual-read identity over the full
     message-shape census (plain user, multimodal user, assistant with
     tool_calls + thinking + metadata + persona, tool result with
     tool_inputs, legacy pair). A fresh manager instance must read back
     EXACTLY what was written — order, every field, timestamps.
  T2 [gates build-step 3] — after a COMPLETED tool cycle, no persisted
     message carries thinking_raw. (Mid-cycle blob persistence of
     thinking_raw is intentionally NOT pinned — rowify correction #3
     stops persisting it entirely; only the completed-cycle invariant
     holds in both worlds.)
  T3 [gates build-step 3] — tool-image prune trusts the AUTHORITATIVE
     message store. Post-rowify, a rows-chat's live IDs must come from
     chat_messages, not the stale/empty blob (rowify correction #5). If
     prune ever reads the wrong store, the live image dies and this test
     screams.
  T4 [gates build-step 3] — raw `current_chat.messages = ...` assignment
     (the /api/history/import idiom, routes/chat.py) followed by save
     persists EXACTLY: grow, shrink, then append with no duplicated or
     resurrected rows (the watermark-desync failure mode).

These must be GREEN on today's blob code and stay green through every
rowify commit. Do NOT weaken an assertion to make a rowify commit pass —
that is the alarm firing.
"""
import pytest
from unittest.mock import patch


TEST_DEFAULTS = {"prompt": "default"}


@pytest.fixture
def chat_env(tmp_path, monkeypatch):
    """Yield a factory for ChatSessionManager instances on a shared temp DB.

    Hermetic: defaults functions patched (per the history.py __getattr__
    docstring: patch get_system_defaults, not the SYSTEM_DEFAULTS shim),
    privacy mode forced off so _save_current_chat never silently skips.
    """
    import core.privacy as privacy
    monkeypatch.setattr(privacy, "is_privacy_mode", lambda: False, raising=False)

    with patch("core.chat.history.get_system_defaults",
               side_effect=lambda: dict(TEST_DEFAULTS)), \
         patch("core.chat.history.get_user_defaults",
               side_effect=lambda: dict(TEST_DEFAULTS)):
        from core.chat.history import ChatSessionManager

        def make():
            return ChatSessionManager(history_dir=str(tmp_path))

        yield make


def _build_census_chat(mgr):
    """Write the full message-shape census through the public write API."""
    # Deterministic persona for the census (wrappers stamp it on messages).
    mgr.update_chat_settings({"persona": "goldenpersona"})

    # 1. Plain-string user turn + assistant with thinking/metadata.
    mgr.add_user_message("plain user text")
    mgr.add_assistant_final(
        "plain reply",
        thinking="I thought about it",
        metadata={"provider": "testprov", "tokens": 42},
    )

    # 2. Multimodal user content (list-shaped content).
    mgr.add_user_message([
        {"type": "text", "text": "look at this"},
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAAA"}},
    ])

    # 3. Full tool cycle: tool_calls + thinking + thinking_raw + metadata,
    #    tool result with name/tool_call_id/tool_inputs, then final.
    mgr.add_assistant_with_tool_calls(
        "calling a tool",
        [{"id": "call_gold1", "type": "function",
          "function": {"name": "gold_tool", "arguments": '{"x": 1}'}}],
        thinking="tool-turn thinking",
        thinking_raw=[{"type": "thinking", "thinking": "raw block", "signature": "sig=="}],
        metadata={"provider": "testprov"},
    )
    mgr.add_tool_result(
        "call_gold1", "gold_tool",
        "tool output with an image <<IMG::tool:live_img>> inline",
        inputs={"x": 1},
    )
    mgr.add_assistant_final("post-tool reply", thinking="final thinking")

    # 4. Legacy pair writer.
    mgr.add_message_pair("legacy user", "legacy assistant")


class TestT1DualReadIdentity:
    def test_full_shape_census_round_trips_exactly(self, chat_env):
        mgr = chat_env()
        _build_census_chat(mgr)

        snapshot = mgr.get_messages()
        assert len(snapshot) == 8, "census should produce exactly 8 messages"

        # Census sanity: the shapes we claim to pin are actually present.
        roles = [m["role"] for m in snapshot]
        assert roles == ["user", "assistant", "user", "assistant",
                         "tool", "assistant", "user", "assistant"]
        assert isinstance(snapshot[2]["content"], list), "multimodal content"
        assert snapshot[3]["tool_calls"][0]["id"] == "call_gold1"
        assert snapshot[3]["metadata"] == {"provider": "testprov"}
        assert snapshot[3]["persona"] == "goldenpersona"
        assert snapshot[4]["tool_call_id"] == "call_gold1"
        assert snapshot[4]["name"] == "gold_tool"
        assert snapshot[4]["tool_inputs"] == {"x": 1}
        assert snapshot[1]["thinking"] == "I thought about it"
        assert all("timestamp" in m for m in snapshot)

        # THE GATE: a fresh instance (fresh _load_chat) reads back the exact
        # same list — order, every field, timestamps, nothing added or lost.
        mgr2 = chat_env()
        reloaded = mgr2.get_messages()
        assert reloaded == snapshot

    def test_reload_is_stable_across_a_second_write_cycle(self, chat_env):
        """Write → reload → write more → reload: no drift, no duplication."""
        mgr = chat_env()
        _build_census_chat(mgr)

        mgr2 = chat_env()
        mgr2.add_user_message("second wave")
        mgr2.add_assistant_final("second reply")
        expected = mgr2.get_messages()

        mgr3 = chat_env()
        assert mgr3.get_messages() == expected
        assert len(expected) == 10


class TestT2ThinkingRawNotPersisted:
    def test_completed_tool_cycle_leaves_no_thinking_raw_on_disk(self, chat_env):
        mgr = chat_env()
        mgr.add_user_message("do a tool thing")
        mgr.add_assistant_with_tool_calls(
            "",
            [{"id": "call_t2", "type": "function",
              "function": {"name": "t2_tool", "arguments": "{}"}}],
            thinking="visible thinking",
            thinking_raw=[{"type": "thinking", "thinking": "raw", "signature": "s=="}],
        )
        mgr.add_tool_result("call_t2", "t2_tool", "result")
        mgr.add_assistant_final("done")

        # In memory: cycle completion stripped it.
        assert all("thinking_raw" not in m for m in mgr.get_messages())

        # On disk: nothing persisted carries it (blob today; rows tomorrow —
        # rowify correction #3 makes this structural).
        mgr2 = chat_env()
        persisted = mgr2.get_messages()
        assert len(persisted) == 4
        assert all("thinking_raw" not in m for m in persisted)
        # The display-facing `thinking` field DOES survive.
        assert persisted[1]["thinking"] == "visible thinking"


class TestT3PruneTrustsAuthoritativeStore:
    def test_live_image_survives_prune_orphan_dies(self, chat_env):
        mgr = chat_env()
        mgr.add_user_message("make an image")
        mgr.add_assistant_with_tool_calls(
            "",
            [{"id": "call_img", "type": "function",
              "function": {"name": "image_gen", "arguments": "{}"}}],
        )
        mgr.add_tool_result(
            "call_img", "image_gen", "made it <<IMG::tool:live_img>> here"
        )
        mgr.add_assistant_final("there you go")

        assert mgr.save_tool_image("live_img", b"\x89LIVE", "image/png")
        assert mgr.save_tool_image("orphan_img", b"\x89ORPH", "image/png")

        deleted = mgr._prune_orphaned_tool_images("default")

        # Orphan pruned, live image UNTOUCHED. Post-rowify this is the
        # single most likely silent-data-loss regression: live IDs read
        # from a stale/empty blob instead of the rows store would kill
        # live_img — and fail here.
        assert deleted == 1
        assert mgr.get_tool_image("live_img") is not None
        assert mgr.get_tool_image("orphan_img") is None

    def test_prune_after_message_removal_only_kills_dereferenced(self, chat_env):
        """Remove the turn that referenced the image, prune → image goes;
        an image referenced by a SURVIVING message stays."""
        mgr = chat_env()
        mgr.add_user_message("first image")
        mgr.add_assistant_final("kept <<IMG::tool:img_kept>> reply")
        mgr.add_user_message("second image")
        mgr.add_assistant_final("doomed <<IMG::tool:img_doomed>> reply")
        mgr.save_tool_image("img_kept", b"K", "image/png")
        mgr.save_tool_image("img_doomed", b"D", "image/png")

        # The removal wrapper saves AND prunes internally (history.py
        # remove_last_messages → _save_current_chat → prune). Pin the
        # OUTCOME, not which call pruned: dereferenced image gone,
        # still-referenced image alive.
        assert mgr.remove_last_messages(2)

        assert mgr.get_tool_image("img_kept") is not None
        assert mgr.get_tool_image("img_doomed") is None
        # And a re-prune is a no-op — nothing live gets collected twice.
        assert mgr._prune_orphaned_tool_images("default") == 0
        assert mgr.get_tool_image("img_kept") is not None


class TestT4ImportPersistsExactly:
    IMPORTED = [
        {"role": "user", "content": "imported 1", "timestamp": "2026-01-01T00:00:00"},
        {"role": "assistant", "content": "imported 2", "timestamp": "2026-01-01T00:00:01"},
        {"role": "user", "content": "imported 3", "timestamp": "2026-01-01T00:00:02"},
    ]

    def test_grow_shrink_then_append_no_dup_no_resurrection(self, chat_env):
        mgr = chat_env()
        mgr.add_user_message("pre-import 1")
        mgr.add_assistant_final("pre-import 2")

        # GROW: import 3 over 2 (the /api/history/import idiom — raw
        # assignment bypassing every mutation method, then save).
        mgr.current_chat.messages = [dict(m) for m in self.IMPORTED]
        mgr._save_current_chat()
        assert chat_env().get_messages() == self.IMPORTED

        # SHRINK: import 1 over 3. Post-rowify, a stale watermark (3) above
        # the new length (1) is the classic dup/lost-rows failure — leftover
        # rows or a skipped write both fail the exact-equality here.
        short = [{"role": "user", "content": "only survivor",
                  "timestamp": "2026-02-02T00:00:00"}]
        mgr.current_chat.messages = [dict(m) for m in short]
        mgr._save_current_chat()
        assert chat_env().get_messages() == short

        # APPEND after import: exactly one new message lands after the
        # imported list — nothing duplicated, nothing resurrected.
        mgr.add_user_message("after import")
        final = chat_env().get_messages()
        assert len(final) == 2
        assert final[0] == short[0]
        assert final[1]["role"] == "user"
        assert final[1]["content"] == "after import"
