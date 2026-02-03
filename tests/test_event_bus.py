"""
Event Bus Tests - Pub/sub system for real-time UI updates via SSE.

Tests the EventBus class for:
- Event publishing and delivery
- Subscriber management
- Replay buffer functionality
- Thread safety basics

Run with: pytest tests/test_event_bus.py -v
"""
import pytest
import time
import threading
from unittest.mock import patch, MagicMock


# =============================================================================
# EventBus Class Tests
# =============================================================================

class TestEventBusPublish:
    """Test event publishing."""

    def test_publish_creates_event_structure(self):
        """Published events should have type, data, timestamp."""
        from core.event_bus import EventBus

        bus = EventBus(replay_size=10)
        bus.publish("test_event", {"key": "value"})

        # Check replay buffer contains the event
        assert len(bus._replay_buffer) == 1
        event = bus._replay_buffer[0]
        assert event["type"] == "test_event"
        assert event["data"] == {"key": "value"}
        assert "timestamp" in event

    def test_publish_with_no_data(self):
        """Publishing with no data should use empty dict."""
        from core.event_bus import EventBus

        bus = EventBus(replay_size=10)
        bus.publish("empty_event")

        event = bus._replay_buffer[0]
        assert event["data"] == {}

    def test_publish_delivers_to_subscribers(self):
        """Published events should be delivered to all subscribers."""
        from core.event_bus import EventBus
        import queue

        bus = EventBus(replay_size=10)

        # Manually add a subscriber queue
        q = queue.Queue()
        with bus._lock:
            bus._subscribers["test_sub"] = q

        bus.publish("test_event", {"foo": "bar"})

        # Check queue received the event
        received = q.get_nowait()
        assert received["type"] == "test_event"
        assert received["data"]["foo"] == "bar"


class TestEventBusReplayBuffer:
    """Test replay buffer functionality."""

    def test_replay_buffer_limits_size(self):
        """Replay buffer should not exceed max size."""
        from core.event_bus import EventBus

        bus = EventBus(replay_size=5)

        for i in range(10):
            bus.publish(f"event_{i}")

        assert len(bus._replay_buffer) == 5
        # Should have the most recent 5
        types = [e["type"] for e in bus._replay_buffer]
        assert types == ["event_5", "event_6", "event_7", "event_8", "event_9"]

    def test_replay_buffer_preserves_order(self):
        """Replay buffer should maintain event order."""
        from core.event_bus import EventBus

        bus = EventBus(replay_size=10)

        bus.publish("first")
        bus.publish("second")
        bus.publish("third")

        types = [e["type"] for e in bus._replay_buffer]
        assert types == ["first", "second", "third"]


class TestEventBusSubscribers:
    """Test subscriber management."""

    def test_subscriber_count_starts_at_zero(self):
        """New bus should have no subscribers."""
        from core.event_bus import EventBus

        bus = EventBus()
        assert bus.subscriber_count() == 0

    def test_subscriber_registered_on_iteration(self):
        """Subscriber registration happens when generator is iterated."""
        from core.event_bus import EventBus
        import threading
        import time

        bus = EventBus()

        # Generator code only runs when iterated
        gen = bus.subscribe(replay=False)
        # Not registered yet (generator not started)
        assert bus.subscriber_count() == 0

        # Start iteration in a thread (will block waiting for events)
        def consume():
            try:
                next(gen)  # This triggers the registration
            except StopIteration:
                pass

        t = threading.Thread(target=consume)
        t.start()
        time.sleep(0.05)  # Give it a moment to register

        # Now should be registered
        assert bus.subscriber_count() == 1

        # Clean up - publish an event so the thread can exit
        bus.publish("done")
        t.join(timeout=1)


class TestEventBusSubscribeReplay:
    """Test replay functionality for late subscribers."""

    def test_replay_buffer_contains_recent_events(self):
        """Replay buffer should contain published events."""
        from core.event_bus import EventBus

        bus = EventBus(replay_size=10)

        # Publish some events
        bus.publish("event_1", {"n": 1})
        bus.publish("event_2", {"n": 2})

        # Check replay buffer directly
        assert len(bus._replay_buffer) == 2
        assert bus._replay_buffer[0]["type"] == "event_1"
        assert bus._replay_buffer[1]["type"] == "event_2"

    def test_replay_flag_affects_initial_queue(self):
        """Test that replay parameter controls initial event delivery."""
        from core.event_bus import EventBus
        import threading
        import time

        bus = EventBus(replay_size=10)

        # Publish events before any subscribers
        bus.publish("pre_event_1")
        bus.publish("pre_event_2")

        received_events = []

        def collector(gen):
            for event in gen:
                received_events.append(event)
                if len(received_events) >= 3:
                    break

        # Subscribe with replay=True in a thread
        gen = bus.subscribe(replay=True)
        t = threading.Thread(target=collector, args=(gen,))
        t.start()

        # Give thread time to process replayed events
        time.sleep(0.1)

        # Publish one more event
        bus.publish("live_event")
        t.join(timeout=1)

        # Should have received the 2 replayed + 1 live event
        event_types = [e["type"] for e in received_events]
        assert "pre_event_1" in event_types
        assert "pre_event_2" in event_types
        assert "live_event" in event_types


class TestEventConstants:
    """Test event type constants."""

    def test_events_class_has_required_types(self):
        """Events class should define all required event types."""
        from core.event_bus import Events

        # Core events
        assert hasattr(Events, 'AI_TYPING_START')
        assert hasattr(Events, 'AI_TYPING_END')
        assert hasattr(Events, 'MESSAGE_ADDED')
        assert hasattr(Events, 'CHAT_SWITCHED')
        assert hasattr(Events, 'CHAT_CLEARED')

        # Prompt events
        assert hasattr(Events, 'PROMPT_CHANGED')
        assert hasattr(Events, 'PROMPT_DELETED')
        assert hasattr(Events, 'COMPONENTS_CHANGED')

        # Context events
        assert hasattr(Events, 'CONTEXT_WARNING')
        assert hasattr(Events, 'CONTEXT_CRITICAL')

    def test_event_type_values_are_strings(self):
        """Event type values should be string identifiers."""
        from core.event_bus import Events

        assert isinstance(Events.AI_TYPING_START, str)
        assert isinstance(Events.PROMPT_CHANGED, str)
        assert isinstance(Events.CHAT_CLEARED, str)


class TestGlobalEventBus:
    """Test singleton and convenience functions."""

    def test_get_event_bus_returns_singleton(self):
        """get_event_bus should return same instance."""
        from core.event_bus import get_event_bus

        bus1 = get_event_bus()
        bus2 = get_event_bus()

        assert bus1 is bus2

    def test_publish_convenience_function(self):
        """publish() should publish to global bus."""
        from core.event_bus import publish, get_event_bus

        bus = get_event_bus()
        initial_count = len(bus._replay_buffer)

        publish("test_convenience", {"value": 123})

        assert len(bus._replay_buffer) == initial_count + 1


class TestEventBusThreadSafety:
    """Basic thread safety tests."""

    def test_concurrent_publishes(self):
        """Multiple threads publishing should not crash."""
        from core.event_bus import EventBus

        bus = EventBus(replay_size=100)
        errors = []

        def publisher(thread_id):
            try:
                for i in range(20):
                    bus.publish(f"thread_{thread_id}", {"i": i})
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=publisher, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        # Should have 100 events (5 threads * 20 each)
        assert len(bus._replay_buffer) == 100
