// audio.js - Audio lifecycle with native WAV recording (no server-side ffmpeg needed)
import * as ui from './ui.js';
import * as api from './api.js';
import { dispatch, on as busOn, Events } from './core/event-bus.js';
import { stripThink } from './shared/think.js';
import { splitParagraphs } from './shared/prose.js';
import { responseError } from './shared/fetch.js';
import { micAudio, micWhy } from './shared/mic-words.js';

let audioContext, mediaStream, sourceNode, processorNode;
let audioChunks = [];
let player, blobUrl, ttsCtrl;
let isRec = false, isStreaming = false;

// TTS cache for replay functionality (keyed by message index)
const ttsCache = new Map();
const TTS_CACHE_MAX = 10;

// Local TTS state (server-side speaker playback)
// Now sourced from unified status via scene.js
let localTtsPlaying = false;

// Recording settings - match what Whisper expects
export const SAMPLE_RATE = 16000;
const NUM_CHANNELS = 1;

// Volume state
let volume = 1.0;
let muted = false;

// Volume control exports
export const setVolume = (val) => {
    volume = Math.max(0, Math.min(1, val));
    if (player) player.volume = muted ? 0 : volume;
    if (_ttsStreamPlayer) _ttsStreamPlayer.volume = muted ? 0 : volume;
    _gain();
};

export const setMuted = (val) => {
    muted = val;
    if (player) player.volume = muted ? 0 : volume;
    if (_ttsStreamPlayer) _ttsStreamPlayer.volume = muted ? 0 : volume;
    _gain();
};

export const getVolume = () => volume;
export const isMuted = () => muted;

// Local TTS control - now updated by unified status poll
export const isLocalTtsPlaying = () => localTtsPlaying;

// Called by scene.js updateScene when unified status returns tts_playing
export const setLocalTtsPlaying = (playing) => {
    localTtsPlaying = playing;
};

// Legacy polling functions - now no-ops since unified status handles this
export const startLocalTtsPoll = () => {
    // No longer needed - unified status provides tts_playing
};

export const stopLocalTtsPoll = () => {
    // No longer needed
    localTtsPlaying = false;
};

export const stopLocalTts = async (streamId = null, chatWide = false, speakers = true) => {
    try {
        await api.stopLocalTts(streamId, chatWide, speakers);
        localTtsPlaying = false;
    } catch {}
    // Re-check server state after a delay to catch races where TTS
    // started after our stop (e.g. user clicked stop before playback began)
    setTimeout(async () => {
        try {
            const status = await api.fetchStatus();
            localTtsPlaying = status?.tts_playing || false;
        } catch {}
    }, 800);
};

const cleanup = () => {
    if (blobUrl) {
        try { URL.revokeObjectURL(blobUrl); } catch {}
        blobUrl = null;
    }
};

// Legacy whole-blob lane generation. Bumped by _stopLegacyPlayer so a
// playText superseded mid-flight (fetch aborted, or a streaming turn took
// the audio focus) never touches shared state again — its catch used to run
// `isStreaming = false` AFTER the new streaming turn had set it true, hiding
// the mic ⏹ until the first chunk. 2026-09-08.
let _legacyGen = 0;

const _stopLegacyPlayer = () => {
    _legacyGen += 1;
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
    cleanup();
};

// chatWide: the person pressed stop meaning this chat's voice (the mic
// button, Stop) - the server then mutes this chat's pumps too. Every other
// caller stops only what this tab plays and the speakers (hunt 21).
export const stop = (force = false, chatWide = false) => {
    if (isStreaming && !force) return;
    // Name the pump we're hearing BEFORE the queue teardown moves it to
    // _stoppedStreamId — the server mutes exactly that pump, never the next
    // turn that's still thinking. 2026-09-08.
    const sid = _currentStreamId;
    // the text stream in flight (Replay, a chat room's line) is cut too: its start
    // could land after Stop, clear the stop guard and play the whole line (2026-11-04)
    if (_textCtrl) { try { _textCtrl.abort(); } catch {} _textCtrl = null; }
    _stopLegacyPlayer();
    // Tear down the streaming-TTS chunk queue too — Stop must cut both
    // legacy playback (full-blob) and the new per-chunk queue.
    _ttsStreamStop();
    isStreaming = false;
    // Always tell server to stop TTS — even if we don't think it's playing yet.
    // Server stop is idempotent and prevents races where TTS starts after our check.
    // force = somebody asked for quiet (Stop, Replay, a barge-in). Without it
    // this is housekeeping (a chat switch, the page closing): the tab's own
    // audio stops, the computer's speakers are left alone.
    stopLocalTts(sid, chatWide, force);
    // the person meant "quiet": a chat room's reading queue hears it and goes
    // quiet too, instead of taking the stop as the end of a line (2026-11-04)
    if (chatWide) dispatch('tts_user_stop', {});
};

export const isTtsPlaying = () => isStreaming;

export const playText = async (txt, cacheKey = null, voiceOpts = null) => {
    stop(true);
    const myGen = _legacyGen;          // stop() just bumped it — this run owns the lane
    const mine = () => myGen === _legacyGen;
    isStreaming = true;
    ttsCtrl = new AbortController();

    // Thinking out (shared/think.js, the twin of core/think.py)
    let clean = stripThink(txt);

    // Filter paragraphs
    const paras = splitParagraphs(clean).filter(p => {
        const t = p.trim();
        // whole emoji, not a class: without /u the class held lone surrogate
        // halves shared by nearly every emoji, so a reply opening with 😊 or 💙
        // was filtered out and never spoken (UI scout, 2026-11-12)
        return !/^(?:🧧|🌁|🧠|💾|⚠️)/u.test(t);
    });

    clean = paras.join('\n\n').trim().replace(/^---\s*$/gm, '').trim();

    if (!clean) {
        isStreaming = false;
        ttsCtrl = null;
        return;
    }

    ui.showStatus();
    ui.updateStatus('Generating TTS...');

    try {
        // Check cache first
        let blob;
        if (cacheKey !== null && ttsCache.has(cacheKey)) {
            blob = ttsCache.get(cacheKey);
            ui.updateStatus('Playing cached TTS...');
        } else {
            blob = await api.fetchAudio(clean, ttsCtrl.signal, voiceOpts);
            if (blob === null) {
                // 204: not voiced here (a plugin took it, or Settings > Voice >
                // Play speech here is off). Quiet finish, nothing to cache.
                if (mine()) { isStreaming = false; ui.hideStatus(); }
                return;
            }
            // Cache if key provided
            if (cacheKey !== null) {
                // LRU eviction
                if (ttsCache.size >= TTS_CACHE_MAX) {
                    const firstKey = ttsCache.keys().next().value;
                    ttsCache.delete(firstKey);
                }
                ttsCache.set(cacheKey, blob);
            }
        }

        // Bail if stopped or superseded while fetching
        if (!isStreaming || !mine()) return;

        // fetchWithTimeout returns a raw Response (not a Blob) when the
        // content-type wasn't audio/* — e.g. Brave/an extension/a proxy
        // stripped or rewrote the header. Surface it with a clear message
        // instead of letting URL.createObjectURL throw a TypeError that the
        // catch's stop-detection would swallow silently. 2026-05-28.
        if (!(blob instanceof Blob)) {
            const ct = blob?.headers?.get?.('content-type') || 'unknown';
            throw new Error(`TTS response not audio (content-type=${ct})`);
        }

        blobUrl = URL.createObjectURL(blob);
        player = new Audio(blobUrl);

        // Apply volume settings
        player.volume = muted ? 0 : volume;

        player.onended = () => {
            if (!mine()) return;
            isStreaming = false;
            ui.hideStatus();
            cleanup();
        };

        player.onerror = e => {
            if (!mine()) return;
            console.error('Audio error:', e);
            isStreaming = false;
            ui.hideStatus();
            cleanup();
            ui.showToast("Sapphire's voice couldn't play. Try Test in Settings › TTS.", 'error');
        };

        await player.play();
        ui.hideStatus();
    } catch (e) {
        if (!mine()) return;           // superseded: the new owner drives the flags
        isStreaming = false;
        ui.hideStatus();
        const cls = e.name || '';
        const msg = e.message || '';
        // Swallow ONLY recognized stop/preempt rejections — those are the
        // expected consequence of stop()/a newer playText tearing down the
        // element mid-flight. Everything else is a genuine failure and must
        // be surfaced. (Previously this was a blanket `player===null` return,
        // which also ate the content-type TypeError above — the silent hole
        // a scout found. 2026-05-28.)
        const isStopRejection =
            msg.includes('Cancelled') || msg.includes('cancelled') ||
            msg.includes('aborted') || msg.includes('interrupted') || msg.includes('removed') ||
            (cls.includes('AbortError') && player === null);
        if (isStopRejection) return;
        if (cls.includes('NotAllowedError') || msg.includes('autoplay')) {
            ui.showToast('Browser blocked audio — click anywhere on the page, then try again.', 'warning');
        } else if (msg.includes('not audio')) {
            console.warn('[TTS] whole-blob fallback got non-audio response:', msg);
            ui.showToast("Sapphire's voice sent something that isn't audio. Try Test in Settings › TTS; a proxy or browser shield can cause this.", 'error');
        } else if (cls.includes('AbortError')) {
            ui.showToast("Sapphire's voice couldn't play. Check your speakers, or click the page and try again.", 'warning');
        } else {
            ui.showToast(`Sapphire's voice couldn't play${msg ? ` (${msg})` : ''}. Try Test in Settings › TTS.`, 'error');
        }
    }
};

// Replay TTS for a specific message index (works for user or assistant)
export const replayTts = async (idx) => {
    const allMsgs = document.querySelectorAll('#chat-container .message:not(.status):not(.error)');
    const msg = allMsgs[idx];
    if (!msg) return;
    const content = msg.querySelector('.message-content');
    if (!content) return;
    const prose = ui.extractProseText(content);   // the one extractor (ui-parsing)
    if (!prose) return;

    // Try the streaming TTS endpoint first. Falls back to /api/tts whole-blob
    // playback on 503 (setting off / provider can't stream) or any error.
    // Streaming gives progressive playback + activates plugin hooks (e.g.
    // captioning banner works for replays too).
    try {
        const ok = await playTextStreaming(prose);
        if (ok) return;
    } catch (e) {
        console.warn('[TTS] streaming replay failed, falling back:', e?.message || e);
    }
    await playText(prose);
};

/** Stream a known text via /api/tts/stream → SSE → existing playback queue.
 * Returns true if the server ran the stream (a chunk arrived, or the turn
 * ended with none: a plugin took the audio, or Play speech here is off),
 * false if the endpoint was unavailable (503) so the caller can fall back.
 * A ran-but-silent stream must NOT fall back: the whole-blob path would
 * fire the TTS hooks a second time for the same text. Other errors throw. */
// `chat`: the chat that produced the text (a room's bound session). The
// server's privacy gate judges THAT chat instead of the operator's active
// one — a private room speaking through a public active chat used to reach
// a cloud TTS provider (broadsword H3). Omit for the operator's own chat.
// `opts`: { speed, pitch } for this text only (a chat-room seat's own), else the live client's.
let _textCtrl = null;          // playTextStreaming's fetch in flight: stop() aborts it

export const playTextStreaming = async (text, voice = null, chat = null, opts = null) => {
    stop(true);  // clear any current playback before starting new stream
    const ctrl = _textCtrl = new AbortController();

    const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
    let res;
    try {
        res = await fetch('/api/tts/stream', {
            signal: ctrl.signal,
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
            body: JSON.stringify({ text, ...(voice ? { voice } : {}), ...(chat ? { chat } : {}),
                                   ...(opts?.speed ? { speed: opts.speed } : {}), ...(opts?.pitch ? { pitch: opts.pitch } : {}) }),
        });
    } catch (e) {
        if (ctrl.signal.aborted) return true;      // stopped: it ran, nothing to fall back to
        throw new Error(`network: ${e.message}`);
    }

    if (res.status === 503 || res.status === 404) {
        // Server declined — caller should fall back to whole-blob path.
        try { await res.body?.cancel(); } catch {}
        return false;
    }
    if (!res.ok) {
        throw await responseError(res);
    }
    const ct = res.headers.get('content-type') || '';
    if (!ct.startsWith('text/event-stream')) {
        try { await res.body?.cancel(); } catch {}
        return false;
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let sawChunk = false;
    let sawEnd = false;
    try {
        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            // split(/\r?\n/) handles both LF (uvicorn default) and CRLF
            // (some Windows-side proxies / middleware normalize to CRLF).
            // 2026-05-18 herring-table #17.
            const lines = buffer.split(/\r?\n/);
            buffer = lines.pop();
            for (const line of lines) {
                if (!line.startsWith('data: ')) continue;
                let data;
                try { data = JSON.parse(line.slice(6)); } catch { continue; }
                if (data.error) throw new Error(data.error);
                if (data.type === 'tts_stream_start') {
                    startTtsStream(data);
                } else if (data.type === 'tts_chunk') {
                    enqueueTtsChunk(data);
                    sawChunk = true;
                } else if (data.type === 'tts_stream_end') {
                    endTtsStream(data);
                    sawEnd = true;
                } else if (data.type === 'notice') {
                    // Dropped-chunks notice from the brain pump. Route
                    // through the same 'chat_notice' bus event that chat-
                    // streaming uses — chat.js already subscribes and
                    // renders these as toasts. Without this branch, the
                    // notice silently disappears and the user gets text +
                    // no audio + no warning (the original no-audio class).
                    // 2026-05-26 — closing the frontend half of the user
                    // report. Brain emits the notice correctly; we just
                    // weren't relaying it from the replay SSE path.
                    dispatch('chat_notice', {
                        message: data.message || '',
                        severity: data.severity || 'warning',
                    });
                }
            }
        }
    } catch (e) {
        if (ctrl.signal.aborted) return true;      // stopped mid-stream: the same
        throw e;
    } finally {
        try { await reader.cancel(); } catch {}
        if (_textCtrl === ctrl) _textCtrl = null;
    }
    return sawChunk || sawEnd;
};


// Clear TTS cache (call on chat switch/history reload)
export const clearTtsCache = () => {
    ttsCache.clear();
};

// ---------------------------------------------------------------------------
// Streaming TTS playback (v2.7.0)
//
// Brain-side chunker emits per-sentence OGG blobs over SSE; we queue them and
// play them in order, gapless on Web Audio (see "Gapless playback" below).
// Each chunk is independently decodable.
// ---------------------------------------------------------------------------
let _ttsStreamGen = 0;       // generation counter — bumped by stop() to invalidate stale chunks
let _ttsStreamActive = false;
let _ttsStreamQueue = [];    // [{ blob, pause_after_ms, index, text, boundary }]
let _ttsStreamPlayer = null;
let _ttsStreamUrl = null;
let _ttsStreamEnded = false; // server sent tts_stream_end (no more chunks coming)
// (The old `_ttsStreamSawChunk` "skip the whole-blob fallback" flag is gone —
// the server reports `tts_streamed` on llm_done. It was wiped by the mic ⏹,
// so a stop mid-speech re-spoke the whole reply from the top. 2026-09-08.)
// True once at least one chunk's play() promise has resolved (i.e. audio
// actually started playing) within the current generation. Drives the
// "chunks arrived but nothing ever played" toast — without it, the .catch
// recursion drains the queue cleanly with no user-facing signal, which
// matches the vague "no audio" reports we couldn't otherwise account for.
let _ttsStreamAnyPlayed = false;
// Brain stamps every streaming-TTS payload with a unique stream_id (UUID).
// Frontend tracks the CURRENT id and drops events tagged with anything
// else — handles the "regenerate while playing" / "replay while chat
// streaming" overlap that otherwise causes audio bleed across turns.
// New stream with different id preempts the old one. 2026-05-18 herring #5.
let _currentStreamId = null;
// A stream the user just stopped (left-button "stop TTS"). Its already-in-flight
// chunks must be DROPPED, not played — else the queue "refills" a beat after stop.
// Cleared when a fresh stream starts. Pairs with the server-side pump mute.
let _stoppedStreamId = null;
// Per-turn playback diagnostics — the "black box". Reset on startTtsStream,
// consumed (logged) at turn end. `played` counts chunks whose audio actually
// PROGRESSED (currentTime advanced) — not chunks whose play() merely resolved,
// which is the blind spot that hid the Brave no-audio reports. The one-line
// summary emitted at turn end is what a user pastes to tell us why they heard
// nothing, without us having to ask. 2026-05-28.
let _ttsStats = null;
const _newTtsStats = () => ({ received: 0, decoded: 0, played: 0, failed: 0, fails: [] });
// Record a chunk's terminal outcome exactly once. outcome 'played' or a
// failure tag ('autoplay-blocked','aborted','play-reject','media-error',
// 'silent-ended','silent-stalled','decode-error').
const _recordChunk = (item, outcome, detail) => {
    if (item && item._recorded) return;
    if (item) item._recorded = true;
    if (outcome === 'played') _ttsStreamAnyPlayed = true;  // accurate signal: real progress, not mere resolve
    if (!_ttsStats) return;
    if (outcome === 'played') {
        _ttsStats.played++;
    } else {
        _ttsStats.failed++;
        _ttsStats.fails.push({ idx: item?.index, outcome, detail });
    }
};
const _dominantFail = (fails) => {
    const counts = {};
    for (const f of fails) counts[f.outcome] = (counts[f.outcome] || 0) + 1;
    let best = 'unknown', n = 0;
    for (const k in counts) if (counts[k] > n) { n = counts[k]; best = k; }
    const sample = fails.find(f => f.outcome === best);
    return { outcome: best, sample: sample && sample.detail ? sample.detail : '' };
};
// Compact element/environment snapshot taken at a chunk's terminal moment so a
// silent-failure paste tells us WHICH silent mode it was — duration=0 (no
// decodable data), readyState stuck below HAVE_ENOUGH_DATA(4), element muted /
// zero volume, or the document hidden. Tab-level mute is invisible to JS, but
// these are the observable proxies that narrow it down. 2026-05-28.
const _audioDiag = (audio) => {
    if (!audio) return 'no-element';
    try {
        const dur = Number.isFinite(audio.duration) ? audio.duration.toFixed(2) : String(audio.duration);
        return `rs=${audio.readyState} ns=${audio.networkState} dur=${dur} ` +
               `ct=${(audio.currentTime || 0).toFixed(2)} vol=${audio.volume} ` +
               `muted=${audio.muted} hidden=${document.hidden} errcode=${(audio.error && audio.error.code) || '-'}`;
    } catch (e) { return 'diag-error'; }
};

const _b64ToBlob = (b64, contentType) => {
    const bin = atob(b64);
    const len = bin.length;
    const bytes = new Uint8Array(len);
    for (let i = 0; i < len; i++) bytes[i] = bin.charCodeAt(i);
    return new Blob([bytes], { type: contentType || 'audio/ogg' });
};

const _disposeItem = (item) => {
    if (!item) return;
    // Defer URL revocation by 1s — Win Chrome's media stack can still hold
    // a reference to the blob URL briefly after the audio element fires
    // onended. Immediate revoke caused sporadic "Failed to load resource"
    // warnings and (under memory pressure) chunks cut off ~50ms early.
    // 2026-05-18 herring-table #20. MDN recommendation for short-lived blob URLs.
    if (item.url) {
        const u = item.url;
        setTimeout(() => { try { URL.revokeObjectURL(u); } catch {} }, 1000);
        item.url = null;
    }
    if (item.audio) {
        item.audio.onended = null;
        item.audio.onerror = null;
        // GATED ABORT: only stop the element if its play() has actually
        // resolved (audio truly playing). Why:
        //   - If play() resolved → audio is in the media stack and will keep
        //     playing even after JS drops the reference (browsers hold media
        //     alive while playing). We MUST stop it explicitly or the user
        //     hears chunks stacking up (10× overlap on slower systems).
        //   - If play() still pending → calling pause() or src='' would
        //     reject the pending promise with AbortError, cascading the
        //     no-audio bug on Brave/Linux + Shields/PipeWire timing jitter.
        // The `_playStarted` flag is set in the .then() of play() below.
        // GC handles never-started elements when their .catch eventually
        // fires (or when they GC naturally if .catch is already detached).
        // 2026-05-26 — fix for user report of overlapping chunk playback (10x stacking).
        if (item.audio._playStarted) {
            try { item.audio.pause(); } catch {}
            item.audio.src = '';
        }
        item.audio = null;
    }
};

const _ttsStreamCleanupCurrent = () => {
    _disposeItem(_ttsStreamCurrent);
    _ttsStreamCurrent = null;
    _ttsStreamPlayer = null;
    _ttsStreamUrl = null;
};

// Called once when a streaming-TTS turn finishes (SSE end + queue drained).
// Emits the always-on one-line "black box" summary, and — only when chunks
// arrived but none actually produced sound — dumps the per-chunk failure
// detail and shows the user a toast. Idempotent: consumes _ttsStats so a
// second caller (endTtsStream vs _pump, whichever lands second)
// is a no-op.
const _ttsStreamMaybeWarnSilent = () => {
    const s = _ttsStats;
    if (!s) return;
    _ttsStats = null;  // consume — fresh stats created by next startTtsStream
    let reason = '';
    if (s.failed) {
        const d = _dominantFail(s.fails);
        reason = ` reason=${d.outcome}` + (d.sample ? ` [${d.sample}]` : '');
    }
    console.info(`[TTS-STREAM] turn done: received=${s.received} decoded=${s.decoded} ` +
                 `played=${s.played} failed=${s.failed}${reason}`);
    if (s.received > 0 && s.played === 0) {
        // The silent-failure signature: audio arrived, nothing was audible.
        // This is the line the user pastes that tells us exactly why.
        console.warn('[TTS-STREAM] NO AUDIO this turn — per-chunk outcomes:', s.fails);
        try {
            ui.showToast(
                'TTS chunks couldn’t play — check system audio output or browser autoplay permissions.',
                'warning'
            );
        } catch {}
    }
};

let _ttsStreamCurrent = null;  // the item an <audio> element is playing: {audio, url, blob, ...}

// ---------------------------------------------------------------------------
// Gapless playback (2026-11-10). Each chunk is decoded the moment it arrives
// (decodeAudioData) and scheduled on one AudioContext exactly where the one
// before it ends, so a sentence or segment boundary costs no time: starting an
// <audio> element per chunk lost ~20-300 ms at every one, more on slow
// machines. The chunker's `pause_after_ms` is scheduled silence between
// CHUNKS; segments of one chunk run on without it.
//
// Where Web Audio can't play a chunk (no gesture on this page yet, a browser
// that can't decode Ogg/Opus, a context that stops advancing) that chunk and
// the rest of the page play through the <audio> element player below, the
// player this replaced, so no browser does worse than before.
// ---------------------------------------------------------------------------
const LEAD_S = 0.03;          // headroom for a chunk started from idle
let _pctx = null, _pgain = null;
let _webAudioBroken = false;  // a decode or a stall failed: elements for this page
let _scheduled = [];          // items on the context, in play order
let _cursor = 0;              // context time the scheduled audio ends
let _endWall = 0;             // performance.now() when the last audio ended
let _lastIndex = null;        // chunk index heard last, and the pause it asked for
let _lastPause = 0;
let _waitTimer = null;        // an element chunk waiting out its pause
let _decodeFails = 0;         // decode failures in a row
let _idleTimer = null;

function _gain() {
    if (_pgain) _pgain.gain.value = muted ? 0 : volume;
}

// The voice's context, or null for elements. Made only once the page has had
// a gesture (sticky activation), so it starts running; suspended when idle
// and resumed on the next chunk (allowed after a gesture).
// WebKit (iPhone, iPad, Safari) keeps the element player: its Web Audio
// obeys the ring/silent switch where <audio> doesn't (a silent voice nothing
// detects), its Ogg/Opus decoding is new (18.4), and setting the page's
// audio session to "playback" could fight the mic in conversation mode.
const _webkitSession = !!navigator.audioSession;

const _playCtx = () => {
    if (_webAudioBroken || _webkitSession) return null;
    if (!_pctx) {
        if (navigator.userActivation && !navigator.userActivation.hasBeenActive) return null;
        try {
            _pctx = new AudioContext();
            _pgain = _pctx.createGain();
            _pgain.connect(_pctx.destination);
            _gain();
        } catch (e) {
            console.warn('[TTS-STREAM] no AudioContext, using <audio> elements:', e?.message);
            _webAudioBroken = true;
            return null;
        }
    }
    if (_pctx.state === 'closed') { _webAudioBroken = true; return null; }
    if (_pctx.state !== 'running') _pctx.resume().catch(() => {});
    return _pctx;
};
// Made inside a gesture so it starts running (pointerdown is one only for a
// mouse; a touch activates on pointerup/touchend/click), then left to idle:
// a click mid-silence wakes nothing, the next chunk resumes it.
for (const ev of ['pointerdown', 'pointerup', 'touchend', 'click', 'keydown']) {
    window.addEventListener(ev, () => {
        if (_pctx || _webAudioBroken || _webkitSession) return;
        if (_playCtx()) _armIdle();
    }, true);
}

// Suspend the context after 10 s with nothing to play: a running one keeps
// the output device open (a Bluetooth headset streaming, a USB DAC awake).
const _armIdle = () => {
    clearTimeout(_idleTimer);
    _idleTimer = setTimeout(() => {
        if (!_busy() && !_ttsStreamActive && _pctx?.state === 'running') _pctx.suspend().catch(() => {});
    }, 10000);
};

const _busy = () => _scheduled.length > 0 || !!_ttsStreamCurrent;

// A chunk the element player will take: its <audio> is built now so it
// preloads while earlier audio plays. false = undecodable (counted, skipped).
const _toElement = (item) => {
    item.buffer = null;
    item.useEl = true;
    try {
        item.url = URL.createObjectURL(item.blob);
        item.audio = new Audio(item.url);
        item.audio.preload = 'auto';
        // No explicit .load(): Chromium (incl. Brave) can read it as a load
        // restart and reject the later play() with AbortError. 2026-05-26.
        if (_ttsStats && !item.counted) _ttsStats.decoded++;
        item.counted = true;
        return true;
    } catch (e) {
        if (item.url) { try { URL.revokeObjectURL(item.url); } catch {} }
        item.dead = true;
        if (_ttsStats) {
            _ttsStats.failed++;
            _ttsStats.fails.push({ idx: item.index, outcome: 'decode-error', detail: e?.message });
        }
        return false;
    }
};

const _release = (item) => {
    clearTimeout(item.watch);
    if (item.src) { try { item.src.disconnect(); } catch {} }
    item.src = null;
    item.buffer = null;
};

const _finish = () => {
    _ttsStreamMaybeWarnSilent();
    _ttsStreamActive = false;
    _ttsStreamCleanupCurrent();
    isStreaming = false;
    _armIdle();
};

// Play what's ready, in order. Called on every arrival, decode, and end.
const _pump = (gen) => {
    if (gen !== _ttsStreamGen || !_ttsStreamActive || _ttsStreamCurrent || _waitTimer) return;
    while (_ttsStreamQueue.length) {
        const item = _ttsStreamQueue[0];
        if (item.dead) { _ttsStreamQueue.shift(); continue; }
        if (!item.buffer && !item.useEl) return;          // still decoding: order holds
        const gap = (_lastIndex !== null && item.index !== _lastIndex) ? _lastPause : 0;
        if (item.buffer) {
            const ctx = _playCtx();
            if (ctx) {
                _ttsStreamQueue.shift();
                _schedule(item, ctx, gap);
                continue;
            }
            if (!_toElement(item)) continue;              // context gone: element
        }
        if (_scheduled.length) return;                    // after the scheduled audio
        const wait = gap - (performance.now() - _endWall);
        if (wait > 1) {
            _waitTimer = setTimeout(() => { _waitTimer = null; _pump(gen); }, wait);
            return;
        }
        _ttsStreamQueue.shift();
        _lastIndex = item.index;
        _lastPause = item.pause_after_ms;
        _elPlay(item, gen);
        return;
    }
    if (!_busy() && _ttsStreamEnded) _finish();
};

const _schedule = (item, ctx, gapMs) => {
    const now = ctx.currentTime;
    const start = _scheduled.length
        ? Math.max(_cursor + gapMs / 1000, now)
        : now + Math.max(LEAD_S, (gapMs - (performance.now() - _endWall)) / 1000);
    const src = ctx.createBufferSource();
    src.buffer = item.buffer;
    src.connect(_pgain);
    item.src = src;
    item.startAt = start;
    item.endAt = start + item.buffer.duration;
    _cursor = item.endAt;
    _lastIndex = item.index;
    _lastPause = item.pause_after_ms;
    _scheduled.push(item);
    clearTimeout(_idleTimer);
    src.onended = () => {
        const mine = _scheduled.includes(item);
        _scheduled = _scheduled.filter(x => x !== item);
        _release(item);
        if (!mine) return;
        _recordChunk(item, 'played');
        _endWall = performance.now();
        _pump(_ttsStreamGen);
    };
    // A context that stops advancing (a lost output device, a suspended tab)
    // never ends its sources. Past this chunk's end plus 3 s, everything still
    // scheduled goes back to the queue for the element player, unless the
    // context is moving and just started late (a sink or Bluetooth headset
    // slow to wake): then wait for the end again.
    const watch = (ms) => setTimeout(() => {
        if (!_scheduled.includes(item)) return;
        const t = ctx.currentTime;
        if (ctx.state === 'running' && t > now + 0.05 && t < item.endAt + 1) {
            item.watch = watch((item.endAt - t) * 1000 + 3000);
            return;
        }
        console.warn(`[TTS-STREAM] audio context stalled (${ctx.state}, t=${ctx.currentTime.toFixed(2)}); ` +
                     '<audio> elements from here on');
        _webAudioBroken = true;
        const stuck = _scheduled;
        _scheduled = [];
        for (const x of stuck) {
            if (x.src) { x.src.onended = null; try { x.src.stop(); } catch {} }
            _release(x);
            _toElement(x);
        }
        _lastIndex = null;
        _ttsStreamQueue.unshift(...stuck);
        _pump(_ttsStreamGen);
    }, ms);
    item.watch = watch((item.endAt - now) * 1000 + 3000);
    src.start(start);
};

/** Where this tab's voice is right now, for anything that follows it (her
 * avatar's mouth). Read-only: never touches playback. Streamed chunk ->
 * {stream_id, index, seg, t}; the whole-blob lane -> {legacy: true, t}; silent -> null. */
export const speechClock = () => {
    const item = _ttsStreamCurrent;
    if (item?.audio && !item.audio.paused && _ttsStreamPlayer === item.audio) {
        return { stream_id: _currentStreamId, index: item.index, seg: item.seg || 0, t: item.audio.currentTime };
    }
    if (_scheduled.length && _pctx) {
        const t = _pctx.currentTime - (_pctx.outputLatency || 0);   // what the speakers play now
        const on = _scheduled.find(x => x.startAt <= t && t < x.endAt);
        if (on) return { stream_id: _currentStreamId, index: on.index, seg: on.seg || 0, t: t - on.startAt };
    }
    if (player && !player.paused) return { legacy: true, t: player.currentTime };
    return null;
};

// The element player: one <audio> per chunk, the fallback lane.
const _elDone = (gen) => {
    if (gen !== _ttsStreamGen) return;        // stopped + restarted under us
    _ttsStreamCleanupCurrent();
    _endWall = performance.now();
    _pump(gen);
};

const _elPlay = (item, gen) => {
    _ttsStreamCurrent = item;
    _ttsStreamPlayer = item.audio;
    _ttsStreamUrl = item.url;
    _ttsStreamPlayer.volume = muted ? 0 : volume;
    // Real-progress detection: 'timeupdate' fires as currentTime advances
    // during actual playback. The FIRST advance past 0 is the only in-browser
    // proof that decode + output succeeded — drives accurate played/failed
    // accounting and the silent-failure toast. (A dead OS sink still advances
    // currentTime, so this can't catch that case — nothing in-browser can —
    // but it catches codec failure, autoplay throttle, and abort.)
    const onProgress = () => {
        if (item.audio && item.audio.currentTime > 0) {
            item.audio.removeEventListener('timeupdate', onProgress);
            _recordChunk(item, 'played');
        }
    };
    _ttsStreamPlayer.addEventListener('timeupdate', onProgress);
    _ttsStreamPlayer.onended = () => {
        // currentTime>0 at end = the clip really advanced (covers very short
        // clips that may not have fired a timeupdate). Otherwise it ended
        // without ever progressing = silent.
        if (item.audio && item.audio.currentTime > 0) _recordChunk(item, 'played');
        else _recordChunk(item, 'silent-ended', _audioDiag(item.audio));
        _elDone(gen);
    };
    _ttsStreamPlayer.onerror = () => {
        // error.code 4 = MEDIA_ERR_SRC_NOT_SUPPORTED = codec/decode failure
        // (the OGG/Opus-on-hardened-Brave signature).
        _recordChunk(item, 'media-error', _audioDiag(item.audio));
        // Don't surface to user — keep draining queue
        _elDone(gen);
    };
    // Track when play() actually resolves (= in the media stack) so
    // _disposeItem can safely abort it later without rejecting a pending
    // promise. NOTE: resolution is NOT proof of audible output — that's why
    // _ttsStreamAnyPlayed is set on real progress (onProgress/onended), not
    // here. The optional-chain guards the rare legacy undefined return.
    const el = _ttsStreamPlayer;
    _ttsStreamPlayer.play()?.then(() => {
        if (gen !== _ttsStreamGen) {
            // Superseded while play() was pending: _disposeItem left this
            // element alone (aborting a pending play() rejects it), so it
            // would run to the end with no handle — the rarer "two voices"
            // overlap on slow machines. 2026-09-08.
            try { el.pause(); el.src = ''; } catch {}
            return;
        }
        if (item.audio) item.audio._playStarted = true;
        // Silent-stall watchdog. play() resolving is NOT proof of sound. If the
        // element never progresses (currentTime stuck at 0) AND never fires
        // ended/error within a generous window, it's the silent class the other
        // tags can't see: duration=0 / no decodable data / decoder refused
        // without firing error / dead OS sink / tab-muted. Record it with
        // diagnostics and advance so a dead chunk can't also hang the queue.
        // 2026-05-28.
        const a = item.audio;
        const stallMs = (a && Number.isFinite(a.duration) && a.duration > 0)
            ? a.duration * 1000 + 1500
            : 3000;
        setTimeout(() => {
            if (gen !== _ttsStreamGen) return;            // turn superseded
            if (!item.audio || item._recorded) return;    // already hit a terminal handler
            if (item.audio.currentTime > 0) return;       // progressed — onProgress owns it
            _recordChunk(item, 'silent-stalled', _audioDiag(item.audio));
            if (_ttsStreamCurrent === item) {
                // Detach handlers so a late ended/error can't double-advance.
                item.audio.onended = null;
                item.audio.onerror = null;
                _elDone(gen);
            }
        }, stallMs);
    }).catch(err => {
        // Classify for the turn summary. autoplay/abort stay quiet (recoverable
        // / happen during legitimate stop) but are still recorded so the
        // black-box can report "every chunk aborted".
        let tag = 'play-reject';
        if (err?.message?.includes('autoplay') || err?.name === 'NotAllowedError') tag = 'autoplay-blocked';
        else if (err?.name === 'AbortError') tag = 'aborted';
        _recordChunk(item, tag, (err?.name || '') + ' ' + (err?.message || '') +
                     (item.audio ? ' ' + _audioDiag(item.audio) : ''));
        _elDone(gen);
    });
};

/** Called by api.js SSE handler on `tts_chunk` events. Decoding starts at
 * once, so a chunk is ready to play the instant the one before it ends. */
export const enqueueTtsChunk = ({ audio_b64, content_type, index, seg, boundary, pause_after_ms, text, stream_id }) => {
    if (!audio_b64) return;
    // Drop stragglers from a stream the user just stopped (left-button "stop TTS").
    // Without this, chunks already in flight replay a beat after stop ("pump refill") —
    // the old code nulled _currentStreamId on stop, which disabled the guard below.
    if (stream_id && stream_id === _stoppedStreamId) {
        console.warn('[TTS-STREAM] dropping straggler from stopped stream', stream_id);
        return;
    }
    // Drop chunks tagged with a stream we've already preempted. Without
    // this, an in-flight chunk from a cancelled chat/replay can bleed
    // into the new stream's playback. herring #5.
    if (stream_id && _currentStreamId && stream_id !== _currentStreamId) {
        console.warn('[TTS-STREAM] dropping stale chunk from preempted stream', stream_id);
        return;
    }
    if (!_ttsStats) _ttsStats = _newTtsStats();  // defensive: chunk before start
    _ttsStats.received++;
    let blob;
    try {
        blob = _b64ToBlob(audio_b64, content_type);
    } catch (e) {
        console.warn('[TTS-STREAM] chunk decode failed, skipping', { index, err: e?.message });
        _ttsStats.failed++;
        _ttsStats.fails.push({ idx: index, outcome: 'decode-error', detail: e?.message });
        return;
    }
    const item = { blob, pause_after_ms: pause_after_ms || 0, index, seg: seg || 0, text, boundary };
    const gen = _ttsStreamGen;
    const ctx = _playCtx();
    if (ctx) {
        blob.arrayBuffer().then(ab => ctx.decodeAudioData(ab)).then(buf => {
            if (gen !== _ttsStreamGen || item.useEl) return;
            item.buffer = buf;
            item.counted = true;
            _decodeFails = 0;
            if (_ttsStats) _ttsStats.decoded++;
            _pump(gen);
        }).catch(e => {
            if (gen !== _ttsStreamGen || item.useEl) return;
            // One bad chunk plays on an element; two in a row = this browser
            // can't decode them: elements for the rest of the page.
            if (++_decodeFails >= 2) _webAudioBroken = true;
            console.warn(`[TTS-STREAM] Web Audio could not decode a chunk (${e?.message}); ` +
                         (_webAudioBroken ? '<audio> elements from here on' : 'this one plays on <audio>'));
            _toElement(item);
            _pump(gen);
        });
    } else if (!_toElement(item)) {
        return;
    }
    _ttsStreamQueue.push(item);
    if (!_ttsStreamActive) {
        _ttsStreamActive = true;
        _ttsStreamEnded = false;
        isStreaming = true;
    }
    _pump(gen);
};

/** Called by api.js SSE handler on `tts_stream_start`.
 * Accepts the brain's payload so we can read stream_id and preempt any
 * still-playing stream from a previous turn. herring #5. */
export const startTtsStream = (data = {}) => {
    const newId = data.stream_id || null;
    // One audio focus: a streaming turn silences a whole-blob player the
    // same way playText silences the queue. Before this a blob (the
    // fallback lane) kept talking under the new turn's chunks — the common
    // "two voices" overlap. 2026-09-08.
    _stopLegacyPlayer();
    if (_currentStreamId && newId && newId !== _currentStreamId &&
        (_ttsStreamActive || _ttsStreamQueue.length > 0 || _busy())) {
        // A NEW stream is starting while a different one is still active.
        // Preempt: tear down old playback so the new turn doesn't audio-bleed.
        console.log('[TTS-STREAM] new stream', newId, 'preempting', _currentStreamId);
        _ttsStreamStop();
    }
    _currentStreamId = newId;
    _stoppedStreamId = null;   // fresh stream — clear any prior stop guard
    // Not preempted (no stream ids) but an element or its pause timer still
    // live: their callbacks carry the old gen and would never advance the
    // new stream, leaving it wedged. End them with the old turn.
    if (_waitTimer) { clearTimeout(_waitTimer); _waitTimer = null; }
    if (_ttsStreamCurrent) _ttsStreamCleanupCurrent();
    _ttsStreamGen += 1;
    _ttsStreamQueue = [];
    _ttsStreamEnded = false;
    _ttsStreamAnyPlayed = false;
    _lastIndex = null;         // a new turn's indexes start over
    _ttsStats = _newTtsStats();  // fresh black-box for this turn
    // The pump is live from its first push — that's the mic ⏹'s real
    // lifetime, not the first decoded chunk. On a slow CPU the gap between
    // the two is the whole synth; ⏹ pressed inside it mutes the pump so
    // nothing ever plays. endTtsStream / _ttsStreamStop clear it.
    isStreaming = true;
    // Don't clear _ttsStreamActive — first chunk arrival kicks playback.
};

/** Called by api.js SSE handler on `tts_stream_end`. */
export const endTtsStream = (data = {}) => {
    // Late end-event from a stream we've already preempted: no-op (the
    // preempting startTtsStream already tore down its state). herring #5.
    if (data.stream_id && _currentStreamId && data.stream_id !== _currentStreamId) {
        return;
    }
    // ...and the end of the stream just stopped: its stats were dropped with it, and a
    // stop in its first second read as "chunks couldn't play" (2026-11-04)
    if (data.stream_id && data.stream_id === _stoppedStreamId) return;
    _ttsStreamEnded = true;
    // Unconditional cleanup of streaming-state flags: a stream that emitted
    // zero playable chunks never set _ttsStreamActive, and the "speaking"
    // indicators stayed dirty until the next stream. Idempotent — safe while
    // the player is still draining. 2026-05-18 herring-table #11.
    if (_ttsStreamQueue.length === 0 && !_busy()) _finish();
};

const _ttsStreamStop = () => {
    _ttsStreamGen += 1;       // any in-flight setTimeout / callbacks become no-ops
    // Dispose preloaded audio elements in the queue too — they hold blob
    // URLs and decoded buffers that should be released immediately.
    for (const item of _ttsStreamQueue) _disposeItem(item);
    _ttsStreamQueue = [];
    for (const item of _scheduled) {
        if (item.src) { item.src.onended = null; try { item.src.stop(); } catch {} }
        _release(item);
    }
    _scheduled = [];
    _lastIndex = null;
    if (_waitTimer) { clearTimeout(_waitTimer); _waitTimer = null; }
    _ttsStreamEnded = true;
    _ttsStreamActive = false;
    _ttsStreamAnyPlayed = false;
    _stoppedStreamId = _currentStreamId || _stoppedStreamId;  // drop its in-flight stragglers
    _ttsStats = null;         // a stopped stream is not a silent one: no "couldn't play" toast for it
    _currentStreamId = null;  // herring #5 — no stream is current after stop
    _ttsStreamCleanupCurrent();
    _armIdle();
};

// Subscribe to SSE-relayed streaming-TTS events at module load.
busOn('tts_stream_start', (data) => startTtsStream(data || {}));
busOn('tts_stream_chunk', (data) => enqueueTtsChunk(data || {}));
busOn('tts_stream_end', (data) => endTtsStream(data || {}));

/**
 * Encode PCM samples as WAV file
 * @param {Float32Array} samples - Audio samples (-1 to 1)
 * @param {number} sampleRate - Sample rate in Hz
 * @returns {Blob} WAV file blob
 */
export function encodeWAV(samples, sampleRate) {
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
export function downsample(buffer, sourceSampleRate, targetSampleRate) {
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
            audio: micAudio({
                channelCount: 1,
                sampleRate: { ideal: SAMPLE_RATE },
                echoCancellation: true,
                noiseSuppression: true
            })
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
        ui.showToast(await micWhy(e), 'error');
        return false;
    }
};

const stopRec = async () => {
    if (!audioContext || audioChunks.length === 0) {
        // Clean up resources even if no audio was captured (quick tap)
        try { sourceNode?.disconnect(); processorNode?.disconnect(); } catch {}
        if (audioContext) { try { audioContext.close(); } catch {} audioContext = null; }
        if (mediaStream) { mediaStream.getTracks().forEach(t => t.stop()); mediaStream = null; }
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

const signalMicActive = (active) => {
    const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
    fetch('/api/mic/active', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
        body: JSON.stringify({ active }),
        keepalive: !active,  // keepalive on deactivation — survives tab close/navigation
    }).catch(() => {});
};

export const handlePress = async (btn) => {
    if (isRec) return;
    const ok = await startRec();
    if (ok) {
        isRec = true;
        btn.classList.add('recording');
        ui.showStatus();
        ui.updateStatus('Recording...');
        signalMicActive(true);
        dispatch(Events.STT_RECORDING_START, { source: 'browser' });
    }
};

export const handleRelease = async (btn, triggerSendFn) => {
    if (!isRec) return;
    isRec = false;
    // ONE unconditional exit signal (outer finally). stopRec() throwing
    // BEFORE the old inner try (device unplug, revoked permission,
    // audioContext failure) skipped every signalMicActive(false) path and
    // stranded a permanent +1 on the wakeword-suppression counter — she
    // stopped answering to her name until restart (negspace N3, 2026-08-31).
    // Server clamps at 0, so the old double-signal paths stay harmless.
    try {
        let blob = null;
        try {
            blob = await stopRec();
        } catch (e) {
            console.error('stopRec failed:', e);
        }
        btn.classList.remove('recording');
        dispatch(Events.STT_RECORDING_END, { source: 'browser' });

        if (blob && blob.size > 1000) {
            ui.updateStatus('Transcribing...');
            dispatch(Events.STT_PROCESSING, { source: 'browser' });
            try {
                const response = await api.postAudio(blob);
                // /api/transcribe manages _web_active in its own finally block
                const text = response.text;

                if (!text || !text.trim()) {
                    const msg = response.quiet
                        ? "Sapphire got silence. Check which mic the browser is using (the icon left of the address)."
                        : "Didn't catch any words. Hold the mic button while you talk.";
                    ui.updateStatus(msg);
                    setTimeout(() => ui.hideStatus(), 3000);
                    return null;
                }

                ui.hideStatus();
                await triggerSendFn(text);
                return text;

            } catch (e) {
                console.error('Transcription failed:', e);
                // the server's reason (privacy gate, timeout, not ready) is the message
                ui.hideStatus();
                ui.showToast(e.message || "Sapphire couldn't turn that recording into text. Try again.", 'error');
                return null;
            }
        }
        ui.hideStatus();
        return null;
    } finally {
        signalMicActive(false);
    }
};

export const forceStop = (btn, triggerSendFn) => {
    if (isRec) handleRelease(btn, triggerSendFn);
};

export const getRecState = () => isRec;