"""The log is plaintext and kept 30 days; a private chat's words are not to
be in it (live check 2026-11-26: the speech pump logged every sentence it
voiced). core/log_safe.py is what the turn's own code logs through wherever
the text could quote the turn: malformed tool arguments, a tool's exception,
a model server's error body.
"""
import logging
from unittest.mock import patch

import pytest

from core import log_safe
from core.chat import function_manager as fmod
from core.chat.function_manager import FunctionManager, scope_private, reset_scopes

SECRET = "PRIVATE-WORDS she only said in a private chat"


@pytest.fixture
def private():
    token = scope_private.set(True)
    yield
    scope_private.reset(token)


class ServerSaid(Exception):
    status_code = 400


def test_an_ordinary_turn_logs_as_it_always_did():
    assert scope_private.get() is False
    assert log_safe.words(SECRET) == repr(SECRET)
    assert log_safe.err(ValueError(SECRET)) == SECRET
    assert log_safe.exc(ValueError(SECRET)) is True


def test_a_private_turn_logs_sizes_and_kinds_only(private):
    assert SECRET not in log_safe.words(SECRET) and str(len(SECRET)) in log_safe.words(SECRET)
    said = log_safe.err(ServerSaid(f'{{"error": "bad input", "input": "{SECRET}"}}'))
    assert SECRET not in said and 'ServerSaid' in said and '400' in said


def test_a_private_turns_traceback_keeps_the_stack_and_drops_the_words(private, caplog):
    def tool():
        raise ValueError(f"could not save: {SECRET}")
    caplog.set_level(logging.ERROR, logger='t')
    try:
        tool()
    except ValueError as e:
        logging.getLogger('t').error(f"tool failed: {log_safe.err(e)}", exc_info=log_safe.exc(e))
    text = caplog.text
    assert SECRET not in text
    assert 'in tool' in text and 'ValueError' in text, "where it broke is still there"


def test_cannot_tell_means_private(monkeypatch):
    class Broken:
        def get(self):
            raise RuntimeError("no context")
    monkeypatch.setattr(fmod, 'scope_private', Broken())
    assert log_safe.private_turn() is True


def _door(monkeypatch, tool):
    with patch.object(FunctionManager, '__init__', lambda self: None):
        fm = FunctionManager()
    fm.execution_map = {'boom': tool}
    fm._is_local_map = {'boom': True}
    fm.function_modules, fm._function_module_map = {}, {}
    fm.system_instance = None
    fm.tool_history = []
    monkeypatch.setattr(fm, '_get_tool_parameters', lambda n: None)
    return fm


@pytest.mark.parametrize("is_private", [True, False])
def test_a_tools_exception_at_the_tool_door(monkeypatch, caplog, is_private):
    def boom(name, args, cfg):
        raise ValueError(f"cannot write {SECRET}")
    fm = _door(monkeypatch, boom)
    monkeypatch.setattr(fm, '_log_tool_call', lambda *a, **k: None)
    caplog.set_level(logging.ERROR, logger='core.chat.function_manager')
    try:
        out = fm.execute_function('boom', {}, scopes={'private': is_private}, allowed_tools=['boom'])
    finally:
        reset_scopes()
    assert SECRET in out, "she is still told what went wrong: that row is the chat's, encrypted"
    assert (SECRET in caplog.text) is (not is_private)


def test_the_tool_trace_file_never_takes_a_private_turn(monkeypatch, private):
    fm = _door(monkeypatch, lambda *a: ('ok', True))
    saved = []
    monkeypatch.setattr(fm, '_save_tool_history', lambda: saved.append(1))
    monkeypatch.setattr(fmod.config, 'TOOL_HISTORY_MAX_ENTRIES', 50, raising=False)
    fm._log_tool_call('save_memory', {'content': SECRET}, 'saved', 0.01, True)
    assert fm.tool_history == [] and not saved


def test_the_tool_trace_file_still_takes_an_ordinary_turn(monkeypatch):
    fm = _door(monkeypatch, lambda *a: ('ok', True))
    monkeypatch.setattr(fm, '_save_tool_history', lambda: None)
    monkeypatch.setattr(fmod.config, 'TOOL_HISTORY_MAX_ENTRIES', 50, raising=False)
    fm._log_tool_call('save_memory', {'content': 'plain'}, 'saved', 0.01, True)
    assert len(fm.tool_history) == 1
