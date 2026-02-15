// views/toolsets.js - Toolset manager view
import { getToolsets, getCurrentToolset, getFunctions, activateToolset, saveCustomToolset, deleteToolset, enableFunctions, setToolsetEmoji } from '../shared/toolset-api.js';
import * as ui from '../ui.js';
import { updateScene } from '../features/scene.js';

const MODULE_ICONS = {
    meta: '\u{1F9E0}', web: '\u{1F310}', memory: '\u{1F4BE}', network: '\u{1F4E1}',
    docs: '\u{1F4DA}', ai: '\u{1F916}', image: '\u{1F3A8}', notepad: '\u{1F4DD}',
    homeassistant: '\u{1F3E0}', goals: '\u{1F3AF}', knowledge: '\u{1F4D6}'
};

const DEFAULT_ICONS = {
    work: '\u{1F4BC}', smarthome: '\u{1F3E0}', personality: '\u{1F3AD}',
    all: '\u{1F4E6}', none: '\u{26D4}'
};

const EMOJI_GRID = [
    // tools & work
    '\u{1F4BC}', '\u{1F6E0}\u{FE0F}', '\u{2699}\u{FE0F}', '\u{1F527}', '\u{1F4A1}', '\u{1F50D}', '\u{1F4CB}', '\u{1F4CC}', '\u{1F4CE}', '\u{1F5C2}\u{FE0F}',
    // tech & science
    '\u{1F9E0}', '\u{1F916}', '\u{1F4BB}', '\u{1F4BE}', '\u{1F4E1}', '\u{1F512}', '\u{1F52C}', '\u{1F9EA}', '\u{1F9F2}', '\u{2697}\u{FE0F}',
    // creative & media
    '\u{1F3A8}', '\u{1F3AD}', '\u{1F3AC}', '\u{1F3B5}', '\u{1F3B8}', '\u{1F4F7}', '\u{1F4DD}', '\u{1F4DA}', '\u{1F4D6}', '\u{270F}\u{FE0F}',
    // web & comms
    '\u{1F310}', '\u{2601}\u{FE0F}', '\u{1F4AC}', '\u{1F4E8}', '\u{1F4F1}', '\u{1F4E2}', '\u{1F517}', '\u{1F4CA}', '\u{1F4C8}', '\u{1F4C9}',
    // home & places
    '\u{1F3E0}', '\u{1F3D7}\u{FE0F}', '\u{1F3EB}', '\u{1F3E5}', '\u{1F3EA}', '\u{26EA}', '\u{1F3F0}', '\u{1F3ED}', '\u{26F2}', '\u{1F6A2}',
    // space & nature
    '\u{1F680}', '\u{1F6F8}', '\u{1F30C}', '\u{2B50}', '\u{1F319}', '\u{2600}\u{FE0F}', '\u{1F30D}', '\u{1F30B}', '\u{26F0}\u{FE0F}', '\u{1F308}',
    // plants & animals
    '\u{1F331}', '\u{1F333}', '\u{1F33B}', '\u{1F340}', '\u{1F335}', '\u{1F43A}', '\u{1F989}', '\u{1F409}', '\u{1F40D}', '\u{1F41D}',
    // food & drink
    '\u{2615}', '\u{1F37A}', '\u{1F377}', '\u{1F355}', '\u{1F354}', '\u{1F363}', '\u{1F382}', '\u{1F352}', '\u{1F34E}', '\u{1F951}',
    // symbols & energy
    '\u{26A1}', '\u{1F525}', '\u{1F48E}', '\u{1F3AF}', '\u{1F6A9}', '\u{1F3C6}', '\u{1F396}\u{FE0F}', '\u{1F4A0}', '\u{269B}\u{FE0F}', '\u{267E}\u{FE0F}',
    // faces & fun
    '\u{1F60E}', '\u{1F47E}', '\u{1F383}', '\u{1F480}', '\u{1F389}', '\u{2764}\u{FE0F}', '\u{1F9CA}', '\u{1FA90}', '\u{1F3B2}', '\u{265F}\u{FE0F}'
];

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
        toolsets = (tsList || []).filter(t => t.type !== 'module');
        currentToolset = cur;
        functions = funcs;
        if (!selectedName || !toolsets.some(t => t.name === selectedName))
            selectedName = currentToolset?.name || 'all';
    } catch (e) {
        console.warn('Toolsets load failed:', e);
    }
}

function getEmoji(t) {
    return t.emoji || DEFAULT_ICONS[t.name] || '';
}

function render() {
    if (!container) return;

    const selected = toolsets.find(t => t.name === selectedName) || toolsets[0];
    const isEditable = selected?.type === 'user';
    const emoji = selected ? getEmoji(selected) : '';
    const canEditEmoji = selected && selected.type !== 'builtin';
    const hasEmoji = !!emoji;

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
                            <span class="ts-item-name">${getEmoji(t) ? getEmoji(t) + ' ' : ''}${t.name}</span>
                            <span class="ts-item-count">${t.function_count}</span>
                        </button>
                    `).join('')}
                </div>
            </div>
            <div class="panel-right">
                <div class="view-header ts-header">
                    <div class="ts-header-left">
                        ${canEditEmoji ? `
                            <div class="ts-emoji-wrap" id="ts-emoji-wrap">
                                <span class="ts-emoji-display${hasEmoji ? '' : ' empty'}" id="ts-emoji-btn" title="Click to pick emoji">${hasEmoji ? emoji : '\u{2795}'}</span>
                            </div>
                        ` : (hasEmoji ? `<span class="ts-emoji-display">${emoji}</span>` : '')}
                        <div class="ts-header-text">
                            <h2>${selected?.name || 'None'}</h2>
                            <span class="view-subtitle">${selected?.function_count || 0} functions${canEditEmoji ? ' \u00B7 <a href="#" id="ts-emoji-edit" class="ts-emoji-link">' + (hasEmoji ? 'change emoji' : 'add emoji') + '</a>' : ''}</span>
                        </div>
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

function showEmojiPicker() {
    const selected = toolsets.find(t => t.name === selectedName);
    if (!selected) return;

    // Close existing picker
    container.querySelector('.ts-emoji-picker')?.remove();

    const wrap = container.querySelector('#ts-emoji-wrap');
    if (!wrap) return;

    const picker = document.createElement('div');
    picker.className = 'ts-emoji-picker';
    picker.innerHTML = `
        <div class="ts-emoji-grid">
            ${EMOJI_GRID.map(e => `<button class="ts-emoji-opt" data-emoji="${e}">${e}</button>`).join('')}
        </div>
        <div class="ts-emoji-picker-footer">
            <button class="ts-emoji-clear" id="ts-emoji-clear">\u{2715} Remove</button>
        </div>
    `;

    wrap.appendChild(picker);

    // Pick emoji
    picker.querySelectorAll('.ts-emoji-opt').forEach(btn => {
        btn.addEventListener('click', async (e) => {
            e.stopPropagation();
            const val = btn.dataset.emoji;
            try {
                await setToolsetEmoji(selectedName, val);
                selected.emoji = val;
            } catch (err) { ui.showToast('Failed to save emoji', 'error'); }
            render();
        });
    });

    // Clear emoji
    picker.querySelector('#ts-emoji-clear')?.addEventListener('click', async (e) => {
        e.stopPropagation();
        try {
            await setToolsetEmoji(selectedName, '');
            selected.emoji = '';
        } catch (err) { ui.showToast('Failed to save emoji', 'error'); }
        render();
    });

    // Close on outside click
    const close = (e) => {
        if (!picker.contains(e.target) && e.target !== container.querySelector('#ts-emoji-btn')) {
            picker.remove();
            document.removeEventListener('click', close);
        }
    };
    setTimeout(() => document.addEventListener('click', close), 0);
}

function renderFunctions(selected, isEditable) {
    if (!functions?.modules) return '<p class="text-muted" style="padding:20px">No functions available</p>';

    const enabledSet = new Set();
    if (selected?.name === 'all') {
        Object.values(functions.modules).forEach(mod =>
            (mod.functions || []).forEach(f => enabledSet.add(f.name))
        );
    } else if (selected?.functions) {
        selected.functions.forEach(f => enabledSet.add(f));
    } else if (functions?.enabled) {
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

    // Emoji picker
    container.querySelector('#ts-emoji-btn')?.addEventListener('click', (e) => {
        e.stopPropagation();
        showEmojiPicker();
    });
    container.querySelector('#ts-emoji-edit')?.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        showEmojiPicker();
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
            selectedName = 'all';
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

function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}
