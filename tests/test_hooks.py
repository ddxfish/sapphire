"""
Tests for core/hooks.py — Plugin hook system.

Run with: pytest tests/test_hooks.py -v
"""
import pytest
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.hooks import HookEvent, HookRunner


@pytest.fixture
def runner():
    """Fresh HookRunner for each test."""
    return HookRunner()


# =============================================================================
# HookEvent Tests
# =============================================================================

class TestHookEvent:
    def test_defaults(self):
        event = HookEvent()
        assert event.input == ""
        assert event.skip_llm is False
        assert event.response is None
        assert event.context_parts == []
        assert event.stop_propagation is False
        assert event.config is None
        assert event.metadata == {}

    def test_fields_mutable(self):
        event = HookEvent(input="hello")
        event.input = "modified"
        event.skip_llm = True
        event.response = "direct response"
        event.context_parts.append("injected text")
        assert event.input == "modified"
        assert event.skip_llm is True
        assert event.response == "direct response"
        assert event.context_parts == ["injected text"]

    def test_context_parts_independent(self):
        """Each HookEvent should have its own context_parts list."""
        e1 = HookEvent()
        e2 = HookEvent()
        e1.context_parts.append("one")
        assert e2.context_parts == []


# =============================================================================
# Registration Tests
# =============================================================================

class TestRegistration:
    def test_register_handler(self, runner):
        def my_handler(event): pass
        runner.register("pre_chat", my_handler, plugin_name="test")
        assert runner.has_handlers("pre_chat")

    def test_no_handlers_by_default(self, runner):
        assert not runner.has_handlers("pre_chat")

    def test_get_handlers_returns_sorted(self, runner):
        def low(event): pass
        def high(event): pass
        runner.register("pre_chat", high, priority=80, plugin_name="high")
        runner.register("pre_chat", low, priority=10, plugin_name="low")
        handlers = runner.get_handlers("pre_chat")
        assert handlers[0][2] == "low"   # plugin_name
        assert handlers[1][2] == "high"

    def test_unregister_specific_hook(self, runner):
        def h1(event): pass
        def h2(event): pass
        runner.register("pre_chat", h1, plugin_name="plugA")
        runner.register("pre_chat", h2, plugin_name="plugB")
        runner.unregister("pre_chat", "plugA")
        handlers = runner.get_handlers("pre_chat")
        assert len(handlers) == 1
        assert handlers[0][2] == "plugB"

    def test_unregister_plugin_all_hooks(self, runner):
        def h1(event): pass
        def h2(event): pass
        runner.register("pre_chat", h1, plugin_name="plugA")
        runner.register("prompt_inject", h2, plugin_name="plugA")
        runner.unregister_plugin("plugA")
        assert not runner.has_handlers("pre_chat")
        assert not runner.has_handlers("prompt_inject")

    def test_clear(self, runner):
        def h(event): pass
        runner.register("pre_chat", h, plugin_name="test")
        runner.clear()
        assert not runner.has_handlers("pre_chat")


# =============================================================================
# Fire Tests
# =============================================================================

class TestFire:
    def test_fire_empty_hook(self, runner):
        """Firing a hook with no handlers should return event unchanged."""
        event = HookEvent(input="hello")
        result = runner.fire("pre_chat", event)
        assert result.input == "hello"

    def test_fire_mutates_event(self, runner):
        def mutator(event):
            event.input = "mutated"
        runner.register("pre_chat", mutator, plugin_name="test")
        event = HookEvent(input="original")
        result = runner.fire("pre_chat", event)
        assert result.input == "mutated"

    def test_fire_priority_order(self, runner):
        """Handlers fire in priority order (lower first)."""
        order = []
        def first(event): order.append("first")
        def second(event): order.append("second")
        def third(event): order.append("third")
        runner.register("pre_chat", third, priority=90, plugin_name="c")
        runner.register("pre_chat", first, priority=10, plugin_name="a")
        runner.register("pre_chat", second, priority=50, plugin_name="b")
        runner.fire("pre_chat", HookEvent())
        assert order == ["first", "second", "third"]

    def test_fire_mutation_cascades(self, runner):
        """Later handlers see mutations from earlier handlers."""
        def step1(event):
            event.input = event.input + " step1"
        def step2(event):
            event.input = event.input + " step2"
        runner.register("pre_chat", step1, priority=10, plugin_name="a")
        runner.register("pre_chat", step2, priority=20, plugin_name="b")
        result = runner.fire("pre_chat", HookEvent(input="start"))
        assert result.input == "start step1 step2"

    def test_fire_returns_same_event_object(self, runner):
        event = HookEvent(input="test")
        result = runner.fire("pre_chat", event)
        assert result is event

    def test_prompt_inject_appends_context(self, runner):
        def inject_outfit(event):
            event.context_parts.append("Wearing: red kimono")
        def inject_location(event):
            event.context_parts.append("Location: home")
        runner.register("prompt_inject", inject_outfit, priority=50, plugin_name="outfit")
        runner.register("prompt_inject", inject_location, priority=60, plugin_name="location")
        event = HookEvent()
        runner.fire("prompt_inject", event)
        assert event.context_parts == ["Wearing: red kimono", "Location: home"]

    def test_skip_llm_pattern(self, runner):
        def stop_handler(event):
            event.skip_llm = True
            event.response = "Stopped."
            event.stop_propagation = True
        runner.register("pre_chat", stop_handler, priority=1, plugin_name="stop")
        event = HookEvent(input="stop")
        runner.fire("pre_chat", event)
        assert event.skip_llm is True
        assert event.response == "Stopped."


# =============================================================================
# Stop Propagation Tests
# =============================================================================

class TestStopPropagation:
    def test_stop_propagation_blocks_later_handlers(self, runner):
        order = []
        def blocker(event):
            order.append("blocker")
            event.stop_propagation = True
        def blocked(event):
            order.append("blocked")
        runner.register("pre_chat", blocker, priority=10, plugin_name="a")
        runner.register("pre_chat", blocked, priority=50, plugin_name="b")
        runner.fire("pre_chat", HookEvent())
        assert order == ["blocker"]

    def test_stop_propagation_doesnt_affect_other_hooks(self, runner):
        def blocker(event):
            event.stop_propagation = True
        def other(event):
            event.metadata["reached"] = True
        runner.register("pre_chat", blocker, plugin_name="a")
        runner.register("post_chat", other, plugin_name="b")
        runner.fire("pre_chat", HookEvent())
        event2 = HookEvent()
        runner.fire("post_chat", event2)
        assert event2.metadata.get("reached") is True


# =============================================================================
# Error Isolation Tests
# =============================================================================

class TestErrorIsolation:
    def test_broken_handler_doesnt_crash(self, runner):
        def broken(event):
            raise ValueError("plugin bug")
        def healthy(event):
            event.metadata["reached"] = True
        runner.register("pre_chat", broken, priority=10, plugin_name="buggy")
        runner.register("pre_chat", healthy, priority=50, plugin_name="good")
        event = HookEvent()
        result = runner.fire("pre_chat", event)
        assert result.metadata["reached"] is True

    def test_broken_handler_doesnt_stop_propagation(self, runner):
        """An exception should NOT behave like stop_propagation."""
        order = []
        def broken(event):
            order.append("broken")
            raise RuntimeError("oops")
        def after(event):
            order.append("after")
        runner.register("pre_chat", broken, priority=10, plugin_name="buggy")
        runner.register("pre_chat", after, priority=20, plugin_name="good")
        runner.fire("pre_chat", HookEvent())
        assert order == ["broken", "after"]


# =============================================================================
# Voice Command Matching Tests
# =============================================================================

class TestVoiceMatching:
    def test_exact_match(self, runner):
        triggered = []
        def handler(event):
            triggered.append(True)
        runner.register("pre_chat", handler, plugin_name="stop",
                        voice_match={"triggers": ["stop", "halt"], "match": "exact"})
        runner.fire("pre_chat", HookEvent(input="stop"))
        assert len(triggered) == 1

    def test_exact_match_case_insensitive(self, runner):
        triggered = []
        def handler(event):
            triggered.append(True)
        runner.register("pre_chat", handler, plugin_name="stop",
                        voice_match={"triggers": ["stop"], "match": "exact"})
        runner.fire("pre_chat", HookEvent(input="STOP"))
        assert len(triggered) == 1

    def test_exact_no_match(self, runner):
        triggered = []
        def handler(event):
            triggered.append(True)
        runner.register("pre_chat", handler, plugin_name="stop",
                        voice_match={"triggers": ["stop"], "match": "exact"})
        runner.fire("pre_chat", HookEvent(input="please stop talking"))
        assert len(triggered) == 0

    def test_starts_with_match(self, runner):
        triggered = []
        def handler(event):
            triggered.append(True)
        runner.register("pre_chat", handler, plugin_name="music",
                        voice_match={"triggers": ["play ", "put on "], "match": "starts_with"})
        runner.fire("pre_chat", HookEvent(input="play some jazz"))
        assert len(triggered) == 1

    def test_starts_with_no_match(self, runner):
        triggered = []
        def handler(event):
            triggered.append(True)
        runner.register("pre_chat", handler, plugin_name="music",
                        voice_match={"triggers": ["play "], "match": "starts_with"})
        runner.fire("pre_chat", HookEvent(input="can you play music"))
        assert len(triggered) == 0

    def test_contains_match(self, runner):
        triggered = []
        def handler(event):
            triggered.append(True)
        runner.register("pre_chat", handler, plugin_name="weather",
                        voice_match={"triggers": ["weather"], "match": "contains"})
        runner.fire("pre_chat", HookEvent(input="what's the weather like today"))
        assert len(triggered) == 1

    def test_regex_match(self, runner):
        triggered = []
        def handler(event):
            triggered.append(True)
        runner.register("pre_chat", handler, plugin_name="timer",
                        voice_match={"triggers": [r"set.*timer.*\d+"], "match": "regex"})
        runner.fire("pre_chat", HookEvent(input="set a timer for 5 minutes"))
        assert len(triggered) == 1

    def test_regex_no_match(self, runner):
        triggered = []
        def handler(event):
            triggered.append(True)
        runner.register("pre_chat", handler, plugin_name="timer",
                        voice_match={"triggers": [r"set.*timer.*\d+"], "match": "regex"})
        runner.fire("pre_chat", HookEvent(input="hello there"))
        assert len(triggered) == 0

    def test_no_voice_match_always_fires(self, runner):
        """Handlers without voice_match fire on every input."""
        triggered = []
        def handler(event):
            triggered.append(True)
        runner.register("pre_chat", handler, plugin_name="logger")
        runner.fire("pre_chat", HookEvent(input="anything at all"))
        assert len(triggered) == 1

    def test_voice_match_with_whitespace(self, runner):
        """Exact match should strip whitespace from input."""
        triggered = []
        def handler(event):
            triggered.append(True)
        runner.register("pre_chat", handler, plugin_name="stop",
                        voice_match={"triggers": ["stop"], "match": "exact"})
        runner.fire("pre_chat", HookEvent(input="  stop  "))
        assert len(triggered) == 1

    def test_mixed_voice_and_regular_handlers(self, runner):
        """Voice-matched and regular handlers coexist on the same hook."""
        order = []
        def stop_cmd(event):
            order.append("stop")
            event.skip_llm = True
            event.stop_propagation = True
        def logger_hook(event):
            order.append("logger")

        runner.register("pre_chat", stop_cmd, priority=1, plugin_name="stop",
                        voice_match={"triggers": ["stop"], "match": "exact"})
        runner.register("pre_chat", logger_hook, priority=90, plugin_name="logger")

        # "stop" triggers both stop_cmd (matches) and would trigger logger,
        # but stop_propagation blocks it
        runner.fire("pre_chat", HookEvent(input="stop"))
        assert order == ["stop"]

        # "hello" skips stop_cmd (no match) but triggers logger
        order.clear()
        runner.fire("pre_chat", HookEvent(input="hello"))
        assert order == ["logger"]


# =============================================================================
# Edge Cases
# =============================================================================

class TestEdgeCases:
    def test_same_priority_stable_order(self, runner):
        """Handlers at same priority maintain registration order."""
        order = []
        def first(event): order.append("first")
        def second(event): order.append("second")
        runner.register("pre_chat", first, priority=50, plugin_name="a")
        runner.register("pre_chat", second, priority=50, plugin_name="b")
        runner.fire("pre_chat", HookEvent())
        assert order == ["first", "second"]

    def test_fire_unknown_hook(self, runner):
        """Firing an unregistered hook should be a no-op."""
        event = HookEvent(input="test")
        result = runner.fire("nonexistent_hook", event)
        assert result.input == "test"

    def test_register_after_fire(self, runner):
        """Registering new handlers after firing should work."""
        def h1(event): event.metadata["h1"] = True
        runner.register("pre_chat", h1, plugin_name="a")
        runner.fire("pre_chat", HookEvent())

        def h2(event): event.metadata["h2"] = True
        runner.register("pre_chat", h2, plugin_name="b")
        event = HookEvent()
        runner.fire("pre_chat", event)
        assert event.metadata.get("h1") and event.metadata.get("h2")

    def test_metadata_passes_between_hooks(self, runner):
        """Metadata set in one hook type persists on the event object."""
        event = HookEvent()

        def pre(e): e.metadata["key"] = "value"
        runner.register("pre_chat", pre, plugin_name="a")
        runner.fire("pre_chat", event)

        # Same event object passed to a different hook
        def post(e): e.metadata["key2"] = e.metadata.get("key", "missing")
        runner.register("post_chat", post, plugin_name="b")
        runner.fire("post_chat", event)

        assert event.metadata["key2"] == "value"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


def test_exact_voice_match_ignores_end_punctuation():
    """STT punctuates: "Stop." and "Be quiet!" must still hit exact triggers."""
    from core.hooks import HookRunner
    r = HookRunner()
    vm = {"triggers": ["stop", "be quiet"], "match": "exact"}
    for said in ("Stop.", "stop", " STOP! ", "Be quiet!", "be quiet..."):
        assert r._check_voice_match(vm, said), said
    for said in ("stop it", "don't stop", "be, quiet"):
        assert not r._check_voice_match(vm, said), said
