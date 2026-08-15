# event_bus.py - Central pub/sub event bus for real-time UI updates
import asyncio
import threading
import queue
import time
import json
import logging
from typing import AsyncGenerator, Generator, Optional, Dict, Any
from collections import deque

logger = logging.getLogger(__name__)

class EventBus:
    """Thread-safe pub/sub event bus with replay buffer for late subscribers."""
    
    def __init__(self, replay_size: int = 50):
        self._lock = threading.Lock()
        self._subscribers: Dict[str, queue.Queue] = {}
        self._async_subscribers: Dict[str, tuple] = {}  # sub_id -> (asyncio.Queue, loop)
        self._replay_buffer: deque = deque(maxlen=replay_size)
        self._subscriber_counter = 0
        logger.info(f"EventBus initialized (replay_size={replay_size})")
    
    # Ephemeral events aren't replayed to late subscribers. plugin_notice:
    # a freshly-opened tab shouldn't surface a stale "done" toast. The
    # voice_turn_* trio: transient paint events whose durable copy is chat
    # history — replaying them re-paints fragments of a finished turn, and
    # the ring would hold a private chat's spoken transcript in memory for
    # any later tab, even past a vault lock (vaulted-chats Phase 0).
    # tts_speak: replaying makes a late tab SPEAK stale text, and the ring
    # would hold a private task response past a lock (Phase 3).
    _EPHEMERAL_TYPES = frozenset({
        "plugin_notice", "voice_turn_start", "voice_turn_chunk",
        "voice_turn_end", "tts_speak",
    })

    def publish(self, event_type: str, data: Optional[Dict[str, Any]] = None,
                ephemeral: bool = False):
        """Publish an event to all subscribers (sync and async).

        ephemeral=True keeps the event out of the replay ring — REQUIRED for
        any event whose payload carries chat content (vaulted chats Phase 3:
        the ring outlives a vault lock and replays to any later tab). Plugin
        publishers can't reach the core name list above, so they must pass
        the flag themselves."""
        event = {
            "type": event_type,
            "data": data or {},
            "timestamp": time.time()
        }

        with self._lock:
            if not ephemeral and event_type not in self._EPHEMERAL_TYPES:
                self._replay_buffer.append(event)
            dead_subscribers = []

            # Sync subscribers
            for sub_id, q in self._subscribers.items():
                try:
                    q.put_nowait(event)
                except queue.Full:
                    logger.warning(f"Subscriber {sub_id} queue full, dropping event")
                except Exception as e:
                    logger.error(f"Error publishing to {sub_id}: {e}")
                    dead_subscribers.append(sub_id)

            for sub_id in dead_subscribers:
                del self._subscribers[sub_id]

            # Async subscribers — thread-safe put via event loop
            dead_async = []
            for sub_id, (aq, loop) in self._async_subscribers.items():
                try:
                    loop.call_soon_threadsafe(aq.put_nowait, event)
                except RuntimeError:
                    dead_async.append(sub_id)

            for sub_id in dead_async:
                del self._async_subscribers[sub_id]

        logger.debug(f"Published: {event_type}")
    
    def subscribe(self, replay: bool = True) -> Generator[Dict[str, Any], None, None]:
        """Subscribe to events. Yields events as they arrive.
        
        Args:
            replay: If True, replay recent events before live stream
        """
        sub_id = None
        q = queue.Queue(maxsize=100)
        
        with self._lock:
            self._subscriber_counter += 1
            sub_id = f"sub_{self._subscriber_counter}"
            self._subscribers[sub_id] = q
            
            if replay:
                for event in self._replay_buffer:
                    try:
                        q.put_nowait(event)
                    except queue.Full:
                        break
        
        logger.debug(f"New subscriber: {sub_id} (replay={replay}) — total subscribers: {len(self._subscribers)}")

        # Immediate connection event - wakes up client instantly
        # Include boot_version so frontend can detect server restarts
        boot_version = None
        try:
            from core.api_fastapi import BOOT_VERSION
            boot_version = BOOT_VERSION
        except Exception:
            pass
        yield {"type": "connected", "data": {"sub_id": sub_id, "boot_version": boot_version}, "timestamp": time.time()}

        try:
            keepalive_count = 0
            while True:
                try:
                    event = q.get(timeout=15)
                    yield event
                except queue.Empty:
                    # Send keepalive (15s interval)
                    keepalive_count += 1
                    logger.debug(f"Keepalive #{keepalive_count} for {sub_id}")
                    yield {"type": "keepalive", "timestamp": time.time()}
        except GeneratorExit:
            logger.debug(f"Subscriber {sub_id} generator closed by client")
        finally:
            with self._lock:
                if sub_id in self._subscribers:
                    del self._subscribers[sub_id]
            logger.debug(f"Subscriber disconnected: {sub_id}")
    
    async def async_subscribe(self, replay: bool = True) -> AsyncGenerator[Dict[str, Any], None]:
        """Async subscribe to events. No threadpool thread consumed."""
        sub_id = None
        aq = asyncio.Queue(maxsize=100)
        loop = asyncio.get_running_loop()

        with self._lock:
            self._subscriber_counter += 1
            sub_id = f"async_sub_{self._subscriber_counter}"
            self._async_subscribers[sub_id] = (aq, loop)

            if replay:
                for event in self._replay_buffer:
                    try:
                        aq.put_nowait(event)
                    except asyncio.QueueFull:
                        break

        logger.debug(f"New async subscriber: {sub_id} (replay={replay}) — total: {len(self._subscribers) + len(self._async_subscribers)}")

        boot_version = None
        try:
            from core.api_fastapi import BOOT_VERSION
            boot_version = BOOT_VERSION
        except Exception:
            pass
        yield {"type": "connected", "data": {"sub_id": sub_id, "boot_version": boot_version}, "timestamp": time.time()}

        try:
            keepalive_count = 0
            while True:
                try:
                    event = await asyncio.wait_for(aq.get(), timeout=15)
                    yield event
                except asyncio.TimeoutError:
                    keepalive_count += 1
                    logger.debug(f"Keepalive #{keepalive_count} for {sub_id}")
                    yield {"type": "keepalive", "timestamp": time.time()}
        except GeneratorExit:
            logger.debug(f"Async subscriber {sub_id} generator closed by client")
        finally:
            with self._lock:
                self._async_subscribers.pop(sub_id, None)
            logger.debug(f"Async subscriber disconnected: {sub_id}")

    def subscriber_count(self) -> int:
        """Return current number of subscribers."""
        with self._lock:
            return len(self._subscribers) + len(self._async_subscribers)

    def clear_replay(self):
        """Empty the replay ring. Vault hunt R6 (2026-08-15): the ring
        outlives a vault lock — chat_switched / chat_settings_changed events
        deposited while unlocked carry private chat names (and settings text)
        to any later replay=true subscriber. lock() calls this so nothing
        pre-seal survives into the sealed world. Live subscribers are
        unaffected; only late-joiner replay is trimmed."""
        with self._lock:
            self._replay_buffer.clear()


# Singleton instance
_bus: Optional[EventBus] = None

def get_event_bus() -> EventBus:
    """Get or create the singleton event bus."""
    global _bus
    if _bus is None:
        _bus = EventBus()
    return _bus

def publish(event_type: str, data: Optional[Dict[str, Any]] = None,
            ephemeral: bool = False):
    """Convenience function to publish to the global bus."""
    get_event_bus().publish(event_type, data, ephemeral=ephemeral)


def clear_replay():
    """Convenience: empty the global bus's replay ring (see EventBus.clear_replay)."""
    get_event_bus().clear_replay()


# Event type constants
class Events:
    # AI/Chat events
    AI_TYPING_START = "ai_typing_start"
    AI_TYPING_END = "ai_typing_end"
    MESSAGE_ADDED = "message_added"
    MESSAGE_REMOVED = "message_removed"
    CHAT_SWITCHED = "chat_switched"
    CHAT_CREATED = "chat_created"
    CHAT_CLEARED = "chat_cleared"
    CHAT_DELETED = "chat_deleted"
    CHAT_RENAMED = "chat_renamed"
    CHAT_TRIMMED = "chat_trimmed"
    CHAT_COMPRESSED = "chat_compressed"
    CHAT_ARCHIVED = "chat_archived"
    
    # TTS events
    TTS_PLAYING = "tts_playing"
    TTS_STOPPED = "tts_stopped"
    TTS_SPEAK = "tts_speak"
    
    # STT events
    STT_RECORDING_START = "stt_recording_start"
    STT_RECORDING_END = "stt_recording_end"
    STT_PROCESSING = "stt_processing"
    
    # Wakeword events
    WAKEWORD_DETECTED = "wakeword_detected"

    # Conversation mode (v3) — fired on enter/exit so the UI reflects state.
    # Payload: {enabled: bool}.
    CONVERSATION_MODE_CHANGED = "conversation_mode_changed"
    # Streaming voice turn → web UI (so a local-mic reply streams in instead of
    # blobbing). START: {message_id}. CHUNK: {message_id, text}. END: {message_id}.
    VOICE_TURN_START = "voice_turn_start"
    VOICE_TURN_CHUNK = "voice_turn_chunk"
    VOICE_TURN_END = "voice_turn_end"
    
    # Tool events
    TOOL_EXECUTING = "tool_executing"
    TOOL_COMPLETE = "tool_complete"
    
    # System events
    PROMPT_CHANGED = "prompt_changed"
    PROMPT_DELETED = "prompt_deleted"
    COMPONENTS_CHANGED = "components_changed"
    TOOLSET_CHANGED = "toolset_changed"
    SPICE_CHANGED = "spice_changed"
    BACKGROUND_CHANGED = "background_changed"            # active chat background changed (render)
    BACKGROUNDS_LIBRARY_CHANGED = "backgrounds_library_changed"  # library upload/delete (tool refresh)
    SETTINGS_CHANGED = "settings_changed"
    CHAT_SETTINGS_CHANGED = "chat_settings_changed"
    # Scope CRUD — fired when memory/knowledge/goal/people scopes are created or
    # deleted, so the chat sidebar can refresh its dropdowns without a page
    # reload. Payload: {kind: "memory"|"knowledge"|"goal"|"people", action:
    # "created"|"deleted", name: "<scope_name>"}.
    SCOPE_CHANGED = "scope_changed"
    
    # Context threshold events
    CONTEXT_WARNING = "context_warning"    # 80% threshold
    CONTEXT_CRITICAL = "context_critical"  # 95% threshold
    
    # Connection events
    CONNECTED = "connected"

    # Error events
    LLM_ERROR = "llm_error"
    TTS_ERROR = "tts_error"
    STT_ERROR = "stt_error"
    
    # Continuity events
    CONTINUITY_TASK_STARTING = "continuity_task_starting"
    CONTINUITY_TASK_COMPLETE = "continuity_task_complete"
    CONTINUITY_TASK_SKIPPED = "continuity_task_skipped"
    CONTINUITY_TASK_ERROR = "continuity_task_error"
    CONTINUITY_TASK_PROGRESS = "continuity_task_progress"

    # Plugin events
    PLUGIN_RELOADED = "plugin_reloaded"
    PLUGIN_LOAD_ERROR = "plugin_load_error"
    # Generic plugin -> UI toast. Any plugin may publish this to surface a toast.
    # Payload: {plugin, message, severity} where severity in
    # {info, success, warning, error}. (Added 2026-06-16.)
    PLUGIN_NOTICE = "plugin_notice"

    # Daemon/webhook events
    DAEMON_EVENT = "daemon_event"
    WEBHOOK_FIRED = "webhook_fired"

    # Mind-data changed events — fired when the AI writes to memory/goal/
    # knowledge/people stores so the Mind view can live-refresh instead of
    # showing stale content after a save. Payload: {domain, scope, action}
    # where domain in {memory, goal, knowledge, people}, action in
    # {save, update, delete}. Without this event the user sees "done"
    # from Sapphire but their Mind tab doesn't update — can't tell the
    # difference between "tool silently failed" and "tool worked, view
    # stale." AIX-class bug. (Added 2026-04-19 after Scout 3 dispatch.)
    MIND_CHANGED = "mind_changed"

    # Re-embed pipeline progress — fired by the background worker as it
    # walks memories/knowledge_entries/people, re-generating vectors under
    # the current provider. Payload is the full status snapshot (running,
    # total, done, current_table, errors, etc). Settings UI uses this to
    # show a live progress bar without polling.
    REEMBED_PROGRESS = "reembed_progress"

    # Agent events
    AGENT_SPAWNED = "agent_spawned"
    AGENT_COMPLETED = "agent_completed"
    AGENT_DISMISSED = "agent_dismissed"
    AGENT_BATCH_COMPLETE = "agent_batch_complete"
    WORKSPACE_READY = "workspace_ready"