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
import { showConfirm } from '../../shared/modal.js';
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
let _ticket = 0;            // the newest load; an older answer is dropped
const _open = new Set();    // days unfolded by hand: a refresh keeps them open

export default {
    init(el) { container = el; },
    async show() {
        if (!unsub) unsub = subscribeMindDomain('memory', () => scope, () => container?.offsetParent !== null, () => load(false));
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
    if (d.people.length) meta.push(`<b>people</b> ${d.people.map(n => `<span class="palace-pill">${escHtml(n)}</span>`).join('')}`);
    if (d.dates.length) meta.push(`<b>dates named</b> ${d.dates.map(n => `<span class="palace-pill palace-pill-dim">${escHtml(n)}</span>`).join('')}`);
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
            ${meta.length ? `<div class="palace-meta-row">${meta.join(' ')}</div>` : ''}
        </div>`;
}

// forScope: the scope these days were loaded for. A card acts on that scope,
// never on whatever the sidebar says by the time it is clicked.
function paint(list, forScope) {
    const count = container.querySelector('#pal-daily-count');
    if (count) count.textContent = `${_total} day${_total === 1 ? '' : 's'}${_search ? ' match' : ' in scope'}`;
    const top = list.closest('.view-scroll')?.scrollTop;
    list.innerHTML = `
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
