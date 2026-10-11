// views/rooms.js - Chat > Rooms: one chat, several bots, round robin (core/rooms.py, 2026-10-11).
//
// A room borrows the REAL rail and composer (surface/organs.js), bound by
// name to the room chat: its rows paint through the normal history path
// (display_format splits bubbles per speaker), a seat's live turn streams in
// through the VOICE_TURN events wearing that seat's face and name (ui.js
// startStreaming(who)). Send is intercepted here: a line goes to
// /api/rooms/{chat}/say and lands between seat turns, never as her turn.
// The sidebar is the roster: each bot is an accordion whose body is the
// shared per-chat sections (prompt, toolset, brain; mind as a sub-accordion,
// surface/sections/core-sections.js) pointed at THAT SEAT's settings, plus
// the room-only fields (name, colour, instructions, memory writes). The
// rules live under the gear; the Bots switch and the Go/Stop row stay in the
// sidebar (Krem's votes, 2026-10-11). Record: tmp/chat-room-plan.md.
import { renderSurface } from '../surface/surface.js';
import { claimOrgans, releaseOrgans } from '../surface/organs.js';
import { coreSections } from '../surface/sections/core-sections.js';
import { accordionHtml, initAccordions } from '../shared/accordion.js';
import { showModal, showConfirm, showPrompt, escapeHtml as esc } from '../shared/modal.js';
import { listPersonas, avatarImg } from '../shared/persona-api.js';
import { fetchWithTimeout } from '../shared/fetch.js';
import * as api from '../api.js';
import * as ui from '../ui.js';
import * as eventBus from '../core/event-bus.js';
import { refresh } from '../core/state.js';
import { updateScene } from '../features/scene.js';
import { populateChatDropdown } from '../features/chat-manager.js';

const OWNER = 'rooms';
const LAST_KEY = 'sapphire-room-last';
const SB_KEY = 'sapphire-rooms-sidebar';
const ACC_NS = 'rooms-sidebar';
const SEAT_SECTIONS = ['prompt', 'toolset', 'brain', 'mind'];     // voice and sysprompt wait for per-seat voices
const PLACEHOLDER = 'Say something to the room';
const ECHO_MS = 1500;           // a room_changed this soon after our own save is our echo, not news

let root = null;
let R = null;                   // the open room: {chat, info, placeholder, mute}
let bound = false;              // bus handlers live

const $ = (sel) => root?.querySelector(sel);
const enc = encodeURIComponent;
const csrf = () => document.querySelector('meta[name="csrf-token"]')?.content || '';
const call = (path, method = 'GET', body = undefined) => fetchWithTimeout(path, {
    method, headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf() },
    body: body === undefined ? undefined : JSON.stringify(body),
}, 20000);
const roomUrl = (tail = '') => `/api/rooms/${enc(R.chat)}${tail}`;
const seatOf = (id) => (R?.info?.seats || []).find(s => s.id === id);
const modelShort = (st) => String(st?.llm_model || st?.llm_primary || 'auto').split('/').pop();

// ------------------------------------------------------------------ view

export default {
    init(container) {
        root = container;
        skeleton();
    },
    async show() {
        bindBus();
        await openLast();
    },
    hide() {
        leave();
        unbindBus();
    },
};

function skeleton() {
    renderSurface(root, {
        id: 'rooms', cssClass: 'surface-rooms',
        mainPane: `
            <div class="rm-head">
                <div class="rm-head-row">
                    <span class="rm-title" id="rm-title">Chat Room</span>
                    <span class="rm-turn" id="rm-turn"></span>
                </div>
                <div class="rm-purpose" id="rm-purpose"></div>
            </div>
            <div class="rm-rail-slot" id="rm-rail-slot"><div class="rm-empty" id="rm-empty"></div></div>`,
        formArea: `<div class="rm-composer-slot" id="rm-composer-slot"></div>`,
        sidebarHeader: `
            <div class="sb-chat-header">
                <button type="button" id="rm-settings" class="sb-icon-btn" title="Room settings - purpose and rules">&#x2699;&#xFE0E;</button>
                <div class="sb-chat-picker" id="rm-picker">
                    <button class="sb-chat-picker-btn" id="rm-picker-btn">
                        <span id="rm-picker-name">Chat Room</span>
                        <span class="sb-chat-arrow">&#x25BE;</span>
                    </button>
                    <div class="sb-chat-picker-dropdown" id="rm-picker-dd"></div>
                </div>
                <button type="button" id="rm-sb-toggle" class="sb-icon-btn sb-collapse-btn" title="Hide sidebar">&#x25B6;</button>
            </div>
            <div class="sb-chat-actions">
                <div class="sb-actions-left">
                    <button type="button" id="rm-new" class="sb-icon-btn" title="New room">+</button>
                    <button type="button" id="rm-delete" class="sb-icon-btn sb-icon-danger" title="Delete this room (its chat too)">&#x1F5D1;</button>
                </div>
            </div>`,
        sidebarBody: `
            <div class="rm-body" id="rm-body">
                <div class="sidebar-section rm-quick">
                    <div class="rm-field">
                        <label for="rm-human-every">human every</label>
                        <input type="number" id="rm-human-every" min="0" max="99" title="The bots go this many rounds, then wait for you. 0 = never wait (use Stop, or a round cap under the gear).">
                        <span class="rm-hint">rounds</span>
                    </div>
                    <div class="rm-run">
                        <button type="button" id="rm-go" class="sb-btn-sm" title="One more round without you">&#x25B6; Go</button>
                        <button type="button" id="rm-stop" class="sb-btn-sm" title="Stop now - the bot mid-sentence is cut and its line dropped">&#x23F9; Stop</button>
                        <span class="rm-skip">
                            <input type="number" id="rm-skip-n" min="1" max="99" value="1" title="rounds">
                            <button type="button" id="rm-skip" class="sb-btn-sm" title="Let the bots go this many rounds without you">skip me</button>
                        </span>
                    </div>
                </div>
                <div class="sb-toggles rm-bots-row">
                    <button type="button" class="sb-toggle" id="rm-bots" data-active="true" title="Off: your lines land, nobody answers">Bots enabled</button>
                </div>
                <div class="rm-add">
                    <select id="rm-add-persona" title="A persona to seat in this room"></select>
                    <button type="button" id="rm-add" class="sb-btn-sm">+ Add bot</button>
                </div>
                <div id="rm-roster"></div>
                <div class="rm-guests" id="rm-guests"></div>
            </div>`,
    });

    // sidebar collapse - own preference, chat's CSS
    const sidebar = $('.chat-sidebar');
    if (localStorage.getItem(SB_KEY) === 'collapsed') sidebar.classList.add('collapsed');
    const toggleSb = () => {
        localStorage.setItem(SB_KEY, sidebar.classList.toggle('collapsed') ? 'collapsed' : 'expanded');
    };
    $('#rm-sb-toggle').onclick = toggleSb;
    $('#chat-sidebar-expand').onclick = toggleSb;

    $('#rm-settings').onclick = () => { if (R) openSettings(); };
    $('#rm-new').onclick = newRoom;
    $('#rm-delete').onclick = () => { if (R) deleteRoom(); };
    $('#rm-go').onclick = () => run('/start', {});
    $('#rm-stop').onclick = () => run('/stop', {});
    $('#rm-skip').onclick = () => run('/start', { rounds: Math.max(1, parseInt($('#rm-skip-n').value, 10) || 1) });
    $('#rm-human-every').onchange = (e) => updateRoom({ human_every: Math.max(0, parseInt(e.target.value, 10) || 0) });
    $('#rm-bots').onclick = () => { if (R) updateRoom({ bots_enabled: !R.info.bots_enabled }); };
    $('#rm-add').onclick = addSeat;

    $('#rm-picker-btn').onclick = async (e) => {
        e.stopPropagation();
        const picker = $('#rm-picker');
        if (picker.classList.toggle('open')) await fillPicker();
    };
    document.addEventListener('click', () => $('#rm-picker')?.classList.remove('open'));

    // the roster: one delegate for the seat rows (accordions persist per room:seat id)
    const roster = $('#rm-roster');
    initAccordions(roster, ACC_NS);
    roster.addEventListener('change', onRosterChange);
    roster.addEventListener('click', onRosterClick);
}

// ------------------------------------------------------------------ open / leave

async function openLast() {
    let rooms = [];
    try { rooms = (await call('/api/rooms')).rooms || []; }
    catch (e) { ui.showToast(`Chat Room: ${e.message}`, 'error'); }
    const names = rooms.map(r => r.chat);
    const last = localStorage.getItem(LAST_KEY);
    const pick = (R && names.includes(R.chat)) ? R.chat : (names.includes(last) ? last : names[0]);
    if (!pick) { leave(); paintEmpty('No rooms yet.', 'New room', newRoom); return; }
    if (R && R.chat === pick) { await reload(); return; }
    await open(pick);
}

async function open(chat) {
    leave();
    let info;
    try { info = await call(`/api/rooms/${enc(chat)}`); }
    catch (e) { ui.showToast(`Could not open "${chat}": ${e.message}`, 'error'); paintEmpty('That room would not open.', 'Chat Room', openLast); return; }
    R = { chat, info, placeholder: null, mute: 0 };
    localStorage.setItem(LAST_KEY, chat);
    // Voice follows you into the room: the wake word and the local mic land
    // here as your lines. The server refuses while a turn streams; the room
    // still opens, voice just stays where it was.
    try { await api.activateChat(chat); }
    catch (e) { ui.showToast(`Voice stays on the previous chat: ${e.message}`, 'warning'); }
    if (!R || R.chat !== chat) return;
    claim();
    paintAll();
}

function claim() {
    const ok = claimOrgans({ railSlot: $('#rm-rail-slot'), composerSlot: $('#rm-composer-slot') }, OWNER, R.chat);
    if (!ok) {
        paintEmpty('The chat rail is busy with a live turn.', 'Try again', claim);
        return;
    }
    $('#rm-empty').style.display = 'none';
    bindComposer();
    Promise.allSettled([refresh(false), updateScene(), populateChatDropdown()]).catch(() => {});
    try { ui.forceScrollToBottom(); } catch (e) { /* cosmetic */ }
}

function leave() {
    if (!R) return;
    unbindComposer();
    releaseOrgans(OWNER);
    R = null;
}

async function reload() {
    if (!R) return;
    try { R.info = await call(roomUrl()); }
    catch (e) { return openLast(); }
    paintAll();
}

function paintEmpty(text, label, fn) {
    const el = $('#rm-empty');
    el.style.display = '';
    el.innerHTML = `<p>${esc(text)}</p><button type="button" class="sb-btn-sm" id="rm-empty-btn">${esc(label)}</button>`;
    el.querySelector('#rm-empty-btn').onclick = fn;
    $('#rm-title').textContent = R ? R.chat : 'Chat Room';
    $('#rm-purpose').textContent = '';
    $('#rm-turn').textContent = '';
    $('#rm-body').style.display = R ? '' : 'none';
    $('#rm-picker-name').textContent = R ? R.chat : 'Chat Room';
}

// ------------------------------------------------------------------ the composer: a line, never her turn

function onKey(e) {
    if (e.key === 'Enter' && !e.shiftKey && e.target?.id === 'prompt-input') {
        e.preventDefault(); e.stopPropagation();
        say();
    }
}

function onClick(e) {
    if (e.target?.closest?.('#send-btn')) {
        e.preventDefault(); e.stopPropagation();
        say();
    }
}

function bindComposer() {
    const slot = $('#rm-composer-slot');
    slot.addEventListener('keydown', onKey, true);          // capture: ahead of the chat's own handlers
    slot.addEventListener('click', onClick, true);
    const input = document.getElementById('prompt-input');
    if (input && R) { R.placeholder = input.placeholder; input.placeholder = PLACEHOLDER; }
}

function unbindComposer() {
    const slot = $('#rm-composer-slot');
    slot?.removeEventListener('keydown', onKey, true);
    slot?.removeEventListener('click', onClick, true);
    const input = document.getElementById('prompt-input');
    if (input && R?.placeholder != null) input.placeholder = R.placeholder;
}

async function say() {
    if (!R) return;
    const input = document.getElementById('prompt-input');
    const txt = (input?.value || '').trim();
    if (!txt) return;
    input.value = '';
    input.dispatchEvent(new Event('input'));
    ui.addUserMessage(txt);
    try { await call(roomUrl('/say'), 'POST', { text: txt }); }
    catch (e) { ui.showToast(e.message, 'error'); }
}

// ------------------------------------------------------------------ the bus

function onState(d) {
    if (!R || d?.chat !== R.chat) return;
    R.info.state = d;
    paintTurn();
}

function onChanged(d) {
    if (!R || d?.chat !== R.chat) return;
    if (Date.now() < R.mute) return;                       // our own save's echo
    reload();
}

function bindBus() {
    if (bound) return;
    bound = true;
    eventBus.on('room_state', onState);
    eventBus.on('room_changed', onChanged);
}

function unbindBus() {
    if (!bound) return;
    bound = false;
    eventBus.off('room_state', onState);
    eventBus.off('room_changed', onChanged);
}

// ------------------------------------------------------------------ paint

function paintAll() {
    if (!R) return;
    const info = R.info;
    $('#rm-body').style.display = '';
    $('#rm-title').textContent = `${info.private ? '\u{1F5DD} ' : ''}${info.chat}`;
    $('#rm-picker-name').textContent = info.chat;
    const purpose = $('#rm-purpose');
    purpose.textContent = info.purpose || 'No purpose yet - every bot reads what you write under the gear.';
    purpose.classList.toggle('empty', !info.purpose);
    $('#rm-human-every').value = String(info.human_every ?? 1);
    const bots = $('#rm-bots');
    bots.classList.toggle('active', !!info.bots_enabled);
    bots.dataset.active = info.bots_enabled ? 'true' : 'false';
    bots.textContent = info.bots_enabled ? 'Bots enabled' : 'Bots off';
    paintTurn();
    paintRoster();
    paintGuests();
    fillPersonas();
}

function paintTurn() {
    if (!R) return;
    const st = R.info.state || {};
    const n = (R.info.seats || []).filter(s => s.enabled !== false).length;
    let text;
    if (!R.info.bots_enabled) text = 'Bots are off';
    else if (st.running && st.turn) text = `Round ${st.round} · ${seatOf(st.turn)?.name || 'a bot'} is speaking`;
    else if (st.running && st.waiting) text = `Round ${st.round} · your turn`;
    else if (st.running) text = `Round ${st.round}`;
    else text = n ? `${n} bot${n === 1 ? '' : 's'} · idle` : 'No bots seated';
    $('#rm-turn').textContent = text;
    $('#rm-stop').disabled = !st.running;
    const idle = !R.info.bots_enabled || !n;
    $('#rm-go').disabled = idle;
    $('#rm-skip').disabled = idle;
    root.querySelectorAll('.rm-seat').forEach(el => el.classList.toggle('speaking', !!st.turn && el.dataset.seat === st.turn));
}

function seatHeadHtml(s) {
    const st = s.settings || {};
    const meta = `${modelShort(st)} · ${s.memory_write ? 'mem on' : 'mem off'} · ${st.toolset || 'all'}`;
    return `
        <span class="accordion-arrow">▶</span>
        ${avatarImg(s.persona, s.color || '', 'rm-face', s.avatar)}
        <span class="rm-seat-text">
            <span class="rm-seat-name" style="${s.color ? `color:${esc(s.color)}` : ''}">${esc(s.name)}</span>
            <span class="rm-seat-meta">${esc(meta)}</span>
        </span>
        <label class="rm-seat-on" title="${s.enabled === false ? 'Seated but silent' : 'Takes turns'}">
            <input type="checkbox" data-f="enabled" ${s.enabled === false ? '' : 'checked'}>
        </label>`;
}

function seatHtml(s) {
    return `
        <div class="sidebar-accordion rm-seat" data-acc="room:seat:${esc(s.id)}" data-seat="${esc(s.id)}">
            <div class="sidebar-accordion-header rm-seat-head">${seatHeadHtml(s)}</div>
            <div class="sidebar-accordion-content" style="display:none">
                <div class="sb-field"><label>name</label><input type="text" data-f="name" value="${esc(s.name)}"></div>
                <div class="sb-field"><label>colour</label><input type="color" data-f="color" value="${esc(s.color || '#4a9eff')}"></div>
                <div class="sb-field sb-field-stack">
                    <label>instructions for this room</label>
                    <textarea data-f="context" rows="3" placeholder="Adds to the persona's custom context. e.g. You hold that a hot dog IS a sandwich.">${esc(s.context || '')}</textarea>
                </div>
                <div class="sb-field"><label><input type="checkbox" data-f="memory_write" ${s.memory_write ? 'checked' : ''}> may write to memory, people, goals</label></div>
                <div class="sidebar-section">
                    ${['prompt', 'toolset', 'brain'].map(k => `<div class="gs-content" data-sec="${k}"></div>`).join('')}
                </div>
                ${accordionHtml({ id: `room:seat:${s.id}:mind`, title: '\u{1F9E0} Mind (scopes)', content: '<div class="gs-content" data-sec="mind"></div>' })}
                <button type="button" class="sb-btn-full rm-seat-remove">Remove from the room</button>
            </div>
        </div>`;
}

async function paintRoster() {
    const roster = $('#rm-roster');
    const seats = R.info.seats || [];
    const open = new Set([...roster.querySelectorAll('.rm-seat')].filter(el =>
        el.querySelector('.sidebar-accordion-header')?.classList.contains('open')).map(el => el.dataset.seat));
    roster.innerHTML = seats.map(seatHtml).join('') || '<div class="rm-none">Nobody seated. Add a bot above.</div>';
    initAccordions(roster, ACC_NS);
    for (const id of open) {                                // keep what was open through a repaint
        const el = roster.querySelector(`.rm-seat[data-seat="${CSS.escape(id)}"]`);
        if (!el) continue;
        el.querySelector('.sidebar-accordion-header').classList.add('open');
        el.querySelector('.sidebar-accordion-content').style.display = 'block';
    }
    const me = R;
    for (const seat of seats) {
        const el = roster.querySelector(`.rm-seat[data-seat="${CSS.escape(seat.id)}"]`);
        if (!el) continue;
        const ctx = {
            get settings() { return seat.settings || (seat.settings = {}); },
            save: (patch) => saveSeat(seat.id, { settings: patch }),
        };
        for (const s of coreSections) {
            if (!SEAT_SECTIONS.includes(s.key)) continue;
            const box = el.querySelector(`.gs-content[data-sec="${s.key}"]`);
            if (!box) continue;
            box.innerHTML = s.html();
            try { await s.init(box, ctx); } catch (e) { console.warn('[Rooms] section failed:', s.key, e); }
            if (R !== me) return;                           // the room changed under us
        }
    }
    paintTurn();
}

function paintSeatHead(id) {
    const s = seatOf(id);
    const head = $(`.rm-seat[data-seat="${CSS.escape(id)}"] .rm-seat-head`);
    if (s && head) head.innerHTML = seatHeadHtml(s);
    paintTurn();
}

function paintGuests() {
    const g = R.info.guests || [];
    const who = [R.info.person, ...g.map(x => x.name)].filter(Boolean);
    $('#rm-guests').textContent = who.length ? `also here: ${who.join(' · ')}` : '';
}

async function fillPersonas() {
    const sel = $('#rm-add-persona');
    if (sel.options.length) return;
    try {
        const data = await listPersonas();
        sel.innerHTML = (data.personas || []).map(p => `<option value="${esc(p.name)}">${esc(p.name)}</option>`).join('');
    } catch (e) { sel.innerHTML = '<option value="">no personas</option>'; }
}

async function fillPicker() {
    const dd = $('#rm-picker-dd');
    let rooms = [];
    try { rooms = (await call('/api/rooms')).rooms || []; } catch (e) { return; }
    dd.innerHTML = rooms.map(r => `
        <button class="chat-picker-item${R && r.chat === R.chat ? ' active' : ''}" data-room="${esc(r.chat)}">
            <span class="chat-picker-item-check">${R && r.chat === R.chat ? '✓' : ''}</span>
            <span class="chat-picker-item-name">${esc(r.chat)}${r.state?.running ? ' · live' : ''}</span>
        </button>`).join('') || '<div class="rm-none">No rooms yet</div>';
    dd.querySelectorAll('[data-room]').forEach(b => { b.onclick = () => open(b.dataset.room); });
}

// ------------------------------------------------------------------ edits

async function saveSeat(id, patch) {
    if (!R) return;
    R.mute = Date.now() + ECHO_MS;
    try {
        const fresh = await call(roomUrl(`/seats/${enc(id)}`), 'PUT', patch);
        const cur = seatOf(id);
        if (cur) Object.assign(cur, fresh);
        paintSeatHead(id);
    } catch (e) { ui.showToast(e.message, 'error'); }
}

function onRosterChange(e) {
    const f = e.target?.dataset?.f;
    const id = e.target?.closest?.('.rm-seat')?.dataset.seat;
    if (!f || !id) return;
    const v = e.target.type === 'checkbox' ? e.target.checked : e.target.value;
    saveSeat(id, { [f]: v });
}

function onRosterClick(e) {
    const btn = e.target?.closest?.('.rm-seat-remove');
    if (!btn || !R) return;
    const id = btn.closest('.rm-seat')?.dataset.seat;
    const s = seatOf(id);
    if (!s) return;
    showConfirm(`Remove ${s.name} from this room? Their lines stay in the transcript.`, async () => {
        R.mute = Date.now() + ECHO_MS;
        try { await call(roomUrl(`/seats/${enc(id)}`), 'DELETE'); await reload(); }
        catch (err) { ui.showToast(err.message, 'error'); }
    }, { title: 'Remove bot', saveLabel: 'Remove' });
}

async function addSeat() {
    if (!R) return;
    const persona = $('#rm-add-persona').value;
    if (!persona) return;
    R.mute = Date.now() + ECHO_MS;
    try { await call(roomUrl('/seats'), 'POST', { persona }); await reload(); }
    catch (e) { ui.showToast(e.message, 'error'); }
}

async function updateRoom(patch) {
    if (!R) return;
    R.mute = Date.now() + ECHO_MS;
    try { R.info = await call(roomUrl(), 'PUT', patch); paintAll(); }
    catch (e) { ui.showToast(e.message, 'error'); reload(); }
}

async function run(tail, body) {
    if (!R) return;
    try { R.info.state = await call(roomUrl(tail), 'POST', body); paintTurn(); }
    catch (e) { ui.showToast(e.message, 'error'); }
}

function openSettings() {
    const r = R.info;
    showModal('Room settings', [
        { id: 'purpose', label: 'What this room is for (every bot reads it)', type: 'textarea', value: r.purpose || '', rows: 4 },
        { id: 'human_every', label: 'Require a human every N rounds (0 = never wait)', type: 'number', value: String(r.human_every ?? 1) },
        { id: 'max_rounds', label: 'Stop after N rounds (0 = no cap)', type: 'number', value: String(r.max_rounds ?? 0) },
        { id: 'pause_s', label: 'Pause between bots, seconds', type: 'number', value: String(r.pause_s ?? 0) },
        { id: 'shuffle', label: 'Speaking order', type: 'select', options: ['fixed', 'shuffle'],
          labels: ['Roster order', 'Shuffle every round'], value: r.shuffle ? 'shuffle' : 'fixed' },
    ], (data) => updateRoom({
        purpose: data.purpose, human_every: parseInt(data.human_every, 10) || 0,
        max_rounds: parseInt(data.max_rounds, 10) || 0, pause_s: parseFloat(data.pause_s) || 0,
        shuffle: data.shuffle === 'shuffle',
    }), { saveLabel: 'Save' });
}

async function newRoom() {
    const name = await showPrompt('New room', 'Name');
    if (!name || !name.trim()) return;
    try {
        const info = await call('/api/rooms', 'POST', { name: name.trim() });
        await open(info.chat);
    } catch (e) { ui.showToast(e.message, 'error'); }
}

function deleteRoom() {
    const chat = R.chat;
    showConfirm(`Delete the room "${chat}" and its whole transcript?`, async () => {
        try {
            leave();
            await api.deleteChat(chat);
            localStorage.removeItem(LAST_KEY);
            await openLast();
        } catch (e) { ui.showToast(e.message, 'error'); await openLast(); }
    }, { title: 'Delete room', saveLabel: 'Delete' });
}
