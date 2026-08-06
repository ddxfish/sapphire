// Game Settings — large modal for ONE game; tabs come from the engine's
// SETTINGS schema ('tab' per field — e.g. poker: Rules / Start). All tabs
// render at once (hidden/shown), one Save writes everything, Reset restores
// schema defaults into the form. A new game ships settings by exporting
// SETTINGS — zero UI code here.

import { setupModalClose } from '/static/shared/modal.js';
import * as ui from '/static/ui.js';

const PLUGIN_API = '/api/plugin/game-room/';

function csrf() {
    return document.querySelector('meta[name="csrf-token"]')?.content || '';
}

async function api(path, method, body) {
    const res = await fetch(PLUGIN_API + path, {
        method: method || 'GET',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf() },
        body: body ? JSON.stringify(body) : undefined,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || ('HTTP ' + res.status));
    return data;
}

function esc(s) {
    return String(s == null ? '' : s)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function fieldHtml(f, val) {
    const v = val ?? f.default ?? '';
    switch (f.type) {
        case 'checkbox':
            return `<div class="sb-field">
                <label class="st-tools-check" style="margin:0">
                    <input type="checkbox" class="grs-field" data-key="${esc(f.key)}"${(v === true || v === 'true') ? ' checked' : ''}>
                    ${esc(f.label)}
                </label>
            </div>`;
        case 'text':
            return `<div class="sb-field sb-field-stack">
                <label>${esc(f.label)}</label>
                <textarea class="grs-field" data-key="${esc(f.key)}" rows="${f.rows || 4}">${esc(v)}</textarea>
            </div>`;
        case 'range':
            return `<div class="sb-field sb-field-stack">
                <label>${esc(f.label)}: <span class="grs-val">${esc(v)}</span></label>
                <input type="range" class="grs-field" data-key="${esc(f.key)}"
                       min="${f.min ?? 0}" max="${f.max ?? 1}" step="${f.step ?? 0.05}" value="${esc(v)}">
            </div>`;
        case 'number':
            return `<div class="sb-field">
                <label>${esc(f.label)}</label>
                <input type="number" class="grs-field" data-key="${esc(f.key)}"
                       min="${f.min ?? ''}" max="${f.max ?? ''}" step="${f.step ?? 1}" value="${esc(v)}">
            </div>`;
        default:
            return `<div class="sb-field">
                <label>${esc(f.label)}</label>
                <input type="text" class="grs-field" data-key="${esc(f.key)}" value="${esc(v)}">
            </div>`;
    }
}

export async function openGameSettings(gameId) {
    if (!gameId) return;
    return openFromPath(`play/${gameId}/settings`, gameId);
}

// Story settings — dual-layer GM conduct (universal style + per-story DM
// guide). Same modal, different route; the schema drives everything.
export async function openStorySettings(slug) {
    if (!slug) return;
    return openFromPath(`story/${encodeURIComponent(slug)}/settings`, slug);
}

async function openFromPath(path, fallbackTitle) {
    let data;
    try {
        data = await api(path);
    } catch (e) {
        ui.showToast('Could not load settings: ' + e.message, 'error');
        return;
    }
    const schema = data.schema || [];
    if (!schema.length) { ui.showToast('No settings here.', 'error'); return; }
    const gameId = fallbackTitle, savePath = path;

    // Group fields by tab, preserving first-appearance order
    const tabs = [];
    const byTab = {};
    for (const f of schema) {
        const tab = f.tab || 'Settings';
        if (!byTab[tab]) { byTab[tab] = []; tabs.push(tab); }
        byTab[tab].push(f);
    }

    const overlay = document.createElement('div');
    overlay.className = 'pr-modal-overlay';
    overlay.innerHTML = `
        <div class="pr-modal grs-modal">
            <div class="pr-modal-header">
                <h3>&#x2699;&#xFE0E; ${esc(data.title || gameId)} settings</h3>
                <button type="button" class="sb-icon-btn grs-close" title="Close">✕</button>
            </div>
            ${tabs.length > 1 ? `<div class="sb-mode-tabs grs-tabs">
                ${tabs.map((t, i) => `<button class="sb-mode-tab grs-tab${i === 0 ? ' active' : ''}" data-tab="${esc(t)}">${esc(t)}</button>`).join('')}
            </div>` : ''}
            <div class="pr-modal-body grs-body">
                ${tabs.map((t, i) => `<div class="grs-pane" data-tab="${esc(t)}" style="display:${i === 0 ? 'block' : 'none'}">
                    ${byTab[t].map(f => fieldHtml(f, data.settings?.[f.key])).join('')}
                </div>`).join('')}
                <div class="grs-actions">
                    <button type="button" class="pk-btn pk-btn-primary grs-save">Save</button>
                    <button type="button" class="pk-btn grs-reset" title="Restore defaults into the form (Save to apply)">Reset to defaults</button>
                </div>
            </div>
        </div>`;
    document.body.appendChild(overlay);
    const closeFn = () => overlay.remove();
    setupModalClose(overlay, closeFn);
    overlay.querySelector('.grs-close').onclick = closeFn;

    overlay.querySelectorAll('.grs-tab').forEach(t => {
        t.onclick = () => {
            overlay.querySelectorAll('.grs-tab').forEach(x => x.classList.toggle('active', x === t));
            overlay.querySelectorAll('.grs-pane').forEach(p =>
                p.style.display = p.dataset.tab === t.dataset.tab ? 'block' : 'none');
        };
    });

    overlay.querySelectorAll('input[type="range"].grs-field').forEach(r => {
        r.addEventListener('input', () => {
            const lbl = r.closest('.sb-field')?.querySelector('.grs-val');
            if (lbl) lbl.textContent = r.value;
        });
    });

    overlay.querySelector('.grs-save').onclick = async () => {
        const out = {};
        for (const f of schema) {
            const el = overlay.querySelector(`.grs-field[data-key="${f.key}"]`);
            if (el) out[f.key] = el.type === 'checkbox' ? el.checked : el.value;
        }
        try {
            await api(savePath, 'POST', { settings: out });
            ui.showToast('Settings saved — live on the next turn', 'success', 2500);
            closeFn();
        } catch (e) { ui.showToast(e.message, 'error'); }
    };

    overlay.querySelector('.grs-reset').onclick = () => {
        for (const f of schema) {
            const el = overlay.querySelector(`.grs-field[data-key="${f.key}"]`);
            if (!el) continue;
            if (el.type === 'checkbox') el.checked = !!(f.default ?? false);
            else el.value = f.default ?? '';
            el.dispatchEvent(new Event('input'));
        }
    };
}
