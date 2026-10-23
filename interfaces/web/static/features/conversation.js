// Browser conversation mode (v3) — continuous mic over WebSocket, TTS back down.
//
// The WS connection IS the mode switch (tmp/v3-conversation-websocket.md,
// pitfall 5): open = enter browser conversation mode server-side, close = exit.
// Tab close / crash / network drop all land in the same server teardown, and
// the sidebar buttons reflect through the conversation_mode_changed SSE event
// like every other state change — this module never touches button state.
//
// Upstream:   getUserMedia (AEC+NS on) -> AudioWorklet batches -> downsample to
//             16k int16 on the main thread -> binary WS frames.
// Downstream: tts_chunk JSON frames -> audio.js streaming-TTS machinery
//             (startTtsStream/enqueueTtsChunk/endTtsStream — same code path as
//             SSE-relayed TTS, including the stream_id preempt/straggler guards).
// Drain:      server sends turn_audio_done after the last chunk; we reply
//             playback_done once local playback goes idle so the server's
//             sink.wait() can end the turn when SHE'S done talking, not when
//             the last byte left the socket (pitfall 3).
import * as audio from '../audio.js';

const TARGET_RATE = 16000;

let ws = null;
let ctx = null;
let mediaStream = null;
let srcNode = null;
let workletNode = null;
let lastStreamId = null;
let turnOpen = false;   // fallback stream tracking when chunks carry no stream_id
let idlePoll = null;

export const isActive = () => !!ws && ws.readyState === WebSocket.OPEN;

export const stop = () => {
    try { ws?.close(); } catch { /* teardown runs in onclose */ }
};

const _teardown = () => {
    if (idlePoll) { clearInterval(idlePoll); idlePoll = null; }
    try { srcNode?.disconnect(); } catch {}
    try { workletNode?.disconnect(); } catch {}
    srcNode = workletNode = null;
    if (ctx) { try { ctx.close(); } catch {} ctx = null; }
    if (mediaStream) { mediaStream.getTracks().forEach(t => t.stop()); mediaStream = null; }
    ws = null;
};

// Linear-interp downsample (same approach as the push-to-talk path in audio.js).
const _downsample = (buf, from, to) => {
    if (from === to) return buf;
    const ratio = from / to;
    const n = Math.round(buf.length / ratio);
    const out = new Float32Array(n);
    for (let i = 0; i < n; i++) {
        const src = i * ratio;
        const lo = Math.floor(src);
        const hi = Math.min(lo + 1, buf.length - 1);
        const t = src - lo;
        out[i] = buf[lo] * (1 - t) + buf[hi] * t;
    }
    return out;
};

const _toInt16 = (f32) => {
    const i16 = new Int16Array(f32.length);
    for (let i = 0; i < f32.length; i++) {
        const s = Math.max(-1, Math.min(1, f32[i]));
        i16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
    }
    return i16;
};

// After turn_audio_done: report playback_done once the chunk queue goes idle.
// Replying ONLY to turn_audio_done (never after barge_stop) is what keeps a
// stale reply from leaking into the next turn — see browser_source.py.
const _watchForIdle = () => {
    if (idlePoll) clearInterval(idlePoll);
    idlePoll = setInterval(() => {
        if (!audio.isTtsPlaying()) {
            clearInterval(idlePoll);
            idlePoll = null;
            try { ws?.send(JSON.stringify({ type: 'playback_done' })); } catch {}
        }
    }, 100);
};

const _onMessage = (msg) => {
    switch (msg.type) {
        case 'tts_chunk': {
            const sid = msg.stream_id || null;
            if ((sid && sid !== lastStreamId) || (!sid && !turnOpen)) {
                lastStreamId = sid;
                turnOpen = true;
                if (idlePoll) { clearInterval(idlePoll); idlePoll = null; } // new turn cancels pending report
                audio.startTtsStream({ stream_id: sid });
            }
            audio.enqueueTtsChunk(msg);
            break;
        }
        case 'turn_audio_done':
            turnOpen = false;
            audio.endTtsStream({ stream_id: lastStreamId });
            _watchForIdle();
            break;
        case 'barge_stop':
            turnOpen = false;
            if (idlePoll) { clearInterval(idlePoll); idlePoll = null; }
            audio.stop(true);   // halts current element + flushes queue + sets stopped-stream guard
            break;
        case 'replaced':
        case 'bye':
            stop();
            break;
        case 'error':
            console.error('[CONV-WS] server error:', msg.msg);
            break;
    }
};

export const start = async () => {
    if (isActive()) return true;
    try {
        mediaStream = await navigator.mediaDevices.getUserMedia({
            audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
        });
    } catch (e) {
        console.error('[CONV-WS] mic access failed:', e);
        alert(e?.name === 'NotFoundError' ? 'No microphone found.' : 'Mic access denied.');
        return false;
    }
    try {
        ctx = new AudioContext();
        const v = window.__v ? `?v=${window.__v}` : '';
        await ctx.audioWorklet.addModule(`/static/features/conv-capture-worklet.js${v}`);
        srcNode = ctx.createMediaStreamSource(mediaStream);
        workletNode = new AudioWorkletNode(ctx, 'conv-capture');
        srcNode.connect(workletNode);
        workletNode.connect(ctx.destination);   // silent output; keeps the graph pulled
    } catch (e) {
        console.error('[CONV-WS] capture setup failed:', e);
        _teardown();
        return false;
    }

    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(`${proto}//${location.host}/ws/conversation`);
    ws.binaryType = 'arraybuffer';

    ws.onopen = () => {
        const rate = ctx.sampleRate;
        workletNode.port.onmessage = (e) => {
            if (!ws || ws.readyState !== WebSocket.OPEN) return;
            const i16 = _toInt16(_downsample(e.data, rate, TARGET_RATE));
            try { ws.send(i16.buffer); } catch {}
        };
        console.log(`[CONV-WS] connected (capture @${rate}Hz -> 16k)`);
    };
    ws.onmessage = (e) => {
        if (typeof e.data !== 'string') return;
        let msg;
        try { msg = JSON.parse(e.data); } catch { return; }
        _onMessage(msg);
    };
    ws.onclose = (e) => {
        if (e.code === 4401) alert('Not authorized for conversation mode — log in again.');
        if (e.code === 4409) console.warn('[CONV-WS] rejected: local conversation mode is active');
        _teardown();
        console.log(`[CONV-WS] closed (${e.code})`);
    };
    ws.onerror = () => { /* onclose follows with the code */ };
    return true;
};
