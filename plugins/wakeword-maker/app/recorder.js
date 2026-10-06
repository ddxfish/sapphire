// recorder.js - the browser microphone as a sample source. Press, say the phrase, press again: one sample.
// Captures float32 at the context's own rate with the browser's processing OFF (a wake word model should
// meet the raw mic), encodes a WAV at that rate; the server makes it 16 kHz mono and trims the silence.
let ctx = null, stream = null, source = null, proc = null, chunks = [], recording = false, level = 0;

export function isRecording() { return recording; }
export function peak() { return level; }

export async function start(deviceId) {
    if (recording) return;
    const audio = { channelCount: 1, echoCancellation: false, noiseSuppression: false, autoGainControl: false };
    if (deviceId) audio.deviceId = { exact: deviceId };
    stream = await navigator.mediaDevices.getUserMedia({ audio });
    ctx = new AudioContext();
    source = ctx.createMediaStreamSource(stream);
    proc = ctx.createScriptProcessor(4096, 1, 1);
    chunks = [];
    level = 0;
    proc.onaudioprocess = (e) => {
        if (!recording) return;
        const data = e.inputBuffer.getChannelData(0);
        chunks.push(new Float32Array(data));
        let m = 0;
        for (let i = 0; i < data.length; i += 8) { const a = Math.abs(data[i]); if (a > m) m = a; }
        level = m;
    };
    source.connect(proc);
    proc.connect(ctx.destination);
    recording = true;
}

export async function stop() {
    if (!recording) return null;
    recording = false;
    const rate = ctx.sampleRate;
    try { proc.disconnect(); source.disconnect(); } catch { /* fine */ }
    stream.getTracks().forEach(t => t.stop());
    try { await ctx.close(); } catch { /* fine */ }
    const n = chunks.reduce((a, c) => a + c.length, 0);
    const all = new Float32Array(n);
    let o = 0;
    for (const c of chunks) { all.set(c, o); o += c.length; }
    chunks = [];
    return { blob: encodeWAV(all, rate), seconds: n / rate, rate };
}

export async function inputs(askIfBlank = false) {
    try {
        let list = (await navigator.mediaDevices.enumerateDevices()).filter(d => d.kind === 'audioinput');
        if (askIfBlank && list.length && list.every(d => !d.label)) {
            // before permission the browser hides the names: borrow the mic for an instant so they appear
            const s = await navigator.mediaDevices.getUserMedia({ audio: true });
            s.getTracks().forEach(t => t.stop());
            list = (await navigator.mediaDevices.enumerateDevices()).filter(d => d.kind === 'audioinput');
        }
        return list.filter(d => d.deviceId && d.deviceId !== 'communications').map(d => ({ id: d.deviceId, label: d.label || '' }));
    } catch { return []; }
}

export function onChange(fn) {
    navigator.mediaDevices?.addEventListener?.('devicechange', fn);
    return () => navigator.mediaDevices?.removeEventListener?.('devicechange', fn);
}

function encodeWAV(samples, rate) {
    const buf = new ArrayBuffer(44 + samples.length * 2);
    const v = new DataView(buf);
    const str = (off, s) => { for (let i = 0; i < s.length; i++) v.setUint8(off + i, s.charCodeAt(i)); };
    str(0, 'RIFF'); v.setUint32(4, 36 + samples.length * 2, true); str(8, 'WAVE');
    str(12, 'fmt '); v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
    v.setUint32(24, rate, true); v.setUint32(28, rate * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true);
    str(36, 'data'); v.setUint32(40, samples.length * 2, true);
    let off = 44;
    for (let i = 0; i < samples.length; i++, off += 2) {
        const s = Math.max(-1, Math.min(1, samples[i]));
        v.setInt16(off, s < 0 ? s * 0x8000 : s * 0x7FFF, true);
    }
    return new Blob([buf], { type: 'audio/wav' });
}
