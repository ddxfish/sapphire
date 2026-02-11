// handlers/message-handlers.js - Trash, regenerate, edit, continue, replay handlers
import * as api from '../api.js';
import * as ui from '../ui.js';
import * as audio from '../audio.js';
import * as chat from '../chat.js';
import { 
    getIsProc, 
    getTtsEnabled,
    setProc, 
    setAbortController, 
    setIsCancelling,
    getIsCancelling,
    refresh,
    setHistLen,
    getHistLen
} from '../core/state.js';

export async function handleTrash(idx) {
    console.log(`Trashing from message ${idx}`);
    const len = await chat.handleTrash(idx, refresh);
    if (len !== null) setHistLen(len);
}

export async function handleRegen(idx) {
    if (getIsProc()) {
        console.log('Regenerate blocked: isProc is true');
        return;
    }
    console.log(`Regenerating message ${idx}`);
    
    const abortController = new AbortController();
    setAbortController(abortController);
    setIsCancelling(false);
    
    const audioFn = getTtsEnabled() ? audio.playText : null;
    
    const len = await chat.handleRegen(
        idx, 
        setProc, 
        audioFn, 
        refresh, 
        abortController,
        getIsCancelling
    );
    
    if (len !== null) setHistLen(len);
}

export async function handleEdit(idx) {
    const hist = await api.fetchHistory();
    const msg = hist[idx];
    const msgEl = document.querySelectorAll('#chat-container .message:not(.status):not(.error)')[idx];
    
    ui.enterEditMode(msgEl, idx, msg.timestamp);
    
    document.getElementById('save-edit').onclick = async () => {
        const newText = document.getElementById('edit-textarea').value;
        const timestamp = msgEl.dataset.editTimestamp;

        try {
            console.log('[EDIT DEBUG] Editing message with timestamp:', timestamp);
            await api.editMessage(msg.role, timestamp, newText);
            // refresh() rebuilds DOM, so exitEditMode not needed - new elements won't have edit state
            await refresh(false);
        } catch (e) {
            console.error('Edit failed:', e);
            ui.showToast(`Edit failed: ${e.message}`, 'error');
            ui.exitEditMode(msgEl, true);  // Restore on error (element still exists)
        }
    };
    
    document.getElementById('cancel-edit').onclick = () => ui.exitEditMode(msgEl, true);
}

export async function handleContinue(idx) {
    if (getIsProc()) {
        console.log('Continue blocked: isProc is true');
        return;
    }
    console.log(`Continuing message ${idx}`);
    
    const abortController = new AbortController();
    setAbortController(abortController);
    setIsCancelling(false);
    
    const audioFn = getTtsEnabled() ? audio.playText : null;
    
    const len = await chat.handleContinue(
        idx, 
        setProc, 
        audioFn, 
        refresh, 
        abortController,
        getIsCancelling
    );
    
    if (len !== null) setHistLen(len);
}

export async function handleReplay(idx) {
    if (audio.isTtsPlaying()) {
        audio.stop(true);
        return;
    }
    console.log(`Replaying TTS for message ${idx}`);
    await audio.replayTts(idx);
}

export function handleToolbar(action, idx) {
    if (action === 'trash') handleTrash(idx);
    else if (action === 'regenerate') handleRegen(idx);
    else if (action === 'continue') handleContinue(idx);
    else if (action === 'edit') handleEdit(idx);
    else if (action === 'replay') handleReplay(idx);
}

export async function handleAutoRefresh() {
    // Skip if SSE is connected — real-time events handle all updates
    if (window.eventBus?.isConnected?.()) return;
    const histLen = getHistLen();
    const len = await chat.autoRefresh(getIsProc(), histLen, async () => {
        // Import dynamically to avoid circular dep
        const scene = await import('../features/scene.js');
        return scene.updateScene();
    });
    setHistLen(len);
}