// views/prompts.js - Prompt editor view
import { listPrompts, getPrompt, getComponents, savePrompt, deletePrompt, saveComponent, deleteComponent, loadPrompt } from '../shared/prompt-api.js';
import * as ui from '../ui.js';
import { updateScene } from '../features/scene.js';

let container = null;
let prompts = [];
let components = {};
let selected = null;
let selectedData = null;
let saveTimer = null;
let activePromptName = null;

const SINGLE_TYPES = ['character', 'location', 'goals', 'relationship', 'format', 'scenario'];
const MULTI_TYPES = ['extras', 'emotions'];

export default {
    init(el) {
        container = el;
    },

    async show() {
        await loadAll();
        render();
    },

    hide() {}
};

async function loadAll() {
    try {
        const [pList, comps] = await Promise.all([
            listPrompts(),
            getComponents()
        ]);
        prompts = pList || [];
        components = comps || {};

        // Find active prompt
        const active = prompts.find(p => p.active);
        activePromptName = active?.name || null;

        if (!selected && activePromptName) {
            selected = activePromptName;
        } else if (!selected && prompts.length > 0) {
            selected = prompts[0].name;
        }

        if (selected) {
            try {
                selectedData = await getPrompt(selected);
            } catch { selectedData = null; }
        }
    } catch (e) {
        console.warn('Prompts load failed:', e);
    }
}

function render() {
    if (!container) return;

    container.innerHTML = `
        <div class="two-panel">
            <div class="panel-right panel-detail">
                ${selected ? renderDetail() : '<div class="view-placeholder"><p>Select a prompt</p></div>'}
            </div>
            <div class="panel-left panel-roster">
                <div class="panel-list-header">
                    <span class="panel-list-title">Prompts</span>
                    <button class="btn-sm" id="ps-new">+</button>
                </div>
                <div class="panel-list-items" id="ps-list">
                    ${prompts.map(p => `
                        <button class="panel-list-item${p.name === selected ? ' active' : ''}" data-name="${p.name}">
                            <span class="ps-item-name">${p.privacy_required ? '\uD83D\uDD12 ' : ''}${p.name}</span>
                            <span class="ps-item-type">${p.type === 'monolith' ? 'M' : 'A'}</span>
                        </button>
                    `).join('')}
                </div>
            </div>
        </div>
    `;

    bindEvents();
}

function renderDetail() {
    if (!selectedData) return '<div class="view-placeholder"><p>Loading...</p></div>';

    const p = selectedData;
    const isActive = selected === activePromptName;
    const isMonolith = p.type === 'monolith';

    return `
        <div class="view-header">
            <div class="view-header-left">
                <h2>${p.privacy_required ? '\uD83D\uDD12 ' : ''}${selected}</h2>
                <span class="view-subtitle">${p.type === 'monolith' ? 'Monolith' : 'Assembled'}${p.char_count ? ` \u2022 ${formatCount(p.char_count)} chars` : ''}</span>
            </div>
            <div class="view-header-actions">
                ${!isActive ? `<button class="btn-primary" id="ps-activate">Activate</button>` : '<span class="badge badge-active">Active</span>'}
                <button class="btn-sm" id="ps-io" title="Import/Export">Import/Export</button>
                <button class="btn-sm" id="ps-preview" title="Preview compiled prompt">Preview</button>
                <button class="btn-sm danger" id="ps-delete">Delete</button>
            </div>
        </div>
        <div class="view-body view-scroll">
            <div class="ps-editor">
                ${isMonolith ? renderMonolith(p) : renderAssembled(p)}
                <div class="ps-privacy-row">
                    <label><input type="checkbox" id="ps-privacy" ${p.privacy_required ? 'checked' : ''}> Private only (requires Privacy Mode)</label>
                </div>
            </div>
        </div>
    `;
}

function renderMonolith(p) {
    return `
        <div class="ps-monolith">
            <textarea id="ps-content" class="ps-textarea" placeholder="Enter your prompt here...">${escapeHtml(p.content || '')}</textarea>
        </div>
    `;
}

function renderAssembled(p) {
    const comps = p.components || {};
    return `
        <div class="ps-assembled">
            ${SINGLE_TYPES.map(type => {
                const options = components[type] ? Object.keys(components[type]) : [];
                const current = comps[type] || '';
                return `
                    <div class="ps-comp-row">
                        <label class="ps-comp-label">${type}</label>
                        <select class="ps-comp-select" data-comp="${type}">
                            <option value="">None</option>
                            ${options.map(k => `<option value="${k}" ${k === current ? 'selected' : ''}>${k}</option>`).join('')}
                        </select>
                        <button class="btn-icon" data-action="edit-comp" data-type="${type}" title="Edit definitions">&#x270F;</button>
                    </div>
                `;
            }).join('')}
            ${MULTI_TYPES.map(type => {
                const options = components[type] ? Object.keys(components[type]) : [];
                const current = comps[type] || [];
                return `
                    <div class="ps-comp-row">
                        <label class="ps-comp-label">${type}</label>
                        <span class="ps-multi-display" data-comp="${type}">${current.length ? current.join(', ') : 'none'}</span>
                        <button class="btn-icon" data-action="edit-multi" data-type="${type}" title="Edit selections">&#x270F;</button>
                    </div>
                `;
            }).join('')}
        </div>
    `;
}

function bindEvents() {
    // Roster selection
    container.querySelector('#ps-list')?.addEventListener('click', async e => {
        const item = e.target.closest('.panel-list-item');
        if (!item) return;
        selected = item.dataset.name;
        try {
            selectedData = await getPrompt(selected);
        } catch { selectedData = null; }
        render();
    });

    // New prompt
    container.querySelector('#ps-new')?.addEventListener('click', async () => {
        const name = prompt('New prompt name:');
        if (!name?.trim()) return;
        const type = confirm('Create as Assembled prompt?\n\nOK = Assembled (components)\nCancel = Monolith (free text)') ? 'assembled' : 'monolith';

        const data = type === 'monolith'
            ? { type: 'monolith', content: 'Enter your prompt here...', privacy_required: false }
            : { type: 'assembled', components: { character: 'sapphire', location: 'default', goals: 'default', relationship: 'default', format: 'default', scenario: 'default', extras: [], emotions: [] }, privacy_required: false };

        try {
            await savePrompt(name.trim(), data);
            selected = name.trim();
            await loadAll();
            render();
            ui.showToast(`Created: ${name.trim()}`, 'success');
        } catch (e) { ui.showToast(e.message || 'Failed', 'error'); }
    });

    // Activate
    container.querySelector('#ps-activate')?.addEventListener('click', async () => {
        try {
            await loadPrompt(selected);
            activePromptName = selected;
            ui.showToast(`Activated: ${selected}`, 'success');
            updateScene();
            render();
        } catch (e) {
            if (e.privacyRequired) {
                ui.showToast('Privacy Mode required for this prompt', 'error');
            } else {
                ui.showToast(e.message || 'Failed', 'error');
            }
        }
    });

    // Delete
    container.querySelector('#ps-delete')?.addEventListener('click', async () => {
        if (!confirm(`Delete "${selected}"?`)) return;
        try {
            await deletePrompt(selected);
            selected = null;
            selectedData = null;
            await loadAll();
            render();
            updateScene();
            ui.showToast('Deleted', 'success');
        } catch (e) { ui.showToast(e.message || 'Failed', 'error'); }
    });

    // Preview
    container.querySelector('#ps-preview')?.addEventListener('click', async () => {
        try {
            const data = await getPrompt(selected);
            const text = data.compiled || data.content || '(empty)';
            const pre = document.createElement('pre');
            pre.textContent = text;
            pre.style.cssText = 'max-height:60vh;overflow:auto;white-space:pre-wrap;font-size:var(--font-sm);padding:16px;background:var(--bg-tertiary);border-radius:var(--radius-md)';
            const modal = document.createElement('div');
            modal.className = 'modal-base';
            modal.style.cssText = 'position:fixed;inset:0;z-index:10000;display:flex;align-items:center;justify-content:center;background:rgba(0,0,0,0.6)';
            modal.innerHTML = `<div style="background:var(--bg-secondary);border-radius:var(--radius-lg);padding:20px;max-width:700px;width:90%;max-height:80vh;overflow:auto">
                <div style="display:flex;justify-content:space-between;margin-bottom:12px"><h3 style="margin:0">Preview: ${selected} (${formatCount(text.length)} chars)</h3><button style="background:none;border:none;color:var(--text);font-size:20px;cursor:pointer" id="ps-preview-close">&times;</button></div></div>`;
            modal.querySelector('div > div').appendChild(pre);
            document.body.appendChild(modal);
            modal.addEventListener('click', e => { if (e.target === modal) modal.remove(); });
            modal.querySelector('#ps-preview-close').addEventListener('click', () => modal.remove());
        } catch (e) { ui.showToast('Preview failed', 'error'); }
    });

    // Import/Export
    container.querySelector('#ps-io')?.addEventListener('click', () => openImportExport());

    // Privacy toggle
    container.querySelector('#ps-privacy')?.addEventListener('change', e => {
        if (selectedData) {
            selectedData.privacy_required = e.target.checked;
            debouncedSavePrompt();
        }
    });

    // Monolith content
    container.querySelector('#ps-content')?.addEventListener('input', () => {
        if (selectedData) {
            selectedData.content = container.querySelector('#ps-content').value;
            debouncedSavePrompt();
        }
    });

    // Assembled component dropdowns
    container.querySelectorAll('.ps-comp-select').forEach(sel => {
        sel.addEventListener('change', () => {
            if (selectedData?.components) {
                selectedData.components[sel.dataset.comp] = sel.value;
                debouncedSavePrompt();
            }
        });
    });

    // Edit component definitions
    container.querySelectorAll('[data-action="edit-comp"]').forEach(btn => {
        btn.addEventListener('click', () => openCompEditor(btn.dataset.type, false));
    });

    // Edit multi-select
    container.querySelectorAll('[data-action="edit-multi"]').forEach(btn => {
        btn.addEventListener('click', () => openCompEditor(btn.dataset.type, true));
    });
}

function openCompEditor(type, isMulti) {
    const defs = components[type] || {};
    const keys = Object.keys(defs);
    const currentSelection = isMulti
        ? (selectedData?.components?.[type] || [])
        : [selectedData?.components?.[type] || ''];

    const modal = document.createElement('div');
    modal.style.cssText = 'position:fixed;inset:0;z-index:10000;display:flex;align-items:center;justify-content:center;background:rgba(0,0,0,0.6)';
    modal.innerHTML = `
        <div style="background:var(--bg-secondary);border-radius:var(--radius-lg);padding:20px;max-width:600px;width:90%;max-height:80vh;overflow:auto">
            <div style="display:flex;justify-content:space-between;margin-bottom:12px">
                <h3 style="margin:0">Edit: ${type}</h3>
                <button class="comp-close" style="background:none;border:none;color:var(--text);font-size:20px;cursor:pointer">&times;</button>
            </div>
            <div class="comp-items">
                ${keys.map(k => `
                    <div class="comp-item" data-key="${k}">
                        <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px">
                            <input type="${isMulti ? 'checkbox' : 'radio'}" name="comp-sel" value="${k}"
                                ${currentSelection.includes(k) ? 'checked' : ''}>
                            <strong>${k}</strong>
                            <button class="btn-icon danger comp-del" data-key="${k}" style="margin-left:auto">&times;</button>
                        </div>
                        <textarea class="comp-text" data-key="${k}" rows="2" style="width:100%;padding:6px;background:var(--input-bg);border:1px solid var(--border);border-radius:var(--radius-sm);color:var(--text);font-size:var(--font-sm);resize:vertical">${escapeHtml(defs[k] || '')}</textarea>
                    </div>
                `).join('')}
            </div>
            <div style="display:flex;gap:8px;margin-top:12px">
                <button class="btn-sm" id="comp-add">+ Add New</button>
                <div style="flex:1"></div>
                <button class="btn-sm" id="comp-cancel">Cancel</button>
                <button class="btn-primary" id="comp-save">Save</button>
            </div>
        </div>
    `;

    document.body.appendChild(modal);

    // Close
    const close = () => modal.remove();
    modal.addEventListener('click', e => { if (e.target === modal) close(); });
    modal.querySelector('.comp-close').addEventListener('click', close);
    modal.querySelector('#comp-cancel').addEventListener('click', close);

    // Delete component
    modal.querySelectorAll('.comp-del').forEach(btn => {
        btn.addEventListener('click', async () => {
            const key = btn.dataset.key;
            if (!confirm(`Delete "${key}" from ${type}?`)) return;
            try {
                await deleteComponent(type, key);
                delete components[type][key];
                btn.closest('.comp-item').remove();
                ui.showToast('Deleted', 'success');
            } catch (e) { ui.showToast('Failed', 'error'); }
        });
    });

    // Add new
    modal.querySelector('#comp-add').addEventListener('click', () => {
        const name = prompt(`New ${type} name:`);
        if (!name?.trim()) return;
        const div = document.createElement('div');
        div.className = 'comp-item';
        div.dataset.key = name.trim();
        div.innerHTML = `
            <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px">
                <input type="${isMulti ? 'checkbox' : 'radio'}" name="comp-sel" value="${name.trim()}" checked>
                <strong>${name.trim()}</strong>
            </div>
            <textarea class="comp-text" data-key="${name.trim()}" rows="2" style="width:100%;padding:6px;background:var(--input-bg);border:1px solid var(--border);border-radius:var(--radius-sm);color:var(--text);font-size:var(--font-sm);resize:vertical" placeholder="Enter definition..."></textarea>
        `;
        modal.querySelector('.comp-items').appendChild(div);
    });

    // Save
    modal.querySelector('#comp-save').addEventListener('click', async () => {
        try {
            // Save all text changes
            const textareas = modal.querySelectorAll('.comp-text');
            for (const ta of textareas) {
                const key = ta.dataset.key;
                const newText = ta.value;
                if (!components[type]) components[type] = {};
                if (components[type][key] !== newText) {
                    await saveComponent(type, key, newText);
                    components[type][key] = newText;
                }
            }

            // Update selection
            if (selectedData?.components) {
                if (isMulti) {
                    const checked = [];
                    modal.querySelectorAll('input[name="comp-sel"]:checked').forEach(cb => checked.push(cb.value));
                    selectedData.components[type] = checked;
                } else {
                    const sel = modal.querySelector('input[name="comp-sel"]:checked');
                    selectedData.components[type] = sel?.value || '';
                }
                await savePrompt(selected, selectedData);
                if (selected === activePromptName) await loadPrompt(selected);
            }

            ui.showToast('Saved', 'success');
            close();
            render();
            updateScene();
        } catch (e) { ui.showToast(e.message || 'Save failed', 'error'); }
    });
}

function openImportExport() {
    const modal = document.createElement('div');
    modal.style.cssText = 'position:fixed;inset:0;z-index:10000;display:flex;align-items:center;justify-content:center;background:rgba(0,0,0,0.6)';
    modal.innerHTML = `
        <div style="background:var(--bg-secondary);border-radius:var(--radius-lg);padding:20px;max-width:550px;width:90%;max-height:80vh;overflow:auto">
            <div style="display:flex;justify-content:space-between;margin-bottom:16px">
                <h3 style="margin:0">Import / Export: ${selected}</h3>
                <button class="io-close" style="background:none;border:none;color:var(--text);font-size:20px;cursor:pointer">&times;</button>
            </div>

            <div style="margin-bottom:16px">
                <h4 style="margin:0 0 8px;font-size:var(--font-sm);color:var(--text-secondary)">Export</h4>
                <label style="display:flex;align-items:center;gap:8px;margin-bottom:8px;font-size:var(--font-sm)">
                    <input type="checkbox" id="io-export-pieces" checked> Include prompt pieces (component definitions)
                </label>
                <div style="display:flex;gap:8px">
                    <button class="btn-sm" id="io-export-clip">Copy to Clipboard</button>
                    <button class="btn-sm" id="io-export-file">Download File</button>
                </div>
            </div>

            <hr style="border:none;border-top:1px solid var(--border);margin:16px 0">

            <div>
                <h4 style="margin:0 0 8px;font-size:var(--font-sm);color:var(--text-secondary)">Import</h4>
                <label style="display:flex;align-items:center;gap:8px;margin-bottom:8px;font-size:var(--font-sm)">
                    <input type="checkbox" id="io-import-pieces"> Overwrite prompt pieces with imported definitions
                </label>
                <div style="display:flex;gap:8px;margin-bottom:8px">
                    <button class="btn-sm" id="io-import-clip">Paste from Clipboard</button>
                    <button class="btn-sm" id="io-import-file">Upload File</button>
                    <input type="file" id="io-file-input" accept=".json" style="display:none">
                </div>
                <div id="io-status" style="font-size:var(--font-sm);color:var(--text-muted)"></div>
            </div>
        </div>
    `;

    document.body.appendChild(modal);
    const close = () => modal.remove();
    modal.addEventListener('click', e => { if (e.target === modal) close(); });
    modal.querySelector('.io-close').addEventListener('click', close);

    // --- Export ---
    async function buildExport() {
        const bundle = { name: selected, prompt: selectedData };
        if (modal.querySelector('#io-export-pieces').checked) {
            bundle.components = components;
        }
        return bundle;
    }

    modal.querySelector('#io-export-clip').addEventListener('click', async () => {
        try {
            const data = await buildExport();
            await navigator.clipboard.writeText(JSON.stringify(data, null, 2));
            ui.showToast('Copied to clipboard', 'success');
        } catch (e) { ui.showToast('Copy failed', 'error'); }
    });

    modal.querySelector('#io-export-file').addEventListener('click', async () => {
        const data = await buildExport();
        const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `${selected}.prompt.json`;
        a.click();
        URL.revokeObjectURL(url);
        ui.showToast('Downloaded', 'success');
    });

    // --- Import ---
    async function doImport(json) {
        const status = modal.querySelector('#io-status');
        try {
            const data = JSON.parse(json);
            if (!data.prompt) { status.textContent = 'Invalid format: missing prompt data'; return; }

            const name = data.name || selected;
            status.textContent = `Importing "${name}"...`;

            await savePrompt(name, data.prompt);

            const importPieces = data.components || data.pieces;
            if (modal.querySelector('#io-import-pieces').checked && importPieces) {
                for (const [type, defs] of Object.entries(importPieces)) {
                    for (const [key, value] of Object.entries(defs)) {
                        await saveComponent(type, key, value);
                    }
                }
            }

            if (name === activePromptName) await loadPrompt(name);
            selected = name;
            await loadAll();
            render();
            updateScene();
            close();
            ui.showToast(`Imported: ${name}`, 'success');
        } catch (e) {
            status.textContent = `Error: ${e.message}`;
        }
    }

    modal.querySelector('#io-import-clip').addEventListener('click', async () => {
        try {
            const text = await navigator.clipboard.readText();
            await doImport(text);
        } catch (e) {
            modal.querySelector('#io-status').textContent = 'Clipboard read failed (check permissions)';
        }
    });

    modal.querySelector('#io-import-file').addEventListener('click', () => {
        modal.querySelector('#io-file-input').click();
    });

    modal.querySelector('#io-file-input').addEventListener('change', e => {
        const file = e.target.files[0];
        if (!file) return;
        const reader = new FileReader();
        reader.onload = () => doImport(reader.result);
        reader.readAsText(file);
    });
}

function debouncedSavePrompt() {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(async () => {
        if (!selected || !selectedData) return;
        try {
            await savePrompt(selected, selectedData);
            if (selected === activePromptName) await loadPrompt(selected);
            updateScene();
        } catch (e) {
            ui.showToast('Save failed', 'error');
        }
    }, 800);
}

function formatCount(n) {
    return n >= 1000 ? (n / 1000).toFixed(1) + 'k' : n;
}

function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}
