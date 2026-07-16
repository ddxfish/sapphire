// shared/plugin-settings-renderer.js - Auto-render plugin settings from manifest schema
// Renders forms using existing .setting-row/.setting-toggle CSS — no new styles needed.

import { showDangerConfirm } from './danger-confirm.js';

function escapeHtml(s) {
    const d = document.createElement('div');
    d.textContent = s ?? '';
    return d.innerHTML;
}

/**
 * Render a settings form from a manifest schema array.
 * @param {HTMLElement} container - Where to render
 * @param {Array} schema - [{key, type, label, default, help?, widget?, options?, placeholder?, confirm?, tab?}]
 * @param {Object} values - Current setting values (merged with defaults by backend)
 * @param {Object} [opts] - {onChange: (key, value) => void}
 */
export function renderSettingsForm(container, schema, values = {}, { onChange, managed } = {}) {
    // hidden:true fields never render here — they belong to a dedicated UI
    // (e.g. Mind → Admin) and are skipped by readSettingsForm too, so a
    // Settings-page save can't clobber them with defaults.
    schema = (schema || []).filter(f => !f.hidden);
    if (!schema?.length) {
        container.innerHTML = '<p style="color:var(--text-muted)">No settings available.</p>';
        return;
    }

    const rowHTML = field => {
        const val = values[field.key] ?? field.default ?? '';
        return `
            <div class="setting-row" data-key="${escapeHtml(field.key)}">
                <div class="setting-label">
                    <label>${escapeHtml(field.label)}</label>
                    ${field.help ? `<div class="setting-help">${escapeHtml(field.help)}</div>` : ''}
                </div>
                <div class="setting-input">${renderWidget(field, val)}</div>
            </div>
        `;
    };

    // Optional per-field "tab" groups fields under a tab strip. Untagged
    // fields go to "General" (always first); tagged tabs follow in
    // first-seen schema order. With <2 groups the output is identical to
    // the untabbed form, so existing plugins render unchanged.
    const groups = new Map();
    for (const field of schema) {
        const tab = String(field.tab || 'General').trim() || 'General';
        if (!groups.has(tab)) groups.set(tab, []);
        groups.get(tab).push(field);
    }

    if (groups.size < 2) {
        container.innerHTML = `<div class="settings-grid">${schema.map(rowHTML).join('')}</div>`;
    } else {
        const names = [...groups.keys()];
        if (names.includes('General')) {
            names.splice(names.indexOf('General'), 1);
            names.unshift('General');
        }
        const strip = `<div class="ps-tabs">${names.map((n, i) =>
            `<button type="button" class="ps-tab${i === 0 ? ' active' : ''}" data-ps-tab="${escapeHtml(n)}">${escapeHtml(n)}</button>`
        ).join('')}</div>`;
        // Every pane stays in the DOM — save (readSettingsForm) and the
        // widget wiring below scan the whole container by #ps-{key}, so
        // inactive panes are CSS-hidden, never removed.
        const panes = names.map((n, i) =>
            `<div class="settings-grid" data-ps-pane="${escapeHtml(n)}"${i === 0 ? '' : ' hidden'}>${groups.get(n).map(rowHTML).join('')}</div>`
        ).join('');
        container.innerHTML = strip + panes;
        container.querySelector('.ps-tabs').addEventListener('click', e => {
            const btn = e.target.closest('.ps-tab');
            if (!btn) return;
            container.querySelectorAll('.ps-tab').forEach(b => b.classList.toggle('active', b === btn));
            container.querySelectorAll('[data-ps-pane]').forEach(p =>
                p.toggleAttribute('hidden', p.dataset.psPane !== btn.dataset.psTab));
        });
    }

    // Attach confirm gates and onChange handlers
    for (const field of schema) {
        if (field.confirm) attachConfirmGate(container, field, managed);
    }

    // Wire up "clear" links for password fields
    container.querySelectorAll('.ps-clear-key').forEach(link => {
        link.addEventListener('click', e => {
            e.preventDefault();
            const input = container.querySelector(`#${link.dataset.field}`);
            if (input) {
                input.value = '__CLEAR__';
                input.placeholder = 'Key cleared — save to apply';
                link.closest('.setting-input').querySelector('small')?.remove();
                link.remove();
            }
        });
    });

    // Wire up action buttons
    for (const field of schema) {
        if ((field.widget || inferWidget(field)) !== 'button') continue;
        const btn = container.querySelector(`#ps-${field.key}`);
        if (!btn) continue;

        // Check status on render if status URL provided
        if (field.status) {
            fetch(field.status).then(r => r.json()).then(data => {
                if (data.connected) {
                    btn.textContent = field.button_label_connected || 'Connected ✓';
                    btn.dataset.connected = 'true';
                    btn.classList.add('btn-connected');
                    // Add disconnect button if disconnect URL provided
                    if (field.disconnect) {
                        let discBtn = btn.parentElement.querySelector('.btn-disconnect');
                        if (!discBtn) {
                            discBtn = document.createElement('button');
                            discBtn.type = 'button';
                            discBtn.className = 'btn-action btn-disconnect';
                            discBtn.textContent = 'Disconnect';
                            discBtn.style.marginLeft = '8px';
                            discBtn.addEventListener('click', async () => {
                                const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
                                try {
                                    const res = await fetch(field.disconnect, {
                                        method: 'POST',
                                        headers: { 'X-CSRF-Token': csrf }
                                    });
                                    if (!res.ok) { console.error('Disconnect failed:', res.status); return; }
                                } catch (e) { console.error('Disconnect failed:', e); return; }
                                btn.textContent = field.button_label || 'Connect';
                                btn.dataset.connected = 'false';
                                btn.classList.remove('btn-connected');
                                discBtn.remove();
                            });
                            btn.parentElement.appendChild(discBtn);
                        }
                    }
                }
            }).catch(() => {});
        }

        btn.addEventListener('click', async () => {
            const url = btn.dataset.actionUrl;
            if (!url) return;
            // Default behavior is browser navigation — required for OAuth flows
            // that expect the server to 302-redirect to an external auth page.
            // Plugins that return JSON (e.g. an API key to display) MUST set
            // `action_mode: "display"` in the manifest — otherwise the raw JSON
            // body lands in the URL bar / browser history and leaks the secret.
            // Scout finding #9 — 2026-04-20.
            const mode = field.action_mode || 'navigate';
            if (mode === 'display') {
                try {
                    const res = await fetch(url);
                    const data = await res.json().catch(() => ({}));
                    _showActionResult(field, data, res.ok);
                } catch (e) {
                    _showActionResult(field, { error: String(e) }, false);
                }
                return;
            }
            window.location.href = url;
        });
    }

    // Wire up list fields (chips + add/remove)
    for (const field of schema) {
        if ((field.widget || inferWidget(field)) !== 'list') continue;
        wireListField(container, field);
    }

    if (onChange) {
        container.addEventListener('change', e => {
            const key = e.target.closest('[data-key]')?.dataset.key;
            if (key) onChange(key, getFieldValue(container, key, schema.find(f => f.key === key)));
        });
    }
}

function wireListField(container, field) {
    const wrap = container.querySelector(`.ps-list[data-list-key="${field.key}"]`);
    if (!wrap) return;
    const hidden = wrap.querySelector(`#ps-${field.key}`);
    const chipsEl = wrap.querySelector('.ps-list-chips');
    const srcEl = wrap.querySelector('.ps-list-src');

    const read = () => { try { const a = JSON.parse(hidden.value); return Array.isArray(a) ? a : []; } catch { return []; } };
    const write = (items) => {
        hidden.value = JSON.stringify(items);
        render();
        hidden.dispatchEvent(new Event('change', { bubbles: true }));
    };
    const render = () => {
        const items = read();
        chipsEl.innerHTML = items.map(v => `
            <span class="ps-list-chip" data-val="${escapeHtml(v)}" style="display:inline-flex;align-items:center;gap:6px;padding:3px 10px;background:var(--bg-secondary,#1a1b2e);border:1px solid var(--border,#333);border-radius:12px;font-size:var(--font-sm,13px)">
                ${escapeHtml(v)}<a href="#" class="ps-list-del" style="color:var(--text-muted);text-decoration:none">✕</a>
            </span>`).join('') || '<span style="color:var(--text-muted);font-size:var(--font-sm,13px)">None added</span>';
        if (srcEl.tagName === 'SELECT') refreshOptions(items);
    };
    const refreshOptions = (items) => {
        const current = srcEl.value;
        const opts = (srcEl._options || []).filter(o => !items.includes(o));
        srcEl.innerHTML = opts.map(o => `<option value="${escapeHtml(o)}">${escapeHtml(o)}</option>`).join('');
        if (opts.includes(current)) srcEl.value = current;
    };

    chipsEl.addEventListener('click', e => {
        const del = e.target.closest('.ps-list-del');
        if (!del) return;
        e.preventDefault();
        const val = del.closest('.ps-list-chip')?.dataset.val;
        write(read().filter(v => v !== val));
    });
    wrap.querySelector('.ps-list-add')?.addEventListener('click', () => {
        const val = (srcEl.value || '').trim();
        if (!val) return;
        const items = read();
        if (!items.includes(val)) write([...items, val]);
        if (srcEl.tagName === 'INPUT') srcEl.value = '';
    });

    if (field.options_endpoint && srcEl.tagName === 'SELECT') {
        fetch(field.options_endpoint).then(r => r.json()).then(data => {
            const rows = field.data_key ? (data[field.data_key] || []) : (Array.isArray(data) ? data : []);
            srcEl._options = rows.map(r => typeof r === 'string' ? r : String(r[field.value_field || 'name'] ?? ''))
                                 .filter(Boolean);
            refreshOptions(read());
        }).catch(() => {});
    }
    render();
}

function renderWidget(field, value) {
    const id = `ps-${field.key}`;
    const widget = field.widget || inferWidget(field);

    switch (widget) {
        case 'textarea':
            return `<textarea id="${id}" rows="${field.rows || 8}" placeholder="${escapeHtml(field.placeholder || '')}" style="width:100%;background:var(--bg-secondary,#1a1b2e);color:var(--text,#e1e1e6);border:1px solid var(--border,#333);border-radius:6px;padding:8px;font-family:monospace;font-size:var(--font-sm,13px);resize:vertical">${escapeHtml(String(value))}</textarea>`;

        case 'password': {
            const hasValue = value && String(value).trim();
            const indicator = hasValue
                ? '<small style="color:var(--success,#4caf50);margin-left:6px">\u2713 Set</small> <a href="#" class="ps-clear-key" data-field="' + id + '" style="font-size:var(--font-xs);margin-left:4px;color:var(--text-muted)">clear</a>'
                : '';
            return `<input type="password" id="${id}" value="" placeholder="${hasValue ? '\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022' : 'Enter key'}">${indicator}`;
        }

        case 'select':
            return `<select id="${id}">${(field.options || []).map(o =>
                `<option value="${escapeHtml(o.value)}" ${String(value) === String(o.value) ? 'selected' : ''}>${escapeHtml(o.label)}</option>`
            ).join('')}</select>`;

        case 'radio':
            return (field.options || []).map(o =>
                `<label style="display:inline-flex;align-items:center;gap:4px;margin-right:12px">
                    <input type="radio" name="${id}" value="${escapeHtml(o.value)}" ${String(value) === String(o.value) ? 'checked' : ''}>
                    ${escapeHtml(o.label)}
                </label>`
            ).join('');

        case 'toggle':
            return `<label class="setting-toggle">
                <input type="checkbox" id="${id}" ${value ? 'checked' : ''}>
                <span>${value ? 'Enabled' : 'Disabled'}</span>
            </label>`;

        case 'number':
            return `<input type="number" id="${id}" value="${value}" step="any" placeholder="${escapeHtml(field.placeholder || '')}">`;

        case 'list': {
            // Chips + "+ Add" row. Value lives as JSON in a hidden input so
            // readSettingsForm/getFieldValue work unchanged. With
            // options_endpoint the source is a dropdown (populated after
            // render, added values filtered out); without, a free-text input.
            const items = Array.isArray(value) ? value : (value ? [String(value)] : []);
            const src = field.options_endpoint
                ? `<select class="ps-list-src" style="flex:1"></select>`
                : `<input type="text" class="ps-list-src" placeholder="${escapeHtml(field.placeholder || '')}" style="flex:1">`;
            return `<div class="ps-list" data-list-key="${escapeHtml(field.key)}">
                <input type="hidden" id="${id}" value="${escapeHtml(JSON.stringify(items))}">
                <div class="ps-list-chips" style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:6px"></div>
                <div style="display:flex;gap:6px">${src}
                    <button type="button" class="btn-action ps-list-add">+ Add</button>
                </div>
            </div>`;
        }

        case 'button':
            return `<button type="button" id="${id}" class="btn-action" data-action-url="${escapeHtml(field.action || '')}" data-status-url="${escapeHtml(field.status || '')}">${escapeHtml(field.button_label || field.label || 'Action')}</button>`;

        default: // text
            return `<input type="text" id="${id}" value="${escapeHtml(String(value))}" placeholder="${escapeHtml(field.placeholder || '')}">`;
    }
}

function inferWidget(field) {
    if (field.type === 'boolean') return 'toggle';
    if (field.type === 'number') return 'number';
    if (field.type === 'textarea') return 'textarea';
    if (field.type === 'list') return 'list';
    if (field.options) return 'select';
    return 'text';
}

/**
 * Read form values back into a dict with type coercion.
 */
export function readSettingsForm(container, schema) {
    const result = {};
    for (const field of schema) {
        // Skip action buttons — they're not settings
        if ((field.widget || inferWidget(field)) === 'button') continue;
        // Skip hidden fields — not in this form's DOM; including them would
        // send their DEFAULTS and overwrite values owned by another UI.
        if (field.hidden) continue;
        const val = getFieldValue(container, field.key, field);
        // Password field: skip if empty (preserve stored key), send empty if sentinel
        if (field.type === 'password') {
            if (val === '__CLEAR__') { result[field.key] = ''; continue; }
            if (!val) continue;
        }
        result[field.key] = val;
    }
    return result;
}

function getFieldValue(container, key, field) {
    const id = `ps-${key}`;
    const widget = field?.widget || inferWidget(field || {});

    if (widget === 'toggle') {
        const el = container.querySelector(`#${id}`);
        return el ? el.checked : false;
    }
    if (widget === 'radio') {
        const checked = container.querySelector(`input[name="${id}"]:checked`);
        return coerce(checked?.value ?? field?.default ?? '', field);
    }
    const el = container.querySelector(`#${id}`);
    if (!el) return field?.default ?? '';
    return coerce(el.value, field);
}

function coerce(value, field) {
    if (!field) return value;
    if (field.type === 'number') return Number(value) || 0;
    if (field.type === 'boolean') return Boolean(value);
    if (field.type === 'list') {
        try { const a = JSON.parse(value); return Array.isArray(a) ? a : []; } catch { return []; }
    }
    return value;
}

/**
 * Attach a danger confirm gate to a field.
 */
function attachConfirmGate(container, field, managed) {
    const id = `ps-${field.key}`;
    const widget = field.widget || inferWidget(field);
    const el = widget === 'radio'
        ? container.querySelectorAll(`input[name="${id}"]`)
        : container.querySelector(`#${id}`);

    if (!el) return;
    const conf = field.confirm;
    let previousValue = getFieldValue(container, field.key, field);

    const handler = async (e) => {
        const newValue = widget === 'toggle' ? String(e.target.checked) : e.target.value;
        if (!conf.values?.includes(newValue)) {
            previousValue = newValue;
            return;
        }

        // Block confirm-gated values entirely in managed mode — unless the
        // manifest opts out (`allow_managed: true`, e.g. alpha-feature gates
        // that warn rather than protect the host).
        if (managed && !conf.allow_managed) {
            if (widget === 'select') e.target.value = previousValue;
            else if (widget === 'toggle') { e.target.checked = previousValue === 'true'; }
            else if (widget === 'radio') {
                const prev = container.querySelector(`input[name="${id}"][value="${previousValue}"]`);
                if (prev) prev.checked = true;
            }
            const { showToast } = await import('../ui.js');
            showToast(`${conf.title || 'This option'} is disabled in managed mode`, 'error');
            e.stopImmediatePropagation();
            return;
        }

        const ok = await showDangerConfirm({
            title: conf.title || 'Confirm',
            warnings: conf.warnings || [],
            buttonLabel: conf.buttonLabel || 'Confirm',
        });

        if (!ok) {
            // Revert
            if (widget === 'select') {
                e.target.value = previousValue;
            } else if (widget === 'toggle') {
                e.target.checked = previousValue === 'true';
                const span = e.target.parentElement?.querySelector('span');
                if (span) span.textContent = e.target.checked ? 'Enabled' : 'Disabled';
            } else if (widget === 'radio') {
                const prev = container.querySelector(`input[name="${id}"][value="${previousValue}"]`);
                if (prev) prev.checked = true;
            }
            e.stopImmediatePropagation();
        } else {
            previousValue = newValue;
        }
    };

    if (el instanceof NodeList || el instanceof HTMLCollection) {
        el.forEach(r => r.addEventListener('change', handler));
    } else {
        el.addEventListener('change', handler);
    }
}

// Minimal modal for displaying action results (keys, tokens, etc.). Built in
// JS to avoid requiring plugin authors to ship CSS. The value is rendered in
// a selectable <input readonly> so users can copy it without it landing in
// browser history or URL bar.
function _showActionResult(field, data, ok) {
    const existing = document.getElementById('ps-action-modal');
    if (existing) existing.remove();
    const wrap = document.createElement('div');
    wrap.id = 'ps-action-modal';
    wrap.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.6);display:flex;align-items:center;justify-content:center;z-index:10000';
    const box = document.createElement('div');
    box.style.cssText = 'background:var(--bg-primary,#0f1020);color:var(--text,#e1e1e6);padding:24px;border-radius:10px;min-width:360px;max-width:80vw;border:1px solid var(--border,#333)';
    const title = document.createElement('h3');
    title.textContent = field.label || field.key || 'Result';
    title.style.cssText = 'margin:0 0 12px 0';
    box.appendChild(title);
    if (!ok) {
        const err = document.createElement('div');
        err.style.cssText = 'color:var(--error,#f44);margin-bottom:12px';
        err.textContent = `Request failed: ${data?.error || 'Unknown error'}`;
        box.appendChild(err);
    } else if (data && typeof data === 'object') {
        // Find the most likely display value — a string field (key/token/etc.)
        const displayKey = Object.keys(data).find(k => typeof data[k] === 'string' && data[k].length > 0);
        if (displayKey) {
            const lbl = document.createElement('div');
            lbl.style.cssText = 'font-size:var(--font-sm,13px);color:var(--text-muted);margin-bottom:4px';
            lbl.textContent = displayKey;
            box.appendChild(lbl);
            const input = document.createElement('input');
            input.type = 'text';
            input.readOnly = true;
            input.value = data[displayKey];
            input.style.cssText = 'width:100%;font-family:monospace;padding:8px;background:var(--bg-secondary,#1a1b2e);color:var(--text,#e1e1e6);border:1px solid var(--border,#333);border-radius:6px;font-size:var(--font-sm,13px)';
            input.addEventListener('focus', () => input.select());
            box.appendChild(input);
        } else {
            const pre = document.createElement('pre');
            pre.style.cssText = 'background:var(--bg-secondary,#1a1b2e);padding:8px;border-radius:6px;overflow:auto;max-height:40vh';
            pre.textContent = JSON.stringify(data, null, 2);
            box.appendChild(pre);
        }
    }
    const btnRow = document.createElement('div');
    btnRow.style.cssText = 'display:flex;justify-content:flex-end;margin-top:16px';
    const close = document.createElement('button');
    close.textContent = 'Close';
    close.style.cssText = 'padding:8px 16px;background:var(--accent,#4a7);color:#fff;border:0;border-radius:6px;cursor:pointer';
    close.addEventListener('click', () => wrap.remove());
    btnRow.appendChild(close);
    box.appendChild(btnRow);
    wrap.appendChild(box);
    wrap.addEventListener('click', e => { if (e.target === wrap) wrap.remove(); });
    document.body.appendChild(wrap);
}
