// views/spices.js - Spice manager view with spice sets
import { getSpices, addSpice, updateSpice, deleteSpice, addCategory, renameCategory, deleteCategory, toggleCategory, reloadSpices,
         getSpiceSets, getCurrentSpiceSet, activateSpiceSet, saveCustomSpiceSet, deleteSpiceSet, setSpiceSetEmoji } from '../shared/spice-api.js';
import { renderPersonaTabs, bindPersonaTabs } from '../shared/persona-tabs.js';
import * as ui from '../ui.js';
import { updateScene } from '../features/scene.js';

const DEFAULT_ICONS = {
    default: '\u{1F336}\u{FE0F}', companion: '\u{1F49C}', professional: '\u{1F4BC}',
    all: '\u{1F525}', none: '\u{26D4}'
};

const EMOJI_GRID = [
    '\u{1F336}\u{FE0F}', '\u{1F525}', '\u{2728}', '\u{1F49C}', '\u{1F4BC}', '\u{1F3AD}', '\u{1F30C}', '\u{1F680}', '\u{1F308}', '\u{26A1}',
    '\u{1F60E}', '\u{1F47E}', '\u{1F9CA}', '\u{1FA90}', '\u{1F48E}', '\u{1F3AF}', '\u{2764}\u{FE0F}', '\u{1F389}', '\u{1F31F}', '\u{1F52E}',
    '\u{1F33B}', '\u{1F335}', '\u{1F340}', '\u{1F30D}', '\u{2615}', '\u{1F377}', '\u{1F37A}', '\u{1F363}', '\u{1F382}', '\u{1F951}'
];

let container = null;
let spiceSets = [];
let currentSetName = null;
let selectedSetName = null;
let spiceData = null;
let saveTimer = null;

export default {
    init(el) { container = el; },
    async show() {
        await loadData();
        render();
    },
    hide() {}
};

async function loadData() {
    try {
        const [sets, cur, spices] = await Promise.all([
            getSpiceSets(),
            getCurrentSpiceSet(),
            getSpices()
        ]);
        spiceSets = sets || [];
        currentSetName = cur;
        spiceData = spices;
        if (!selectedSetName || !spiceSets.some(s => s.name === selectedSetName))
            selectedSetName = currentSetName || 'default';
    } catch (e) {
        console.warn('Spice sets load failed:', e);
    }
}

function getEmoji(s) {
    return s.emoji || DEFAULT_ICONS[s.name] || '';
}

function render() {
    if (!container) return;

    const selected = spiceSets.find(s => s.name === selectedSetName) || spiceSets[0];
    const emoji = selected ? getEmoji(selected) : '';
    const hasEmoji = !!emoji;
    const enabledSet = new Set(selected?.categories || []);

    container.innerHTML = `
        ${renderPersonaTabs('spices')}
        <div class="two-panel">
            <div class="panel-left panel-list">
                <div class="panel-list-header">
                    <span class="panel-list-title">Spice Sets</span>
                    <button class="btn-sm" id="ss-new" title="Save current as new">+</button>
                </div>
                <div class="panel-list-items" id="ss-list">
                    ${spiceSets.map(s => `
                        <button class="panel-list-item${s.name === selectedSetName ? ' active' : ''}" data-name="${s.name}">
                            <span class="ts-item-name">${getEmoji(s) ? getEmoji(s) + ' ' : ''}${s.name}</span>
                            <span class="ts-item-count">${s.category_count}</span>
                        </button>
                    `).join('')}
                </div>
            </div>
            <div class="panel-right">
                <div class="view-header ts-header">
                    <div class="ts-header-left">
                        <div class="ts-emoji-wrap" id="ss-emoji-wrap">
                            <span class="ts-emoji-display${hasEmoji ? '' : ' empty'}" id="ss-emoji-btn" title="Click to pick emoji">${hasEmoji ? emoji : '\u{2795}'}</span>
                        </div>
                        <div class="ts-header-text">
                            <h2>${selected?.name || 'None'}</h2>
                            <span class="view-subtitle">${enabledSet.size} categories \u00B7 <a href="#" id="ss-emoji-edit" class="ts-emoji-link">${hasEmoji ? 'change emoji' : 'add emoji'}</a></span>
                        </div>
                    </div>
                    <div class="view-header-actions">
                        ${selected?.name !== currentSetName ?
                            `<button class="btn-primary" id="ss-activate">Activate</button>` :
                            `<span class="badge badge-active">Active</span>`
                        }
                        <button class="btn-sm danger" id="ss-delete">Delete</button>
                    </div>
                </div>
                <div class="view-body view-scroll">
                    <div class="ss-actions-bar">
                        <button class="btn-sm" id="spice-add-cat">+ Category</button>
                        <button class="btn-sm" id="spice-reload" title="Reload spice pool from disk">Reload Pool</button>
                    </div>
                    <div class="spice-list" id="ss-categories">
                        ${renderCategories(enabledSet)}
                    </div>
                </div>
            </div>
        </div>
    `;

    bindEvents();
}

function renderCategories(enabledSet) {
    if (!spiceData?.categories) return '<p class="text-muted" style="padding:20px">No categories available</p>';

    const cats = spiceData.categories;
    return Object.entries(cats).map(([name, cat]) => {
        const spices = cat.spices || [];
        const inSet = enabledSet.has(name);
        const catEmoji = cat.emoji || '\u{1F9C2}';

        return `
            <details class="spice-cat" data-category="${name}">
                <summary class="spice-cat-header">
                    <label class="ss-cat-check" onclick="event.stopPropagation()">
                        <input type="checkbox" data-action="toggle-cat-set" data-cat="${name}" ${inSet ? 'checked' : ''}>
                    </label>
                    <span class="spice-cat-icon">${catEmoji}</span>
                    <div class="spice-cat-info">
                        <span class="spice-cat-name">${name} <span class="spice-cat-count">(${spices.length})</span></span>
                        ${cat.description ? `<span class="spice-cat-desc">${escapeHtml(cat.description)}</span>` : ''}
                    </div>
                </summary>
                <div class="spice-cat-body">
                    <div class="spice-cat-inner">
                        <div class="spice-cat-actions">
                            <button class="btn-sm" data-action="add-spice" data-cat="${name}">+ Spice</button>
                            <button class="btn-icon" data-action="rename-cat" data-cat="${name}" title="Rename">&#x270F;</button>
                            <button class="btn-icon danger" data-action="delete-cat" data-cat="${name}" title="Delete category">&times;</button>
                        </div>
                        ${spices.length === 0 ? '<div class="text-muted" style="padding:8px;font-size:var(--font-sm)">Empty \u2014 add a spice above</div>' :
                        spices.map((text, i) => `
                            <div class="spice-item">
                                <span class="spice-text">${escapeHtml(text)}</span>
                                <div class="spice-item-actions">
                                    <button class="btn-icon" data-action="edit-spice" data-cat="${name}" data-idx="${i}" title="Edit">&#x270E;</button>
                                    <button class="btn-icon danger" data-action="delete-spice" data-cat="${name}" data-idx="${i}" title="Delete">&times;</button>
                                </div>
                            </div>
                        `).join('')}
                    </div>
                </div>
            </details>
        `;
    }).join('');
}

function showEmojiPicker() {
    const selected = spiceSets.find(s => s.name === selectedSetName);
    if (!selected) return;
    container.querySelector('.ts-emoji-picker')?.remove();
    const wrap = container.querySelector('#ss-emoji-wrap');
    if (!wrap) return;

    const picker = document.createElement('div');
    picker.className = 'ts-emoji-picker';
    picker.innerHTML = `
        <div class="ts-emoji-grid">
            ${EMOJI_GRID.map(e => `<button class="ts-emoji-opt" data-emoji="${e}">${e}</button>`).join('')}
        </div>
        <div class="ts-emoji-picker-footer">
            <button class="ts-emoji-clear" id="ss-emoji-clear">\u{2715} Remove</button>
        </div>
    `;
    wrap.appendChild(picker);

    picker.querySelectorAll('.ts-emoji-opt').forEach(btn => {
        btn.addEventListener('click', async (e) => {
            e.stopPropagation();
            try {
                await setSpiceSetEmoji(selectedSetName, btn.dataset.emoji);
                selected.emoji = btn.dataset.emoji;
            } catch (err) { ui.showToast('Failed to save emoji', 'error'); }
            render();
        });
    });

    picker.querySelector('#ss-emoji-clear')?.addEventListener('click', async (e) => {
        e.stopPropagation();
        try {
            await setSpiceSetEmoji(selectedSetName, '');
            selected.emoji = '';
        } catch (err) { ui.showToast('Failed to save emoji', 'error'); }
        render();
    });

    const close = (e) => {
        if (!picker.contains(e.target) && e.target !== container.querySelector('#ss-emoji-btn')) {
            picker.remove();
            document.removeEventListener('click', close);
        }
    };
    setTimeout(() => document.addEventListener('click', close), 0);
}

function bindEvents() {
    bindPersonaTabs(container);

    // Set list selection
    container.querySelector('#ss-list')?.addEventListener('click', e => {
        const item = e.target.closest('.panel-list-item');
        if (!item) return;
        selectedSetName = item.dataset.name;
        render();
    });

    // Emoji picker
    container.querySelector('#ss-emoji-btn')?.addEventListener('click', (e) => { e.stopPropagation(); showEmojiPicker(); });
    container.querySelector('#ss-emoji-edit')?.addEventListener('click', (e) => { e.preventDefault(); e.stopPropagation(); showEmojiPicker(); });

    // Activate
    container.querySelector('#ss-activate')?.addEventListener('click', async () => {
        try {
            await activateSpiceSet(selectedSetName);
            currentSetName = selectedSetName;
            ui.showToast(`Activated: ${selectedSetName}`, 'success');
            updateScene();
            render();
        } catch (e) { ui.showToast('Failed to activate', 'error'); }
    });

    // New set
    container.querySelector('#ss-new')?.addEventListener('click', async () => {
        const name = prompt('New spice set name:');
        if (!name?.trim()) return;
        const categories = collectEnabledCategories();
        try {
            await saveCustomSpiceSet(name.trim(), categories);
            ui.showToast(`Created: ${name.trim()}`, 'success');
            selectedSetName = name.trim();
            await loadData();
            render();
        } catch (e) { ui.showToast(e.message || 'Failed', 'error'); }
    });

    // Delete set
    container.querySelector('#ss-delete')?.addEventListener('click', async () => {
        if (!confirm(`Delete spice set "${selectedSetName}"?`)) return;
        try {
            await deleteSpiceSet(selectedSetName);
            ui.showToast(`Deleted: ${selectedSetName}`, 'success');
            selectedSetName = 'default';
            await loadData();
            render();
        } catch (e) { ui.showToast('Failed to delete', 'error'); }
    });

    // Category set membership checkboxes
    container.querySelectorAll('[data-action="toggle-cat-set"]').forEach(cb => {
        cb.addEventListener('change', () => debouncedSaveSet());
    });

    // Spice content management (add cat, reload, CRUD)
    container.querySelector('#spice-add-cat')?.addEventListener('click', async () => {
        const name = prompt('New category name:');
        if (!name?.trim()) return;
        try {
            await addCategory(name.trim());
            ui.showToast(`Created: ${name.trim()}`, 'success');
            await loadData();
            render();
        } catch (e) { ui.showToast(e.message || 'Failed', 'error'); }
    });

    container.querySelector('#spice-reload')?.addEventListener('click', async () => {
        try {
            await reloadSpices();
            ui.showToast('Reloaded from disk', 'success');
            await loadData();
            render();
        } catch (e) { ui.showToast('Reload failed', 'error'); }
    });

    // Event delegation for spice CRUD actions
    container.querySelector('#ss-categories')?.addEventListener('click', handleSpiceAction);
}

async function handleSpiceAction(e) {
    const btn = e.target.closest('[data-action]');
    if (!btn || btn.dataset.action === 'toggle-cat-set') return;

    const action = btn.dataset.action;
    const cat = btn.dataset.cat;
    const idx = btn.dataset.idx !== undefined ? parseInt(btn.dataset.idx) : null;

    try {
        if (action === 'add-spice') {
            const text = prompt(`Add spice to "${cat}":`);
            if (!text?.trim()) return;
            await addSpice(cat, text.trim());
            ui.showToast('Added', 'success');
        } else if (action === 'edit-spice') {
            const current = spiceData.categories[cat]?.spices?.[idx] || '';
            const text = prompt('Edit spice:', current);
            if (text === null || text === current) return;
            await updateSpice(cat, idx, text);
            ui.showToast('Updated', 'success');
        } else if (action === 'delete-spice') {
            if (!confirm('Delete this spice?')) return;
            await deleteSpice(cat, idx);
            ui.showToast('Deleted', 'success');
        } else if (action === 'rename-cat') {
            const newName = prompt(`Rename "${cat}" to:`);
            if (!newName?.trim() || newName.trim() === cat) return;
            await renameCategory(cat, newName.trim());
            ui.showToast(`Renamed to ${newName.trim()}`, 'success');
        } else if (action === 'delete-cat') {
            if (!confirm(`Delete category "${cat}" and all its spices?`)) return;
            await deleteCategory(cat);
            ui.showToast(`Deleted: ${cat}`, 'success');
        } else {
            return;
        }
        await loadData();
        render();
    } catch (e) {
        ui.showToast(e.message || 'Failed', 'error');
    }
}

function collectEnabledCategories() {
    const cats = [];
    container.querySelectorAll('[data-action="toggle-cat-set"]:checked').forEach(cb => {
        cats.push(cb.dataset.cat);
    });
    return cats;
}

function debouncedSaveSet() {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(async () => {
        const categories = collectEnabledCategories();
        try {
            await saveCustomSpiceSet(selectedSetName, categories);
            // Update local data
            const s = spiceSets.find(s => s.name === selectedSetName);
            if (s) { s.categories = categories; s.category_count = categories.length; }
            // Update count in left panel
            const countEl = container.querySelector(`#ss-list .panel-list-item.active .ts-item-count`);
            if (countEl) countEl.textContent = categories.length;
            // Update subtitle
            const subtitle = container.querySelector('.view-subtitle');
            if (subtitle) subtitle.innerHTML = subtitle.innerHTML.replace(/\d+ categories/, `${categories.length} categories`);
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
