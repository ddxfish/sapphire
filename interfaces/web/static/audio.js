// audio.js - Audio lifecycle
import * as ui from './ui.js';
import * as api from './api.js';

let recorder, chunks = [];
let player, blobUrl, ttsCtrl;
let isRec = false, isStreaming = false;

// Volume state
let volume = 1.0;
let muted = false;

// Volume control exports
export const setVolume = (val) => {
    volume = Math.max(0, Math.min(1, val));
    if (player) player.volume = muted ? 0 : volume;
};

export const setMuted = (val) => {
    muted = val;
    if (player) player.volume = muted ? 0 : volume;
};

export const getVolume = () => volume;
export const isMuted = () => muted;

const cleanup = () => {
    if (blobUrl) {
        try { URL.revokeObjectURL(blobUrl); } catch {}
        blobUrl = null;
    }
};

export const stop = (force = false) => {
    if (isStreaming && !force) return;
    if (ttsCtrl) {
        ttsCtrl.abort();
        ttsCtrl = null;
    }
    if (player) {
        player.pause();
        player.onended = null;
        player.onerror = null;
        player.src = '';
        player = null;
    }
    isStreaming = false;
    cleanup();
};

export const isTtsPlaying = () => isStreaming;

export const playText = async (txt) => {
    stop(true);
    isStreaming = true;
    
    // Remove think blocks (both formats + orphaned)
    let clean = txt;
    clean = clean.replace(/<(?:seed:)?think>.*?<\/(?:seed:think|seed:cot_budget_reflect|think)>\s*/gs, '');
    
    const orphans = [...clean.matchAll(/<\/(?:seed:think|seed:cot_budget_reflect|think)>/g)];
    if (orphans.length > 0) {
        const last = orphans[orphans.length - 1];
        clean = clean.substring(last.index + last[0].length);
    }
    
    // Filter paragraphs
    const paras = clean.split(/\n\s*\n/).filter(p => {
        const t = p.trim();
        return !t.match(/^[🧧🌁🧠💾🧠⚠️]/);
    });
    
    clean = paras.join('\n\n').trim().replace(/^---\s*$/gm, '').trim();
    
    if (!clean) {
        isStreaming = false;
        return;
    }
    
    ui.updateStatus('Generating audio...');
    
    try {
        const blob = await api.fetchAudio(clean, null);
        blobUrl = URL.createObjectURL(blob);
        player = new Audio(blobUrl);
        
        // Apply volume settings
        player.volume = muted ? 0 : volume;
        
        player.onended = () => {
            isStreaming = false;
            ui.hideStatus();
            cleanup();
        };
        
        player.onerror = e => {
            console.error('Audio error:', e);
            isStreaming = false;
            ui.hideStatus();
        };
        
        await player.play();
        ui.hideStatus();
    } catch (e) {
        isStreaming = false;
        ui.hideStatus();
        if (!e.message?.includes('cancelled') && !e.message?.includes('aborted') && 
            !e.name?.includes('NotAllowedError') && !e.message?.includes('autoplay')) {
            ui.showToast(`Audio error: ${e.message}`, 'error');
        }
    }
};

const startRec = async () => {
    try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        recorder = new MediaRecorder(stream);
        recorder.ondataavailable = e => chunks.push(e.data);
        recorder.start();
        return true;
    } catch {
        alert('Mic access denied');
        return false;
    }
};

const stopRec = () => new Promise(resolve => {
    if (recorder && recorder.state === 'recording') {
        recorder.onstop = () => {
            const blob = new Blob(chunks, { type: 'audio/webm' });
            chunks = [];
            recorder.stream.getTracks().forEach(t => t.stop());
            resolve(blob);
        };
        recorder.stop();
    } else {
        resolve(null);
    }
});

export const handlePress = async (btn) => {
    if (isRec) return;
    const ok = await startRec();
    if (ok) {
        isRec = true;
        btn.classList.add('recording');
        ui.showStatus();
        ui.updateStatus('Recording...');
    }
};

export const handleRelease = async (btn, triggerSendFn) => {
    if (!isRec) return;
    isRec = false;
    const blob = await stopRec();
    btn.classList.remove('recording');
    
    if (blob && blob.size > 1000) {
        ui.updateStatus('Transcribing...');
        try {
            const response = await api.postAudio(blob);
            const text = response.text;
            
            if (!text || !text.trim()) {
                ui.updateStatus('No speech detected');
                setTimeout(() => ui.hideStatus(), 2000);
                return null;
            }
            
            ui.hideStatus();
            await triggerSendFn(text);
            return text;
            
        } catch (e) {
            console.error('Transcription failed:', e);
            ui.updateStatus('Transcription failed');
            setTimeout(() => ui.hideStatus(), 2000);
            return null;
        }
    } else {
        ui.hideStatus();
        return null;
    }
};

export const forceStop = (btn, triggerSendFn) => {
    if (isRec) handleRelease(btn, triggerSendFn);
};

export const getRecState = () => isRec;