// features/mic.js - Mic button state, TTS detection, recording handlers
import * as audio from '../audio.js';
import { getElements, getIsProc, getSttEnabled, getSttReady, getConvo } from '../core/state.js';
import { setConvo } from './convo.js';

// Conversation mode owned by THIS machine (local server mic or this browser's
// mic). A phone call is source 'phone' and doesn't touch the mic button.
const convoOn = () => { const c = getConvo(); return c.enabled && (c.source === 'local' || c.source === 'browser'); };

let micIconPollInterval = null;

export function updateMicButtonState() {
    const { micBtn } = getElements();
    if (!micBtn) return;

    // Conversation on: the mic IS the live-session indicator (tap = end).
    // Outranks tts-playing — her speech is the conversation's.
    const conv = getConvo();
    const on = convoOn();
    micBtn.classList.toggle('convo-on', on);
    if (on) {
        micBtn.classList.remove('tts-playing');
        micBtn.textContent = '\u{1F399}';
        micBtn.title = `Conversation on (${conv.source} mic) \u2014 tap to end`;
        return;
    }

    // Check both browser TTS and local (server speaker) TTS
    const ttsActive = audio.isTtsPlaying() || audio.isLocalTtsPlaying();

    if (ttsActive) {
        micBtn.classList.add('tts-playing');
        micBtn.textContent = '⏹';
        micBtn.title = 'Stop TTS';
    } else {
        micBtn.classList.remove('tts-playing');
        const canRecord = getSttEnabled() && getSttReady();
        micBtn.textContent = canRecord ? '🎤' : '🎤';
        micBtn.title = micBtn.dataset.sttTitle || 'Hold to record';
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

    // Conversation on → this press ends it; hold-to-record is suspended
    if (convoOn()) {
        setConvo('off');
        return;
    }

    // If any TTS is playing (browser or local), stop it instead of recording
    if (audio.isTtsPlaying() || audio.isLocalTtsPlaying()) {
        audio.stop(true);
        updateMicButtonState();
        return;
    }
    
    // Block recording if STT is disabled or not initialized
    if (!getSttEnabled() || !getSttReady()) return;

    // Normal recording behavior
    await audio.handlePress(micBtn);
}

export async function handleMicRelease(triggerSendFn) {
    const { micBtn } = getElements();

    // Always defer to audio's own isRec guard (audio.js handleRelease
    // no-ops on !isRec). Gating on the poll-painted classes read a clock up
    // to 200ms behind truth: a press that started recording could meet a
    // release that still saw a stale .convo-on/.tts-playing and never stop
    // the recorder -- hot mic + dead button on touch (S2 #1, hunt
    // 2026-08-30). Presses that didn't record (TTS stop / convo end) fall
    // through harmlessly.
    await audio.handleRelease(micBtn, triggerSendFn);
    updateMicButtonState();
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