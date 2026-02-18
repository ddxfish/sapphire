// api.js - Backend communication
import { fetchWithTimeout } from './shared/fetch.js';

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

export const fetchHistory = async () => {
    const response = await fetchWithTimeout('/api/history');
    // Update context bar if context info is present
    if (response && response.context) {
        updateContextBar(response.context);
    }
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
export const startStory = (preset) => fetchWithTimeout('/api/story/start', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ preset })
}, 10000);
export const createChat = (name) => fetchWithTimeout('/api/chats', { 
    method: 'POST', 
    headers: { 'Content-Type': 'application/json' }, 
    body: JSON.stringify({ name }) 
}, 10000);
export const deleteChat = (name) => fetchWithTimeout(`/api/chats/${encodeURIComponent(name)}`, { 
    method: 'DELETE' 
}, 10000);
export const activateChat = (name) => fetchWithTimeout(`/api/chats/${encodeURIComponent(name)}/activate`, { 
    method: 'POST' 
}, 10000);
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
    let reader = null;
    try {
        const res = await fetch('/api/chat/stream', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
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
            const lines = buffer.split('\n');
            buffer = lines.pop();
            
            for (const line of lines) {
                if (line.startsWith('data: ')) {
                    try {
                        const data = JSON.parse(line.slice(6));
                        if (data.error) return (await reader.cancel(), onError(new Error(data.error)));
                        
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

export const streamChat = async (text, onChunk, onComplete, onError, signal = null, prefill = null, onToolStart = null, onToolEnd = null, onStreamStarted = null, onIterationStart = null, images = null, files = null) => {
    let reader = null;
    try {
        const body = { text };
        if (prefill) body.prefill = prefill;
        if (images && images.length > 0) body.images = images;
        if (files && files.length > 0) body.files = files;
        
        const res = await fetch('/api/chat/stream', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
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
            const lines = buffer.split('\n');
            buffer = lines.pop();
            
            for (const line of lines) {
                if (line.startsWith('data: ')) {
                    try {
                        const data = JSON.parse(line.slice(6));
                        if (data.error) return (await reader.cancel(), onError(new Error(data.error)));
                        
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
    form.append('audio', blob, 'recording.webm');
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
    
    const res = await fetch('/api/upload/image', {
        method: 'POST',
        body: formData
    });
    
    if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.error || `Upload failed: ${res.status}`);
    }
    
    return res.json();
};