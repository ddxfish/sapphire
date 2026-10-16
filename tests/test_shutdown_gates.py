"""Shutdown gates added 2026-10-16 (tmp/single-instance-plan.md rows 16, 17):
no new turn starts once the drain began, and the MCP daemon gets to close
its own stdio children before anyone stops its loop from outside."""
import pytest

from core.chat.chat import LLMChat, ChatBusy
import plugins.mcp_client.daemon as d


def test_closing_refuses_new_turns():
    llm = LLMChat.__new__(LLMChat)
    llm.closing = True
    with pytest.raises(ChatBusy):
        llm.begin_stream('trinity')


def test_mcp_stop_lets_the_loop_clean_up_first(monkeypatch):
    events = []

    class Loop:
        def is_running(self):
            return True

        def call_soon_threadsafe(self, fn):
            events.append('loop.stop')

    class Thread:
        alive = True

        def is_alive(self):
            return self.alive

        def join(self, timeout=None):
            events.append(f'join{timeout}')
            self.alive = False               # the loop finished its own disconnects
    monkeypatch.setattr(d, '_loop', Loop())
    monkeypatch.setattr(d, '_thread', Thread())
    d.stop()
    assert events == ['join8'] and d._stop_event.is_set()


def test_mcp_stop_forces_the_loop_only_when_cleanup_hangs(monkeypatch):
    events = []

    class Loop:
        def is_running(self):
            return True

        def stop(self):
            pass

        def call_soon_threadsafe(self, fn):
            events.append('loop.stop')

    class Thread:
        def is_alive(self):
            return True

        def join(self, timeout=None):
            events.append(f'join{timeout}')
    monkeypatch.setattr(d, '_loop', Loop())
    monkeypatch.setattr(d, '_thread', Thread())
    d.stop()
    assert events == ['join8', 'loop.stop', 'join3']
