// api.js - Backend communication
import { fetchWithTimeout } from './shared/fetch.js';

export { fetchWithTimeout };

export const fetchHistory = () => fetchWithTimeout('/api/history');
export const fetchRawHistory = () => fetchWithTimeout('/api/history/raw');
export const postReset = () => fetchWithTimeout('/api/reset', { method: 'POST' });
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
export const fetchSystemStatus = () => fetchWithTimeout('/api/system/status', {}, 5000);

// Chat management
export const cancelGeneration = () => fetchWithTimeout('/api/cancel', { 
    method: 'POST',
    headers: { 'Content-Type': 'application/json' }
}, 5000);
export const fetchChatList = () => fetchWithTimeout('/api/chats', {}, 10000);
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

export const streamChatContinue = async (text, prefill, onChunk, onComplete, onError, signal = null, onToolStart = null, onToolEnd = null) => {
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
                        
                        // Handle typed events
                        if (data.type === 'content') {
                            gotContent = true;
                            onChunk(data.text || '');
                        } else if (data.type === 'tool_start') {
                            gotContent = true;
                            if (onToolStart) onToolStart(data.id, data.name, data.args);
                        } else if (data.type === 'tool_end') {
                            if (onToolEnd) onToolEnd(data.id, data.name, data.result, data.error);
                        } else if (data.type === 'reload') {
                            setTimeout(() => window.location.reload(), 500);
                            return;
                        }
                        // Legacy: handle old 'chunk' format
                        else if (data.chunk) {
                            if (data.chunk.includes('<<RELOAD_PAGE>>')) {
                                setTimeout(() => window.location.reload(), 500);
                                return;
                            }
                            gotContent = true;
                            onChunk(data.chunk);
                        }
                        
                        if (data.done) return (await reader.cancel(), onComplete(data.ephemeral || false));
                    } catch {}
                }
            }
        }
    } catch (e) {
        onError(e.name === 'AbortError' ? new Error('Cancelled') : e);
    } finally {
        if (reader) try { await reader.cancel(); } catch {}
    }
};

export const streamChat = async (text, onChunk, onComplete, onError, signal = null, prefill = null, onToolStart = null, onToolEnd = null) => {
    let reader = null;
    try {
        const body = prefill ? { text, prefill } : { text };
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
                        
                        // Handle typed events
                        if (data.type === 'content') {
                            gotContent = true;
                            onChunk(data.text || '');
                        } else if (data.type === 'tool_start') {
                            gotContent = true;
                            if (onToolStart) onToolStart(data.id, data.name, data.args);
                        } else if (data.type === 'tool_end') {
                            if (onToolEnd) onToolEnd(data.id, data.name, data.result, data.error);
                        } else if (data.type === 'reload') {
                            setTimeout(() => window.location.reload(), 500);
                            return;
                        }
                        // Legacy: handle old 'chunk' format for backwards compatibility
                        else if (data.chunk) {
                            if (data.chunk.includes('<<RELOAD_PAGE>>')) {
                                setTimeout(() => window.location.reload(), 500);
                                return;
                            }
                            gotContent = true;
                            onChunk(data.chunk);
                        }
                        
                        if (data.done) return (await reader.cancel(), onComplete(data.ephemeral || false));
                    } catch {}
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
            body: JSON.stringify({ text }),
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
        return await fetchWithTimeout('/api/transcribe', { method: 'POST', body: form });
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