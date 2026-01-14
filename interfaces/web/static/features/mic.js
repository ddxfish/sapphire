// features/mic.js - Mic button state, TTS detection, recording handlers
import * as audio from '../audio.js';
import { getElements, getIsProc } from '../core/state.js';

let micIconPollInterval = null;

export function updateMicButtonState() {
    const { micBtn } = getElements();
    if (!micBtn) return;
    
    // Check both browser TTS and local (server speaker) TTS
    const ttsActive = audio.isTtsPlaying() || audio.isLocalTtsPlaying();
    
    if (ttsActive) {
        micBtn.classList.add('tts-playing');
        micBtn.textContent = '⏹';
        micBtn.title = 'Stop TTS';
    } else {
        micBtn.classList.remove('tts-playing');
        micBtn.textContent = '🎤';
        micBtn.title = 'Hold to record';
    }
}

export function startMicIconPolling() {
    if (micIconPollInterval) return;
    micIconPollInterval = setInterval(updateMicButtonState, 200);
    // Also start local TTS status polling
    audio.startLocalTtsPoll();
}

export function stopMicIconPolling() {
    if (micIconPollInterval) {
        clearInterval(micIconPollInterval);
        micIconPollInterval = null;
    }
    audio.stopLocalTtsPoll();
}

export async function handleMicPress() {
    const { micBtn } = getElements();
    
    // If any TTS is playing (browser or local), stop it instead of recording
    if (audio.isTtsPlaying() || audio.isLocalTtsPlaying()) {
        audio.stop(true);
        updateMicButtonState();
        return;
    }
    
    // Normal recording behavior
    await audio.handlePress(micBtn);
}

export async function handleMicRelease(triggerSendFn) {
    const { micBtn } = getElements();
    
    // If TTS was playing (we just stopped it), do nothing on release
    if (micBtn.classList.contains('tts-playing')) {
        updateMicButtonState();
        return;
    }
    
    // Normal recording release
    await audio.handleRelease(micBtn, triggerSendFn);
}

export function handleMicLeave(triggerSendFn) {
    if (audio.getRecState()) {
        const { micBtn } = getElements();
        setTimeout(() => {
            if (audio.getRecState()) audio.handleRelease(micBtn, triggerSendFn);
        }, 500);
    }
}

export function handleVisibilityChange(triggerSendFn) {
    if (document.hidden && audio.getRecState()) {
        const { micBtn } = getElements();
        audio.forceStop(micBtn, triggerSendFn);
    }
}