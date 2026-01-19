// core/event-bus.js - EventSource client for real-time server events

let eventSource = null;
let reconnectAttempts = 0;
let reconnectTimeout = null;
const MAX_RECONNECT_DELAY = 30000;
const BASE_RECONNECT_DELAY = 1000;

// Event handlers registry
const handlers = new Map();

/**
 * Connect to the event bus SSE endpoint
 * @param {boolean} replay - Whether to replay recent events on connect
 */
export function connect(replay = false) {
    if (eventSource && eventSource.readyState !== EventSource.CLOSED) {
        console.log('[EventBus] Already connected');
        return;
    }
    
    const url = `/api/events?replay=${replay}`;
    console.log(`[EventBus] Connecting to ${url}`);
    
    eventSource = new EventSource(url);
    
    eventSource.onopen = () => {
        console.log('[EventBus] Connected');
        reconnectAttempts = 0;
        dispatch('bus_connected', {});
    };
    
    eventSource.onmessage = (e) => {
        try {
            const event = JSON.parse(e.data);
            
            // Skip keepalives
            if (event.type === 'keepalive') return;
            
            // Dispatch to registered handlers
            dispatch(event.type, event.data, event.timestamp);
            
        } catch (err) {
            console.error('[EventBus] Parse error:', err, e.data);
        }
    };
    
    eventSource.onerror = (e) => {
        console.warn('[EventBus] Connection error, will reconnect');
        eventSource.close();
        scheduleReconnect();
    };
}

/**
 * Disconnect from the event bus
 */
export function disconnect() {
    if (reconnectTimeout) {
        clearTimeout(reconnectTimeout);
        reconnectTimeout = null;
    }
    
    if (eventSource) {
        eventSource.close();
        eventSource = null;
        console.log('[EventBus] Disconnected');
        dispatch('bus_disconnected', {});
    }
}

/**
 * Schedule a reconnection with exponential backoff
 */
function scheduleReconnect() {
    if (reconnectTimeout) return;
    
    const delay = Math.min(
        BASE_RECONNECT_DELAY * Math.pow(2, reconnectAttempts),
        MAX_RECONNECT_DELAY
    );
    
    console.log(`[EventBus] Reconnecting in ${delay}ms (attempt ${reconnectAttempts + 1})`);
    
    reconnectTimeout = setTimeout(() => {
        reconnectTimeout = null;
        reconnectAttempts++;
        connect(false); // Don't replay on reconnect
    }, delay);
}

/**
 * Register an event handler
 * @param {string} eventType - Event type to listen for
 * @param {function} handler - Handler function(data, timestamp)
 * @returns {function} Unsubscribe function
 */
export function on(eventType, handler) {
    if (!handlers.has(eventType)) {
        handlers.set(eventType, new Set());
    }
    handlers.get(eventType).add(handler);
    
    // Return unsubscribe function
    return () => {
        const set = handlers.get(eventType);
        if (set) {
            set.delete(handler);
            if (set.size === 0) {
                handlers.delete(eventType);
            }
        }
    };
}

/**
 * Remove an event handler
 * @param {string} eventType - Event type
 * @param {function} handler - Handler to remove
 */
export function off(eventType, handler) {
    const set = handlers.get(eventType);
    if (set) {
        set.delete(handler);
    }
}

/**
 * Dispatch an event to all registered handlers
 */
function dispatch(eventType, data, timestamp) {
    const set = handlers.get(eventType);
    if (set) {
        for (const handler of set) {
            try {
                handler(data, timestamp);
            } catch (err) {
                console.error(`[EventBus] Handler error for ${eventType}:`, err);
            }
        }
    }
    
    // Also dispatch wildcard handlers
    const wildcardSet = handlers.get('*');
    if (wildcardSet) {
        for (const handler of wildcardSet) {
            try {
                handler({ type: eventType, data, timestamp });
            } catch (err) {
                console.error('[EventBus] Wildcard handler error:', err);
            }
        }
    }
}

/**
 * Check if connected
 * @returns {boolean}
 */
export function isConnected() {
    return eventSource && eventSource.readyState === EventSource.OPEN;
}

// Event type constants (mirror server-side)
export const Events = {
    // AI/Chat events
    AI_TYPING_START: 'ai_typing_start',
    AI_TYPING_END: 'ai_typing_end',
    MESSAGE_ADDED: 'message_added',
    MESSAGE_REMOVED: 'message_removed',
    CHAT_SWITCHED: 'chat_switched',
    CHAT_CLEARED: 'chat_cleared',
    
    // TTS events
    TTS_PLAYING: 'tts_playing',
    TTS_STOPPED: 'tts_stopped',
    
    // STT events
    STT_RECORDING_START: 'stt_recording_start',
    STT_RECORDING_END: 'stt_recording_end',
    STT_PROCESSING: 'stt_processing',
    
    // Wakeword events
    WAKEWORD_DETECTED: 'wakeword_detected',
    
    // Tool events
    TOOL_EXECUTING: 'tool_executing',
    TOOL_COMPLETE: 'tool_complete',
    
    // System events
    PROMPT_CHANGED: 'prompt_changed',
    ABILITY_CHANGED: 'ability_changed',
    SPICE_CHANGED: 'spice_changed',
    
    // Context threshold events
    CONTEXT_WARNING: 'context_warning',
    CONTEXT_CRITICAL: 'context_critical',
    
    // Error events
    LLM_ERROR: 'llm_error',
    TTS_ERROR: 'tts_error',
    STT_ERROR: 'stt_error',
    
    // Connection events (client-side only)
    BUS_CONNECTED: 'bus_connected',
    BUS_DISCONNECTED: 'bus_disconnected'
};