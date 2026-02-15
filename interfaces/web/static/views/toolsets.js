// views/toolsets.js - Toolset manager view
import { getToolsets, getCurrentToolset, getFunctions, activateToolset, saveCustomToolset, deleteToolset, enableFunctions } from '../shared/toolset-api.js';
import * as ui from '../ui.js';
import { updateScene } from '../features/scene.js';

const MODULE_ICONS = {
    meta: '\u{1F9E0}', web: '\u{1F310}', memory: '\u{1F4BE}', network: '\u{1F4E1}',
    docs: '\u{1F4DA}', ai: '\u{1F916}', image: '\u{1F3A8}', notepad: '\u{1F4DD}',
    homeassistant: '\u{1F3E0}'
};

let container = null;
let toolsets = [];
let currentToolset = null;
let functions = null;
let selectedName = null;
let saveTimer = null;

export default {
    init(el) {
        container = el;
    },

    async show() {
        if (window._viewSelect) { selectedName = window._viewSelect; delete window._viewSelect; }
        await loadData();
        render();
    },

    hide() {}
};

async function loadData() {
    try {
        const [tsList, cur, funcs] = await Promise.all([
            getToolsets(),
            getCurrentToolset(),
            getFunctions()
        ]);
        toolsets = tsList || [];
        currentToolset = cur;
        functions = funcs;
        if (!selectedName || !toolsets.some(t => t.name === selectedName))
            selectedName = currentToolset?.name || 'default';
    } catch (e) {
        console.warn('Toolsets load failed:', e);
    }
}

function render() {
    if (!container) return;

    const selected = toolsets.find(t => t.name === selectedName) || toolsets[0];
    const isEditable = selected?.type === 'user';

    container.innerHTML = `
        <div class="two-panel">
            <div class="panel-left panel-list">
                <div class="panel-list-header">
                    <span class="panel-list-title">Toolsets</span>
                    <button class="btn-sm" id="ts-new" title="Save current as new">+</button>
                </div>
                <div class="panel-list-items" id="ts-list">
                    ${toolsets.map(t => `
                        <button class="panel-list-item${t.name === selectedName ? ' active' : ''}" data-name="${t.name}">
                            <span class="ts-item-name">${typeIcon(t.type, t.name)} ${t.name}</span>
                            <span class="ts-item-count">${t.function_count}</span>
                        </button>
                    `).join('')}
                </div>
            </div>
            <div class="panel-right">
                <div class="view-header">
                    <div class="view-header-left">
                        <h2>${selected?.name || 'None'}</h2>
                        <span class="view-subtitle">${selected?.function_count || 0} functions ${!isEditable ? '(read-only)' : ''}</span>
                    </div>
                    <div class="view-header-actions">
                        ${selected?.name !== currentToolset?.name ?
                            `<button class="btn-primary" id="ts-activate">Activate</button>` :
                            `<span class="badge badge-active">Active</span>`
                        }
                        ${isEditable ? `<button class="btn-sm danger" id="ts-delete">Delete</button>` : ''}
                    </div>
                </div>
                <div class="view-body view-scroll">
                    ${renderFunctions(selected, isEditable)}
                </div>
            </div>
        </div>
    `;

    bindEvents();
}

function renderFunctions(selected, isEditable) {
    if (!functions?.modules) return '<p class="text-muted" style="padding:20px">No functions available</p>';

    const enabledSet = new Set();
    if (selected?.functions) {
        selected.functions.forEach(f => enabledSet.add(f));
    } else if (functions?.enabled) {
        // For non-user toolsets, use the currently enabled list
        functions.enabled.forEach(f => enabledSet.add(f));
    }

    const modules = functions.modules;
    return Object.entries(modules).map(([modName, mod]) => {
        const funcs = mod.functions || [];
        const enabledCount = funcs.filter(f => enabledSet.has(f.name)).length;
        const allChecked = enabledCount === funcs.length;
        const someChecked = enabledCount > 0 && !allChecked;

        return `
            <div class="ts-module">
                <div class="ts-module-header">
                    <label class="ts-module-toggle">
                        <input type="checkbox" data-action="toggle-module" data-module="${modName}"
                            ${allChecked ? 'checked' : ''} ${someChecked ? 'data-indeterminate="true"' : ''}
                            ${!isEditable ? 'disabled' : ''}>
                        <span class="ts-module-name">${MODULE_ICONS[modName] || '\u{1F527}'} ${modName}</span>
                        <span class="ts-module-count">(${enabledCount}/${funcs.length})</span>
                    </label>
                </div>
                <div class="ts-func-list">
                    ${funcs.map(f => `
                        <label class="ts-func-item">
                            <input type="checkbox" data-action="toggle-func" data-func="${f.name}"
                                ${enabledSet.has(f.name) ? 'checked' : ''} ${!isEditable ? 'disabled' : ''}>
                            <span class="ts-func-name">${f.name}</span>
                            ${f.description ? `<span class="ts-func-desc">${escapeHtml(f.description)}</span>` : ''}
                        </label>
                    `).join('')}
                </div>
            </div>
        `;
    }).join('');
}

function bindEvents() {
    // Toolset list selection
    container.querySelector('#ts-list')?.addEventListener('click', e => {
        const item = e.target.closest('.panel-list-item');
        if (!item) return;
        selectedName = item.dataset.name;
        render();
    });

    // Activate
    container.querySelector('#ts-activate')?.addEventListener('click', async () => {
        try {
            await activateToolset(selectedName);
            currentToolset = { name: selectedName };
            ui.showToast(`Activated: ${selectedName}`, 'success');
            updateScene();
            render();
        } catch (e) { ui.showToast('Failed to activate', 'error'); }
    });

    // New
    container.querySelector('#ts-new')?.addEventListener('click', async () => {
        const name = prompt('New toolset name:');
        if (!name?.trim()) return;
        const enabled = collectEnabled();
        try {
            await saveCustomToolset(name.trim(), enabled);
            ui.showToast(`Created: ${name.trim()}`, 'success');
            selectedName = name.trim();
            await loadData();
            render();
        } catch (e) { ui.showToast(e.message || 'Failed', 'error'); }
    });

    // Delete
    container.querySelector('#ts-delete')?.addEventListener('click', async () => {
        if (!confirm(`Delete toolset "${selectedName}"?`)) return;
        try {
            await deleteToolset(selectedName);
            ui.showToast(`Deleted: ${selectedName}`, 'success');
            selectedName = 'default';
            await loadData();
            render();
        } catch (e) { ui.showToast('Failed to delete', 'error'); }
    });

    // Function toggles
    container.querySelectorAll('[data-action="toggle-func"]').forEach(cb => {
        cb.addEventListener('change', () => debouncedSave());
    });

    // Module toggles
    container.querySelectorAll('[data-action="toggle-module"]').forEach(cb => {
        cb.addEventListener('change', e => {
            const mod = e.target.dataset.module;
            const checked = e.target.checked;
            container.querySelectorAll(`[data-action="toggle-func"]`).forEach(fc => {
                const funcMod = findFuncModule(fc.dataset.func);
                if (funcMod === mod) fc.checked = checked;
            });
            debouncedSave();
        });
    });

    // Set indeterminate state
    container.querySelectorAll('[data-indeterminate="true"]').forEach(cb => {
        cb.indeterminate = true;
    });
}

function collectEnabled() {
    const enabled = [];
    container.querySelectorAll('[data-action="toggle-func"]:checked').forEach(cb => {
        enabled.push(cb.dataset.func);
    });
    return enabled;
}

function findFuncModule(funcName) {
    if (!functions?.modules) return null;
    for (const [modName, mod] of Object.entries(functions.modules)) {
        if (mod.functions?.some(f => f.name === funcName)) return modName;
    }
    return null;
}

function debouncedSave() {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(async () => {
        const enabled = collectEnabled();
        try {
            const selected = toolsets.find(t => t.name === selectedName);
            if (selected?.type === 'user') {
                await saveCustomToolset(selectedName, enabled);
            }
            await enableFunctions(enabled);
            updateScene();
        } catch (e) {
            ui.showToast('Save failed', 'error');
        }
    }, 300);
}

function typeIcon(type, name) {
    if (type === 'user') return '\u{1F6E0}\u{FE0F}';
    if (type === 'module') return MODULE_ICONS[name] || '\u{1F9E9}';
    return '\u{1F527}';
}

function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}
