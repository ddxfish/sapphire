# event_bus.py - Central pub/sub event bus for real-time UI updates
import threading
import queue
import time
import json
import logging
from typing import Generator, Optional, Dict, Any
from collections import deque

logger = logging.getLogger(__name__)

class EventBus:
    """Thread-safe pub/sub event bus with replay buffer for late subscribers."""
    
    def __init__(self, replay_size: int = 50):
        self._lock = threading.Lock()
        self._subscribers: Dict[str, queue.Queue] = {}
        self._replay_buffer: deque = deque(maxlen=replay_size)
        self._subscriber_counter = 0
        logger.info(f"EventBus initialized (replay_size={replay_size})")
    
    def publish(self, event_type: str, data: Optional[Dict[str, Any]] = None):
        """Publish an event to all subscribers."""
        event = {
            "type": event_type,
            "data": data or {},
            "timestamp": time.time()
        }
        
        with self._lock:
            self._replay_buffer.append(event)
            dead_subscribers = []
            
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
        
        logger.info(f"New subscriber: {sub_id} (replay={replay})")
        
        try:
            while True:
                try:
                    event = q.get(timeout=30)
                    yield event
                except queue.Empty:
                    # Send keepalive
                    yield {"type": "keepalive", "timestamp": time.time()}
        finally:
            with self._lock:
                if sub_id in self._subscribers:
                    del self._subscribers[sub_id]
            logger.info(f"Subscriber disconnected: {sub_id}")
    
    def subscriber_count(self) -> int:
        """Return current number of subscribers."""
        with self._lock:
            return len(self._subscribers)


# Singleton instance
_bus: Optional[EventBus] = None

def get_event_bus() -> EventBus:
    """Get or create the singleton event bus."""
    global _bus
    if _bus is None:
        _bus = EventBus()
    return _bus

def publish(event_type: str, data: Optional[Dict[str, Any]] = None):
    """Convenience function to publish to the global bus."""
    get_event_bus().publish(event_type, data)


# Event type constants
class Events:
    # AI/Chat events
    AI_TYPING_START = "ai_typing_start"
    AI_TYPING_END = "ai_typing_end"
    MESSAGE_ADDED = "message_added"
    MESSAGE_REMOVED = "message_removed"
    CHAT_SWITCHED = "chat_switched"
    CHAT_CLEARED = "chat_cleared"
    
    # TTS events
    TTS_PLAYING = "tts_playing"
    TTS_STOPPED = "tts_stopped"
    
    # STT events
    STT_RECORDING_START = "stt_recording_start"
    STT_RECORDING_END = "stt_recording_end"
    STT_PROCESSING = "stt_processing"
    
    # Wakeword events
    WAKEWORD_DETECTED = "wakeword_detected"
    
    # Tool events
    TOOL_EXECUTING = "tool_executing"
    TOOL_COMPLETE = "tool_complete"
    
    # System events
    PROMPT_CHANGED = "prompt_changed"
    ABILITY_CHANGED = "ability_changed"
    SPICE_CHANGED = "spice_changed"
    
    # Context threshold events
    CONTEXT_WARNING = "context_warning"    # 80% threshold
    CONTEXT_CRITICAL = "context_critical"  # 95% threshold
    
    # Error events
    LLM_ERROR = "llm_error"
    TTS_ERROR = "tts_error"
    STT_ERROR = "stt_error"
    
    # Continuity events
    CONTINUITY_TASK_STARTING = "continuity_task_starting"
    CONTINUITY_TASK_COMPLETE = "continuity_task_complete"
    CONTINUITY_TASK_SKIPPED = "continuity_task_skipped"
    CONTINUITY_TASK_ERROR = "continuity_task_error"