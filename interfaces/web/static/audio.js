// audio.js - Audio lifecycle with native WAV recording (no server-side ffmpeg needed)
import * as ui from './ui.js';
import * as api from './api.js';
import { dispatch, on as busOn, Events } from './core/event-bus.js';

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
const SAMPLE_RATE = 16000;
const NUM_CHANNELS = 1;

// Volume state
let volume = 1.0;
let muted = false;

// Volume control exports
export const setVolume = (val) => {
    volume = Math.max(0, Math.min(1, val));
    if (player) player.volume = muted ? 0 : volume;
    if (_ttsStreamPlayer) _ttsStreamPlayer.volume = muted ? 0 : volume;
};

export const setMuted = (val) => {
    muted = val;
    if (player) player.volume = muted ? 0 : volume;
    if (_ttsStreamPlayer) _ttsStreamPlayer.volume = muted ? 0 : volume;
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

export const stopLocalTts = async () => {
    try {
        await api.stopLocalTts();
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
    // Tear down the streaming-TTS chunk queue too — Stop must cut both
    // legacy playback (full-blob) and the new per-chunk queue.
    _ttsStreamStop();
    isStreaming = false;
    cleanup();
    // Always tell server to stop TTS — even if we don't think it's playing yet.
    // Server stop is idempotent and prevents races where TTS starts after our check.
    stopLocalTts();
};

export const isTtsPlaying = () => isStreaming;

export const playText = async (txt, cacheKey = null) => {
    stop(true);
    isStreaming = true;
    ttsCtrl = new AbortController();

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
            blob = await api.fetchAudio(clean, ttsCtrl.signal);
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

        // Bail if stopped while fetching
        if (!isStreaming) return;

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
            isStreaming = false;
            ui.hideStatus();
            cleanup();
        };

        player.onerror = e => {
            console.error('Audio error:', e);
            isStreaming = false;
            ui.hideStatus();
            cleanup();
            ui.showToast('Audio playback failed — check TTS provider', 'error');
        };

        await player.play();
        ui.hideStatus();
    } catch (e) {
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
            ui.showToast('TTS response was not playable audio — check TTS provider / proxy.', 'error');
        } else if (cls.includes('AbortError')) {
            ui.showToast('Audio playback failed — check system audio output or browser autoplay permissions.', 'warning');
        } else {
            ui.showToast(`Audio error: ${msg}`, 'error');
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
    const prose = extractProseForTts(content);
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
 * Returns true if at least one chunk arrived, false if the endpoint was
 * unavailable (503) so the caller can fall back. Other errors throw. */
export const playTextStreaming = async (text, voice = null) => {
    stop(true);  // clear any current playback before starting new stream

    const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
    let res;
    try {
        res = await fetch('/api/tts/stream', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
            body: JSON.stringify(voice ? { text, voice } : { text }),
        });
    } catch (e) {
        throw new Error(`network: ${e.message}`);
    }

    if (res.status === 503 || res.status === 404) {
        // Server declined — caller should fall back to whole-blob path.
        try { await res.body?.cancel(); } catch {}
        return false;
    }
    if (!res.ok) {
        let detail = '';
        try { detail = (await res.json()).detail || ''; } catch {}
        throw new Error(`HTTP ${res.status}${detail ? ': ' + detail : ''}`);
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
    } finally {
        try { await reader.cancel(); } catch {}
    }
    return sawChunk;
};

// Extract prose text for TTS (mirrors ui.extractProseText logic)
const extractProseForTts = (el) => {
    const clone = el.cloneNode(true);
    clone.querySelectorAll('details, .tool-block, .message-metadata, pre, code').forEach(e => e.remove());
    return clone.textContent.trim();
};

// Clear TTS cache (call on chat switch/history reload)
export const clearTtsCache = () => {
    ttsCache.clear();
};

// ---------------------------------------------------------------------------
// Streaming TTS playback (v2.7.0)
//
// Brain-side chunker emits per-sentence OGG blobs over SSE; we queue + play
// them in order via short-lived Audio elements. Each chunk is independently
// decodable. Pause-between-chunks honors the brain's `pause_after_ms`.
// ---------------------------------------------------------------------------
let _ttsStreamGen = 0;       // generation counter — bumped by stop() to invalidate stale chunks
let _ttsStreamActive = false;
let _ttsStreamQueue = [];    // [{ blob, pause_after_ms, index, text, boundary }]
let _ttsStreamPlayer = null;
let _ttsStreamUrl = null;
let _ttsStreamEnded = false; // server sent tts_stream_end (no more chunks coming)
let _ttsStreamSawChunk = false; // any chunk arrived this turn? signals send-handlers to skip legacy audioFn
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
// second caller (endTtsStream vs _ttsStreamPlayNext, whichever lands second)
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

let _ttsStreamCurrent = null;  // {audio, url, blob, pause_after_ms, index, text, boundary}

const _ttsStreamPlayNext = (gen) => {
    if (gen !== _ttsStreamGen) return;        // stopped + restarted under us
    if (!_ttsStreamActive) return;
    if (_ttsStreamQueue.length === 0) {
        // Queue empty: either we're done (end marker received) or waiting.
        if (_ttsStreamEnded) {
            _ttsStreamMaybeWarnSilent();
            _ttsStreamActive = false;
            _ttsStreamCleanupCurrent();
            isStreaming = false;
        } else {
            // Slow CPU: queue drained before next chunk arrived. Null the
            // player so enqueueTtsChunk can detect idle state and restart
            // playback when new audio arrives. 2026-05-20.
            _ttsStreamCleanupCurrent();
        }
        return;
    }
    const item = _ttsStreamQueue.shift();
    _ttsStreamCleanupCurrent();
    _ttsStreamCurrent = item;
    _ttsStreamPlayer = item.audio;
    _ttsStreamUrl = item.url;
    if (!_ttsStreamPlayer) {
        // Defensive — should have been pre-built on enqueue
        console.warn('[TTS-STREAM] item missing audio element, skipping');
        _ttsStreamPlayNext(gen);
        return;
    }
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
        const pause = item.pause_after_ms || 0;
        if (pause > 0) {
            setTimeout(() => _ttsStreamPlayNext(gen), pause);
        } else {
            _ttsStreamPlayNext(gen);
        }
    };
    _ttsStreamPlayer.onerror = () => {
        // error.code 4 = MEDIA_ERR_SRC_NOT_SUPPORTED = codec/decode failure
        // (the OGG/Opus-on-hardened-Brave signature).
        _recordChunk(item, 'media-error', _audioDiag(item.audio));
        // Don't surface to user — keep draining queue
        _ttsStreamPlayNext(gen);
    };
    // Track when play() actually resolves (= in the media stack) so
    // _disposeItem can safely abort it later without rejecting a pending
    // promise. NOTE: resolution is NOT proof of audible output — that's why
    // _ttsStreamAnyPlayed is set on real progress (onProgress/onended), not
    // here. The optional-chain guards the rare legacy undefined return.
    _ttsStreamPlayer.play()?.then(() => {
        if (item.audio) item.audio._playStarted = true;
        // Silent-stall watchdog. play() resolving is NOT proof of sound. If the
        // element never progresses (currentTime stuck at 0) AND never fires
        // ended/error within a generous window, it's the silent class the other
        // tags can't see: duration=0 / no decodable data / decoder refused
        // without firing error / dead OS sink / tab-muted. That case otherwise
        // produces a totally silent turn with ZERO log — the worst no-audio
        // report to debug. Record it with diagnostics and advance so a dead
        // chunk can't also hang the queue. 2026-05-28.
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
                _ttsStreamPlayNext(gen);
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
        _ttsStreamPlayNext(gen);
    });
};

/** Called by api.js SSE handler on `tts_chunk` events.
 *
 * The Audio element is built HERE (not when the chunk is popped to play),
 * so the browser starts preloading/decoding while the previous chunk is
 * still playing. By the time we hit play() the OGG is ready — eliminates
 * the per-chunk Audio() startup gap that was stacking with the intentional
 * pause_after_ms to make speech feel laggy on faster machines.
 */
export const enqueueTtsChunk = ({ audio_b64, content_type, index, boundary, pause_after_ms, text, stream_id }) => {
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
    // Decode + Audio() build can throw on bad base64 or unsupported codec.
    // Build the blob/URL/audio element BEFORE setting sawChunk — otherwise
    // a malformed first chunk crashes the decode, sawChunk stays true, and
    // send-handlers skips the legacy fallback → total silent failure with
    // no audible output. 2026-05-18 herring-table #9.
    let blob, url, audio;
    try {
        blob = _b64ToBlob(audio_b64, content_type);
        url = URL.createObjectURL(blob);
        audio = new Audio(url);
        audio.preload = 'auto';   // hint browser to start decoding immediately
        // `new Audio(url)` already starts loading; explicit `.load()` after
        // construction can be interpreted by Chromium (incl. Brave) as a
        // load-restart, leaving subsequent .play() in a state that rejects
        // with "AbortError: interrupted by new load request" in some
        // environments. Dropped 2026-05-26 user-report scouting party.
    } catch (e) {
        console.warn('[TTS-STREAM] chunk decode failed, skipping', { index, err: e?.message });
        if (_ttsStats) {
            _ttsStats.failed++;
            _ttsStats.fails.push({ idx: index, outcome: 'decode-error', detail: e?.message });
        }
        if (url) { try { URL.revokeObjectURL(url); } catch {} }
        return;  // sawChunk NOT set — legacy fallback can still fire
    }
    if (_ttsStats) _ttsStats.decoded++;
    _ttsStreamSawChunk = true;
    _ttsStreamQueue.push({
        audio, url, blob,
        pause_after_ms: pause_after_ms || 0,
        index, text, boundary,
    });
    if (!_ttsStreamActive) {
        _ttsStreamActive = true;
        _ttsStreamEnded = false;
        isStreaming = true;
        _ttsStreamPlayNext(_ttsStreamGen);
    } else if (!_ttsStreamPlayer) {
        // Consumer stalled on empty queue (slow CPU / Kokoro couldn't keep
        // up) — _ttsStreamPlayNext nulled the player when it found nothing
        // to play. Wake it back up now that new audio has arrived. 2026-05-20.
        _ttsStreamPlayNext(_ttsStreamGen);
    }
};

/** Called by api.js SSE handler on `tts_stream_start`.
 * Accepts the brain's payload so we can read stream_id and preempt any
 * still-playing stream from a previous turn. herring #5. */
export const startTtsStream = (data = {}) => {
    const newId = data.stream_id || null;
    if (_currentStreamId && newId && newId !== _currentStreamId &&
        (_ttsStreamActive || _ttsStreamQueue.length > 0 || _ttsStreamPlayer)) {
        // A NEW stream is starting while a different one is still active.
        // Preempt: tear down old playback so the new turn doesn't audio-bleed.
        console.log('[TTS-STREAM] new stream', newId, 'preempting', _currentStreamId);
        _ttsStreamStop();
    }
    _currentStreamId = newId;
    _stoppedStreamId = null;   // fresh stream — clear any prior stop guard
    _ttsStreamGen += 1;
    _ttsStreamQueue = [];
    _ttsStreamEnded = false;
    _ttsStreamSawChunk = false;
    _ttsStreamAnyPlayed = false;
    _ttsStats = _newTtsStats();  // fresh black-box for this turn
    // Don't clear _ttsStreamActive — first chunk arrival kicks playback.
};

/** Called by api.js SSE handler on `tts_stream_end`. */
export const endTtsStream = (data = {}) => {
    // Late end-event from a stream we've already preempted: no-op (the
    // preempting startTtsStream already tore down its state). herring #5.
    if (data.stream_id && _currentStreamId && data.stream_id !== _currentStreamId) {
        return;
    }
    _ttsStreamEnded = true;
    // Unconditional cleanup of streaming-state flags. The old version only
    // cleaned up if `_ttsStreamActive` was true, but a stream that emitted
    // zero successful chunks (e.g. all synth failed, or plugin muted all)
    // never set _ttsStreamActive, so the "speaking" indicators stayed
    // dirty until the next stream. Cleanup is idempotent — safe to run
    // even when the player is still draining. 2026-05-18 herring-table #11.
    if (_ttsStreamQueue.length === 0 && !_ttsStreamPlayer) {
        _ttsStreamMaybeWarnSilent();
        _ttsStreamActive = false;
        isStreaming = false;
        _ttsStreamCleanupCurrent();
    }
};

/** True if any chunk arrived in the current turn — send-handlers uses this
 * to skip the legacy end-of-stream audioFn(prose) fallback. */
export const ttsStreamSawChunk = () => _ttsStreamSawChunk;

const _ttsStreamStop = () => {
    _ttsStreamGen += 1;       // any in-flight setTimeout / callbacks become no-ops
    // Dispose preloaded audio elements in the queue too — they hold blob
    // URLs and decoded buffers that should be released immediately.
    for (const item of _ttsStreamQueue) _disposeItem(item);
    _ttsStreamQueue = [];
    _ttsStreamEnded = true;
    _ttsStreamActive = false;
    _ttsStreamSawChunk = false;
    _ttsStreamAnyPlayed = false;
    _stoppedStreamId = _currentStreamId || _stoppedStreamId;  // drop its in-flight stragglers
    _currentStreamId = null;  // herring #5 — no stream is current after stop
    _ttsStreamCleanupCurrent();
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
        const msg = e.name === 'NotFoundError' || e.message?.includes('not be found')
            ? 'No microphone found. Check your audio device.'
            : 'Mic access denied';
        alert(msg);
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
    const blob = await stopRec();
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
                    ? 'No audio received — check browser mic selection'
                    : 'No speech detected';
                ui.updateStatus(msg);
                setTimeout(() => ui.hideStatus(), 3000);
                return null;
            }

            ui.hideStatus();
            await triggerSendFn(text);
            return text;

        } catch (e) {
            console.error('Transcription failed:', e);
            const msg = e.message?.includes('disabled') || e.message?.includes('not initialized')
                ? e.message : 'Transcription failed';
            ui.updateStatus(msg);
            setTimeout(() => ui.hideStatus(), 2000);
            return null;
        } finally {
            signalMicActive(false);
        }
    } else {
        signalMicActive(false);
        ui.hideStatus();
        return null;
    }
};

export const forceStop = (btn, triggerSendFn) => {
    if (isRec) handleRelease(btn, triggerSendFn);
};

export const getRecState = () => isRec;