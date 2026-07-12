// views/palace/self.js - Mind › Self, palace edition (L0: the self layer).
// The self-sheet as section cards: autosave textareas for the typed sections,
// a key-value editor for handles, custom boxes with +add, version history for
// the versioned sections (identity/values/projects), and the live dashboard —
// computed from mind.db at read time, read-only by construction.
import { renderSectionHeader, bindSectionHeader } from '../../shared/section-header.js';
import { helpPills } from '../../features/video-link.js';
import { renderScopeSidebar, bindScopeSidebar } from '../../shared/scope-sidebar.js';
import { listScopes } from '../../shared/scope-api.js';
import { escHtml, escAttr, timeAgo, scopeForChatTab, subscribeMindDomain } from '../../shared/mind-common.js';
import { setupModalClose } from '../../shared/modal.js';
import * as ui from '../../ui.js';
import { PALACE_TABS, SCOPE_ENDPOINT, palaceGet, palaceSend, describeScopeForDelete, transferButtons, bindTransfer } from './common.js';

const SCOPE_KEY = 'memory_scope';
const MAX_CHARS = 2000;
const MODE_CHIPS = {
    'hand': { icon: '✍️', tip: 'Hand-authored — you and Sapphire edit this' },
    'librarian-regen': { icon: '\u{1F319}', tip: 'The librarian will rewrite this nightly (v1: hand-authored)' },
    'computed': { icon: '⚙️', tip: 'Computed — read-only' },
};

let container = null;
let scope = 'default';
let scopes = [];
let unsub = null;
let _saveTimers = {};
let _localBoxes = [];   // client-side boxes not yet persisted

export default {
    init(el) { container = el; },
    async show() {
        // Editor semantics: skip SSE refresh while ANY self card holds focus
        // (buttons included — a re-render mid-edit wipes unsaved input) or a
        // save is still pending (the 'conse' truncation class, 2026-07-12).
        if (!unsub) unsub = subscribeMindDomain('memory', () => scope,
            () => container?.offsetParent !== null
                && !container.querySelector('.palace-self-card:focus-within')
                && !Object.keys(_saveTimers).length,
            renderSheet);
        if (window._mindScope) { scope = window._mindScope; delete window._mindScope; }
        else { const s = await scopeForChatTab(SCOPE_KEY); if (s) scope = s; }
        delete window._mindTab;
        scopes = await listScopes(SCOPE_ENDPOINT);
        render();
    },
    hide() { if (unsub) { unsub(); unsub = null; } }
};

function content() { return container?.querySelector('#pal-self-content'); }

function render() {
    if (!container) return;
    container.innerHTML = `
        ${renderSectionHeader({ tabs: PALACE_TABS, active: 'self', help: helpPills('Self', { doc: 'MEMORY.md', inline: true }), status: '\u{1F48D} Mind Palace — the self sheet (L0). Who she is, in her own words. Sections autosave; prior identity/values/projects versions are archived, never lost.' })}
        <div class="two-panel">
            ${renderScopeSidebar(scopes, scope)}
            <div class="panel-right">
                <div class="view-body view-scroll" id="pal-self-content"></div>
            </div>
        </div>`;
    bindSectionHeader(container);
    bindScopeSidebar(container, {
        describeScope: describeScopeForDelete,
        onScopeChange: (s) => { scope = s; _localBoxes = []; render(); },
        onChanged: async (s) => { scope = s || 'default'; _localBoxes = []; scopes = await listScopes(SCOPE_ENDPOINT); render(); },
    });
    renderSheet();
}

async function renderSheet() {
    const el = content();
    if (!el) return;
    // Pending saves fire from the still-attached DOM and land BEFORE we read
    // server state — a stale timer surviving into a re-render used to save
    // from a detached card and truncate mid-word ('consent' → 'conse').
    await flushAllSaves();
    let data;
    try {
        data = await palaceGet(`self?scope=${encodeURIComponent(scope)}`);
    } catch (e) {
        el.innerHTML = `<div class="mind-empty">Failed to load: ${escHtml(e.message)}</div>`;
        return;
    }
    const persisted = new Set(data.custom.map(c => c.section));
    _localBoxes = _localBoxes.filter(b => !persisted.has(b.section));

    const customCard = (c) => {
        const base = { ...c, title: `[${c.section}]`, mode: 'hand', versioned: false, custom: true,
                       hint: c.fields ? 'custom list' : 'custom box' };
        return c.fields ? structCard(base) : sectionCard(base);
    };
    const localCard = (b) => b.fields
        ? structCard({ section: b.section, title: `[${b.section}]`, hint: 'custom list — saves when you add rows',
                       mode: 'hand', custom: true, fields: b.fields, rows: [], width: 'half', history_count: 0 })
        : sectionCard({ section: b.section, title: `[${b.section}]`, hint: 'custom box — saves when you write',
                        mode: 'hand', custom: true, content: '', width: 'third', history_count: 0 });

    el.innerHTML = `
        ${dashboardCard(data.dashboard)}
        <div class="palace-self-grid">
            ${data.sections.map(s => s.fields ? structCard(s) : sectionCard(s)).join('')}
            ${data.custom.map(customCard).join('')}
            ${_localBoxes.map(localCard).join('')}
        </div>
        <div class="palace-more-wrap">
            <button class="mind-btn" id="pal-self-addbox">+ Add box</button>
            ${transferButtons()}
        </div>
        <div id="pal-ledger"></div>
    `;
    bindCards(el);
    bindLibrarian(el);
    bindTransfer(el, 'self', () => scope, ui, renderSheet);
    renderLedger(el);
}

// ─── The Ledger (v1) — append-only change stream, bottom of the sheet ───────
// Collapsed by default: header counts + the last 3 lines. Expanded: the full
// stream, unread divider at her last read, pass rows unfold their children.
// Read-only surface — every row is written by the backend seams.

const LEDGER_ICONS = { user: '\u{1F464}', ai: '\u{1F916}', librarian: '\u{1F9F9}', import: '\u{1F4E6}', system: '⚙️' };
let _ledgerOpen = false;
let _ledgerLimit = 30;

function ledgerLine(r) {
    return `<div class="palace-ledger-row">
        <span class="palace-ledger-ts">${escHtml((r.ts || '').slice(0, 10))}</span>
        <span class="palace-ledger-actor" title="${escAttr(r.actor)}">${LEDGER_ICONS[r.actor] || '·'}</span>
        <span class="palace-ledger-sum">${escHtml(r.summary)}</span>
        ${r.children ? `<button class="mind-btn-sm palace-ledger-kids" data-id="${r.id}" data-n="${r.children}">▸ ${r.children}</button>` : ''}
    </div>`;
}

async function renderLedger(el) {
    const box = el.querySelector('#pal-ledger');
    if (!box) return;
    let data;
    try {
        data = await palaceGet(`ledger?scope=${encodeURIComponent(scope)}&limit=${_ledgerOpen ? _ledgerLimit : 3}`);
    } catch { box.innerHTML = ''; return; }
    const rows = data.rows || [];
    const lines = [];
    let divided = false;
    for (const r of rows) {
        if (_ledgerOpen && !divided && data.last_read_ts && r.ts <= data.last_read_ts && lines.length) {
            lines.push('<div class="palace-ledger-divider">— she has read to here —</div>');
            divided = true;
        }
        lines.push(ledgerLine(r));
    }
    box.innerHTML = `
        <div class="mind-mem-card palace-ledger">
            <div class="palace-self-card-head">
                <span class="palace-self-title">\u{1F4D2} Ledger</span>
                <span class="palace-self-hint">${data.week} this week · ${data.unread} since her last read</span>
                <button class="mind-btn-sm" id="pal-ledger-toggle">${_ledgerOpen ? '▾ collapse' : '▸ expand'}</button>
            </div>
            <div class="palace-self-hint">Every change to her memory, by whoever made it — append-only, read-only.</div>
            ${rows.length ? lines.join('') : '<div class="mind-empty">Nothing recorded yet — changes land here from now on.</div>'}
            ${(_ledgerOpen && rows.length < data.total)
                ? `<div class="palace-more-wrap"><button class="mind-btn-sm" id="pal-ledger-more">Load more (${data.total - rows.length} older)</button></div>`
                : ''}
        </div>`;
    box.querySelector('#pal-ledger-toggle')?.addEventListener('click', () => {
        _ledgerOpen = !_ledgerOpen;
        renderLedger(el);
    });
    box.querySelector('#pal-ledger-more')?.addEventListener('click', () => {
        _ledgerLimit += 30;
        renderLedger(el);
    });
    box.querySelectorAll('.palace-ledger-kids').forEach(btn => {
        btn.addEventListener('click', async () => {
            const row = btn.closest('.palace-ledger-row');
            const open = row.nextElementSibling?.classList.contains('palace-ledger-children');
            if (open) { row.nextElementSibling.remove(); btn.textContent = `▸ ${btn.dataset.n}`; return; }
            try {
                const d = await palaceGet(`ledger?scope=${encodeURIComponent(scope)}&parent_id=${btn.dataset.id}`);
                row.insertAdjacentHTML('afterend',
                    `<div class="palace-ledger-children">${(d.rows || []).map(ledgerLine).join('')}</div>`);
                btn.textContent = `▾ ${btn.dataset.n}`;
            } catch (e) { ui.showToast(`Ledger children failed: ${e.message}`, 'error'); }
        });
    });
}

function dashboardCard(d) {
    const stat = (n, label) => `<div class="palace-dash-stat"><div class="palace-dash-n">${n}</div><div class="palace-dash-l">${label}</div></div>`;
    return `
        <div class="palace-self-dash">
            <div class="palace-self-card-head">
                <span class="palace-self-title">⚙️ Dashboard</span>
                <span class="palace-self-hint">computed live — she reads, nothing writes</span>
            </div>
            <div class="palace-dash-grid">
                ${stat(d.events, `memories <span class="palace-dash-sub">+${d.events_7d} wk · +${d.events_30d} mo</span>`)}
                ${stat(d.per_day_30d, 'per day (30d)')}
                ${stat(d.entities, 'entities')}
                ${stat(d.knowledge, 'knowledge')}
                ${stat(d.edges, 'connections')}
                ${stat(d.favorites, 'favorites')}
            </div>
            ${d.most_woven.length ? `<div class="palace-dash-woven">Most woven: ${d.most_woven.map(w => `<span class="palace-pill">${escHtml(w.name)} <b>${w.count}</b></span>`).join('')}</div>` : ''}
            ${d.since ? `<div class="palace-dash-since">Mind since ${escHtml(d.since)}</div>` : ''}
            <div class="palace-librarian-row">
                <span class="palace-lib-title">\u{1F9F9} Librarian</span>
                <span class="palace-lib-status" id="pal-lib-status">checking…</span>
                <button class="mind-btn-sm" id="pal-lib-run-self" title="Review unprocessed Self-layer memories in this scope">Tidy Self</button>
                <button class="mind-btn-sm" id="pal-lib-run-all" title="Review all unprocessed memories in this scope (events + self)">Tidy whole scope</button>
            </div>
        </div>`;
}

let _libTimer = null;

async function refreshLibStatus(el, { poll = false } = {}) {
    const box = el.querySelector('#pal-lib-status');
    if (!box) return;
    let st;
    try {
        st = await palaceGet(`librarian/status?scope=${encodeURIComponent(scope)}`);
    } catch (e) { box.textContent = 'status unavailable'; return; }
    clearTimeout(_libTimer);
    if (st.running) {
        const c = st.current;
        box.textContent = `running (${c.scope}): message ${c.messages_done}/${c.messages_total || '?'}`;
        _libTimer = setTimeout(() => refreshLibStatus(el, { poll: true }), 3000);
    } else if (poll) {
        ui.showToast(st.current?.last_message || 'Librarian pass finished', 'success');
        renderSheet();
    } else {
        const mine = (st.scopes || []).find(s => s.scope === scope);
        box.textContent = mine
            ? `last pass ${timeAgo(mine.last_pass)} · ${mine.passes_today} today`
            : 'never run in this scope';
    }
}

function bindLibrarian(el) {
    const run = (what) => async () => {
        try {
            const r = await palaceSend('librarian/run', 'POST', { scope, what });
            ui.showToast(r.message || 'Pass started', 'success');
            refreshLibStatus(el);
        } catch (e) { ui.showToast(e.message, 'error'); }
    };
    el.querySelector('#pal-lib-run-self')?.addEventListener('click', run('self'));
    el.querySelector('#pal-lib-run-all')?.addEventListener('click', run('all'));
    refreshLibStatus(el);
}

function modeChip(mode) {
    const m = MODE_CHIPS[mode] || MODE_CHIPS.hand;
    return `<span class="palace-mode-chip" title="${escAttr(m.tip)}">${m.icon}</span>`;
}

function cardHead(s) {
    return `
            <div class="palace-self-card-head">
                <span class="palace-self-title">${escHtml(s.title)}</span>
                ${modeChip(s.mode)}
                <span class="palace-self-saved" hidden>✓ saved</span>
                ${s.history_count ? `<button class="mind-btn-sm palace-self-hist" data-section="${escAttr(s.section)}" title="Archived versions">\u{1F4DC} ${s.history_count}</button>` : ''}
                ${s.custom ? `<button class="mind-btn-sm palace-self-delbox" data-section="${escAttr(s.section)}" title="Remove box">✕</button>` : ''}
            </div>
            <div class="palace-self-hint">${escHtml(s.hint)}${s.updated ? ` · ${escHtml(timeAgo(s.updated))}` : ''}</div>`;
}

function sectionCard(s) {
    return `
        <div class="mind-mem-card palace-self-card pal-w-${escAttr(s.width || 'third')}" data-section="${escAttr(s.section)}">
            ${cardHead(s)}
            <textarea class="palace-self-text" maxlength="${MAX_CHARS}" placeholder="${escAttr(s.hint)}">${escHtml(s.content)}</textarea>
        </div>`;
}

// The structured-list editor — the Handles "+add" pattern, generalized to any
// section/box with a fields spec (1–3 columns). Rows live in meta.rows server-
// side; the canonical text stays the spider/embedding surface.
function rowGridStyle(n) {
    const cols = n === 1 ? '1fr' : n === 2 ? 'minmax(90px, 34%) 1fr' : 'minmax(80px, 26%) 1fr 1fr';
    return `grid-template-columns: ${cols} auto;`;
}

function structRow(fields, row = {}) {
    return `<div class="palace-row" style="${rowGridStyle(fields.length)}">
        ${fields.map(f => `<input type="text" data-k="${escAttr(f.key)}" placeholder="${escAttr(f.label)}" value="${escAttr(row[f.key] || '')}">`).join('')}
        <button class="mind-btn-sm palace-row-del" title="Remove">✕</button>
    </div>`;
}

function structCard(s) {
    const fields = s.fields || [];
    const rows = s.rows?.length ? s.rows : [];
    const atMax = s.max_rows && rows.length >= s.max_rows;
    return `
        <div class="mind-mem-card palace-self-card palace-self-struct pal-w-${escAttr(s.width || 'half')}"
             data-section="${escAttr(s.section)}" data-spec="${escAttr(JSON.stringify(fields))}"
             ${s.max_rows ? `data-max="${s.max_rows}"` : ''} ${s.custom ? 'data-custom="1"' : ''}>
            ${cardHead(s)}
            ${fields.length > 1 ? `<div class="palace-row palace-row-head" style="${rowGridStyle(fields.length)}">${fields.map(f => `<span>${escHtml(f.label)}</span>`).join('')}<span></span></div>` : ''}
            <div class="palace-row-list">
                ${rows.map(r => structRow(fields, r)).join('')}
            </div>
            <button class="mind-btn-sm palace-row-add" ${atMax ? 'hidden' : ''}>+ add</button>
        </div>`;
}

function flashSaved(card) {
    const chip = card.querySelector('.palace-self-saved');
    if (!chip) return;
    chip.hidden = false;
    clearTimeout(chip._t);
    chip._t = setTimeout(() => { chip.hidden = true; }, 1500);
}

async function saveSection(card, section, body, scopeAt = scope) {
    try {
        await palaceSend(`self/${encodeURIComponent(section)}`, 'PUT', { ...body, scope: scopeAt });
        flashSaved(card);
    } catch (e) { ui.showToast(`Save failed: ${e.message}`, 'error'); }
}

function queueSave(card, section, body, delay = 1000) {
    clearTimeout(_saveTimers[section]?.t);
    const scopeAt = scope;   // a flush after a scope switch must not cross-write
    const fire = () => { delete _saveTimers[section]; return saveSection(card, section, body(), scopeAt); };
    _saveTimers[section] = { t: setTimeout(fire, delay), fire };
}

// Fire one section's pending save NOW, reading the live DOM. No-op when idle.
function flushSave(section) {
    const p = _saveTimers[section];
    if (!p) return null;
    clearTimeout(p.t);
    return p.fire();
}

async function flushAllSaves() {
    const waits = Object.keys(_saveTimers).map(flushSave).filter(Boolean);
    if (waits.length) await Promise.all(waits);
}

function collectRows(card) {
    return [...card.querySelectorAll('.palace-row:not(.palace-row-head)')].map(r => {
        const row = {};
        r.querySelectorAll('input').forEach(inp => { row[inp.dataset.k] = inp.value.trim(); });
        return row;
    }).filter(row => Object.values(row).some(v => v));
}

function bindCards(el) {
    // Text sections + custom boxes: autosave on idle, flush on blur.
    el.querySelectorAll('.palace-self-card:not(.palace-self-struct)').forEach(card => {
        const section = card.dataset.section;
        const ta = card.querySelector('textarea');
        if (!ta) return;
        ta.addEventListener('input', () => queueSave(card, section, () => ({ content: ta.value })));
        ta.addEventListener('blur', () => flushSave(section));
    });

    // Structured lists: the generic row editor (handles, relationships,
    // values, projects, custom lists). fields_spec rides along for custom
    // boxes so a not-yet-persisted list creates itself on first save.
    el.querySelectorAll('.palace-self-struct').forEach(card => {
        const section = card.dataset.section;
        const spec = JSON.parse(card.dataset.spec || '[]');
        const body = () => ({ rows: collectRows(card),
                              ...(card.dataset.custom ? { fields_spec: spec } : {}) });
        const save = () => queueSave(card, section, body);
        // Struct inputs had no blur flush (textareas did) — leaving the card
        // mid-debounce stranded the half-typed word as the final save.
        card.addEventListener('focusout', e => {
            if (!card.contains(e.relatedTarget)) flushSave(section);
        });
        const syncAdd = () => {
            const max = parseInt(card.dataset.max || '0', 10);
            const n = card.querySelectorAll('.palace-row:not(.palace-row-head)').length;
            card.querySelector('.palace-row-add').hidden = !!max && n >= max;
        };
        card.addEventListener('input', e => { if (e.target.matches('.palace-row input')) save(); });
        card.addEventListener('click', e => {
            if (e.target.matches('.palace-row-add')) {
                card.querySelector('.palace-row-list').insertAdjacentHTML('beforeend', structRow(spec));
                card.querySelector('.palace-row-list .palace-row:last-child input')?.focus();
                syncAdd();
            } else if (e.target.matches('.palace-row-del')) {
                e.target.closest('.palace-row').remove();
                syncAdd();
                save();
            }
        });
    });

    // History modals.
    el.querySelectorAll('.palace-self-hist').forEach(btn => {
        btn.addEventListener('click', () => showHistory(btn.dataset.section));
    });

    // Custom box removal (PUT empty = remove — user_bio semantics).
    el.querySelectorAll('.palace-self-delbox').forEach(btn => {
        btn.addEventListener('click', async () => {
            const section = btn.dataset.section;
            if (!confirm(`Remove box [${section}]?`)) return;
            _localBoxes = _localBoxes.filter(b => b.section !== section);
            try {
                await palaceSend(`self/${encodeURIComponent(section)}`, 'PUT', { content: '', scope });
                renderSheet();
            } catch (e) { ui.showToast(`Remove failed: ${e.message}`, 'error'); }
        });
    });

    // + Add box.
    el.querySelector('#pal-self-addbox')?.addEventListener('click', addBoxModal);
}

function _slug(s) {
    return s.trim().toLowerCase().replace(/\s+/g, '-').replace(/[^a-z0-9_-]/g, '').slice(0, 32);
}

function addBoxModal() {
    document.querySelector('.mind-modal-overlay')?.remove();
    const overlay = document.createElement('div');
    overlay.className = 'pr-modal-overlay mind-modal-overlay';
    overlay.innerHTML = `
        <div class="pr-modal palace-ent-modal">
            <div class="pr-modal-header">
                <h3>+ Add box</h3>
                <button class="mind-btn-sm mind-modal-close">✕</button>
            </div>
            <div class="pr-modal-body">
                <label class="palace-addbox-label">Name
                    <input type="text" id="pal-box-name" placeholder="e.g. quirks, games-beaten"></label>
                <label class="palace-addbox-label">Type
                    <select id="pal-box-type">
                        <option value="text">Text box (free writing)</option>
                        <option value="list">Structured list (columns, + add rows)</option>
                    </select></label>
                <div id="pal-box-cols" hidden>
                    <div class="palace-self-hint">Columns (1–3) — e.g. "Game" and "Score"</div>
                    <input type="text" class="pal-box-col palace-addbox-col" placeholder="Column 1">
                    <input type="text" class="pal-box-col palace-addbox-col" placeholder="Column 2 (optional)">
                    <input type="text" class="pal-box-col palace-addbox-col" placeholder="Column 3 (optional)">
                </div>
                <div class="palace-more-wrap"><button class="mind-btn" id="pal-box-create">Create</button></div>
            </div>
        </div>`;
    document.body.appendChild(overlay);
    const close = () => overlay.remove();
    overlay.querySelector('.mind-modal-close').addEventListener('click', close);
    setupModalClose(overlay, close);
    overlay.querySelector('#pal-box-type').addEventListener('change', e => {
        overlay.querySelector('#pal-box-cols').hidden = e.target.value !== 'list';
    });
    overlay.querySelector('#pal-box-create').addEventListener('click', () => {
        const slug = _slug(overlay.querySelector('#pal-box-name').value || '');
        if (!slug) { ui.showToast('Box needs a name', 'error'); return; }
        let fields = null;
        if (overlay.querySelector('#pal-box-type').value === 'list') {
            fields = [...overlay.querySelectorAll('.pal-box-col')]
                .map(i => i.value.trim()).filter(Boolean)
                .map(label => ({ key: _slug(label), label }));
            if (!fields.length) { ui.showToast('A list needs at least one column', 'error'); return; }
        }
        if (!_localBoxes.some(b => b.section === slug)) _localBoxes.push({ section: slug, fields });
        close();
        renderSheet().then(() => {
            content()?.querySelector(`.palace-self-card[data-section="${slug}"] textarea, ` +
                `.palace-self-card[data-section="${slug}"] .palace-row-add`)?.focus();
        });
    });
    overlay.querySelector('#pal-box-name').focus();
}

async function showHistory(section) {
    let data;
    try {
        data = await palaceGet(`self/${encodeURIComponent(section)}/history?scope=${encodeURIComponent(scope)}`);
    } catch (e) { ui.showToast(`History failed: ${e.message}`, 'error'); return; }

    document.querySelector('.mind-modal-overlay')?.remove();
    const overlay = document.createElement('div');
    overlay.className = 'pr-modal-overlay mind-modal-overlay';
    overlay.innerHTML = `
        <div class="pr-modal palace-ent-modal">
            <div class="pr-modal-header">
                <h3>\u{1F4DC} ${escHtml(section)} — the becoming trail</h3>
                <button class="mind-btn-sm mind-modal-close">✕</button>
            </div>
            <div class="pr-modal-body view-scroll">
                ${data.versions.length ? data.versions.map(v => `
                    <div class="mind-mem-card">
                        <div class="mind-mem-header">
                            <span class="mind-mem-time">written ${escHtml(timeAgo(v.created))} · archived ${escHtml(timeAgo(v.superseded_at))}</span>
                        </div>
                        <div class="mind-mem-content">${escHtml(v.content)}</div>
                    </div>`).join('') : '<div class="mind-empty">No archived versions yet</div>'}
            </div>
        </div>`;
    document.body.appendChild(overlay);
    overlay.querySelector('.mind-modal-close').addEventListener('click', () => overlay.remove());
    setupModalClose(overlay, () => overlay.remove());
}
