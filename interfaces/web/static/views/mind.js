// views/mind.js - Mind view: Memories, People, Knowledge, AI Knowledge, Goals
import * as ui from '../ui.js';

let container = null;
let activeTab = 'memories';
let currentScope = 'default';
let memoryScopeCache = [];
let knowledgeScopeCache = [];
let peopleScopeCache = [];
let goalScopeCache = [];
let memoryPage = 0;
const MEMORIES_PER_PAGE = 100;

export default {
    init(el) {
        container = el;
    },
    async show() {
        if (window._mindTab) {
            activeTab = window._mindTab;
            delete window._mindTab;
        }
        if (window._mindScope) {
            currentScope = window._mindScope;
            delete window._mindScope;
        }
        await render();
    },
    hide() {}
};

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

    // Tab switching
    container.querySelectorAll('.mind-tab').forEach(btn => {
        btn.addEventListener('click', () => {
            container.querySelectorAll('.mind-tab').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            activeTab = btn.dataset.tab;
            memoryPage = 0;
            updateScopeDropdown();
            renderContent();
        });
    });

    // Scope change
    container.querySelector('#mind-scope').addEventListener('change', (e) => {
        currentScope = e.target.value;
        memoryPage = 0;
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
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ name: clean })
                })
            ));
            const anyOk = results.some(r => r.status === 'fulfilled' && r.value.ok);
            if (anyOk) {
                const newScope = { name: clean, count: 0 };
                // Add to caches if not already present
                for (const cache of [memoryScopeCache, knowledgeScopeCache, peopleScopeCache, goalScopeCache]) {
                    if (!cache.find(s => s.name === clean)) cache.push(newScope);
                }
                currentScope = clean;
                updateScopeDropdown();
                renderContent();
                ui.showToast(`Created: ${clean}`, 'success');
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

async function renderMemories(el) {
    const resp = await fetch(`/api/memory/list?scope=${encodeURIComponent(currentScope)}`);
    if (!resp.ok) { el.innerHTML = '<div class="mind-empty">Failed to load memories</div>'; return; }
    const data = await resp.json();
    const groups = data.memories || {};
    const labels = Object.keys(groups).sort();

    const desc = '<div class="mind-tab-desc">Short identity snippets the AI saves during conversation. Grouped by label — these shape how it remembers you and itself.</div>';

    if (!labels.length) {
        el.innerHTML = desc + '<div class="mind-empty">No memories in this scope</div>';
        return;
    }

    // Count total and paginate by accordion (never split a group)
    const totalMemories = labels.reduce((n, l) => n + groups[l].length, 0);
    let pageLabels = labels, showPagination = false;
    if (totalMemories > MEMORIES_PER_PAGE) {
        showPagination = true;
        let count = 0, startIdx = 0, collected = 0;
        // Find starting label for current page
        for (let i = 0; i < labels.length; i++) {
            if (count >= memoryPage * MEMORIES_PER_PAGE) { startIdx = i; break; }
            count += groups[labels[i]].length;
            startIdx = i;
        }
        // Collect labels for this page (don't break groups)
        pageLabels = [];
        count = 0;
        for (let i = startIdx; i < labels.length && count < MEMORIES_PER_PAGE; i++) {
            pageLabels.push(labels[i]);
            count += groups[labels[i]].length;
        }
    }

    const totalPages = showPagination ? Math.ceil(labels.length / pageLabels.length) : 1;

    el.innerHTML = desc + (showPagination ? `
        <div class="mind-pagination">
            <button class="mind-btn-sm" id="mem-prev" ${memoryPage === 0 ? 'disabled' : ''}>&#x25C0; Prev</button>
            <span class="mind-page-info">${memoryPage + 1} / ${totalPages} (${totalMemories} memories)</span>
            <button class="mind-btn-sm" id="mem-next" ${memoryPage >= totalPages - 1 ? 'disabled' : ''}>Next &#x25B6;</button>
        </div>
    ` : '') +
    '<div class="mind-list">' + pageLabels.map(label => {
        const memories = groups[label];
        return `
            <details class="mind-accordion" open>
                <summary class="mind-accordion-header">
                    <span class="mind-accordion-title">${escHtml(label)}</span>
                    <span class="mind-accordion-count">${memories.length}</span>
                </summary>
                <div class="mind-accordion-body">
                    <div class="mind-accordion-inner">
                        ${memories.map(m => `
                            <div class="mind-item" data-id="${m.id}">
                                <div class="mind-item-content">${escHtml(m.content)}</div>
                                <div class="mind-item-actions">
                                    <button class="mind-btn-sm mind-edit-memory" data-id="${m.id}" title="Edit">&#x270E;</button>
                                    <button class="mind-btn-sm mind-del-memory" data-id="${m.id}" title="Delete">&#x2715;</button>
                                </div>
                            </div>
                        `).join('')}
                    </div>
                </div>
            </details>
        `;
    }).join('') + '</div>';

    // Pagination handlers
    el.querySelector('#mem-prev')?.addEventListener('click', () => {
        if (memoryPage > 0) { memoryPage--; renderMemories(el); }
    });
    el.querySelector('#mem-next')?.addEventListener('click', () => {
        if (memoryPage < totalPages - 1) { memoryPage++; renderMemories(el); }
    });

    // Edit handlers
    el.querySelectorAll('.mind-edit-memory').forEach(btn => {
        btn.addEventListener('click', () => {
            const id = parseInt(btn.dataset.id);
            const item = btn.closest('.mind-item');
            const content = item.querySelector('.mind-item-content').textContent;
            showMemoryEditModal(el, id, content);
        });
    });

    // Delete handlers
    el.querySelectorAll('.mind-del-memory').forEach(btn => {
        btn.addEventListener('click', async () => {
            if (!confirm('Delete this memory?')) return;
            const id = parseInt(btn.dataset.id);
            try {
                const resp = await fetch(`/api/memory/${id}?scope=${encodeURIComponent(currentScope)}`, { method: 'DELETE' });
                if (resp.ok) {
                    ui.showToast('Deleted', 'success');
                    await renderMemories(el);
                }
            } catch (e) { ui.showToast('Failed', 'error'); }
        });
    });
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
    overlay.addEventListener('click', (e) => { if (e.target === overlay) close(); });

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
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ content: newContent, scope: currentScope })
            });
            if (resp.ok) {
                close();
                ui.showToast('Memory updated', 'success');
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
            <input type="file" id="mind-vcf-input" accept=".vcf" style="display:none">
        </div>
        ${people.length ? `<div class="mind-people-grid">
            ${people.map(p => `
                <div class="mind-person-card" data-id="${p.id}">
                    <div class="mind-person-name">${escHtml(p.name)}</div>
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
            const resp = await fetch('/api/knowledge/people/import-vcf', { method: 'POST', body: form });
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
                const resp = await fetch(`/api/knowledge/people/${btn.dataset.id}`, { method: 'DELETE' });
                if (resp.ok) {
                    ui.showToast('Deleted', 'success');
                    await renderPeople(el);
                }
            } catch (e) { ui.showToast('Failed', 'error'); }
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
                    <button class="mind-btn" id="mp-save">${person ? 'Update' : 'Save'}</button>
                </div>
            </div>
        </div>
    `;

    document.body.appendChild(overlay);

    overlay.querySelector('.mind-modal-close').addEventListener('click', () => overlay.remove());
    overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.remove(); });

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
            scope: currentScope,
        };
        if (person?.id) body.id = person.id;

        try {
            const resp = await fetch('/api/knowledge/people', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
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
        </div>
        ${tabs.length ? `<div class="mind-list">
            ${tabs.map(t => `
                <details class="mind-accordion">
                    <summary class="mind-accordion-header">
                        <span class="mind-accordion-title">${escHtml(t.name)}</span>
                        <span class="mind-accordion-count">${t.entry_count} entries</span>
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
                headers: { 'Content-Type': 'application/json' },
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
                const resp = await fetch(`/api/knowledge/tabs/${btn.dataset.id}`, { method: 'DELETE' });
                if (resp.ok) {
                    ui.showToast('Deleted', 'success');
                    await renderKnowledge(el, tabType);
                }
            } catch (e) { ui.showToast('Failed', 'error'); }
        });
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
        const resp = await fetch(`/api/knowledge/tabs/${tabId}`);
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
                        method: 'POST', body: form
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
                    const resp = await fetch(`/api/knowledge/tabs/${btn.dataset.tabId}/file/${encodeURIComponent(fname)}`, { method: 'DELETE' });
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
            btn.addEventListener('click', async () => {
                const content = prompt('Entry content:');
                if (!content) return;
                try {
                    const resp = await fetch(`/api/knowledge/tabs/${btn.dataset.tabId}/entries`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ content })
                    });
                    if (resp.ok) {
                        ui.showToast('Added', 'success');
                        inner.dataset.loaded = '';
                        await loadEntries(inner, tabId, tabType);
                    }
                } catch (e) { ui.showToast('Failed', 'error'); }
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
                    const resp = await fetch(`/api/knowledge/entries/${btn.dataset.id}`, { method: 'DELETE' });
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
    overlay.addEventListener('click', (e) => { if (e.target === overlay) close(); });

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
                headers: { 'Content-Type': 'application/json' },
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
    overlay.addEventListener('click', (e) => { if (e.target === overlay) close(); });

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
    overlay.addEventListener('click', (e) => { if (e.target === overlay) close(); });

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
        const apis = [
            `/api/memory/scopes/${enc}`,
            `/api/knowledge/scopes/${enc}`,
            `/api/knowledge/people/scopes/${enc}`,
            `/api/goals/scopes/${enc}`
        ];
        try {
            const results = await Promise.allSettled(apis.map(url =>
                fetch(url, {
                    method: 'DELETE',
                    headers: { 'Content-Type': 'application/json' },
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
                const resp = await fetch(`/api/goals/${btn.dataset.id}`, { method: 'DELETE' });
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
                    headers: { 'Content-Type': 'application/json' },
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
                    headers: { 'Content-Type': 'application/json' },
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
            if (!confirm('Delete this goal and all subtasks/progress?')) return;
            try {
                const resp = await fetch(`/api/goals/${btn.dataset.id}`, { method: 'DELETE' });
                if (resp.ok) { ui.showToast('Deleted', 'success'); renderGoals(el); }
            } catch (e) { ui.showToast('Failed', 'error'); }
        });
    });
}

async function updateGoalStatus(el, goalId, status) {
    try {
        const resp = await fetch(`/api/goals/${goalId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
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
    overlay.addEventListener('click', (e) => { if (e.target === overlay) close(); });

    overlay.querySelector('#mg-title').focus();

    overlay.querySelector('#mg-save').addEventListener('click', async () => {
        const title = overlay.querySelector('#mg-title').value.trim();
        if (!title) { ui.showToast('Title is required', 'error'); return; }
        const body = {
            title,
            description: overlay.querySelector('#mg-desc').value.trim() || null,
            priority: overlay.querySelector('#mg-priority').value,
        };

        try {
            let resp;
            if (goal) {
                resp = await fetch(`/api/goals/${goal.id}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body)
                });
            } else {
                body.scope = currentScope;
                resp = await fetch('/api/goals', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
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
