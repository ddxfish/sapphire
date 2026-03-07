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
 * @param {Array} schema - [{key, type, label, default, help?, widget?, options?, placeholder?, confirm?}]
 * @param {Object} values - Current setting values (merged with defaults by backend)
 * @param {Object} [opts] - {onChange: (key, value) => void}
 */
export function renderSettingsForm(container, schema, values = {}, { onChange, managed } = {}) {
    if (!schema?.length) {
        container.innerHTML = '<p style="color:var(--text-muted)">No settings available.</p>';
        return;
    }

    const rows = schema.map(field => {
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
    }).join('');

    container.innerHTML = `<div class="settings-grid">${rows}</div>`;

    // Attach confirm gates and onChange handlers
    for (const field of schema) {
        if (field.confirm) attachConfirmGate(container, field, managed);
    }

    if (onChange) {
        container.addEventListener('change', e => {
            const key = e.target.closest('[data-key]')?.dataset.key;
            if (key) onChange(key, getFieldValue(container, key, schema.find(f => f.key === key)));
        });
    }
}

function renderWidget(field, value) {
    const id = `ps-${field.key}`;
    const widget = field.widget || inferWidget(field);

    switch (widget) {
        case 'textarea':
            return `<textarea id="${id}" rows="3" placeholder="${escapeHtml(field.placeholder || '')}">${escapeHtml(String(value))}</textarea>`;

        case 'password':
            return `<input type="password" id="${id}" value="${escapeHtml(String(value))}" placeholder="${escapeHtml(field.placeholder || '')}">`;

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

        default: // text
            return `<input type="text" id="${id}" value="${escapeHtml(String(value))}" placeholder="${escapeHtml(field.placeholder || '')}">`;
    }
}

function inferWidget(field) {
    if (field.type === 'boolean') return 'toggle';
    if (field.type === 'number') return 'number';
    if (field.options) return 'select';
    return 'text';
}

/**
 * Read form values back into a dict with type coercion.
 */
export function readSettingsForm(container, schema) {
    const result = {};
    for (const field of schema) {
        result[field.key] = getFieldValue(container, field.key, field);
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

        // Block confirm-gated values entirely in managed mode
        if (managed) {
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
