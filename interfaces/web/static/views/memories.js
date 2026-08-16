// views/memories.js - Mind › Memories. Short snippets the AI saves. Own scope
// domain (memory_scope). The most stateful Mind view: warm cache + client-side
// search/sort/label-chips + dedup review + edit. Extracted from mind.js.
import { renderSectionHeader, bindSectionHeader } from '../shared/section-header.js';
import { helpPills } from '../features/video-link.js';
import { renderScopeSidebar, bindScopeSidebar } from '../shared/scope-sidebar.js';
import { listScopes } from '../shared/scope-api.js';
import { MIND_TABS, csrfHeaders, escHtml, scopeForChatTab, subscribeMindDomain } from '../shared/mind-common.js';
import { showExportDialog, showImportDialog } from '../shared/import-export.js';
import { setupModalClose } from '../shared/modal.js';
import * as ui from '../ui.js';

const SCOPE_KEY = 'memory_scope';
const DOMAIN = 'memory';
const SCOPE_ENDPOINT = '/api/memory/scopes';

let container = null;
let scope = 'default';
let scopes = [];
let unsub = null;

// Filter/view state — survives in-view re-renders; reset on scope change.
let _memSearch = '';
let _memSort = 'newest';      // newest | oldest | longest | shortest | label
let _memLabelFilter = null;    // null = all
let _memShowAll = false;
const MEM_INITIAL_LIMIT = 200;
const MEM_TOP_LABELS = 8;

// Warm cache: rows for the current scope. Filter/sort/search re-render from this
// without a network round-trip. Invalidated on scope change / MIND_CHANGED / edits.
let _memCache = { scope: null, rows: null };
function _invalidateMemCache() { _memCache = { scope: null, rows: null }; }
function resetFilters() { _memSearch = ''; _memSort = 'newest'; _memLabelFilter = null; _memShowAll = false; }

export default {
    init(el) { container = el; },
    async show() {
        // Subscribe BEFORE the awaits so a fast tab-switch (hide() during the
        // await) can't orphan the subscription. Guarded so re-entry won't double.
        if (!unsub) unsub = subscribeMindDomain(DOMAIN, () => scope, () => container?.offsetParent !== null,
            () => { _invalidateMemCache(); renderMemories(); });
        if (window._mindScope) { scope = window._mindScope; delete window._mindScope; }
        else { const s = await scopeForChatTab(SCOPE_KEY); if (s) scope = s; }
        delete window._mindTab;
        scopes = await listScopes(SCOPE_ENDPOINT);
        render();
    },
    hide() { if (unsub) { unsub(); unsub = null; } }
};

function content() { return container?.querySelector('#mem-content'); }

function render() {
    if (!container) return;
    container.innerHTML = `
        ${renderSectionHeader({ tabs: MIND_TABS, active: 'memories', help: helpPills('Memories', { video: 'nM__u1fiWCw', doc: 'MEMORY.md', inline: true }), status: 'Short snippets Sapphire saves during conversation — search, filter, or click chips to narrow.' })}
        <div class="two-panel">
            ${renderScopeSidebar(scopes, scope)}
            <div class="panel-right">
                <div class="view-body view-scroll" id="mem-content"></div>
            </div>
        </div>`;
    bindSectionHeader(container);
    bindScopeSidebar(container, {
        onScopeChange: (s) => { scope = s; resetFilters(); _invalidateMemCache(); render(); },
        onChanged: async (s) => { scope = s || 'default'; resetFilters(); _invalidateMemCache(); scopes = await listScopes(SCOPE_ENDPOINT); render(); },
    });
    renderMemories();
}

// ── Memory rendering (extracted from mind.js) ──

const MEM_RELATIVE_TIME_THRESHOLDS = [
    [60, 'just now', 1],
    [3600, 'm ago', 60],
    [86400, 'h ago', 3600],
    [604800, 'd ago', 86400],
    [2592000, 'w ago', 604800],
    [Infinity, 'mo ago', 2592000],
];
function _relativeTime(ts) {
    if (!ts) return '';
    const t = typeof ts === 'string' ? new Date(ts).getTime() : ts;
    if (!t || isNaN(t)) return '';
    const sec = Math.max(0, (Date.now() - t) / 1000);
    for (const [bound, suffix, divisor] of MEM_RELATIVE_TIME_THRESHOLDS) {
        if (sec < bound) {
            return suffix === 'just now' ? suffix : `${Math.floor(sec / divisor)}${suffix}`;
        }
    }
    return new Date(t).toLocaleDateString();
}

function _labelHue(label) {
    if (!label) return 220;
    let h = 0;
    for (let i = 0; i < label.length; i++) h = (h * 31 + label.charCodeAt(i)) % 360;
    return h;
}

function _renderMemoryCard(m, animDelay) {
    const labelText = m.label || 'unlabeled';
    const hue = _labelHue(m.label);
    const labelStyle = m.label
        ? `background:hsl(${hue},60%,18%);color:hsl(${hue},80%,72%);border:1px solid hsl(${hue},60%,32%)`
        : `background:var(--bg-tertiary,#1a1b2e);color:var(--text-muted,#888);border:1px solid var(--border,#333)`;
    const keyPill = m.private_key
        ? `<span class="mind-mem-key" title="Gated by this private key — only AI calls passing this key can see it">🔒 ${escHtml(m.private_key)}</span>`
        : '';
    const ts = _relativeTime(m.timestamp);
    const pkAttr = m.private_key ? ` data-private-key="${escHtml(m.private_key)}"` : '';
    return `
        <div class="mind-mem-card" data-id="${m.id}"${pkAttr} style="animation-delay:${animDelay.toFixed(2)}s">
            <div class="mind-mem-header">
                <span class="mind-mem-label" style="${labelStyle}">${escHtml(labelText)}</span>
                ${keyPill}
                <span class="mind-mem-time">${escHtml(ts)}</span>
                <span class="mind-mem-id">[${m.id}]</span>
            </div>
            <div class="mind-mem-content">${escHtml(m.content)}</div>
            <div class="mind-mem-actions">
                <button class="mind-btn-sm mind-edit-memory" data-id="${m.id}" title="Edit">&#x270E;</button>
                <button class="mind-btn-sm mind-del-memory" data-id="${m.id}" title="Delete">&#x2715;</button>
            </div>
        </div>
    `;
}

const MEM_CARD_STYLES = `
<style>
@keyframes mindMemSlideIn {
    from { opacity: 0; transform: translateY(8px); }
    to { opacity: 1; transform: translateY(0); }
}
.mind-mem-controls {
    container-type: inline-size;
    display: flex; align-items: center; gap: 10px; margin-bottom: 14px;
    flex-wrap: wrap;
}
.mind-mem-search-wrap { position: relative; flex: 1 1 200px; min-width: 0; }
.mind-mem-sort { flex-shrink: 0; }
.mind-mem-stats-inline { flex-shrink: 0; }
@container (max-width: 520px) {
    .mind-mem-stats-inline { flex-basis: 100%; margin-left: 0; margin-top: 2px; justify-content: flex-end; }
}
.mind-mem-search-wrap::before {
    content: '⌕'; position: absolute; left: 10px; top: 50%; transform: translateY(-50%);
    font-size: 0.8125rem; color: var(--text-muted, #888); pointer-events: none;
}
.mind-mem-search {
    width: 100%; background: var(--bg-secondary, #1a1b2e); color: var(--text, #e1e1e6);
    border: 1px solid var(--border, #333); border-radius: 6px;
    padding: 7px 12px 7px 30px; font-size: 0.8125rem; outline: none;
}
.mind-mem-search:focus { border-color: var(--accent, #4a7); }
.mind-mem-sort {
    width: auto !important;
    background: var(--bg-secondary, #1a1b2e); color: var(--text, #e1e1e6);
    border: 1px solid var(--border, #333); border-radius: 6px;
    padding: 6px 10px; font-size: 0.75rem; cursor: pointer; outline: none;
}
.mind-mem-chips { display: flex; flex-wrap: wrap; gap: 4px; margin-bottom: 14px; }
.mind-mem-chip {
    padding: 4px 10px; font-size: 0.6875rem; border-radius: 4px; cursor: pointer;
    background: transparent; color: var(--text-muted, #888);
    border: 1px solid var(--border, #333); transition: all 0.15s;
}
.mind-mem-chip:hover { color: var(--text, #e1e1e6); border-color: var(--accent, #4a7); }
.mind-mem-chip.active {
    background: hsla(var(--chip-hue, 200), 60%, 18%, 1);
    color: hsl(var(--chip-hue, 200), 80%, 72%);
    border-color: hsl(var(--chip-hue, 200), 60%, 40%);
}
.mind-mem-stats-inline {
    margin-left: auto; display: inline-flex; gap: 6px; align-items: center;
    font-size: 0.6875rem; font-family: monospace; color: var(--text-muted, #888);
    white-space: nowrap;
}
.mind-mem-stats-inline strong { color: var(--text, #e1e1e6); }
.mind-mem-stats-scope { color: var(--text, #e1e1e6); opacity: 0.85; }
.mind-mem-list { display: flex; flex-direction: column; gap: 8px; }
.mind-mem-card {
    background: var(--bg-secondary, #1a1b2e); border: 1px solid var(--border, #333);
    border-radius: 8px; padding: 12px 14px; position: relative;
    animation: mindMemSlideIn 0.32s ease both;
}
.mind-mem-header { display: flex; align-items: center; gap: 8px; margin-bottom: 7px; flex-wrap: wrap; }
.mind-mem-label { font-size: 0.625rem; padding: 2px 8px; border-radius: 3px; font-family: monospace; letter-spacing: 0.04em; }
.mind-mem-key {
    font-size: 0.625rem; padding: 2px 8px; border-radius: 3px; font-family: monospace;
    background: hsla(40, 80%, 18%, 1); color: hsl(40, 90%, 70%); border: 1px solid hsl(40, 70%, 38%);
}
.mind-mem-time { font-size: 0.625rem; color: var(--text-muted, #888); font-family: monospace; margin-left: auto; }
.mind-mem-id { font-size: 0.5625rem; color: var(--text-muted, #888); font-family: monospace; opacity: 0.5; }
.mind-mem-content { font-size: 0.8125rem; color: var(--text, #e1e1e6); line-height: 1.55; word-break: break-word; }
.mind-mem-actions { position: absolute; top: 8px; right: 8px; display: flex; gap: 4px; opacity: 0; transition: opacity 0.15s; }
.mind-mem-card:hover .mind-mem-actions { opacity: 1; }
.mind-mem-show-more {
    margin-top: 10px; padding: 8px; text-align: center; font-size: 0.75rem;
    color: var(--text-muted, #888); cursor: pointer;
    background: var(--bg-secondary, #1a1b2e); border: 1px dashed var(--border, #333); border-radius: 6px;
}
.mind-mem-show-more:hover { color: var(--text, #e1e1e6); border-color: var(--accent, #4a7); }
.mind-mem-empty { padding: 24px; text-align: center; color: var(--text-muted, #888); font-style: italic; }
</style>
`;

async function renderMemories(elArg) {
    const el = elArg || content();
    if (!el) return;
    if (_memCache.scope !== scope || _memCache.rows === null) {
        try {
            const resp = await fetch(`/api/memory/list?scope=${encodeURIComponent(scope)}`);
            if (!resp.ok) { el.innerHTML = '<div class="mind-empty">Failed to load memories</div>'; return; }
            const data = await resp.json();
            const groups = data.memories || {};
            const rows = [];
            for (const arr of Object.values(groups)) for (const m of arr) rows.push(m);
            _memCache = { scope, rows };
        } catch (e) {
            el.innerHTML = `<div class="mind-empty">Failed to load memories: ${e.message}</div>`;
            return;
        }
    }
    _renderMemoriesFromCache(el);
}

function _renderMemoriesFromCache(el) {
    const focusedEl = document.activeElement;
    const refocus = focusedEl && el.contains(focusedEl) && focusedEl.id
        ? { id: focusedEl.id, selStart: focusedEl.selectionStart ?? null, selEnd: focusedEl.selectionEnd ?? null }
        : null;

    const all = _memCache.rows || [];

    const labelCounts = {};
    for (const m of all) {
        const k = m.label || 'unlabeled';
        labelCounts[k] = (labelCounts[k] || 0) + 1;
    }
    const topLabels = Object.entries(labelCounts).sort((a, b) => b[1] - a[1]).slice(0, MEM_TOP_LABELS);

    const totalCount = all.length;
    const privateCount = all.filter(m => m.private_key).length;
    const labelVariety = Object.keys(labelCounts).length;

    const search = _memSearch.trim().toLowerCase();
    let filtered = all;
    if (_memLabelFilter) filtered = filtered.filter(m => (m.label || 'unlabeled') === _memLabelFilter);
    if (search) {
        filtered = filtered.filter(m =>
            (m.content || '').toLowerCase().includes(search) ||
            (m.label || '').toLowerCase().includes(search) ||
            (m.private_key || '').toLowerCase().includes(search)
        );
    }
    const sortFns = {
        newest: (a, b) => (new Date(b.timestamp) - new Date(a.timestamp)),
        oldest: (a, b) => (new Date(a.timestamp) - new Date(b.timestamp)),
        longest: (a, b) => (b.content || '').length - (a.content || '').length,
        shortest: (a, b) => (a.content || '').length - (b.content || '').length,
        label: (a, b) => (a.label || 'zz').localeCompare(b.label || 'zz'),
    };
    filtered.sort(sortFns[_memSort] || sortFns.newest);

    const visible = _memShowAll ? filtered : filtered.slice(0, MEM_INITIAL_LIMIT);
    const hidden = filtered.length - visible.length;

    const toolbar = `<div class="mind-toolbar">
        <button class="mind-btn" id="mind-find-dups">Find Duplicates</button>
        <button class="mind-btn" id="mind-export-memories">Export</button>
        <button class="mind-btn" id="mind-import-memories">Import</button>
    </div>`;

    if (!totalCount) {
        el.innerHTML = MEM_CARD_STYLES + toolbar + '<div class="mind-mem-empty">No memories in this scope yet.</div>';
        _bindMemoryIO(el);
        return;
    }

    const chips = [
        `<div class="mind-mem-chip ${_memLabelFilter === null ? 'active' : ''}" data-label="" style="--chip-hue:200">All (${totalCount})</div>`,
        ...topLabels.map(([label, count]) => {
            const hue = _labelHue(label === 'unlabeled' ? null : label);
            const active = _memLabelFilter === label ? 'active' : '';
            return `<div class="mind-mem-chip ${active}" data-label="${escHtml(label)}" style="--chip-hue:${hue}">${escHtml(label)} (${count})</div>`;
        }),
    ].join('');

    const cards = visible.map((m, i) => _renderMemoryCard(m, i * 0.025)).join('');
    const showMoreBtn = hidden > 0
        ? `<div class="mind-mem-show-more" id="mind-mem-show-all">Show ${hidden} more memories</div>`
        : '';
    const emptyFiltered = !visible.length
        ? `<div class="mind-mem-empty">No memories match ${search ? `"${escHtml(search)}"` : 'this filter'}.</div>`
        : '';

    const statsInline = `
        <span class="mind-mem-stats-inline">
            <span><strong>${totalCount}</strong> mem</span>
            <span>·</span>
            <span><strong>${labelVariety}</strong> labels</span>
            ${privateCount > 0 ? `<span>·</span><span><strong>${privateCount}</strong> private</span>` : ''}
            <span>·</span>
            <span class="mind-mem-stats-scope">${escHtml(scope)}</span>
        </span>
    `;

    el.innerHTML = MEM_CARD_STYLES + toolbar + `
        <div class="mind-mem-controls">
            <div class="mind-mem-search-wrap">
                <input type="text" class="mind-mem-search" id="mind-mem-search"
                    placeholder="Search memories..." value="${escHtml(_memSearch)}">
            </div>
            <select class="mind-mem-sort" id="mind-mem-sort">
                <option value="newest" ${_memSort === 'newest' ? 'selected' : ''}>Sort: Newest</option>
                <option value="oldest" ${_memSort === 'oldest' ? 'selected' : ''}>Sort: Oldest</option>
                <option value="longest" ${_memSort === 'longest' ? 'selected' : ''}>Sort: Longest</option>
                <option value="shortest" ${_memSort === 'shortest' ? 'selected' : ''}>Sort: Shortest</option>
                <option value="label" ${_memSort === 'label' ? 'selected' : ''}>Sort: By Label</option>
            </select>
            ${statsInline}
        </div>
        <div class="mind-mem-chips">${chips}</div>
        <div class="mind-mem-list">${cards}${emptyFiltered}</div>
        ${showMoreBtn}
    `;

    el.querySelector('#mind-mem-search')?.addEventListener('input', e => {
        _memSearch = e.target.value;
        _memShowAll = false;
        renderMemories(el);
    });
    el.querySelector('#mind-mem-sort')?.addEventListener('change', e => {
        _memSort = e.target.value;
        renderMemories(el);
    });
    el.querySelectorAll('.mind-mem-chip').forEach(chip => {
        chip.addEventListener('click', () => {
            const lbl = chip.dataset.label || null;
            _memLabelFilter = (lbl === _memLabelFilter || lbl === '') ? null : lbl;
            _memShowAll = false;
            renderMemories(el);
        });
    });
    el.querySelector('#mind-mem-show-all')?.addEventListener('click', () => {
        _memShowAll = true;
        renderMemories(el);
    });

    el.querySelectorAll('.mind-edit-memory').forEach(btn => {
        btn.addEventListener('click', () => {
            const id = parseInt(btn.dataset.id);
            const card = btn.closest('.mind-mem-card');
            const c = card.querySelector('.mind-mem-content').textContent;
            showMemoryEditModal(el, id, c);
        });
    });

    el.querySelectorAll('.mind-del-memory').forEach(btn => {
        btn.addEventListener('click', async () => {
            if (!confirm('Delete this memory?')) return;
            const id = parseInt(btn.dataset.id);
            const card = btn.closest('.mind-mem-card');
            const pk = card?.dataset.privateKey || '';
            const url = `/api/memory/${id}?scope=${encodeURIComponent(scope)}`
                + (pk ? `&private_key=${encodeURIComponent(pk)}` : '');
            try {
                const resp = await fetch(url, { method: 'DELETE', headers: csrfHeaders() });
                if (resp.ok) { ui.showToast('Deleted', 'success'); _invalidateMemCache(); await renderMemories(el); }
            } catch (e) { ui.showToast('Failed', 'error'); }
        });
    });

    _bindMemoryIO(el);

    if (refocus) {
        const restored = el.querySelector(`#${refocus.id}`);
        if (restored) {
            restored.focus();
            if (refocus.selStart !== null && typeof restored.setSelectionRange === 'function') {
                try { restored.setSelectionRange(refocus.selStart, refocus.selEnd); } catch { /* unsupported */ }
            }
        }
    }
}

function _bindMemoryIO(el) {
    el.querySelector('#mind-export-memories')?.addEventListener('click', async () => {
        try {
            const resp = await fetch(`/api/memory/export?scope=${encodeURIComponent(scope)}`);
            if (!resp.ok) throw new Error('Export failed');
            const data = await resp.json();
            showExportDialog({ type: 'Memories', name: `${scope} (${data.count})`, filename: `memories-${scope}.json`, data });
        } catch (e) { ui.showToast(e.message, 'error'); }
    });

    el.querySelector('#mind-find-dups')?.addEventListener('click', async () => {
        try {
            const btn = el.querySelector('#mind-find-dups');
            btn.textContent = 'Scanning...';
            btn.disabled = true;
            const resp = await fetch(`/api/memory/duplicates?scope=${encodeURIComponent(scope)}`);
            btn.textContent = 'Find Duplicates';
            btn.disabled = false;
            if (!resp.ok) throw new Error('Scan failed');
            const data = await resp.json();
            if (!data.pairs.length) { ui.showToast('No duplicates found', 'success'); return; }
            _showDuplicatesModal(el, data.pairs);
        } catch (e) { ui.showToast(e.message, 'error'); }
    });

    el.querySelector('#mind-import-memories')?.addEventListener('click', () => {
        showImportDialog({
            type: 'Memories',
            existingNames: [],
            validate: (d) => (d.entries && Array.isArray(d.entries)) ? null : 'Invalid format: needs entries array',
            getName: (d) => d.scope || scope,
            onImport: async (data) => {
                const resp = await fetch('/api/memory/import', {
                    method: 'POST',
                    headers: csrfHeaders({ 'Content-Type': 'application/json' }),
                    body: JSON.stringify({ entries: data.entries, scope }),
                });
                if (!resp.ok) throw new Error('Import failed');
                const result = await resp.json();
                ui.showToast(`Imported ${result.imported} memories, ${result.skipped} duplicates skipped`, 'success');
            },
            onDone: async () => { _invalidateMemCache(); await renderMemories(el); },
        });
    });
}

function _showDuplicatesModal(el, pairs) {
    document.querySelector('.mind-modal-overlay')?.remove();
    let currentIdx = 0;
    const overlay = document.createElement('div');
    overlay.className = 'pr-modal-overlay mind-modal-overlay';
    document.body.appendChild(overlay);

    function renderPair() {
        if (currentIdx >= pairs.length) {
            overlay.remove();
            ui.showToast('All duplicates reviewed', 'success');
            _invalidateMemCache();
            renderMemories(el);
            return;
        }
        const pair = pairs[currentIdx];
        const pct = Math.round(pair.similarity * 100);
        overlay.innerHTML = `
            <div class="pr-modal" style="max-width:650px">
                <div class="pr-modal-header">
                    <h3>Duplicates (${currentIdx + 1}/${pairs.length}) — ${pct}% similar</h3>
                    <button class="mind-btn-sm mind-modal-close">&#x2715;</button>
                </div>
                <div class="pr-modal-body" style="display:flex;flex-direction:column;gap:12px">
                    <div style="display:flex;gap:12px">
                        <div style="flex:1;padding:10px;background:var(--bg-tertiary);border-radius:var(--radius);font-size:var(--font-sm)">
                            <div style="font-size:0.6875rem;color:var(--text-muted);margin-bottom:4px">Keep (oldest)</div>
                            ${escHtml(pair.keep.content)}
                            ${pair.keep.label ? `<div style="margin-top:6px;font-size:0.6875rem;color:var(--text-muted)">Label: ${escHtml(pair.keep.label)}</div>` : ''}
                        </div>
                        <div style="flex:1;padding:10px;background:var(--bg-tertiary);border-radius:var(--radius);font-size:var(--font-sm);opacity:0.7">
                            <div style="font-size:0.6875rem;color:var(--text-muted);margin-bottom:4px">Remove (newer)</div>
                            ${escHtml(pair.remove.content)}
                            ${pair.remove.label ? `<div style="margin-top:6px;font-size:0.6875rem;color:var(--text-muted)">Label: ${escHtml(pair.remove.label)}</div>` : ''}
                        </div>
                    </div>
                    <div style="display:flex;gap:8px;justify-content:center">
                        <button class="mind-btn" id="dup-combine">Combine</button>
                        <button class="mind-btn" id="dup-delete">Delete Newer</button>
                        <button class="mind-btn" id="dup-skip">Skip</button>
                        <button class="mind-btn" id="dup-skip-all" style="color:var(--text-muted)">Done</button>
                    </div>
                </div>
            </div>
        `;

        overlay.querySelector('.mind-modal-close').addEventListener('click', () => {
            overlay.remove(); _invalidateMemCache(); renderMemories(el);
        });
        overlay.querySelector('#dup-delete').addEventListener('click', async () => {
            try {
                await fetch(`/api/memory/${pair.remove.id}?scope=${encodeURIComponent(scope)}`, { method: 'DELETE', headers: csrfHeaders() });
                currentIdx++; renderPair();
            } catch { ui.showToast('Delete failed', 'error'); }
        });
        overlay.querySelector('#dup-combine').addEventListener('click', async () => {
            const combined = pair.keep.content + '\n' + pair.remove.content;
            try {
                await fetch(`/api/memory/${pair.keep.id}`, {
                    method: 'PUT',
                    headers: csrfHeaders({ 'Content-Type': 'application/json' }),
                    body: JSON.stringify({ content: combined, scope }),
                });
                await fetch(`/api/memory/${pair.remove.id}?scope=${encodeURIComponent(scope)}`, { method: 'DELETE', headers: csrfHeaders() });
                currentIdx++; renderPair();
            } catch { ui.showToast('Combine failed', 'error'); }
        });
        overlay.querySelector('#dup-skip').addEventListener('click', () => { currentIdx++; renderPair(); });
        overlay.querySelector('#dup-skip-all').addEventListener('click', () => {
            overlay.remove(); _invalidateMemCache(); renderMemories(el);
        });
    }
    renderPair();
}

function showMemoryEditModal(el, memoryId, content) {
    document.querySelector('.mind-modal-overlay')?.remove();
    const overlay = document.createElement('div');
    overlay.className = 'pr-modal-overlay mind-modal-overlay';
    overlay.innerHTML = `
        <div class="pr-modal">
            <div class="pr-modal-header">
                <h3>Edit Memory</h3>
                <button class="mind-btn-sm mind-modal-close">&#x2715;</button>
            </div>
            <div class="pr-modal-body">
                <div class="mind-form">
                    <textarea id="mm-content" rows="8" style="min-height:150px">${escHtml(content)}</textarea>
                    <div style="display:flex;justify-content:flex-end;gap:8px">
                        <button class="mind-btn mind-modal-cancel">Cancel</button>
                        <button class="mind-btn" id="mm-save" style="border-color:var(--trim,var(--accent-blue))">Save</button>
                    </div>
                </div>
            </div>
        </div>
    `;
    document.body.appendChild(overlay);

    const close = () => overlay.remove();
    overlay.querySelector('.mind-modal-close').addEventListener('click', close);
    overlay.querySelector('.mind-modal-cancel').addEventListener('click', close);
    setupModalClose(overlay, close);

    const textarea = overlay.querySelector('#mm-content');
    textarea.focus();
    textarea.setSelectionRange(textarea.value.length, textarea.value.length);

    overlay.querySelector('#mm-save').addEventListener('click', async () => {
        const newContent = textarea.value.trim();
        if (!newContent) { ui.showToast('Content cannot be empty', 'error'); return; }
        if (newContent === content) { close(); return; }
        try {
            const resp = await fetch(`/api/memory/${memoryId}`, {
                method: 'PUT',
                headers: csrfHeaders({ 'Content-Type': 'application/json' }),
                body: JSON.stringify({ content: newContent, scope })
            });
            if (resp.ok) { close(); ui.showToast('Memory updated', 'success'); _invalidateMemCache(); await renderMemories(el); }
            else { const err = await resp.json(); ui.showToast(err.detail || 'Failed', 'error'); }
        } catch (e) { ui.showToast('Failed', 'error'); }
    });
}
