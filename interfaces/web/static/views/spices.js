// views/spices.js - Spice manager view
import { getSpices, addSpice, updateSpice, deleteSpice, addCategory, renameCategory, deleteCategory, toggleCategory, reloadSpices } from '../shared/spice-api.js';
import * as ui from '../ui.js';

let container = null;
let data = null;

export default {
    init(el) {
        container = el;
    },

    async show() {
        await render();
    },

    hide() {}
};

async function render() {
    try {
        data = await getSpices();
    } catch (e) {
        container.innerHTML = '<div class="view-placeholder"><h2>Spices</h2><p>Failed to load spices</p></div>';
        return;
    }

    const cats = data.categories || {};
    const catNames = Object.keys(cats);

    container.innerHTML = `
        <div class="view-header">
            <div class="view-header-left">
                <h2>Spices</h2>
                <span class="view-subtitle">${data.total_spices || 0} spices in ${data.category_count || 0} categories</span>
            </div>
            <div class="view-header-actions">
                <button class="btn-sm" id="spice-add-cat">+ Category</button>
                <button class="btn-sm" id="spice-reload" title="Reload from disk">Reload</button>
            </div>
        </div>
        <div class="view-body view-scroll">
            <div class="spice-list">
                ${catNames.length === 0 ? '<p class="text-muted" style="text-align:center;padding:40px">No spice categories yet. Click "+ Category" to create one.</p>' :
                catNames.map(name => renderCategory(name, cats[name])).join('')}
            </div>
        </div>
    `;

    bindEvents();
}

function renderCategory(name, cat) {
    const spices = cat.spices || [];
    const emoji = cat.emoji || '';
    const desc = cat.description || '';
    return `
        <details class="spice-cat" data-category="${name}">
            <summary class="spice-cat-header">
                <span class="spice-cat-icon">${emoji || '🧂'}</span>
                <div class="spice-cat-info">
                    <span class="spice-cat-name">${name} <span class="spice-cat-count">(${spices.length})</span></span>
                    ${desc ? `<span class="spice-cat-desc">${escapeHtml(desc)}</span>` : ''}
                </div>
                <div class="spice-cat-controls" onclick="event.stopPropagation()">
                    <label class="toggle-switch" onclick="event.stopPropagation()">
                        <input type="checkbox" ${cat.enabled ? 'checked' : ''} data-action="toggle-cat" data-cat="${name}">
                        <span class="toggle-slider"></span>
                    </label>
                </div>
            </summary>
            <div class="spice-cat-body">
                <div class="spice-cat-inner${cat.enabled ? '' : ' disabled'}">
                    <div class="spice-cat-actions">
                        <button class="btn-sm" data-action="add-spice" data-cat="${name}">+ Spice</button>
                        <button class="btn-icon" data-action="rename-cat" data-cat="${name}" title="Rename">&#x270F;</button>
                        <button class="btn-icon danger" data-action="delete-cat" data-cat="${name}" title="Delete category">&times;</button>
                    </div>
                    ${spices.length === 0 ? '<div class="text-muted" style="padding:8px;font-size:var(--font-sm)">Empty — add a spice above</div>' :
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
}

function bindEvents() {
    container.querySelector('#spice-add-cat')?.addEventListener('click', async () => {
        const name = prompt('New category name:');
        if (!name?.trim()) return;
        try {
            await addCategory(name.trim());
            ui.showToast(`Created: ${name.trim()}`, 'success');
            await render();
        } catch (e) { ui.showToast(e.message || 'Failed', 'error'); }
    });

    container.querySelector('#spice-reload')?.addEventListener('click', async () => {
        try {
            await reloadSpices();
            ui.showToast('Reloaded from disk', 'success');
            await render();
        } catch (e) { ui.showToast('Reload failed', 'error'); }
    });

    // Event delegation for card actions
    container.querySelector('.spice-list')?.addEventListener('click', handleAction);
    container.querySelector('.spice-list')?.addEventListener('change', handleAction);
}

async function handleAction(e) {
    const btn = e.target.closest('[data-action]') || (e.target.dataset.action ? e.target : null);
    if (!btn) return;

    const action = btn.dataset.action;
    const cat = btn.dataset.cat;
    const idx = btn.dataset.idx !== undefined ? parseInt(btn.dataset.idx) : null;

    try {
        if (action === 'toggle-cat') {
            await toggleCategory(cat);
            await render();
        } else if (action === 'add-spice') {
            const text = prompt(`Add spice to "${cat}":`);
            if (!text?.trim()) return;
            await addSpice(cat, text.trim());
            ui.showToast('Added', 'success');
            await render();
        } else if (action === 'edit-spice') {
            const cats = data.categories || {};
            const current = cats[cat]?.spices?.[idx] || '';
            const text = prompt('Edit spice:', current);
            if (text === null || text === current) return;
            await updateSpice(cat, idx, text);
            ui.showToast('Updated', 'success');
            await render();
        } else if (action === 'delete-spice') {
            if (!confirm('Delete this spice?')) return;
            await deleteSpice(cat, idx);
            ui.showToast('Deleted', 'success');
            await render();
        } else if (action === 'rename-cat') {
            const newName = prompt(`Rename "${cat}" to:`);
            if (!newName?.trim() || newName.trim() === cat) return;
            await renameCategory(cat, newName.trim());
            ui.showToast(`Renamed to ${newName.trim()}`, 'success');
            await render();
        } else if (action === 'delete-cat') {
            if (!confirm(`Delete category "${cat}" and all its spices?`)) return;
            await deleteCategory(cat);
            ui.showToast(`Deleted: ${cat}`, 'success');
            await render();
        }
    } catch (e) {
        ui.showToast(e.message || 'Failed', 'error');
    }
}

function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}
