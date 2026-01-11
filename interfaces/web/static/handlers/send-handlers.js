// handlers/send-handlers.js - Send, stop, and input handlers
import * as api from '../api.js';
import * as ui from '../ui.js';
import * as audio from '../audio.js';
import * as chat from '../chat.js';
import { 
    getElements, 
    getIsProc, 
    getTtsEnabled,
    setProc, 
    setAbortController, 
    getAbortController,
    setIsCancelling,
    getIsCancelling,
    refresh,
    setHistLen
} from '../core/state.js';

export async function handleSend() {
    const { input, sendBtn } = getElements();
    const txt = input.value.trim();
    if (!txt) return;
    
    const abortController = new AbortController();
    setAbortController(abortController);
    setIsCancelling(false);
    
    setProc(true);
    input.value = '';
    sendBtn.disabled = true;
    sendBtn.textContent = '...';
    input.dispatchEvent(new Event('input'));
    
    ui.addUserMessage(txt);
    ui.showStatus();
    ui.updateStatus('Connecting...');
    
    try {
        let streamOk = false;
        const audioFn = getTtsEnabled() ? audio.playText : null;
        
        await api.streamChat(
            txt,
            chunk => {
                if (!streamOk) {
                    ui.hideStatus();
                    ui.startStreaming();
                    streamOk = true;
                }
                ui.appendStream(chunk);
            },
            async (ephemeral) => {
                if (getIsCancelling()) {
                    console.log('Stream completed but cancellation in progress - skipping finishStreaming');
                    return;
                }
                
                if (ephemeral) {
                    console.log('[EPHEMERAL] Module response - skipping TTS and swap');
                    await ui.finishStreaming(true);
                    await refresh(false);
                    return;
                }
                
                if (streamOk) {
                    await ui.finishStreaming();
                    await refresh(false);
                    
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
                if (e.message === 'Cancelled') {
                    console.log('Stream cancelled by user');
                    if (streamOk) ui.cancelStreaming();
                    return;
                }
                console.error('Stream failed:', e.message);
                if (streamOk) ui.cancelStreaming();
                ui.showToast(e.message, 'error');
            },
            abortController.signal,
            null,  // prefill
            // Tool event handlers
            (id, name, args) => {
                if (!streamOk) {
                    ui.hideStatus();
                    ui.startStreaming();
                    streamOk = true;
                }
                ui.startTool(id, name, args);
            },
            (id, name, result, error) => {
                ui.endTool(id, name, result, error);
            }
        );
        
        if (streamOk) return null;
    } catch (e) {
        if (e.message !== 'Cancelled') {
            console.error('Error send message:', e);
            ui.showToast(e.message, 'error');
        }
        return null;
    } finally {
        ui.hideStatus();
        sendBtn.disabled = false;
        sendBtn.textContent = 'Send';
        input.focus();
        setProc(false);
    }
}

export async function handleStop() {
    const controller = getAbortController();
    
    if (controller) {
        setIsCancelling(true);
        console.log('Cancellation flag set');
        
        try {
            await api.cancelGeneration();
            console.log('Cancel request sent to backend');
        } catch (e) {
            console.error('Failed to send cancel request:', e);
        }
        
        controller.abort();
        ui.cancelStreaming();
        ui.hideStatus();
        setProc(false);
        ui.showToast('Generation stopped', 'success');
    }
}

export async function triggerSendWithText(text) {
    if (getIsProc()) {
        console.log('Already processing, ignoring transcribed text');
        return;
    }
    
    const { input } = getElements();
    input.value = text;
    input.dispatchEvent(new Event('input'));
    await handleSend();
}

export function handleInput() {
    const { input } = getElements();
    input.parentElement.dataset.replicatedValue = input.value;
}

export function handleKeyDown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        handleSend();
    }
}