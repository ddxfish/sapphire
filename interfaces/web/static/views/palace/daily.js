// views/palace/daily.js - the Daily tab: one card per kept day (2026-11-20).
// A day is the summary /compact-daily wrote for it, kept by the palace when
// the chat was compacted (core/plugins/mindpalace/tools/daily.py). It is
// stored in pieces for search; here it is always one whole day: read, marked
// private and deleted as one. Registered by main.js from the palace's layer
// list (view: 'daily'), so the tab and the menu item follow the plugin.
//
// Three rules from the scout hunt (2026-11-20):
// - Pages are fetched by real offset and appended. Asking for a growing
//   limit from 0 hit the route's cap, and day 101 could never be reached.
// - Every load carries a ticket; a response whose ticket is no longer the
//   newest is dropped. An older answer (the scope before, the search before)
//   painted over the newer one, and a click on it acted on the wrong scope.
// - The toolbar is built once. Rebuilding the search box on each paint ate a
//   keystroke typed while a search was in flight.
import { renderSectionHeader, bindSectionHeader } from '../../shared/section-header.js';
import { renderScopeSidebar, bindScopeSidebar } from '../../shared/scope-sidebar.js';
import { listScopes } from '../../shared/scope-api.js';
import { escHtml, escAttr, scopeForChatTab, subscribeMindDomain } from '../../shared/mind-common.js';
import { showConfirm, setupModalClose } from '../../shared/modal.js';
import { fetchWithTimeout } from '../../shared/fetch.js';
import * as ui from '../../ui.js';
import { PALACE_TABS, PLUGIN_LAYERS, refreshPalaceTabs, SCOPE_ENDPOINT, palaceGet, palaceSend, describeScopeForDelete, transferButtons, bindTransfer, rememberMindScope, recallMindScope, writerText } from './common.js';

const LAYER = 'daily';
const PAGE = 20;
const FOLD = 480;           // a day longer than this opens folded

let container = null;
let scope = 'default';
let scopes = [];
let unsub = null;
let _search = '';
let _searchTimer = null;
let _days = [];             // the days on the page, newest first
let _total = 0;
let _clock = null;          // set when the time zone setting and the computer's clock differ (list route)
let _ticket = 0;            // the newest load; an older answer is dropped
const _open = new Set();    // days unfolded by hand: a refresh keeps them open

export default {
    init(el) { container = el; },
    async show() {
        // A refresh while an entity name is being typed on a card would eat it.
        if (!unsub) unsub = subscribeMindDomain('memory', () => scope, () => container?.offsetParent !== null,
            () => { if (!container?.querySelector('.pal-day-linkbox')) load(false); });
        await refreshPalaceTabs();
        if (!PLUGIN_LAYERS.some(l => l.key === LAYER)) {
            // The layer list is the palace's own route: no entry = it is off
            // (or did not load). The menu item outlives a live switch-off.
            if (container) container.innerHTML = `<div class="view-placeholder">
                <h2>\u{1F4C5} Daily</h2>
                <p style="color:var(--text-muted);font-size:var(--font-sm)">Mind Palace is off, so there are no day summaries to show. The days already kept are still stored and return when it is switched on again (Settings &rsaquo; Plugins).</p>
            </div>`;
            return;
        }
        const s = recallMindScope() || await scopeForChatTab('memory_scope');
        if (s) scope = s;
        scopes = await listScopes(SCOPE_ENDPOINT);
        if (scope !== 'default' && !scopes.some(x => x.name === scope)) scope = 'default';
        rememberMindScope(scope);
        // An entity's card opens one of its days here (entities.js).
        try { const q = sessionStorage.getItem('pal-daily-q'); if (q) { _search = q; sessionStorage.removeItem('pal-daily-q'); } } catch {}
        render();
    },
    hide() { if (unsub) { unsub(); unsub = null; } _ticket++; }
};

function render() {
    if (!container) return;
    container.innerHTML = `
        ${renderSectionHeader({ tabs: PALACE_TABS, active: `layer-${LAYER}`, status: '\u{1F4C5} Daily: one summary per finished day, kept when a chat is compacted by day (/compact-daily). She can search and read these; only you can delete one.' })}
        <div class="two-panel">
            ${renderScopeSidebar(scopes, scope)}
            <div class="panel-right">
                <div class="view-body view-scroll">
                    <div class="mind-toolbar">
                        <input type="search" id="pal-daily-search" class="palace-search" placeholder="Search days, or a date like 2026-11" value="${escAttr(_search)}">
                        ${transferButtons()}
                        <button class="mind-btn-sm" id="pal-daily-dry" title="Write one day of a chat as /compact-daily would, and change nothing: the chat and her memory stay as they are. For tuning how she writes a day.">\u{1F9EA} Dry run</button>
                        <span class="palace-count" id="pal-daily-count"></span>
                    </div>
                    <div id="pal-daily-list"></div>
                </div>
            </div>
        </div>`;
    bindSectionHeader(container);
    // A search typed just before the click must not fire into the new scope.
    const move = (s) => { clearTimeout(_searchTimer); scope = s || 'default'; rememberMindScope(scope); _search = ''; };
    bindScopeSidebar(container, {
        describeScope: describeScopeForDelete,
        onScopeChange: (s) => { move(s); render(); },
        onChanged: async (s) => { move(s); scopes = await listScopes(SCOPE_ENDPOINT); render(); },
    });
    const box = container.querySelector('#pal-daily-search');
    box?.addEventListener('input', () => {
        clearTimeout(_searchTimer);
        _searchTimer = setTimeout(() => { _search = box.value.trim(); load(false); }, 300);
    });
    bindTransfer(container.querySelector('.mind-toolbar'), LAYER, () => scope, ui, () => load(false));
    container.querySelector('#pal-daily-dry')?.addEventListener('click', () => dryRun(scope));
    load(false);
}

// more=false: the first page again (a new scope, a new search, a change
// somewhere), keeping as many days on the page as were there. more=true: the
// next page, appended.
async function load(more) {
    const list = container?.querySelector('#pal-daily-list');
    if (!list) return;
    const ticket = ++_ticket, forScope = scope, forSearch = _search;
    const want = more ? PAGE : Math.max(PAGE, _days.length);
    const got = [];
    let total = 0;
    try {
        // The route answers at most 100 days a call: a long page is refetched in parts.
        for (let offset = more ? _days.length : 0; got.length < want;) {
            const qp = new URLSearchParams({ scope: forScope, limit: Math.min(100, want - got.length), offset });
            if (forSearch) qp.set('q', forSearch);
            const data = await palaceGet(`days?${qp}`);
            if (ticket !== _ticket) return;
            total = data.total;
            _clock = data.clock || null;
            if (!(data.days || []).length) break;
            got.push(...data.days);
            offset += data.days.length;
        }
    } catch (e) {
        if (ticket === _ticket) list.innerHTML = `<div class="mind-empty">Failed to load: ${escHtml(e.message)}</div>`;
        return;
    }
    const seen = new Set(), fresh = [];
    for (const d of got) if (!seen.has(d.source)) { seen.add(d.source); fresh.push(d); }   // a long page comes in parts
    if (more) {
        // A day recorded or deleted meanwhile shifts every page: the next page
        // then repeats a day or skips one, and the count no longer adds up
        // (a "Load more (1 older)" that loaded nothing). Reload the whole list.
        if (fresh.some(d => _days.some(x => x.source === d.source)) || total !== _total) {
            _days = _days.concat(fresh.filter(d => !_days.some(x => x.source === d.source)));
            return load(false);
        }
        _days = _days.concat(fresh);
    } else {
        _days = fresh;
    }
    _total = total;
    paint(list, forScope);
}

function dayLabel(iso) {
    const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso || '');
    if (!m) return iso || '';
    return new Date(+m[1], +m[2] - 1, +m[3]).toLocaleDateString(undefined,
        { weekday: 'short', year: 'numeric', month: 'short', day: 'numeric' });
}

function dayCard(d) {
    const long = d.text.length > FOLD, open = _open.has(d.source);
    const chat = d.chat && d.chat !== '__private__' ? `<span class="palace-pill palace-pill-dim" title="The chat this day was compacted in">${escHtml(d.chat)}</span>` : '';
    const persona = d.persona ? `<span class="palace-pill palace-pill-dim" title="The persona whose day this is">${escHtml(d.persona)}</span>` : '';
    const counts = d.messages ? `${d.messages} messages` : '';
    const meta = [];
    // A provider's model id can be a path ("accounts/x/models/glm-5p3"): the name is its last part.
    const wrote = writerText(d.writer && { ...d.writer, model: d.writer.model ? String(d.writer.model).split('/').pop() : d.writer.model });
    if (wrote) meta.push(`<span class="palace-meta-kv">${escHtml(wrote)}</span>`);
    // The person's own corrections (days/tags): a link or a date taken off here
    // stays off, and the day's text is never touched.
    const x = (cls, data, title) => `<button class="${cls}" ${data} title="${title}" style="border:0;background:none;color:inherit;cursor:pointer;padding:0 0 0 5px;opacity:.6;font-size:inherit">✕</button>`;
    meta.push(`<b>entities</b> ${(d.entities || []).map(e => `<span class="palace-pill">${escHtml(e.name)}${x('pal-day-unlink', `data-eid="${e.id}" data-name="${escAttr(e.name)}"`, 'Unlink from this day')}</span>`).join('')}<button class="palace-pill palace-pill-dim pal-day-link" title="Link this day to an entity that already has a card" style="cursor:pointer">+</button>`);
    const off = d.dates_off || [];
    if ((d.dates || []).length || off.length) meta.push(`<b>dates named</b> ${(d.dates || []).map(n => `<span class="palace-pill palace-pill-dim">${escHtml(n)}${x('pal-day-hide', `data-date="${escAttr(n)}"`, 'Take this date off the card')}</span>`).join('')}${off.length ? `<button class="palace-pill palace-pill-dim pal-day-showdates" title="Show the dates taken off this card again" style="cursor:pointer">${off.length} hidden ↺</button>` : ''}`);
    const privTitle = d.private ? "Private models only: kept from any model not marked 'Local / private server' in Settings > LLM, while that filter is on (Mind > Settings). Click to show this day to every model."
        : 'Shown to every model. Click to keep this day for models marked private.';
    return `
        <div class="ui-card palace-chunk" data-source="${escAttr(d.source)}" data-day="${escAttr(d.day)}">
            <button class="ui-card-x pal-day-del" title="Delete this day">✕</button>
            <div class="ui-card-meta" style="margin-top:0;padding-right:26px">
                <b>${escHtml(dayLabel(d.day))}</b>
                ${chat}${persona}
                <span class="ui-meta-text" style="margin-left:auto">${escHtml(counts)}</span>
                <button class="palace-priv pal-day-priv ${d.private ? 'is-priv' : ''}" aria-pressed="${d.private}" title="${privTitle}">\u{1F3E0}</button>
            </div>
            <div class="ui-card-body pal-day-text" style="white-space:pre-wrap;${long && !open ? 'max-height:7.5em;overflow:hidden' : ''}">${escHtml(d.text)}</div>
            ${long ? `<div class="ui-card-actions"><button class="mind-btn-sm pal-day-fold">${open ? 'Fold' : 'Show the whole day'}</button></div>` : ''}
            <div class="palace-meta-row">${meta.join(' ')}</div>
        </div>`;
}

// forScope: the scope these days were loaded for. A card acts on that scope,
// never on whatever the sidebar says by the time it is clicked.
function paint(list, forScope) {
    const count = container.querySelector('#pal-daily-count');
    if (count) count.textContent = `${_total} day${_total === 1 ? '' : 's'}${_search ? ' match' : ' in scope'}`;
    const top = list.closest('.view-scroll')?.scrollTop;
    // Day summaries are cut on the computer's clock, her tasks run on the time
    // zone setting: where they differ nobody could tell from the app.
    const clock = _clock ? `<div class="mind-empty" style="text-align:left;margin-bottom:10px;color:var(--warning,#d9a441)">\u26A0\uFE0F ${escHtml(_clock.words)}</div>` : '';
    list.innerHTML = `${clock}
        ${_days.length
            ? `<div class="palace-chunk-list">${_days.map(dayCard).join('')}</div>`
            : `<div class="mind-empty">${_search ? 'No days match' : 'No day summaries in this scope yet. Type /compact-daily in a chat that uses this scope: each finished day gets a summary, and it is kept here. To do it every night, schedule a task that runs /compact-daily a few minutes after the day-start hour (docs: Slash commands, "Every night"). Keeping them can be switched off in Mind › Settings › Memories.'}</div>`}
        ${_days.length < _total
            ? `<div class="palace-more-wrap"><button class="mind-btn" id="pal-daily-more">Load more (${_total - _days.length} older)</button></div>`
            : ''}`;
    if (top != null) list.closest('.view-scroll').scrollTop = top;
    list.querySelector('#pal-daily-more')?.addEventListener('click', () => load(true));
    list.querySelectorAll('.palace-chunk[data-source]').forEach(card => {
        const source = card.dataset.source;
        card.querySelector('.pal-day-fold')?.addEventListener('click', () => {
            if (_open.has(source)) _open.delete(source); else _open.add(source);
            paint(list, forScope);
        });
        const tag = async (body, done) => {
            try {
                await palaceSend('days/tags', 'POST', { scope: forScope, source, ...body });
                if (done) ui.showToast(done, 'success');
            } catch (e) { ui.showToast(`Could not change it: ${e.message}`, 'error'); }
            load(false);
        };
        card.querySelectorAll('.pal-day-unlink').forEach(b => b.addEventListener('click',
            () => tag({ action: 'unlink', entity: +b.dataset.eid }, `${b.dataset.name} unlinked from this day`)));
        card.querySelectorAll('.pal-day-hide').forEach(b => b.addEventListener('click',
            () => tag({ action: 'hide_date', date: b.dataset.date })));
        card.querySelector('.pal-day-showdates')?.addEventListener('click', () => tag({ action: 'show_dates' }));
        card.querySelector('.pal-day-link')?.addEventListener('click', (ev) => linkBox(ev.currentTarget, forScope,
            (name) => palaceSend('days/tags', 'POST', { scope: forScope, source, action: 'link', name })));
        card.querySelector('.pal-day-priv')?.addEventListener('click', async (ev) => {
            const on = !ev.currentTarget.classList.contains('is-priv');
            try {
                await palaceSend('days/private', 'POST', { scope: forScope, source, private: on });
                ui.showToast(on ? 'Day kept for private models only' : 'Day released to every model', 'success');
            } catch (e) { ui.showToast(`Could not change it: ${e.message}`, 'error'); }
            load(false);
        });
        card.querySelector('.pal-day-del')?.addEventListener('click', () => {
            showConfirm(`Delete the day summary for ${dayLabel(card.dataset.day)} from memory? This can't be undone: it is not saved again and an import does not bring it back. The summary in the chat, if it is still there, stays.`, async () => {
                try {
                    await palaceSend(`days?${new URLSearchParams({ scope: forScope, source })}`, 'DELETE');
                    _open.delete(source);
                    ui.showToast('Day deleted', 'success');
                } catch (e) { ui.showToast(`Delete failed: ${e.message}`, 'error'); }
                load(false);
            });
        });
    });
}

// The "+" on a card becomes a box that takes the name (or a nickname) of an
// entity with a card, from this scope or global. Enter links it; Esc or
// leaving puts the "+" back. Three things the first version got wrong
// (hunt, 2026-11-21): a "+" on a second card was wiped by the first box's own
// reload; a name that was refused took the box and what was typed with it; a
// second Enter sent the link twice.
let _boxes = 0;

async function linkBox(btn, forScope, link) {
    const input = document.createElement('input');
    input.type = 'text';
    input.className = 'palace-search pal-day-linkbox';
    input.placeholder = 'Entity name, then Enter';
    input.style.cssText = 'width:13em;display:inline-block';
    // Its own list of names: one list shared by the page could show another scope's.
    const names = document.createElement('datalist');
    names.id = `pal-day-ents-${++_boxes}`;
    input.setAttribute('list', names.id);
    let busy = false;
    input.addEventListener('keydown', async (e) => {
        if (e.key === 'Escape') { input.value = ''; input.blur(); return; }
        if (e.key !== 'Enter' || busy || !input.value.trim()) return;
        busy = true;
        try {
            await link(input.value.trim());
            ui.showToast('Linked to this day', 'success');
            load(false);
        } catch (err) {
            ui.showToast(`Could not link it: ${err.message}`, 'error');
            busy = false;               // the box and the name stay, to be corrected
            input.select();
        }
    });
    // Leaving the box puts the "+" back, unless a link is on its way, the
    // window only lost focus, or another card's box has just been opened.
    input.addEventListener('blur', () => setTimeout(() => {
        const now = document.activeElement;
        if (busy || now === input || now?.classList?.contains('pal-day-linkbox')) return;
        load(false);
    }, 150));
    btn.replaceWith(input, names);
    input.focus();
    try {
        const all = new Set();
        for (const s of new Set([forScope, 'global'])) {
            (await palaceGet(`entities?scope=${encodeURIComponent(s)}`)).entities.forEach(e => all.add(e.name));
        }
        if (names.isConnected) {
            names.innerHTML = [...all].sort((a, b) => a.localeCompare(b)).map(n => `<option value="${escAttr(n)}"></option>`).join('');
        }
    } catch { /* the box still takes a typed name */ }
}

// ── Dry run ──────────────────────────────────────────────────────────────────
// One day of a chat, written as /compact-daily would write it, with nothing
// changed (POST /api/chats/compress/preview): no backup, no write to the chat,
// nothing kept in memory. The wording in the box is the one tried, saved or
// not, so it can be tuned here; Save makes it the day instructions (the same
// text as Settings > Tools). What was typed, the last result and a run still
// on its way all outlive the dialog: it is closed and opened again while
// reading the days behind it, and a run takes a minute.
const WORDING = '/api/chats/compress/instructions?kind=daily';
const _dry = { chats: {}, day: null, text: null, result: '', running: 0, runs: 0 };
const RUNNING = 'Writing one day. This is a real model call and can take a minute.';
// The Settings > Tools box edits the same text: a save in either drops the other's unsaved copy.
window.addEventListener('daily-wording-saved', () => { _dry.text = null; });

// Whatever dialog is open now shows the run's state: the one that started a
// run may be long closed.
function dryPaint(note) {
    const out = document.querySelector('#pal-dry-out'), run = document.querySelector('#pal-dry-run');
    if (!out) return;
    if (_dry.result) { out.textContent = _dry.result; out.style.color = ''; }
    const say = document.querySelector('#pal-dry-note');
    if (_dry.running) { run.disabled = true; say.textContent = RUNNING; return; }
    run.disabled = !document.querySelector('#pal-dry-day')?.length;
    if (note !== undefined) say.textContent = note;
}

async function dryRun(forScope) {
    document.querySelector('.mind-modal-overlay')?.remove();
    const overlay = document.createElement('div');
    overlay.className = 'pr-modal-overlay mind-modal-overlay';
    overlay.innerHTML = `
        <div class="pr-modal" style="width:min(820px,94vw);max-width:94vw">
            <div class="pr-modal-header">
                <h3>\u{1F9EA} Dry run: one day, nothing changed</h3>
                <button class="mind-btn-sm mind-modal-close">✕</button>
            </div>
            <div class="pr-modal-body">
                <div style="display:flex;flex-wrap:wrap;gap:8px;align-items:center">
                    <select id="pal-dry-chat" class="palace-select" style="max-width:16em" title="The chat whose day is written"></select>
                    <select id="pal-dry-day" class="palace-select" style="max-width:22em" title="The days of that chat not summarized yet"></select>
                    <button class="mind-btn palace-btn-primary" id="pal-dry-run" disabled>Run</button>
                    <span id="pal-dry-note" style="color:var(--text-muted);font-size:var(--font-sm)"></span>
                </div>
                <div style="color:var(--text-muted);font-size:var(--font-sm);margin-top:4px">Only days not summarized yet can be tried: once /compact-daily has written a day, its turns are no longer in the chat.</div>
                <div style="margin-top:10px;font-weight:600">Day summary instructions</div>
                <textarea id="pal-dry-text" rows="5" spellcheck="true" disabled style="width:100%;box-sizing:border-box;height:8em;max-height:18em;resize:vertical;font:inherit;padding:6px 8px;margin-top:4px"></textarea>
                <div style="display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-top:4px">
                    <button class="mind-btn-sm" id="pal-dry-save" disabled title="Make the text in the box the day instructions /compact-daily uses">Save as the day instructions</button>
                    <button class="mind-btn-sm" id="pal-dry-saved" disabled title="Put the saved instructions back in the box">Saved wording</button>
                    <button class="mind-btn-sm" id="pal-dry-default" disabled title="Put the default wording in the box. Nothing changes until you save.">Default wording</button>
                    <span id="pal-dry-state" style="color:var(--text-muted);font-size:var(--font-sm)"></span>
                </div>
                <div id="pal-dry-out" style="margin-top:10px;max-height:38vh;overflow-y:auto;white-space:pre-wrap;border:1px solid var(--border,#333);border-radius:6px;padding:8px 10px;min-height:5em;color:var(--text-muted)">The run uses the text in the box, saved or not, the chat's own model, and the usual size per day. It is one real model call (several for a long day).</div>
            </div>
        </div>`;
    document.body.appendChild(overlay);
    const $ = (id) => overlay.querySelector(id);
    const close = () => overlay.remove();
    $('.mind-modal-close').addEventListener('click', close);
    setupModalClose(overlay, close);
    const chatPick = $('#pal-dry-chat'), dayPick = $('#pal-dry-day'), runBtn = $('#pal-dry-run');
    const note = $('#pal-dry-note'), text = $('#pal-dry-text'), state = $('#pal-dry-state'), out = $('#pal-dry-out');
    let saved = '', fallback = '', broken = '', asked = 0;
    const sync = () => {
        const now = text.value.trim();
        $('#pal-dry-save').disabled = now === saved && !broken;
        $('#pal-dry-saved').disabled = now === saved;
        $('#pal-dry-default').disabled = now === fallback;
        state.textContent = broken ? `The saved file could not be read (${broken}) Saving from here replaces it.`
            : !now ? 'The box is empty: a run uses the saved wording.'
            : now !== saved ? 'Not saved: the run uses the box.' : (saved === fallback ? 'The default.' : 'Saved (edited).');
    };
    dryPaint();

    // The days of the chosen chat that are still raw turns (no model call). An
    // answer for a chat no longer chosen is dropped.
    const days = async () => {
        const mine = ++asked, chat = chatPick.value;
        dayPick.length = 0;
        dayPick.disabled = runBtn.disabled = true;
        if (!chat) return;
        try {
            const r = await fetchWithTimeout(`/api/chats/compress/preview?chat=${encodeURIComponent(chat)}`, {}, 15000);
            if (mine !== asked || !overlay.isConnected) return;
            for (const d of r.days || []) dayPick.add(new Option(`${d.label}${d.finished ? '' : ' (today so far)'} · ${d.messages} messages`, d.day));
            const pick = (r.days || []).find(d => d.day === _dry.day) || (r.days || []).find(d => d.finished) || (r.days || [])[0];
            if (pick) dayPick.value = pick.day;
            dayPick.disabled = !dayPick.length;
            dryPaint(dayPick.length ? '' : 'That chat has no turns from the last days that are not summarized yet.');
        } catch (e) { if (mine === asked) dryPaint(`Could not read that chat: ${e.message}`); }
    };

    // Bound before anything is awaited: a click or a key during the loading counts.
    chatPick.addEventListener('change', () => { _dry.chats[forScope] = chatPick.value; _dry.day = null; days(); });
    dayPick.addEventListener('change', () => { _dry.day = dayPick.value; });
    text.addEventListener('input', () => { _dry.text = text.value; sync(); });
    $('#pal-dry-default').addEventListener('click', () => { text.value = _dry.text = fallback; sync(); });
    $('#pal-dry-saved').addEventListener('click', () => { text.value = saved; _dry.text = null; sync(); });
    $('#pal-dry-save').addEventListener('click', async () => {
        const sent = text.value;
        try {
            const r = await fetchWithTimeout(WORDING, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ instructions: sent }) }, 10000);
            saved = (r.instructions || '').trim();
            broken = '';
            if (text.value === sent) text.value = saved;
            window.dispatchEvent(new CustomEvent('daily-wording-saved'));
            if (text.value !== saved) _dry.text = text.value;      // typed on during the save: kept
            sync();
            ui.showToast('Day summary instructions saved', 'success');
        } catch (e) { ui.showToast(`Not saved: ${e.message}`, 'error'); }
    });
    runBtn.addEventListener('click', async () => {
        if (_dry.running) return;
        const mine = _dry.running = ++_dry.runs;
        const chat = chatPick.value, day = dayPick.value || null;
        _dry.chats[forScope] = chat;
        _dry.day = day;
        dryPaint();
        let said = 'Nothing was changed.';
        try {
            const r = await fetchWithTimeout('/api/chats/compress/preview', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ chat, day, instructions: text.value }),
            }, 600000);
            _dry.result = `${r.label} in "${r.chat}"${r.finished ? '' : ' (today so far)'} · ${r.messages} messages · about ${r.tokens_est} tokens\n\n${r.text}`;
        } catch (e) { said = `Could not run it: ${e.message}`; }
        if (_dry.running === mine) _dry.running = 0;
        dryPaint(said);
        const box = document.querySelector('#pal-dry-out');
        if (box) box.scrollTop = 0;
    });

    try {
        const w = await fetchWithTimeout(WORDING, {}, 10000);
        saved = (w.instructions || '').trim();
        fallback = (w.default || '').trim();
        broken = w.unreadable || '';
        if (!text.value) text.value = _dry.text !== null ? _dry.text : saved;
        text.disabled = false;
        sync();
    } catch (e) { state.textContent = `Could not load the instructions: ${e.message}`; }
    try {
        const r = await fetchWithTimeout('/api/chats?slim=1', {}, 10000);
        // Private chats, games and rooms are not compacted by day, so there is nothing to try on them.
        const ok = (r.chats || []).filter(c => !(c.settings || {}).private_chat && c.kind !== 'game'
            && (c.settings || {}).mode !== 'room');
        const here = ok.filter(c => (c.settings || {}).memory_scope === forScope), rest = ok.filter(c => !here.includes(c));
        const group = (label, list) => {
            if (!list.length) return;
            const g = document.createElement('optgroup');
            g.label = label;
            list.forEach(c => g.appendChild(new Option(c.name, c.name)));
            chatPick.appendChild(g);
        };
        group(`Chats that use the scope "${forScope}"`, here);
        group('Other chats', rest);
        const names = ok.map(c => c.name);
        const want = names.includes(_dry.chats[forScope]) ? _dry.chats[forScope]
            : (here[0]?.name || (names.includes(r.active_chat) ? r.active_chat : names[0]));
        if (want) chatPick.value = want;
        await days();
    } catch (e) { note.textContent = `Could not list the chats: ${e.message}`; }
}
