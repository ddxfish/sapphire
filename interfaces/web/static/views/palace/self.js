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
import { PALACE_TABS, SCOPE_ENDPOINT, palaceGet, palaceSend } from './common.js';

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
        // Editor semantics: skip SSE refresh while a card is being typed in.
        if (!unsub) unsub = subscribeMindDomain('memory', () => scope,
            () => container?.offsetParent !== null && !container.querySelector('.palace-self-card textarea:focus, .palace-kv input:focus'),
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
        onScopeChange: (s) => { scope = s; _localBoxes = []; render(); },
        onChanged: async (s) => { scope = s || 'default'; _localBoxes = []; scopes = await listScopes(SCOPE_ENDPOINT); render(); },
    });
    renderSheet();
}

async function renderSheet() {
    const el = content();
    if (!el) return;
    let data;
    try {
        data = await palaceGet(`self?scope=${encodeURIComponent(scope)}`);
    } catch (e) {
        el.innerHTML = `<div class="mind-empty">Failed to load: ${escHtml(e.message)}</div>`;
        return;
    }
    const persisted = new Set(data.custom.map(c => c.section));
    _localBoxes = _localBoxes.filter(n => !persisted.has(n));

    el.innerHTML = `
        ${dashboardCard(data.dashboard)}
        <div class="palace-self-grid">
            ${data.sections.map(s => s.section === 'handles' ? handlesCard(s) : sectionCard(s)).join('')}
            ${data.custom.map(c => sectionCard({ ...c, title: `[${c.section}]`, hint: 'custom box', mode: 'hand', versioned: false, custom: true })).join('')}
            ${_localBoxes.map(n => sectionCard({ section: n, title: `[${n}]`, hint: 'custom box — saves when you write', mode: 'hand', versioned: false, custom: true, content: '', history_count: 0 })).join('')}
        </div>
        <div class="palace-more-wrap"><button class="mind-btn" id="pal-self-addbox">+ Add box</button></div>
    `;
    bindCards(el);
    bindLibrarian(el);
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

function sectionCard(s) {
    return `
        <div class="mind-mem-card palace-self-card" data-section="${escAttr(s.section)}">
            <div class="palace-self-card-head">
                <span class="palace-self-title">${escHtml(s.title)}</span>
                ${modeChip(s.mode)}
                <span class="palace-self-saved" hidden>✓ saved</span>
                ${s.history_count ? `<button class="mind-btn-sm palace-self-hist" data-section="${escAttr(s.section)}" title="Archived versions">\u{1F4DC} ${s.history_count}</button>` : ''}
                ${s.custom ? `<button class="mind-btn-sm palace-self-delbox" data-section="${escAttr(s.section)}" title="Remove box">✕</button>` : ''}
            </div>
            <div class="palace-self-hint">${escHtml(s.hint)}${s.updated ? ` · ${escHtml(timeAgo(s.updated))}` : ''}</div>
            <textarea class="palace-self-text" maxlength="${MAX_CHARS}" placeholder="${escAttr(s.hint)}">${escHtml(s.content)}</textarea>
        </div>`;
}

function handlesCard(s) {
    const pairs = s.pairs?.length ? s.pairs : [];
    return `
        <div class="mind-mem-card palace-self-card palace-self-handles" data-section="handles">
            <div class="palace-self-card-head">
                <span class="palace-self-title">${escHtml(s.title)}</span>
                ${modeChip(s.mode)}
                <span class="palace-self-saved" hidden>✓ saved</span>
            </div>
            <div class="palace-self-hint">${escHtml(s.hint)}${s.updated ? ` · ${escHtml(timeAgo(s.updated))}` : ''}</div>
            <div class="palace-kv-list">
                ${pairs.map(p => kvRow(p.key, p.value)).join('')}
            </div>
            <button class="mind-btn-sm palace-kv-add">+ add</button>
        </div>`;
}

function kvRow(key = '', value = '') {
    return `<div class="palace-kv">
        <input type="text" class="palace-kv-key" placeholder="key" value="${escAttr(key)}">
        <input type="text" class="palace-kv-val" placeholder="value" value="${escAttr(value)}">
        <button class="mind-btn-sm palace-kv-del" title="Remove">✕</button>
    </div>`;
}

function flashSaved(card) {
    const chip = card.querySelector('.palace-self-saved');
    if (!chip) return;
    chip.hidden = false;
    clearTimeout(chip._t);
    chip._t = setTimeout(() => { chip.hidden = true; }, 1500);
}

async function saveSection(card, section, body) {
    try {
        await palaceSend(`self/${encodeURIComponent(section)}`, 'PUT', { ...body, scope });
        flashSaved(card);
    } catch (e) { ui.showToast(`Save failed: ${e.message}`, 'error'); }
}

function queueSave(card, section, body, delay = 900) {
    clearTimeout(_saveTimers[section]);
    _saveTimers[section] = setTimeout(() => saveSection(card, section, body()), delay);
}

function collectPairs(card) {
    return [...card.querySelectorAll('.palace-kv')].map(row => ({
        key: row.querySelector('.palace-kv-key').value.trim(),
        value: row.querySelector('.palace-kv-val').value.trim(),
    })).filter(p => p.key);
}

function bindCards(el) {
    // Text sections + custom boxes: autosave on idle, flush on blur.
    el.querySelectorAll('.palace-self-card:not(.palace-self-handles)').forEach(card => {
        const section = card.dataset.section;
        const ta = card.querySelector('textarea');
        if (!ta) return;
        ta.addEventListener('input', () => queueSave(card, section, () => ({ content: ta.value })));
        ta.addEventListener('blur', () => {
            clearTimeout(_saveTimers[section]);
            saveSection(card, section, { content: ta.value });
        });
    });

    // Handles: KV editor.
    const hc = el.querySelector('.palace-self-handles');
    if (hc) {
        const saveHandles = () => queueSave(hc, 'handles', () => ({ pairs: collectPairs(hc) }), 600);
        hc.addEventListener('input', e => { if (e.target.matches('.palace-kv input')) saveHandles(); });
        hc.addEventListener('click', e => {
            if (e.target.matches('.palace-kv-add')) {
                hc.querySelector('.palace-kv-list').insertAdjacentHTML('beforeend', kvRow());
                hc.querySelector('.palace-kv:last-child .palace-kv-key').focus();
            } else if (e.target.matches('.palace-kv-del')) {
                e.target.closest('.palace-kv').remove();
                saveHandles();
            }
        });
    }

    // History modals.
    el.querySelectorAll('.palace-self-hist').forEach(btn => {
        btn.addEventListener('click', () => showHistory(btn.dataset.section));
    });

    // Custom box removal (PUT empty = remove — user_bio semantics).
    el.querySelectorAll('.palace-self-delbox').forEach(btn => {
        btn.addEventListener('click', async () => {
            const section = btn.dataset.section;
            if (!confirm(`Remove box [${section}]?`)) return;
            _localBoxes = _localBoxes.filter(n => n !== section);
            try {
                await palaceSend(`self/${encodeURIComponent(section)}`, 'PUT', { content: '', scope });
                renderSheet();
            } catch (e) { ui.showToast(`Remove failed: ${e.message}`, 'error'); }
        });
    });

    // + Add box.
    el.querySelector('#pal-self-addbox')?.addEventListener('click', () => {
        const name = prompt('Box name:');
        if (!name?.trim()) return;
        const slug = name.trim().toLowerCase().replace(/\s+/g, '-').replace(/[^a-z0-9_-]/g, '').slice(0, 32);
        if (!slug) { ui.showToast('Invalid box name', 'error'); return; }
        if (!_localBoxes.includes(slug)) _localBoxes.push(slug);
        renderSheet().then(() => {
            content()?.querySelector(`.palace-self-card[data-section="${slug}"] textarea`)?.focus();
        });
    });
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
