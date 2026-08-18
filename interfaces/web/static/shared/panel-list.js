// shared/panel-list.js - Shared left-panel selector for section views.
//
// One titled list with select / add / delete-selected. `renderItem` is a
// callback so each row carries its own content + metadata (the prompt-token /
// scope-count detail line). Reuses the existing .two-panel / .panel-list* CSS.
// Used by the persona rosters and (wrapped by scope-sidebar) the Mind scopes.
//
// Delete placement (locked decision): a single 🗑 in the header next to +,
// acting on the SELECTED item — shown when the host opts in (`showDelete`),
// enabled only when something deletable is selected. One delete location.

function esc(s) {
    // Quote-escaping included — output lands in attribute contexts
    // (title="…", data-pl-id="…") with user-controlled strings; same class
    // as the modal.js fix (2026-07-19).
    if (s == null) return '';
    const d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML.replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

/**
 * @param {object} o
 * @param {string} o.title
 * @param {object[]} o.items
 * @param {string|null} o.selectedId
 * @param {string} [o.idKey='id']      - which field of an item is its id
 * @param {(item)=>string} o.renderItem - inner HTML for a row (label + metadata)
 * @param {string} [o.addTitle]         - tooltip for the + button; omit = no +
 * @param {string} [o.extraHeader='']   - extra header HTML (e.g. an import button)
 * @param {boolean} [o.showDelete=false]- render the header 🗑
 * @param {boolean} [o.deletable=false] - is the current selection deletable
 * @param {string} [o.deleteTitle='Delete selected']
 */
export function renderPanelList({
    title, items = [], selectedId = null, idKey = 'id', renderItem,
    addTitle, extraHeader = '', showDelete = false, deletable = false,
    deleteTitle = 'Delete selected', emptyHTML = '', listClass = '', itemClass = null,
    footer = '',
} = {}) {
    const add = addTitle
        ? `<button class="btn-sm" data-pl-action="add" title="${esc(addTitle)}">+</button>` : '';
    const del = showDelete
        ? `<button class="btn-sm danger" data-pl-action="delete" title="${esc(deleteTitle)}"${(selectedId != null && deletable) ? '' : ' disabled'}>\u{1F5D1}\u{FE0F}</button>` : '';
    const rows = items.length
        ? items.map(it => {
            const id = it[idKey];
            return `<button class="panel-list-item${id === selectedId ? ' active' : ''}${itemClass ? ' ' + itemClass(it) : ''}" data-pl-id="${esc(String(id))}">${renderItem(it)}</button>`;
        }).join('')
        : emptyHTML;
    return `
        <div class="panel-left panel-list${listClass ? ' ' + listClass : ''}">
            <div class="panel-list-header">
                <span class="panel-list-title">${esc(title)}</span>
                <div class="panel-list-actions">${extraHeader}${add}${del}</div>
            </div>
            <div class="panel-list-items">${rows}</div>
            ${footer ? `<div class="panel-list-footer">${footer}</div>` : ''}
        </div>`;
}

/** Delegated click → callbacks. Bind once per render on a stable parent. */
export function bindPanelList(container, { onSelect, onAdd, onDelete } = {}) {
    const root = container.querySelector('.panel-list');
    if (!root) return;
    root.addEventListener('click', e => {
        const act = e.target.closest('[data-pl-action]');
        if (act) {
            if (act.disabled) return;
            const a = act.dataset.plAction;
            if (a === 'add' && onAdd) onAdd();
            else if (a === 'delete' && onDelete) onDelete();
            return;
        }
        const item = e.target.closest('.panel-list-item');
        if (item && onSelect) onSelect(item.dataset.plId);
    });
}
