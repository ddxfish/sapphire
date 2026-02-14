// views/mind.js - Mind view: Memories, People, Knowledge, AI Notes
import * as ui from '../ui.js';

let container = null;
let activeTab = 'memories';
let currentScope = 'default';
let memoryScopeCache = [];
let knowledgeScopeCache = [];

export default {
    init(el) {
        container = el;
    },
    async show() {
        await render();
    },
    hide() {}
};

// ─── Main Render ─────────────────────────────────────────────────────────────

async function render() {
    // Fetch both scope types in parallel
    const [memResp, knowResp] = await Promise.allSettled([
        fetch('/api/memory/scopes').then(r => r.ok ? r.json() : null),
        fetch('/api/knowledge/scopes').then(r => r.ok ? r.json() : null)
    ]);
    memoryScopeCache = memResp.status === 'fulfilled' && memResp.value ? memResp.value.scopes || [] : [];
    knowledgeScopeCache = knowResp.status === 'fulfilled' && knowResp.value ? knowResp.value.scopes || [] : [];

    container.innerHTML = `
        <div class="mind-view">
            <div class="mind-header">
                <h2>Mind</h2>
                <div class="mind-tabs">
                    <button class="mind-tab active" data-tab="memories">Memories</button>
                    <button class="mind-tab" data-tab="people">People</button>
                    <button class="mind-tab" data-tab="knowledge">Knowledge</button>
                    <button class="mind-tab" data-tab="ai-notes">AI Notes</button>
                </div>
            </div>
            <div class="mind-scope-bar">
                <label>Scope:</label>
                <select id="mind-scope"></select>
                <button class="mind-btn-sm" id="mind-new-scope" title="New scope">+</button>
                <button class="mind-btn-sm mind-del-scope-btn" id="mind-del-scope" title="Delete scope">&#x1F5D1;</button>
            </div>
            <div id="mind-content" class="mind-content"></div>
        </div>
    `;

    // Tab switching
    container.querySelectorAll('.mind-tab').forEach(btn => {
        btn.addEventListener('click', () => {
            container.querySelectorAll('.mind-tab').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            activeTab = btn.dataset.tab;
            updateScopeDropdown();
            renderContent();
        });
    });

    // Scope change
    container.querySelector('#mind-scope').addEventListener('change', (e) => {
        currentScope = e.target.value;
        renderContent();
    });

    // New scope button
    container.querySelector('#mind-new-scope').addEventListener('click', async () => {
        const name = prompt('New scope name (lowercase, no spaces):');
        if (!name) return;
        const clean = name.trim().toLowerCase().replace(/[^a-z0-9_]/g, '');
        if (!clean || clean.length > 32) {
            ui.showToast('Invalid name', 'error');
            return;
        }
        const apiPath = activeTab === 'memories' ? '/api/memory/scopes' : '/api/knowledge/scopes';
        try {
            const res = await fetch(apiPath, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name: clean })
            });
            if (res.ok) {
                const newScope = { name: clean, count: 0 };
                if (activeTab === 'memories') memoryScopeCache.push(newScope);
                else knowledgeScopeCache.push(newScope);
                currentScope = clean;
                updateScopeDropdown();
                renderContent();
                ui.showToast(`Created: ${clean}`, 'success');
            } else {
                ui.showToast('Failed to create scope', 'error');
            }
        } catch (e) { ui.showToast('Failed', 'error'); }
    });

    // Delete scope button
    container.querySelector('#mind-del-scope').addEventListener('click', () => {
        if (currentScope === 'default') {
            ui.showToast('Cannot delete the default scope', 'error');
            return;
        }
        const isMemory = activeTab === 'memories';
        const scopes = isMemory ? memoryScopeCache : knowledgeScopeCache;
        const scopeInfo = scopes.find(s => s.name === currentScope);
        const count = scopeInfo?.count || 0;
        const typeLabel = isMemory ? 'memories' : 'knowledge tabs (and all entries within them)';

        showDeleteScopeConfirmation(currentScope, typeLabel, count, isMemory);
    });

    updateScopeDropdown();
    await renderContent();
}

function updateScopeDropdown() {
    const sel = container.querySelector('#mind-scope');
    if (!sel) return;
    const scopes = activeTab === 'memories' ? memoryScopeCache : knowledgeScopeCache;
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

    // Show/hide scope bar for People tab (people are universal)
    const scopeBar = container.querySelector('.mind-scope-bar');
    if (scopeBar) scopeBar.style.display = activeTab === 'people' ? 'none' : '';

    try {
        switch (activeTab) {
            case 'memories': await renderMemories(el); break;
            case 'people': await renderPeople(el); break;
            case 'knowledge': await renderKnowledge(el, 'user'); break;
            case 'ai-notes': await renderKnowledge(el, 'ai'); break;
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

    if (!labels.length) {
        el.innerHTML = '<div class="mind-empty">No memories in this scope</div>';
        return;
    }

    el.innerHTML = '<div class="mind-list">' + labels.map(label => {
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
    const resp = await fetch('/api/knowledge/people');
    if (!resp.ok) { el.innerHTML = '<div class="mind-empty">Failed to load</div>'; return; }
    const data = await resp.json();
    const people = data.people || [];

    el.innerHTML = `
        <div class="mind-toolbar">
            <button class="mind-btn" id="mind-add-person">+ Add Person</button>
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
        };

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

    el.innerHTML = `
        <div class="mind-toolbar">
            ${!isAI ? '<button class="mind-btn" id="mind-new-tab">+ New Tab</button>' : ''}
        </div>
        ${tabs.length ? `<div class="mind-list">
            ${tabs.map(t => `
                <details class="mind-accordion">
                    <summary class="mind-accordion-header">
                        <span class="mind-accordion-title">${escHtml(t.name)}</span>
                        <span class="mind-accordion-count">${t.entry_count} entries</span>
                        ${!isAI ? `<button class="mind-btn-sm mind-del-tab" data-id="${t.id}" title="Delete tab">&#x2715;</button>` : ''}
                    </summary>
                    <div class="mind-accordion-body">
                        <div class="mind-accordion-inner mind-tab-entries" data-tab-id="${t.id}" data-type="${tabType}">
                            <div class="mind-empty">Click to load entries</div>
                        </div>
                    </div>
                </details>
            `).join('')}
        </div>` : `<div class="mind-empty">No ${isAI ? 'AI notes' : 'knowledge tabs'} in this scope</div>`}
    `;

    // New tab button
    el.querySelector('#mind-new-tab')?.addEventListener('click', async () => {
        const name = prompt('Tab name:');
        if (!name) return;
        try {
            const resp = await fetch('/api/knowledge/tabs', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name: name.trim(), scope: currentScope, type: 'user' })
            });
            if (resp.ok) {
                ui.showToast('Tab created', 'success');
                await renderKnowledge(el, tabType);
            } else {
                const err = await resp.json();
                ui.showToast(err.detail || 'Failed', 'error');
            }
        } catch (e) { ui.showToast('Failed', 'error'); }
    });

    // Delete tab buttons
    el.querySelectorAll('.mind-del-tab').forEach(btn => {
        btn.addEventListener('click', async (e) => {
            e.preventDefault();
            e.stopPropagation();
            if (!confirm('Delete this tab and all its entries?')) return;
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

        inner.innerHTML = entries.map(e => `
            <div class="mind-item" data-id="${e.id}">
                <div class="mind-item-content">${escHtml(e.content)}</div>
                <div class="mind-item-actions">
                    ${!isAI ? `<button class="mind-btn-sm mind-edit-entry" data-id="${e.id}" title="Edit">&#x270E;</button>` : ''}
                    <button class="mind-btn-sm mind-del-entry" data-id="${e.id}" title="Delete">&#x2715;</button>
                </div>
            </div>
        `).join('') + (!isAI ? `<button class="mind-btn mind-add-entry" data-tab-id="${tabId}">+ Add Entry</button>` : '');

        if (!entries.length) {
            inner.innerHTML = `<div class="mind-empty">Empty tab</div>` +
                (!isAI ? `<button class="mind-btn mind-add-entry" data-tab-id="${tabId}">+ Add Entry</button>` : '');
        }

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
                const newContent = prompt('Edit entry:', content);
                if (newContent === null || newContent === content) return;
                try {
                    const resp = await fetch(`/api/knowledge/entries/${btn.dataset.id}`, {
                        method: 'PUT',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ content: newContent })
                    });
                    if (resp.ok) {
                        ui.showToast('Updated', 'success');
                        inner.dataset.loaded = '';
                        await loadEntries(inner, tabId, tabType);
                    }
                } catch (e) { ui.showToast('Failed', 'error'); }
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

// ─── Scope Deletion (double confirmation) ────────────────────────────────────

function showDeleteScopeConfirmation(scopeName, typeLabel, count, isMemory) {
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
        showDeleteScopeConfirmation2(scopeName, typeLabel, count, isMemory);
    });
}

function showDeleteScopeConfirmation2(scopeName, typeLabel, count, isMemory) {
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
        const apiPath = isMemory
            ? `/api/memory/scopes/${encodeURIComponent(scopeName)}`
            : `/api/knowledge/scopes/${encodeURIComponent(scopeName)}`;
        try {
            const resp = await fetch(apiPath, {
                method: 'DELETE',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ confirm: 'DELETE' })
            });
            if (resp.ok) {
                close();
                // Remove from cache
                if (isMemory) memoryScopeCache = memoryScopeCache.filter(s => s.name !== scopeName);
                else knowledgeScopeCache = knowledgeScopeCache.filter(s => s.name !== scopeName);
                currentScope = 'default';
                updateScopeDropdown();
                renderContent();
                ui.showToast(`Scope "${scopeName}" deleted`, 'success');
            } else {
                const err = await resp.json();
                ui.showToast(err.detail || 'Failed to delete', 'error');
            }
        } catch (e) {
            ui.showToast('Failed to delete scope', 'error');
        }
    });
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
