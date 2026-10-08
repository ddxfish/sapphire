// handlers/send-handlers.js - Send, stop, and input handlers
import * as api from '../api.js';
import * as ui from '../ui.js';
import * as audio from '../audio.js';
import * as chat from '../chat.js';
import * as Images from '../ui-images.js';
import { dispatch, on, Events } from '../core/event-bus.js';
import { focusUnlessEditing } from '../shared/dom-guard.js';
import { getBoundChat } from '../core/bound-chat.js';
import {
    getElements,
    getIsProc,
    getTtsEnabled,
    getPromptPrivacyRequired,
    setProc,
    setAbortController,
    getAbortController,
    setIsCancelling,
    getIsCancelling,
    refresh,
    setHistLen,
    setSendLabel
} from '../core/state.js';

// This tab's sends still in flight, by ticket (2026-10-08). A send OWNS its
// turn for the turn's whole life, not for the life of its first socket: the
// server says `queued` and closes the socket when the chat is busy (no
// connection is held while a turn waits - six held connections hit the
// browser's per-host cap and froze Stop and ×), and a phone's screen lock
// kills a live socket outright. Either way the owner is still here, in this
// map, and FOLLOWS its ticket: the bus (`inbox_ticket`) says when the turn
// started, merged or was dropped; a reattach by ticket replays what was
// missed. The previous shape handed a lost turn to a "viewer" and let the
// dead send's handlers arm the live slot afterwards - Stop lit, Send dead,
// transcript frozen (race scout, 2026-10-07).
const pending = new Map();
const BACKOFF_MS = [1000, 2000, 4000, 8000, 15000, 15000];
const CHECK_EVERY_MS = 20000;     // a slow belt under the bus: waiting/lost sends check in

on('inbox_ticket', (d) => {
    const ctl = pending.get(d?.ticket);
    if (!ctl) return;                                    // another tab's send
    if (d.state === 'started') ctl.started();
    else if (d.state === 'merged') ctl.merged();
    else if (d.state === 'dropped') ctl.dropped(d.reason || 'dropped');
});
const checkPending = () => { for (const ctl of pending.values()) ctl.checkIn(); };
on('bus_connected', checkPending);
window.addEventListener('online', checkPending);
document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') checkPending(); });
setInterval(() => { if (pending.size) checkPending(); }, CHECK_EVERY_MS);

// refocus=false: a dictated turn (triggerSendWithText) — refocusing the
// composer after it pops the phone keyboard on every voice turn (D2#4).
// Wired as a click listener too: an Event has no `refocus` → default true.
// `text`: send THIS instead of the composer's contents and leave the composer
// alone (a question card's answer must not overwrite a draft the person was
// typing). `attach=false`: the staged images/files stay staged (they belong to
// the draft, not to a card's clicks). Race scout, 2026-10-07.
export async function handleSend({ refocus = true, text = null, attach = true } = {}) {
    // Inbox (2026-10-06, wave I-2): a send while a turn is live is no longer
    // blocked — it waits its turn in the chat's inbox, server-side, in order
    // with everything else. The bubble pulses until the turn starts; the
    // live turn keeps Stop, the status line and the abort slot until then.
    //
    // The SERVER says whether this send queued (its first event is `queued`),
    // never this tab's busy flag: the flag misses audio tails, pinned agent
    // and satellite turns, and other tabs. So every send starts un-armed and
    // arms on its own first live event. The live slot (abort controller, busy
    // state, Stop) is OWNED: a turn only clears what it still owns — the inbox
    // starts the next turn within milliseconds of this one ending.
    const { input, sendBtn } = getElements();
    const fromCard = typeof text === 'string';
    const txt = fromCard ? text.trim() : input.value.trim();
    if (!txt && !(attach && (Images.hasPendingUploadImages() || Images.hasPendingFiles()))) return;

    // Block send if the prompt is private but this chat isn't
    // (status already computes: prompt private AND chat not private)
    if (getPromptPrivacyRequired()) {
        ui.showToast('This prompt is marked private — toggle the eyeball to make this chat private', 'error');
        return;
    }

    dispatch(Events.USER_SENT, { text: txt });

    if (!fromCard) {
        input.value = '';
        input.dispatchEvent(new Event('input'));
    }
    
    // Get pending images and files, then clear them (not for a card's answer)
    const pendingImages = attach ? Images.getImagesForApi() : [];
    const hasImages = pendingImages.length > 0;
    const pendingFilesForApi = attach ? Images.getFilesForApi() : [];
    const hasFiles = pendingFilesForApi.length > 0;

    ui.addUserMessage(txt, hasImages ? Images.getPendingUploadImages() : null, hasFiles ? Images.getPendingFiles() : null);
    if (attach) {
        Images.clearPendingUploadImages();
        Images.clearPendingFiles();
        updateImagePreviewArea();
    }

    // This send's bubble: it pulses, carries its place in line and a × that
    // takes it back out of the inbox, from the moment the server says `queued`.
    const bubbles = document.querySelectorAll('#chat-container .message.user');
    const myBubble = bubbles[bubbles.length - 1] || null;

    // The owner. `state`: sent (first socket open, turn not started) → queued
    // (socket closed, waiting for the bus) → live (armed, streaming) → lost
    // (socket died, following) → done. `controller` is replaced on every
    // follow; the live slot compares against the CURRENT one.
    const ctl = {
        ticket: null, chat: null, state: 'sent', controller: new AbortController(),
        armed: false, streamOk: false, streamId: -1, lastSeq: null, following: false,
        headless: false,     // the view moved to another chat before this turn ran: nothing is painted here
    };
    // The chat this tab is LOOKING at. The server pins the turn to the chat it
    // was sent from (ctl.chat); if the view has moved elsewhere by the time the
    // turn runs, its reply must not be painted - or swapped - into the chat on
    // screen (two scouts, 2026-10-07: B's transcript showed A's reply, then a
    // duplicate of B's own last row).
    const viewChat = () => getBoundChat() || document.getElementById('chat-select')?.value || null;
    const viewMoved = () => !!(ctl.chat && viewChat() && viewChat() !== ctl.chat);
    const ownsLive = () => getAbortController() === ctl.controller;
    const solid = () => {
        if (!myBubble) return;
        myBubble.classList.remove('queued');
        myBubble.title = '';
        myBubble.querySelector('.queued-x')?.remove();
    };
    const armLive = () => {
        // our turn started: take the status line, Stop and the abort slot
        if (ctl.armed) return;
        ctl.armed = true;
        ctl.state = 'live';
        solid();
        if (viewMoved()) { ctl.headless = true; return; }   // runs in its own chat; this view is someone else's
        setAbortController(ctl.controller);
        setIsCancelling(false);
        setProc(true);
        sendBtn.disabled = true;
        setSendLabel('busy');
        ui.showStatus();
        ui.updateStatus('Generating...');
    };
    // The ONE place this send gives anything back - and only what it still
    // owns: a send that never ran owns nothing; once a later turn armed itself
    // the slot is that turn's (finishStreaming's 500 ms sleep is long enough
    // for the inbox to have started it).
    const release = () => {
        if (ctl.state === 'done') return;
        ctl.state = 'done';
        if (ctl.ticket) pending.delete(ctl.ticket);
        if (ctl.armed && ownsLive()) {
            ui.hideStatus();
            sendBtn.disabled = false;
            setSendLabel('send');
            setProc(false);
        } else if (!ctl.armed && !getIsProc()) {
            ui.hideStatus();                    // the 'Connecting…' hint, nothing else was ours
        }
    };
    const pulse = (position) => {
        if (!myBubble || ctl.armed) return;
        // a refresh in the gap before `queued` may have dropped the optimistic
        // bubble (no key): it belongs at the end of the chat it was sent from
        if (!myBubble.isConnected && !viewMoved()) document.getElementById('chat-container')?.appendChild(myBubble);
        myBubble.dataset.ticket = ctl.ticket || '';
        myBubble.title = position > 1 ? `Waiting — ${position} in line` : 'Waiting for Sapphire to finish…';
        if (!myBubble.classList.contains('queued')) {
            myBubble.classList.add('queued');
            const x = document.createElement('button');
            x.className = 'queued-x';
            x.type = 'button';
            x.title = 'Take it back out of the queue';
            x.textContent = '×';
            x.addEventListener('click', async (e) => {
                e.stopPropagation();
                if (!ctl.ticket) return;
                try { await api.dropQueued(ctl.ticket, ctl.chat); } catch (err) { console.warn('[QUEUE] drop failed:', err); }
            });
            myBubble.appendChild(x);
        }
    };
    const queueHooks = {
        onTicket: (ticket, chat) => {          // every send: its identity, from the first line
            ctl.ticket = ticket; ctl.chat = chat || null;
            pending.set(ticket, ctl);
        },
        onQueued: (ticket, position, chat) => {
            ctl.ticket = ticket; ctl.chat = chat || null;
            if (ctl.state !== 'sent') return;   // the bus already said it started
            ctl.state = 'queued';               // the socket closes after this line
            pulse(position);
        },
        onQueuedDropped: (reason) => ctl.dropped(reason),
        onMerged: () => ctl.merged(),
    };

    if (!getIsProc()) {
        // nothing of ours is live in this tab: a hint while the server decides
        ui.showStatus();
        ui.updateStatus('Connecting...');
    }

    const audioFn = getTtsEnabled() ? audio.playText : null;
    // Stream-id capture: Stop→immediate-Send can leave OLD stream's trailing
    // chunks in the SSE pipeline. They'd previously land on the NEW message's
    // DOM (visible content bleed). Capture ui's stream-id at the moment WE call
    // startStreaming; later UI calls bail if the id changed. 2026-05-14.
    const streamStillMine = () => ctl.streamId !== -1 && ui.getCurrentStreamId() === ctl.streamId;
    const openBubble = () => {
        if (ctl.streamOk || ctl.headless) return;
        ui.updateStatus('Generating...');
        ui.startStreaming();
        ctl.streamId = ui.getCurrentStreamId();
        ctl.streamOk = true;
    };

    // Handlers are named once so the SAME set rides every follow — the bubble
    // keeps filling in place.
    const onChunk = chunk => {
        armLive();
        openBubble();
        if (ctl.headless || !streamStillMine()) return;  // not this view's / a newer stream took over
        ui.appendStream(chunk);
        if (ui.hasVisibleContent()) ui.hideStatus();   // hide status once actual visible content appears
    };
    const onComplete = async (ephemeral, { ttsStreamed = false } = {}) => {
        if (ctl.headless) {
            ui.showToast(`Her reply landed in “${ctl.chat}”`, 'info');
            return;
        }
        if (getIsCancelling()) {
            console.log('Stream completed but cancellation in progress - skipping finishStreaming');
            return;
        }
        if (ephemeral) {
            console.log('[EPHEMERAL] Module response - skipping TTS and swap');
            await ui.finishStreaming(true);
            await refresh(false);
            return;
        }
        if (!ctl.streamOk) return;
        await ui.finishStreaming();
        // Note: finishStreaming already syncs with history - no refresh needed
        //
        // Whole-blob playback ONLY when the server's streaming-TTS pump never
        // reached this tab's ears: the pump never ran (streaming off, provider
        // can't stream, privacy gate — the server says so on llm_done), or this
        // tab followed the turn after her voice had already started (the
        // reattach says `attached: audio=false`). streamStillMine: Send is back
        // during finishStreaming's sleep now; a new turn bumps the stream id
        // and owns audio.
        if (!ttsStreamed && audioFn && streamStillMine()) {
            const el = document.querySelector('.message.assistant:last-of-type .message-content')
                    || document.querySelector('.message.assistant:last-child .message-content');
            if (el) audioFn(ui.extractProseText(el));
        }
    };
    const onToolStart = (id, name, args) => {
        armLive();
        openBubble();
        if (ctl.headless || !streamStillMine()) return;
        ui.startTool(id, name, args);
    };
    const onToolEnd = (id, name, result, error) => {
        if (ctl.headless || !streamStillMine()) return;
        ui.endTool(id, name, result, error);
    };
    const onStreamStarted = () => { armLive(); if (!ctl.headless) ui.updateStatus('Processing...'); };
    const onIterationStart = (iteration) => {
        if (iteration > 1 && !ctl.headless) {
            ui.showStatus();
            ui.updateStatus('Generating...');
        }
    };
    const onResync = () => { ctl.streamId = ui.getCurrentStreamId(); ctl.streamOk = true; };
    const onError = async (e, statusCode, lastSeq) => {
        if (typeof lastSeq === 'number') ctl.lastSeq = lastSeq;
        if (e.message === 'Cancelled') {
            console.log('Stream cancelled by user');
            if (ctl.streamOk) ui.cancelStreaming();
            return;
        }
        if (e.feedLost) {
            // Our socket died (phone lock, tab sleep, a proxy) — the turn did
            // NOT. Keep the bubble and follow our ticket; nothing is released.
            if (ctl.state !== 'done') { ctl.state = 'lost'; ctl.follow(); }
            return;
        }
        if (statusCode === 409) {
            // Server gate (a sealed or missing chat): undo the optimistic
            // paint — text back in the box, bubble gone. Attached images/files
            // are dropped (rare).
            if (!fromCard) {
                input.value = txt;
                input.dispatchEvent(new Event('input'));
            }
            myBubble?.remove();
            ui.showToast(e.message, 'error');
            return;
        }
        console.error('Stream failed:', e.message);
        if (ctl.streamOk) ui.cancelStreaming();
        ui.showToast(e.message, 'error');
    };
    const liveHandlers = { onChunk, onComplete, onError, onToolStart, onToolEnd, onStreamStarted, onIterationStart, onResync };

    // ── the owner's life after its first socket ──────────────────────────
    ctl.started = () => {                       // the bus: our turn began with no socket on it
        if (ctl.state === 'sent' || ctl.state === 'queued') { ctl.state = 'starting'; ctl.follow(); }
    };
    ctl.merged = () => {                        // folded into the turn ahead: one turn, one reply, streamed by that send
        solid();
        release();
    };
    ctl.dropped = (reason) => {
        myBubble?.remove();
        if (!fromCard && !input.value) {
            input.value = txt;
            input.dispatchEvent(new Event('input'));
        }
        ui.showToast(`Not sent: ${reason}`, 'warning');
        release();
    };
    ctl.checkIn = () => {                       // visibility / online / bus reconnect / the slow belt
        if (ctl.state === 'queued' || ctl.state === 'lost' || ctl.state === 'starting') ctl.follow();
    };
    const gone = async (reason) => {
        // nothing to follow: the turn ended while we were away (history has
        // it), or she restarted and lost track of the message
        if (ctl.streamOk && document.getElementById('streaming-message')) ui.detachStreaming();
        release();
        await refresh(false);
        if (reason === 'unknown') ui.showToast("Lost track of that message (she restarted?) — check the chat", 'warning');
        if (reason === 'unreachable') ui.showToast("Couldn't reconnect — her reply lands in the chat when she finishes", 'error');
    };
    ctl.follow = async () => {
        if (ctl.following || ctl.state === 'done') return;
        ctl.following = true;
        try {
            for (let i = 0; ; i++) {
                if (ctl.state === 'done') return;
                // a fresh fetch, the same owner: the live slot moves to the new
                // controller only if it still held the old one
                const old = ctl.controller;
                ctl.controller = new AbortController();
                if (ctl.armed && getAbortController() === old) setAbortController(ctl.controller);
                let lost = false;
                const h = {
                    ...liveHandlers,
                    onError: (e, code, seq) => {
                        if (typeof seq === 'number') ctl.lastSeq = seq;
                        if (e?.feedLost) { lost = true; return; }
                        liveHandlers.onError(e, code, seq);
                    },
                    onResync: async () => {
                        // the ring no longer reaches back to our seq: repaint
                        // from history, then keep streaming into a fresh bubble
                        if (ctl.streamOk) ui.detachStreaming();
                        await refresh(false);
                        ui.startStreaming();
                        onResync();
                    },
                };
                const r = await api.attachTurn(ctl.chat, ctl.lastSeq ?? 0, h, ctl.ticket,
                                               { audio: !!audioFn, signal: ctl.controller.signal });
                if (typeof r.lastSeq === 'number') ctl.lastSeq = r.lastSeq;
                if (r.queued) {                       // still in line: the bus will say
                    if (ctl.state !== 'live') { ctl.state = 'queued'; pulse(r.position); }
                    return;
                }
                if (r.live === true && !lost) { release(); return; }       // ended through the handlers
                if (r.live === false) { await gone('finished'); return; }
                if (r.unknown) { await gone('unknown'); return; }
                if (r.cancelled) { release(); return; }                    // our own Stop aborted the follow
                if (i >= BACKOFF_MS.length) { await gone('unreachable'); return; }
                ctl.state = 'lost';
                await new Promise(res => setTimeout(res, BACKOFF_MS[i]));
            }
        } finally {
            ctl.following = false;
        }
    };

    try {
        await api.streamChat(
            txt, onChunk, onComplete, onError,
            ctl.controller.signal,
            null,  // prefill
            onToolStart, onToolEnd, onStreamStarted, onIterationStart,
            hasImages ? pendingImages : null,
            hasFiles ? pendingFilesForApi : null,
            queueHooks
        );
        return null;
    } catch (e) {
        if (e.message !== 'Cancelled') {
            console.error('Error send message:', e);
            ui.showToast(e.message, 'error');
        }
        return null;
    } finally {
        // The first socket is done. A send that is still waiting (queued) or
        // following (lost) stays an owner - its follow releases. Everything
        // else - streamed to the end, merged, dropped, refused - releases here.
        if (ctl.state === 'queued' || ctl.state === 'lost' || ctl.state === 'starting') {
            if (!ctl.armed && !getIsProc()) ui.hideStatus();   // the 'Connecting…' hint
        } else {
            release();
        }
        // Not unconditional: the user may have moved into a sidebar textarea
        // while she replied — yanking the cursor back was the seeded case of
        // the DOM-refresh hunt, 2026-09-08. Dictated turns skip it entirely
        // (nothing editable holds focus after a mic tap, so the guard alone
        // couldn't stop the keyboard pop).
        if (refocus) focusUnlessEditing(input);
    }
}

// Update image/file preview area in DOM
function updateImagePreviewArea() {
    const previewArea = document.getElementById('image-preview-area');
    if (!previewArea) return;

    previewArea.innerHTML = '';
    const pendingImgs = Images.getPendingUploadImages();
    const pendingFilesList = Images.getPendingFiles();

    if (pendingImgs.length === 0 && pendingFilesList.length === 0) {
        previewArea.style.display = 'none';
        return;
    }

    previewArea.style.display = 'flex';
    pendingImgs.forEach((img, idx) => {
        const preview = Images.createUploadPreview(img, idx, (index) => {
            Images.removePendingUploadImage(index);
            updateImagePreviewArea();
        });
        previewArea.appendChild(preview);
    });
    pendingFilesList.forEach((file, idx) => {
        const chip = Images.createFilePreview(file, idx, (index) => {
            Images.removePendingFile(index);
            updateImagePreviewArea();
        });
        previewArea.appendChild(chip);
    });
}

// Handle text file upload (read client-side)
export async function handleFileUpload(file) {
    const dot = file.name.lastIndexOf('.');
    const ext = dot !== -1 ? file.name.slice(dot).toLowerCase() : '';

    if (!Images.ALLOWED_FILE_EXTENSIONS.has(ext)) {
        ui.showToast(`Unsupported file type: ${ext || 'no extension'}`, 'error');
        return;
    }

    if (file.size > 100 * 1024) {
        ui.showToast('File too large (max 100KB)', 'error');
        return;
    }

    try {
        const text = await new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = () => resolve(reader.result);
            reader.onerror = () => reject(new Error('Failed to read file'));
            reader.readAsText(file);
        });

        Images.addPendingFile({ filename: file.name, text });
        updateImagePreviewArea();
        ui.showToast(`File attached: ${file.name}`, 'success', 2000);
    } catch (e) {
        console.error('File read failed:', e);
        ui.showToast(e.message, 'error');
    }
}

// Handle image file selection/paste/drop
export async function handleImageUpload(file) {
    if (!file.type.startsWith('image/')) {
        ui.showToast('Only image files are supported', 'error');
        return;
    }
    
    // Check size (10MB max)
    if (file.size > 10 * 1024 * 1024) {
        ui.showToast('Image too large (max 10MB)', 'error');
        return;
    }
    
    try {
        const result = await api.uploadImage(file);
        
        // Create preview URL from file
        const previewUrl = URL.createObjectURL(file);
        
        Images.addPendingUploadImage({
            data: result.data,
            media_type: result.media_type,
            filename: result.filename,
            previewUrl: previewUrl
        });
        
        updateImagePreviewArea();
        ui.showToast('Image attached', 'success', 2000);
    } catch (e) {
        console.error('Image upload failed:', e);
        ui.showToast(e.message, 'error');
    }
}

// Setup paste and drag-drop handlers
export function setupImageHandlers() {
    const input = document.getElementById('prompt-input');
    const form = document.getElementById('chat-form');
    const uploadBtn = document.getElementById('image-upload-btn');
    const fileInput = document.getElementById('image-upload-input');
    
    // Upload button click -> trigger file input
    if (uploadBtn && fileInput) {
        uploadBtn.addEventListener('click', () => fileInput.click());
    }
    
    // File input handler
    if (fileInput) {
        fileInput.addEventListener('change', async (e) => {
            const files = e.target.files;
            for (const file of files) {
                if (file.type.startsWith('image/')) {
                    await handleImageUpload(file);
                } else {
                    await handleFileUpload(file);
                }
            }
            fileInput.value = '';
        });
    }
    
    // Paste handler
    document.addEventListener('paste', async (e) => {
        const items = e.clipboardData?.items;
        if (!items) return;
        
        for (const item of items) {
            if (item.type.startsWith('image/')) {
                e.preventDefault();
                const file = item.getAsFile();
                if (file) await handleImageUpload(file);
                return;
            }
        }
    });
    
    // Drag-drop handlers on form
    if (form) {
        form.addEventListener('dragover', (e) => {
            e.preventDefault();
            form.classList.add('drag-over');
        });
        
        form.addEventListener('dragleave', (e) => {
            e.preventDefault();
            form.classList.remove('drag-over');
        });
        
        form.addEventListener('drop', async (e) => {
            e.preventDefault();
            form.classList.remove('drag-over');

            const files = e.dataTransfer?.files;
            if (!files) return;

            for (const file of files) {
                if (file.type.startsWith('image/')) {
                    await handleImageUpload(file);
                } else {
                    // Try as text file
                    const dot = file.name.lastIndexOf('.');
                    const ext = dot !== -1 ? file.name.slice(dot).toLowerCase() : '';
                    if (Images.ALLOWED_FILE_EXTENSIONS.has(ext)) {
                        await handleFileUpload(file);
                    }
                }
            }
        });
    }
}

export async function handleStop() {
    const controller = getAbortController();

    if (controller) {
        setIsCancelling(true);
        console.log('Cancellation flag set');

        try {
            await api.cancelGeneration();
            console.log('Cancel request sent to backend');
        } catch (e) {
            console.error('Failed to send cancel request:', e);
        }

        controller.abort();
        audio.stop(true);
        // Only if the turn we stopped still holds the slot: during the await
        // the inbox may have started the NEXT turn, which is now live and
        // not ours to tear down (race scout, 2026-10-07).
        if (getAbortController() === controller) {
            ui.cancelStreaming();
            ui.hideStatus();
            setProc(false);
        } else {
            setIsCancelling(false);
        }
        ui.showToast('Generation stopped', 'success');
    } else {
        // Voice turn (wake / local conversation): no fetch to abort — the
        // turn is a server-side stream flagged via /api/cancel. Don't tear
        // down the live bubble here; the backend saves the partial and the
        // voice_turn_end event reconciles it with history + restores Send.
        try {
            await api.cancelGeneration();
            console.log('Cancel request sent for voice turn');
            ui.showToast('Generation stopped', 'success');
        } catch (e) {
            console.error('Failed to send cancel request:', e);
        }
    }
}

// A dictated turn, or a question card's answer. Since the inbox (2026-10-06)
// handleSend no longer blocks while a turn is live — the text queues with a
// pulsing bubble — so the old "already processing, ignoring" early-out here
// only dropped spoken turns (and would have eaten card clicks). Gone 2026-10-07.
export async function triggerSendWithText(text, { card = false } = {}) {
    if (card) {
        // a question card's picks: their own send, the composer's draft and
        // staged attachments untouched
        await handleSend({ refocus: false, text, attach: false });
        return true;
    }
    const { input } = getElements();
    input.value = text;
    input.dispatchEvent(new Event('input'));
    await handleSend({ refocus: false });
    return true;
}

let _userTypingTimer = null;
export function handleInput() {
    const { input } = getElements();
    input.parentElement.dataset.replicatedValue = input.value;
    // Debounced user_typing event for avatar (fire once, not per keystroke)
    if (!_userTypingTimer && input.value.trim()) {
        dispatch(Events.USER_TYPING);
    }
    clearTimeout(_userTypingTimer);
    _userTypingTimer = setTimeout(() => { _userTypingTimer = null; }, 2000);
}

export function handleKeyDown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        handleSend();
    }
}