// Game Room — the room. Hosts a game on the Surface (game mode): stage +
// compact talk rail in the main pane, composer + action strip below, game
// sidebar on the right (tmp/chat-surface-plan.md Phase 2c).
//
// Sessions are mode-tagged chats — the chat IS the save. Opening a session
// activates its chat (server active-chat singleton), so per-session settings
// (persona/prompt, provider/model, voice, trim) ride the normal chat plumbing
// and her seat's brain resolves from them server-side. Moves and table talk
// stay on the sealed one-shot seat (hidden info never touches a chat).
//
// A board module supplies {id, renderBoard, renderActions[, renderSidebar]}
// (poker.js unchanged), or {mount, unmount} for free-mount games (SNES later).

import { renderSurface } from '/static/surface/surface.js';
import { accordionHtml, initAccordions } from '/static/shared/accordion.js';
import { coreSections } from '/static/surface/sections/core-sections.js';
import { playTextStreaming, playText, stop as ttsStop } from '/static/audio.js';
import * as coreApi from '/static/api.js';
import * as ui from '/static/ui.js';
import { refresh } from '/static/core/state.js';
import { updateScene, updateSendButtonLLM } from '/static/features/scene.js';
import { applyTrimColor } from '/static/features/chat-settings.js';
import { populateChatDropdown } from '/static/features/chat-manager.js';

const PLUGIN_API = '/api/plugin/game-room/';
const VOICE_KEY = 'gameroom_voice';
const SIDEBAR_KEY = 'sapphire-game-sidebar';
const TALK_KEY = 'sapphire-game-talk';

let _root = null, _back = null, _game = null, _mod = null, _games = [];
let _session = null, _state = null, _seat = null, _chatSettings = {};
let _busy = false, _busyLabel = '';
let _ttsEnabled = false;
let _voiceOn = localStorage.getItem(VOICE_KEY) === '1';
let _lastSpokenSeq = null;
let _speakChain = Promise.resolve();

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

async function api(path, method, body) {
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), 180000);
    try {
        const res = await fetch(PLUGIN_API + path, {
            method: method || 'GET',
            headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf() },
            body: body ? JSON.stringify(body) : undefined,
            signal: ctl.signal,
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.error || ('HTTP ' + res.status));
        return data;
    } finally {
        clearTimeout(timer);
    }
}

const gamePath = (p) => 'play/' + _game.id + '/' + p;

// ------------------------------------------------------------------ sessions

export function sanitizeName(name) {
    // Mirror core create_chat: keep alnum/space/dash/underscore, spaces → _
    return String(name || '').split('').filter(c => /[a-zA-Z0-9 _-]/.test(c)).join('')
        .trim().replace(/\s+/g, '_').toLowerCase();
}

export async function listSessions() {
    const data = await coreApi.fetchChatList();
    return (data.chats || []).filter(c =>
        (c.mode || c.settings?.mode) === 'game' && !c.archived);
}

export async function createSession(game, rawName) {
    const name = sanitizeName(rawName);
    if (!name) throw new Error('Session needs a name');
    await coreApi.createChat(name);
    await coreApi.updateChatSettings(name, { mode: 'game', game_id: game.id });
    return name;
}

// Newest session tagged `tagId`, or a fresh auto-named one (base, base_2...).
// Stories tag as "story:<slug>" — colon can't collide with game ids.
export async function ensureSession(tagId, baseName) {
    const mine = (await listSessions())
        .filter(c => (c.settings?.game_id) === tagId);
    if (mine.length) return mine[0].name;   // list is updated_at DESC
    for (let n = 1; n <= 20; n++) {
        const nm = sanitizeName(n === 1 ? baseName : `${baseName}_${n}`);
        try {
            await coreApi.createChat(nm);
            await coreApi.updateChatSettings(nm, { mode: 'game', game_id: tagId });
            return nm;
        } catch (e) {
            if (!String(e.message).includes('already exists')) throw e;
        }
    }
    throw new Error('Could not find a free session name');
}

export const latestOrNewSession = (game) => ensureSession(game.id, game.id);

// Activate a session chat + repaint core (rail/scene/trim/picker) — the same
// post-activate sequence openRoom runs, exported for the library's story tiles.
export async function activateSession(name) {
    const act = await coreApi.activateChat(name);
    syncCore(name, act?.settings || {});
    return act;
}

// Repaint the core chat machinery after switching the active chat from here —
// same post-activate sequence handleChatChange runs, so returning to the Chat
// view shows the session with fresh rail/scene/trim/sidebar (no stale state).
function syncCore(sessionName, settings) {
    Promise.allSettled([
        refresh(false),
        updateScene(),
        populateChatDropdown(),
    ]).catch(() => {});
    try {
        updateSendButtonLLM(settings.llm_primary || 'auto', settings.llm_model || '');
        applyTrimColor(settings.trim_color || '');
        const sel = document.getElementById('chat-select');
        if (sel) {
            sel.value = sessionName;
            sel.dispatchEvent(new CustomEvent('chat-activated', {
                detail: { chat: sessionName, settings }
            }));
        }
    } catch (e) { console.warn('[GameRoom] core sync failed', e); }
}

// ------------------------------------------------------------------ open/close

export async function openRoom(root, gameMeta, sessionName, opts) {
    close();
    _root = root; _game = gameMeta; _back = opts?.back; _games = opts?.games || [];
    root.innerHTML = '<div class="pk-loading">Setting the table...</div>';

    if (!sessionName) sessionName = await latestOrNewSession(gameMeta);
    _session = sessionName;
    // Bookmarkable: #app-game-room/<game>. The navrail item resets this to
    // the base hash (library) when clicked while already in a game.
    history.replaceState(null, '', '#app-game-room/' + encodeURIComponent(gameMeta.id));

    const act = await coreApi.activateChat(sessionName);
    _chatSettings = act?.settings || {};
    syncCore(sessionName, _chatSettings);

    const v = document.querySelector('meta[name="boot-version"]')?.content || '';
    const [mod, status] = await Promise.all([
        // &t= busts the ES MODULE MAP, not just HTTP cache: import() of an
        // identical URL returns the memoized module WITHOUT a network fetch,
        // so no-store alone can't deliver updated game code to an open tab
        // (stale-towerd hunt, 2026-08-04; same lesson as loadPluginScripts'
        // loadId, 2026-05-14). Games are hot-iterated — always fetch fresh.
        import(`/plugin-web/${gameMeta.plugin_name}/${gameMeta.entry_js}?v=${v}&t=${Date.now()}`),
        fetch('/api/status', { headers: { 'X-CSRF-Token': csrf() } })
            .then(r => r.ok ? r.json() : null).catch(() => null),
    ]);
    if (_root !== root || _session !== sessionName) return;   // superseded mid-load
    _mod = mod.game || mod;
    _ttsEnabled = !!(status && status.tts_enabled);

    skeleton();
    await loadState();
    baseline();
    drawAll();
}

export function close() {
    if (_mod?.unmount) { try { _mod.unmount(); } catch (e) { /* game's problem */ } }
    // force: plain stop() no-ops while a stream is mid-flight, so leaving a
    // room let her keep talking into the next one (finding 4.11).
    try { ttsStop(true); } catch (e) { /* nothing playing */ }
    _root = null; _game = null; _mod = null; _session = null;
    _state = null; _seat = null;
    _busy = false; _lastSpokenSeq = null;
}

async function loadState() {
    // Supersede guard: the response is only allowed to land if the room is
    // still showing the session it was fetched for. Without it, session A's
    // board rendered into room B after a fast switch (finding 4.5).
    const root = _root, session = _session;
    try {
        const data = await api(gamePath('state') + '?session=' + encodeURIComponent(session));
        if (_root !== root || _session !== session) return;
        _seat = data.seat || null;
        delete data.seat;
        _state = data;
    } catch (e) {
        if (_root !== root || _session !== session) return;
        showError(e.message);
    }
}

// ------------------------------------------------------------------ layout

function skeleton() {
    renderSurface(_root, {
        id: 'game',
        cssClass: 'surface-game',
        mainPane: `
            <div class="gr-stage-bar">
                <button type="button" id="gr-stage-back" class="sb-icon-btn" title="Back to Game Room">&#x2190;</button>
                <span class="gr-stage-title">${esc(_game.icon || '🎲')} ${esc(_game.title || _game.id)}</span>
                <span class="gr-stage-info" id="gr-stage-info"></span>
            </div>
            <div class="gr-room" id="gr-room">
                <div class="gr-stage" id="gr-stage"></div>
                <aside class="gr-talk">
                    <div class="gr-talk-head">
                        <button type="button" id="gr-talk-toggle" class="sb-icon-btn" title="Hide table talk">&#x25B6;</button>
                        <span>table talk</span></div>
                    <div class="gr-talk-log" id="gr-talklog"></div>
                </aside>
                <button type="button" id="gr-talk-expand" class="gr-talk-expand" title="Show table talk">&#x1F4AC;</button>
            </div>`,
        formArea: `
            <div class="gr-botrow">
                <input type="text" class="gr-composer" id="gr-composer" maxlength="300"
                       placeholder="Table talk (optional) — rides with your move, or Send it solo" autocomplete="off">
                <button class="pk-btn" id="gr-send" title="Just talk — no move">Send</button>
            </div>
            <div class="gr-actions" id="gr-actions"></div>`,
        sidebarHeader: `
            <div class="sb-chat-header">
                <button type="button" id="gr-back" class="sb-icon-btn" title="Game library">🎲</button>
                <div class="sb-chat-picker" id="gr-session-picker">
                    <button class="sb-chat-picker-btn" id="gr-session-btn">
                        <span id="gr-session-name">${esc(_session)}</span>
                        <span class="sb-chat-arrow">&#x25BE;</span>
                    </button>
                    <div class="sb-chat-picker-dropdown" id="gr-session-dropdown"></div>
                </div>
                <button type="button" id="gr-sidebar-toggle" class="sb-icon-btn sb-collapse-btn" title="Hide sidebar">&#x25B6;</button>
            </div>
            <div class="sb-chat-actions">
                <button type="button" id="gr-new-session" class="sb-icon-btn" title="New session">+</button>
                <button type="button" id="gr-clear-session" class="sb-icon-btn" title="Clear session — fresh chips and history, keeps the name">&#x2715;</button>
                <button type="button" id="gr-speak" class="sb-icon-btn" title="Speak her table talk">${_voiceOn ? '🔊' : '🔇'}</button>
                <button type="button" id="gr-del-session" class="sb-icon-btn sb-icon-danger" title="Delete this session (chat + game state)">&#x1F5D1;</button>
            </div>
            <button type="button" id="gr-game-settings" class="sb-btn-full gr-settings-btn">&#x2699;&#xFE0E; Game Settings</button>
            <div class="gr-game-title">${esc(_game.icon || '')} ${esc(_game.title || _game.id)}</div>`,
        sidebarBody: `
            <div class="sidebar-section">
                ${coreSections.filter(s => s.bare).map(s =>
                    `<div class="gs-content" data-sec="${s.key}"></div>`).join('')}
            </div>
            ${coreSections.filter(s => !s.bare).map(s => accordionHtml({
                id: 'surface:' + s.key, title: s.title, icon: s.icon, open: !!s.open,
                content: `<div class="gs-content" data-sec="${s.key}"></div>`,
            })).join('')}
            <div id="gr-game-sidebar"></div>`,
    });

    const $ = (sel) => _root.querySelector(sel);

    // Sidebar collapse — own preference, chat's CSS
    const sidebar = $('.chat-sidebar');
    if (localStorage.getItem(SIDEBAR_KEY) === 'collapsed') sidebar.classList.add('collapsed');
    const toggleSb = () => {
        const collapsed = sidebar.classList.toggle('collapsed');
        localStorage.setItem(SIDEBAR_KEY, collapsed ? 'collapsed' : 'expanded');
    };
    $('#gr-sidebar-toggle').onclick = toggleSb;
    $('#chat-sidebar-expand').onclick = toggleSb;

    // Table-talk rail collapse — its expand tab sits lower on the right edge
    // than the sidebar's, so both can be collapsed without overlapping.
    const roomEl = $('#gr-room');
    if (localStorage.getItem(TALK_KEY) === 'collapsed') roomEl.classList.add('talk-collapsed');
    const toggleTalk = () => {
        const c = roomEl.classList.toggle('talk-collapsed');
        localStorage.setItem(TALK_KEY, c ? 'collapsed' : 'expanded');
    };
    $('#gr-talk-toggle').onclick = toggleTalk;
    $('#gr-talk-expand').onclick = toggleTalk;

    $('#gr-stage-back').onclick = () => { const back = _back; close(); if (back) back(); };

    initAccordions($('.chat-sidebar-inner'), 'game-sidebar');
    initSections();   // async fill — per-session settings controls

    $('#gr-back').onclick = () => { const back = _back; close(); if (back) back(); };
    $('#gr-new-session').onclick = newSessionPrompt;
    $('#gr-clear-session').onclick = clearSessionPrompt;
    $('#gr-del-session').onclick = deleteSessionPrompt;
    $('#gr-game-settings').onclick = async () => {
        const v = document.querySelector('meta[name="boot-version"]')?.content || '';
        const mod = await import(`./settings-modal.js?v=${v}`);
        mod.openGameSettings(_game?.id || null);
    };
    $('#gr-speak').onclick = () => {
        _voiceOn = !_voiceOn;
        localStorage.setItem(VOICE_KEY, _voiceOn ? '1' : '0');
        const b = _root && $('#gr-speak');
        if (b) b.textContent = _voiceOn ? '🔊' : '🔇';
        if (_voiceOn && _ttsEnabled) speakText('Voice on. Let’s play.');
        else { try { ttsStop(); } catch (e) { /* idle */ } }
    };

    // Session picker
    $('#gr-session-btn').onclick = async (e) => {
        e.stopPropagation();
        const picker = $('#gr-session-picker');
        if (picker.classList.toggle('open')) await fillSessionDropdown();
    };
    bindDocClose();

    const composer = $('#gr-composer');
    composer.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendBanter(); }
    });
    $('#gr-send').onclick = sendBanter;
}

let _docCloseBound = false;
function bindDocClose() {
    if (_docCloseBound) return;             // once per page life, not per room
    _docCloseBound = true;
    document.addEventListener('click', () => {
        _root?.querySelector('#gr-session-picker')?.classList.remove('open');
    });
}

async function fillSessionDropdown() {
    const dd = _root?.querySelector('#gr-session-dropdown');
    if (!dd) return;
    let sessions = [];
    try { sessions = await listSessions(); } catch (e) { return; }
    const byId = Object.fromEntries(_games.map(g => [g.id, g]));
    dd.innerHTML = sessions.map(c => {
        const gid = c.settings?.game_id;
        const g = byId[gid];
        if (!g) return '';                  // its game's plugin is off — dormant
        return `<button class="chat-picker-item${c.name === _session ? ' active' : ''}"
                        data-session="${esc(c.name)}" data-game="${esc(gid)}">
                    <span class="chat-picker-item-check">${c.name === _session ? '✓' : ''}</span>
                    <span class="chat-picker-item-name">${esc(g.icon || '🎲')} ${esc(c.display_name)}</span>
                </button>`;
    }).join('') || '<div class="gr-dd-empty">No sessions yet</div>';
    dd.querySelectorAll('[data-session]').forEach(btn => {
        btn.onclick = () => {
            const root = _root, back = _back, games = _games;
            const g = games.find(x => x.id === btn.dataset.game);
            if (g) openRoom(root, g, btn.dataset.session, { back, games });
        };
    });
}

// Fill the per-session settings sections. Saves target the session BY NAME
// (works active or not — core/routes/chat.py non-active branch), so a save
// that lands after a session switch still writes to the right chat.
async function initSections() {
    const root = _root, session = _session;
    const ctx = {
        settings: _chatSettings,
        save: async (patch) => {
            try {
                await coreApi.updateChatSettings(session, patch);
                Object.assign(_chatSettings, patch);
                if (_session === session) { await loadState(); drawResolved(); }
            } catch (e) { showError(e.message); }
        },
    };
    for (const s of coreSections) {
        if (_root !== root || _session !== session) return;   // superseded
        const el = root.querySelector(`.gs-content[data-sec="${s.key}"]`);
        if (!el) continue;
        el.innerHTML = s.html();
        try { await s.init(el, ctx); } catch (e) { console.warn('[GameRoom] section failed:', s.key, e); }
    }
    drawResolved();
}

async function clearSessionPrompt() {
    if (!_session || !_game) return;
    if (!confirm(`Clear "${_session}"?\n\nWipes the chat history and resets the ${_game.title || _game.id} table — fresh chips, same name.`)) return;
    const session = _session;
    try {
        // BY NAME, never "the active chat" — the dialog names a session, so
        // that is the only thing allowed to be wiped. clearChat() hit
        // whatever was active, which a second tab or phone turn can change
        // between the confirm and the click (finding 2.3).
        const res = await coreApi.bulkClearChats([session]);
        if (!res?.results?.[session]?.ok)
            throw new Error(res?.results?.[session]?.message || 'clear failed');
        _state = await api(gamePath('new-session'), 'POST', { session });
        _seat = _state.seat || _seat;
        delete _state.seat;
        baseline();
        refresh(false).catch(() => {});     // repaint the hidden chat rail
        drawAll();
        ui.showToast('Session cleared — fresh table', 'success', 2500);
    } catch (e) {
        showError(e.message);
    }
}

async function deleteSessionPrompt() {
    if (!_session || !_game) return;
    if (!confirm(`Delete session "${_session}"?\n\nRemoves its chat history AND its ${_game.title || _game.id} game state.`)) return;
    const session = _session, back = _back, forgetPath = gamePath('forget');
    try {
        try { await api(forgetPath, 'POST', { session }); } catch (e) { /* state cleanup is best-effort */ }
        const act = await coreApi.activateChat('default');
        await coreApi.deleteChat(session);
        syncCore('default', act?.settings || {});
        close();
        if (back) back();
    } catch (e) {
        showError(e.message);
    }
}

async function newSessionPrompt() {
    const name = prompt('Name for the new session:', `${_game.id} ${new Date().toISOString().slice(0, 10)}`);
    if (!name || !name.trim()) return;
    try {
        const session = await createSession(_game, name);
        const root = _root, back = _back, games = _games;
        await openRoom(root, _game, session, { back, games });
    } catch (e) {
        showError(e.message.includes('already exists') ? 'A chat with that name already exists.' : e.message);
    }
}

// ------------------------------------------------------------------ actions

async function post(path, body, label) {
    if (_busy) return;
    // The room may be closed or switched while the turn is in flight. Every
    // landing below is gated on still being the same (root, session) — a
    // reply used to render session A's board and SPEAK its line inside room
    // B, and close() dropped _busy so a stale reply could re-enter (4.5).
    const root = _root, session = _session;
    _busy = true; _busyLabel = label || 'thinking';
    drawBoard(); drawActions(); drawTalk();
    try {
        const next = await api(gamePath(path), 'POST', { ...(body || {}), session });
        if (_root !== root || _session !== session) return;
        _state = next;
        _seat = _state.seat || _seat;
        delete _state.seat;
        speakNew();
    } catch (e) {
        if (_root !== root || _session !== session) return;
        showError(e.message);
    } finally {
        if (_root === root && _session === session) _busy = false;
    }
    drawAll();
}

function composerText() {
    const el = _root && _root.querySelector('#gr-composer');
    return el ? el.value.trim() : '';
}

function clearComposer() {
    const el = _root && _root.querySelector('#gr-composer');
    if (el) el.value = '';
}

function requireTalk() {
    const text = composerText();
    if (!text) {
        showError('Table talk is mandatory — type a line first.');
        _root?.querySelector('#gr-composer')?.focus();
        return null;
    }
    return text;
}

function sendBanter() {
    if (_busy) return;
    const text = requireTalk();
    if (!text) return;
    clearComposer();
    post('banter', { say: text }, 'typing');
}

const ctx = {
    api, post, esc,
    prettyCodes: (s) => prettyCodes(s),
    state: () => _state,
    cfg: () => _seat || {},
    session: () => _session,
    busy: () => _busy,
    composerText, clearComposer, requireTalk,
    showError: (m) => showError(m),
    // One-line status in the stage bar ("Hand #10 · preflop") — games write
    // here instead of burning a row of their own board.
    stageInfo: (html) => {
        const el = _root && _root.querySelector('#gr-stage-info');
        if (el) el.innerHTML = html || '';
    },
};

// ------------------------------------------------------------------ voice
// (ported from the retired shell.js — same watermark + chain semantics)

function maxSeq(talk) {
    return (talk || []).reduce((m, t) => (t.seq != null && t.seq > m ? t.seq : m), 0);
}

function baseline() {
    if (_state) _lastSpokenSeq = maxSeq(_state.talk);
}

function speakNew() {
    if (!_state) return;
    if (_lastSpokenSeq === null) { baseline(); return; }
    const mx = maxSeq(_state.talk);
    if (mx < _lastSpokenSeq) _lastSpokenSeq = 0;   // seq went backwards = new stream
    const fresh = (_state.talk || []).filter(t =>
        t.who === 'ai' && t.seq != null && t.seq > _lastSpokenSeq);
    _lastSpokenSeq = Math.max(_lastSpokenSeq, mx);
    if (!_voiceOn || !_ttsEnabled || !fresh.length) return;
    speakText(fresh.map(t => t.text).join(' ... '));
}

function speakText(line) {
    // Token the queued line to the session that produced it: a chain entry
    // that starts after you've left used to play A's line in B's room, in
    // B's voice (finding 4.11).
    const token = _session;
    _speakChain = _speakChain.then(async () => {
        if (_session !== token) return;
        try {
            const voice = (_seat && _seat.voice) || null;
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

function drawAll() { drawBoard(); drawActions(); drawTalk(); drawResolved(); drawGameSidebar(); }

function sessName(who) {
    const s = _state && _state.session;
    if (!s) return who === 'ai' ? 'Sapphire' : 'You';
    return who === 'ai' ? s.ai_name : s.player_name;
}

function drawBoard() {
    const el = _root && _root.querySelector('#gr-stage');
    if (!el || !_mod) return;
    if (_mod.renderBoard) { _mod.renderBoard(el, _state, ctx); return; }
    if (_mod.mount && !el.dataset.mounted) {   // free-mount games own the stage
        el.dataset.mounted = '1';
        _mod.mount(el, ctx);
    }
}

function drawActions() {
    const el = _root && _root.querySelector('#gr-actions');
    if (!el || !_mod) return;
    if (!_mod.renderActions) { el.innerHTML = ''; return; }
    if (_busy) { el.innerHTML = `<div class="pk-wait">${esc(sessName('ai'))} is ${esc(_busyLabel)}&hellip;</div>`; return; }
    _mod.renderActions(el, _state, ctx);
}

function drawTalk() {
    const el = _root && _root.querySelector('#gr-talklog');
    if (!el) return;
    const s = _state || {};
    const names = { player: sessName('player'), ai: sessName('ai'), dealer: 'Dealer' };
    const rows = (s.talk || []).map(t => {
        const cls = t.who === 'dealer' ? 'pk-t-dealer' : (t.who === 'ai' ? 'pk-t-ai' : 'pk-t-me');
        const who = t.who === 'dealer' ? '' : `<b>${esc(names[t.who])}</b> `;
        const text = t.who === 'dealer' ? prettyCodes(esc(t.text)) : esc(t.text);
        return `<div class="pk-t ${cls}">${who}${text}</div>`;
    });
    if (_busy) rows.push(`<div class="pk-t pk-t-dealer gr-typing">${esc(names.ai)} is ${esc(_busyLabel)}&hellip;</div>`);
    el.innerHTML = rows.join('');
    el.scrollTop = el.scrollHeight;
}

function drawResolved() {
    const el = _root && _root.querySelector('.gs-resolved');
    if (!el) return;
    const res = (_seat && _seat.resolved) || {};
    el.textContent = res.provider
        ? `seat: ${res.provider}${res.model ? ' · ' + res.model : ''}`
        : '';
}

function drawGameSidebar() {
    const el = _root && _root.querySelector('#gr-game-sidebar');
    if (!el || !_mod) return;
    if (_mod.renderSidebar) _mod.renderSidebar(el, _state, ctx);
}

function showError(msg) {
    try { ui.showToast(msg, 'error'); }
    catch (e) { console.error('[GameRoom]', msg); }
}
