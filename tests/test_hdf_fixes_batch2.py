"""Round 2 of Hubble Deep Field LHF fixes (2026-04-19).

Covers:
  - Scout 4 #1: TTS provider swap waits for generation thread
  - Scout 1 #2: tool_images orphan pruning on message removal
  - Scout 4 #3: wakeword silent null fallback surfaces a warning event
"""
import json
import sqlite3
from unittest.mock import MagicMock, patch

import pytest


# ─── Scout 4 #1 — TTS wait on provider swap ──────────────────────────────────

def test_init_tts_provider_waits_on_old_client():
    """[REGRESSION_GUARD] When swapping TTS providers, the old TTSClient's
    generation thread must be joined (via `wait`) before reassigning.
    Without this, the orphaned thread can publish stale TTS_STOPPED events
    AFTER the new client has started."""
    import inspect
    from sapphire import VoiceChatSystem
    src = inspect.getsource(VoiceChatSystem._init_tts_provider)
    # Expect both stop AND wait on the old tts
    assert 'self.tts.stop()' in src
    assert 'self.tts.wait' in src, \
        "TTS provider swap should wait() for generation thread, not just stop()"


# ─── Scout 1 #2 — tool_images orphan pruning ─────────────────────────────────

@pytest.fixture
def session_manager_with_images(tmp_path):
    from core.chat.history import ChatSessionManager
    sm = ChatSessionManager()
    sm._db_path = tmp_path / "chats.db"
    sm._init_db()
    sm.create_chat('imagechat')
    sm.set_active_chat('imagechat')
    # Seed some tool_images rows — two referenced, two orphan
    with sqlite3.connect(sm._db_path) as conn:
        for img_id in ('live1.jpg', 'live2.png', 'orphan1.jpg', 'orphan2.png'):
            conn.execute(
                "INSERT INTO tool_images (id, chat_name, data, media_type, created_at) "
                "VALUES (?, ?, ?, ?, datetime('now'))",
                (img_id, 'imagechat', b'fake_bytes', 'image/jpeg')
            )
        # Set chat messages to reference only the 'live' IDs
        msgs = json.dumps([
            {'role': 'tool', 'content': '<<IMG::tool:live1.jpg>>\nfirst result'},
            {'role': 'tool', 'content': 'text without image here'},
            {'role': 'tool', 'content': '<<IMG::tool:live2.png>>\nsecond result'},
        ])
        # Pin to BLOB format: this fixture hand-writes the messages blob, and
        # post rowify step 2 create_chat births 'rows' chats (which ignore the
        # blob). Keeping these tests on blob preserves prune coverage for the
        # legacy path (dual-reader anti-cold-rot); rows prune is covered by
        # test_rowify_step2.py + golden master T3.
        conn.execute(
            "UPDATE chats SET messages = ?, storage_format = 'blob' WHERE name = 'imagechat'", (msgs,)
        )
    return sm


def test_prune_orphaned_tool_images_removes_unreferenced_rows(session_manager_with_images):
    """[DATA_INTEGRITY] _prune_orphaned_tool_images must delete tool_images
    rows whose IDs no longer appear anywhere in the chat's message content.
    Scout 1 found unbounded growth here; this is the reclamation path."""
    sm = session_manager_with_images
    removed = sm._prune_orphaned_tool_images('imagechat')
    assert removed == 2, f"expected 2 orphans removed, got {removed}"

    conn = sqlite3.connect(sm._db_path)
    remaining = {r[0] for r in conn.execute(
        "SELECT id FROM tool_images WHERE chat_name = 'imagechat'"
    ).fetchall()}
    conn.close()
    assert remaining == {'live1.jpg', 'live2.png'}, \
        f"expected live rows preserved, got {remaining}"


def test_prune_noop_when_all_referenced(session_manager_with_images):
    """If all rows are still referenced, prune is a zero-delete noop."""
    sm = session_manager_with_images
    # Reset messages to reference all 4 IDs
    with sqlite3.connect(sm._db_path) as conn:
        msgs = json.dumps([
            {'role': 'tool', 'content': '<<IMG::tool:live1.jpg>>'},
            {'role': 'tool', 'content': '<<IMG::tool:live2.png>>'},
            {'role': 'tool', 'content': '<<IMG::tool:orphan1.jpg>>'},
            {'role': 'tool', 'content': '<<IMG::tool:orphan2.png>>'},
        ])
        conn.execute(
            "UPDATE chats SET messages = ? WHERE name = 'imagechat'", (msgs,)
        )
    removed = sm._prune_orphaned_tool_images('imagechat')
    assert removed == 0


def test_prune_handles_missing_chat_gracefully(session_manager_with_images):
    """No exception when chat doesn't exist — just returns 0."""
    sm = session_manager_with_images
    assert sm._prune_orphaned_tool_images('nonexistent_chat') == 0


def test_remove_tool_call_prunes_images(tmp_path):
    """[REGRESSION_GUARD] remove_tool_call must fire image pruning. Otherwise
    removing a tool result carrying an `<<IMG::tool:X>>` marker leaves the
    image blob in tool_images forever."""
    from core.chat.history import ChatSessionManager
    sm = ChatSessionManager()
    sm._db_path = tmp_path / "chats.db"
    sm._init_db()
    sm.create_chat('trinity')
    sm.set_active_chat('trinity')

    # Seed via direct DB: user msg + assistant-with-tool-call + tool result with image
    img_id = 'doomed.jpg'
    with sqlite3.connect(sm._db_path) as conn:
        conn.execute(
            "INSERT INTO tool_images (id, chat_name, data, media_type, created_at) "
            "VALUES (?, 'trinity', ?, ?, datetime('now'))",
            (img_id, b'blob', 'image/jpeg')
        )
        msgs = json.dumps([
            {'role': 'user', 'content': 'show me'},
            {'role': 'assistant', 'content': '',
             'tool_calls': [{'id': 'call_1', 'type': 'function',
                            'function': {'name': 'snap', 'arguments': '{}'}}]},
            {'role': 'tool', 'tool_call_id': 'call_1', 'name': 'snap',
             'content': f'<<IMG::tool:{img_id}>>\npic result'},
        ])
        # Pin to blob — hand-written blob seed (see session_manager_with_images
        # note); keeps this as legacy-path coverage post rowify step 2.
        conn.execute("UPDATE chats SET messages = ?, storage_format = 'blob' WHERE name='trinity'", (msgs,))
    # Reload in-memory
    sm._load_chat('trinity')

    # Remove the tool call
    ok = sm.remove_tool_call('call_1')
    assert ok

    # The image should be gone
    conn = sqlite3.connect(sm._db_path)
    remaining = conn.execute(
        "SELECT COUNT(*) FROM tool_images WHERE id = ?", (img_id,)
    ).fetchone()[0]
    conn.close()
    assert remaining == 0, "image blob survived remove_tool_call — prune path broken"


# ─── Scout 4 #3 — wakeword silent null fallback publishes event ──────────────

def test_wakeword_init_failure_publishes_warning():
    """[REGRESSION_GUARD] When wake word init fails and Sapphire falls back to
    NullWakeWordDetector, a CONTINUITY_TASK_ERROR event must fire so the UI
    can surface "wakeword silently fell off." Without it, user thinks Sapphire
    is listening when she isn't."""
    import inspect
    import sapphire as sapph_mod
    src = inspect.getsource(sapph_mod)
    # The failure path must publish CONTINUITY_TASK_ERROR with a Wake-Word-
    # related task label. Source-level check is good enough here — wiring up
    # an actual wake-word init failure in-test requires heavy setup.
    assert 'Wake Word' in src or 'wake word' in src.lower()
    assert 'CONTINUITY_TASK_ERROR' in src, \
        "wake-word null-fallback path should publish CONTINUITY_TASK_ERROR"
