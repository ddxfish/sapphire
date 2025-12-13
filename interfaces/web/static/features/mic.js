// features/mic.js - Mic button state, TTS detection, recording handlers
import * as audio from '../audio.js';
import { getElements, getIsProc } from '../core/state.js';

let micIconPollInterval = null;

export function updateMicButtonState() {
    const { micBtn } = getElements();
    if (!micBtn) return;
    
    if (audio.isTtsPlaying()) {
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
}

export function stopMicIconPolling() {
    if (micIconPollInterval) {
        clearInterval(micIconPollInterval);
        micIconPollInterval = null;
    }
}

export async function handleMicPress() {
    const { micBtn } = getElements();
    
    // If TTS is playing, stop it instead of recording
    if (audio.isTtsPlaying()) {
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