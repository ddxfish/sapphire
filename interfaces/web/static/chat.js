//  chat.js - Chat logic
import * as ui from './ui.js';
import * as api from './api.js';
import * as audio from './audio.js';

const handleError = (e, action) => {
    if (e.message === 'Cancelled') return console.log(`${action} cancelled by user`);
    console.error(`Error ${action}:`, e);
    ui.showToast(e.message, 'error');
};

let _histPaintSeq = 0;

// F2 degraded latch: warn ONCE per chat per page-load when the open chat has
// unreadable rows (server marks /api/history with `degraded`). Sticky toast —
// the chat is read-only and new replies aren't persisting; that must not
// scroll away. Healthy fetch clears the memo so a re-degraded chat warns again.
const _degradedWarned = new Set();
const noteDegraded = (name, deg) => {
    if (!name) return;
    if (!deg?.skipped) { _degradedWarned.delete(name); return; }
    if (_degradedWarned.has(name)) return;
    _degradedWarned.add(name);
    ui.showToast(
        `⚠ ${deg.skipped} message(s) in this chat could not be read — the chat `
        + `is READ-ONLY and new replies are NOT being saved. Repair it in Chat `
        + `Manager, or clear/start a new chat.`, 'warning', 0);
};

// A refresh that arrived while an edit was open. Save/cancel swap only the
// edited bubble; they replay a held refresh so no event is lost.
let _heldRefresh = false;
export const takeHeldRefresh = () => { const h = _heldRefresh; _heldRefresh = false; return h; };

export const fetchAndRender = async (playAudio = false, audioFn, lastLen) => {
    // Hold the render while a message edit is open — renderHistory rebuilds the
    // whole transcript, so a background refresh (SSE event, autoRefresh poll)
    // would destroy the edit textarea and the unsaved text in it. The editor's
    // exit path replays it (takeHeldRefresh).
    if (document.querySelector('#chat-container .message.editing')) {
        _heldRefresh = true;
        return { hist: null, len: lastLen };
    }
    try {
        // Paint-seq + same-response binding (hunt 2026-08-17 H12): the old
        // guard compared two globals that six concurrent fetchHistory
        // callers clobber — a private chat's late fetch could paint its
        // transcript into a tab showing another chat. Only the LATEST
        // fetchAndRender paints, and the chat name comes from the same
        // response as the messages it labels.
        const seq = ++_histPaintSeq;
        const { messages: hist, chat_name: returnedChat, degraded } = await api.fetchHistoryFull();
        if (seq !== _histPaintSeq) return { hist: null, len: lastLen };
        noteDegraded(returnedChat, degraded);

        // Guard: skip render if backend is temporarily on a different chat
        // (e.g. continuity foreground task switched the active chat)
        const expectedChat = document.getElementById('chat-select')?.value;
        if (returnedChat && expectedChat && returnedChat !== expectedChat) {
            return { hist: null, len: lastLen };
        }

        const isNew = hist.length > lastLen;
        ui.renderHistory(hist);
        if (playAudio && isNew && audioFn && hist.length > 0 && typeof audioFn === 'function') {
            const last = hist[hist.length - 1];
            if (last.role === 'assistant') await audioFn(last.content);
        }
        return { hist, len: hist.length };
    } catch (e) {
        handleError(e, 'load history');
        return { hist: null, len: lastLen };
    }
};

export const handleTrash = async (idx, refreshFn) => {
    console.log(`[TRASH DEBUG] Starting trash at index ${idx}`);
    try {
        console.log('[TRASH DEBUG] Fetching history...');
        const hist = await api.fetchHistory();
        console.log(`[TRASH DEBUG] Got history, length: ${hist.length}`);
        
        if (idx >= hist.length) {
            console.log('[TRASH DEBUG] Index out of bounds');
            return null;
        }
        
        const clicked = hist[idx];
        console.log(`[TRASH DEBUG] Clicked message role: ${clicked.role}`);
        
        const messagesToDelete = hist.length - idx;
        const confirmMsg = `Delete ${messagesToDelete} message${messagesToDelete === 1 ? '' : 's'}?`;
        console.log(`[TRASH DEBUG] Showing confirm: ${confirmMsg}`);
        
        if (!confirm(confirmMsg)) {
            console.log('[TRASH DEBUG] User cancelled');
            return null;
        }
        
        if (clicked.role === 'user') {
            // Delete user message and everything after
            console.log('[TRASH DEBUG] Deleting from user message...');
            await api.removeFromUserMessage(clicked.content);
        } else {
            // Delete assistant message and everything after (leaves user message intact)
            console.log('[TRASH DEBUG] Deleting from assistant message...');
            await api.removeFromAssistant(clicked.timestamp);
        }
        
        console.log('[TRASH DEBUG] Messages removed, refreshing...');
        const len = await refreshFn(false);
        console.log(`[TRASH DEBUG] Refreshed, new length: ${len}`);
        
        ui.forceUpdateToolbars();
        console.log('[TRASH DEBUG] Toolbars updated, done!');
        return len;
    } catch (e) {
        console.error('[TRASH DEBUG] Error caught:', e);
        handleError(e, 'delete messages');
        return null;
    }
};

export const handleSend = async (input, btn, setProc, audioFn, refreshFn, abortController = null, isCancellingGetter = null) => {
    const txt = input.value.trim();
    if (!txt) return;
    
    setProc(true);
    input.value = '';
    btn.disabled = true;
    btn.textContent = '...';
    input.dispatchEvent(new Event('input'));
    
    ui.addUserMessage(txt);
    ui.showStatus();
    ui.updateStatus('Connecting...');
    
    try {
        let streamOk = false;
        
        await api.streamChat(
            txt,
            chunk => {
                if (!streamOk) {
                    ui.updateStatus('Generating...');
                    ui.startStreaming();
                    streamOk = true;
                }
                ui.appendStream(chunk);
                if (ui.hasVisibleContent()) {
                    ui.hideStatus();
                }
            },
            async (ephemeral, { ttsStreamed = false } = {}) => {
                const myStreamId = ui.getCurrentStreamId();   // see handleRegen
                if (isCancellingGetter && isCancellingGetter()) {
                    console.log('Stream completed but cancellation in progress - skipping finishStreaming');
                    return;
                }

                // Ephemeral responses: just clean up, no TTS or history swap
                if (ephemeral) {
                    console.log('[EPHEMERAL] Module response - skipping TTS and swap');
                    await ui.finishStreaming(true);
                    if (refreshFn) await refreshFn(false);
                    return;
                }

                if (streamOk) {
                    await ui.finishStreaming();
                    // Note: finishStreaming already syncs with history - no refresh needed

                    // Whole-blob playback ONLY when the server's streaming-TTS
                    // pump never ran this turn (see send-handlers.handleSend).
                    if (!ttsStreamed && audioFn && ui.getCurrentStreamId() === myStreamId) {
                        const el = document.querySelector('.message.assistant:last-child .message-content');
                        if (el) audioFn(ui.extractProseText(el));
                    }
                }
            },
            async (e, statusCode) => {
                if (e.message === 'Cancelled') return (console.log('Stream cancelled by user'), streamOk && ui.cancelStreaming());
                console.error('Stream failed:', e.message);
                streamOk && ui.cancelStreaming();
                handleError(e, 'stream');
            },
            abortController ? abortController.signal : null,
            null,  // prefill
            // Tool event handlers
            (id, name, args) => {
                if (!streamOk) {
                    ui.updateStatus('Generating...');
                    ui.startStreaming();
                    streamOk = true;
                }
                ui.startTool(id, name, args);
            },
            (id, name, result, error) => {
                ui.endTool(id, name, result, error);
            },
            // Stream started handler
            () => {
                ui.updateStatus('Processing...');
            },
            // Iteration start handler (after tool calls)
            (iteration) => {
                if (iteration > 1) {
                    ui.showStatus();
                    ui.updateStatus('Generating...');
                }
            }
        );
        
        if (streamOk) return null;
    } catch (e) {
        if (e.message !== 'Cancelled') handleError(e, 'send message');
        return null;
    } finally {
        ui.hideStatus();
        btn.disabled = false;
        btn.textContent = 'Send';
        input.focus();
        setProc(false);
    }
};

export const handleRegen = async (idx, setProc, audioFn, refreshFn, abortController = null, isCancellingGetter = null) => {
    console.log(`[REGEN DEBUG] Starting regen at index ${idx}`);
    try {
        console.log('[REGEN DEBUG] Fetching history...');
        const hist = await api.fetchHistory();
        console.log(`[REGEN DEBUG] Got history, length: ${hist.length}`);
        
        if (idx >= hist.length) {
            console.log('[REGEN DEBUG] Index out of bounds');
            return null;
        }
        
        const clicked = hist[idx];
        console.log(`[REGEN DEBUG] Clicked message role: ${clicked.role}`);
        
        let userMessage;
        
        if (clicked.role === 'user') {
            userMessage = clicked.content;
            console.log(`[REGEN DEBUG] User message selected`);
        } else {
            console.log('[REGEN DEBUG] Assistant message, finding previous user message...');
            let userIdx = -1;
            for (let i = idx - 1; i >= 0; i--) {
                if (hist[i].role === 'user') {
                    userIdx = i;
                    userMessage = hist[i].content;
                    break;
                }
            }
            console.log(`[REGEN DEBUG] Found user message at index: ${userIdx}`);
            
            if (!userMessage) {
                console.log('[REGEN DEBUG] No user text found!');
                ui.showToast('No user message found to regenerate from', 'error');
                return null;
            }
        }
        
        console.log(`[REGEN DEBUG] Will regenerate from: well, no logging..."`);
        
        // (confirm happens in message-handlers.handleRegen, BEFORE the
        // abort controller is registered -- S1 #3)
        console.log('[REGEN DEBUG] Setting proc...');
        setProc(true);
        
        console.log('[REGEN DEBUG] Removing from user message...');
        await api.removeFromUserMessage(userMessage);
        console.log('[REGEN DEBUG] Messages removed, refreshing...');
        await refreshFn(false);
        
        console.log('[REGEN DEBUG] Adding user message...');
        ui.addUserMessage(userMessage);
        ui.showStatus();
        ui.updateStatus('Regenerating...');
        
        console.log('[REGEN DEBUG] Starting stream...');
        let streamOk = false;
        await api.streamChat(
            userMessage,
            chunk => {
                if (!streamOk) {
                    console.log('[REGEN DEBUG] First chunk, starting stream');
                    ui.updateStatus('Generating...');
                    ui.startStreaming();
                    streamOk = true;
                }
                ui.appendStream(chunk);
                if (ui.hasVisibleContent()) {
                    ui.hideStatus();
                }
            },
            async (ephemeral, { ttsStreamed = false } = {}) => {
                // Captured synchronously: at llm_done this is OUR stream. Send
                // returns during finishStreaming's sleep (llm-done split), so
                // a newer turn can bump the id before the fallback below runs.
                const myStreamId = ui.getCurrentStreamId();
                if (isCancellingGetter && isCancellingGetter()) {
                    console.log('Regen completed but cancellation in progress - skipping finishStreaming');
                    return;
                }
                
                // Ephemeral responses: just clean up, no TTS or history swap
                if (ephemeral) {
                    console.log('[EPHEMERAL] Module response - skipping TTS and swap');
                    await ui.finishStreaming(true);
                    if (refreshFn) await refreshFn(false);
                    return;
                }
                
                console.log('[REGEN DEBUG] Stream complete');
                if (streamOk) {
                    await ui.finishStreaming();
                    // Note: finishStreaming already syncs with history - no refresh needed

                    // Whole-blob playback ONLY when the server's streaming-TTS
                    // pump never ran this turn (see send-handlers.handleSend).
                    if (!ttsStreamed && audioFn && ui.getCurrentStreamId() === myStreamId) {
                        const el = document.querySelector('.message.assistant:last-child .message-content');
                        if (el) audioFn(ui.extractProseText(el));
                    }
                }
            },
            async (e, statusCode) => {
                if (e.message === 'Cancelled') return (console.log('[REGEN DEBUG] Stream cancelled by user'), streamOk && ui.cancelStreaming());
                if (statusCode === 409) {
                    // One-turn gate refused AFTER the old turn was deleted --
                    // don't lose the words: optimistic bubble off, text back
                    // in the box (same shape as handleSend's 409 undo).
                    // S1 #3, hunt 2026-08-30.
                    const bubbles = document.querySelectorAll('#chat-container .message.user');
                    bubbles[bubbles.length - 1]?.remove();
                    const inp = document.getElementById('prompt-input');
                    if (inp) { inp.value = userMessage; inp.dispatchEvent(new Event('input')); }
                    ui.showToast(e.message, 'error');
                    return;
                }
                console.error('[REGEN DEBUG] Stream failed:', e.message);
                streamOk && ui.cancelStreaming();
                handleError(e, 'regenerate');
            },
            abortController ? abortController.signal : null,
            null,  // prefill
            // Tool event handlers
            (id, name, args) => {
                if (!streamOk) {
                    ui.updateStatus('Generating...');
                    ui.startStreaming();
                    streamOk = true;
                }
                ui.startTool(id, name, args);
            },
            (id, name, result, error) => {
                ui.endTool(id, name, result, error);
            },
            // Stream started handler
            () => {
                ui.updateStatus('Processing...');
            },
            // Iteration start handler (after tool calls)
            (iteration) => {
                if (iteration > 1) {
                    ui.showStatus();
                    ui.updateStatus('Generating...');
                }
            }
        );
        
        console.log('[REGEN DEBUG] Counting messages...');
        if (streamOk) {
            const messageCount = document.querySelectorAll('#chat-container .message:not(.status):not(.error)').length;
            console.log(`[REGEN DEBUG] Done! Message count: ${messageCount}`);
            return messageCount;
        }
        
        const len = await refreshFn(false);
        console.log(`[REGEN DEBUG] Fallback done, length: ${len}`);
        return len;
    } catch (e) {
        if (e.message !== 'Cancelled') {
            console.error('[REGEN DEBUG] Error caught:', e);
            handleError(e, 'regenerate');
        }
        return null;
    } finally {
        console.log('[REGEN DEBUG] Finally block - hiding status and unsetting proc');
        ui.hideStatus();
        setProc(false);
    }
};

export const autoRefresh = async (isProc, lastLen, sceneUpdateFn) => {
    if (isProc) return lastLen;
    try {
        // Single call to unified status - gets message_count, updates scene
        if (sceneUpdateFn) {
            const status = await sceneUpdateFn();
            // Check if message count changed
            const messageCount = status?.message_count ?? lastLen;
            if (messageCount > lastLen) {
                // New messages - fetch full history for rendering
                const { len } = await fetchAndRender(false);
                return len;
            }
            return messageCount;
        }
        // Fallback if no scene function
        const hist = await api.fetchHistory();
        if (hist.length > lastLen) {
            const { len } = await fetchAndRender(false);
            return len;
        }
        return lastLen;
    } catch {
        return lastLen;
    }
};

// In-place Continue (2026-09-10). The reply's row never leaves history: the
// engine reads it as the prefill, sends history with nothing appended, and
// edits the row on success — a Stop that generated nothing changes nothing.
// On the page the existing bubble is adopted as the streaming message, so
// the tool half of a turn stays put and the sentence carries on in place.
export const handleContinue = async (idx, setProc, audioFn, refreshFn, abortController = null, isCancellingGetter = null) => {
    try {
        const hist = await api.fetchHistory();
        const clicked = hist[idx];
        if (!clicked || clicked.role !== 'assistant' || idx !== hist.length - 1) {
            ui.showToast('Continue works on the last reply only', 'error');
            return null;
        }
        // The raw tail row is what the engine resumes from; its last paragraph
        // seeds the on-page paragraph so the sentence continues visibly.
        const raw = await api.fetchRawHistory();
        const tail = raw[raw.length - 1];
        const prose = (tail && tail.role === 'assistant' && !tail.tool_calls
                       && typeof tail.content === 'string') ? tail.content : '';
        if (!prose.trim()) {
            ui.showToast('Nothing to continue — that reply ended in a tool call', 'error');
            return null;
        }
        if (!confirm('Continue this assistant message?')) return null;

        setProc(true);
        const msgEl = document.querySelectorAll('#chat-container .message:not(.status):not(.error)')[idx];
        if (!msgEl) {
            ui.showToast('Reply not on screen — refresh and try again', 'error');
            return null;
        }
        const paras = prose.trim().split(/\n\s*\n/);
        ui.continueStreaming(msgEl, paras[paras.length - 1].trim());
        ui.showStatus();
        ui.updateStatus('Continuing...');

        await api.streamChatContinue(
            clicked.timestamp,
            chunk => {
                ui.appendStream(chunk);
                if (ui.hasVisibleContent()) ui.hideStatus();
            },
            async (ephemeral, { ttsStreamed = false } = {}) => {
                const myStreamId = ui.getCurrentStreamId();   // see handleRegen
                if (isCancellingGetter && isCancellingGetter()) return;
                await ui.finishStreaming();   // swaps the bubble for the saved row
                // Whole-blob playback ONLY when the server's streaming-TTS
                // pump never ran this turn (see send-handlers.handleSend).
                if (!ttsStreamed && audioFn && ui.getCurrentStreamId() === myStreamId) {
                    const el = document.querySelector('.message.assistant:last-child .message-content');
                    if (el) audioFn(ui.extractProseText(el));
                }
            },
            async (e) => {
                ui.cancelStreaming();   // keeps what was rendered; the row on disk is the truth
                if (e.message === 'Cancelled') return;
                if (refreshFn) await refreshFn(true);
                handleError(e, 'continue');
            },
            abortController ? abortController.signal : null,
            (id, name, args) => ui.startTool(id, name, args),
            (id, name, result, error) => ui.endTool(id, name, result, error),
            () => ui.updateStatus('Processing...'),
            (iteration) => {
                if (iteration > 1) {
                    ui.showStatus();
                    ui.updateStatus('Generating...');
                }
            }
        );

        return document.querySelectorAll('#chat-container .message:not(.status):not(.error)').length;
    } catch (e) {
        if (e.message !== 'Cancelled') handleError(e, 'continue');
        return null;
    } finally {
        ui.hideStatus();
        setProc(false);
    }
};
