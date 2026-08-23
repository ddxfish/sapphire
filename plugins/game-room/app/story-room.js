// Game Room — the story room (Plan B, tmp/story-primitive-plan.md). Hosts a
// story on the Surface (story mode): the REAL chat rail + composer, borrowed
// via the organ transplant (/static/surface/organs.js), floating over a
// full-bleed stage the session chat's background paints. Streaming, mic, TTS,
// stop, images all work because they're the same live nodes, just visiting.
//
// Playthroughs are mode-tagged chats (game_id "story:<slug>") — the chat IS
// the save. Opening one activates its chat; the story engine's ghost rail and
// prompt swap ride the normal pipeline server-side.

import { renderSurface } from '/static/surface/surface.js';
import { accordionHtml, initAccordions } from '/static/shared/accordion.js';
import { coreSections } from '/static/surface/sections/core-sections.js';
import { claimOrgans, releaseOrgans } from '/static/surface/organs.js';
import { claimBackground, releaseBackground } from '/static/features/chat-settings.js';
import { getInitData } from '/static/shared/init-data.js';
import * as coreApi from '/static/api.js';
import * as ui from '/static/ui.js';
import { getIsProc } from '/static/core/state.js';
import { on as busOn, Events as BusEvents } from '/static/core/event-bus.js';

const OWNER = 'story-room';
const PLUGIN_API = '/api/plugin/game-room/';
const SIDEBAR_KEY = 'sapphire-story-sidebar';
// The referee's module name in the core registry — the "include story tools"
// checkbox unions it into ANY toolset via the chat's extra_toolsets setting.
const STORY_TOOLS = 'plugin_game-room_story_tools';
// Sections the story room hand-rolls: prompt (identity dropdown owns it)
// and toolset (needs the story-tools checkbox).
const OWN_SECTIONS = ['prompt', 'toolset'];

let room = null;                 // ./room.js — same URL as index.js's import,
                                 // so the module cache returns the same instance
const bootV = () => document.querySelector('meta[name="boot-version"]')?.content || '';

let _root = null, _story = null, _session = null, _back = null, _stories = [];
let _chatSettings = {};
let _status = null;              // last story/status payload (.active)
let _houseWasOpen = null;        // zork-line transition detector (null = no baseline)
let _timer = null;
let _tickNow = null;             // current open()'s tick, for the bus nudge
let _liveShown = {};             // seal key → live popup already raised this wait

// Live seal wait nudge (Krem 2026-08-06): the server publishes tool_executing
// the instant story_act begins — if she's reaching for an unfilled seal, a
// wait registers within milliseconds. Poll now (and once more past the
// registration race) so the popup raises the moment she reaches, not up to
// 12s later. Registered once at module load; _tickNow carries the current
// room's tick closure, so every liveness rule of the 12s poll applies.
busOn(BusEvents.TOOL_EXECUTING, (d) => {
    if (!_tickNow || document.hidden) return;
    if (!d || d.name !== 'story_act') return;
    _tickNow();
    const t = _tickNow;
    setTimeout(() => { if (_tickNow === t) _tickNow(); }, 1200);
});

// Vault watch (v1.3.1, room.js twin): the vault locking while a PRIVATE
// playthrough is open evicts its chat server-side — the story room must
// follow instead of showing a sealed tale. Registered once at module load;
// _session guards liveness like every other module-level listener here.
busOn(BusEvents.PROMPT_CHANGED, async (d) => {
    if (d?.action !== 'vault_changed' || !_session) return;
    try {
        const list = await room.listSessions();
        if (!list.some(c => c.name === _session)) {
            const back = _back;
            ui.showToast('Vault locked — this private playthrough is sealed until you unlock', 'info');
            close();
            if (back) back();
        }
    } catch { /* listing failed — leave the room alone */ }
});

// SSE reconnect twin (see room.js): server reboot lands active on
// 'default'; main.js's resync paints that into the transplanted rail under
// the story frame. Re-assert the playthrough after the storm settles —
// activateSession repaints the rail, story/reassert re-dresses the costume
// (boot re-merge covers visible chats, but re-assert is the resume law).
busOn(BusEvents.BUS_CONNECTED, () => {
    if (!_session) return;
    const sess = _session;
    setTimeout(async () => {
        if (_session !== sess) return;
        try {
            await room.activateSession(sess);
            await api('story/reassert', 'POST').catch(() => {});
        } catch {
            const back = _back;
            ui.showToast('Playthrough unavailable after restart — back to the library', 'info');
            close();
            if (back) back();
        }
    }, 800);
});

function csrf() {
    const m = document.querySelector('meta[name="csrf-token"]');
    return (m && m.content) || '';
}

// Every story call names its session (finding 1.1). The room knows which
// chat it has open; the server must not guess from "whatever is active",
// which a second tab, a phone turn, or a daemon can change underfoot.
// Read at issue time — the same instant the caller decided to call.
async function api(path, method, body) {
    let url = PLUGIN_API + path;
    let payload = body;
    if (_session) {
        if (method && method !== 'GET') payload = { ...(body || {}), session: _session };
        else url += (url.includes('?') ? '&' : '?') + 'session=' + encodeURIComponent(_session);
    }
    const res = await fetch(url, {
        method: method || 'GET',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf() },
        body: payload ? JSON.stringify(payload) : undefined,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || ('HTTP ' + res.status));
    return data;
}

// ------------------------------------------------------------------ open/close

// Start a story, routing through the Mad-Libs setup form when the story
// declares slots/presets/sets (open-mansion v1). Returns true = started,
// false = server refused, null = player cancelled the form.
async function startWithSetup(slug) {
    const mod = await import(`./settings-modal.js?v=${bootV()}`);
    const setup = await mod.storyNeedsSetup(slug);
    let payload = { story: slug };
    let sealedFills = [];
    if (setup) {
        // _session rides along so the form's Environment tab can stock the
        // house pre-start (rows land on this chat's user layer; start merges).
        const choice = await mod.openStorySetup(slug, setup, _session);
        if (choice === null) return null;
        payload = { story: slug, slots: choice.slots };
        sealedFills = choice.sealed || [];
    }
    const r = await api('story/start', 'POST', payload);
    if (!r.success) { ui.showToast(r.detail || 'story start failed', 'error'); return false; }
    // Sealed slot values ride the seal machinery — written at turn 0,
    // never through her (the whole mechanic).
    for (const f of sealedFills) {
        try {
            const fr = await api('story/fill', 'POST', { key: f.key, text: f.text });
            if (!fr.success) ui.showToast(`Seal '${f.key}': ${fr.detail}`, 'warning', 4000);
        } catch (e) { ui.showToast(`Seal '${f.key}': ${e.message}`, 'warning', 4000); }
    }
    return true;
}

export async function openStoryRoom(root, story, sessionName, opts) {
    if (!room) room = await import(`./room.js?v=${bootV()}`);
    if (getIsProc()) {
        ui.showToast('Finish the current turn first, then enter the story.', 'warning');
        return;
    }
    close();
    _root = root; _story = story; _session = sessionName;
    _houseWasOpen = null;                      // fresh baseline per room entry
    _back = opts?.back; _stories = opts?.stories || [];
    root.innerHTML = '<div class="pk-loading">Opening the book...</div>';

    history.replaceState(null, '', '#app-game-room/story:' + encodeURIComponent(story.slug));

    // activateChat REJECTS while a turn is streaming elsewhere (400). An
    // unhandled rejection here left a dead room with no back button
    // (finding 4.9), and assigning _chatSettings before the supersede guard
    // wrote the wrong session's settings into the right chat (finding 4.10).
    let act;
    try {
        act = await coreApi.activateChat(sessionName);
    } catch (e) {
        // Both checks, same as the success path: a rejection for session A
        // must not tear down session B opened meanwhile in the same root.
        if (_root !== root || _session !== sessionName) return;
        ui.showToast(`Could not open "${sessionName}": ${e.message}`, 'error');
        const back = _back; close(); if (back) back();
        return;
    }
    if (_root !== root || _session !== sessionName) return;    // superseded mid-load
    room.applyRoomModel(sessionName);
    _chatSettings = act?.settings || {};

    // Resume-aware start: auto-start ONLY a virgin session (zero messages).
    // A session with history but no active story is an ENDED playthrough —
    // refreshing the room must not silently begin a new tale (and re-stamp
    // the cockpit). The ▶ Start button is the deliberate way back in.
    try {
        const status = await api('story/status');
        if (!(status.active && status.active.slug === story.slug)) {
            const sess = (await room.listSessions()).find(c => c.name === sessionName);
            if (!sess || !sess.message_count) {
                // Mad-Libs gate (open-mansion v1): a story that declares
                // slots/presets/sets gets its setup form BEFORE room 1;
                // plain stories keep the zero-friction auto-start.
                const started = await startWithSetup(story.slug);
                if (_root !== root || _session !== sessionName) return;   // superseded
                if (started === null) {                    // player cancelled
                    const back = _back; close(); if (back) back();
                    return;
                }
                if (!started) throw new Error('story start failed');
            }
        } else {
            // Resume path: assert the costume. After a reboot the role prompt
            // ('rose') may be unregistered — and core's missing-prompt fallback
            // rewrites the chat to 'default' (Sapph-not-Rose, 2026-08-05).
            // Re-render + re-activate heals both, every entry.
            await api('story/reassert', 'POST').catch(() => {});
        }
        const st2 = await api('story/status');
        _status = st2.active;
        // Ended playthrough: THE END survives refresh — the journal replay
        // (status.last) recovers the final scene + earned ending card.
        if (!_status && st2.last && st2.last.slug === story.slug) _lastActive = st2.last;
        // Re-snapshot settings AFTER story_start stamped prompt/toolset/extras —
        // the pre-start snapshot painted the story-tools checkbox unchecked on
        // every fresh start while the server was actually armed (2026-08-03).
        const fresh = await fetch(`/api/chats/${encodeURIComponent(sessionName)}/settings`,
            { headers: { 'X-CSRF-Token': csrf() } });
        const freshSettings = fresh.ok ? (await fresh.json()).settings : null;
        if (_root !== root || _session !== sessionName) return;    // superseded
        if (freshSettings) _chatSettings = freshSettings;
    } catch (e) {
        ui.showToast(e.message, 'error');
        _status = null;                      // room still works — the rail is real
    }
    if (_root !== root || _session !== sessionName) return;

    skeleton();

    // The transplant: real rail + composer move into the story frame.
    const ok = claimOrgans({
        railSlot: root.querySelector('#st-rail-slot'),
        composerSlot: root.querySelector('.form-wrapper'),
    }, OWNER);
    if (!ok) {
        ui.showToast('Could not borrow the chat rail — finish the current turn first.', 'warning');
        const back = _back; close(); if (back) back();
        return;
    }
    try { ui.forceScrollToBottom(); } catch (e) { /* cosmetic */ }

    // Repaint core with the session chat active (rail paints wherever it lives)
    room.activateSession(sessionName).catch(() => {});

    paintPanel();
    const tick = async (myTimer) => {
        // A tick in flight when close() runs used to land afterwards and
        // paint story art onto the normal chat, then raise the seal modal
        // over a storyless view (finding 4.4). The timer identity IS the
        // liveness token: if _timer moved on, this tick is a ghost.
        if (_timer !== myTimer) { clearInterval(myTimer); return; }
        if (!_root || document.hidden) return;
        if (!document.body.contains(_root)) { clearInterval(myTimer); _timer = null; return; }
        try {
            const s = await api('story/status');
            if (_timer !== myTimer || _root !== root || _session !== sessionName) return;
            const wasActive = !!_status;
            _status = s.active;
            if (!_status && s.last && _story && s.last.slug === _story.slug && !_lastActive) _lastActive = s.last;
            // Story ended by ANY path (her story_end tool included): end()
            // may have restored or kept the cockpit — resync the settings-
            // backed UI so the sidebar never lies (get_self_info-at-none
            // desync, 2026-08-03; class fix, not the button-only patch).
            if (wasActive && !_status) await resyncSettingsUI();
            if (_timer !== myTimer) return;
            paintPanel();
        } catch (e) { /* offline tick */ }
    };
    const myTimer = setInterval(() => tick(myTimer), 12000);
    _timer = myTimer;
    // The bus nudge borrows this closure's tick, so an instant poll obeys
    // exactly the same liveness rules as the scheduled one.
    _tickNow = () => tick(myTimer);
}

export function close() {
    if (_timer) { clearInterval(_timer); _timer = null; }
    _tickNow = null;
    restoreBackdrop();                       // chat's own scene back on the rail
    releaseOrgans(OWNER);                    // organs home BEFORE any other view shows
    _root = null; _story = null; _session = null; _status = null;
    _hintShown = {}; _lastActive = null; _sealPrompted = {}; _sealHeld = {};
    _liveShown = {}; _shownSeq = null;
    document.getElementById('st-lightbox')?.remove();
}

// ------------------------------------------------------------------ layout

function skeleton() {
    const esc = room.esc;
    renderSurface(_root, {
        id: 'story',
        cssClass: 'surface-story',
        mainPane: `
            <div class="gr-stage-bar">
                <button type="button" id="st-back" class="sb-icon-btn" title="Back to Game Room">&#x2190;</button>
                <span class="gr-stage-title">&#x1F4D6; ${_chatSettings.private_chat ? '\u{1F5DD} ' : ''}${esc(_story.title || _story.slug)}</span>
                <span class="gr-stage-info" id="st-stage-info"></span>
            </div>
            <div class="st-stage" id="st-stage">
                <div class="st-rail-slot" id="st-rail-slot"></div>
            </div>`,
        formArea: '',                        // the frame's .form-wrapper IS the composer slot
        sidebarHeader: `
            <div class="sb-chat-header">
                <button type="button" id="st-lib" class="sb-icon-btn" title="Game library">🎲</button>
                <div class="sb-chat-picker" id="st-session-picker">
                    <button class="sb-chat-picker-btn" id="st-session-btn">
                        <span id="st-session-name">${esc(_session)}</span>
                        <span class="sb-chat-arrow">&#x25BE;</span>
                    </button>
                    <div class="sb-chat-picker-dropdown" id="st-session-dropdown"></div>
                </div>
                <button type="button" id="st-sidebar-toggle" class="sb-icon-btn sb-collapse-btn" title="Hide sidebar">&#x25B6;</button>
            </div>
            <div class="sb-chat-actions">
                <button type="button" id="st-new-session" class="sb-icon-btn" title="New playthrough">+</button>
                <button type="button" id="st-del-session" class="sb-icon-btn sb-icon-danger" title="Delete this playthrough (chat + journal position)">&#x1F5D1;</button>
            </div>`,
        sidebarBody: `
            <div class="gr-game-title">&#x1F4D6; ${esc(_story.title || _story.slug)}</div>
            <div class="sidebar-section">
                <div class="st-status" id="st-status"></div>
                <div class="st-ctl-row" id="st-ctl-row"></div>
            </div>
            ${accordionHtml({
                id: 'story:teller', title: 'Storyteller', icon: '\u{1F3AD}',
                content: `
                <div class="sb-field">
                    <label>prompt</label>
                    <div class="sb-field-row">
                        <select id="st-mode"></select>
                        <button type="button" id="st-preview" class="sb-btn-sm" title="Preview the full prompt — exactly what she gets">&#x1F441;</button>
                    </div>
                </div>
                <div class="sb-field" id="st-local-group" style="display:none">
                    <label>local prompt</label>
                    <select id="st-local"></select>
                </div>
                <label class="st-tools-check">
                    <input type="checkbox" id="st-return-check"> return to a specific prompt after
                </label>
                <div class="sb-field" id="st-return-group" style="display:none">
                    <label>after the story</label>
                    <select id="st-return" title="Which prompt returns when the story ends"></select>
                </div>
                <div class="sb-field">
                    <label>toolset</label>
                    <select id="st-toolset"></select>
                </div>
                <label class="st-tools-check">
                    <input type="checkbox" id="st-story-tools"> include story tools
                </label>
                ${coreSections.filter(s => s.bare && !OWN_SECTIONS.includes(s.key)).map(s =>
                    `<div class="gs-content" data-sec="${s.key}"></div>`).join('')}`,
            })}
            ${coreSections.filter(s => !s.bare).map(s => accordionHtml({
                id: 'story:' + s.key, title: s.title, icon: s.icon, open: !!s.open,
                content: `<div class="gs-content" data-sec="${s.key}"></div>`,
            })).join('')}`,
    });

    const $ = (sel) => _root.querySelector(sel);

    const sidebar = $('.chat-sidebar');
    if (localStorage.getItem(SIDEBAR_KEY) === 'collapsed') sidebar.classList.add('collapsed');
    const toggleSb = () => {
        const collapsed = sidebar.classList.toggle('collapsed');
        localStorage.setItem(SIDEBAR_KEY, collapsed ? 'collapsed' : 'expanded');
    };
    $('#st-sidebar-toggle').onclick = toggleSb;
    $('#chat-sidebar-expand').onclick = toggleSb;

    const goBack = () => { const back = _back; close(); if (back) back(); };
    $('#st-back').onclick = goBack;
    $('#st-lib').onclick = goBack;
    $('#st-new-session').onclick = newPlaythroughPrompt;
    $('#st-del-session').onclick = deletePlaythroughPrompt;

    $('#st-session-btn').onclick = async (e) => {
        e.stopPropagation();
        const picker = $('#st-session-picker');
        if (picker.classList.toggle('open')) await fillSessionDropdown();
    };
    bindDocClose();

    $('#st-preview').onclick = previewModal;

    initAccordions($('.chat-sidebar-inner'), 'story-sidebar');
    initPromptBlock();
    initToolsetBlock();
    initSections();
}

// ------------------------------------------------------------------ identity

// The three-mode dropdown (Krem's ruling 2026-08-03): Story = total swap
// into the pack's role; Local = a local prompt inside the story as
// themselves; Combined = local persona wearing the role as a mask.
async function initPromptBlock() {
    const root = _root, session = _session;
    const modeSel = root.querySelector('#st-mode');
    const localSel = root.querySelector('#st-local');
    const localGroup = root.querySelector('#st-local-group');
    const esc = room.esc;

    const hasRole = !!(_story.role);
    const roleLabel = hasRole ? `Story — ${_story.role}` : 'Story (no role in this pack)';
    modeSel.innerHTML = `
        <option value="story"${hasRole ? '' : ' disabled'}>${esc(roleLabel)}</option>
        <option value="local">Local prompt</option>
        <option value="combined"${hasRole ? '' : ' disabled'}>Combined${hasRole ? ` — as ${esc(_story.role)}` : ''}</option>`;

    const init = await getInitData().catch(() => null);
    if (_root !== root || _session !== session) return;
    const prompts = init?.prompts?.list || [];
    const opts = prompts.map(p =>
        `<option value="${esc(p.name)}">${esc(p.name.charAt(0).toUpperCase() + p.name.slice(1))}</option>`).join('');
    localSel.innerHTML = opts;

    // "Return to a specific prompt after" — checkbox reveals the select;
    // unchecked = default behavior (whatever the chat wore pre-story).
    const returnSel = root.querySelector('#st-return');
    const returnCheck = root.querySelector('#st-return-check');
    const returnGroup = root.querySelector('#st-return-group');
    returnSel.innerHTML = opts;
    const explicit = !!(_status && _status.return_prompt);
    returnCheck.checked = explicit;
    returnGroup.style.display = explicit ? '' : 'none';
    const retVal = (_status && (_status.return_prompt || _status.prev_prompt)) || '';
    if (retVal) returnSel.value = retVal;
    const postReturn = async (val) => {
        try {
            const r = await api('story/mode', 'POST', { return_prompt: val });
            if (!r.success) throw new Error(r.detail || 'save failed');
            ui.showToast(r.detail, 'success', 3000);
        } catch (e) { ui.showToast(e.message, 'error'); }
    };
    returnCheck.addEventListener('change', () => {
        returnGroup.style.display = returnCheck.checked ? '' : 'none';
        postReturn(returnCheck.checked ? returnSel.value : '');
    });
    returnSel.addEventListener('change', () => postReturn(returnSel.value));

    const active = () => !!(_status && _status.slug === _story.slug);
    const paint = () => {
        // No running story (ended, or awaiting ▶ Start): identity modes are
        // meaningless, but the PROMPT must stay steerable — she may still be
        // wearing Rose (stay-as-role ending) and the user talks to her here.
        const on = active();
        modeSel.disabled = !on;
        modeSel.title = on ? '' : 'identity modes apply to a running story';
        const lbl = localGroup.querySelector('label');
        if (on) {
            const mode = _status.mode || (hasRole ? 'story' : 'local');
            modeSel.value = mode;
            localGroup.style.display = (mode === 'local' || mode === 'combined') ? '' : 'none';
            if (lbl) lbl.textContent = 'local prompt';
            const local = _status.local || _chatSettings.prompt || '';
            if (local) localSel.value = local;
        } else {
            localGroup.style.display = '';
            if (lbl) lbl.textContent = 'prompt';
            const cur = _chatSettings.prompt || '';
            if (cur && [...localSel.options].some(o => o.value === cur)) localSel.value = cur;
        }
    };
    paint();
    _paintPrompt = paint;   // paintPanel re-syncs this block on status changes

    const postMode = async () => {
        if (!active()) {
            // ended/idle: the select IS the chat's prompt switcher
            try {
                await coreApi.updateChatSettings(session, { prompt: localSel.value });
                _chatSettings.prompt = localSel.value;
                ui.showToast(`Prompt: ${localSel.value}`, 'success', 2500);
            } catch (e) { ui.showToast(e.message, 'error'); }
            return;
        }
        try {
            const body = { mode: modeSel.value };
            if (modeSel.value !== 'story') body.local = localSel.value;
            const r = await api('story/mode', 'POST', body);
            if (!r.success) throw new Error(r.detail || 'mode change failed');
            _status = (await api('story/status')).active;
            paint(); paintPanel();
            ui.showToast(r.detail, 'success', 3500);
        } catch (e) {
            ui.showToast(e.message, 'error');
            paint();
        }
    };
    modeSel.addEventListener('change', postMode);
    localSel.addEventListener('change', postMode);
}

// Toolset dropdown is NORMAL (like chat) with the story-tools checkbox as an
// independent axis — phone + story tools = she can call you mid-ARG.
async function initToolsetBlock() {
    const root = _root, session = _session;
    const sel = root.querySelector('#st-toolset');
    const check = root.querySelector('#st-story-tools');
    const esc = room.esc;

    const init = await getInitData().catch(() => null);
    if (_root !== root || _session !== session) return;
    const sets = (init?.toolsets?.list || []).filter(t => t.type !== 'module');
    sel.innerHTML = sets.map(t =>
        `<option value="${esc(t.name)}">${esc(t.name)} (${t.function_count})</option>`).join('');
    sel.value = _chatSettings.toolset || 'none';
    if (sel.value !== (_chatSettings.toolset || 'none')) sel.value = 'none';
    check.checked = (_chatSettings.extra_toolsets || []).includes(STORY_TOOLS);

    const save = async (patch) => {
        try {
            await coreApi.updateChatSettings(session, patch);
            Object.assign(_chatSettings, patch);
        } catch (e) { ui.showToast(e.message, 'error'); }
    };
    sel.addEventListener('change', () => save({ toolset: sel.value }));
    check.addEventListener('change', () => {
        const extras = (_chatSettings.extra_toolsets || []).filter(x => x !== STORY_TOOLS);
        if (check.checked) extras.push(STORY_TOOLS);
        save({ extra_toolsets: extras });
        if (!check.checked) ui.showToast('Referee off — she can narrate but nothing mechanical can advance.', 'warning', 5000);
    });
}

// The critical feature (Krem 2026-08-03): EXACTLY what she gets. Since
// 2026-08-23 this is core's own assembly (/api/chats/{chat}/prompt-preview):
// persona + custom context + spice + EVERY plugin prompt_inject (surface-
// filtered — avatar, etc.) and the real ghost envelope (story state block
// rides inside it). The old client-side stitch silently omitted plugin
// injections — Krem caught avatar instructions she was getting unseen.
async function previewModal() {
    let pp, data;
    try {
        pp = await import(`./prompt-preview.js?v=${bootV()}`);
        data = await pp.fetchPromptPreview(_session);
    } catch (e) { ui.showToast(e.message, 'error'); return; }
    document.getElementById('st-modal')?.remove();
    const wrap = document.createElement('div');
    wrap.id = 'st-modal';
    wrap.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.6);display:flex;align-items:center;justify-content:center;z-index:10000';
    wrap.innerHTML = `
        <div style="background:var(--bg-primary,#0f1020);color:var(--text,#e1e1e6);padding:20px 24px;border-radius:10px;width:min(860px,92vw);max-height:86vh;overflow:auto;border:1px solid var(--border,#333)">
            <h3 style="margin:0 0 4px">&#x1F441; What she gets — verbatim</h3>
            ${pp.promptPreviewHtml(data)}
            <div style="display:flex;justify-content:flex-end">
                <button class="btn-sm" id="st-modal-close">Close</button>
            </div>
        </div>`;
    wrap.addEventListener('click', e => { if (e.target === wrap) wrap.remove(); });
    wrap.querySelector('#st-modal-close').onclick = () => wrap.remove();
    document.body.appendChild(wrap);
}

let _docCloseBound = false;
function bindDocClose() {
    if (_docCloseBound) return;              // once per page life, not per room
    _docCloseBound = true;
    document.addEventListener('click', () => {
        _root?.querySelector('#st-session-picker')?.classList.remove('open');
    });
}

// Refetch the session's settings and repaint everything that mirrors them
// (toolset select + story-tools checkbox; the prompt block repaints via
// paintPanel). Called on the active→ended transition from any code path.
async function resyncSettingsUI() {
    // Capture at entry: switching playthroughs during the fetch must not
    // write the OLD session's settings into the NEW room (4.10's cousin).
    const root = _root, sess = _session;
    if (!root || !sess) return;
    try {
        const r = await fetch(`/api/chats/${encodeURIComponent(sess)}/settings`,
            { headers: { 'X-CSRF-Token': csrf() } });
        if (!r.ok) return;
        const body = await r.json().catch(() => null);
        if (_root !== root || _session !== sess) return;   // superseded
        _chatSettings = (body && body.settings) || _chatSettings;
        const tsel = _root.querySelector('#st-toolset');
        if (tsel) tsel.value = _chatSettings.toolset || 'none';
        const chk = _root.querySelector('#st-story-tools');
        if (chk) chk.checked = (_chatSettings.extra_toolsets || []).includes(STORY_TOOLS);
    } catch (e) { /* next poll retries */ }
}

// ------------------------------------------------------------------ sessions

async function fillSessionDropdown() {
    const dd = _root?.querySelector('#st-session-dropdown');
    if (!dd) return;
    const esc = room.esc;
    let sessions = [];
    try { sessions = await room.listSessions(); } catch (e) { return; }
    const bySlug = Object.fromEntries(_stories.map(s => [s.slug, s]));
    dd.innerHTML = sessions.map(c => {
        const gid = c.settings?.game_id || '';
        if (!gid.startsWith('story:')) return '';
        const st = bySlug[gid.slice(6)];
        if (!st) return '';                  // its pack is off — dormant
        return `<button class="chat-picker-item${c.name === _session ? ' active' : ''}"
                        data-session="${esc(c.name)}" data-slug="${esc(st.slug)}">
                    <span class="chat-picker-item-check">${c.name === _session ? '✓' : ''}</span>
                    <span class="chat-picker-item-name">&#x1F4D6; ${esc(c.display_name)}</span>
                </button>`;
    }).join('') || '<div class="gr-dd-empty">No playthroughs yet</div>';
    dd.querySelectorAll('[data-session]').forEach(btn => {
        btn.onclick = () => {
            const root = _root, back = _back, stories = _stories;
            const st = stories.find(x => x.slug === btn.dataset.slug);
            if (st) openStoryRoom(root, st, btn.dataset.session, { back, stories });
        };
    });
}

async function newPlaythroughPrompt() {
    const name = prompt('Name for the new playthrough:',
        `${_story.slug} ${new Date().toISOString().slice(0, 10)}`);
    if (!name || !name.trim()) return;
    try {
        const session = await room.createSession({ id: 'story:' + _story.slug }, name);
        const root = _root, back = _back, stories = _stories;
        await openStoryRoom(root, _story, session, { back, stories });
    } catch (e) {
        ui.showToast(e.message.includes('already exists') ? 'A chat with that name already exists.' : e.message, 'error');
    }
}

async function deletePlaythroughPrompt() {
    if (!_session || !_story) return;
    if (!confirm(`Delete playthrough "${_session}"?\n\nRemoves its chat history and its place in "${_story.title || _story.slug}".`)) return;
    const session = _session, back = _back;
    try {
        // End first (this chat is active): restores the prompt and closes the
        // active.json entry — a deleted chat must not leave a ghost story that
        // an identically-named future chat would resume into.
        try { await api('story/end', 'POST'); } catch (e) { /* best-effort */ }
        await room.activateSession('default');
        await coreApi.deleteChat(session);
        close();
        if (back) back();
    } catch (e) {
        ui.showToast(e.message, 'error');
    }
}

// ------------------------------------------------------------------ sections

// Shared per-playthrough sections (brain/mind/voice/sysprompt). Prompt and
// toolset are hand-rolled above: the identity dropdown owns the prompt, the
// toolset needs its story-tools checkbox.
async function initSections() {
    const root = _root, session = _session;
    const ctx = {
        // Getter, not a snapshot: `settings` was bound to the object
        // _chatSettings pointed at when initSections ran, while `save` wrote
        // through the live module reference. After a resync replaced the
        // object, sections read an orphan (finding 4.16).
        get settings() { return _chatSettings; },
        save: async (patch) => {
            try {
                await coreApi.updateChatSettings(session, patch);
                Object.assign(_chatSettings, patch);
            } catch (e) { ui.showToast(e.message, 'error'); }
        },
    };
    for (const s of coreSections) {
        if (OWN_SECTIONS.includes(s.key)) continue;
        if (_root !== root || _session !== session) return;    // superseded
        const el = root.querySelector(`.gs-content[data-sec="${s.key}"]`);
        if (!el) continue;
        el.innerHTML = s.html();
        try { await s.init(el, ctx); } catch (e) { console.warn('[StoryRoom] section failed:', s.key, e); }
    }
}

// ------------------------------------------------------------------ story panel
// Port of web/sidebar.js (the 📖 chat accordion) into the room's sidebar.

function paintPanel() {
    const a = _status;
    const info = _root?.querySelector('#st-stage-info');
    if (info) info.textContent = a ? `turn ${a.turn}${a.paused ? ' · paused' : a.ended ? ' · ended' : ''}` : '';
    // Zork-line moment (open-mansion v1): toast ONCE when the house opens
    // live in this session. Baseline null on entry — resuming an already
    // open house never re-toasts.
    if (a && a.open_flag) {
        if (_houseWasOpen === false && a.house_open) {
            ui.showToast('\u{1F3E0} The house is open — the Rooms panel is live. '
                + 'Stock rooms, load a set, be the ghost. (⚙ → Rooms)', 'success', 8000);
        }
        _houseWasOpen = !!a.house_open;
    }
    paintStatus(a);
    paintControls(a);
    paintBackdrop(a);
    if (_paintPrompt) _paintPrompt();
    checkSeals(a);
    checkShown(a);
}

// The controls line, icon-only: ⏸/▶ · ⏹ · 🔍 · ⚙ (▶ Start when idle).
// Re-rendered per paint, so bindings live here — including the gear.
function paintControls(a) {
    const row = _root?.querySelector('#st-ctl-row');
    if (!row) return;
    const gearHtml = `<button class="btn-sm" id="st-gm-settings" title="Story settings — setup, story text, objects, GM style">&#x2699;&#xFE0E;</button>`;
    const bindGear = () => {
        row.querySelector('#st-gm-settings').onclick = async () => {
            const mod = await import(`./settings-modal.js?v=${bootV()}`);
            // In-game gear: the full modal family — Setup (this run) +
            // Objects tabs join when a playthrough is live (open-mansion v1).
            mod.openStorySettings(_story?.slug, {
                session: _session,
                active: !!(_status && _status.slug === _story?.slug),
                slots: _status?.slots || {},
                state: _status || null,   // read-only State tab (the old 🔍)
                onEndStory: endStory,
                storyTitle: _story?.title || _story?.slug,
            });
        };
    };
    if (!a || a.slug !== _story.slug) {
        row.innerHTML = `<button class="btn-sm st-ctl-wide" id="st-start">&#x25B6; Start Story</button>${gearHtml}`;
        bindGear();
        row.querySelector('#st-start').onclick = async () => {
            try {
                const started = await startWithSetup(_story.slug);
                if (started === null) return;              // player cancelled
                if (!started) throw new Error('start failed');
                _status = (await api('story/status')).active;
                paintPanel();
            } catch (e) { ui.showToast(e.message, 'error'); }
        };
        return;
    }
    row.innerHTML = `
        <button class="btn-sm" id="st-pause" title="${a.paused ? 'Resume — back into the story costume' : 'Pause — intermission; chat returns to your persona (Game Room sidebar setting)'}">${a.paused ? '&#x25B6;' : '&#x23F8;'}</button>
        ${gearHtml}`;
    bindGear();
    row.querySelector('#st-pause').onclick = async () => {
        try {
            const r = await api('story/pause', 'POST', { paused: !a.paused });
            if (r.detail) ui.showToast(r.detail, 'success', 3500);
            _status = (await api('story/status')).active;
            await resyncSettingsUI();   // pause may have swapped the persona
            paintPanel();
        } catch (e) { ui.showToast(e.message, 'error'); }
    };
}

// End the playthrough — lives in the gear's State tab now (Krem 2026-08-21:
// the toolbar keeps only ⏸ and ⚙; ending is a deliberate act, not a button
// next to pause). The gear passes this as opts.onEndStory.
async function endStory() {
    try {
        const r = await api('story/end', 'POST');
        if (r.detail) ui.showToast(r.detail, 'success', 5000);
        const s = await api('story/status');
        _status = s.active;
        if (!_status && s.last && s.last.slug === _story.slug) _lastActive = s.last;
        await resyncSettingsUI();
        paintPanel();
    } catch (e) { ui.showToast(e.message, 'error'); }
}

// Player-facing scene strip — the establishing shot the transcript alone
// doesn't give. Her detailed version rides the ghost block; this is yours:
// title, short desc, exit chips, visible interactables (🧩 = unsolved puzzle,
// ✓ = solved), and turn-gated hints behind a 💡 click — never auto-spoiled.
let _hintShown = {};   // room_id → revealed hint index (survives 12s repaints)
let _sealPrompted = {}; // seal key → auto-popup already shown once
let _sealHeld = {};     // seal key → last seen held count (bump = she reached early)
let _lastActive = null; // last non-null status — lets THE END keep its scene
let _paintPrompt = null; // initPromptBlock's paint(), re-run on status changes

// Per-room backdrop, painted straight onto the transplanted #chatbg (the
// same pipe applyBackground uses: inline image + has-bg scrim class). Pure
// client-side — no settings stamping, so nothing to stash server-side; the
// pre-story look is captured at first paint and restored on close().
let _bdUrl = null, _bdPrev = null;

function paintBackdrop(a) {
    const el = document.getElementById('chatbg');
    if (!el) return;
    if (!a) {
        // Story ended: the finale art lingers — and if the outcome has a
        // dedicated ending card, the STAGE wears it too (the credits shot).
        // Capture-then-paint: on a refresh straight into an ended run,
        // nothing was painted yet, so _bdPrev must be captured HERE or the
        // card never lands (grey-stage-vs-sidebar mismatch, 2026-08-04).
        const card = _lastActive && _lastActive.ending_card;
        if (card) {
            if (_bdPrev === null) _bdPrev = { img: el.style.backgroundImage || '', had: el.classList.contains('has-bg') };
            // The credits shot owns the surface too — without the claim,
            // core's loadSidebar→applyBackground overpainted THE END art
            // ~0.5s after every ended-room entry (post-fix review 2026-08-05).
            claimBackground(OWNER);
            const want = `url("${card}")`;
            if (el.style.backgroundImage !== want) el.style.backgroundImage = want;
            el.classList.add('has-bg');
        }
        return;
    }
    const url = a.room_backdrop || null;
    if (_bdPrev === null) _bdPrev = { img: el.style.backgroundImage || '', had: el.classList.contains('has-bg') };
    if (url) claimBackground(OWNER);            // core defers its paints while we own it
    _bdUrl = url;
    if (url) {
        // Assert against DOM truth EVERY paint, never a memo: core's
        // updateScene repaints #chatbg from chat settings on prompt/settings
        // events and silently wins until the next room change otherwise
        // (sapph-bg-at-the-stern-rail bug, 2026-08-03). The 12s poll makes
        // this self-healing.
        const want = `url("${url}")`;
        if (el.style.backgroundImage !== want) el.style.backgroundImage = want;
        el.classList.add('has-bg');
    } else {
        el.style.backgroundImage = _bdPrev.img;
        el.classList.toggle('has-bg', _bdPrev.had);
        // Backdrop-less room: hand the surface back, or the claim from a
        // prior backdropped room leaks — core's set_scene paints were being
        // swallowed into bgPending until story exit (P0 hunt 2026-08-16).
        releaseBackground(OWNER);
    }
}

function restoreBackdrop() {
    if (_bdPrev !== null) {
        const el = document.getElementById('chatbg');
        if (el) {
            el.style.backgroundImage = _bdPrev.img;
            el.classList.toggle('has-bg', _bdPrev.had);
        }
    }
    // Hand the surface back — any chat-settings paint deferred while we held
    // it lands now (finding 4.3).
    releaseBackground(OWNER);
    _bdPrev = null; _bdUrl = null;
}

// The sidebar status panel — the story's face (Krem's layout 2026-08-03):
// room image, title, short desc, exits, here-chips (🧩 unsolved / ✓ solved),
// carrying, mood + numeric stats (trust, HP...), turn-gated 💡 hints behind
// a click. Replaces both the old accordion rows and the stage-top strip.
function paintStatus(a) {
    const box = _root?.querySelector('#st-status');
    if (!box) return;
    const esc = room.esc;
    if (a) _lastActive = a;
    // The tale closed (story_end) but we're still in the room: the finale
    // panel LINGERS — that art and that room are the whole payoff.
    if (!a && _lastActive) {
        const art = _lastActive.ending_card || _lastActive.room_backdrop;
        const img = art ? `<div class="st-status-img"><img src="${esc(art)}" alt=""></div>` : '';
        box.innerHTML = `${img}
            <div class="st-scene-title">&#x1F4D6; The End — ${esc(_lastActive.story || '')}</div>
            <div class="st-scene-desc">The tale closed at ${esc(_lastActive.room || 'its final scene')}. The journal is sealed; her own prompt is back. Start again for another telling.</div>`;
        box.style.display = '';
        return;
    }
    if (!a) {
        box.innerHTML = `<div class="st-scene-desc" style="color:var(--text-muted)">No active story in this playthrough.</div>`;
        box.style.display = '';
        return;
    }
    const exits = (a.room_exits || []).map((x, i) =>
        `<span class="st-exit st-tap" data-exit="${i}" title="${esc(x.desc)}">${esc(x.label)}</span>`).join('');
    // Section = emoji heading + badge row (Krem 2026-08-21). The door moved
    // from every badge onto the heading — compact badges, one glyph up top.
    const sect = (icon, label, body) => body
        ? `<div class="st-scene-h">${icon} ${label}</div><div class="st-scene-exits">${body}</div>` : '';
    // Icon language (Krem 2026-08-03): ✓ done-something, 🧩 unsolved puzzle,
    // ✋ has do-verbs, 👁 look-only. One glyph carries the affordance.
    const objIcon = o => o.solved || o.used ? '✓ '
        : o.puzzle ? '\u{1F9E9} '
        : (o.verbs || []).some(v => v !== 'look' && v !== 'solve') ? '✋ '
        : '\u{1F441} ';
    const objs = (a.room_objects || []).map((o, i) =>
        `<span class="st-exit st-obj st-tap" data-obj="${i}" title="${esc(o.desc)}">${objIcon(o)}${esc(o.name.replace(/[_-]/g, ' '))}</span>`).join('');
    const carrying = (a.inventory || []).map(i =>
        `<span class="st-exit st-obj">${esc(String(i).replace(/[_-]/g, ' '))}</span>`).join('');
    const stats = [];
    if ((a.emotions || []).length) stats.push(`mood: ${a.emotions.join(', ')}`);
    for (const [k, v] of Object.entries(a.flags || {})) {
        if (typeof v === 'number') stats.push(`${k.replace(/_/g, ' ')}: ${v}`);
    }
    // Sealed blanks: ✍ open (the story is waiting on your words), ✉ filled
    // (sealed — editable until she finds it). Revealed ones leave the row.
    const sealChips = (a.seals || []).map((s, i) => ({ s, i })).filter(x => !x.s.revealed);
    const sealsRow = sealChips.map(x => {
        const open = !x.s.filled && !x.s.skipped;
        return `<span class="st-exit st-obj st-tap" data-seal="${x.i}" title="${esc(open
            ? 'the story left this blank for you — tap to write it'
            : 'sealed — tap to edit before she finds it')}">${open ? '✍' : '✉'} ${esc(pretty(x.s.object))}</span>`;
    }).join('');
    const hints = a.player_hints || [];
    const shownIdx = _hintShown[a.room_id];
    const shown = (shownIdx != null && hints.length) ? hints[Math.min(shownIdx, hints.length - 1)] : null;
    const hintBtn = hints.length && shown == null
        ? `<button type="button" class="st-hint-btn" id="st-hint-btn" title="A nudge, not an answer">\u{1F4A1} hint</button>` : '';
    box.innerHTML = `
        ${a.room_backdrop ? `<div class="st-status-img"><img src="${esc(a.room_backdrop)}" alt=""></div>` : ''}
        <div class="st-scene-title">${esc(a.room)}</div>
        ${a.room_desc ? `<div class="st-scene-desc">${esc(a.room_desc)}</div>` : ''}
        ${sect('\u{1F6AA}', 'Exits', exits)}
        ${sect('\u{1F4CD}', 'Here', objs)}
        ${sect('\u{1F392}', 'Carrying', carrying)}
        ${stats.length ? `<div class="st-scene-exits">${esc(stats.join(' · '))}</div>` : ''}
        ${(a.blockers || []).length ? `<div class="st-scene-exits" style="color:var(--text-muted)">${a.blockers.map(b => {
            const i = b.indexOf(' — ');
            const who = i > 0 ? b.slice(0, i) : b;
            const why = i > 0 ? b.slice(i + 3) : '';
            return `<span${why ? ` title="${esc(why)}"` : ''}>\u{1F512} ${esc(who)}</span>`;
        }).join(' · ')}</div>` : ''}
        ${sealsRow ? `<div class="st-scene-exits">${sealsRow}</div>` : ''}
        ${hintBtn ? `<div class="st-scene-exits">${hintBtn}</div>` : ''}
        ${shown != null ? `<div class="st-hint">\u{1F4A1} ${esc(shown)}${hints.length > 1 && shownIdx < hints.length - 1
            ? ` <button type="button" class="st-hint-btn" id="st-hint-btn" title="Another nudge">more</button>` : ''}</div>` : ''}`;
    box.style.display = '';
    const btn = box.querySelector('#st-hint-btn');
    if (btn) btn.onclick = () => {
        // gentlest hint first; "more" escalates toward the strongest
        _hintShown[a.room_id] = shownIdx == null ? 0 : Math.min(shownIdx + 1, hints.length - 1);
        paintStatus(a);
    };
    // Tap-to-draft: chips open a card (desc + declared verbs); tapping a
    // verb pre-fills the REAL composer — the player edits, flavors, sends.
    // Never auto-sends: the voice stays theirs, she narrates, the referee rules.
    box.querySelectorAll('[data-obj]').forEach(el => el.onclick = () => {
        const o = (a.room_objects || [])[+el.dataset.obj];
        if (o) sceneCard(pretty(o.name), o.desc,
            (o.verbs || []).map(v => ({ label: v, draft: draftVerb(v, o.name) })));
    });
    box.querySelectorAll('[data-exit]').forEach(el => el.onclick = () => {
        const x = (a.room_exits || [])[+el.dataset.exit];
        if (x) sceneCard(x.label, x.desc, [{ label: 'go', draft: `Let's go ${x.label}.` }]);
    });
    box.querySelectorAll('[data-seal]').forEach(el => el.onclick = () => {
        const s = (a.seals || [])[+el.dataset.seal];
        if (s) sealModal(s);
    });
}

// ------------------------------------------------------------------ sealed blanks
// (Krem 2026-08-04) The author leaves a reveal BLANK; the PLAYER writes it
// mid-run in this popup; she discovers it verbatim through the referee. The
// text never passes through her on the way in — the player surprises the
// storyteller. Popup auto-raises on room entry, and again with urgency if
// she reaches for the object before the blank is filled (held-count bump).

function checkSeals(a) {
    const seals = (a && a.seals) || [];
    // Live wait (Krem 2026-08-06): she is blocked inside story_act RIGHT NOW,
    // waiting for this seal. Raise the popup with a countdown, bypassing the
    // prompted-once suppression — but only once per wait (_liveShown), so a
    // deliberate "Later" isn't fought every tick. A modal already open for
    // the same key is upgraded in place (countdown injected), never recreated
    // — recreating would eat the player's typing mid-sentence.
    const live = (a && a.seal_wait) || null;   // {key, remaining}
    const openModal = document.getElementById('st-seal-modal');
    if (live) {
        if (openModal && openModal.dataset.key === live.key) {
            syncCountdown(openModal, live.remaining);
        } else if (!openModal && !_liveShown[live.key]) {
            const s = seals.find(x => x.key === live.key && !x.revealed);
            if (s) {
                _liveShown[live.key] = true;
                _sealHeld[s.key] = s.held || 0;
                _sealPrompted[s.key] = true;
                sealModal(s, true, live);
                return;
            }
        }
        // A DIFFERENT seal's modal is open: leave their typing alone — the
        // wait degrades honestly to the hold, and the ✍ chip still stands.
    } else {
        _liveShown = {};
        if (openModal && openModal.dataset.live === '1') expireCountdown(openModal);
    }
    // Never steal focus from someone mid-sentence, and never mark a blank
    // "prompted" unless its popup actually opened — marking first meant a
    // room's SECOND open blank was recorded as shown while the first held
    // the modal, so it never auto-raised at all (finding 3.3).
    const composing = document.activeElement;
    const busy = !!document.getElementById('st-seal-modal')
        || (composing && /^(TEXTAREA|INPUT)$/.test(composing.tagName) && composing.value);
    for (const s of seals) {
        const heldBump = (s.held || 0) > (_sealHeld[s.key] || 0);
        if (s.revealed || s.filled || s.skipped) { _sealHeld[s.key] = s.held || 0; continue; }
        // Single trigger (Krem 2026-08-06): a popup only ever raises because
        // she reached for the blank — the live countdown when you're here,
        // this urgent catch-up when you weren't. Entry never ambushes; the
        // ✍ chip is the player's volitional early-fill.
        if (!(s.held > 0)) { _sealHeld[s.key] = s.held || 0; continue; }
        if (_sealPrompted[s.key] && !heldBump) { _sealHeld[s.key] = s.held || 0; continue; }
        // busy: leave the bump UNconsumed so the next tick still sees it —
        // recording it here meant an urgent popup suppressed once (composer
        // in use) never auto-raised again (post-fix review 2026-08-05).
        if (busy) continue;
        _sealHeld[s.key] = s.held || 0;
        _sealPrompted[s.key] = true;
        sealModal(s, true);
        return;                                  // one popup at a time
    }
}

// ── countdown (live wait) ──
// wrap._count = {remaining, total, timer}; the 1s tick self-clears when the
// modal leaves the DOM, and each 12s/nudge poll resyncs remaining from the
// server so extensions and drift both heal.

function paintCount(wrap) {
    const c = wrap._count;
    if (!c) return;
    const bar = wrap.querySelector('#st-seal-bar');
    const secs = wrap.querySelector('#st-seal-secs');
    if (bar) bar.style.width = Math.max(0, Math.min(100, (c.remaining / c.total) * 100)) + '%';
    if (secs) secs.textContent = c.remaining + 's';
}

function startCountdown(wrap, remaining) {
    wrap.dataset.live = '1';
    const row = wrap.querySelector('#st-seal-count');
    if (row) row.style.display = 'flex';
    wrap._count = { remaining: Math.max(0, remaining), total: Math.max(remaining, 1) };
    paintCount(wrap);
    wrap._count.timer = setInterval(() => {
        if (!document.body.contains(wrap)) { clearInterval(wrap._count?.timer); return; }
        if (!wrap._count) return;
        wrap._count.remaining -= 1;
        if (wrap._count.remaining <= 0) { expireCountdown(wrap); return; }
        paintCount(wrap);
    }, 1000);
}

function syncCountdown(wrap, remaining) {
    if (!wrap._count) { startCountdown(wrap, remaining); return; }   // upgrade in place
    if (remaining <= 0) { expireCountdown(wrap); return; }
    wrap._count.remaining = remaining;
    wrap._count.total = Math.max(wrap._count.total, remaining);
    paintCount(wrap);
}

function expireCountdown(wrap) {
    if (wrap._count?.timer) clearInterval(wrap._count.timer);
    wrap._count = null;
    wrap.dataset.live = '';
    const row = wrap.querySelector('#st-seal-count');
    if (row) row.innerHTML = `<span class="st-card-note" style="margin:0">the moment passed — she moved on; seal it anyway and she finds it on her next try</span>`;
}

function sealModal(seal, urgent, live) {
    const esc = room.esc;
    document.getElementById('st-seal-modal')?.remove();
    const wrap = document.createElement('div');
    wrap.id = 'st-seal-modal';
    wrap.dataset.key = seal.key;
    wrap.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.6);display:flex;align-items:center;justify-content:center;z-index:10000';
    wrap.innerHTML = `
        <div class="st-card" style="max-width:min(560px,92vw)">
            <div class="st-card-title">&#x270D; The story left this blank for you</div>
            ${urgent ? `<div class="st-card-desc" style="color:var(--warning,#e8b339)">${live ? "She's reaching for it RIGHT NOW — she waits while you write." : "She's reaching for it right now — she holds off until you write it."}</div>` : ''}
            <div class="st-card-desc">${esc(seal.ask || '')}</div>
            <div id="st-seal-count" style="display:none;align-items:center;gap:8px;margin:2px 0 6px">
                <div style="flex:1;height:6px;background:var(--bg-secondary,#161b26);border-radius:3px;overflow:hidden"><div id="st-seal-bar" style="height:100%;width:100%;background:var(--warning,#e8b339);transition:width 1s linear"></div></div>
                <span id="st-seal-secs" style="font-variant-numeric:tabular-nums;color:var(--text-muted,#999);min-width:3.5ch;text-align:right"></span>
                <button type="button" class="btn-sm" id="st-seal-more" title="+60 seconds">&#x23F3; More time</button>
            </div>
            <textarea id="st-seal-text" rows="4" style="width:100%;box-sizing:border-box;margin:8px 0;background:var(--bg-secondary,#161b26);color:var(--text,#e1e1e6);border:1px solid var(--border,#333);border-radius:6px;padding:8px">${esc(seal.text || '')}</textarea>
            <div class="st-card-verbs">
                <button type="button" class="btn-sm" id="st-seal-save">&#x1F512; Seal it in</button>
                ${seal.has_fallback && !seal.filled ? `<button type="button" class="btn-sm" id="st-seal-skip" title="Use the author's line instead">${live ? "Use author's line" : 'Skip'}</button>` : ''}
                <button type="button" class="btn-sm" id="st-seal-later">Later</button>
            </div>
            <div class="st-card-note">what you write never passes through her — she discovers it in play</div>
        </div>`;
    const refresh = async () => {
        try { _status = (await api('story/status')).active; paintPanel(); } catch (e) { /* poll heals */ }
    };
    const post = async (body, failMsg) => {
        try {
            const r = await api('story/fill', 'POST', body);
            if (!r.success) throw new Error(r.detail || failMsg);
            ui.showToast(r.detail, 'success', 3500);
            wrap.remove(); refresh();
        } catch (e) { ui.showToast(e.message, 'error'); }
    };
    wrap.querySelector('#st-seal-save').onclick = () => {
        const text = wrap.querySelector('#st-seal-text').value.trim();
        if (!text) { ui.showToast('Write something — the blank is the whole point.', 'warning'); return; }
        post({ key: seal.key, text }, 'seal failed');
    };
    const skipBtn = wrap.querySelector('#st-seal-skip');
    if (skipBtn) skipBtn.onclick = () => post({ key: seal.key, skip: true }, 'skip failed');
    wrap.querySelector('#st-seal-more').onclick = async () => {
        try {
            const r = await api('story/wait', 'POST', { key: seal.key });
            if (r.success && r.remaining != null) syncCountdown(wrap, r.remaining);
            else expireCountdown(wrap);           // the moment already passed
        } catch (e) { /* poll heals */ }
    };
    wrap.querySelector('#st-seal-later').onclick = () => wrap.remove();
    wrap.addEventListener('click', e => { if (e.target === wrap) wrap.remove(); });
    document.body.appendChild(wrap);
    if (live) startCountdown(wrap, live.remaining);
    wrap.querySelector('#st-seal-text').focus();
}

// ------------------------------------------------------------------ lightbox
// Show-image fx (Krem 2026-08-22): a `show` effect fired server-side — the
// journal folded it, full_state carries the latest as shown_last {url,
// caption, seq}. Raise it exactly once per seq bump. Arrival baselines
// (resume/refresh never replays an old reveal); the seal modal wins the
// screen (their writing > our picture) — the seq stays unconsumed so the
// next tick retries, same law as the seals' busy check.

let _shownSeq = null;   // last seq raised; null = no baseline yet

function checkShown(a) {
    if (!a) return;
    const s = a.shown_last;
    if (!s) { _shownSeq = 0; return; }        // fresh run (or nothing shown)
    if (_shownSeq === null) { _shownSeq = s.seq; return; }   // arrival baseline
    if (s.seq <= _shownSeq) return;
    if (document.getElementById('st-seal-modal')) return;    // retry next tick
    _shownSeq = s.seq;
    lightbox(s);
}

function lightbox(s) {
    const esc = room.esc;
    document.getElementById('st-lightbox')?.remove();
    const wrap = document.createElement('div');
    wrap.id = 'st-lightbox';
    wrap.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.82);display:flex;flex-direction:column;align-items:center;justify-content:center;z-index:10001;cursor:pointer;gap:14px;padding:24px;box-sizing:border-box';
    wrap.innerHTML = `
        <img src="${esc(s.url)}" alt="" style="max-width:min(1100px,94vw);max-height:80vh;border-radius:10px;box-shadow:0 12px 48px rgba(0,0,0,0.6);object-fit:contain">
        ${s.caption ? `<div style="color:#e8e8ee;font-size:1.05em;text-align:center;max-width:min(900px,90vw);text-shadow:0 1px 4px rgba(0,0,0,0.8)">${esc(s.caption)}</div>` : ''}
        <div class="st-card-note" style="color:#aaa">click anywhere to close</div>`;
    wrap.onclick = () => wrap.remove();
    document.body.appendChild(wrap);
}

// ------------------------------------------------------------------ tap-to-draft

const pretty = n => String(n).replace(/[_-]/g, ' ');
const _noArticle = /^(the|a|an|her|his|my|your|their|our|some)\s/i;
const _the = n => (_noArticle.test(n) ? n : 'the ' + n);

function draftVerb(verb, name) {
    const n = pretty(name);
    if (verb === 'look') return `I take a closer look at ${_the(n)}.`;
    if (verb === 'solve') return `My answer for ${_the(n)}: `;
    return `I ${verb} ${_the(n)}.`;
}

function draftToComposer(text) {
    const input = document.getElementById('prompt-input');
    if (!input) return;
    input.value = text;
    input.dispatchEvent(new Event('input', { bubbles: true }));   // autosize
    input.focus();
    try { input.setSelectionRange(input.value.length, input.value.length); } catch (e) { /* ok */ }
}

function sceneCard(title, desc, actions) {
    const esc = room.esc;
    document.getElementById('st-card')?.remove();
    const wrap = document.createElement('div');
    wrap.id = 'st-card';
    wrap.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.45);display:flex;align-items:center;justify-content:center;z-index:10000';
    wrap.innerHTML = `
        <div class="st-card">
            <div class="st-card-title">${esc(title)}</div>
            ${desc ? `<div class="st-card-desc">${esc(desc)}</div>` : ''}
            ${actions.length ? `<div class="st-card-verbs">${actions.map((v, i) =>
                `<button type="button" class="btn-sm" data-act="${i}">${esc(v.label)}</button>`).join('')}</div>` : ''}
            <div class="st-card-note">tap an action to draft it — you edit and send</div>
        </div>`;
    wrap.addEventListener('click', e => { if (e.target === wrap) wrap.remove(); });
    wrap.querySelectorAll('[data-act]').forEach(b => b.onclick = () => {
        draftToComposer(actions[+b.dataset.act].draft);
        wrap.remove();
    });
    document.body.appendChild(wrap);
}

