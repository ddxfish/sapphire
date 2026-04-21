// views/mind.js - Mind view: Memories, People, Knowledge, AI Knowledge, Goals
import * as ui from '../ui.js';
import { showExportDialog, showImportDialog } from '../shared/import-export.js';
import { setupModalClose } from '../shared/modal.js';
import { getInitData } from '../shared/init-data.js';
import { on as onBusEvent, Events as BusEvents } from '../core/event-bus.js';

function csrfHeaders(extra = {}) {
    const token = document.querySelector('meta[name="csrf-token"]')?.content || '';
    return { 'X-CSRF-Token': token, ...extra };
}

let container = null;
let activeTab = 'memories';
let currentScope = 'default';
let memoryScopeCache = [];
let knowledgeScopeCache = [];
let peopleScopeCache = [];
let goalScopeCache = [];
let memoryPage = 0;
const MEMORIES_PER_PAGE = 100;

// Memory card view state — survives re-renders within a single Mind session
// so toggling tabs / scopes doesn't reset what the user was filtering by.
// Reset by hand if you ever want a "fresh view" button.
let _memSearch = '';
let _memSort = 'newest';            // newest | oldest | longest | shortest | label
let _memLabelFilter = null;          // null = show all labels; string = filter
let _memShowAll = false;             // false = cap at MEM_INITIAL_LIMIT cards
const MEM_INITIAL_LIMIT = 200;
const MEM_TOP_LABELS = 8;

// Fetched-data cache. Populated on first render for a scope; re-rendered
// in-place on every filter change WITHOUT a new fetch. Invalidated on scope
// change, MIND_CHANGED event for this domain/scope, or manual invalidate().
// Witch-hunt 2026-04-21 R2 — without this, every keystroke fired a new
// `/api/memory/list` HTTP round-trip; rapid typing also caused response
// reorder (fetch-ab lands after fetch-abc) showing stale results.
let _memCache = { scope: null, rows: null };
function _invalidateMemCache() { _memCache = { scope: null, rows: null }; }

export default {
    init(el) {
        container = el;
        subscribeMindSse();
    },
    async show() {
        if (window._mindTab) {
            activeTab = window._mindTab;
            delete window._mindTab;
        }
        if (window._mindScope) {
            // Explicit programmatic override (e.g. clicked a memory link from chat)
            currentScope = window._mindScope;
            delete window._mindScope;
        } else {
            // Sync to the active chat's scope for the current tab. Without this,
            // Mind view always shows 'default' while the AI writes into whatever
            // scope the active chat's settings have (memory_scope/goal_scope/etc).
            // Two rooms, same house — root of the "Sapphire made a goal but I
            // don't see it" class of bug.
            const chatScope = await _scopeForActiveChatTab(activeTab);
            if (chatScope) currentScope = chatScope;
        }
        await render();
    },
    hide() {}
};


// ─── Active-chat scope resolution ────────────────────────────────────────────

const _TAB_TO_SCOPE_KEY = {
    memories: 'memory_scope',
    people: 'people_scope',
    knowledge: 'knowledge_scope',
    'ai-notes': 'knowledge_scope',
    goals: 'goal_scope',
};

// Each Mind tab corresponds to a server-side MIND_CHANGED domain. Used by the
// SSE handler below to decide whether an incoming event is relevant to the
// currently-visible tab. 'ai-notes' and 'knowledge' share the knowledge
// domain on the server (same tables, filtered by tab type on the client).
const _DOMAIN_FOR_TAB = {
    memories: 'memory',
    people: 'people',
    knowledge: 'knowledge',
    'ai-notes': 'knowledge',
    goals: 'goal',
};

let _mindSseSubscribed = false;

function subscribeMindSse() {
    // Subscribe once — init() may be called more than once as views cycle.
    if (_mindSseSubscribed) return;
    _mindSseSubscribed = true;
    onBusEvent(BusEvents.MIND_CHANGED, (data) => {
        if (!data || !container || !container.isConnected) return;
        // Skip when Mind view isn't currently visible (offsetParent is null
        // when the element is display:none or its parent is). No point
        // re-fetching for a view the user can't see.
        if (container.offsetParent === null) return;
        const { domain, scope } = data;
        if (!domain || !scope) return;
        if (scope !== currentScope) return;
        if (_DOMAIN_FOR_TAB[activeTab] !== domain) return;
        // SSE says backing data for this domain/scope changed — invalidate
        // any domain-specific caches so the next render re-fetches.
        if (domain === 'memory') _invalidateMemCache();
        renderContent();
    });
}

async function _scopeForActiveChatTab(tab) {
    // Returns the scope name the active chat uses for `tab`'s domain, or
    // null if it can't be determined (caller keeps currentScope as-is).
    const settingKey = _TAB_TO_SCOPE_KEY[tab];
    if (!settingKey) return null;
    try {
        const resp = await fetch('/api/status');
        if (!resp.ok) return null;
        const data = await resp.json();
        const raw = (data.chat_settings || {})[settingKey];
        // 'none' means the scope dimension is disabled for this chat —
        // the AI can't write there. Fall back to 'default' so the user
        // can still see SOMETHING (global + default overlay).
        if (!raw || raw === 'none') return 'default';
        return raw;
    } catch {
        return null;
    }
}

// ─── Main Render ─────────────────────────────────────────────────────────────

async function render() {
    // Fetch all scope types in parallel
    const [memResp, knowResp, peopleResp, goalResp] = await Promise.allSettled([
        fetch('/api/memory/scopes').then(r => r.ok ? r.json() : null),
        fetch('/api/knowledge/scopes').then(r => r.ok ? r.json() : null),
        fetch('/api/knowledge/people/scopes').then(r => r.ok ? r.json() : null),
        fetch('/api/goals/scopes').then(r => r.ok ? r.json() : null)
    ]);
    memoryScopeCache = memResp.status === 'fulfilled' && memResp.value ? memResp.value.scopes || [] : [];
    knowledgeScopeCache = knowResp.status === 'fulfilled' && knowResp.value ? knowResp.value.scopes || [] : [];
    peopleScopeCache = peopleResp.status === 'fulfilled' && peopleResp.value ? peopleResp.value.scopes || [] : [];
    goalScopeCache = goalResp.status === 'fulfilled' && goalResp.value ? goalResp.value.scopes || [] : [];

    container.innerHTML = `
        <div class="mind-view">
            <div class="mind-header">
                <h2>Mind</h2>
                <div class="mind-tabs">
                    <button class="mind-tab${activeTab === 'memories' ? ' active' : ''}" data-tab="memories">Memories</button>
                    <button class="mind-tab${activeTab === 'people' ? ' active' : ''}" data-tab="people">People</button>
                    <button class="mind-tab${activeTab === 'knowledge' ? ' active' : ''}" data-tab="knowledge">Human Knowledge</button>
                    <button class="mind-tab${activeTab === 'ai-notes' ? ' active' : ''}" data-tab="ai-notes">AI Knowledge</button>
                    <button class="mind-tab${activeTab === 'goals' ? ' active' : ''}" data-tab="goals">Goals</button>
                </div>
                <div class="mind-scope-bar">
                    <label>Scope:</label>
                    <select id="mind-scope"></select>
                    <button class="mind-btn-sm" id="mind-new-scope" title="New scope">+</button>
                    <button class="mind-btn-sm mind-del-scope-btn" id="mind-del-scope" title="Delete scope">&#x1F5D1;</button>
                </div>
            </div>
            <div class="mind-body">
                <div id="mind-content" class="mind-content"></div>
            </div>
        </div>
    `;

    // Tab switching — re-sync scope to the active chat's setting for the new
    // tab's domain. Each tab maps to a different scope axis (memories →
    // memory_scope, goals → goal_scope, etc). Without this, switching tabs
    // keeps the prior tab's scope and the user sees mismatched data.
    container.querySelectorAll('.mind-tab').forEach(btn => {
        btn.addEventListener('click', async () => {
            container.querySelectorAll('.mind-tab').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            activeTab = btn.dataset.tab;
            memoryPage = 0;
            const chatScope = await _scopeForActiveChatTab(activeTab);
            const scopeChanged = chatScope && chatScope !== currentScope;
            if (chatScope) currentScope = chatScope;
            // If the auto-sync changed scope, reset memory-card filter state
            // for the same reason as the manual scope dropdown handler. H17.
            if (scopeChanged) {
                _memSearch = ''; _memSort = 'newest';
                _memLabelFilter = null; _memShowAll = false;
            }
            updateScopeDropdown();
            renderContent();
        });
    });

    // Scope change
    container.querySelector('#mind-scope').addEventListener('change', (e) => {
        currentScope = e.target.value;
        memoryPage = 0;
        // Reset memory-card filter state — without this, typing "boss" in
        // 'work' scope and switching to 'home' lands on Memories with the
        // search box still saying "boss" and zero results, looks broken.
        // Witch-hunt 2026-04-21 finding H17.
        _memSearch = '';
        _memSort = 'newest';
        _memLabelFilter = null;
        _memShowAll = false;
        renderContent();
    });

    // New scope button — creates across all backends
    container.querySelector('#mind-new-scope').addEventListener('click', async () => {
        const name = prompt('New scope name (lowercase, no spaces):');
        if (!name) return;
        const clean = name.trim().toLowerCase().replace(/[^a-z0-9_]/g, '');
        if (!clean || clean.length > 32) {
            ui.showToast('Invalid name', 'error');
            return;
        }
        const apis = [
            '/api/memory/scopes',
            '/api/knowledge/scopes',
            '/api/knowledge/people/scopes',
            '/api/goals/scopes'
        ];
        try {
            const results = await Promise.allSettled(apis.map(url =>
                fetch(url, {
                    method: 'POST',
                    headers: csrfHeaders({ 'Content-Type': 'application/json' }),
                    body: JSON.stringify({ name: clean })
                })
            ));
            const caches = [memoryScopeCache, knowledgeScopeCache, peopleScopeCache, goalScopeCache];
            const labels = ['memory', 'knowledge', 'people', 'goals'];
            const newScope = { name: clean, count: 0 };
            let okCount = 0;
            const failed = [];
            results.forEach((r, i) => {
                if (r.status === 'fulfilled' && r.value.ok) {
                    okCount++;
                    if (!caches[i].find(s => s.name === clean)) caches[i].push(newScope);
                } else {
                    failed.push(labels[i]);
                    console.warn(`Scope create failed for ${labels[i]}:`, r.status === 'fulfilled' ? r.value.status : r.reason);
                }
            });
            if (okCount > 0) {
                currentScope = clean;
                updateScopeDropdown();
                renderContent();
                if (failed.length) {
                    ui.showToast(`Created ${clean} (failed: ${failed.join(', ')})`, 'warning');
                } else {
                    ui.showToast(`Created: ${clean}`, 'success');
                }
            } else {
                ui.showToast('Failed to create scope', 'error');
            }
        } catch (e) { ui.showToast('Failed', 'error'); }
    });

    // Delete scope button — deletes from all backends
    container.querySelector('#mind-del-scope').addEventListener('click', () => {
        if (currentScope === 'default') {
            ui.showToast('Cannot delete the default scope', 'error');
            return;
        }
        // Count items across all backends for this scope
        const memCount = memoryScopeCache.find(s => s.name === currentScope)?.count || 0;
        const knowCount = knowledgeScopeCache.find(s => s.name === currentScope)?.count || 0;
        const peopleCount = peopleScopeCache.find(s => s.name === currentScope)?.count || 0;
        const goalCount = goalScopeCache.find(s => s.name === currentScope)?.count || 0;
        const totalCount = memCount + knowCount + peopleCount + goalCount;

        showDeleteScopeConfirmation(currentScope, 'items (memories, knowledge, people, goals)', totalCount);
    });

    updateScopeDropdown();
    await renderContent();
}

function getAllScopes() {
    // Union of all scope names across all backends
    const map = {};
    for (const cache of [memoryScopeCache, knowledgeScopeCache, peopleScopeCache, goalScopeCache]) {
        for (const s of cache) {
            if (!map[s.name]) map[s.name] = 0;
            map[s.name] += s.count || 0;
        }
    }
    // Ensure global always exists
    if (!map['global']) map['global'] = 0;
    // Sort: default first, global second, then alphabetical
    return Object.entries(map)
        .sort(([a], [b]) => {
            if (a === 'default') return -1;
            if (b === 'default') return 1;
            if (a === 'global') return -1;
            if (b === 'global') return 1;
            return a.localeCompare(b);
        })
        .map(([name, count]) => ({ name, count }));
}

function updateScopeDropdown() {
    const sel = container.querySelector('#mind-scope');
    if (!sel) return;
    const scopes = getAllScopes();
    sel.innerHTML = scopes.map(s =>
        `<option value="${s.name}"${s.name === currentScope ? ' selected' : ''}>${s.name} (${s.count})</option>`
    ).join('');
    // If current scope not in list, reset to default
    if (sel.value !== currentScope && scopes.length) {
        currentScope = scopes.find(s => s.name === 'default') ? 'default' : scopes[0].name;
        sel.value = currentScope;
    }
}

async function renderContent() {
    const el = container.querySelector('#mind-content');
    if (!el) return;

    try {
        switch (activeTab) {
            case 'memories': await renderMemories(el); break;
            case 'people': await renderPeople(el); break;
            case 'knowledge': await renderKnowledge(el, 'user'); break;
            case 'ai-notes': await renderKnowledge(el, 'ai'); break;
            case 'goals': await renderGoals(el); break;
        }
    } catch (e) {
        el.innerHTML = `<div class="mind-empty">Failed to load: ${e.message}</div>`;
    }
}

// ─── Memories Tab ────────────────────────────────────────────────────────────

// ─── Memory cards view (TODO L138 — UX overhaul, 2026-04-21) ─────────────────
//
// Replaces the old grouped-by-label accordion with a flat card list driven by
// search, sort, and top-N label chips (option B from the design discussion).
// Cards show the full memory content — no truncation, the 512-char save cap is
// the bound. Private rows render with a plaintext private_key pill so the user
// can see the gating word at a glance.

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

// Hash a label string into a hue so each label gets a stable color across
// renders. Light visual distinction without forcing taxonomy.
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
    // Stash private_key on the card via data-attr so delete can pass it
    // through. The user already sees the plaintext key on this UI surface;
    // the gate is for AI tool calls, not for the user's own privileged view.
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
    /* container-type lets stats below query THIS element's width (not the
       viewport) — Mind panel can be sub-viewport when the chat sidebar is
       open, so viewport media queries aren't accurate. Stats wrap to their
       own line below ~520px container width. */
    container-type: inline-size;
    display: flex; align-items: center; gap: 10px; margin-bottom: 14px;
    flex-wrap: wrap;
}
.mind-mem-search-wrap {
    position: relative; flex: 1 1 200px; min-width: 0;
}
.mind-mem-sort { flex-shrink: 0; }
.mind-mem-stats-inline { flex-shrink: 0; }
@container (max-width: 520px) {
    .mind-mem-stats-inline {
        /* Drop to own line below the search/sort row when narrow. */
        flex-basis: 100%; margin-left: 0; margin-top: 2px;
        justify-content: flex-end;
    }
}
.mind-mem-search-wrap::before {
    content: '⌕'; position: absolute; left: 10px; top: 50%; transform: translateY(-50%);
    font-size: 13px; color: var(--text-muted, #888); pointer-events: none;
}
.mind-mem-search {
    width: 100%; background: var(--bg-secondary, #1a1b2e); color: var(--text, #e1e1e6);
    border: 1px solid var(--border, #333); border-radius: 6px;
    padding: 7px 12px 7px 30px; font-size: 13px; outline: none;
}
.mind-mem-search:focus { border-color: var(--accent, #4a7); }
.mind-mem-sort {
    /* width:auto overrides the global "select { width:100% }" rule from
       shared.css. Without this the sort dropdown eats the controls row. */
    width: auto !important;
    background: var(--bg-secondary, #1a1b2e); color: var(--text, #e1e1e6);
    border: 1px solid var(--border, #333); border-radius: 6px;
    padding: 6px 10px; font-size: 12px; cursor: pointer; outline: none;
}
.mind-mem-chips {
    display: flex; flex-wrap: wrap; gap: 4px; margin-bottom: 14px;
}
.mind-mem-chip {
    padding: 4px 10px; font-size: 11px; border-radius: 4px; cursor: pointer;
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
    font-size: 11px; font-family: monospace; color: var(--text-muted, #888);
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
.mind-mem-header {
    display: flex; align-items: center; gap: 8px; margin-bottom: 7px; flex-wrap: wrap;
}
.mind-mem-label {
    font-size: 10px; padding: 2px 8px; border-radius: 3px; font-family: monospace;
    letter-spacing: 0.04em;
}
.mind-mem-key {
    font-size: 10px; padding: 2px 8px; border-radius: 3px; font-family: monospace;
    background: hsla(40, 80%, 18%, 1); color: hsl(40, 90%, 70%);
    border: 1px solid hsl(40, 70%, 38%);
}
.mind-mem-time {
    font-size: 10px; color: var(--text-muted, #888); font-family: monospace; margin-left: auto;
}
.mind-mem-id { font-size: 9px; color: var(--text-muted, #888); font-family: monospace; opacity: 0.5; }
.mind-mem-content {
    font-size: 13px; color: var(--text, #e1e1e6); line-height: 1.55; word-break: break-word;
}
.mind-mem-actions {
    position: absolute; top: 8px; right: 8px; display: flex; gap: 4px;
    opacity: 0; transition: opacity 0.15s;
}
.mind-mem-card:hover .mind-mem-actions { opacity: 1; }
.mind-mem-show-more {
    margin-top: 10px; padding: 8px; text-align: center; font-size: 12px;
    color: var(--text-muted, #888); cursor: pointer;
    background: var(--bg-secondary, #1a1b2e); border: 1px dashed var(--border, #333); border-radius: 6px;
}
.mind-mem-show-more:hover { color: var(--text, #e1e1e6); border-color: var(--accent, #4a7); }
.mind-mem-empty { padding: 24px; text-align: center; color: var(--text-muted, #888); font-style: italic; }
</style>
`;

async function renderMemories(el) {
    // Cache-first: if we already have rows for currentScope, skip the fetch
    // and render in-place. Filter/search/sort handlers below call this same
    // function; they hit the warm cache, nothing goes to the network. Cache
    // populates on cold scope and invalidates on scope change / MIND_CHANGED
    // event / explicit invalidate. Witch-hunt 2026-04-21 R2.
    if (_memCache.scope !== currentScope || _memCache.rows === null) {
        try {
            const resp = await fetch(`/api/memory/list?scope=${encodeURIComponent(currentScope)}`);
            if (!resp.ok) { el.innerHTML = '<div class="mind-empty">Failed to load memories</div>'; return; }
            const data = await resp.json();
            const groups = data.memories || {};
            // Flatten to one array — server returns grouped by label, we want
            // a single sortable/filterable list.
            const rows = [];
            for (const arr of Object.values(groups)) for (const m of arr) rows.push(m);
            _memCache = { scope: currentScope, rows };
        } catch (e) {
            el.innerHTML = `<div class="mind-empty">Failed to load memories: ${e.message}</div>`;
            return;
        }
    }
    _renderMemoriesFromCache(el);
}

function _renderMemoriesFromCache(el) {
    // Preserve focus across the el.innerHTML rewrite below — without this,
    // typing in the search input kills focus after the first keystroke.
    const focusedEl = document.activeElement;
    const refocus = focusedEl && el.contains(focusedEl) && focusedEl.id
        ? {
            id: focusedEl.id,
            selStart: focusedEl.selectionStart ?? null,
            selEnd: focusedEl.selectionEnd ?? null,
        }
        : null;

    const all = _memCache.rows || [];

    // Top-N label chips (option B): most-frequent labels in this scope.
    const labelCounts = {};
    for (const m of all) {
        const k = m.label || 'unlabeled';
        labelCounts[k] = (labelCounts[k] || 0) + 1;
    }
    const topLabels = Object.entries(labelCounts)
        .sort((a, b) => b[1] - a[1])
        .slice(0, MEM_TOP_LABELS);

    // Stats ribbon — minimal, three numbers.
    const totalCount = all.length;
    const privateCount = all.filter(m => m.private_key).length;
    const labelVariety = Object.keys(labelCounts).length;

    // Apply current filter state.
    const search = _memSearch.trim().toLowerCase();
    let filtered = all;
    if (_memLabelFilter) {
        filtered = filtered.filter(m => (m.label || 'unlabeled') === _memLabelFilter);
    }
    if (search) {
        filtered = filtered.filter(m =>
            (m.content || '').toLowerCase().includes(search) ||
            (m.label || '').toLowerCase().includes(search) ||
            (m.private_key || '').toLowerCase().includes(search)
        );
    }
    // Sort.
    const sortFns = {
        newest: (a, b) => (new Date(b.timestamp) - new Date(a.timestamp)),
        oldest: (a, b) => (new Date(a.timestamp) - new Date(b.timestamp)),
        longest: (a, b) => (b.content || '').length - (a.content || '').length,
        shortest: (a, b) => (a.content || '').length - (b.content || '').length,
        label: (a, b) => (a.label || 'zz').localeCompare(b.label || 'zz'),
    };
    filtered.sort(sortFns[_memSort] || sortFns.newest);

    // Cap visible cards to keep render fast on large scopes; reveal-all button
    // for the rest. 200 is enough that scroll feels natural; show-all is one
    // click away.
    const visible = _memShowAll ? filtered : filtered.slice(0, MEM_INITIAL_LIMIT);
    const hidden = filtered.length - visible.length;

    const desc = '<div class="mind-tab-desc">Short snippets the AI saves during conversation. Search, filter, or click chips to narrow.</div>';
    const toolbar = `<div class="mind-toolbar">
        <button class="mind-btn" id="mind-find-dups">Find Duplicates</button>
        <button class="mind-btn" id="mind-export-memories">Export</button>
        <button class="mind-btn" id="mind-import-memories">Import</button>
    </div>`;

    if (!totalCount) {
        el.innerHTML = MEM_CARD_STYLES + desc + toolbar +
            '<div class="mind-mem-empty">No memories in this scope yet.</div>';
        _bindMemoryIO(el);
        return;
    }

    // Build chips. "All" chip + top labels + a clear-filter when one is active.
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

    // Stats fold into the controls row (margin-left:auto floats them right
    // of the sort dropdown). Drops one whole vertical row from the layout —
    // search/sort/stats live on a single line. Krem feedback 2026-04-21.
    const statsInline = `
        <span class="mind-mem-stats-inline">
            <span><strong>${totalCount}</strong> mem</span>
            <span>·</span>
            <span><strong>${labelVariety}</strong> labels</span>
            ${privateCount > 0 ? `<span>·</span><span><strong>${privateCount}</strong> private</span>` : ''}
            <span>·</span>
            <span class="mind-mem-stats-scope">${escHtml(currentScope)}</span>
        </span>
    `;

    el.innerHTML = MEM_CARD_STYLES + desc + toolbar + `
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

    // Search wires up live (debounced is overkill at this scale).
    el.querySelector('#mind-mem-search')?.addEventListener('input', e => {
        _memSearch = e.target.value;
        _memShowAll = false;  // reset reveal when filter changes
        renderMemories(el);
    });
    // Sort dropdown.
    el.querySelector('#mind-mem-sort')?.addEventListener('change', e => {
        _memSort = e.target.value;
        renderMemories(el);
    });
    // Chips — clicking the active one clears it.
    el.querySelectorAll('.mind-mem-chip').forEach(chip => {
        chip.addEventListener('click', () => {
            const lbl = chip.dataset.label || null;
            _memLabelFilter = (lbl === _memLabelFilter || lbl === '') ? null : lbl;
            _memShowAll = false;
            renderMemories(el);
        });
    });
    // Show-more reveal.
    el.querySelector('#mind-mem-show-all')?.addEventListener('click', () => {
        _memShowAll = true;
        renderMemories(el);
    });

    // Edit handlers
    el.querySelectorAll('.mind-edit-memory').forEach(btn => {
        btn.addEventListener('click', () => {
            const id = parseInt(btn.dataset.id);
            const card = btn.closest('.mind-mem-card');
            const content = card.querySelector('.mind-mem-content').textContent;
            showMemoryEditModal(el, id, content);
        });
    });

    // Delete handlers — pass private_key from the card's data-attr if set
    // so the user can delete private rows from their own UI (gate is for
    // AI tool callers, not the authenticated user).
    el.querySelectorAll('.mind-del-memory').forEach(btn => {
        btn.addEventListener('click', async () => {
            if (!confirm('Delete this memory?')) return;
            const id = parseInt(btn.dataset.id);
            const card = btn.closest('.mind-mem-card');
            const pk = card?.dataset.privateKey || '';
            const url = `/api/memory/${id}?scope=${encodeURIComponent(currentScope)}`
                + (pk ? `&private_key=${encodeURIComponent(pk)}` : '');
            try {
                const resp = await fetch(url, { method: 'DELETE', headers: csrfHeaders() });
                if (resp.ok) {
                    ui.showToast('Deleted', 'success');
                    _invalidateMemCache();
                    await renderMemories(el);
                }
            } catch (e) { ui.showToast('Failed', 'error'); }
        });
    });

    _bindMemoryIO(el);

    // Restore focus + cursor on the same-id element after the innerHTML
    // rewrite. If the originally-focused element no longer exists (e.g. the
    // user clicked something that's now gone), this is a no-op.
    if (refocus) {
        const restored = el.querySelector(`#${refocus.id}`);
        if (restored) {
            restored.focus();
            if (refocus.selStart !== null && typeof restored.setSelectionRange === 'function') {
                try { restored.setSelectionRange(refocus.selStart, refocus.selEnd); } catch { /* element type doesn't support selection */ }
            }
        }
    }
}

function _bindMemoryIO(el) {
    el.querySelector('#mind-export-memories')?.addEventListener('click', async () => {
        try {
            const resp = await fetch(`/api/memory/export?scope=${encodeURIComponent(currentScope)}`);
            if (!resp.ok) throw new Error('Export failed');
            const data = await resp.json();
            showExportDialog({
                type: 'Memories',
                name: `${currentScope} (${data.count})`,
                filename: `memories-${currentScope}.json`,
                data,
            });
        } catch (e) { ui.showToast(e.message, 'error'); }
    });

    el.querySelector('#mind-find-dups')?.addEventListener('click', async () => {
        try {
            const btn = el.querySelector('#mind-find-dups');
            btn.textContent = 'Scanning...';
            btn.disabled = true;
            const resp = await fetch(`/api/memory/duplicates?scope=${encodeURIComponent(currentScope)}`);
            btn.textContent = 'Find Duplicates';
            btn.disabled = false;
            if (!resp.ok) throw new Error('Scan failed');
            const data = await resp.json();
            if (!data.pairs.length) {
                ui.showToast('No duplicates found', 'success');
                return;
            }
            _showDuplicatesModal(el, data.pairs);
        } catch (e) { ui.showToast(e.message, 'error'); }
    });

    el.querySelector('#mind-import-memories')?.addEventListener('click', () => {
        showImportDialog({
            type: 'Memories',
            existingNames: [],
            validate: (d) => {
                if (d.entries && Array.isArray(d.entries)) return null;
                return 'Invalid format: needs entries array';
            },
            getName: (d) => d.scope || currentScope,
            onImport: async (data, { name }) => {
                const resp = await fetch('/api/memory/import', {
                    method: 'POST',
                    headers: csrfHeaders({ 'Content-Type': 'application/json' }),
                    body: JSON.stringify({ entries: data.entries, scope: currentScope }),
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
    const existing = document.querySelector('.mind-modal-overlay');
    if (existing) existing.remove();

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
                            <div style="font-size:11px;color:var(--text-muted);margin-bottom:4px">Keep (oldest)</div>
                            ${escHtml(pair.keep.content)}
                            ${pair.keep.label ? `<div style="margin-top:6px;font-size:11px;color:var(--text-muted)">Label: ${escHtml(pair.keep.label)}</div>` : ''}
                        </div>
                        <div style="flex:1;padding:10px;background:var(--bg-tertiary);border-radius:var(--radius);font-size:var(--font-sm);opacity:0.7">
                            <div style="font-size:11px;color:var(--text-muted);margin-bottom:4px">Remove (newer)</div>
                            ${escHtml(pair.remove.content)}
                            ${pair.remove.label ? `<div style="margin-top:6px;font-size:11px;color:var(--text-muted)">Label: ${escHtml(pair.remove.label)}</div>` : ''}
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
            overlay.remove();
            _invalidateMemCache();
            renderMemories(el);
        });

        overlay.querySelector('#dup-delete').addEventListener('click', async () => {
            try {
                await fetch(`/api/memory/${pair.remove.id}?scope=${encodeURIComponent(currentScope)}`, { method: 'DELETE', headers: csrfHeaders() });
                currentIdx++;
                renderPair();
            } catch { ui.showToast('Delete failed', 'error'); }
        });

        overlay.querySelector('#dup-combine').addEventListener('click', async () => {
            // Combine: merge both texts into the older memory, delete the newer
            const combined = pair.keep.content + '\n' + pair.remove.content;
            try {
                await fetch(`/api/memory/${pair.keep.id}`, {
                    method: 'PUT',
                    headers: csrfHeaders({ 'Content-Type': 'application/json' }),
                    body: JSON.stringify({ content: combined, scope: currentScope }),
                });
                await fetch(`/api/memory/${pair.remove.id}?scope=${encodeURIComponent(currentScope)}`, { method: 'DELETE', headers: csrfHeaders() });
                currentIdx++;
                renderPair();
            } catch { ui.showToast('Combine failed', 'error'); }
        });

        overlay.querySelector('#dup-skip').addEventListener('click', () => {
            currentIdx++;
            renderPair();
        });

        overlay.querySelector('#dup-skip-all').addEventListener('click', () => {
            overlay.remove();
            _invalidateMemCache();
            renderMemories(el);
        });
    }

    renderPair();
}

function showMemoryEditModal(el, memoryId, content) {
    const existing = document.querySelector('.mind-modal-overlay');
    if (existing) existing.remove();

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

    // Focus textarea
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
                body: JSON.stringify({ content: newContent, scope: currentScope })
            });
            if (resp.ok) {
                close();
                ui.showToast('Memory updated', 'success');
                _invalidateMemCache();
                await renderMemories(el);
            } else {
                const err = await resp.json();
                ui.showToast(err.detail || 'Failed', 'error');
            }
        } catch (e) { ui.showToast('Failed', 'error'); }
    });
}

// ─── People Tab ──────────────────────────────────────────────────────────────

async function renderPeople(el) {
    const resp = await fetch(`/api/knowledge/people?scope=${encodeURIComponent(currentScope)}`);
    if (!resp.ok) { el.innerHTML = '<div class="mind-empty">Failed to load</div>'; return; }
    const data = await resp.json();
    const people = data.people || [];

    el.innerHTML = `
        <div class="mind-tab-desc">Contacts the AI learns about through conversation. Searchable by name, relationship, or notes.</div>
        <div class="mind-toolbar">
            <button class="mind-btn" id="mind-add-person">+ Add Person</button>
            <button class="mind-btn" id="mind-import-vcf">Import VCF</button>
            <button class="mind-btn" id="mind-export-people">Export</button>
            <button class="mind-btn" id="mind-import-people">Import</button>
            <input type="file" id="mind-vcf-input" accept=".vcf" style="display:none">
        </div>
        ${people.length ? `<div class="mind-people-grid">
            ${people.map(p => `
                <div class="mind-person-card" data-id="${p.id}">
                    <div class="mind-person-name">${escHtml(p.name)}${p.email_whitelisted ? ' <span title="Email allowed" style="font-size:12px">&#x1F4E7;</span>' : ''}</div>
                    ${p.relationship ? `<div class="mind-person-rel">${escHtml(p.relationship)}</div>` : ''}
                    <div class="mind-person-details">
                        ${p.phone ? `<div>&#x1F4DE; ${escHtml(p.phone)}</div>` : ''}
                        ${p.email ? `<div>&#x2709; ${escHtml(p.email)}</div>` : ''}
                        ${p.address ? `<div>&#x1F4CD; ${escHtml(p.address)}</div>` : ''}
                    </div>
                    ${p.notes ? `<div class="mind-person-notes">${escHtml(p.notes)}</div>` : ''}
                    <div class="mind-person-actions">
                        <button class="mind-btn-sm mind-edit-person" data-id="${p.id}">Edit</button>
                        <button class="mind-btn-sm mind-del-person" data-id="${p.id}">Delete</button>
                    </div>
                </div>
            `).join('')}
        </div>` : '<div class="mind-empty">No contacts saved</div>'}
    `;

    el.querySelector('#mind-add-person')?.addEventListener('click', () => showPersonModal(el));

    const vcfInput = el.querySelector('#mind-vcf-input');
    el.querySelector('#mind-import-vcf')?.addEventListener('click', () => vcfInput?.click());
    vcfInput?.addEventListener('change', async (e) => {
        const file = e.target.files?.[0];
        if (!file) return;
        const form = new FormData();
        form.append('file', file);
        form.append('scope', currentScope);
        try {
            const resp = await fetch('/api/knowledge/people/import-vcf', { method: 'POST', headers: csrfHeaders(), body: form });
            if (!resp.ok) throw new Error('Upload failed');
            const result = await resp.json();
            let msg = `Imported ${result.imported} of ${result.total_in_file} contacts`;
            if (result.skipped_count > 0) {
                msg += `\nSkipped ${result.skipped_count} duplicates:`;
                result.skipped.forEach(s => { msg += `\n  - ${s}`; });
                if (result.skipped_count > result.skipped.length) msg += `\n  ... and ${result.skipped_count - result.skipped.length} more`;
            }
            ui.showToast(msg, result.imported > 0 ? 'success' : 'info');
            await renderPeople(el);
        } catch (err) { ui.showToast('Import failed: ' + err.message, 'error'); }
        vcfInput.value = '';
    });

    el.querySelectorAll('.mind-edit-person').forEach(btn => {
        btn.addEventListener('click', () => {
            const p = people.find(x => x.id === parseInt(btn.dataset.id));
            if (p) showPersonModal(el, p);
        });
    });

    el.querySelectorAll('.mind-del-person').forEach(btn => {
        btn.addEventListener('click', async () => {
            if (!confirm('Delete this contact?')) return;
            try {
                const resp = await fetch(`/api/knowledge/people/${btn.dataset.id}`, { method: 'DELETE', headers: csrfHeaders() });
                if (resp.ok) {
                    ui.showToast('Deleted', 'success');
                    await renderPeople(el);
                }
            } catch (e) { ui.showToast('Failed', 'error'); }
        });
    });

    // Export/Import people
    el.querySelector('#mind-export-people')?.addEventListener('click', async () => {
        try {
            const resp = await fetch(`/api/knowledge/people/export?scope=${encodeURIComponent(currentScope)}`);
            if (!resp.ok) throw new Error('Export failed');
            const data = await resp.json();
            showExportDialog({
                type: 'People',
                name: `${currentScope} (${data.count})`,
                filename: `people-${currentScope}.json`,
                data,
            });
        } catch (e) { ui.showToast(e.message, 'error'); }
    });

    el.querySelector('#mind-import-people')?.addEventListener('click', () => {
        showImportDialog({
            type: 'People',
            existingNames: [],
            validate: (d) => {
                if (d.entries && Array.isArray(d.entries)) return null;
                return 'Invalid format: needs entries array';
            },
            getName: (d) => d.scope || currentScope,
            onImport: async (data) => {
                const resp = await fetch('/api/knowledge/people/import', {
                    method: 'POST',
                    headers: csrfHeaders({ 'Content-Type': 'application/json' }),
                    body: JSON.stringify({ entries: data.entries, scope: currentScope }),
                });
                if (!resp.ok) throw new Error('Import failed');
                const result = await resp.json();
                ui.showToast(`Imported ${result.imported} contacts, ${result.skipped} duplicates skipped`, 'success');
            },
            onDone: async () => { await renderPeople(el); },
        });
    });
}

function showPersonModal(el, person = null) {
    const existing = document.querySelector('.mind-modal-overlay');
    if (existing) existing.remove();

    const overlay = document.createElement('div');
    overlay.className = 'pr-modal-overlay mind-modal-overlay';
    overlay.innerHTML = `
        <div class="pr-modal">
            <div class="pr-modal-header">
                <h3>${person ? 'Edit' : 'Add'} Contact</h3>
                <button class="mind-btn-sm mind-modal-close">&#x2715;</button>
            </div>
            <div class="pr-modal-body">
                <div class="mind-form">
                    <input type="text" id="mp-name" placeholder="Name *" value="${escAttr(person?.name || '')}">
                    <input type="text" id="mp-relationship" placeholder="Relationship" value="${escAttr(person?.relationship || '')}">
                    <input type="text" id="mp-phone" placeholder="Phone" value="${escAttr(person?.phone || '')}">
                    <input type="text" id="mp-email" placeholder="Email" value="${escAttr(person?.email || '')}">
                    <input type="text" id="mp-address" placeholder="Address" value="${escAttr(person?.address || '')}">
                    <textarea id="mp-notes" placeholder="Notes" rows="3">${escHtml(person?.notes || '')}</textarea>
                    <label style="display:flex;align-items:center;gap:8px;font-size:13px;color:var(--text-muted);cursor:pointer">
                        <input type="checkbox" id="mp-email-whitelist" ${person?.email_whitelisted ? 'checked' : ''}> Allow AI to send email
                    </label>
                    <button class="mind-btn" id="mp-save">${person ? 'Update' : 'Save'}</button>
                </div>
            </div>
        </div>
    `;

    document.body.appendChild(overlay);

    overlay.querySelector('.mind-modal-close').addEventListener('click', () => overlay.remove());
    setupModalClose(overlay, () => overlay.remove());

    overlay.querySelector('#mp-save').addEventListener('click', async () => {
        const name = overlay.querySelector('#mp-name').value.trim();
        if (!name) { ui.showToast('Name is required', 'error'); return; }

        const body = {
            name,
            relationship: overlay.querySelector('#mp-relationship').value.trim(),
            phone: overlay.querySelector('#mp-phone').value.trim(),
            email: overlay.querySelector('#mp-email').value.trim(),
            address: overlay.querySelector('#mp-address').value.trim(),
            notes: overlay.querySelector('#mp-notes').value.trim(),
            email_whitelisted: overlay.querySelector('#mp-email-whitelist').checked,
            scope: currentScope,
        };
        if (person?.id) body.id = person.id;

        try {
            const resp = await fetch('/api/knowledge/people', {
                method: 'POST',
                headers: csrfHeaders({ 'Content-Type': 'application/json' }),
                body: JSON.stringify(body)
            });
            if (resp.ok) {
                overlay.remove();
                ui.showToast(person ? 'Updated' : 'Saved', 'success');
                await renderPeople(el);
            } else {
                const err = await resp.json();
                ui.showToast(err.detail || 'Failed', 'error');
            }
        } catch (e) { ui.showToast('Failed', 'error'); }
    });
}

// ─── Knowledge / AI Notes Tab ────────────────────────────────────────────────

async function renderKnowledge(el, tabType) {
    const isAI = tabType === 'ai';
    const resp = await fetch(`/api/knowledge/tabs?scope=${encodeURIComponent(currentScope)}&type=${tabType}`);
    if (!resp.ok) { el.innerHTML = '<div class="mind-empty">Failed to load</div>'; return; }
    const data = await resp.json();
    const tabs = data.tabs || [];

    const knDesc = isAI
        ? 'Reference data the AI writes on its own — research, notes, things it learned. You can read and delete, but only the AI creates entries here.'
        : 'Your reference library — upload files, add notes, organize into categories. The AI can search this when the scope is active but cannot edit it.';

    el.innerHTML = `
        <div class="mind-tab-desc">${knDesc}</div>
        <div class="mind-toolbar">
            ${!isAI ? '<button class="mind-btn" id="mind-new-tab">+ New Category</button>' : ''}
            <button class="mind-btn" id="mind-import-tab">Import</button>
            <button class="mind-btn" id="mind-find-dups">Find Duplicates</button>
        </div>
        <div id="mind-dup-results" style="display:none"></div>
        ${tabs.length ? `<div class="mind-list">
            ${tabs.map(t => `
                <details class="mind-accordion">
                    <summary class="mind-accordion-header">
                        <span class="mind-accordion-title">${escHtml(t.name)}</span>
                        <span class="mind-accordion-count">${t.entry_count} entries</span>
                        <button class="mind-btn-sm mind-export-tab" data-id="${t.id}" data-name="${escAttr(t.name)}" title="Export">\u21E9</button>
                        <button class="mind-btn-sm mind-del-tab" data-id="${t.id}" title="Delete category">&#x2715;</button>
                    </summary>
                    <div class="mind-accordion-body">
                        <div class="mind-accordion-inner mind-tab-entries" data-tab-id="${t.id}" data-type="${tabType}">
                            <div class="mind-empty">Click to load entries</div>
                        </div>
                    </div>
                </details>
            `).join('')}
        </div>` : `<div class="mind-empty">No ${isAI ? 'AI notes' : 'knowledge'} in this scope</div>`}
    `;

    // New category button
    el.querySelector('#mind-new-tab')?.addEventListener('click', async () => {
        const name = prompt('Category name:');
        if (!name) return;
        try {
            const resp = await fetch('/api/knowledge/tabs', {
                method: 'POST',
                headers: csrfHeaders({ 'Content-Type': 'application/json' }),
                body: JSON.stringify({ name: name.trim(), scope: currentScope, type: 'user' })
            });
            if (resp.ok) {
                ui.showToast('Category created', 'success');
                await renderKnowledge(el, tabType);
            } else {
                const err = await resp.json();
                ui.showToast(err.detail || 'Failed', 'error');
            }
        } catch (e) { ui.showToast('Failed', 'error'); }
    });

    // Delete category buttons
    el.querySelectorAll('.mind-del-tab').forEach(btn => {
        btn.addEventListener('click', async (e) => {
            e.preventDefault();
            e.stopPropagation();
            const name = btn.closest('.mind-accordion')?.querySelector('.mind-accordion-title')?.textContent || 'this category';
            if (!confirm(`Delete "${name}" and all its entries?`)) return;
            try {
                const resp = await fetch(`/api/knowledge/tabs/${btn.dataset.id}`, { method: 'DELETE', headers: csrfHeaders() });
                if (resp.ok) {
                    ui.showToast('Deleted', 'success');
                    await renderKnowledge(el, tabType);
                }
            } catch (e) { ui.showToast('Failed', 'error'); }
        });
    });

    // Export tab buttons
    el.querySelectorAll('.mind-export-tab').forEach(btn => {
        btn.addEventListener('click', async (e) => {
            e.preventDefault();
            e.stopPropagation();
            const tabId = parseInt(btn.dataset.id);
            const tabName = btn.dataset.name;
            try {
                const resp = await fetch(`/api/knowledge/tabs/${tabId}/export?scope=${encodeURIComponent(currentScope)}`);
                if (!resp.ok) throw new Error('Export failed');
                const data = await resp.json();
                showExportDialog({
                    type: 'Knowledge Tab',
                    name: `${tabName} (${data.count} entries)`,
                    filename: `knowledge-${tabName.replace(/\s+/g, '_')}.json`,
                    data,
                });
            } catch (e) { ui.showToast(e.message, 'error'); }
        });
    });

    // Import tab
    el.querySelector('#mind-import-tab')?.addEventListener('click', () => {
        showImportDialog({
            type: 'Knowledge Tab',
            overwrites: [
                { key: 'overwrite', label: 'Overwrite if tab already exists' },
            ],
            existingNames: tabs.map(t => t.name),
            validate: (d) => {
                if (d.entries && Array.isArray(d.entries) && d.name) return null;
                return 'Invalid format: needs name and entries array';
            },
            getName: (d) => d.name || 'imported',
            onImport: async (data, { name, overwrites }) => {
                const resp = await fetch('/api/knowledge/tabs/import', {
                    method: 'POST',
                    headers: csrfHeaders({ 'Content-Type': 'application/json' }),
                    body: JSON.stringify({
                        name, entries: data.entries, scope: currentScope,
                        description: data.description, tab_type: data.tab_type || tabType,
                        overwrite: overwrites.overwrite || false,
                    }),
                });
                if (!resp.ok) throw new Error('Import failed');
                const result = await resp.json();
                const msg = result.merged
                    ? `Merged ${result.imported} entries, ${result.skipped} duplicates skipped`
                    : `Imported ${result.imported} entries`;
                ui.showToast(msg, 'success');
            },
            onDone: async () => { await renderKnowledge(el, tabType); },
        });
    });

    // Find Duplicates
    el.querySelector('#mind-find-dups')?.addEventListener('click', async () => {
        const btn = el.querySelector('#mind-find-dups');
        const resultsDiv = el.querySelector('#mind-dup-results');
        if (!resultsDiv) return;

        btn.disabled = true;
        btn.textContent = 'Scanning...';
        resultsDiv.style.display = 'block';
        resultsDiv.innerHTML = '<div class="mind-empty">Scanning for duplicates...</div>';

        try {
            const resp = await fetch(`/api/knowledge/dedup?scope=${encodeURIComponent(currentScope)}`);
            if (!resp.ok) throw new Error('Scan failed');
            const data = await resp.json();
            const dups = data.duplicates || {};
            const stats = data.stats || {};

            if (stats.total_duplicate_groups === 0) {
                resultsDiv.innerHTML = '<div class="mind-dup-clean">No duplicates found</div>';
                btn.textContent = 'Find Duplicates';
                btn.disabled = false;
                return;
            }

            let html = `<div class="mind-dup-header">Found ${stats.total_duplicate_groups} duplicate group(s) in ${stats.total_entries} entries</div>`;

            // Exact duplicates
            if (dups.exact?.length) {
                html += `<div class="mind-dup-section"><h4>Identical Content (${dups.exact.length})</h4>`;
                for (const group of dups.exact) {
                    const keep = group.entries[0];
                    const remove = group.entries.slice(1);
                    const removeIds = remove.map(e => e.id);
                    html += `<div class="mind-dup-group">
                        <div class="mind-dup-preview">${escHtml(group.preview)}</div>
                        <div class="mind-dup-entries">
                            <div class="mind-dup-entry keep">Keep: ${escHtml(keep.tab_name)}${keep.filename ? ' / ' + escHtml(keep.filename) : ''}</div>
                            ${remove.map(e => `<div class="mind-dup-entry remove">Remove: ${escHtml(e.tab_name)}${e.filename ? ' / ' + escHtml(e.filename) : ''} (id:${e.id})</div>`).join('')}
                        </div>
                        <button class="mind-btn-sm mind-dup-resolve" data-ids='${JSON.stringify(removeIds)}'>Remove ${remove.length} duplicate(s)</button>
                    </div>`;
                }
                html += '</div>';
            }

            // File duplicates
            if (dups.file?.length) {
                html += `<div class="mind-dup-section"><h4>Same File in Multiple Categories (${dups.file.length})</h4>`;
                for (const group of dups.file) {
                    html += `<div class="mind-dup-group">
                        <div class="mind-dup-preview">${escHtml(group.filename)}</div>
                        <div class="mind-dup-entries">
                            ${group.tabs.map(t => `<div class="mind-dup-entry">${escHtml(t.tab_name)} (${t.scope}) — ${t.chunks} chunks</div>`).join('')}
                        </div>
                        <div class="mind-dup-hint">Remove duplicates manually from the category above</div>
                    </div>`;
                }
                html += '</div>';
            }

            // Similar entries
            if (dups.similar?.length) {
                html += `<div class="mind-dup-section"><h4>Similar Content (${dups.similar.length})</h4>`;
                for (const group of dups.similar) {
                    const keep = group.entries[0];
                    const remove = group.entries.slice(1);
                    const removeIds = remove.map(e => e.id);
                    html += `<div class="mind-dup-group">
                        <div class="mind-dup-preview">${escHtml(group.preview)}</div>
                        <div class="mind-dup-entries">
                            <div class="mind-dup-entry keep">Keep: ${escHtml(keep.tab_name)}${keep.filename ? ' / ' + escHtml(keep.filename) : ''}</div>
                            ${remove.map(e => `<div class="mind-dup-entry remove">${(e.score * 100).toFixed(0)}% match: ${escHtml(e.tab_name)}${e.filename ? ' / ' + escHtml(e.filename) : ''}</div>`).join('')}
                        </div>
                        <button class="mind-btn-sm mind-dup-resolve" data-ids='${JSON.stringify(removeIds)}'>Remove ${remove.length} similar duplicate(s)</button>
                    </div>`;
                }
                html += '</div>';
            }

            resultsDiv.innerHTML = html;

            // Wire resolve buttons
            resultsDiv.querySelectorAll('.mind-dup-resolve').forEach(resolveBtn => {
                resolveBtn.addEventListener('click', async () => {
                    const ids = JSON.parse(resolveBtn.dataset.ids);
                    if (!confirm(`Delete ${ids.length} duplicate entry/entries?`)) return;
                    resolveBtn.disabled = true;
                    resolveBtn.textContent = 'Removing...';
                    try {
                        const resp = await fetch('/api/knowledge/dedup/resolve', {
                            method: 'DELETE',
                            headers: csrfHeaders({ 'Content-Type': 'application/json' }),
                            body: JSON.stringify({ ids }),
                        });
                        if (resp.ok) {
                            const result = await resp.json();
                            ui.showToast(`Removed ${result.deleted} duplicate(s)`, 'success');
                            resolveBtn.closest('.mind-dup-group').remove();
                            // Re-render the tab list to update counts
                            await renderKnowledge(el, tabType);
                        }
                    } catch (e) { ui.showToast('Failed to remove', 'error'); }
                });
            });

        } catch (e) {
            resultsDiv.innerHTML = `<div class="mind-empty" style="color:var(--error)">Scan failed: ${escHtml(e.message)}</div>`;
        }
        btn.textContent = 'Find Duplicates';
        btn.disabled = false;
    });

    // Lazy-load entries on accordion open
    el.querySelectorAll('.mind-accordion').forEach(details => {
        details.addEventListener('toggle', async () => {
            if (!details.open) return;
            const inner = details.querySelector('.mind-tab-entries');
            if (!inner || inner.dataset.loaded) return;
            inner.dataset.loaded = 'true';
            await loadEntries(inner, parseInt(inner.dataset.tabId), inner.dataset.type);
        });
    });
}

async function loadEntries(inner, tabId, tabType) {
    const isAI = tabType === 'ai';
    try {
        const resp = await fetch(`/api/knowledge/tabs/${tabId}?scope=${encodeURIComponent(currentScope)}`);
        if (!resp.ok) { inner.innerHTML = '<div class="mind-empty">Failed to load</div>'; return; }
        const data = await resp.json();
        const entries = data.entries || [];

        // Group entries: files first (grouped by filename), then loose entries
        const fileGroups = {};
        const loose = [];
        for (const e of entries) {
            if (e.source_filename) {
                if (!fileGroups[e.source_filename]) fileGroups[e.source_filename] = [];
                fileGroups[e.source_filename].push(e);
            } else {
                loose.push(e);
            }
        }
        const filenames = Object.keys(fileGroups).sort();

        let html = '';

        // File groups
        for (const fname of filenames) {
            const group = fileGroups[fname];
            html += `
                <div class="mind-file-group">
                    <div class="mind-file-header">
                        <span class="mind-file-badge">&#x1F4C4;</span>
                        <span class="mind-file-name">${escHtml(fname)}</span>
                        <span class="mind-file-info">${group.length} chunk${group.length > 1 ? 's' : ''}</span>
                        <button class="mind-btn-sm mind-del-file" data-tab-id="${tabId}" data-filename="${escAttr(fname)}" title="Delete file">&#x2715;</button>
                    </div>
                    ${group.map(e => `
                        <div class="mind-item mind-file-entry" data-id="${e.id}">
                            <div class="mind-item-content">${escHtml(e.content)}</div>
                            <div class="mind-item-actions">
                                <button class="mind-btn-sm mind-edit-entry" data-id="${e.id}" title="Edit">&#x270E;</button>
                                <button class="mind-btn-sm mind-del-entry" data-id="${e.id}" title="Delete chunk">&#x2715;</button>
                            </div>
                        </div>
                    `).join('')}
                </div>
            `;
        }

        // Loose entries
        for (const e of loose) {
            html += `
                <div class="mind-item" data-id="${e.id}">
                    <div class="mind-item-content">${escHtml(e.content)}</div>
                    <div class="mind-item-actions">
                        ${!isAI ? `<button class="mind-btn-sm mind-edit-entry" data-id="${e.id}" title="Edit">&#x270E;</button>` : ''}
                        <button class="mind-btn-sm mind-del-entry" data-id="${e.id}" title="Delete">&#x2715;</button>
                    </div>
                </div>
            `;
        }

        // Action buttons
        if (!isAI) {
            html += `<div class="mind-entry-actions">
                <button class="mind-btn mind-add-entry" data-tab-id="${tabId}">+ Add Entry</button>
                <button class="mind-btn mind-upload-file" data-tab-id="${tabId}">+ Add File</button>
                <input type="file" class="mind-file-input" style="display:none"
                    accept=".txt,.md,.py,.js,.ts,.html,.css,.json,.csv,.xml,.yml,.yaml,.log,.cfg,.ini,.conf,.sh,.bat,.toml,.rs,.go,.java,.c,.cpp,.h,.rb,.php,.sql,.r,.m">
            </div>`;
        }

        if (!entries.length && !html.includes('mind-entry-actions')) {
            html = `<div class="mind-empty">Empty</div>` + html;
        }
        if (!entries.length && isAI) {
            html = `<div class="mind-empty">No AI notes yet</div>`;
        }

        inner.innerHTML = html;

        // Upload file
        inner.querySelectorAll('.mind-upload-file').forEach(btn => {
            const fileInput = btn.parentElement.querySelector('.mind-file-input');
            btn.addEventListener('click', () => fileInput.click());
            fileInput.addEventListener('change', async () => {
                const file = fileInput.files[0];
                if (!file) return;
                const form = new FormData();
                form.append('file', file);
                try {
                    btn.textContent = 'Uploading...';
                    btn.disabled = true;
                    const resp = await fetch(`/api/knowledge/tabs/${btn.dataset.tabId}/upload`, {
                        method: 'POST', headers: csrfHeaders(), body: form
                    });
                    if (resp.ok) {
                        const result = await resp.json();
                        ui.showToast(`Uploaded ${result.filename} (${result.chunks} chunks)`, 'success');
                        inner.dataset.loaded = '';
                        await loadEntries(inner, tabId, tabType);
                    } else {
                        const err = await resp.json();
                        ui.showToast(err.detail || 'Upload failed', 'error');
                        btn.textContent = '+ Add File';
                        btn.disabled = false;
                    }
                } catch (e) {
                    ui.showToast('Upload failed', 'error');
                    btn.textContent = '+ Add File';
                    btn.disabled = false;
                }
                fileInput.value = '';
            });
        });

        // Delete file (all chunks)
        inner.querySelectorAll('.mind-del-file').forEach(btn => {
            btn.addEventListener('click', async () => {
                const fname = btn.dataset.filename;
                if (!confirm(`Delete all chunks from "${fname}"?`)) return;
                try {
                    const resp = await fetch(`/api/knowledge/tabs/${btn.dataset.tabId}/file/${encodeURIComponent(fname)}`, { method: 'DELETE', headers: csrfHeaders() });
                    if (resp.ok) {
                        ui.showToast(`Deleted ${fname}`, 'success');
                        inner.dataset.loaded = '';
                        await loadEntries(inner, tabId, tabType);
                    }
                } catch (e) { ui.showToast('Failed', 'error'); }
            });
        });

        // Add entry
        inner.querySelectorAll('.mind-add-entry').forEach(btn => {
            btn.addEventListener('click', () => {
                showAddEntryModal(inner, parseInt(btn.dataset.tabId), tabType);
            });
        });

        // Edit entry
        inner.querySelectorAll('.mind-edit-entry').forEach(btn => {
            btn.addEventListener('click', async () => {
                const item = btn.closest('.mind-item');
                const content = item.querySelector('.mind-item-content').textContent;
                showEntryEditModal(inner, tabId, tabType, parseInt(btn.dataset.id), content);
            });
        });

        // Delete entry
        inner.querySelectorAll('.mind-del-entry').forEach(btn => {
            btn.addEventListener('click', async () => {
                if (!confirm('Delete this entry?')) return;
                try {
                    const resp = await fetch(`/api/knowledge/entries/${btn.dataset.id}`, { method: 'DELETE', headers: csrfHeaders() });
                    if (resp.ok) {
                        ui.showToast('Deleted', 'success');
                        inner.dataset.loaded = '';
                        await loadEntries(inner, tabId, tabType);
                    }
                } catch (e) { ui.showToast('Failed', 'error'); }
            });
        });
    } catch (e) {
        inner.innerHTML = `<div class="mind-empty">Error: ${e.message}</div>`;
    }
}

function showEntryEditModal(inner, tabId, tabType, entryId, content) {
    const existing = document.querySelector('.mind-modal-overlay');
    if (existing) existing.remove();

    const overlay = document.createElement('div');
    overlay.className = 'pr-modal-overlay mind-modal-overlay';
    overlay.innerHTML = `
        <div class="pr-modal">
            <div class="pr-modal-header">
                <h3>Edit Entry</h3>
                <button class="mind-btn-sm mind-modal-close">&#x2715;</button>
            </div>
            <div class="pr-modal-body">
                <div class="mind-form">
                    <textarea id="me-content" rows="12" style="min-height:200px">${escHtml(content)}</textarea>
                    <div style="display:flex;justify-content:flex-end;gap:8px">
                        <button class="mind-btn mind-modal-cancel">Cancel</button>
                        <button class="mind-btn" id="me-save" style="border-color:var(--trim,var(--accent-blue))">Save</button>
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

    const textarea = overlay.querySelector('#me-content');
    textarea.focus();
    textarea.setSelectionRange(textarea.value.length, textarea.value.length);

    overlay.querySelector('#me-save').addEventListener('click', async () => {
        const newContent = textarea.value.trim();
        if (!newContent) { ui.showToast('Content cannot be empty', 'error'); return; }
        if (newContent === content) { close(); return; }
        try {
            const resp = await fetch(`/api/knowledge/entries/${entryId}`, {
                method: 'PUT',
                headers: csrfHeaders({ 'Content-Type': 'application/json' }),
                body: JSON.stringify({ content: newContent })
            });
            if (resp.ok) {
                close();
                ui.showToast('Entry updated', 'success');
                inner.dataset.loaded = '';
                await loadEntries(inner, tabId, tabType);
            } else {
                const err = await resp.json();
                ui.showToast(err.detail || 'Failed', 'error');
            }
        } catch (e) { ui.showToast('Failed', 'error'); }
    });
}

// ─── Add Entry Modal ─────────────────────────────────────────────────────────

function showAddEntryModal(inner, tabId, tabType) {
    const existing = document.querySelector('.mind-modal-overlay');
    if (existing) existing.remove();

    const overlay = document.createElement('div');
    overlay.className = 'pr-modal-overlay mind-modal-overlay';
    overlay.innerHTML = `
        <div class="pr-modal">
            <div class="pr-modal-header">
                <h3>Add Entry</h3>
                <button class="mind-btn-sm mind-modal-close">&#x2715;</button>
            </div>
            <div class="pr-modal-body">
                <div class="mind-form">
                    <textarea id="mae-content" rows="16" style="min-height:300px" placeholder="Paste or type content here — large texts are automatically chunked for search"></textarea>
                    <div style="display:flex;justify-content:flex-end;gap:8px">
                        <button class="mind-btn mind-modal-cancel">Cancel</button>
                        <button class="mind-btn" id="mae-save" style="border-color:var(--trim,var(--accent-blue))">Save</button>
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

    overlay.querySelector('#mae-content').focus();

    overlay.querySelector('#mae-save').addEventListener('click', async () => {
        const content = overlay.querySelector('#mae-content').value.trim();
        if (!content) { ui.showToast('Content cannot be empty', 'error'); return; }
        try {
            const resp = await fetch(`/api/knowledge/tabs/${tabId}/entries`, {
                method: 'POST',
                headers: csrfHeaders({ 'Content-Type': 'application/json' }),
                body: JSON.stringify({ content })
            });
            if (resp.ok) {
                const result = await resp.json();
                const msg = result.chunks ? `Added (${result.chunks} chunks)` : 'Added';
                close();
                ui.showToast(msg, 'success');
                inner.dataset.loaded = '';
                await loadEntries(inner, tabId, tabType);
            } else {
                const err = await resp.json();
                ui.showToast(err.detail || 'Failed', 'error');
            }
        } catch (e) { ui.showToast('Failed', 'error'); }
    });
}

// ─── Scope Deletion (double confirmation) ────────────────────────────────────

function showDeleteScopeConfirmation(scopeName, typeLabel, count) {
    const existing = document.querySelector('.mind-modal-overlay');
    if (existing) existing.remove();

    // ── Confirmation 1 ──
    const overlay = document.createElement('div');
    overlay.className = 'pr-modal-overlay mind-modal-overlay';
    overlay.innerHTML = `
        <div class="pr-modal">
            <div class="pr-modal-header">
                <h3>Delete Scope: ${escHtml(scopeName)}</h3>
                <button class="mind-btn-sm mind-modal-close">&#x2715;</button>
            </div>
            <div class="pr-modal-body">
                <p style="margin:0 0 12px;color:var(--text-secondary);font-size:var(--font-sm)">
                    This will <strong>permanently delete</strong> the scope <strong>"${escHtml(scopeName)}"</strong>
                    and all <strong>${count} ${typeLabel}</strong> inside it.
                </p>
                <p style="margin:0 0 16px;color:var(--text-muted);font-size:var(--font-xs)">
                    This action cannot be undone. Type <strong>DELETE</strong> to proceed.
                </p>
                <input type="text" id="del-scope-confirm-1" placeholder="Type DELETE" style="width:100%;padding:8px 10px;background:var(--input-bg);border:1px solid var(--border);border-radius:var(--radius-sm);color:var(--text);font-size:var(--font-sm);margin-bottom:12px">
                <div style="display:flex;justify-content:flex-end;gap:8px">
                    <button class="mind-btn mind-modal-cancel">Cancel</button>
                    <button class="mind-btn" id="del-scope-next" style="opacity:0.4;pointer-events:none;border-color:var(--danger,#e55)">Continue</button>
                </div>
            </div>
        </div>
    `;

    document.body.appendChild(overlay);

    const close = () => overlay.remove();
    overlay.querySelector('.mind-modal-close').addEventListener('click', close);
    overlay.querySelector('.mind-modal-cancel').addEventListener('click', close);
    setupModalClose(overlay, close);

    const input1 = overlay.querySelector('#del-scope-confirm-1');
    const nextBtn = overlay.querySelector('#del-scope-next');
    input1.focus();

    input1.addEventListener('input', () => {
        const valid = input1.value.trim() === 'DELETE';
        nextBtn.style.opacity = valid ? '1' : '0.4';
        nextBtn.style.pointerEvents = valid ? 'auto' : 'none';
    });

    nextBtn.addEventListener('click', () => {
        if (input1.value.trim() !== 'DELETE') return;
        close();
        showDeleteScopeConfirmation2(scopeName, typeLabel, count);
    });
}

function showDeleteScopeConfirmation2(scopeName, typeLabel, count) {
    // ── Confirmation 2 — more alarming ──
    const overlay = document.createElement('div');
    overlay.className = 'pr-modal-overlay mind-modal-overlay';
    overlay.innerHTML = `
        <div class="pr-modal" style="border:2px solid var(--danger,#e55)">
            <div class="pr-modal-header" style="background:rgba(238,85,85,0.1);border-bottom-color:var(--danger,#e55)">
                <h3 style="color:var(--danger,#e55)">&#x26A0; FINAL WARNING</h3>
                <button class="mind-btn-sm mind-modal-close">&#x2715;</button>
            </div>
            <div class="pr-modal-body">
                <p style="margin:0 0 8px;font-size:var(--font-md);font-weight:600;color:var(--danger,#e55)">
                    You are about to permanently destroy:
                </p>
                <div style="margin:0 0 16px;padding:12px;background:rgba(238,85,85,0.08);border:1px solid var(--danger,#e55);border-radius:var(--radius-sm);font-size:var(--font-sm)">
                    <strong>Scope:</strong> ${escHtml(scopeName)}<br>
                    <strong>Contains:</strong> ${count} ${typeLabel}<br>
                    <strong>Recovery:</strong> None. Data is gone forever.
                </div>
                <p style="margin:0 0 16px;color:var(--text-secondary);font-size:var(--font-sm)">
                    Type <strong>DELETE</strong> one more time to confirm destruction.
                </p>
                <input type="text" id="del-scope-confirm-2" placeholder="Type DELETE" style="width:100%;padding:8px 10px;background:var(--input-bg);border:2px solid var(--danger,#e55);border-radius:var(--radius-sm);color:var(--text);font-size:var(--font-sm);margin-bottom:12px">
                <div style="display:flex;justify-content:flex-end;gap:8px">
                    <button class="mind-btn mind-modal-cancel">Cancel</button>
                    <button class="mind-btn" id="del-scope-execute" style="opacity:0.4;pointer-events:none;background:var(--danger,#e55);color:#fff;border-color:var(--danger,#e55)">Delete Forever</button>
                </div>
            </div>
        </div>
    `;

    document.body.appendChild(overlay);

    const close = () => overlay.remove();
    overlay.querySelector('.mind-modal-close').addEventListener('click', close);
    overlay.querySelector('.mind-modal-cancel').addEventListener('click', close);
    setupModalClose(overlay, close);

    const input2 = overlay.querySelector('#del-scope-confirm-2');
    const execBtn = overlay.querySelector('#del-scope-execute');
    input2.focus();

    input2.addEventListener('input', () => {
        const valid = input2.value.trim() === 'DELETE';
        execBtn.style.opacity = valid ? '1' : '0.4';
        execBtn.style.pointerEvents = valid ? 'auto' : 'none';
    });

    execBtn.addEventListener('click', async () => {
        if (input2.value.trim() !== 'DELETE') return;
        const enc = encodeURIComponent(scopeName);
        // Phase 2f: derive the delete API list from /api/init scope_declarations
        // filtered to Mind-domain scopes (nav_target starts with "mind:"). Was a
        // hardcoded 4-URL list. New plugin mind scopes get swept automatically.
        const initData = await getInitData().catch(() => null);
        const mindDecls = (initData?.scope_declarations || [])
            .filter(d => d.nav_target?.startsWith('mind:'));
        const apis = mindDecls.map(d => `${d.endpoint}/${enc}`);
        try {
            const results = await Promise.allSettled(apis.map(url =>
                fetch(url, {
                    method: 'DELETE',
                    headers: csrfHeaders({ 'Content-Type': 'application/json' }),
                    body: JSON.stringify({ confirm: 'DELETE' })
                })
            ));
            const anyOk = results.some(r => r.status === 'fulfilled' && r.value.ok);
            if (anyOk) {
                close();
                memoryScopeCache = memoryScopeCache.filter(s => s.name !== scopeName);
                knowledgeScopeCache = knowledgeScopeCache.filter(s => s.name !== scopeName);
                peopleScopeCache = peopleScopeCache.filter(s => s.name !== scopeName);
                goalScopeCache = goalScopeCache.filter(s => s.name !== scopeName);
                currentScope = 'default';
                updateScopeDropdown();
                renderContent();
                ui.showToast(`Scope "${scopeName}" deleted`, 'success');
            } else {
                ui.showToast('Failed to delete scope', 'error');
            }
        } catch (e) {
            ui.showToast('Failed to delete scope', 'error');
        }
    });
}

// ─── Goals Tab ───────────────────────────────────────────────────────────────

let goalStatusFilter = 'active';

async function renderGoals(el) {
    const resp = await fetch(`/api/goals?scope=${encodeURIComponent(currentScope)}&status=${goalStatusFilter}`);
    if (!resp.ok) { el.innerHTML = '<div class="mind-empty">Failed to load goals</div>'; return; }
    const data = await resp.json();
    const goals = data.goals || [];

    const desc = '<div class="mind-tab-desc">Tracked objectives and tasks. The AI creates and updates these via tools, but you can also manage them here.</div>';

    const filterHtml = `
        <div class="mind-toolbar">
            <button class="mind-btn" id="mind-new-goal">+ New Goal</button>
            <div class="goal-status-filter">
                ${['active', 'completed', 'abandoned', 'all'].map(s =>
                    `<button class="mind-btn-sm goal-filter-btn${goalStatusFilter === s ? ' active' : ''}" data-status="${s}">${s[0].toUpperCase() + s.slice(1)}</button>`
                ).join('')}
            </div>
        </div>
    `;

    if (!goals.length) {
        el.innerHTML = desc + filterHtml + `<div class="mind-empty">No ${goalStatusFilter === 'all' ? '' : goalStatusFilter + ' '}goals in this scope</div>`;
        bindGoalToolbar(el);
        return;
    }

    el.innerHTML = desc + filterHtml + '<div class="mind-list">' + goals.map(g => {
        const priClass = `goal-pri-${g.priority}`;
        const statusIcon = g.status === 'completed' ? '&#x2705;' : g.status === 'abandoned' ? '&#x274C;' : '&#x1F7E2;';
        const ago = timeAgo(g.updated_at);
        const subtasksDone = g.subtasks.filter(s => s.status === 'completed').length;
        const subtasksTotal = g.subtasks.length;

        return `
            <details class="mind-accordion">
                <summary class="mind-accordion-header">
                    <span class="goal-status-dot" title="${escHtml(g.status)}">${statusIcon}</span>
                    <span class="mind-accordion-title">${escHtml(g.title)}</span>
                    <span class="goal-pri-badge ${priClass}">${g.priority}</span>
                    ${g.permanent ? '<span class="goal-perm-badge" title="Permanent — AI cannot complete or delete">PERM</span>' : ''}
                    ${subtasksTotal ? `<span class="goal-subtask-count">${subtasksDone}/${subtasksTotal}</span>` : ''}
                    <span class="mind-accordion-count">${ago}</span>
                </summary>
                <div class="mind-accordion-body">
                    <div class="mind-accordion-inner">
                        ${g.description ? `<div class="goal-desc">${escHtml(g.description)}</div>` : ''}

                        ${subtasksTotal ? `
                            <div class="goal-subtasks">
                                <div class="goal-section-label">Subtasks</div>
                                ${g.subtasks.map(s => `
                                    <div class="goal-subtask" data-id="${s.id}">
                                        <button class="goal-subtask-check${s.status === 'completed' ? ' done' : ''}" data-id="${s.id}" data-status="${s.status}" title="Toggle complete">${s.status === 'completed' ? '&#x2611;' : '&#x2610;'}</button>
                                        <span class="goal-subtask-title${s.status === 'completed' ? ' done' : ''}">${escHtml(s.title)}</span>
                                        <button class="mind-btn-sm goal-del-subtask" data-id="${s.id}" title="Delete">&#x2715;</button>
                                    </div>
                                `).join('')}
                            </div>
                        ` : ''}

                        ${g.progress.length ? `
                            <div class="goal-progress">
                                <div class="goal-section-label">Progress Journal</div>
                                ${g.progress.map(p => `
                                    <div class="goal-progress-entry">
                                        <span class="goal-progress-time">${timeAgo(p.created_at)}</span>
                                        <span class="goal-progress-note">${escHtml(p.note)}</span>
                                    </div>
                                `).join('')}
                            </div>
                        ` : ''}

                        <div class="goal-actions">
                            ${g.status === 'active' ? `
                                <button class="mind-btn-sm goal-complete-btn" data-id="${g.id}" title="Mark complete">&#x2705; Complete</button>
                                <button class="mind-btn-sm goal-abandon-btn" data-id="${g.id}" title="Abandon">&#x274C; Abandon</button>
                            ` : `
                                <button class="mind-btn-sm goal-reactivate-btn" data-id="${g.id}" title="Reactivate">&#x1F504; Reactivate</button>
                            `}
                            <button class="mind-btn-sm goal-add-subtask" data-id="${g.id}" title="Add subtask">+ Subtask</button>
                            <button class="mind-btn-sm goal-add-note" data-id="${g.id}" title="Add progress note">+ Note</button>
                            <button class="mind-btn-sm goal-edit-btn" data-id="${g.id}" title="Edit">&#x270E;</button>
                            <button class="mind-btn-sm goal-del-btn" data-id="${g.id}" title="Delete">&#x1F5D1;</button>
                        </div>
                    </div>
                </div>
            </details>
        `;
    }).join('') + '</div>';

    bindGoalToolbar(el);
    bindGoalActions(el);
}

function bindGoalToolbar(el) {
    el.querySelector('#mind-new-goal')?.addEventListener('click', () => showGoalModal(el));
    el.querySelectorAll('.goal-filter-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            goalStatusFilter = btn.dataset.status;
            renderGoals(el);
        });
    });
}

function bindGoalActions(el) {
    // Status changes
    el.querySelectorAll('.goal-complete-btn').forEach(btn => {
        btn.addEventListener('click', () => updateGoalStatus(el, btn.dataset.id, 'completed'));
    });
    el.querySelectorAll('.goal-abandon-btn').forEach(btn => {
        btn.addEventListener('click', () => updateGoalStatus(el, btn.dataset.id, 'abandoned'));
    });
    el.querySelectorAll('.goal-reactivate-btn').forEach(btn => {
        btn.addEventListener('click', () => updateGoalStatus(el, btn.dataset.id, 'active'));
    });

    // Subtask toggle
    el.querySelectorAll('.goal-subtask-check').forEach(btn => {
        btn.addEventListener('click', () => {
            const newStatus = btn.dataset.status === 'completed' ? 'active' : 'completed';
            updateGoalStatus(el, btn.dataset.id, newStatus);
        });
    });

    // Delete subtask
    el.querySelectorAll('.goal-del-subtask').forEach(btn => {
        btn.addEventListener('click', async () => {
            if (!confirm('Delete this subtask?')) return;
            try {
                const resp = await fetch(`/api/goals/${btn.dataset.id}`, { method: 'DELETE', headers: csrfHeaders() });
                if (resp.ok) { ui.showToast('Deleted', 'success'); renderGoals(el); }
            } catch (e) { ui.showToast('Failed', 'error'); }
        });
    });

    // Add subtask
    el.querySelectorAll('.goal-add-subtask').forEach(btn => {
        btn.addEventListener('click', async () => {
            const title = prompt('Subtask title:');
            if (!title?.trim()) return;
            try {
                const resp = await fetch('/api/goals', {
                    method: 'POST',
                    headers: csrfHeaders({ 'Content-Type': 'application/json' }),
                    body: JSON.stringify({ title: title.trim(), parent_id: parseInt(btn.dataset.id), scope: currentScope })
                });
                if (resp.ok) { ui.showToast('Subtask added', 'success'); renderGoals(el); }
                else { const err = await resp.json(); ui.showToast(err.detail || 'Failed', 'error'); }
            } catch (e) { ui.showToast('Failed', 'error'); }
        });
    });

    // Add progress note
    el.querySelectorAll('.goal-add-note').forEach(btn => {
        btn.addEventListener('click', async () => {
            const note = prompt('Progress note:');
            if (!note?.trim()) return;
            try {
                const resp = await fetch(`/api/goals/${btn.dataset.id}/progress`, {
                    method: 'POST',
                    headers: csrfHeaders({ 'Content-Type': 'application/json' }),
                    body: JSON.stringify({ note: note.trim() })
                });
                if (resp.ok) { ui.showToast('Note added', 'success'); renderGoals(el); }
                else { const err = await resp.json(); ui.showToast(err.detail || 'Failed', 'error'); }
            } catch (e) { ui.showToast('Failed', 'error'); }
        });
    });

    // Edit goal
    el.querySelectorAll('.goal-edit-btn').forEach(btn => {
        btn.addEventListener('click', async () => {
            try {
                const resp = await fetch(`/api/goals/${btn.dataset.id}`);
                if (resp.ok) {
                    const goal = await resp.json();
                    showGoalModal(el, goal);
                }
            } catch (e) { ui.showToast('Failed to load goal', 'error'); }
        });
    });

    // Delete goal
    el.querySelectorAll('.goal-del-btn').forEach(btn => {
        btn.addEventListener('click', async () => {
            const isPerm = btn.closest('.mind-accordion')?.querySelector('.goal-perm-badge');
            const msg = isPerm
                ? 'This is a PERMANENT goal. Are you sure you want to delete it?'
                : 'Delete this goal and all subtasks/progress?';
            if (!confirm(msg)) return;
            try {
                const resp = await fetch(`/api/goals/${btn.dataset.id}`, { method: 'DELETE', headers: csrfHeaders() });
                if (resp.ok) { ui.showToast('Deleted', 'success'); renderGoals(el); }
            } catch (e) { ui.showToast('Failed', 'error'); }
        });
    });
}

async function updateGoalStatus(el, goalId, status) {
    try {
        const resp = await fetch(`/api/goals/${goalId}`, {
            method: 'PUT',
            headers: csrfHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify({ status })
        });
        if (resp.ok) { renderGoals(el); }
        else { const err = await resp.json(); ui.showToast(err.detail || 'Failed', 'error'); }
    } catch (e) { ui.showToast('Failed', 'error'); }
}

function showGoalModal(el, goal = null) {
    const existing = document.querySelector('.mind-modal-overlay');
    if (existing) existing.remove();

    const overlay = document.createElement('div');
    overlay.className = 'pr-modal-overlay mind-modal-overlay';
    overlay.innerHTML = `
        <div class="pr-modal">
            <div class="pr-modal-header">
                <h3>${goal ? 'Edit' : 'New'} Goal</h3>
                <button class="mind-btn-sm mind-modal-close">&#x2715;</button>
            </div>
            <div class="pr-modal-body">
                <div class="mind-form">
                    <input type="text" id="mg-title" placeholder="Title *" value="${escAttr(goal?.title || '')}">
                    <textarea id="mg-desc" placeholder="Description (optional)" rows="3">${escHtml(goal?.description || '')}</textarea>
                    <div style="display:flex;gap:8px;align-items:center">
                        <label style="color:var(--text-muted);font-size:var(--font-sm)">Priority:</label>
                        <select id="mg-priority" style="padding:4px 8px;background:var(--input-bg);border:1px solid var(--border);border-radius:var(--radius-sm);color:var(--text);font-size:var(--font-sm)">
                            ${['high', 'medium', 'low'].map(p =>
                                `<option value="${p}"${(goal?.priority || 'medium') === p ? ' selected' : ''}>${p[0].toUpperCase() + p.slice(1)}</option>`
                            ).join('')}
                        </select>
                    </div>
                    <label style="display:flex;gap:6px;align-items:center;color:var(--text-muted);font-size:var(--font-sm);cursor:pointer">
                        <input type="checkbox" id="mg-permanent" ${goal?.permanent ? 'checked' : ''}>
                        Permanent <span style="opacity:0.6">(AI cannot complete or delete)</span>
                    </label>
                    <div style="display:flex;justify-content:flex-end;gap:8px">
                        <button class="mind-btn mind-modal-cancel">Cancel</button>
                        <button class="mind-btn" id="mg-save" style="border-color:var(--trim,var(--accent-blue))">${goal ? 'Update' : 'Create'}</button>
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

    overlay.querySelector('#mg-title').focus();

    overlay.querySelector('#mg-save').addEventListener('click', async () => {
        const title = overlay.querySelector('#mg-title').value.trim();
        if (!title) { ui.showToast('Title is required', 'error'); return; }
        const body = {
            title,
            description: overlay.querySelector('#mg-desc').value.trim() || null,
            priority: overlay.querySelector('#mg-priority').value,
            permanent: overlay.querySelector('#mg-permanent').checked,
        };

        try {
            let resp;
            if (goal) {
                resp = await fetch(`/api/goals/${goal.id}`, {
                    method: 'PUT',
                    headers: csrfHeaders({ 'Content-Type': 'application/json' }),
                    body: JSON.stringify(body)
                });
            } else {
                body.scope = currentScope;
                resp = await fetch('/api/goals', {
                    method: 'POST',
                    headers: csrfHeaders({ 'Content-Type': 'application/json' }),
                    body: JSON.stringify(body)
                });
            }
            if (resp.ok) {
                close();
                ui.showToast(goal ? 'Updated' : 'Created', 'success');
                renderGoals(el);
            } else {
                const err = await resp.json();
                ui.showToast(err.detail || 'Failed', 'error');
            }
        } catch (e) { ui.showToast('Failed', 'error'); }
    });
}

function timeAgo(ts) {
    if (!ts) return '';
    try {
        const diff = Date.now() - new Date(ts).getTime();
        const mins = Math.floor(diff / 60000);
        if (mins < 1) return 'just now';
        if (mins < 60) return `${mins}m ago`;
        const hrs = Math.floor(mins / 60);
        if (hrs < 24) return `${hrs}h ago`;
        const days = Math.floor(hrs / 24);
        if (days < 14) return `${days}d ago`;
        return `${Math.floor(days / 7)}w ago`;
    } catch { return ''; }
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

function escHtml(s) {
    if (!s) return '';
    return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function escAttr(s) {
    if (!s) return '';
    return s.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}
