// handlers/message-handlers.js - Trash, regenerate, edit, continue, replay handlers
import * as api from '../api.js';
import * as ui from '../ui.js';
import * as audio from '../audio.js';
import * as chat from '../chat.js';
import {
    getIsProc,
    getTtsEnabled,
    getPromptPrivacyRequired,
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
    // Same guard as handleSend — and it must run BEFORE chat.handleRegen,
    // which deletes the old reply before streaming (a backend refusal after
    // that point loses the message).
    if (getPromptPrivacyRequired()) {
        ui.showToast('This prompt is marked private — toggle the eyeball to make this chat private', 'error');
        return;
    }
    console.log(`Regenerating message ${idx}`);

    // Confirm BEFORE registering the abort controller -- while the dialog
    // sat open, the registered controller blinded the cross-tab AI_TYPING
    // mirror (main.js gates on !getAbortController()), so a live foreign
    // turn never flipped isProc and regen proceeded into a 409 AFTER
    // deleting the turn (S1 #3, hunt 2026-08-30).
    if (!confirm('Regenerate this response?')) return;

    // Regen means "that answer was wrong" — cut its voice at the click, not
    // at the new turn's first chunk (a slow CPU = 10s+ of the wrong answer
    // still talking). Send deliberately does NOT do this: she finishes the
    // last answer until the new voice arrives. Krem's ruling A, 2026-09-08.
    audio.stop(true);

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

    // User bubbles carry `content` (the markdown you typed). Assistant turns
    // carry `parts`; the server edits the LAST assistant message of the turn,
    // so show the last content part — that's the text the save overwrites.
    let text = msg.content || '';
    if (msg.role === 'assistant' && Array.isArray(msg.parts)) {
        const last = msg.parts.filter(p => p.type === 'content').pop();
        if (last) text = last.text || '';
    }
    ui.enterEditMode(msgEl, idx, msg.timestamp, text);
    
    document.getElementById('save-edit').onclick = async () => {
        const newText = document.getElementById('edit-textarea').value;
        const timestamp = msgEl.dataset.editTimestamp;

        try {
            console.log('[EDIT DEBUG] Editing message with timestamp:', timestamp);
            await api.editMessage(msg.role, timestamp, newText);
            // Clear .editing BEFORE refreshing — fetchAndRender holds renders
            // while an edit is open, so refresh() would no-op otherwise.
            ui.exitEditMode(msgEl, false);
            await refresh(false);
        } catch (e) {
            console.error('Edit failed:', e);
            ui.showToast(`Edit failed: ${e.message}`, 'error');
            ui.exitEditMode(msgEl, true);  // Restore on error (element still exists)
        }
    };

    document.getElementById('cancel-edit').onclick = () => {
        ui.exitEditMode(msgEl, true);
        refresh(false);  // catch up on refreshes held back during the edit
    };
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