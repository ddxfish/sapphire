"""Isolation hunt F3 (Krem's ruling 2026-11-26): schedule_task is refused in a
private chat. A task's words live in the plaintext tasks file, its first 50
characters become its name on the Schedule page and on bus events, and all of
that stays readable while the vault is locked.
"""
import importlib.util
import types
from pathlib import Path

import pytest

import core.api_fastapi as af
from core.chat.function_manager import scope_private

SECRET = "PRIVATE-WORDS remind me about the thing only this chat knows"


@pytest.fixture
def tool():
    spec = importlib.util.spec_from_file_location(
        'schedule_tool_under_test', Path(__file__).absolute().parent.parent / 'functions' / 'schedule_tool.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def system(monkeypatch):
    made = []
    scheduler = types.SimpleNamespace(
        list_tasks=lambda: [],
        create_task=lambda data: made.append(data) or {'id': 'abcdef123456', **data})
    box = types.SimpleNamespace(private=False, made=made)
    sm = types.SimpleNamespace(get_chat_settings=lambda: {'private_chat': box.private})
    monkeypatch.setattr(af, 'get_system', lambda: types.SimpleNamespace(
        continuity_scheduler=scheduler, llm_chat=types.SimpleNamespace(session_manager=sm)))
    return box


def _ask(tool):
    return tool.execute('schedule_task', {'description': SECRET, 'time': '5pm'})


def test_an_ordinary_chat_schedules_as_ever(tool, system):
    text, ok = _ask(tool)
    assert ok and 'Scheduled one-shot task' in text
    assert system.made[0]['initial_message'] == SECRET


def test_a_private_turn_is_refused_and_nothing_is_stored(tool, system):
    token = scope_private.set(True)
    try:
        text, ok = _ask(tool)
    finally:
        scope_private.reset(token)
    assert not ok and system.made == []
    assert 'private' in text and 'Schedule' in text, "words she can act on"
    assert SECRET not in text


def test_a_private_chat_is_refused_when_only_its_setting_says_so(tool, system):
    """A lane that did not set the turn's flag is still judged on the chat."""
    system.private = True
    text, ok = _ask(tool)
    assert not ok and system.made == []
