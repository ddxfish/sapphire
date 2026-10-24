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
// Drops:      `wanted` is the user's intent — only start()/stop() change it, and
//             every deliberate Off (sidebar, mic button, server bye/replaced)
//             runs stop(). A close while still wanted is a DROP: the mic stays
//             warm and only the socket comes back; the server parks the session
//             (BROWSER_GRACE_S) so a quick return resumes it with no mode flap.
import * as audio from '../audio.js';
import { showToast } from '../shared/toast.js';

const TARGET_RATE = 16000;
const RETRY_MS = [2000, 5000, 10000, 20000, 30000];   // then every 30 s
const GIVE_UP_MS = 10 * 60 * 1000;
const REFUSALS = new Set([4401, 4409, 4500]);        // not logged in / local mode / start failed

let ws = null;
let ctx = null;
let mediaStream = null;
let srcNode = null;
let workletNode = null;
let lastStreamId = null;
let turnOpen = false;   // fallback stream tracking when chunks carry no stream_id
let idlePoll = null;
let wanted = false;
let retryTimer = null;
let retries = 0;
let droppedAt = 0;

export const isActive = () => !!ws && ws.readyState === WebSocket.OPEN;

// The ONE deliberate exit. Tears down synchronously, so a quick stop()->start()
// can't have the old socket's late onclose touch the new session.
export const stop = () => {
    wanted = false;
    _teardown();
};

const _teardown = () => {
    if (retryTimer) { clearTimeout(retryTimer); retryTimer = null; }
    if (idlePoll) { clearInterval(idlePoll); idlePoll = null; }
    try { srcNode?.disconnect(); } catch {}
    try { workletNode?.disconnect(); } catch {}
    srcNode = workletNode = null;
    if (ctx) { try { ctx.close(); } catch {} ctx = null; }
    if (mediaStream) { mediaStream.getTracks().forEach(t => t.stop()); mediaStream = null; }
    const w = ws;
    ws = null;
    try { w?.close(1000, 'off'); } catch {}
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
    if (wanted) {                       // already on — or between retries: reconnect now
        if (!ws) { clearTimeout(retryTimer); retryTimer = null; _connect(); }
        return true;
    }
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
    const rate = ctx.sampleRate;
    workletNode.port.onmessage = (e) => {
        if (!ws || ws.readyState !== WebSocket.OPEN) return;   // dropped: frames go nowhere
        const i16 = _toInt16(_downsample(e.data, rate, TARGET_RATE));
        try { ws.send(i16.buffer); } catch {}
    };
    wanted = true;
    retries = 0;
    droppedAt = 0;
    _connect();
    return true;
};

const _connect = () => {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const sock = new WebSocket(`${proto}//${location.host}/ws/conversation`);
    sock.binaryType = 'arraybuffer';
    ws = sock;

    sock.onopen = () => {
        if (ctx?.state === 'suspended') ctx.resume().catch(() => {});
        console.log(`[CONV-WS] connected (capture @${ctx?.sampleRate}Hz -> 16k)` +
                    (droppedAt ? ` after a ${Math.round((Date.now() - droppedAt) / 1000)}s drop` : ''));
        retries = 0;
        droppedAt = 0;
    };
    sock.onmessage = (e) => {
        if (typeof e.data !== 'string') return;
        let msg;
        try { msg = JSON.parse(e.data); } catch { return; }
        _onMessage(msg);
    };
    sock.onclose = (e) => {
        console.log(`[CONV-WS] closed (${e.code}${e.reason ? ' ' + e.reason : ''})`);
        if (sock !== ws) return;        // stop() already tore this one down
        ws = null;
        if (e.code === 4401) alert('Not authorized for conversation mode — log in again.');
        if (e.code === 4409) console.warn('[CONV-WS] rejected: local conversation mode is active');
        if (!wanted || REFUSALS.has(e.code)) { stop(); return; }
        _scheduleRetry();
    };
    sock.onerror = () => { /* onclose follows with the code */ };
};

const _scheduleRetry = () => {
    if (idlePoll) { clearInterval(idlePoll); idlePoll = null; }
    turnOpen = false;
    if (!droppedAt) droppedAt = Date.now();
    if (Date.now() - droppedAt > GIVE_UP_MS) {
        stop();
        showToast('Conversation mode lost its connection — turn it back on when the server is back', 'error');
        return;
    }
    const ms = RETRY_MS[Math.min(retries++, RETRY_MS.length - 1)];
    console.warn(`[CONV-WS] dropped — reconnecting in ${ms / 1000}s`);
    retryTimer = setTimeout(() => { retryTimer = null; if (wanted) _connect(); }, ms);
};
