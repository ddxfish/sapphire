// shared/account-manager.js — Reusable multi-account list/editor shell
// Handles: list view, add scope, delete scope, navigation, CSS injection.
// Each plugin provides its own editor form via renderEditor callback.

function csrfHeaders(extra = {}) {
    const token = document.querySelector('meta[name="csrf-token"]')?.content || '';
    return { 'X-CSRF-Token': token, ...extra };
}

const INJECTED = new Set();

function injectBaseStyles(prefix) {
    const id = `${prefix}-acctmgr-styles`;
    if (INJECTED.has(id)) return;
    INJECTED.add(id);

    const style = document.createElement('style');
    style.id = id;
    style.textContent = `
        .am-form { display: flex; flex-direction: column; gap: 16px; }
        .am-list { display: flex; flex-direction: column; gap: 8px; }

        .am-item {
            display: flex; align-items: center; gap: 10px;
            padding: 10px 14px; border: 1px solid var(--border); border-radius: 8px;
            background: var(--bg-secondary); cursor: pointer; transition: all 0.15s ease;
        }
        .am-item:hover { border-color: var(--accent-blue); background: var(--bg-hover); }
        .am-item-name { font-weight: 600; font-size: 0.8125rem; color: var(--text); min-width: 80px; }
        .am-item-detail { font-size: 0.75rem; color: var(--text-muted); flex: 1; overflow: hidden; text-overflow: ellipsis; }

        .am-add-btn {
            padding: 8px 16px; border: 1px dashed var(--border); border-radius: 8px;
            background: transparent; color: var(--text-muted); cursor: pointer;
            font-size: 0.8125rem; transition: all 0.15s ease;
        }
        .am-add-btn:hover { border-color: var(--accent-blue); color: var(--accent-blue); }

        .am-back-btn {
            padding: 6px 12px; border: 1px solid var(--border); border-radius: 6px;
            background: transparent; color: var(--text); cursor: pointer;
            font-size: 0.75rem; transition: all 0.15s ease;
        }
        .am-back-btn:hover { background: var(--bg-hover); }

        .am-delete-btn {
            padding: 6px 12px; border: 1px solid var(--error, #dc3545); border-radius: 6px;
            background: transparent; color: var(--error, #dc3545); cursor: pointer;
            font-size: 0.75rem; transition: all 0.15s ease;
        }
        .am-delete-btn:hover { background: var(--error-light, #f8d7da); }

        .am-editor-title { font-size: 0.9375rem; font-weight: 600; color: var(--text); margin-bottom: 4px; }
        .am-hint { font-size: 0.6875rem; color: var(--text-muted); margin-top: 4px; }

        .am-group { display: flex; flex-direction: column; gap: 6px; }
        .am-group label { font-size: 0.8125rem; font-weight: 500; color: var(--text); }
        .am-group input, .am-group select {
            padding: 8px 12px; border: 1px solid var(--border); border-radius: 6px;
            background: var(--bg-primary); color: var(--text); font-size: 0.8125rem; font-family: inherit;
        }
        .am-group input:focus { outline: none; border-color: var(--accent-blue); }
        .am-row { display: flex; gap: 8px; align-items: flex-start; }
        .am-row input { flex: 1; }

        .am-action-btn {
            padding: 8px 14px; border: 1px solid var(--border); border-radius: 6px;
            background: var(--bg-tertiary); color: var(--text); cursor: pointer;
            font-size: 0.8125rem; white-space: nowrap; transition: all 0.15s ease;
        }
        .am-action-btn:hover { background: var(--bg-hover); }
        .am-action-btn:disabled { opacity: 0.6; cursor: not-allowed; }
        .am-action-btn.success { background: var(--success-light, #d4edda); border-color: var(--success, #28a745); color: var(--success, #28a745); }
        .am-action-btn.error { background: var(--error-light, #f8d7da); border-color: var(--error, #dc3545); color: var(--error, #dc3545); }
    `;
    document.head.appendChild(style);
}

/**
 * Create a multi-account manager instance.
 *
 * @param {Object} config
 * @param {string} config.prefix         - CSS/ID prefix (e.g. 'email', 'gcal')
 * @param {string} config.entityName     - Display name ('Account', 'Wallet', 'Calendar')
 * @param {string} config.listEndpoint   - GET URL returning { accounts/wallets/items: [...] }
 * @param {string} config.listKey        - Key in response JSON ('accounts', 'wallets')
 * @param {(scope) => string} config.deleteEndpoint - URL builder for DELETE
 * @param {(item) => {name, detail}} config.formatItem - How to display each item in list
 * @param {(container, scope, item, helpers) => void} config.renderEditor
 *        - Render the editor form. `helpers` has: { onBack, onDelete, csrfHeaders, showResult }
 * @param {string} [config.hint]         - Help text shown above the list
 * @param {string} [config.addLabel]     - Button text (default: '+ Add {entityName}')
 * @param {string} [config.addPrompt]    - Prompt text for scope name
 * @param {string} [config.listHeader]   - HTML rendered above the list (e.g. security banners)
 * @param {(container, helpers) => void} [config.listFooter] - Render extra buttons/content after Add button
 */
export function createAccountManager(config) {
    injectBaseStyles(config.prefix);

    let _container = null;
    let _items = [];
    let _editingScope = null;

    async function loadItems() {
        try {
            const res = await fetch(config.listEndpoint);
            if (res.ok) {
                const data = await res.json();
                _items = data[config.listKey] || [];
            }
        } catch (e) {
            console.warn(`Failed to load ${config.entityName}s:`, e);
            _items = [];
        }
        return _items;
    }

    function renderList(container) {
        _container = container;
        _editingScope = null;

        const items = _items.map(item => {
            const fmt = config.formatItem(item);
            return `<div class="am-item" data-scope="${item.scope}">
                <span class="am-item-name">${fmt.name}</span>
                <span class="am-item-detail">${fmt.detail}</span>
            </div>`;
        }).join('');

        const hint = config.hint ? `<div class="am-hint">${config.hint}</div>` : '';
        const listHeader = config.listHeader || '';
        const addLabel = config.addLabel || `+ Add ${config.entityName}`;
        const addPrompt = config.addPrompt || `Name for new ${config.entityName.toLowerCase()} (e.g. "work", "personal"):`;

        container.innerHTML = `
            <div class="am-form">
                ${listHeader}
                ${hint}
                <div class="am-list">
                    ${items || `<div class="am-hint">No ${config.entityName.toLowerCase()}s configured.</div>`}
                </div>
                <button type="button" class="am-add-btn" id="${config.prefix}-add">${addLabel}</button>
                <div id="${config.prefix}-list-footer"></div>
            </div>
        `;

        container.querySelectorAll('.am-item').forEach(el => {
            el.addEventListener('click', () => {
                const scope = el.dataset.scope;
                const item = _items.find(i => i.scope === scope);
                _renderEditor(container, scope, item);
            });
        });

        container.querySelector(`#${config.prefix}-add`).addEventListener('click', () => {
            const name = prompt(addPrompt);
            if (!name || !name.trim()) return;
            const scope = name.trim().toLowerCase().replace(/[^a-z0-9_-]/g, '_');
            const existing = _items.find(i => i.scope === scope);
            _renderEditor(container, scope, existing || null);
        });

        // Let plugin render extra list content (e.g. import button)
        if (config.listFooter) {
            const footer = container.querySelector(`#${config.prefix}-list-footer`);
            if (footer) config.listFooter(footer, { csrfHeaders, reloadList: () => { loadItems().then(() => renderList(container)); } });
        }
    }

    function _renderEditor(container, scope, item) {
        _editingScope = scope;

        const helpers = {
            onBack: () => renderList(container),
            onDelete: () => _deleteItem(container, scope),
            csrfHeaders,
            showResult: (success, message, detail) => _showResult(container, success, message, detail),
            reloadItems: loadItems,
            reloadList: () => { loadItems().then(() => renderList(container)); },
        };

        // Render header (back + title + delete)
        container.innerHTML = `
            <div class="am-form">
                <div class="am-row" style="align-items:center;gap:12px">
                    <button type="button" class="am-back-btn" id="${config.prefix}-back">\u2190 Back</button>
                    <div class="am-editor-title">${scope}</div>
                    ${item ? `<button type="button" class="am-delete-btn" id="${config.prefix}-delete" style="margin-left:auto">Delete</button>` : ''}
                </div>
                <div id="${config.prefix}-editor-body"></div>
            </div>
        `;

        container.querySelector(`#${config.prefix}-back`).addEventListener('click', helpers.onBack);
        container.querySelector(`#${config.prefix}-delete`)?.addEventListener('click', helpers.onDelete);

        // Let plugin render its custom form inside the body
        const body = container.querySelector(`#${config.prefix}-editor-body`);
        config.renderEditor(body, scope, item, helpers);
    }

    async function _deleteItem(container, scope) {
        if (!confirm(`Delete ${config.entityName.toLowerCase()} "${scope}"? This cannot be undone.`)) return;
        try {
            const res = await fetch(config.deleteEndpoint(scope), {
                method: 'DELETE', headers: csrfHeaders()
            });
            if (res.ok) {
                await loadItems();
                renderList(container);
            } else {
                const data = await res.json();
                alert(data.detail || 'Delete failed');
            }
        } catch (e) {
            alert('Delete failed: ' + e.message);
        }
    }

    function _showResult(container, success, message, detail) {
        container.querySelector('.am-result')?.remove();
        const div = document.createElement('div');
        div.className = `am-result`;
        div.style.cssText = `padding:10px 14px;border-radius:8px;font-size:0.8125rem;line-height:1.4;
            background:${success ? 'var(--success-light, #d4edda)' : 'var(--error-light, #f8d7da)'};
            border:1px solid ${success ? 'var(--success, #28a745)' : 'var(--error, #dc3545)'};
            color:${success ? 'var(--success, #28a745)' : 'var(--error, #dc3545)'}`;
        div.innerHTML = message + (detail ? `<div style="margin-top:4px;font-size:0.6875rem;opacity:0.85">${detail}</div>` : '');
        container.querySelector('.am-form')?.appendChild(div);
    }

    return {
        loadItems,
        renderList,
        getEditingScope: () => _editingScope,
        getItems: () => _items,
    };
}
