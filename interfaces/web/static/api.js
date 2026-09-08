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

export const fetchHistoryFull = async () => {
    const response = await fetchWithTimeout('/api/history');
    // Update context bar if context info is present
    if (response && response.context) {
        updateContextBar(response.context);
    }
    _lastHistoryChatName = response?.chat_name || null;
    // chat_name rides the SAME response as the messages — painters must
    // compare against this, not the module-global above, which any
    // concurrent fetchHistory caller clobbers (hunt 2026-08-17 H12).
    return { messages: response?.messages || response || [],
             chat_name: response?.chat_name || null };
};

export const fetchHistory = async () => (await fetchHistoryFull()).messages;

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
export const repairChat = (name, opts) => fetchWithTimeout(`/api/chats/${encodeURIComponent(name)}/repair`, {
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
    const { onChunk, onToolStart, onToolEnd, onReload, onDone, onLlmDone, onLegacyChunk, onStreamStarted, onIterationStart } = handlers;
    
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
    
    // LLM half of the turn is over (history row + metrics written server-
    // side). The body stays open for the streaming-TTS tail — see _readTurn.
    if (data.type === 'llm_done') {
        if (onLlmDone) onLlmDone(data);
        return { llmDone: true };
    }

    if (data.done) {
        console.log('[SSE] Done received');
        if (onDone) onDone(data.ephemeral || false, data);
        return { shouldReturn: true, isDone: true };
    }
    
    return {};
};

// ---------------------------------------------------------------------------
// /api/chat/stream reader — ONE loop for send / regen / continue.
//
// The awaited promise settles at `llm_done`: the LLM half of the turn is
// over and the server has already written the history row (metrics
// included). Everything after that on the wire is the streaming-TTS tail —
// tts_chunk events arriving as synth finishes, which on a slow CPU can be
// the whole reply's worth of seconds. The tail drains DETACHED: the caller
// gets Send + metrics back at the last word; audio keeps arriving through
// the same event-bus dispatches audio.js already listens to. Belts: a
// `done` with no llm_done before it settles the turn too (tts_streamed
// unknown → whole-blob lane, the old behavior); a body that closes or
// faults mid-tail finalizes the audio queue so the mic ⏹ can't stick.
// 2026-09-08, record tmp/llm-done-split-plan.md.
// ---------------------------------------------------------------------------
const _readTurn = async (reader, handlers, onTurnDone, onError) => {
    const decoder = new TextDecoder();
    let buffer = '', gotContent = false, turnDone = false, tailId = null;
    const finishTurn = (data = {}) => {
        if (turnDone) return;
        turnDone = true;
        onTurnDone(data.ephemeral || false, { ttsStreamed: !!data.tts_streamed });
    };
    // The belts name THIS turn's pump: an id-less end flagged a NEWER
    // stream's queue as ended (mic flicker, false "couldn't play" toast) when
    // a stale tail closed after the next turn began (E1#6).
    const endTail = () => dispatch('tts_stream_end', tailId ? { stream_id: tailId } : {});
    handlers.onLlmDone = finishTurn;
    handlers.onDone = (_ephemeral, data) => finishTurn(data);
    try {
        while (true) {
            const { done, value } = await reader.read();
            if (done) {
                if (turnDone) { endTail(); return; }   // tail closed without `done`
                return gotContent ? finishTurn() : onError(new Error("No content"));
            }

            buffer += decoder.decode(value, { stream: true });
            // split(/\r?\n/) handles both LF (uvicorn default) and CRLF
            // (some Win-side proxies normalize). Herring-table #17.
            const lines = buffer.split(/\r?\n/);
            buffer = lines.pop();

            for (const line of lines) {
                if (!line.startsWith('data: ')) continue;
                let data;
                try { data = JSON.parse(line.slice(6)); }
                catch (parseErr) { console.error('[SSE] Parse error:', parseErr, 'Line:', line); continue; }
                // Two error shapes on the wire: {"error": msg} (route
                // except-handlers, no type field) and {"type":"error",
                // "text":...} (in-stream refusals like the private-
                // prompt gate). Both must reach onError or the refusal
                // is silently swallowed and the caller takes the
                // success path. The !data.type guard keeps tool_end
                // (which carries an error:bool flag) off this path.
                if (data.type === 'error' || (data.error && !data.type)) {
                    const msg = data.error || data.text || 'Stream error';
                    if (turnDone) {
                        // Tail fault: the turn is complete, only audio was lost.
                        dispatch('chat_notice', { message: `TTS tail failed: ${msg}`, severity: 'warning' });
                        endTail();
                        return;
                    }
                    return onError(new Error(msg));
                }
                if (data.type === 'tts_stream_start') tailId = data.stream_id || tailId;
                // A cancel that wasn't THIS tab's Stop (second-tab Stop, mic ⏹ in
                // conversation mode, a barge-in): the route ends the body with a
                // bare {cancelled} and no `done`. Before the turn settled that's
                // a cancelled turn — the same path as our own Stop, NOT the
                // success lane whose whole-blob fallback re-spoke the cut reply
                // from the top (E1#1, 2026-09-08). After llm_done: audio-only cut.
                if (data.cancelled && !data.type) {
                    if (turnDone) { endTail(); return; }
                    turnDone = true;
                    endTail();
                    return onError(new Error('Cancelled'));
                }
                const result = processSSEData(data, handlers);
                if (result.gotContent) gotContent = true;
                if (result.shouldReturn) return;
            }
        }
    } catch (e) {
        if (turnDone) { endTail(); return; }   // dropped mid-tail: audio only
        onError(e.name === 'AbortError' ? new Error('Cancelled') : e);
    } finally {
        try { await reader.cancel(); } catch {}
    }
};

const _streamTurn = async (body, { onChunk, onComplete, onError, signal = null,
                                   onToolStart = null, onToolEnd = null,
                                   onStreamStarted = null, onIterationStart = null }) => {
    onChunk = _wrapChunkWithAvatarScan(onChunk);
    let res;
    try {
        const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
        res = await fetch('/api/chat/stream', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
            body: JSON.stringify(body),
            signal
        });
    } catch (e) {
        return onError(e.name === 'AbortError' ? new Error('Cancelled') : e);
    }
    if (!res.ok) {
        if (res.status === 401) {
            window.location.href = '/login';
            return;
        }
        const err = await res.json().catch(() => ({}));
        return onError(new Error(err.error || `HTTP ${res.status}`), res.status);
    }
    const handlers = {
        onChunk,
        onToolStart,
        onToolEnd,
        onStreamStarted,
        onIterationStart,
        onReload: () => setTimeout(() => window.location.reload(), 500),
        onLegacyChunk: onChunk
    };
    // Settle at llm_done (or error / no-content / reload); the read loop
    // itself runs on to the end of the body for the audio tail.
    await new Promise((settle) => {
        _readTurn(res.body.getReader(), handlers,
                  (ephemeral, meta) => { onComplete(ephemeral, meta); settle(); },
                  (e, code) => { onError(e, code); settle(); })
            .finally(settle);
    });
};

export const streamChatContinue = (text, prefill, onChunk, onComplete, onError, signal = null, onToolStart = null, onToolEnd = null, onStreamStarted = null, onIterationStart = null) =>
    _streamTurn({ text, prefill, skip_user_message: true },
                { onChunk, onComplete, onError, signal, onToolStart, onToolEnd, onStreamStarted, onIterationStart });

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

export const streamChat = (text, onChunk, onComplete, onError, signal = null, prefill = null, onToolStart = null, onToolEnd = null, onStreamStarted = null, onIterationStart = null, images = null, files = null) => {
    const body = { text };
    if (prefill) body.prefill = prefill;
    if (images && images.length > 0) body.images = images;
    if (files && files.length > 0) body.files = files;
    return _streamTurn(body, { onChunk, onComplete, onError, signal, onToolStart, onToolEnd, onStreamStarted, onIterationStart });
};

export const fetchAudio = async (text, signal = null, opts = null) => {
    try {
        // opts: {voice, pitch, speed} — task-TTS carries the task's values in
        // the event payload because global voice is restored before we fetch.
        const body = { text, output_mode: 'file' };
        if (opts?.voice) body.voice = opts.voice;
        if (opts?.pitch != null) body.pitch = opts.pitch;
        if (opts?.speed != null) body.speed = opts.speed;
        return await fetchWithTimeout('/api/tts', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
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
// streamId: the streaming-TTS pump the browser is hearing — the server mutes
// exactly that one (never the next turn still thinking). 2026-09-08.
export const stopLocalTts = (streamId = null) => fetchWithTimeout('/api/tts/stop', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(streamId ? { stream_id: streamId } : {})
}, 2000);

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