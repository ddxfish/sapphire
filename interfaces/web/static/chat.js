// chat.js - Chat logic
import * as ui from './ui.js';
import * as api from './api.js';

const handleError = (e, action) => {
    if (e.message === 'Cancelled') return console.log(`${action} cancelled by user`);
    console.error(`Error ${action}:`, e);
    ui.showToast(e.message, 'error');
};

export const fetchAndRender = async (playAudio = false, audioFn, lastLen) => {
    try {
        const hist = await api.fetchHistory();
        const isNew = hist.length > lastLen;
        ui.renderHistory(hist);
        if (playAudio && isNew && audioFn && hist.length > 0 && typeof playTextFn === 'function') {
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
            async (ephemeral) => {
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
                    
                    setTimeout(() => {
                        if (audioFn) {
                            const el = document.querySelector('.message.assistant:last-child .message-content');
                            if (el) {
                                const prose = ui.extractProseText(el);
                                audioFn(prose);
                            }
                        }
                    }, 200);
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
        
        if (!confirm('Regenerate this response?')) {
            console.log('[REGEN DEBUG] User cancelled');
            return null;
        }
        
        console.log('[REGEN DEBUG] User confirmed, setting proc...');
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
            async (ephemeral) => {
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
                    
                    setTimeout(() => {
                        if (audioFn) {
                            const el = document.querySelector('.message.assistant:last-child .message-content');
                            if (el) {
                                const prose = ui.extractProseText(el);
                                audioFn(prose);
                            }
                        }
                    }, 200);
                }
            },
            async (e, statusCode) => {
                if (e.message === 'Cancelled') return (console.log('[REGEN DEBUG] Stream cancelled by user'), streamOk && ui.cancelStreaming());
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

// Add this new function to chat.js
export const handleContinue = async (idx, setProc, audioFn, refreshFn, abortController = null, isCancellingGetter = null) => {
    console.log(`[CONTINUE DEBUG] Starting continue at index ${idx}`);
    try {
        console.log('[CONTINUE DEBUG] Fetching history...');
        const hist = await api.fetchHistory();
        console.log(`[CONTINUE DEBUG] Got history, length: ${hist.length}`);
        
        if (idx >= hist.length) {
            console.log('[CONTINUE DEBUG] Index out of bounds');
            return null;
        }
        
        const clicked = hist[idx];
        console.log(`[CONTINUE DEBUG] Clicked message role: ${clicked.role}`);
        
        if (clicked.role !== 'assistant') {
            console.log('[CONTINUE DEBUG] Can only continue assistant messages');
            ui.showToast('Can only continue assistant messages', 'error');
            return null;
        }
        
        console.log('[CONTINUE DEBUG] Finding parent user message...');
        let userMessage;
        for (let i = idx - 1; i >= 0; i--) {
            if (hist[i].role === 'user') {
                userMessage = hist[i].content;
                break;
            }
        }
        
        if (!userMessage) {
            console.log('[CONTINUE DEBUG] No user text found!');
            ui.showToast('No parent user message found', 'error');
            return null;
        }
        
        const rawHistory = await api.fetchRawHistory();
        
        let prefillContent = '';
        const timestamp = clicked.timestamp;
        
        let lastAssistantInTurn = null;
        for (let i = 0; i < rawHistory.length; i++) {
            const msg = rawHistory[i];
            if (msg.role === 'assistant' && msg.timestamp === timestamp) {
                lastAssistantInTurn = msg;
                for (let j = i + 1; j < rawHistory.length; j++) {
                    if (rawHistory[j].role === 'user') break;
                    if (rawHistory[j].role === 'assistant') {
                        lastAssistantInTurn = rawHistory[j];
                    }
                }
                break;
            }
        }
        
        if (lastAssistantInTurn) {
            prefillContent = lastAssistantInTurn.content || '';
        } else {
            if (clicked.parts) {
                const contentParts = clicked.parts.filter(p => p.type === 'content');
                prefillContent = contentParts.map(p => p.text).join('\n\n');
            } else {
                prefillContent = clicked.content || '';
            }
        }
        
        const userPreview = userMessage.substring(0, 50);
        const prefillPreview = prefillContent.substring(0, 50);
        console.log(`[CONTINUE DEBUG] Will continue`);
        
        if (!confirm('Continue this assistant message?')) {
            console.log('[CONTINUE DEBUG] User cancelled');
            return null;
        }
        
        console.log('[CONTINUE DEBUG] User confirmed, setting proc...');
        setProc(true);
        
        console.log('[CONTINUE DEBUG] Removing last assistant message from history...');
        await api.removeLastAssistant(timestamp);
        
        console.log('[CONTINUE DEBUG] Removing message element from DOM...');
        const messages = document.querySelectorAll('#chat-container .message:not(.status):not(.error)');
        if (idx < messages.length) {
            messages[idx].remove();
            console.log('[CONTINUE DEBUG] Message element removed from DOM');
        }
        
        ui.showStatus();
        ui.updateStatus('Continuing...');
        
        console.log('[CONTINUE DEBUG] Starting stream with prefill (skip_user_message=true)...');
        let streamOk = false;
        
        await api.streamChatContinue(
            userMessage,
            prefillContent,
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
            async (ephemeral) => {
                if (isCancellingGetter && isCancellingGetter()) {
                    console.log('Continue completed but cancellation in progress - skipping finishStreaming');
                    return;
                }
                
                // Ephemeral responses: just clean up, no TTS or history swap
                if (ephemeral) {
                    console.log('[EPHEMERAL] Module response - skipping TTS and swap');
                    await ui.finishStreaming(true);
                    if (refreshFn) await refreshFn(false);
                    return;
                }
                
                console.log('[CONTINUE DEBUG] Stream complete');
                if (streamOk) {
                    await ui.finishStreaming();
                    // Note: finishStreaming already syncs with history - no refresh needed
                    
                    setTimeout(() => {
                        if (audioFn) {
                            const el = document.querySelector('.message.assistant:last-child .message-content');
                            if (el) {
                                const prose = ui.extractProseText(el);
                                audioFn(prose);
                            }
                        }
                    }, 200);
                }
            },
            async (e, statusCode) => {
                if (e.message === 'Cancelled') return (console.log('[CONTINUE DEBUG] Stream cancelled by user'), streamOk && ui.cancelStreaming());
                console.error('[CONTINUE DEBUG] Stream failed:', e.message);
                streamOk && ui.cancelStreaming();
                handleError(e, 'continue');
            },
            abortController ? abortController.signal : null,
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
        
        console.log('[CONTINUE DEBUG] Counting messages...');
        if (streamOk) {
            const messageCount = document.querySelectorAll('#chat-container .message:not(.status):not(.error)').length;
            console.log(`[CONTINUE DEBUG] Done! Message count: ${messageCount}`);
            return messageCount;
        }
        
        const len = await refreshFn(false);
        console.log(`[CONTINUE DEBUG] Fallback done, length: ${len}`);
        return len;
    } catch (e) {
        if (e.message !== 'Cancelled') {
            console.error('[CONTINUE DEBUG] Error caught:', e);
            handleError(e, 'continue');
        }
        return null;
    } finally {
        console.log('[CONTINUE DEBUG] Finally block - hiding status and unsetting proc');
        ui.hideStatus();
        setProc(false);
    }
};