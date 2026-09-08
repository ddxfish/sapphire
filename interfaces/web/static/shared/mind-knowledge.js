// shared/mind-knowledge.js - The knowledge renderer, shared by the Human
// Knowledge (tabType 'user') and AI Knowledge (tabType 'ai') views. One
// parametrized code path — `scope` is threaded in (per-view) rather than read
// from a module global. Extracted from the mind.js monolith.
import { csrfHeaders, escHtml, escAttr } from './mind-common.js';
import { showExportDialog, showImportDialog } from './import-export.js';
import { setupModalClose } from './modal.js';
import * as ui from '../ui.js';
import { snapScroll } from './dom-guard.js';

export async function renderKnowledge(el, tabType, scope) {
    if (!el) return;   // a held refresh draining after a palace↔classic engine swap (D1#3)
    const isAI = tabType === 'ai';
    const resp = await fetch(`/api/knowledge/tabs?scope=${encodeURIComponent(scope)}&type=${tabType}`);
    if (!resp.ok) { el.innerHTML = '<div class="mind-empty">Failed to load</div>'; return; }
    const data = await resp.json();
    const tabs = data.tabs || [];

    // Carry scroll + which categories the user had open across the rebuild
    // (this paints on her save_knowledge and every librarian pass — every
    // open accordion collapsed and its lazy entries reverted to "Click to
    // load"). DOM-refresh hunt 2026-09-08; idiom from views/heartbeat.js.
    const restoreScroll = snapScroll(el);
    const openIds = [...el.querySelectorAll('details.mind-accordion[open] .mind-tab-entries')]
        .map(d => d.dataset.tabId);

    el.innerHTML = `
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
                        <button class="mind-btn-sm mind-export-tab" data-id="${t.id}" data-name="${escAttr(t.name)}" title="Export">⇩</button>
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

    el.querySelector('#mind-new-tab')?.addEventListener('click', async () => {
        const name = prompt('Category name:');
        if (!name) return;
        try {
            const r = await fetch('/api/knowledge/tabs', {
                method: 'POST',
                headers: csrfHeaders({ 'Content-Type': 'application/json' }),
                body: JSON.stringify({ name: name.trim(), scope, type: 'user' })
            });
            if (r.ok) { ui.showToast('Category created', 'success'); await renderKnowledge(el, tabType, scope); }
            else { const err = await r.json(); ui.showToast(err.detail || 'Failed', 'error'); }
        } catch (e) { ui.showToast('Failed', 'error'); }
    });

    el.querySelectorAll('.mind-del-tab').forEach(btn => {
        btn.addEventListener('click', async (e) => {
            e.preventDefault();
            e.stopPropagation();
            const name = btn.closest('.mind-accordion')?.querySelector('.mind-accordion-title')?.textContent || 'this category';
            if (!confirm(`Delete "${name}" and all its entries?`)) return;
            try {
                const r = await fetch(`/api/knowledge/tabs/${btn.dataset.id}`, { method: 'DELETE', headers: csrfHeaders() });
                if (r.ok) { ui.showToast('Deleted', 'success'); await renderKnowledge(el, tabType, scope); }
            } catch (e) { ui.showToast('Failed', 'error'); }
        });
    });

    el.querySelectorAll('.mind-export-tab').forEach(btn => {
        btn.addEventListener('click', async (e) => {
            e.preventDefault();
            e.stopPropagation();
            const tabId = parseInt(btn.dataset.id);
            const tabName = btn.dataset.name;
            try {
                const r = await fetch(`/api/knowledge/tabs/${tabId}/export?scope=${encodeURIComponent(scope)}`);
                if (!r.ok) throw new Error('Export failed');
                const d = await r.json();
                showExportDialog({
                    type: 'Knowledge Tab',
                    name: `${tabName} (${d.count} entries)`,
                    filename: `knowledge-${tabName.replace(/\s+/g, '_')}.json`,
                    data: d,
                });
            } catch (e) { ui.showToast(e.message, 'error'); }
        });
    });

    el.querySelector('#mind-import-tab')?.addEventListener('click', () => {
        showImportDialog({
            type: 'Knowledge Tab',
            overwrites: [{ key: 'overwrite', label: 'Overwrite if tab already exists' }],
            existingNames: tabs.map(t => t.name),
            validate: (d) => (d.entries && Array.isArray(d.entries) && d.name) ? null : 'Invalid format: needs name and entries array',
            getName: (d) => d.name || 'imported',
            onImport: async (data, { name, overwrites }) => {
                const r = await fetch('/api/knowledge/tabs/import', {
                    method: 'POST',
                    headers: csrfHeaders({ 'Content-Type': 'application/json' }),
                    body: JSON.stringify({
                        name, entries: data.entries, scope,
                        description: data.description, tab_type: data.tab_type || tabType,
                        overwrite: overwrites.overwrite || false,
                    }),
                });
                if (!r.ok) throw new Error('Import failed');
                const result = await r.json();
                const msg = result.merged
                    ? `Merged ${result.imported} entries, ${result.skipped} duplicates skipped`
                    : `Imported ${result.imported} entries`;
                ui.showToast(msg, 'success');
            },
            onDone: async () => { await renderKnowledge(el, tabType, scope); },
        });
    });

    el.querySelector('#mind-find-dups')?.addEventListener('click', async () => {
        const btn = el.querySelector('#mind-find-dups');
        const resultsDiv = el.querySelector('#mind-dup-results');
        if (!resultsDiv) return;
        btn.disabled = true;
        btn.textContent = 'Scanning...';
        resultsDiv.style.display = 'block';
        resultsDiv.innerHTML = '<div class="mind-empty">Scanning for duplicates...</div>';
        try {
            const r = await fetch(`/api/knowledge/dedup?scope=${encodeURIComponent(scope)}`);
            if (!r.ok) throw new Error('Scan failed');
            const data = await r.json();
            const dups = data.duplicates || {};
            const stats = data.stats || {};
            if (stats.total_duplicate_groups === 0) {
                resultsDiv.innerHTML = '<div class="mind-dup-clean">No duplicates found</div>';
                btn.textContent = 'Find Duplicates';
                btn.disabled = false;
                return;
            }
            let html = `<div class="mind-dup-header">Found ${stats.total_duplicate_groups} duplicate group(s) in ${stats.total_entries} entries</div>`;
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
            resultsDiv.querySelectorAll('.mind-dup-resolve').forEach(resolveBtn => {
                resolveBtn.addEventListener('click', async () => {
                    const ids = JSON.parse(resolveBtn.dataset.ids);
                    if (!confirm(`Delete ${ids.length} duplicate entry/entries?`)) return;
                    resolveBtn.disabled = true;
                    resolveBtn.textContent = 'Removing...';
                    try {
                        const rr = await fetch('/api/knowledge/dedup/resolve', {
                            method: 'DELETE',
                            headers: csrfHeaders({ 'Content-Type': 'application/json' }),
                            body: JSON.stringify({ ids }),
                        });
                        if (rr.ok) {
                            const result = await rr.json();
                            ui.showToast(`Removed ${result.deleted} duplicate(s)`, 'success');
                            resolveBtn.closest('.mind-dup-group').remove();
                            await renderKnowledge(el, tabType, scope);
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

    el.querySelectorAll('.mind-accordion').forEach(details => {
        details.addEventListener('toggle', async () => {
            if (!details.open) return;
            const inner = details.querySelector('.mind-tab-entries');
            if (!inner || inner.dataset.loaded) return;
            inner.dataset.loaded = 'true';
            await loadEntries(inner, parseInt(inner.dataset.tabId), inner.dataset.type, scope);
        });
    });
    // Re-open what was open (setting .open fires 'toggle' → entries reload).
    for (const id of openIds) {
        const det = el.querySelector(`.mind-tab-entries[data-tab-id="${id}"]`)?.closest('details');
        if (det) det.open = true;
    }
    restoreScroll();
}

async function loadEntries(inner, tabId, tabType, scope) {
    const isAI = tabType === 'ai';
    try {
        const resp = await fetch(`/api/knowledge/tabs/${tabId}?scope=${encodeURIComponent(scope)}`);
        if (!resp.ok) { inner.innerHTML = '<div class="mind-empty">Failed to load</div>'; return; }
        const data = await resp.json();
        const entries = data.entries || [];

        const fileGroups = {};
        const loose = [];
        for (const e of entries) {
            if (e.source_filename) {
                if (!fileGroups[e.source_filename]) fileGroups[e.source_filename] = [];
                fileGroups[e.source_filename].push(e);
            } else { loose.push(e); }
        }
        const filenames = Object.keys(fileGroups).sort();

        let html = '';
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
        if (!isAI) {
            html += `<div class="mind-entry-actions">
                <button class="mind-btn mind-add-entry" data-tab-id="${tabId}">+ Add Entry</button>
                <button class="mind-btn mind-upload-file" data-tab-id="${tabId}">+ Add File</button>
                <input type="file" class="mind-file-input" style="display:none"
                    accept=".txt,.md,.py,.js,.ts,.html,.css,.json,.csv,.xml,.yml,.yaml,.log,.cfg,.ini,.conf,.sh,.bat,.toml,.rs,.go,.java,.c,.cpp,.h,.rb,.php,.sql,.r,.m">
            </div>`;
        }
        if (!entries.length && !html.includes('mind-entry-actions')) html = `<div class="mind-empty">Empty</div>` + html;
        if (!entries.length && isAI) html = `<div class="mind-empty">No AI notes yet</div>`;

        inner.innerHTML = html;

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
                    const r = await fetch(`/api/knowledge/tabs/${btn.dataset.tabId}/upload`, { method: 'POST', headers: csrfHeaders(), body: form });
                    if (r.ok) {
                        const result = await r.json();
                        ui.showToast(`Uploaded ${result.filename} (${result.chunks} chunks)`, 'success');
                        inner.dataset.loaded = '';
                        await loadEntries(inner, tabId, tabType, scope);
                    } else {
                        const err = await r.json();
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

        inner.querySelectorAll('.mind-del-file').forEach(btn => {
            btn.addEventListener('click', async () => {
                const fname = btn.dataset.filename;
                if (!confirm(`Delete all chunks from "${fname}"?`)) return;
                try {
                    const r = await fetch(`/api/knowledge/tabs/${btn.dataset.tabId}/file/${encodeURIComponent(fname)}`, { method: 'DELETE', headers: csrfHeaders() });
                    if (r.ok) { ui.showToast(`Deleted ${fname}`, 'success'); inner.dataset.loaded = ''; await loadEntries(inner, tabId, tabType, scope); }
                } catch (e) { ui.showToast('Failed', 'error'); }
            });
        });

        inner.querySelectorAll('.mind-add-entry').forEach(btn => {
            btn.addEventListener('click', () => showAddEntryModal(inner, parseInt(btn.dataset.tabId), tabType, scope));
        });

        inner.querySelectorAll('.mind-edit-entry').forEach(btn => {
            btn.addEventListener('click', async () => {
                const item = btn.closest('.mind-item');
                const c = item.querySelector('.mind-item-content').textContent;
                showEntryEditModal(inner, tabId, tabType, scope, parseInt(btn.dataset.id), c);
            });
        });

        inner.querySelectorAll('.mind-del-entry').forEach(btn => {
            btn.addEventListener('click', async () => {
                if (!confirm('Delete this entry?')) return;
                try {
                    const r = await fetch(`/api/knowledge/entries/${btn.dataset.id}`, { method: 'DELETE', headers: csrfHeaders() });
                    if (r.ok) { ui.showToast('Deleted', 'success'); inner.dataset.loaded = ''; await loadEntries(inner, tabId, tabType, scope); }
                } catch (e) { ui.showToast('Failed', 'error'); }
            });
        });
    } catch (e) {
        inner.innerHTML = `<div class="mind-empty">Error: ${e.message}</div>`;
    }
}

function showEntryEditModal(inner, tabId, tabType, scope, entryId, content) {
    document.querySelector('.mind-modal-overlay')?.remove();
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
            const r = await fetch(`/api/knowledge/entries/${entryId}`, {
                method: 'PUT',
                headers: csrfHeaders({ 'Content-Type': 'application/json' }),
                body: JSON.stringify({ content: newContent })
            });
            if (r.ok) { close(); ui.showToast('Entry updated', 'success'); inner.dataset.loaded = ''; await loadEntries(inner, tabId, tabType, scope); }
            else { const err = await r.json(); ui.showToast(err.detail || 'Failed', 'error'); }
        } catch (e) { ui.showToast('Failed', 'error'); }
    });
}

function showAddEntryModal(inner, tabId, tabType, scope) {
    document.querySelector('.mind-modal-overlay')?.remove();
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
        const c = overlay.querySelector('#mae-content').value.trim();
        if (!c) { ui.showToast('Content cannot be empty', 'error'); return; }
        try {
            const r = await fetch(`/api/knowledge/tabs/${tabId}/entries`, {
                method: 'POST',
                headers: csrfHeaders({ 'Content-Type': 'application/json' }),
                body: JSON.stringify({ content: c })
            });
            if (r.ok) {
                const result = await r.json();
                const msg = result.chunks ? `Added (${result.chunks} chunks)` : 'Added';
                close();
                ui.showToast(msg, 'success');
                inner.dataset.loaded = '';
                await loadEntries(inner, tabId, tabType, scope);
            } else { const err = await r.json(); ui.showToast(err.detail || 'Failed', 'error'); }
        } catch (e) { ui.showToast('Failed', 'error'); }
    });
}
