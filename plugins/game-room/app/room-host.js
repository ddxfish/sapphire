// Game Room — the room host (F6, 2026-09-09). ONE lifecycle every room rides.
//
// Before this file each room (room.js for games, story-room.js for stories)
// carried its own copy of the chrome — activate + supersede, sidebar collapse,
// session picker, new/delete prompts, sections init, vault + reconnect watch,
// backdrop claim/restore, liveness tick — ~400 duplicated lines, and every
// fix landed in one copy at a time (S5, tmp/gameroom-foundation-20260908.md).
// Games ALSO re-implemented what the organ transplant gives for free: a
// composer, a talk log, a voice section. Now:
//
//   openRoom(root, spec, sessionName, opts)   — the ONE door
//   openGame(root, gameMeta, session, opts)   — the game adapter (poker, TD)
//   story-room.js                             — the story adapter, on this host
//
// A room = a mode-tagged chat (the chat IS the save) + the REAL chat rail and
// composer transplanted into the room frame (surface/organs.js), BOUND BY
// NAME to the session (F1): history + turns address the session whatever the
// server's active pointer does. Rooms still ACTIVATE their session (voice
// follows you to the table — Krem's ruling) but never DEPEND on it.
// Table talk is the chat. The sealed seat (gameroom_core) plays MOVES only.
// ONE TABLE, ONE TRANSCRIPT (Krem's plan A, 2026-09-09): whatever the player
// typed rides the move button they click — the server lands the move + words
// as their row and her quip as hers on the session chat, so the rail IS the
// table log. Send alone is a normal chat turn. The 💬 badge on the move
// buttons says "this goes with your move" while text is pending.
// THE CADENCE ORGAN (F3, same day): a game room ARMS core/cadence.py for
// its session from the spine (mode/range/frames/voice route) and keeps it
// alive every 30s while open; leaving disarms, a dead tab expires. Her
// unprompted turns arrive as VOICE_TURN events — the bound rail streams
// them, the subtitle carries them, 'browser' voice plays here. A canvas
// stage feeds the perception inbox with frames while `send_frames` is on;
// boards poke game moments (ctx.poke) for event mode.
// THE SUBTITLE (coffee sip, same day): one caption over the felt with her
// LATEST row — her move in small caps, her words — shown ONLY while the rail
// is folded, so exactly one surface ever carries her line. It fades, the
// next beat replaces it, a click unfolds the rail. Doom fullscreen later
// gets her lines the same way.
//
// Stage modes (spec.stage.mode):
//   side        board left, rail right (collapsible)         — poker, TD
//   stack       board top, rail below                         — watchers
//   rail        the rail IS the stage (no board)              — stories
//   fullscreen  board fills the pane, rail as a toggle        — emulators
//
// spec = {
//   id            tag stamped as game_id on sessions ('poker', 'story:<slug>')
//   kind          'game' | 'story'
//   title, icon, hash, loading, cssClass
//   stage         { mode, keepsFocus }    keepsFocus → the stage owns keyboard
//                                         focus (dom-guard data-keeps-focus)
//   board         async () => module      {renderBoard, renderActions,
//                                         renderSidebar} | {mount, unmount}
//   sayRidesMoves composer text rides ctx.post moves (games)
//   roomKeys      the spine's GAME layer as sidebar accordions (Cadence, …):
//                 fields pre-filled with what this game inherits from the room
//   cadence       arm her unprompted turns for this session (games)
//   voice         🔊 speak her fresh seat quips
//   settingsButton()                     ⚙ in the sidebar
//   clearSession  ✕ clear (games: wipe history + fresh table, keep the name)
//   ownSections   core sections the mode renders itself
//   bareSections  false → the mode places the bare sections in its own html
//   sidebarTop(ctx) → html               above the sections
//   sessions      { match(c), label(c), open(name) }   the picker
//   newSession    { label, defaultName() }
//   deleteSession { confirm(name), before(ctx) }
//   preOpen(ctx)  between activate and skeleton; return false to abort
//   onOpen(ctx), onClose(), onReconnect(ctx), onTurn(ctx)
//   tick          { every, fn(ctx) }      liveness-guarded poll
// }

import { renderSurface } from '/static/surface/surface.js';
import { accordionHtml, initAccordions } from '/static/shared/accordion.js';
import { coreSections } from '/static/surface/sections/core-sections.js';
import { claimOrgans, releaseOrgans } from '/static/surface/organs.js';
import { claimBackground, releaseBackground, applyTrimColor } from '/static/features/chat-settings.js';
import { playTextStreaming, playText, stop as ttsStop } from '/static/audio.js';
import * as coreApi from '/static/api.js';
import * as ui from '/static/ui.js';
import { getIsProc, getElements, refresh } from '/static/core/state.js';
import { updateScene, updateSendButtonLLM } from '/static/features/scene.js';
import { populateChatDropdown } from '/static/features/chat-manager.js';
import * as eventBus from '/static/core/event-bus.js';
import * as Images from '/static/ui-images.js';
import { handleSend } from '/static/handlers/send-handlers.js';

const OWNER = 'game-room';
const PLUGIN_API = '/api/plugin/game-room/';
const SIDEBAR_KEY = 'sapphire-game-sidebar';
const RAIL_KEY = 'sapphire-game-rail';
const VOICE_KEY = 'gameroom_voice';
const ROOM_PLACEHOLDER = 'Say something — Send talks; a move button plays it with your move';
const CAPTION_MS = 10000;
const KEEPALIVE_MS = 30000;

const bootV = () => document.querySelector('meta[name="boot-version"]')?.content || '';

export function esc(s) {
    return String(s == null ? '' : s)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

// Dealer lines carry raw card codes ("Flop: Ks 7d 2c") — prettify them.
const SUIT = { s: '♠', h: '♥', d: '♦', c: '♣' };
const RED = { h: true, d: true };
export function prettyCodes(escaped) {
    return escaped.replace(/\b([2-9TJQKA])([shdc])\b/g, (_, r, s) =>
        `<span class="pk-code${RED[s] ? ' pk-red' : ''}">${r === 'T' ? '10' : r}${SUIT[s]}</span>`);
}

function csrf() {
    const m = document.querySelector('meta[name="csrf-token"]');
    return (m && m.content) || '';
}

// ------------------------------------------------------------------ sessions
// (by name, always — the room knows which chat it has open; the server must
// never guess from "whatever is active")

export function sanitizeName(name) {
    // Mirror core create_chat: keep alnum/space/dash/underscore, spaces → _
    return String(name || '').split('').filter(c => /[a-zA-Z0-9 _-]/.test(c)).join('')
        .trim().replace(/\s+/g, '_').toLowerCase();
}

export async function listSessions() {
    // Server-side kind filter + slim settings (F1): the room never downloads
    // every chat's full settings blob to find its own.
    const data = await coreApi.fetchChatList('game', { slim: true });
    return (data.chats || []).filter(c =>
        (c.kind === 'game' || (c.mode || c.settings?.mode) === 'game') && !c.archived);
}

// Birth stamp (F1): mode + game_id ride the create — a session never exists
// untagged for a beat (the old create-then-PUT left an untagged chat when
// the second call failed). Birth defaults (the spine, 2026-09-09): the
// room's / game's `new_session_toolset` rides the same write.
async function birthSettings(tagId) {
    const settings = { mode: 'game', game_id: tagId };
    try {
        const gid = String(tagId || '').startsWith('story:') ? '' : tagId;
        const r = await fetch(PLUGIN_API + 'room/effective' + (gid ? `?game=${encodeURIComponent(gid)}` : ''),
            { headers: { 'X-CSRF-Token': csrf() } });
        const eff = r.ok ? (await r.json()).effective || {} : {};
        if (eff.new_session_toolset) settings.toolset = eff.new_session_toolset;
    } catch (e) { /* birth without defaults is still a birth */ }
    return settings;
}

export async function createSession(tagId, rawName) {
    const name = sanitizeName(rawName);
    if (!name) throw new Error('Session needs a name');
    await coreApi.createChat(name, await birthSettings(tagId));
    return name;
}

// Newest session tagged `tagId`, or a fresh auto-named one (base, base_2...).
// Stories tag as "story:<slug>" — colon can't collide with game ids.
export async function ensureSession(tagId, baseName) {
    const mine = (await listSessions()).filter(c => (c.settings?.game_id) === tagId);
    if (mine.length) return mine[0].name;   // list is updated_at DESC
    const birth = await birthSettings(tagId);
    for (let n = 1; n <= 20; n++) {
        const nm = sanitizeName(n === 1 ? baseName : `${baseName}_${n}`);
        try {
            await coreApi.createChat(nm, birth);
            return nm;
        } catch (e) {
            if (!String(e.message).includes('already exists')) throw e;
        }
    }
    throw new Error('Could not find a free session name');
}

// Play Private (v1.3.1, Krem's ruling 2026-08-15): a NEW session born
// private — never flips an existing one (that stays Chat Manager's job).
// The tag rides the create; the private flip is its own call (the birth
// route refuses private_chat — the vault registers the chat on the flip).
// A failed flip deletes the just-made chat — no public stragglers.
export async function createPrivateSession(tagId, baseName) {
    const birth = await birthSettings(tagId);
    for (let n = 1; n <= 20; n++) {
        const nm = sanitizeName(n === 1 ? `${baseName}_private` : `${baseName}_private_${n}`);
        try {
            await coreApi.createChat(nm, birth);
        } catch (e) {
            if (String(e.message).includes('already exists')) continue;
            throw e;
        }
        try {
            await coreApi.updateChatSettings(nm, { private_chat: true });
            return nm;
        } catch (e) {
            try { await coreApi.deleteChat(nm); } catch { /* best effort */ }
            throw new Error(`Couldn't make the session private: ${e.message}`);
        }
    }
    throw new Error('Could not find a free session name');
}

// Stamp the room's model override onto a session (the server no-ops when
// the picker says "persona's model"). Krem 2026-08-20. Resolves to the
// applied provider key ('' = nothing stamped). The send-button LLM label
// repaints from the stamp's RESPONSE — and since 2026-09-09 the room AWAITS
// this and re-reads the session's settings before painting the sidebar:
// the Brain dropdown used to paint from the activate snapshot, which
// predates the stamp, and said "Claude" while LM Studio was audibly
// running (Krem's poker click-list).
export async function applyRoomModel(session) {
    try {
        const r = await fetch(PLUGIN_API + 'room/apply-model', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf() },
            body: JSON.stringify({ session }),
        });
        const d = await r.json();
        if (d && d.success && d.applied) { updateSendButtonLLM(d.applied, ''); return d.applied; }
    } catch (e) { /* model override is best-effort */ }
    return '';
}

// Activate a session chat + repaint core (rail/scene/trim/picker). Attention
// only since F1 — the rail is bound by name regardless.
export async function activateSession(name) {
    const act = await coreApi.activateChat(name);
    const applied = await applyRoomModel(name);
    const settings = act?.settings || {};
    if (applied) { settings.llm_primary = applied; settings.llm_model = ''; }
    syncCore(name, settings);
    return act;
}

function syncCore(sessionName, settings) {
    Promise.allSettled([refresh(false), updateScene(), populateChatDropdown()]).catch(() => {});
    try {
        updateSendButtonLLM(settings.llm_primary || 'auto', settings.llm_model || '');
        applyTrimColor(settings.trim_color || '');
        const sel = document.getElementById('chat-select');
        if (sel) {
            sel.value = sessionName;
            sel.dispatchEvent(new CustomEvent('chat-activated', { detail: { chat: sessionName, settings } }));
        }
    } catch (e) { console.warn('[GameRoom] core sync failed', e); }
}

async function fetchSettings(name) {
    const r = await fetch(`/api/chats/${encodeURIComponent(name)}/settings`,
        { headers: { 'X-CSRF-Token': csrf() } });
    if (!r.ok) return null;
    const body = await r.json().catch(() => null);
    return (body && body.settings) || null;
}

// ------------------------------------------------------------------ the room
// R = the live room record; its identity is the supersede token every async
// landing checks (`R !== me` → a ghost from a room that's gone).

let R = null;
export const current = () => R;

export async function openRoom(root, spec, sessionName, opts = {}) {
    if (getIsProc()) {
        ui.showToast('Finish the current turn first, then enter the room.', 'warning');
        return null;
    }
    close();
    const me = {
        root, spec, session: sessionName, back: opts.back || null, opts,
        settings: {}, mod: null, state: null, seat: null,
        busy: false, busyLabel: '', timer: null,
        lastSpokenSeq: null, voiceOn: localStorage.getItem(VOICE_KEY) === '1',
        ttsEnabled: false, speakChain: Promise.resolve(),
        bdPrev: null, bdUrl: null, ctx: null,
    };
    R = me;
    const ctx = makeCtx(me);
    me.ctx = ctx;
    root.innerHTML = `<div class="pk-loading">${esc(spec.loading || 'Opening the room...')}</div>`;
    if (spec.hash) history.replaceState(null, '', spec.hash);

    // activateChat REJECTS while a turn is streaming elsewhere (400). An
    // unhandled rejection left a dead room with no back button (finding
    // 4.9); assigning settings before the supersede guard wrote the wrong
    // session's settings into the right chat (4.10). Both guards, both paths.
    let act;
    try {
        act = await coreApi.activateChat(sessionName);
    } catch (e) {
        if (R !== me) return null;
        ui.showToast(`Could not open "${sessionName}": ${e.message}`, 'error');
        bounce(me);
        return null;
    }
    if (R !== me) return null;
    me.settings = act?.settings || {};
    // The stamp lands BEFORE the sidebar paints; the settings the sections
    // read are re-fetched from the server (the stamp + anything else the
    // activate did), never the pre-stamp snapshot.
    if (await applyRoomModel(sessionName)) {
        const fresh = await fetchSettings(sessionName).catch(() => null);
        if (R !== me) return null;
        if (fresh) me.settings = fresh;
    }
    bindWatch();

    if (spec.preOpen) {
        let ok = true;
        try { ok = await spec.preOpen(ctx); } catch (e) { ui.showToast(e.message, 'error'); }
        if (R !== me) return null;
        if (ok === false) { bounce(me); return null; }
    }

    let mod = null, status = null;
    if (spec.board) {
        try {
            [mod, status] = await Promise.all([
                spec.board(),
                fetch('/api/status', { headers: { 'X-CSRF-Token': csrf() } })
                    .then(r => r.ok ? r.json() : null).catch(() => null),
            ]);
        } catch (e) {
            if (R !== me) return null;
            ui.showToast(`Board failed to load: ${e.message}`, 'error');
            bounce(me);
            return null;
        }
        if (R !== me) return null;
        me.mod = mod?.game || mod;
        me.ttsEnabled = !!(status && status.tts_enabled);
    }

    skeleton(me);

    // The transplant: real rail + composer move into the room, bound by
    // name (F1). Refused while a turn is live — toast + back, never a dead
    // room. Layout is fixed BEFORE the claim so #chatbg lands in a slot with
    // a real flex basis.
    const ok = claimOrgans({
        railSlot: root.querySelector('#gr-rail-slot'),
        composerSlot: root.querySelector('#gr-composer-slot'),
    }, OWNER, sessionName);
    if (!ok) {
        ui.showToast('Could not borrow the chat rail — finish the current turn first.', 'warning');
        bounce(me);
        return null;
    }
    try { ui.forceScrollToBottom(); } catch (e) { /* cosmetic */ }
    if (spec.sayRidesMoves) bindComposer(me);
    // Repaint core with the session active (the rail paints wherever it lives)
    syncCore(sessionName, me.settings);

    initSections(me);
    if (spec.roomKeys) initRoomKeys(me);
    if (spec.cadence) startCadence(me);
    if (me.mod) {
        await loadState(me);
        if (R !== me) return null;
        baseline(me);
        drawAll(me);
    }
    if (spec.onOpen) {
        try { await spec.onOpen(ctx); } catch (e) { console.warn('[GameRoom] onOpen failed', e); }
        if (R !== me) return null;
    }
    if (spec.tick) startTick(me);
    paintAttention(me);
    return ctx;
}

// Leave the room: mode teardown, board unmount, voice off, backdrop back,
// organs home (BEFORE any other view shows), timers dead. Idempotent.
export function close() {
    const me = R;
    if (!me) return;
    R = null;
    if (me.timer) { clearInterval(me.timer); me.timer = null; }
    if (me.capTimer) { clearTimeout(me.capTimer); me.capTimer = null; }
    stopCadence(me);
    try { me.spec.onClose?.(); } catch (e) { console.warn('[GameRoom] onClose failed', e); }
    if (me.mod?.unmount) { try { me.mod.unmount(); } catch (e) { /* game's problem */ } }
    // force: plain stop() no-ops while a stream is mid-flight, so leaving a
    // room let her keep talking into the next one (finding 4.11).
    try { ttsStop(true); } catch (e) { /* nothing playing */ }
    unbindComposer(me);
    restoreBackdrop(me);
    releaseOrgans(OWNER);
    document.getElementById('gr-modal')?.remove();
}

function bounce(me) {
    const back = me.back;
    if (R === me) close();
    if (back) back();
}

// ------------------------------------------------------------------ ctx

function makeCtx(me) {
    const spec = me.spec;
    // Every plugin call names the session at issue time — the same instant
    // the caller decided to call (finding 1.1).
    const api = async (path, method, body) => {
        let url = PLUGIN_API + path;
        let payload = body;
        if (me.session) {
            if (method && method !== 'GET') payload = { ...(body || {}), session: me.session };
            else if (!url.includes('session=')) {
                url += (url.includes('?') ? '&' : '?') + 'session=' + encodeURIComponent(me.session);
            }
        }
        const ctl = new AbortController();
        const timer = setTimeout(() => ctl.abort(), 180000);
        try {
            const res = await fetch(url, {
                method: method || 'GET',
                headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf() },
                body: payload ? JSON.stringify(payload) : undefined,
                signal: ctl.signal,
            });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(data.error || data.detail || ('HTTP ' + res.status));
            return data;
        } finally {
            clearTimeout(timer);
        }
    };
    const gamePath = (p) => 'play/' + spec.id + '/' + p;
    return {
        api, esc, prettyCodes,
        // A MOVE on the sealed seat: busy gate, supersede guard, state swap,
        // quip voice, redraw. The composer's text rides along (plan A).
        post: (path, body, label) => post(me, gamePath(path), body, label),
        state: () => me.state,
        cfg: () => me.seat || {},
        session: () => me.session,
        // Getter, not a snapshot (finding 4.16): a resync replaces the
        // object; sections read through the live reference.
        get settings() { return me.settings; },
        setSettings: (s) => { if (s && typeof s === 'object') me.settings = s; },
        save: async (patch) => {
            try {
                await coreApi.updateChatSettings(me.session, patch);
                Object.assign(me.settings, patch);
                if (R === me && me.mod) { await loadState(me); drawResolved(me); }
            } catch (e) { showError(e.message); }
        },
        busy: () => me.busy,
        root: () => me.root,
        spec,
        showError,
        // One-line status in the stage bar ("Hand #10 · preflop") — games
        // write here instead of burning a row of their own board.
        stageInfo: (html) => {
            const el = me.root?.querySelector('#gr-stage-info');
            if (el) el.innerHTML = html || '';
        },
        composerText: () => (document.getElementById('prompt-input')?.value || '').trim(),
        draft,
        sendTurn: (o) => sendTurn(me, o),
        refreshState: async () => { if (me.mod) { await loadState(me); if (R === me) drawAll(me); } },
        backdrop: (url) => setBackdrop(me, url),
        tickNow: () => { if (me.tickFn) me.tickFn(); },
        live: () => R === me,
        // perception: what she sees next (latest wins); frames = [{data, media_type}]
        deposit: (o) => deposit(me, o),
        // a game moment — event mode fires on it (no sooner than the min gap).
        // The frame of THAT moment rides along when the game shows her the screen.
        poke: (note, opts) => pokeMoment(me, note, opts),
        cadence: () => me.cad,
    };
}

// Pre-fill the REAL composer — the player edits, flavors, sends. Never
// auto-sends: the voice stays theirs.
function draft(text) {
    const input = document.getElementById('prompt-input');
    if (!input) return;
    input.value = text;
    input.dispatchEvent(new Event('input', { bubbles: true }));   // autosize
    input.focus();
    try { input.setSelectionRange(input.value.length, input.value.length); } catch (e) { /* ok */ }
}

// A programmatic turn on the bound session through the REAL send path:
// streaming, Stop, TTS, images — everything a typed turn gets. The player's
// unsent draft is stashed and restored (the clobber the old
// triggerSendWithText lane had, S4 #2). images: [{data, media_type}].
async function sendTurn(me, { text = '', images = [], refocus = false } = {}) {
    if (R !== me || getIsProc()) return false;
    const { input } = getElements();
    if (!input) return false;
    const stash = input.value;
    input.value = text;
    for (const im of images || []) Images.addPendingUploadImage({ data: im.data, media_type: im.media_type, filename: im.filename || 'frame' });
    input.dispatchEvent(new Event('input'));
    try {
        await handleSend({ refocus });
    } finally {
        if (stash && !input.value) { input.value = stash; input.dispatchEvent(new Event('input')); }
    }
    return true;
}

function showError(msg) {
    try { ui.showToast(msg, 'error'); }
    catch (e) { console.error('[GameRoom]', msg); }
}

// ------------------------------------------------------------------ layout

function skeleton(me) {
    const { root, spec } = me;
    const mode = spec.stage?.mode || 'side';
    const railMode = mode === 'rail';
    const title = `${esc(spec.icon || '🎲')} ${me.settings.private_chat ? '\u{1F5DD} ' : ''}${esc(spec.title || spec.id)}`;
    const keeps = spec.stage?.keepsFocus ? ' data-keeps-focus="1" tabindex="-1"' : '';
    renderSurface(root, {
        id: spec.kind === 'story' ? 'story' : 'game',
        cssClass: spec.cssClass || (spec.kind === 'story' ? 'surface-story' : 'surface-game'),
        mainPane: `
            <div class="gr-stage-bar">
                <button type="button" id="gr-stage-back" class="sb-icon-btn" title="Back to Game Room">&#x2190;</button>
                <span class="gr-stage-title">${title}</span>
                <span class="gr-stage-info" id="gr-stage-info"></span>
                <button type="button" id="gr-attn" class="gr-attn" style="display:none" title="Her attention (voice, wake word) moved to another chat — bring it back to this table">&#x1F3A4; retake</button>
                ${spec.cadence ? '<span class="gr-cad" id="gr-cad" style="display:none"><span id="gr-cad-text"></span><button type="button" id="gr-cad-pause" class="sb-icon-btn" title="Pause her unprompted turns in this session">&#x23F8;</button></span>' : ''}
                ${railMode ? '' : `<button type="button" id="gr-rail-toggle" class="sb-icon-btn" title="Show / hide the chat">&#x1F4AC;</button>`}
            </div>
            <div class="gr-room gr-mode-${esc(mode)}" id="gr-room">
                ${railMode ? '' : `
                <div class="gr-stagecol">
                    <div class="gr-stage" id="gr-stage"${keeps}></div>
                    <div class="gr-caption" id="gr-caption" title="Her last line — click to open the chat"></div>
                </div>`}
                <div class="gr-rail-slot" id="gr-rail-slot"></div>
            </div>`,
        formArea: `
            ${spec.board ? '<div class="gr-actions" id="gr-actions"></div>' : ''}
            <div class="gr-composer-slot" id="gr-composer-slot"></div>`,
        sidebarHeader: `
            <div class="sb-chat-header">
                <button type="button" id="gr-back" class="sb-icon-btn" title="Game library">🎲</button>
                <div class="sb-chat-picker" id="gr-session-picker">
                    <button class="sb-chat-picker-btn" id="gr-session-btn">
                        <span id="gr-session-name">${me.settings.private_chat ? '\u{1F5DD} ' : ''}${esc(me.session)}</span>
                        <span class="sb-chat-arrow">&#x25BE;</span>
                    </button>
                    <div class="sb-chat-picker-dropdown" id="gr-session-dropdown"></div>
                </div>
                <button type="button" id="gr-sidebar-toggle" class="sb-icon-btn sb-collapse-btn" title="Hide sidebar">&#x25B6;</button>
            </div>
            <div class="sb-chat-actions">
                <button type="button" id="gr-new-session" class="sb-icon-btn" title="${esc(spec.newSession?.label || 'New session')}">+</button>
                ${spec.clearSession ? '<button type="button" id="gr-clear-session" class="sb-icon-btn" title="Clear session — fresh table and history, keeps the name">&#x2715;</button>' : ''}
                ${spec.voice ? `<button type="button" id="gr-speak" class="sb-icon-btn" title="Speak her lines at the table">${me.voiceOn ? '🔊' : '🔇'}</button>` : ''}
                <button type="button" id="gr-del-session" class="sb-icon-btn sb-icon-danger" title="Delete this session (chat + game state)">&#x1F5D1;</button>
            </div>
            ${spec.settingsButton ? '<button type="button" id="gr-game-settings" class="sb-btn-full gr-settings-btn">&#x2699;&#xFE0E; Game Settings</button>' : ''}
            <div class="gr-game-title">${esc(spec.icon || '')} ${esc(spec.title || spec.id)}</div>`,
        sidebarBody: `
            ${spec.sidebarTop ? spec.sidebarTop(me.ctx) : ''}
            ${spec.bareSections === false ? '' : `
            <div class="sidebar-section">
                ${coreSections.filter(s => s.bare && !(spec.ownSections || []).includes(s.key)).map(s =>
                    `<div class="gs-content" data-sec="${s.key}"></div>`).join('')}
            </div>`}
            ${spec.roomKeys ? '<div id="gr-room-keys"></div>' : ''}
            ${coreSections.filter(s => !s.bare && !(spec.ownSections || []).includes(s.key)).map(s => accordionHtml({
                id: 'surface:' + s.key, title: s.title, icon: s.icon, open: !!s.open,
                content: `<div class="gs-content" data-sec="${s.key}"></div>`,
            })).join('')}
            <div id="gr-game-sidebar"></div>`,
    });

    const $ = (sel) => root.querySelector(sel);

    // Sidebar collapse — own preference, chat's CSS
    const sidebar = $('.chat-sidebar');
    if (localStorage.getItem(SIDEBAR_KEY) === 'collapsed') sidebar.classList.add('collapsed');
    const toggleSb = () => {
        const collapsed = sidebar.classList.toggle('collapsed');
        localStorage.setItem(SIDEBAR_KEY, collapsed ? 'collapsed' : 'expanded');
    };
    $('#gr-sidebar-toggle').onclick = toggleSb;
    $('#chat-sidebar-expand').onclick = toggleSb;

    // Rail collapse (side/stack/fullscreen): the chat folds away, the board
    // takes the pane. Per-mode memory — a fullscreen game folding the rail
    // says nothing about the poker table.
    const railKey = `${RAIL_KEY}-${mode}`;
    const roomEl = $('#gr-room');
    if (!railMode) {
        const def = mode === 'fullscreen' ? 'collapsed' : 'expanded';
        if ((localStorage.getItem(railKey) || def) === 'collapsed') roomEl.classList.add('rail-collapsed');
        const toggleRail = (open) => {
            const c = open === undefined ? roomEl.classList.toggle('rail-collapsed')
                                         : !roomEl.classList.toggle('rail-collapsed', !open);
            localStorage.setItem(railKey, c ? 'collapsed' : 'expanded');
            if (!c) { hideCaption(me); try { ui.forceScrollToBottom(); } catch (e) { /* cosmetic */ } }
        };
        $('#gr-rail-toggle').onclick = () => toggleRail();
        const cap = $('#gr-caption');
        if (cap) {
            cap.onclick = () => toggleRail(true);
            cap.onmouseenter = () => { if (me.capTimer) { clearTimeout(me.capTimer); me.capTimer = null; } };
            cap.onmouseleave = () => { if (cap.classList.contains('on')) me.capTimer = setTimeout(() => hideCaption(me), 4000); };
        }
    }

    const goBack = () => bounce(me);
    $('#gr-stage-back').onclick = goBack;
    $('#gr-back').onclick = goBack;
    $('#gr-attn').onclick = () => activateSession(me.session).then(() => paintAttention(me)).catch(e => showError(e.message));
    $('#gr-new-session').onclick = () => newSessionPrompt(me);
    $('#gr-del-session').onclick = () => deleteSessionPrompt(me);
    const clr = $('#gr-clear-session');
    if (clr) clr.onclick = () => clearSessionPrompt(me);
    const gear = $('#gr-game-settings');
    if (gear) gear.onclick = () => spec.settingsButton(me.ctx);
    const speak = $('#gr-speak');
    if (speak) speak.onclick = () => {
        me.voiceOn = !me.voiceOn;
        localStorage.setItem(VOICE_KEY, me.voiceOn ? '1' : '0');
        speak.textContent = me.voiceOn ? '🔊' : '🔇';
        if (me.voiceOn && me.ttsEnabled) speakText(me, 'Voice on. Let’s play.');
        else { try { ttsStop(); } catch (e) { /* idle */ } }
    };

    $('#gr-session-btn').onclick = async (e) => {
        e.stopPropagation();
        const picker = $('#gr-session-picker');
        if (picker.classList.toggle('open')) await fillSessionDropdown(me);
    };
    bindDocClose();
    initAccordions($('.chat-sidebar-inner'), spec.kind === 'story' ? 'story-sidebar' : 'game-sidebar');
}

let _docCloseBound = false;
function bindDocClose() {
    if (_docCloseBound) return;             // once per page life, not per room
    _docCloseBound = true;
    document.addEventListener('click', () => {
        R?.root?.querySelector('#gr-session-picker')?.classList.remove('open');
    });
}

async function fillSessionDropdown(me) {
    const dd = me.root?.querySelector('#gr-session-dropdown');
    if (!dd) return;
    let sessions = [];
    try { sessions = await listSessions(); } catch (e) { return; }
    if (R !== me) return;
    const ss = me.spec.sessions || {};
    const rows = sessions.filter(c => !ss.match || ss.match(c));
    dd.innerHTML = rows.map(c => `
        <button class="chat-picker-item${c.name === me.session ? ' active' : ''}" data-session="${esc(c.name)}">
            <span class="chat-picker-item-check">${c.name === me.session ? '✓' : ''}</span>
            <span class="chat-picker-item-name">${ss.label ? ss.label(c) : esc(c.display_name || c.name)}</span>
        </button>`).join('') || '<div class="gr-dd-empty">No sessions yet</div>';
    dd.querySelectorAll('[data-session]').forEach(btn => {
        btn.onclick = () => {
            const name = btn.dataset.session;
            const c = rows.find(x => x.name === name);
            if (ss.open) ss.open(name, c);
        };
    });
}

// Fill the per-session settings sections. Saves target the session BY NAME
// (works active or not), so a save that lands after a session switch still
// writes to the right chat. Any `.gs-content[data-sec]` in the root is
// filled — modes may place sections inside their own accordions.
async function initSections(me) {
    const own = me.spec.ownSections || [];
    for (const s of coreSections) {
        if (own.includes(s.key)) continue;
        if (R !== me) return;                                     // superseded
        const el = me.root.querySelector(`.gs-content[data-sec="${s.key}"]`);
        if (!el) continue;
        el.innerHTML = s.html();
        try { await s.init(el, me.ctx); } catch (e) { console.warn('[GameRoom] section failed:', s.key, e); }
    }
    if (R === me) drawResolved(me);
}

// ------------------------------------------------------------------ the game's layer (the spine)
// The room keys THIS GAME may override — cadence range, frames, voice route,
// end summary, costume line, birth toolset — as sidebar accordions, fields
// pre-filled with what the game inherits from the room; an edited value is
// the game's own (dot + ↺), persisting across its sessions. Same kit as the
// library's room defaults (settings-modal.js), so both sidebars read alike.
// The SESSION layer keeps no fields here — F3's pause button writes it.

async function initRoomKeys(me) {
    const box = me.root?.querySelector('#gr-room-keys');
    if (!box) return;
    let mod, data;
    try {
        mod = await import(`./settings-modal.js?v=${bootV()}`);
        data = await me.ctx.api(`play/${me.spec.id}/settings`);
    } catch (e) { return; }
    if (R !== me || !box.isConnected) return;
    const room = data.room || {};
    const schema = room.schema || [];
    if (!schema.length) return;
    // Voice keys join the core TTS accordion (one Voice, not two — Krem
    // 2026-09-09); every other tab is its own accordion.
    const voiceHome = me.root.querySelector('.sidebar-accordion[data-acc="surface:voice"] .sidebar-accordion-content');
    const own = [], merged = [];
    for (const f of schema) ((f.tab === 'Voice' && voiceHome) ? merged : own).push(f);
    box.innerHTML = mod.layerAccordionsHtml(own, room.inherited || {}, room.overrides || {}, 'game', mod.LAYER_ICONS, room.notes || {});
    let voiceBox = null;
    if (merged.length) {
        voiceBox = document.createElement('div');
        voiceBox.className = 'gr-voice-route';
        voiceBox.innerHTML = mod.layerRowsHtml(merged, room.inherited || {}, room.overrides || {});
        voiceHome.appendChild(voiceBox);
    }
    initAccordions(me.root.querySelector('.chat-sidebar-inner'), 'game-sidebar');
    const save = async (key, value) => {
        try {
            await me.ctx.api(`play/${me.spec.id}/settings`, 'POST', { room_overrides: { [key]: value } });
        } catch (e) { showError(e.message); }
    };
    mod.wireLayer(box, own, room.inherited || {}, save);
    if (voiceBox) mod.wireLayer(voiceBox, merged, room.inherited || {}, save);
}

// ------------------------------------------------------------------ session CRUD

async function newSessionPrompt(me) {
    const ns = me.spec.newSession || {};
    const def = ns.defaultName ? ns.defaultName() : `${me.spec.id} ${new Date().toISOString().slice(0, 10)}`;
    const name = prompt(`Name for the new ${ns.noun || 'session'}:`, def);
    if (!name || !name.trim()) return;
    try {
        const session = await createSession(me.spec.id, name);
        if (me.spec.sessions?.open) me.spec.sessions.open(session, null);
    } catch (e) {
        showError(e.message.includes('already exists') ? 'A chat with that name already exists.' : e.message);
    }
}

async function clearSessionPrompt(me) {
    if (!confirm(`Clear "${me.session}"?\n\nWipes the chat history and resets the ${me.spec.title || me.spec.id} table — fresh start, same name.`)) return;
    const session = me.session;
    try {
        // BY NAME, never "the active chat" — the dialog names a session, so
        // that is the only thing allowed to be wiped (finding 2.3).
        const res = await coreApi.bulkClearChats([session]);
        if (!res?.results?.[session]?.ok)
            throw new Error(res?.results?.[session]?.message || 'clear failed');
        if (R !== me) return;
        if (me.mod) {
            const st = await me.ctx.api(`play/${me.spec.id}/new-session`, 'POST', {});
            if (R !== me) return;
            me.seat = st.seat || me.seat;
            delete st.seat;
            me.state = st;
            baseline(me);
        }
        refresh(false).catch(() => {});     // repaint the rail (empty now)
        if (me.mod) drawAll(me);
        ui.showToast('Session cleared — fresh table', 'success', 2500);
    } catch (e) {
        showError(e.message);
    }
}

async function deleteSessionPrompt(me) {
    const ds = me.spec.deleteSession || {};
    const text = ds.confirm ? ds.confirm(me.session)
        : `Delete session "${me.session}"?\n\nRemoves its chat history AND its ${me.spec.title || me.spec.id} game state.`;
    if (!confirm(text)) return;
    const session = me.session;
    try {
        if (ds.before) { try { await ds.before(me.ctx); } catch (e) { /* cleanup is best-effort */ } }
        // Deleting the ACTIVE chat: the server re-points and publishes
        // CHAT_SWITCHED (F1) — no pre-activate dance needed here.
        await coreApi.deleteChat(session);
        bounce(me);
    } catch (e) {
        showError(e.message);
    }
}

// ------------------------------------------------------------------ moves (sealed seat)

async function loadState(me) {
    // Supersede guard: the response lands only if the room still shows the
    // session it was fetched for (finding 4.5).
    try {
        const data = await me.ctx.api(`play/${me.spec.id}/state`);
        if (R !== me) return;
        me.seat = data.seat || null;
        delete data.seat;
        me.state = data;
    } catch (e) {
        if (R !== me) return;
        showError(e.message);
    }
}

async function post(me, path, body, label) {
    if (me.busy || R !== me) return;
    // One turn per chat: the pair this move appends waits on her live
    // stream — refuse instead of stacking a move behind her reply.
    if (getIsProc()) { ui.showToast('Let her finish this turn first.', 'warning', 2500); return; }
    body = { ...(body || {}) };
    // Plan A: the composer's text rides the move as the player's row. Taken
    // NOW (a double-click can't resend it) and put back if the move fails.
    let taken = '';
    const input = me.spec.sayRidesMoves ? document.getElementById('prompt-input') : null;
    if (input && input.value.trim()) {
        taken = input.value;
        body.say = taken.trim();
        input.value = '';
        input.dispatchEvent(new Event('input', { bubbles: true }));
    }
    me.busy = true; me.busyLabel = label || 'thinking';
    drawBoard(me); drawActions(me);
    try {
        const next = await me.ctx.api(path, 'POST', body);
        if (R !== me) return;
        me.state = next;
        me.seat = me.state.seat || me.seat;
        delete me.state.seat;
        const last = me.state.last;
        delete me.state.last;
        speakNew(me);
        if (last && last.assistant) showCaption(me, last.assistant);
        refresh(false).catch(() => {});   // the pair landed on the rail (MESSAGE_ADDED also fires)
    } catch (e) {
        if (R !== me) return;
        if (taken && input && !input.value) {
            input.value = taken;
            input.dispatchEvent(new Event('input', { bubbles: true }));
        }
        showError(e.message);
    } finally {
        if (R === me) me.busy = false;
    }
    if (R === me) drawAll(me);
}

// The composer belongs to the table while a game room is open: a
// placeholder that says the rule once, and the 💬 badge on the move buttons
// whenever text is pending (the affordance for "this rides your move").
function bindComposer(me) {
    const input = document.getElementById('prompt-input');
    if (!input) return;
    me.placeholder = input.placeholder;
    input.placeholder = ROOM_PLACEHOLDER;
    me.onInput = () => {
        const bar = me.root?.querySelector('#gr-actions');
        if (bar) bar.classList.toggle('has-say', !!input.value.trim());
    };
    input.addEventListener('input', me.onInput);
    me.onInput();
}

function unbindComposer(me) {
    const input = document.getElementById('prompt-input');
    if (input && me.onInput) input.removeEventListener('input', me.onInput);
    if (input && me.placeholder != null) input.placeholder = me.placeholder;
    me.onInput = null; me.placeholder = null;
}

// ------------------------------------------------------------------ quip voice
// Her per-move lines come from the sealed seat (not the chat pipeline, so
// no streaming TTS even though they land on the rail as her rows): speak
// the NEW ones, watermarked by talk seq. Mirrored chat turns (via='chat')
// already spoke through the pipeline.

function maxSeq(talk) {
    return (talk || []).reduce((m, t) => (t.seq != null && t.seq > m ? t.seq : m), 0);
}

function baseline(me) {
    if (me.state) me.lastSpokenSeq = maxSeq(me.state.talk);
}

function speakNew(me) {
    if (!me.state) return;
    if (me.lastSpokenSeq === null) { baseline(me); return; }
    const mx = maxSeq(me.state.talk);
    if (mx < me.lastSpokenSeq) me.lastSpokenSeq = 0;   // seq went backwards = new stream
    const fresh = (me.state.talk || []).filter(t =>
        t.who === 'ai' && !t.via && t.seq != null && t.seq > me.lastSpokenSeq);
    me.lastSpokenSeq = Math.max(me.lastSpokenSeq, mx);
    if (!me.spec.voice || !me.voiceOn || !me.ttsEnabled || !fresh.length) return;
    speakText(me, fresh.map(t => t.text).join(' ... '));
}

function speakText(me, line) {
    // Token the queued line to the room that produced it: a chain entry
    // that starts after you've left used to play A's line in B's room, in
    // B's voice (finding 4.11).
    me.speakChain = me.speakChain.then(async () => {
        if (R !== me) return;
        try {
            const voice = (me.seat && me.seat.voice) || null;
            const ok = await playTextStreaming(line, voice);
            if (ok) return;
            if (voice) {
                const res = await fetch('/api/tts/preview', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf() },
                    body: JSON.stringify({ text: line, voice }),
                });
                if (!res.ok) throw new Error('preview HTTP ' + res.status);
                const blob = await res.blob();
                await new Promise((resolve) => {
                    const a = new Audio(URL.createObjectURL(blob));
                    a.onended = a.onerror = () => { URL.revokeObjectURL(a.src); resolve(); };
                    a.play().catch(() => resolve());
                });
            } else {
                await playText(line);
            }
        } catch (e) {
            console.warn('[GameRoom] voice failed', e);
        }
    });
}

// ------------------------------------------------------------------ draw

function drawAll(me) { drawBoard(me); drawActions(me); drawResolved(me); drawGameSidebar(me); }

function sessName(me, who) {
    const s = me.state && me.state.session;
    if (!s) return who === 'ai' ? 'Sapphire' : 'You';
    return who === 'ai' ? s.ai_name : s.player_name;
}

function drawBoard(me) {
    const el = me.root?.querySelector('#gr-stage');
    if (!el || !me.mod) return;
    if (me.mod.renderBoard) { me.mod.renderBoard(el, me.state, me.ctx); return; }
    if (me.mod.mount && !el.dataset.mounted) {   // free-mount games own the stage
        el.dataset.mounted = '1';
        me.mod.mount(el, me.ctx);
    }
}

function drawActions(me) {
    const el = me.root?.querySelector('#gr-actions');
    if (!el || !me.mod) return;
    if (!me.mod.renderActions) { el.innerHTML = ''; return; }
    if (me.busy) { el.innerHTML = `<div class="pk-wait">${esc(sessName(me, 'ai'))} is ${esc(me.busyLabel)}&hellip;</div>`; return; }
    me.mod.renderActions(el, me.state, me.ctx);
    if (me.onInput) me.onInput();
}

function drawResolved(me) {
    const el = me.root?.querySelector('.gs-resolved');
    if (!el) return;
    const res = (me.seat && me.seat.resolved) || {};
    el.textContent = res.provider ? `seat: ${res.provider}${res.model ? ' · ' + res.model : ''}` : '';
}

function drawGameSidebar(me) {
    const el = me.root?.querySelector('#gr-game-sidebar');
    if (!el || !me.mod) return;
    if (me.mod.renderSidebar) me.mod.renderSidebar(el, me.state, me.ctx);
}

// ------------------------------------------------------------------ the subtitle
// Her latest row, over the felt, ONLY while the rail is folded (rail open =
// the rail is the voice; the caption stays dark). Text comes from the pair
// the server just landed (seat lane) or from the bubble the rail just
// rendered (chat lane) — never a third source.

function railFolded(me) {
    return !!me.root?.querySelector('#gr-room')?.classList.contains('rail-collapsed');
}

function showCaption(me, text) {
    const el = me.root?.querySelector('#gr-caption');
    if (!el || R !== me || !railFolded(me)) return;
    // Her row = dealer lines (🂠 …) then her words. Moves in small caps,
    // words as spoken; empty words = the move alone.
    const moves = [], words = [];
    for (const line of String(text || '').split('\n')) {
        const t = line.trim();
        if (!t || t === '\u2026') continue;
        if (t.startsWith('\u{1F0A0}')) moves.push(t.slice(2).trim()); else words.push(t);
    }
    if (!moves.length && !words.length) return;
    el.innerHTML = `${moves.length ? `<span class="gr-cap-move">${esc(moves.join(' · '))}</span>` : ''}`
        + `${words.length ? `<span class="gr-cap-say">${esc(words.join(' ').slice(0, 400))}</span>` : ''}`;
    el.classList.remove('fade');
    el.classList.add('on');
    if (me.capTimer) clearTimeout(me.capTimer);
    me.capTimer = setTimeout(() => hideCaption(me), CAPTION_MS);
}

function hideCaption(me) {
    const el = me.root?.querySelector('#gr-caption');
    if (me.capTimer) { clearTimeout(me.capTimer); me.capTimer = null; }
    if (!el || !el.classList.contains('on')) return;
    el.classList.add('fade');
    setTimeout(() => { if (el.classList.contains('fade')) { el.classList.remove('on', 'fade'); el.innerHTML = ''; } }, 650);
}

// Chat lane: after her turn on this session ends, the rail has rendered
// her bubble (folded rails still render — display:none, not detached).
// Read it back rather than refetch the history: same text the rail shows.
function captionFromRail(me) {
    if (R !== me || !railFolded(me)) return;
    setTimeout(() => {
        if (R !== me) return;
        const bubbles = document.querySelectorAll('#chat-container .message.assistant:not(.status):not(.error) .message-content');
        const last = bubbles[bubbles.length - 1];
        if (!last) return;
        const copy = last.cloneNode(true);
        copy.querySelectorAll('details, .thinking, .think, .tool-call, .tool-accordion, .message-metadata, script, style').forEach(n => n.remove());
        const text = (copy.textContent || '').replace(/<(?:seed:)?think>[\s\S]*?(?:<\/(?:seed:)?think>|$)/g, '').replace(/\s+/g, ' ').trim();
        if (text) showCaption(me, firstSentences(text));
    }, 350);
}

// A subtitle is a line, not a paragraph: the first sentence, a second if it
// still fits, then … (the rail has the rest).
function firstSentences(text, max = 200) {
    const t = String(text || '').replace(/\s+/g, ' ').trim();
    if (t.length <= max) return t;
    const parts = t.match(/[^.!?…]+[.!?…]+["')\]]?\s*|[^.!?…]+$/g) || [t];
    let out = '';
    for (const p of parts) {
        if ((out + p).trim().length > max) break;
        out += p;
    }
    out = out.trim() || t.slice(0, max);
    return out.length < t.length ? out.replace(/[.!?…]*$/, '') + '…' : out;
}

// ------------------------------------------------------------------ the cadence organ (F3)
// Arm on open, keep alive while open, disarm on leave (+ the session-end
// summary turn, which runs server-side after we're gone). The stage-bar
// pill shows the mode and the countdown; ⏸ writes the session's pause.

function startCadence(me) {
    const arm = async () => {
        if (R !== me) return;
        try {
            const r = await me.ctx.api('room/cadence/arm', 'POST', {});
            if (R !== me) return;
            me.cad = r.cadence || null;
            me.cadSpec = r.spec || null;
            paintCadence(me);
            startFrames(me);
        } catch (e) { /* not a game session, or the organ is off — the pill stays dark */ }
    };
    arm();
    me.cadKeep = setInterval(arm, KEEPALIVE_MS);
    me.cadPaint = setInterval(() => { if (me.cad && me.cad.next_in != null && !me.cad.paused) { me.cad.next_in = Math.max(0, me.cad.next_in - 1); paintCadence(me); } }, 1000);
    const btn = me.root?.querySelector('#gr-cad-pause');
    if (btn) btn.onclick = async () => {
        if (!me.cad) return;
        try {
            const r = await me.ctx.api('room/cadence/pause', 'POST', { paused: !me.cad.paused });
            if (R !== me) return;
            me.cad = r.cadence || me.cad;
            if (me.cad) me.cad.paused = !!r.paused;
            paintCadence(me);
        } catch (e) { showError(e.message); }
    };
}

function stopCadence(me) {
    if (me.cadKeep) { clearInterval(me.cadKeep); me.cadKeep = null; }
    if (me.cadPaint) { clearInterval(me.cadPaint); me.cadPaint = null; }
    stopFrames(me);
    if (me.cad && me.cad.armed) {
        // the session is ending: disarm + her summary (server-side, after us)
        fetch(PLUGIN_API + 'room/session-end', {
            method: 'POST', keepalive: true,
            headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf() },
            body: JSON.stringify({ session: me.session }),
        }).catch(() => {});
    }
    me.cad = null;
}

const ordinal = n => n + ({ 1: 'st', 2: 'nd', 3: 'rd' }[(n % 100 > 10 && n % 100 < 14) ? 0 : n % 10] || 'th');

function paintCadence(me) {
    const pill = me.root?.querySelector('#gr-cad');
    const text = me.root?.querySelector('#gr-cad-text');
    const btn = me.root?.querySelector('#gr-cad-pause');
    if (!pill || !text) return;
    const c = me.cad;
    if (!c || !c.armed) { pill.style.display = 'none'; return; }
    pill.style.display = '';
    let label;
    const moments = (me.spec.moments || []).join(', ');
    if (c.paused) label = 'her turns paused';
    else if (c.running) label = 'her turn…';
    else if (c.mode === 'event') {
        label = c.pending ? 'her turn: soon' : `her turns: ${moments || 'on game events'}`;
        // every-N: the count so far rides the pill — "· every 3rd (1/3)"
        if (!c.pending && c.every > 1) label += ` · every ${ordinal(c.every)}${c.pokes ? ` (${c.pokes}/${c.every})` : ''}`;
    }
    else label = c.next_in != null ? `her turn in ${Math.ceil(c.next_in)}s` : 'her turns: on';
    if (c.skips) label += ` (waited ${c.skips}×)`;
    text.textContent = label;
    if (btn) { btn.textContent = c.paused ? '\u25B6' : '\u23F8'; btn.title = c.paused ? 'Resume her unprompted turns' : 'Pause her unprompted turns in this session'; }
}

// ── perception: frames from a canvas stage ──
// TIMER rooms: while `send_frames` is on and the stage holds a canvas, grab
// it on a steady beat (the gap spread over frames_per_tick), keep the last
// N, deposit the ring — latest wins server-side, the organ takes it at fire
// time. EVENT rooms capture at the moment instead (pokeMoment). A board may
// deposit its own view (ctx.deposit).

// opts.force = a terminal moment (the castle fell) — skips the every-N count.
async function pokeMoment(me, note, opts = {}) {
    if (R !== me) return;
    if (me.cadSpec?.send_frames) {
        const f = grabFrame(me);
        if (f) await deposit(me, { frames: [f], text: note || '' });
    }
    return me.ctx.api('room/cadence/poke', 'POST', { note: note || '', force: !!opts.force })
        .then(r => { if (R === me && r?.cadence?.armed) { me.cad = r.cadence; paintCadence(me); } })
        .catch(() => {});
}

function grabFrame(me) {
    const cv = me.root?.querySelector('#gr-stage canvas');
    if (!cv || !cv.width || !cv.height) return null;
    try {
        const short = Math.max(128, Math.min(1080, me.cadSpec?.frame_short_edge_px || 512));
        const scale = Math.min(1, short / Math.min(cv.width, cv.height));
        const off = document.createElement('canvas');
        off.width = Math.round(cv.width * scale); off.height = Math.round(cv.height * scale);
        off.getContext('2d').drawImage(cv, 0, 0, off.width, off.height);
        return { data: off.toDataURL('image/jpeg', 0.72), media_type: 'image/jpeg' };
    } catch (e) { return null; }   // a tainted canvas or a dead stage
}

function startFrames(me) {
    const spec = me.cadSpec, c = me.cad;
    if (!spec || !c || !c.armed || !spec.send_frames || c.mode !== 'timer') { stopFrames(me); return; }
    if (me.frameTimer) return;
    const n = Math.max(1, spec.frames_per_tick || 6);
    const every = Math.max(2, Math.min(30, (spec.min_s || 60) / n)) * 1000;
    me.frames = [];
    me.frameTimer = setInterval(() => captureFrame(me, n), every);
}

function stopFrames(me) {
    if (me.frameTimer) { clearInterval(me.frameTimer); me.frameTimer = null; }
    me.frames = [];
}

function captureFrame(me, n) {
    if (R !== me || document.hidden) return;
    const f = grabFrame(me);
    if (!f) return;
    me.frames = [...(me.frames || []), f].slice(-n);
    deposit(me, { frames: me.frames });
}

function deposit(me, { frames, text } = {}) {
    if (R !== me) return Promise.resolve(false);
    return fetch(`/api/perception/${encodeURIComponent(me.session)}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf() },
        body: JSON.stringify({ frames: frames || [], text: text || '', source: 'room' }),
    }).then(r => r.ok).catch(() => false);
}

// ------------------------------------------------------------------ backdrop
// Painted straight onto the transplanted #chatbg (the same pipe
// applyBackground uses: inline image + has-bg scrim class). Pure client-
// side — no settings stamping. The pre-room look is captured at first paint
// and restored on close(), BEFORE the organs go home (finding 4.3 order).

function setBackdrop(me, url) {
    const el = document.getElementById('chatbg');
    if (!el || R !== me) return;
    if (me.bdPrev === null) me.bdPrev = { img: el.style.backgroundImage || '', had: el.classList.contains('has-bg') };
    me.bdUrl = url || null;
    if (url) {
        claimBackground(OWNER);            // core defers its paints while we own it
        // Assert against DOM truth EVERY paint, never a memo: core's
        // updateScene repaints #chatbg from chat settings on prompt/settings
        // events and silently wins otherwise (sapph-bg-at-the-stern-rail).
        const want = `url("${url}")`;
        if (el.style.backgroundImage !== want) el.style.backgroundImage = want;
        el.classList.add('has-bg');
    } else {
        el.style.backgroundImage = me.bdPrev.img;
        el.classList.toggle('has-bg', me.bdPrev.had);
        // Backdrop-less: hand the surface back, or a prior claim leaks and
        // core's set_scene paints get swallowed until exit (P0 hunt).
        releaseBackground(OWNER);
    }
}

function restoreBackdrop(me) {
    if (me.bdPrev !== null) {
        const el = document.getElementById('chatbg');
        if (el) {
            el.style.backgroundImage = me.bdPrev.img;
            el.classList.toggle('has-bg', me.bdPrev.had);
        }
    }
    releaseBackground(OWNER);
    me.bdPrev = null; me.bdUrl = null;
}

// ------------------------------------------------------------------ liveness tick
// Timer identity IS the liveness token (finding 4.4): a tick in flight when
// close() runs must land nowhere.

function startTick(me) {
    const { every, fn } = me.spec.tick;
    const tick = async (myTimer) => {
        if (me.timer !== myTimer || R !== me) { clearInterval(myTimer); return; }
        if (!me.root || document.hidden) return;
        if (!document.body.contains(me.root)) { clearInterval(myTimer); me.timer = null; return; }
        try { await fn(me.ctx); } catch (e) { /* offline tick */ }
    };
    const myTimer = setInterval(() => tick(myTimer), every || 12000);
    me.timer = myTimer;
    me.tickFn = () => tick(myTimer);
}

// ------------------------------------------------------------------ the ONE identity watch
// Module-level, bound once; R guards liveness. Vault lock → the session may
// be sealed (bounce). SSE reconnect → the server may have rebooted (re-take
// attention, let the mode re-dress; bounce if the session is gone).
// Remote pointer move → the rail does NOT follow (F1); offer a retake.

let _watchBound = false;
function bindWatch() {
    if (_watchBound) return;
    _watchBound = true;
    eventBus.on(eventBus.Events.PROMPT_CHANGED, async (d) => {
        const me = R;
        if (d?.action !== 'vault_changed' || !me) return;
        try {
            const list = await listSessions();
            if (R === me && !list.some(c => c.name === me.session)) {
                ui.showToast('Vault locked — this private session is sealed until you unlock', 'info');
                bounce(me);
            }
        } catch { /* listing failed — leave the room alone */ }
    });
    eventBus.on(eventBus.Events.BUS_CONNECTED, () => {
        const me = R;
        if (!me) return;
        setTimeout(async () => {
            if (R !== me) return;
            try {
                const list = await listSessions();
                if (R !== me) return;
                if (!list.some(c => c.name === me.session)) throw new Error('gone');
                await activateSession(me.session).catch(() => {});
                if (R !== me) return;
                if (me.spec.onReconnect) await me.spec.onReconnect(me.ctx);
                paintAttention(me);
            } catch {
                if (R !== me) return;
                ui.showToast('Session unavailable after restart — back to the library', 'info');
                bounce(me);
            }
        }, 800);
    });
    eventBus.on(eventBus.Events.CHAT_SWITCHED, (d) => {
        const me = R;
        if (!me) return;
        paintAttention(me, d?.name);
    });
    eventBus.on(eventBus.Events.MESSAGE_ADDED, (d) => {
        const me = R;
        if (!me || !me.spec.onTurn) return;
        if (d?.chat_name && d.chat_name !== me.session) return;
        try { me.spec.onTurn(me.ctx); } catch (e) { /* mode's problem */ }
    });
    // Her chat-lane reply on this session (typed Send, wake word, phone):
    // the subtitle carries it while the rail is folded.
    const mine = (me, d) => !d?.chat ? !d?.foreign : d.chat === me.session;
    eventBus.on(eventBus.Events.AI_TYPING_END, (d) => {
        const me = R;
        if (me && me.spec.sayRidesMoves && mine(me, d)) captionFromRail(me);
    });
    eventBus.on('voice_turn_end', (d) => {
        const me = R;
        if (!me || !mine(me, d)) return;
        if (d?.source) {
            // her unprompted turn: the END carries what she actually said
            // (think blocks gone; empty = a dropped no-answer turn)
            if (d.text) showCaption(me, firstSentences(d.text));
        } else if (me.spec.sayRidesMoves) {
            captionFromRail(me);
        }
        // 'browser' voice route: this tab speaks it
        if (d?.speak === 'browser' && d?.text && me.spec.cadence) speakText(me, String(d.text).slice(0, 2000));
        if (me.cad) { me.cad.running = false; me.cad.last_at = Date.now() / 1000; me.cad.pending = false; paintCadence(me); }
    });
    eventBus.on('voice_turn_start', (d) => {
        const me = R;
        if (me && me.cad && mine(me, d) && d?.source === 'cadence') { me.cad.running = true; paintCadence(me); }
    });
}

// "attention" = the server's active pointer (where wake-word / phone turns
// land). Typed turns here go by name regardless; the pill only says where
// her ears are.
function paintAttention(me, activeName) {
    const btn = me.root?.querySelector('#gr-attn');
    if (!btn) return;
    if (activeName === undefined) {
        activeName = document.getElementById('chat-select')?.value || me.session;
    }
    btn.style.display = (activeName && activeName !== me.session) ? '' : 'none';
}

// ------------------------------------------------------------------ the game adapter
// What room.js's openRoom was: a registered game (poker, Dark Horse) with a
// board module, the sealed seat for moves, the chat for talk.

export async function openGame(root, gameMeta, sessionName, opts = {}) {
    if (!sessionName) sessionName = await ensureSession(gameMeta.id, gameMeta.id);
    const games = opts.games || [];
    const byId = Object.fromEntries(games.map(g => [g.id, g]));
    const v = bootV();
    const gen = opts.gen != null ? opts.gen : 0;
    const spec = {
        id: gameMeta.id, kind: 'game',
        title: gameMeta.title || gameMeta.id, icon: gameMeta.icon || '🎲',
        hash: '#app-game-room/' + encodeURIComponent(gameMeta.id),
        loading: 'Setting the table...',
        stage: { mode: gameMeta.stage_mode || 'side', keepsFocus: !!gameMeta.keeps_focus },
        moments: gameMeta.moments || [],
        // Module bust = boot version + registry generation: a plugin hot
        // reload/toggle bumps the generation, so an updated board module is
        // fetched fresh while an unchanged one stays memoized (the old
        // t=Date.now() retained one module instance per room entry, forever).
        board: () => import(`/plugin-web/${gameMeta.plugin_name}/${gameMeta.entry_js}?v=${v}&g=${gen}`),
        sayRidesMoves: true, voice: true, roomKeys: true, cadence: true,
        settingsButton: async () => {
            const mod = await import(`./settings-modal.js?v=${v}`);
            mod.openGameSettings(gameMeta.id);
        },
        clearSession: true,
        sessions: {
            match: (c) => !!byId[c.settings?.game_id],
            label: (c) => `${esc(byId[c.settings?.game_id]?.icon || '🎲')} ${esc(c.display_name || c.name)}`,
            open: (name, c) => {
                const g = (c && byId[c.settings?.game_id]) || gameMeta;
                openGame(root, g, name, opts).catch(e => showError(e.message));
            },
        },
        newSession: { noun: 'session' },
        deleteSession: {
            before: (ctx) => ctx.api(`play/${gameMeta.id}/forget`, 'POST', {}),
        },
    };
    return openRoom(root, spec, sessionName, opts);
}
