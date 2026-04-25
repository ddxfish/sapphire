"""Regression for 2026-04-25 user report:

User pasted an image into a chat with a Qwen vision model on llama.cpp,
got a blank response, tried to delete the message → 404 'User message
not found'. Restart didn't help — message persisted but couldn't be
removed.

Root cause: ChatStore.remove_from_user_message did a direct equality
check `msg["content"] == user_content`. For multimodal messages,
content is a list of parts (text + image_url blocks). List never
equals string → match always failed → permanent stuck message.

Fix: extract text from list-shaped content before comparing.
"""
import threading


def _make_store():
    """Build a ChatStore with just enough plumbing to exercise
    remove_from_user_message without spinning the full DB."""
    from core.chat.history import ConversationHistory
    store = ConversationHistory.__new__(ConversationHistory)
    store._lock = threading.RLock()
    store.messages = []
    return store


def test_remove_text_only_user_message():
    """Baseline: text-only message still deletable (no regression)."""
    store = _make_store()
    store.messages = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ]
    assert store.remove_from_user_message("hello") is True
    assert store.messages == []


def test_remove_image_user_message_text_match():
    """The fix: an image-bearing message with content as list-of-parts
    must match when caller passes the text portion as user_content.
    This was the user's original failure path."""
    store = _make_store()
    image_message = {
        "role": "user",
        "content": [
            {"type": "text", "text": "I have an old antique boot. Can you tell me something?"},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,/9j/..."}},
        ],
    }
    store.messages = [
        image_message,
        {"role": "assistant", "content": "[blank response]"},
    ]
    result = store.remove_from_user_message(
        "I have an old antique boot. Can you tell me something?"
    )
    assert result is True, "Image-bearing user message must be deletable by its text content"
    assert store.messages == []


def test_remove_multipart_with_multiple_text_blocks():
    """Edge case: multipart with several text blocks — joined with \\n
    for matching. (Rare but possible if the frontend splits user text.)"""
    store = _make_store()
    store.messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "line one"},
                {"type": "text", "text": "line two"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}},
            ],
        },
    ]
    assert store.remove_from_user_message("line one\nline two") is True
    assert store.messages == []


def test_remove_returns_false_when_no_match():
    """If text doesn't match, we still return False (and log) — same
    as before. The fix only added a shape-tolerant comparison, not
    a behavior change for genuine mismatches."""
    store = _make_store()
    store.messages = [
        {"role": "user", "content": "alpha"},
        {"role": "user", "content": [{"type": "text", "text": "beta"}]},
    ]
    assert store.remove_from_user_message("gamma") is False
    assert len(store.messages) == 2  # unchanged


def test_remove_with_empty_content_list_does_not_crash():
    """Defensive: a list with no text parts shouldn't crash the loop
    looking for matches. (Unlikely shape but not impossible.)"""
    store = _make_store()
    store.messages = [
        {"role": "user", "content": []},
        {"role": "user", "content": "real message"},
    ]
    # Empty-content user msg should NOT match ""-or-anything; the real
    # message at index 1 should match.
    assert store.remove_from_user_message("real message") is True
    assert len(store.messages) == 1  # the empty-list one survives
    assert store.messages[0]["content"] == []


def test_remove_handles_dict_content_gracefully():
    """Defensive: if a future provider stores content as a dict
    (single-block shape some experimental APIs use), don't crash."""
    store = _make_store()
    store.messages = [
        {"role": "user", "content": {"type": "text", "text": "weird shape"}},
        {"role": "user", "content": "normal"},
    ]
    # We don't claim to MATCH the dict shape — that's not the bug we
    # fixed — but the loop should not raise on it.
    assert store.remove_from_user_message("normal") is True
    assert len(store.messages) == 1
