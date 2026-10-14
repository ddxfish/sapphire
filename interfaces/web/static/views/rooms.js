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
// cadence block tops the sidebar (Krem's sketch, 2026-10-13): a heading with
// the gear (purpose, round cap, pause), then one row: turns per round, the
// speaking order, the play button. Record: tmp/chat-room-plan.md.
import { renderSurface } from '../surface/surface.js';
import { claimOrgans, releaseOrgans } from '../surface/organs.js';
import { coreSections } from '../surface/sections/core-sections.js';
import { accordionHtml, initAccordions } from '../shared/accordion.js';
import { showModal, showConfirm, showPrompt, escapeHtml as esc } from '../shared/modal.js';
import { showExportDialog, showImportDialog } from '../shared/import-export.js';
import { listPersonas, avatarImg } from '../shared/persona-api.js';
import { fetchWithTimeout } from '../shared/fetch.js';
import * as api from '../api.js';
import * as ui from '../ui.js';
import * as eventBus from '../core/event-bus.js';
import { refresh, getTtsEnabled } from '../core/state.js';
import * as audio from '../audio.js';
import { updateScene } from '../features/scene.js';
import { populateChatDropdown } from '../features/chat-manager.js';
import * as Images from '../ui-images.js';
import { updateImagePreviewArea } from '../handlers/send-handlers.js';

const OWNER = 'rooms';
const LAST_KEY = 'sapphire-room-last';
const SB_KEY = 'sapphire-rooms-sidebar';
const ACC_NS = 'rooms-sidebar';
const SEAT_SECTIONS = ['prompt', 'toolset', 'brain', 'voice', 'mind'];     // sysprompt waits
const PLACEHOLDER = 'Say something to the room';
const ECHO_MS = 1500;           // a room_changed this soon after our own save is our echo, not news
const INVITE_MS = 330000;       // an invitation waits on her lobby turn over there (the server gives up at 300 s)
const DEVICE = 'device:';       // the Add bot list: a Sapphire device's option value is this + its id
const AT_DOOR = 'door:';        // ...and a persona seated AT the MCP door (answered by whoever holds its key) is this + its name

let root = null;
let R = null;                   // the open room: {chat, info, placeholder, mute}
let bound = false;              // bus handlers live
let epoch = 0;                  // bumped by hide(): an open() still awaiting must not claim the rail into a hidden view
let fillSeq = 0;                // the Add bot list's latest fill
const PENDING = new Map();      // room chat -> {device, label}: an invitation still waiting on her decision

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
        fillPersonas(true);
        await openLast();
    },
    hide() {
        epoch++;
        const was = R?.chat;
        leave();
        unbindBus();
        // voice lands back on the Chat view's chat; the pointer never moved (2026-10-13)
        if (was) api.setLanding(was, false).catch(() => {});
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
                    <button type="button" id="rm-clear" class="sb-icon-btn" title="Clear the transcript - bots, rules and purpose stay">&#x2715;</button>
                    <div class="sb-kebab kebab-menu" id="rm-kebab">
                        <button type="button" class="sb-icon-btn kebab-btn" id="rm-kebab-btn" title="More">&#x22EE;</button>
                        <div class="kebab-dropdown">
                            <button type="button" id="rm-import">Import</button>
                            <button type="button" id="rm-export">Export</button>
                        </div>
                    </div>
                </div>
            </div>`,
        sidebarBody: `
            <div class="rm-body" id="rm-body">
                <div class="sidebar-section rm-quick">
                    <!-- Krem's sketch (2026-10-13): a small heading, then one row - turns, speaking
                         order, play. The turns select maps to the rules: bots off; a number = human
                         every N; "until Stop" = never wait. Stop is the chat bar's Stop while a bot
                         speaks. The gear holds the rest (purpose, round cap, pause). -->
                    <div class="rm-sec-head">
                        <label for="rm-rounds" class="rm-sec-title">Bot turns per round</label>
                        <button type="button" id="rm-settings" class="sb-icon-btn" title="Room settings: purpose, round cap, pause between bots">&#x2699;&#xFE0E;</button>
                    </div>
                    <div class="rm-run">
                        <select id="rm-rounds" title="How many turns each bot takes after a line of yours. Bots off: your lines land, nobody answers. Until Stop: the chat bar's Stop ends it, and the round cap under the gear is the brake.">
                            <option value="off">bots off</option>
                            <option value="1">1 turn</option>
                            <option value="2">2 turns</option>
                            <option value="3">3 turns</option>
                            <option value="5">5 turns</option>
                            <option value="0">until Stop</option>
                        </select>
                        <select id="rm-order" title="Round robin: roster order, each bot reads the lines before hers. Shuffled: a new order every round. Blind round: every bot is cued before any of them answers, so nobody sees the others' lines until the next round.">
                            <option value="fixed">Round robin</option>
                            <option value="shuffle">Shuffled</option>
                            <option value="blind">Blind round</option>
                        </select>
                        <button type="button" id="rm-go" class="sb-btn-sm" title="One round: every bot takes one more turn, without you">&#x25B6;</button>
                    </div>
                </div>
                <div class="rm-sec-head rm-roster-head">
                    <label for="rm-add-persona" class="rm-sec-title">Roster</label>
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
    $('#rm-clear').onclick = () => { if (R) clearRoom(); };
    $('#rm-kebab-btn').onclick = (e) => { e.stopPropagation(); $('#rm-kebab').classList.toggle('open'); };
    $('#rm-import').onclick = () => { $('#rm-kebab').classList.remove('open'); if (R) importRoom(); };
    $('#rm-export').onclick = () => { $('#rm-kebab').classList.remove('open'); if (R) exportRoom(); };
    $('#rm-go').onclick = () => run('/start', {});
    $('#rm-rounds').onchange = (e) => {
        const v = e.target.value;
        updateRoom(v === 'off' ? { bots_enabled: false } : { bots_enabled: true, human_every: Math.max(0, parseInt(v, 10) || 0) });
    };
    $('#rm-order').onchange = (e) => {
        const v = e.target.value;
        updateRoom({ shuffle: v === 'shuffle', blind: v === 'blind' });
    };
    $('#rm-add').onclick = addSeat;
    // a room renamed elsewhere (Chat Manager, another tab): the last-open key follows it
    eventBus.on(eventBus.Events.CHAT_RENAMED, (d) => {
        if (d?.old && d?.new && localStorage.getItem(LAST_KEY) === d.old) localStorage.setItem(LAST_KEY, d.new);
    });

    $('#rm-picker-btn').onclick = async (e) => {
        e.stopPropagation();
        const picker = $('#rm-picker');
        if (picker.classList.toggle('open')) await fillPicker();
    };
    document.addEventListener('click', () => { $('#rm-picker')?.classList.remove('open'); $('#rm-kebab')?.classList.remove('open'); });

    // the roster: one delegate for the seat rows (accordions persist per room:seat id)
    const roster = $('#rm-roster');
    initAccordions(roster, ACC_NS);
    roster.addEventListener('change', onRosterChange);
    roster.addEventListener('click', onRosterClick);
}

// ------------------------------------------------------------------ open / leave

async function openLast() {
    const my = epoch;
    let rooms = [];
    try { rooms = (await call('/api/rooms')).rooms || []; }
    catch (e) { ui.showToast(`Chat Room: ${e.message}`, 'error'); }
    if (my !== epoch) return;                                // the view closed while we fetched
    const names = rooms.map(r => r.chat);
    const last = localStorage.getItem(LAST_KEY);
    const pick = (R && names.includes(R.chat)) ? R.chat : (names.includes(last) ? last : names[0]);
    if (!pick) { leave(); paintEmpty('No rooms yet.', 'New room', newRoom); return; }
    if (R && R.chat === pick) { await reload(); return; }
    await open(pick);
}

async function open(chat) {
    const my = epoch;
    leave();
    let info;
    try { info = await call(`/api/rooms/${enc(chat)}`); }
    catch (e) { ui.showToast(`Could not open "${chat}": ${e.message}`, 'error'); paintEmpty('That room would not open.', 'Chat Room', openLast); return; }
    if (my !== epoch) return;                                // the view closed meanwhile: nothing is claimed
    // voiceGen starts at 0, never undefined: speakLine's liveness check compares it to
    // the generation it captured, and `undefined === 0` kept every line silent until a
    // Stop press had bumped it (opening night, 2026-10-13: nobody ever heard the room)
    R = { chat, info, placeholder: null, mute: 0, voiceGen: 0 };
    localStorage.setItem(LAST_KEY, chat);
    // Voice follows you into the room - the wake word, a satellite, the local mic
    // land here as your lines - without the Chat view's chat moving: the room is the
    // LANDING chat while it is open, never the active one (the one pointer used to
    // play both parts; 2026-10-13). A satellite that spoke is where the bots' lines
    // are read back (core/rooms.py _read_aloud).
    try { await api.setLanding(chat, true); }
    catch (e) { ui.showToast(`Voice stays where it was: ${e.message}`, 'warning'); }
    if (my !== epoch || !R || R.chat !== chat) return;
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
    hush();
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
    } else if (e.target?.closest?.('#stop-btn')) {
        e.preventDefault(); e.stopPropagation();           // the chat's Stop cancels one seat and the round goes on
        hush();                                            // ...and silences the lines queued to be read
        run('/stop');
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
    const images = Images.getImagesForApi();                 // the composer's staged pictures ride the line (2026-10-13)
    if (!txt && !images.length) return;
    input.value = '';
    input.dispatchEvent(new Event('input'));
    ui.addUserMessage(txt, images.length ? Images.getPendingUploadImages() : null);
    if (images.length) { Images.clearPendingUploadImages(); updateImagePreviewArea(); }
    try { await call(roomUrl('/say'), 'POST', images.length ? { text: txt, images } : { text: txt }); }
    catch (e) {
        ui.showToast(e.message, 'error');
        if (input && !input.value) { input.value = txt; input.dispatchEvent(new Event('input')); }   // the words come back
    }
}

// ------------------------------------------------------------------ the bus

function onState(d) {
    if (!R || d?.chat !== R.chat) return;
    const doors = JSON.stringify(R.info.state?.doors || {});
    R.info.state = d;
    if (d.turn) R.passed = null;                           // someone is speaking: the last pass is old news
    paintTurn();
    if (JSON.stringify(d.doors || {}) !== doors)           // someone came to the door, or left it
        (R.info.seats || []).filter(s => s.kind === 'door').forEach(s => paintSeatHead(s.id));
}

// A seat's turn that left no line (she passed, said nothing, hit her model's
// ceiling, was stopped): the bubble goes (main.js), and the strip says so until
// the next seat speaks - a bubble that opened and vanished read as a lost line
// twice (field reports 2026-10-12).
function onTurnEnd(d) {
    if (!R || d?.chat !== R.chat || d?.source !== 'room' || !d?.speaker) return;
    if (d.dropped) {
        R.passed = { name: d.name || seatOf(d.speaker)?.name || 'a bot', why: d.why || '' };
        paintTurn();
        return;
    }
    speakLine(seatOf(d.speaker), d.text, d.heard);
}

// ------------------------------------------------------------------ voices: her line aloud, in order (Krem 2026-10-13)
// The lines queue in the browser where the room is open, one after another, each in the
// seat's own voice within the active engine (speed and pitch too); the next bot never cuts
// the last one off. The round does not wait for the reading: TTS on and muted is the same
// as TTS off. Streaming resolves when the server is done SENDING, not when the last chunk
// has played, so the chain waits for the queue to drain; a provider with no streaming door
// gets the whole line as one blob. Stop and leaving the room hush it all (the generation
// counter drops what was queued). A satellite as the output is a later lane.
function speakLine(seat, text, heard = null) {
    if (heard) return;                                      // read on the satellite the person spoke from (core/rooms.py)
    if (!R || !text || !seat || seat.tts === false || !getTtsEnabled()) return;
    const me = R, st = seat.settings || {}, voice = st.voice || null, gen = me.voiceGen || 0;
    const live = () => R === me && me.voiceGen === gen;
    me.voiceChain = (me.voiceChain || Promise.resolve()).then(async () => {
        if (!live()) return;
        try {
            const ok = await audio.playTextStreaming(text, voice, me.chat, { speed: st.speed, pitch: st.pitch });
            if (ok) {
                for (let i = 0; i < 3000 && live() && audio.isTtsPlaying(); i++) await new Promise(r => setTimeout(r, 100));
                return;
            }
            const res = await fetch('/api/tts/preview', {
                method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf() },
                body: JSON.stringify({ text, chat: me.chat, ...(voice ? { voice } : {}),
                                       ...(st.speed ? { speed: st.speed } : {}), ...(st.pitch ? { pitch: st.pitch } : {}) }),
            });
            if (!res.ok || !live()) return;
            const blob = await res.blob();
            await new Promise((done) => {
                const el = new Audio(URL.createObjectURL(blob));
                me.voiceEl = el;
                el.onended = el.onerror = () => { URL.revokeObjectURL(el.src); if (me.voiceEl === el) me.voiceEl = null; done(); };
                el.play().catch(() => done());
            });
        } catch (e) { console.warn('[Rooms] voice failed:', e); }
    });
}

function hush() {
    audio.stop(true);
    if (!R) return;
    R.voiceGen = (R.voiceGen || 0) + 1;
    R.voiceChain = null;
    if (R.voiceEl) { try { R.voiceEl.pause(); } catch (e) { /* gone */ } R.voiceEl = null; }
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
    eventBus.on('voice_turn_end', onTurnEnd);
}

function unbindBus() {
    if (!bound) return;
    bound = false;
    eventBus.off('room_state', onState);
    eventBus.off('room_changed', onChanged);
    eventBus.off('voice_turn_end', onTurnEnd);
}

// ------------------------------------------------------------------ paint

function paintAll() {
    if (!R) return;
    const info = R.info;
    $('#rm-body').style.display = '';
    $('#rm-title').textContent = `${info.private ? '\u{1F5DD} ' : ''}${info.chat}`;
    $('#rm-picker-name').textContent = info.chat;
    const purpose = $('#rm-purpose');
    purpose.textContent = info.purpose || 'No purpose yet - every bot reads what you write under the sidebar\u2019s gear.';
    purpose.classList.toggle('empty', !info.purpose);
    paintRounds();
    paintTurn();
    paintRoster();
    paintGuests();
    fillPersonas();
}

function paintRounds() {
    const sel = $('#rm-rounds');
    const info = R.info;
    const v = !info.bots_enabled ? 'off' : String(info.human_every ?? 1);
    if (![...sel.options].some(o => o.value === v)) {      // a number the list does not offer (set earlier): shown as it is
        const o = document.createElement('option');
        o.value = v;
        o.textContent = `${v} turns`;
        sel.insertBefore(o, sel.querySelector('option[value="0"]'));
    }
    sel.value = v;
    $('#rm-order').value = info.blind ? 'blind' : info.shuffle ? 'shuffle' : 'fixed';
}

function paintTurn() {
    if (!R) return;
    const st = R.info.state || {};
    const n = (R.info.seats || []).filter(s => s.enabled !== false).length;
    let text;
    const passed = R.passed ? `${R.passed.name} passed` : '';
    if (!R.info.bots_enabled) text = 'Bots are off';
    else if (st.running && st.turn) text = `Round ${st.round} · ${seatOf(st.turn)?.name || 'a bot'} is speaking`;
    else if (st.running && st.waiting) text = `Round ${st.round} · ${passed ? passed + ' · ' : ''}your turn`;
    else if (st.running) text = `Round ${st.round}${passed ? ' · ' + passed : ''}`;
    else text = (passed ? passed + ' · ' : '') + (n ? `${n} bot${n === 1 ? '' : 's'} · idle` : 'No bots seated');
    $('#rm-turn').textContent = text;
    $('#rm-turn').title = R.passed?.why || '';
    $('#rm-go').disabled = !R.info.bots_enabled || !n;
    root.querySelectorAll('.rm-seat').forEach(el => el.classList.toggle('speaking', !!st.turn && el.dataset.seat === st.turn));
}

function seatHeadHtml(s) {
    const st = s.settings || {};
    const guest = s.kind === 'sapphire';
    const meta = (guest ? `another Sapphire · ${s.label || s.device}`
        : s.kind === 'door' ? `at the door · ${R?.info?.state?.doors?.[s.id] ? 'here' : 'away'}`
        : `${modelShort(st)} · ${s.memory_write ? 'mem on' : 'mem off'} · ${st.toolset || 'all'}`)
        + (s.tts === false ? ' · muted' : '');
    return `
        <span class="accordion-arrow">▶</span>
        ${guest ? '<span class="rm-face rm-face-gem">\u{1F48E}</span>' : avatarImg(s.persona, s.color || '', 'rm-face', s.avatar)}
        <span class="rm-seat-text">
            <span class="rm-seat-name" style="${s.color ? `color:${esc(s.color)}` : ''}">${esc(s.name)}</span>
            <span class="rm-seat-meta">${esc(meta)}</span>
        </span>
        <label class="rm-seat-on" title="${s.enabled === false ? 'Seated but silent' : 'Takes turns'}">
            <input type="checkbox" data-f="enabled" ${s.enabled === false ? '' : 'checked'}>
        </label>`;
}

function seatHtml(s) {
    if (s.kind === 'door') return `
        <div class="sidebar-accordion rm-seat rm-door" data-acc="room:seat:${esc(s.id)}" data-seat="${esc(s.id)}">
            <div class="sidebar-accordion-header rm-seat-head">${seatHeadHtml(s)}</div>
            <div class="sidebar-accordion-content" style="display:none">
                <div class="sb-field"><label>name</label><input type="text" data-f="name" value="${esc(s.name)}"></div>
                <div class="sb-field"><label>colour</label><input type="color" data-f="color" value="${esc(s.color || '#4a9eff')}"></div>
                <div class="sb-field sb-field-stack">
                    <label>what you ask of them here</label>
                    <textarea data-f="context" rows="3" placeholder="Goes with every cue they take at the door.">${esc(s.context || '')}</textarea>
                </div>
                <div class="sb-field"><label><input type="checkbox" data-f="tts" ${s.tts === false ? '' : 'checked'}> speaks their lines aloud (TTS)</label></div>
                <div class="sidebar-section"><div class="gs-content" data-sec="voice"></div></div>
                <p class="rm-guest-note">Answered over MCP by whoever holds the API key that speaks as "${esc(s.persona)}": they wait at the door (room_wait) and answer on their turn (room_post). Away, their turn is skipped at once.</p>
                <button type="button" class="sb-btn-full rm-seat-remove">Remove from the room</button>
            </div>
        </div>`;
    if (s.kind === 'sapphire') return `
        <div class="sidebar-accordion rm-seat rm-guest" data-acc="room:seat:${esc(s.id)}" data-seat="${esc(s.id)}">
            <div class="sidebar-accordion-header rm-seat-head">${seatHeadHtml(s)}</div>
            <div class="sidebar-accordion-content" style="display:none">
                <div class="sb-field"><label>name</label><input type="text" data-f="name" value="${esc(s.name)}"></div>
                <div class="sb-field"><label>colour</label><input type="color" data-f="color" value="${esc(s.color || '#4a9eff')}"></div>
                <div class="sb-field sb-field-stack">
                    <label>what you ask of her here</label>
                    <textarea data-f="context" rows="3" placeholder="Goes to her box: the room block in her chat there is re-written when this changes.">${esc(s.context || '')}</textarea>
                </div>
                <div class="sb-field"><label><input type="checkbox" data-f="tts" ${s.tts === false ? '' : 'checked'}> speaks her lines aloud here (TTS, the default voice)</label></div>
                <p class="rm-guest-note">She answers from her own box with her own prompt, model and tools, in her chat "${esc(s.chat || '?')}". Nothing of hers is set here.</p>
                <button type="button" class="sb-btn-full rm-seat-remove">Remove from the room</button>
            </div>
        </div>`;
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
                <div class="sb-field"><label><input type="checkbox" data-f="tts" ${s.tts === false ? '' : 'checked'}> speaks her lines aloud (TTS)</label></div>
                <div class="sidebar-section">
                    ${['prompt', 'toolset', 'brain', 'voice'].map(k => `<div class="gs-content" data-sec="${k}"></div>`).join('')}
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
    const waiting = PENDING.get(R.chat);
    roster.innerHTML = (seats.map(seatHtml).join('') || (waiting ? '' : '<div class="rm-none">Nobody seated. Add a bot above.</div>'))
        + (waiting ? `<div class="rm-pending"><span class="rm-face rm-face-gem">\u{1F48E}</span><span class="rm-seat-text"><span class="rm-seat-name">${esc(waiting.label)}</span><span class="rm-seat-meta">invited · she is deciding</span></span></div>` : '');
    initAccordions(roster, ACC_NS);
    for (const id of open) {                                // keep what was open through a repaint
        const el = roster.querySelector(`.rm-seat[data-seat="${CSS.escape(id)}"]`);
        if (!el) continue;
        el.querySelector('.sidebar-accordion-header').classList.add('open');
        el.querySelector('.sidebar-accordion-content').style.display = 'block';
    }
    const me = R;
    for (const seat of seats) {
        if (seat.kind === 'sapphire') continue;                 // her brain is hers: no sections
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

async function fillPersonas(force = false) {
    const sel = $('#rm-add-persona');
    if (!force && sel.options.length) return;
    const seq = ++fillSeq;
    const was = sel.value;
    let personas = [], hers = [], keyed = [];
    try { personas = (await listPersonas()).personas || []; } catch (e) { /* listed as none */ }
    // another Sapphire: every Another Sapphire device, whatever its state (she answers or she does not)
    try { hers = ((await fetchWithTimeout('/api/devices', {}, 10000)).devices || []).filter(d => d.driver === 'sapphire' && d.enabled !== false); }
    catch (e) { /* no devices page, no guests */ }
    // at the door: every persona some API key speaks as (Settings > System > API Keys) - Claude Code's seat
    try { keyed = [...new Set(((await call('/api/system/api-tokens')).tokens || []).map(t => t.persona).filter(Boolean))]; }
    catch (e) { /* no keys page, no door seats */ }
    if (seq !== fillSeq) return;                              // a newer fill is on its way
    // another Sapphire first (Krem 2026-10-12), the door next, then the personas of this one
    sel.innerHTML = (hers.length ? `<optgroup label="Another Sapphire">${hers.map(d =>
            `<option value="${esc(DEVICE + d.id)}" data-label="${esc(d.label || d.id)}">\u{1F48E} ${esc(d.label || d.id)}</option>`).join('')}</optgroup>` : '')
        + (keyed.length ? `<optgroup label="At the door (over MCP)">${keyed.map(n =>
            `<option value="${esc(AT_DOOR + n)}">\u{1F6AA} ${esc(n)}</option>`).join('')}</optgroup>` : '')
        + `<optgroup label="Personas">${personas.map(p => `<option value="${esc(p.name)}">${esc(p.name)}</option>`).join('') || '<option value="">no personas</option>'}</optgroup>`;
    if (was && [...sel.options].some(o => o.value === was)) sel.value = was;
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
    const sel = $('#rm-add-persona');
    const persona = sel.value;
    if (!persona) return;
    if (persona.startsWith(DEVICE)) return inviteSapphire(persona.slice(DEVICE.length), sel.selectedOptions[0]?.dataset.label || persona.slice(DEVICE.length));
    const body = persona.startsWith(AT_DOOR) ? { persona: persona.slice(AT_DOOR.length), door: true } : { persona };
    R.mute = Date.now() + ECHO_MS;
    try { await call(roomUrl('/seats'), 'POST', body); await reload(); }
    catch (e) { ui.showToast(e.message, 'error'); }
}

function inviteSapphire(device, label) {
    const chat = R.chat;
    if (PENDING.has(chat)) { ui.showToast(`${PENDING.get(chat).label} is still deciding; one invitation at a time.`, 'warning'); return; }
    showModal(`Invite ${label}`, [
        { type: 'html', value: `<p style="margin:0 0 8px;color:var(--text-secondary);font-size:var(--font-sm)">She decides on her own box, with her memory behind her, and picks what she brings: her persona, model and tools. This can take a few minutes; her seat appears when she joins, and what she said shows then. The room's purpose, the names here and these instructions are sent to her.</p>` },
        { id: 'name', label: 'Call her', type: 'text', value: label },
        { id: 'instructions', label: 'What you ask of her in this room', type: 'textarea', rows: 3, value: '',
          placeholder: 'e.g. You hold that a hot dog IS a sandwich.' },
    ], (data) => {
        // nothing returned: the dialog closes now; the wait is a roster row
        PENDING.set(chat, { device, label });
        if (R && R.chat === chat) paintRoster();
        ui.showToast(`Inviting ${label}... she is deciding.`, 'info', 6000);
        fetchWithTimeout(roomUrl('/seats'), {
            method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf() },
            body: JSON.stringify({ device, name: data.name, instructions: data.instructions }),
        }, INVITE_MS).then(async (out) => {
            PENDING.delete(chat);
            const who = out.joined ? (out.seat?.name || label) : label;
            const words = `${who} ${out.joined ? 'joined' : 'declined'} "${chat}". She says: ${out.said || '(nothing)'}`;
            if (!R || R.chat !== chat) { ui.showToast(words, 'info', 12000); return; }
            if (out.joined) await reload(); else paintRoster();
            showConfirm(words, () => {}, { title: out.joined ? 'Joined' : 'Declined', saveLabel: 'OK' });
        }).catch((e) => {
            PENDING.delete(chat);
            if (R && R.chat === chat) paintRoster();
            ui.showToast(`Inviting ${label}: ${e.message}`, 'error');
        });
    }, { saveLabel: 'Invite' });
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
        { id: 'max_rounds', label: 'Round cap: stop after N rounds in one run (0 = no cap; the brake for "until you stop them")', type: 'number', value: String(r.max_rounds ?? 0) },
        { id: 'pause_s', label: 'Pause between bots, seconds', type: 'number', value: String(r.pause_s ?? 0) },
        { id: 'mention_max', label: '@mentions: write @Name in a line and that seat answers next, whoever wrote it; a bot naming another chains. Most mentioned turns in a row since your last line (0 = no cap)', type: 'number', value: String(r.mention_max ?? 3) },
    ], (data) => updateRoom({
        purpose: data.purpose,
        max_rounds: parseInt(data.max_rounds, 10) || 0, pause_s: parseFloat(data.pause_s) || 0,
        mention_max: Math.max(0, parseInt(data.mention_max, 10) || 0),
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

function clearRoom() {
    const chat = R.chat;
    showConfirm(`Clear the transcript of "${chat}"? The bots, their settings, the rules and the purpose stay.`, async () => {
        try {
            R.info.state = await call(roomUrl('/clear'), 'POST', {});
            await refresh(false);
            paintTurn();
        } catch (e) { ui.showToast(e.message, 'error'); }
    }, { title: 'Clear transcript', saveLabel: 'Clear' });
}

async function exportRoom() {
    try {
        const data = await call(`/api/chats/${enc(R.chat)}/export`);
        showExportDialog({ type: 'Chat Room', name: R.chat, data, filename: `${R.chat}.room.json` });
    } catch (e) { ui.showToast(e.message, 'error'); }
}

function importRoom() {
    const chat = R.chat;
    showImportDialog({
        type: `to REPLACE the transcript of the room "${chat}"`,
        overwrites: [{ key: 'room', label: 'Also restore the bots, rules and purpose from the file (a room export)' }],
        existingNames: [],
        validate: (p) => (Array.isArray(p) || Array.isArray(p?.messages)) ? null : 'Not a chat export: no messages array',
        getName: () => chat,
        onImport: async (p, { overwrites }) => {
            const body = { messages: Array.isArray(p) ? p : p.messages };
            const rec = p?.settings?.room || p?.room;
            if (overwrites.room && rec && typeof rec === 'object') body.room = rec;
            if (!R || R.chat !== chat) throw new Error('The room changed while the file was open.');
            R.mute = Date.now() + ECHO_MS;
            R.info = await call(roomUrl('/import'), 'POST', body);
        },
        onDone: async () => { if (R && R.chat === chat) { paintAll(); await refresh(false); } },
    });
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
