"""The sidebar's "Last Ghost Sent": in-memory per-chat stash + its route."""
import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from core import ghost_messages as gm
from core.hooks import hook_runner

PRIVATE = {"vaulted"}


@pytest.fixture(autouse=True)
def clean():
    resolver = MagicMock(side_effect=lambda name=None: (name, name in PRIVATE))
    with patch.object(hook_runner, "_privacy_resolver", resolver):
        gm.forget_all()
        yield
        gm.forget_all()


def test_remember_and_read_back():
    gm.remember("kitchen", "[System context...]\n- voice-tone: calm")
    at, text = gm.last("kitchen")
    assert text.endswith("calm") and at > 0


def test_no_ghost_turn_is_honest_empty():
    gm.remember("kitchen", "old")
    gm.remember("kitchen", None)
    assert gm.last("kitchen")[1] == ""


def test_private_chat_never_kept():
    gm.remember("vaulted", "secret")
    assert gm.last("vaulted") is None
    assert "vaulted" not in gm._last


def test_chat_going_private_hides_the_old_one():
    gm.remember("later", "x")
    PRIVATE.add("later")
    try:
        assert gm.last("later") is None
    finally:
        PRIVATE.discard("later")


def test_resolver_error_fails_closed():
    with patch.object(hook_runner, "_privacy_resolver", MagicMock(side_effect=RuntimeError)):
        gm.remember("x", "y")
    assert "x" not in gm._last


def test_rename_moves_delete_forgets_lock_wipes():
    gm.remember("a", "one")
    gm.forget("a", "b")
    assert gm.last("a") is None and gm.last("b")[1] == "one"
    gm.forget("b")
    assert gm.last("b") is None
    gm.remember("c", "two")
    gm.forget_all()
    assert gm.last("c") is None


def test_history_rename_carries_it():
    from core.chat.history import _chat_moved
    gm.remember("old", "z")
    _chat_moved("old", "new")
    assert gm.last("new")[1] == "z"


def _route(chat, settings):
    from core.routes.chat import chat_ghost_last
    sm = SimpleNamespace(get_settings_for=lambda n: settings)
    system = SimpleNamespace(llm_chat=SimpleNamespace(session_manager=sm))
    return asyncio.run(chat_ghost_last(chat, None, system))


def test_route_shapes():
    assert _route("kitchen", {})["ghost"] is None                       # no turn since boot
    gm.remember("kitchen", "g")
    r = _route("kitchen", {})
    assert r["ghost"] == "g" and r["at"] and r["private"] is False
    assert _route("kitchen", {"private_chat": True}) == {
        "chat": "kitchen", "private": True, "ghost": None, "at": None}
    with pytest.raises(HTTPException) as e:
        _route("nope", None)
    assert e.value.status_code == 404
