// api.js - Backend communication
import { fetchWithTimeout } from './shared/fetch.js';
import { dispatch, on, Events } from './core/event-bus.js';

export { fetchWithTimeout };

// Context bar update function
const updateContextBar = (context) => {
    const bar = document.getElementById('context-bar');
    if (!bar || !context) return;
    
    // Hide bar if context limit is disabled (0)
    if (context.limit === 0) {
        bar.style.display = 'none';
        return;
    }
    
    bar.style.display = 'block';
    bar.style.width = `${context.percent}%`;
    bar.title = `Context: ${context.used.toLocaleString()} / ${context.limit.toLocaleString()} tokens (${context.percent}%)`;
};

// Unified status endpoint - single call for all UI state
export const fetchStatus = async () => {
    const status = await fetchWithTimeout('/api/status', {}, 5000);
    // Update context bar if present
    if (status?.context) {
        updateContextBar(status.context);
    }
    return status;
};

let _lastHistoryChatName = null;
export const getLastHistoryChatName = () => _lastHistoryChatName;

export const fetchHistory = async () => {
    const response = await fetchWithTimeout('/api/history');
    // Update context bar if context info is present
    if (response && response.context) {
        updateContextBar(response.context);
    }
    _lastHistoryChatName = response?.chat_name || null;
    // Return messages array for backward compatibility
    return response.messages || response;
};

export const fetchRawHistory = () => fetchWithTimeout('/api/history/raw');
export const removeFromUserMessage = (userMessage) => fetchWithTimeout('/api/history/messages', {
    method: 'DELETE', 
    headers: { 'Content-Type': 'application/json' }, 
    body: JSON.stringify({ user_message: userMessage }) 
}, 10000);
export const removeLastAssistant = (timestamp) => fetchWithTimeout('/api/history/messages/remove-last-assistant', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ timestamp })
}, 10000);
export const removeFromAssistant = (timestamp) => fetchWithTimeout('/api/history/messages/remove-from-assistant', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ timestamp })
}, 10000);
export const removeToolCall = (toolCallId) => fetchWithTimeout(`/api/history/tool-call/${encodeURIComponent(toolCallId)}`, {
    method: 'DELETE'
}, 10000);
// Legacy - kept for backwards compatibility, prefer fetchStatus
export const fetchSystemStatus = () => fetchWithTimeout('/api/system/status', {}, 5000);

// Chat management
export const cancelGeneration = () => fetchWithTimeout('/api/cancel', { 
    method: 'POST',
    headers: { 'Content-Type': 'application/json' }
}, 5000);
export const fetchChatList = (type) => fetchWithTimeout(type ? `/api/chats?type=${type}` : '/api/chats', {}, 10000);
export const createChat = (name) => fetchWithTimeout('/api/chats', {
    method: 'POST', 
    headers: { 'Content-Type': 'application/json' }, 
    body: JSON.stringify({ name }) 
}, 10000);
export const deleteChat = (name) => fetchWithTimeout(`/api/chats/${encodeURIComponent(name)}`, { 
    method: 'DELETE' 
}, 10000);
// Chat-switch epoch: bumped when an activate STARTS. A chat-list response
// captured before the bump is stale truth — populateChatDropdown must not
// adopt its active_chat into #chat-select (the GLM-sidebar race, 2026-08-08).
// High-water counter, not a boolean: story entry fires two overlapping
// activates and the Game Room exit handback is un-awaited.
let _switchEpoch = 0;
let _pendingActivates = 0;
export const getSwitchEpoch = () => _switchEpoch;
export const hasPendingActivate = () => _pendingActivates > 0;
export const activateChat = async (name) => {
    _switchEpoch++;
    _pendingActivates++;
    try {
        return await fetchWithTimeout(`/api/chats/${encodeURIComponent(name)}/activate`, {
            method: 'POST'
        }, 10000);
    } finally {
        _pendingActivates--;
    }
};
export const clearChat = () => fetchWithTimeout('/api/history/messages', {
    method: 'DELETE',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ count: -1 })
}, 10000);
export const importChat = (messages) => fetchWithTimeout('/api/history/import', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ messages })
}, 30000);

// Chat Manager (views/chat-manage.js)
export const fetchChatListStats = () => fetchWithTimeout('/api/chats?stats=1', {}, 15000);
export const searchChats = (q) => fetchWithTimeout(
    '/api/chats/search?q=' + encodeURIComponent(q), {}, 15000);
export const bulkDeleteChats = (names) => fetchWithTimeout('/api/chats/bulk-delete', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ names })
}, 30000);
export const bulkClearChats = (names) => fetchWithTimeout('/api/chats/bulk-clear', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ names })
}, 30000);
export const bulkExportChats = (names) => fetchWithTimeout('/api/chats/bulk-export', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ names })
}, 60000);
export const exportChatByName = (name) => fetchWithTimeout(`/api/chats/${encodeURIComponent(name)}/export`, {}, 30000);
export const renameChat = (name, newName) => fetchWithTimeout(`/api/chats/${encodeURIComponent(name)}/rename`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ new_name: newName })
}, 10000);
export const trimChat = (name, opts) => fetchWithTimeout(`/api/chats/${encodeURIComponent(name)}/trim`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(opts)
}, 60000);
export const compressChat = (name, opts) => fetchWithTimeout(`/api/chats/${encodeURIComponent(name)}/compress`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(opts)
}, 15000);
export const compressStatus = () => fetchWithTimeout('/api/chats/compress/status', {}, 10000);
export const setChatArchived = (name, archived) => fetchWithTimeout(`/api/chats/${encodeURIComponent(name)}/archive`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ archived })
}, 10000);

// Shared SSE event processor
const processSSEData = (data, handlers) => {
    const { onChunk, onToolStart, onToolEnd, onReload, onDone, onLegacyChunk, onStreamStarted, onIterationStart } = handlers;
    
    if (data.type === 'stream_started') {
        if (onStreamStarted) onStreamStarted();
        return { gotContent: true };
    }
    
    if (data.type === 'iteration_start') {
        if (onIterationStart) onIterationStart(data.iteration);
        return { gotContent: true };
    }
    
    if (data.type === 'content') {
        if (onChunk) onChunk(data.text || '');
        return { gotContent: true };
    }
    
    if (data.type === 'tool_pending') {
        if (onToolStart) {
            onToolStart(`pending-${data.index || 0}`, data.name, null);
        }
        return { gotContent: true };
    }

    if (data.type === 'tool_start') {
        if (onToolStart) {
            onToolStart(data.id, data.name, data.args);
        } else {
            console.warn('[SSE] tool_start received but no handler!');
        }
        return { gotContent: true };
    }
    
    if (data.type === 'tool_end') {
        if (onToolEnd) {
            onToolEnd(data.id, data.name, data.result, data.error);
        } else {
            console.warn('[SSE] tool_end received but no handler!');
        }
        return {};
    }
    
    if (data.type === 'reload') {
        if (onReload) onReload();
        return { shouldReturn: true };
    }

    if (data.type === 'notice') {
        // Transient UX signal from backend (dangling toolset, empty-content
        // fallback, etc). Dispatched via event-bus so any view can subscribe
        // and call ui.showToast — keeps api.js decoupled from ui.js.
        dispatch('chat_notice', { message: data.message || '', severity: data.severity || 'warning' });
        return {};
    }

    // Streaming TTS events (v2.7.0). Dispatched via event-bus so audio.js
    // can subscribe without an import cycle (audio.js already imports api.js).
    // Inert when streaming TTS is disabled on the brain — these events
    // simply don't fire. Pass the full payload so audio.js can read
    // `stream_id` for per-stream isolation (2026-05-18 herring #5).
    if (data.type === 'tts_stream_start') {
        dispatch('tts_stream_start', data);
        return {};
    }
    if (data.type === 'tts_chunk') {
        dispatch('tts_stream_chunk', data);
        return {};
    }
    if (data.type === 'tts_stream_end') {
        dispatch('tts_stream_end', data);
        return {};
    }

    // Legacy chunk format
    if (data.chunk) {
        if (data.chunk.includes('<<RELOAD_PAGE>>')) {
            if (onReload) onReload();
            return { shouldReturn: true };
        }
        if (onLegacyChunk) onLegacyChunk(data.chunk);
        return { gotContent: true };
    }
    
    if (data.done) {
        console.log('[SSE] Done received');
        if (onDone) onDone(data.ephemeral || false);
        return { shouldReturn: true, isDone: true };
    }
    
    return {};
};

export const streamChatContinue = async (text, prefill, onChunk, onComplete, onError, signal = null, onToolStart = null, onToolEnd = null, onStreamStarted = null, onIterationStart = null) => {
    onChunk = _wrapChunkWithAvatarScan(onChunk);
    let reader = null;
    try {
        const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
        const res = await fetch('/api/chat/stream', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
            body: JSON.stringify({ text, prefill, skip_user_message: true }),
            signal
        });
        
        if (!res.ok) {
            if (res.status === 401) {
                window.location.href = '/login';
                return;
            }
            const err = await res.json().catch(() => ({}));
            return onError(new Error(err.error || `HTTP ${res.status}`), res.status);
        }
        
        reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '', gotContent = false;
        
        const handlers = {
            onChunk,
            onToolStart,
            onToolEnd,
            onStreamStarted,
            onIterationStart,
            onReload: () => setTimeout(() => window.location.reload(), 500),
            onDone: (ephemeral) => onComplete(ephemeral),
            onLegacyChunk: onChunk
        };
        
        while (true) {
            const { done, value } = await reader.read();
            if (done) return gotContent ? onComplete(false) : onError(new Error("No content"));
            
            buffer += decoder.decode(value, { stream: true });
            // split(/\r?\n/) handles both LF (uvicorn default) and CRLF
            // (some Win-side proxies normalize). Herring-table #17.
            const lines = buffer.split(/\r?\n/);
            buffer = lines.pop();
            
            for (const line of lines) {
                if (line.startsWith('data: ')) {
                    try {
                        const data = JSON.parse(line.slice(6));
                        // Same two-shape normalization as streamChat below
                        if (data.type === 'error' || (data.error && !data.type)) {
                            return (await reader.cancel(), onError(new Error(data.error || data.text || 'Stream error')));
                        }

                        const result = processSSEData(data, handlers);
                        if (result.gotContent) gotContent = true;
                        if (result.shouldReturn) {
                            await reader.cancel();
                            return;
                        }
                    } catch (parseErr) {
                        console.error('[SSE] Parse error:', parseErr, 'Line:', line);
                    }
                }
            }
        }
    } catch (e) {
        onError(e.name === 'AbortError' ? new Error('Cancelled') : e);
    } finally {
        if (reader) try { await reader.cancel(); } catch {}
    }
};

// Avatar tag scanner — wraps onChunk to detect <<avatar: trackname>> in streamed responses
// Reads strip_tags setting from avatar plugin state (cached on page load)
window._avatarStripTags = false;
window._avatarUserTags = false;  // gated by the "User tags trigger animations" setting
fetch('/api/plugin/avatar/config').then(r => {
    if (!r.ok) { console.warn('[Avatar] Config fetch failed:', r.status); return {}; }
    return r.json();
}).then(cfg => {
    window._avatarStripTags = cfg?.strip_tags ?? false;
    window._avatarUserTags = cfg?.user_tags ?? false;
}).catch(e => { console.warn('[Avatar] Config fetch error:', e); });

// User-typed avatar tags can also fire animations, when enabled in settings.
// Scans USER_SENT payloads with the same regex as the AI chunk scanner.
on(Events.USER_SENT, (data) => {
    if (!window._avatarUserTags) return;
    const text = data?.text || '';
    if (!text) return;
    const userTagRe = /<<avatar:\s*([a-zA-Z0-9_]+)(?:\s+(once|loop|\d+(?:\.\d+)?s))?>>/g;
    for (const match of text.matchAll(userTagRe)) {
        const track = match[1];
        const mode = (match[2] === 'loop') ? 'loop' : 'once';
        dispatch('avatar_animate', { track, mode });
    }
});

function _wrapChunkWithAvatarScan(onChunk) {
    let scanBuf = '';
    let holdBuf = '';  // text held back while a potential tag is forming
    // Permissive regex: matches `<<avatar: track>>`, `<<avatar: track loop>>`,
    // `<<avatar: track once>>`, and (for backward compat with old chat history)
    // `<<avatar: track 2.5s>>`. The dispatcher treats any captured token that
    // isn't 'loop' as the default 'once' mode, so old syntax keeps its
    // historical behavior and old messages still strip cleanly.
    const tagRe = /<<avatar:\s*([a-zA-Z0-9_]+)(?:\s+(once|loop|\d+(?:\.\d+)?s))?>>/g;
    return (chunk) => {
        // Scan for complete tags
        scanBuf += chunk;
        for (const match of scanBuf.matchAll(tagRe)) {
            const track = match[1];
            const mode = (match[2] === 'loop') ? 'loop' : 'once';
            dispatch('avatar_animate', { track, mode });
        }
        // Carry over any partial tag for the next chunk. Two cases:
        //   (a) Unclosed `<<...` — keep from the last `<<` onwards.
        //   (b) Trailing single `<` — LLM tokenizers commonly emit `<<` as
        //       two separate `<` tokens, so a trailing single `<` could be
        //       the first half of a forming `<<`. Without this branch the
        //       leading `<` was discarded and the next chunk's `<avatar:`
        //       would never reach the regex as `<<avatar:`. 2026-05-27.
        const lastDouble = scanBuf.lastIndexOf('<<');
        if (lastDouble >= 0 && scanBuf.indexOf('>>', lastDouble) < 0) {
            scanBuf = scanBuf.slice(lastDouble);
        } else if (scanBuf.endsWith('<')) {
            scanBuf = '<';
        } else {
            scanBuf = '';
        }

        if (window._avatarStripTags) {
            // Buffer text to avoid showing partial tags
            holdBuf += chunk;
            // Strip complete tags
            holdBuf = holdBuf.replace(tagRe, '');
            // Hold any partial forming tag — both `<<...` and trailing `<`
            // (split-bracket case, same as scanBuf above).
            const partialIdx = holdBuf.lastIndexOf('<<');
            let holdFrom = -1;
            if (partialIdx >= 0 && holdBuf.indexOf('>>', partialIdx) < 0) {
                holdFrom = partialIdx;
            } else if (holdBuf.endsWith('<')) {
                holdFrom = holdBuf.length - 1;
            }
            if (holdFrom >= 0) {
                const safe = holdBuf.slice(0, holdFrom);
                holdBuf = holdBuf.slice(holdFrom);
                if (safe) { onChunk(safe); dispatch('chat_chunk', { text: safe }); }
            } else {
                if (holdBuf) { onChunk(holdBuf); dispatch('chat_chunk', { text: holdBuf }); }
                holdBuf = '';
            }
        } else {
            onChunk(chunk);
            dispatch('chat_chunk', { text: chunk });
        }
    };
}

export const streamChat = async (text, onChunk, onComplete, onError, signal = null, prefill = null, onToolStart = null, onToolEnd = null, onStreamStarted = null, onIterationStart = null, images = null, files = null) => {
    onChunk = _wrapChunkWithAvatarScan(onChunk);
    let reader = null;
    try {
        const body = { text };
        if (prefill) body.prefill = prefill;
        if (images && images.length > 0) body.images = images;
        if (files && files.length > 0) body.files = files;
        
        const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
        const res = await fetch('/api/chat/stream', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
            body: JSON.stringify(body),
            signal
        });
        
        if (!res.ok) {
            if (res.status === 401) {
                window.location.href = '/login';
                return;
            }
            const err = await res.json().catch(() => ({}));
            return onError(new Error(err.error || `HTTP ${res.status}`), res.status);
        }
        
        reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '', gotContent = false;
        
        const handlers = {
            onChunk,
            onToolStart,
            onToolEnd,
            onStreamStarted,
            onIterationStart,
            onReload: () => setTimeout(() => window.location.reload(), 500),
            onDone: (ephemeral) => onComplete(ephemeral),
            onLegacyChunk: onChunk
        };
        
        while (true) {
            const { done, value } = await reader.read();
            if (done) return gotContent ? onComplete(false) : onError(new Error("No content"));
            
            buffer += decoder.decode(value, { stream: true });
            // split(/\r?\n/) handles both LF (uvicorn default) and CRLF
            // (some Win-side proxies normalize). Herring-table #17.
            const lines = buffer.split(/\r?\n/);
            buffer = lines.pop();
            
            for (const line of lines) {
                if (line.startsWith('data: ')) {
                    try {
                        const data = JSON.parse(line.slice(6));
                        // Two error shapes on the wire: {"error": msg} (route
                        // except-handlers, no type field) and {"type":"error",
                        // "text":...} (in-stream refusals like the private-
                        // prompt gate). Both must reach onError or the refusal
                        // is silently swallowed and the caller takes the
                        // success path. The !data.type guard keeps tool_end
                        // (which carries an error:bool flag) off this path.
                        if (data.type === 'error' || (data.error && !data.type)) {
                            return (await reader.cancel(), onError(new Error(data.error || data.text || 'Stream error')));
                        }

                        const result = processSSEData(data, handlers);
                        if (result.gotContent) gotContent = true;
                        if (result.shouldReturn) {
                            await reader.cancel();
                            return;
                        }
                    } catch (parseErr) {
                        console.error('[SSE] Parse error:', parseErr, 'Line:', line);
                    }
                }
            }
        }
    } catch (e) {
        onError(e.name === 'AbortError' ? new Error('Cancelled') : e);
    } finally {
        if (reader) try { await reader.cancel(); } catch {}
    }
};

export const fetchAudio = async (text, signal = null) => {
    try {
        return await fetchWithTimeout('/api/tts', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text, output_mode: 'file' }),
            signal
        }, 120000);
    } catch (e) {
        if (e.message.includes('timeout') && text.length > 500) {
            throw new Error(`TTS timeout (${text.length} chars)`);
        }
        throw e;
    }
};

export const postAudio = async (blob) => {
    const form = new FormData();
    form.append('audio', blob, 'recording.wav');
    try {
        return await fetchWithTimeout('/api/transcribe', { method: 'POST', body: form }, 120000);
    } catch (e) {
        if (e.message.includes('No audio') || e.message.includes('empty')) throw new Error('Audio too small');
        if (e.message.includes('transcription')) throw new Error('Could not understand audio');
        if (e.message.includes('timeout')) throw new Error('Processing timeout');
        throw e;
    }
};

export const editMessage = (role, timestamp, newContent) => 
  fetchWithTimeout('/api/history/messages/edit', { 
    method: 'POST', 
    headers: { 'Content-Type': 'application/json' }, 
    body: JSON.stringify({ role, timestamp, new_content: newContent }) 
  }, 10000);

export const getChatSettings = (chatName) => 
  fetchWithTimeout(`/api/chats/${encodeURIComponent(chatName)}/settings`, {}, 10000);

export const updateChatSettings = (chatName, settings) =>
  fetchWithTimeout(`/api/chats/${encodeURIComponent(chatName)}/settings`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ settings })
  }, 10000);

export const toggleSpice = async (chatName, enabled) => {
  return updateChatSettings(chatName, { spice_enabled: enabled });
};

// Local TTS control (server-side speaker playback)
export const getTtsStatus = () => fetchWithTimeout('/api/tts/status', {}, 2000);
export const stopLocalTts = () => fetchWithTimeout('/api/tts/stop', { method: 'POST' }, 2000);

// Image upload
export const uploadImage = async (file) => {
    const formData = new FormData();
    formData.append('image', file);
    
    const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
    const res = await fetch('/api/upload/image', {
        method: 'POST',
        headers: { 'X-CSRF-Token': csrf },
        body: formData
    });
    
    if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.error || `Upload failed: ${res.status}`);
    }
    
    return res.json();
};