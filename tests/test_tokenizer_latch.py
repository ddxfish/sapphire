"""Tokenizer failure latch (2026-08-31, the Prime boot freeze).

Invariant: a failed tokenizer load dials the network ONCE per boot. Every
count after that estimates without touching tiktoken — a dead proxy or an
offline box must never freeze the event loop through /api/status.
"""
import pytest

import core.chat.history as h


@pytest.fixture
def broken_tokenizer(monkeypatch):
    calls = []

    def boom(name):
        calls.append(name)
        raise ConnectionError("proxy: host unreachable")

    monkeypatch.setattr(h.tiktoken, "get_encoding", boom)
    monkeypatch.setattr(h, "_tokenizer", None)
    monkeypatch.setattr(h, "_tokenizer_failed", False)
    return calls


def test_one_attempt_then_latched(broken_tokenizer):
    assert h.count_tokens("hello world") == max(1, len("hello world") // 3)
    for _ in range(5):
        h.count_tokens("more text " * 50)
    assert len(broken_tokenizer) == 1  # dialed once, never again


def test_estimate_shape(broken_tokenizer):
    assert h.count_tokens("") == 0
    assert h.count_tokens("abc") == 1
    assert h.count_tokens("x" * 300) == 100


def test_recovers_with_fresh_boot_state(monkeypatch):
    class Enc:
        def encode(self, t, disallowed_special=()):
            return t.split()

    monkeypatch.setattr(h.tiktoken, "get_encoding", lambda n: Enc())
    monkeypatch.setattr(h, "_tokenizer", None)
    monkeypatch.setattr(h, "_tokenizer_failed", False)
    assert h.count_tokens("a b c") == 3
