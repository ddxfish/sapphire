// audio.js - Audio lifecycle with native WAV recording (no server-side ffmpeg needed)
import * as ui from './ui.js';
import * as api from './api.js';

let audioContext, mediaStream, sourceNode, processorNode;
let audioChunks = [];
let player, blobUrl, ttsCtrl;
let isRec = false, isStreaming = false;

// Local TTS state (server-side speaker playback)
let localTtsPlaying = false;
let localTtsPollInterval = null;

// Recording settings - match what Whisper expects
const SAMPLE_RATE = 16000;
const NUM_CHANNELS = 1;

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

// Local TTS control
export const isLocalTtsPlaying = () => localTtsPlaying;

export const startLocalTtsPoll = () => {
    if (localTtsPollInterval) return;
    localTtsPollInterval = setInterval(async () => {
        try {
            const status = await api.getTtsStatus();
            localTtsPlaying = status.playing;
        } catch { localTtsPlaying = false; }
    }, 1000);
};

export const stopLocalTtsPoll = () => {
    if (localTtsPollInterval) {
        clearInterval(localTtsPollInterval);
        localTtsPollInterval = null;
    }
    localTtsPlaying = false;
};

export const stopLocalTts = async () => {
    try {
        await api.stopLocalTts();
        localTtsPlaying = false;
    } catch {}
};

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
    // Also stop local TTS if playing
    if (localTtsPlaying) stopLocalTts();
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

/**
 * Encode PCM samples as WAV file
 * @param {Float32Array} samples - Audio samples (-1 to 1)
 * @param {number} sampleRate - Sample rate in Hz
 * @returns {Blob} WAV file blob
 */
function encodeWAV(samples, sampleRate) {
    const buffer = new ArrayBuffer(44 + samples.length * 2);
    const view = new DataView(buffer);
    
    // WAV header
    const writeString = (offset, string) => {
        for (let i = 0; i < string.length; i++) {
            view.setUint8(offset + i, string.charCodeAt(i));
        }
    };
    
    writeString(0, 'RIFF');
    view.setUint32(4, 36 + samples.length * 2, true);
    writeString(8, 'WAVE');
    writeString(12, 'fmt ');
    view.setUint32(16, 16, true); // fmt chunk size
    view.setUint16(20, 1, true); // PCM format
    view.setUint16(22, NUM_CHANNELS, true);
    view.setUint32(24, sampleRate, true);
    view.setUint32(28, sampleRate * NUM_CHANNELS * 2, true); // byte rate
    view.setUint16(32, NUM_CHANNELS * 2, true); // block align
    view.setUint16(34, 16, true); // bits per sample
    writeString(36, 'data');
    view.setUint32(40, samples.length * 2, true);
    
    // Convert float samples to 16-bit PCM
    let offset = 44;
    for (let i = 0; i < samples.length; i++) {
        const s = Math.max(-1, Math.min(1, samples[i]));
        view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7FFF, true);
        offset += 2;
    }
    
    return new Blob([buffer], { type: 'audio/wav' });
}

/**
 * Downsample audio buffer to target sample rate
 * @param {Float32Array} buffer - Source audio buffer
 * @param {number} sourceSampleRate - Source sample rate
 * @param {number} targetSampleRate - Target sample rate
 * @returns {Float32Array} Downsampled buffer
 */
function downsample(buffer, sourceSampleRate, targetSampleRate) {
    if (sourceSampleRate === targetSampleRate) {
        return buffer;
    }
    const ratio = sourceSampleRate / targetSampleRate;
    const newLength = Math.round(buffer.length / ratio);
    const result = new Float32Array(newLength);
    
    for (let i = 0; i < newLength; i++) {
        const srcIndex = i * ratio;
        const srcIndexFloor = Math.floor(srcIndex);
        const srcIndexCeil = Math.min(srcIndexFloor + 1, buffer.length - 1);
        const t = srcIndex - srcIndexFloor;
        // Linear interpolation
        result[i] = buffer[srcIndexFloor] * (1 - t) + buffer[srcIndexCeil] * t;
    }
    
    return result;
}

const startRec = async () => {
    try {
        mediaStream = await navigator.mediaDevices.getUserMedia({ 
            audio: {
                channelCount: 1,
                sampleRate: { ideal: SAMPLE_RATE },
                echoCancellation: true,
                noiseSuppression: true
            } 
        });
        
        // Create audio context (browser may give us a different sample rate)
        audioContext = new AudioContext();
        sourceNode = audioContext.createMediaStreamSource(mediaStream);
        
        // Use ScriptProcessor for capturing (deprecated but universal)
        // Buffer size 4096 is a good balance of latency vs overhead
        processorNode = audioContext.createScriptProcessor(4096, 1, 1);
        audioChunks = [];
        
        processorNode.onaudioprocess = (e) => {
            if (isRec) {
                // Copy the input data (it gets reused)
                const inputData = e.inputBuffer.getChannelData(0);
                audioChunks.push(new Float32Array(inputData));
            }
        };
        
        sourceNode.connect(processorNode);
        processorNode.connect(audioContext.destination); // Required for processing to work
        
        return true;
    } catch (e) {
        console.error('Mic access error:', e);
        alert('Mic access denied');
        return false;
    }
};

const stopRec = async () => {
    if (!audioContext || audioChunks.length === 0) {
        return null;
    }
    
    // Disconnect nodes
    try {
        sourceNode?.disconnect();
        processorNode?.disconnect();
    } catch {}
    
    // Stop media stream
    mediaStream?.getTracks().forEach(t => t.stop());
    
    // Concatenate all chunks
    const totalLength = audioChunks.reduce((acc, chunk) => acc + chunk.length, 0);
    const fullBuffer = new Float32Array(totalLength);
    let offset = 0;
    for (const chunk of audioChunks) {
        fullBuffer.set(chunk, offset);
        offset += chunk.length;
    }
    audioChunks = [];
    
    // Downsample to 16kHz if needed
    const sourceSampleRate = audioContext.sampleRate;
    const samples = downsample(fullBuffer, sourceSampleRate, SAMPLE_RATE);
    
    // Close audio context
    try {
        await audioContext.close();
    } catch {}
    audioContext = null;
    
    // Encode as WAV
    return encodeWAV(samples, SAMPLE_RATE);
};

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