// trigger-editor/trigger-event.js - Event trigger section (daemons + webhooks)
// Response routing is implicit: daemons always reply to source, webhooks always reply via HTTP.
// Chat history + TTS are configured in the existing Chat/Voice accordions (from ai-config).

import { editableFocused } from '../dom-guard.js';

// Cache sources data so filter hints update on source change
let _sourcesCache = [];

/**
 * Render the event trigger section HTML
 * @param {Object} t - Existing task data (or {})
 * @param {Object} opts - { type: 'daemon' | 'webhook' }
 * @returns {string} HTML string
 */
export function renderEventTrigger(t, opts = {}) {
    const { type } = opts;
    const triggerConfig = t.trigger_config || {};

    if (type === 'webhook') {
        const path = triggerConfig.path || '';
        const method = triggerConfig.method || 'POST';
        const secret = triggerConfig.secret || '';
        return `
            <div class="sched-section-title" style="margin-top:16px">\uD83D\uDD17 Webhook</div>
            <div class="sched-field">
                <label>Path <span class="help-tip" data-tip="The URL path to listen on. Will be available at /api/events/webhook/{path}">?</span></label>
                <div style="display:flex;align-items:center;gap:4px">
                    <span class="text-muted" style="font-size:var(--font-xs)">/api/events/webhook/</span>
                    <input type="text" id="ed-webhook-path" value="${_esc(path)}" placeholder="my-hook" style="flex:1">
                </div>
            </div>
            <div class="sched-field">
                <label>Method</label>
                <select id="ed-webhook-method">
                    <option value="POST" ${method === 'POST' ? 'selected' : ''}>POST</option>
                    <option value="GET" ${method === 'GET' ? 'selected' : ''}>GET</option>
                    <option value="PUT" ${method === 'PUT' ? 'selected' : ''}>PUT</option>
                </select>
            </div>
            <div class="sched-field">
                <label>Secret <span class="help-tip" data-tip="Optional. If set, incoming requests must include X-Webhook-Secret header or X-Hub-Signature-256 (GitHub-style HMAC). Leave empty for no auth.">?</span></label>
                <div style="display:flex;align-items:center;gap:4px">
                    <input type="password" id="ed-webhook-secret" value="${_esc(secret)}" placeholder="Optional secret" style="flex:1">
                    <button type="button" id="ed-wh-gen-secret" title="Generate a random 32-char key" style="padding:2px 8px;cursor:pointer">🔑</button>
                </div>
            </div>
            <div class="sched-checkbox">
                <label><input type="checkbox" id="ed-wh-from-payload" ${triggerConfig.chat_from_payload ? 'checked' : ''}> Webhook specifies chat name <span class="help-tip" data-tip="The POST body names the chat to reply in (JSON field chat_target), and THAT chat answers with its own persona, tools, and memory. The AI and Chat settings below are then ignored. Use for round-trips where the caller picks the chat (e.g. Nova).">?</span></label>
            </div>
            <details class="sched-accordion" style="margin-top:8px">
                <summary class="sched-acc-header">Filter <span class="sched-preview" id="ed-wh-filter-preview"></span></summary>
                <div class="sched-acc-body"><div class="sched-acc-inner">
                    <div class="text-muted" style="font-size:var(--font-xs);margin-bottom:8px">
                        Payload must match every row &middot; comma = any &middot; keys take <code>_not</code> / <code>_contains</code>
                    </div>
                    <div id="ed-wh-filter-rows" class="filter-rows"></div>
                    <button type="button" class="btn-sm" id="ed-wh-filter-add" style="margin-top:6px">+ Add filter</button>
                </div></div>
            </details>`;
    }

    // Daemon type — event source from plugins
    const eventSource = triggerConfig.source || '';
    return `
        <div class="sched-field" style="margin-top:16px">
            <label>Daemon Source <span class="help-tip" data-tip="The event type to listen for. Available sources come from loaded daemon plugins.">?</span></label>
            <select id="ed-event-source" data-current-value="${_esc(eventSource)}">
                <option value="">Select event source...</option>
                <option value="_loading" disabled>Loading plugin events...</option>
            </select>
        </div>
        <div id="ed-realtime-note" class="text-muted" style="display:none;font-size:var(--font-xs);margin-top:6px;padding:8px;border-left:2px solid var(--accent, #6cf);background:rgba(120,180,255,0.06)">
            ⚡ Live source — this task is an <strong>on/off switch</strong>: enabling it lets Sapphire answer the call live (not a one-shot reply). Check <strong>"Ephemeral per-caller chat"</strong> above for a fresh throwaway chat per caller (auto-clears after the minutes you set); otherwise the call runs in the saved <strong>Chat</strong> you pick in AI settings, persistent across calls. <em>(The free-text prompt field isn't used here — behavior comes from the chat's persona.)</em>
        </div>
        <div id="ed-task-fields"></div>
        <details class="sched-accordion" style="margin-top:8px">
            <summary class="sched-acc-header">Filter <span class="sched-preview" id="ed-filter-preview"></span></summary>
            <div class="sched-acc-body"><div class="sched-acc-inner">
                <div class="text-muted" style="font-size:var(--font-xs);margin-bottom:8px">
                    Event must match every row &middot; comma = any &middot; keys take <code>_not</code> / <code>_contains</code>
                </div>
                <div id="ed-filter-rows" class="filter-rows"></div>
                <button type="button" class="btn-sm" id="ed-filter-add" style="margin-top:6px">+ Add filter</button>
            </div></div>
        </details>`;
}

/**
 * Wire event trigger event listeners
 * @param {HTMLElement} modal - The editor modal element
 * @param {Object} opts - { type: 'daemon' | 'webhook' }
 */
export function wireEventTrigger(modal, opts = {}) {
    const { type, triggerConfig } = opts;

    if (type === 'webhook') {
        // Filter rows — webhook payloads are arbitrary, so keys are free text (no dropdown)
        _wireFilterRows(modal, '#ed-wh-filter-rows', '#ed-wh-filter-add', '#ed-wh-filter-preview');
        _buildFilterRows(modal, '#ed-wh-filter-rows', triggerConfig?.filter, null);

        // Generate a random 32-char alphanumeric secret on demand.
        modal.querySelector('#ed-wh-gen-secret')?.addEventListener('click', () => {
            const A = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
            const key = Array.from(crypto.getRandomValues(new Uint8Array(32)), b => A[b % A.length]).join('');
            const inp = modal.querySelector('#ed-webhook-secret');
            if (inp) { inp.type = 'text'; inp.value = key; }
        });

        // "Webhook specifies chat name": the named chat decides destination + AI,
        // so grey out the Chat field and the AI section (they're ignored when on).
        const fromPayload = modal.querySelector('#ed-wh-from-payload');
        if (fromPayload) {
            const ids = ['#ed-chat', '#ed-persona', '#ed-prompt', '#ed-toolset',
                         '#ed-provider', '#ed-model', '#ed-model-custom'];
            const applyGrey = () => {
                const off = fromPayload.checked;
                ids.forEach(sel => { const el = modal.querySelector(sel); if (el) el.disabled = off; });
                const scopes = modal.querySelector('#ed-scope-dropdowns');
                if (scopes) { scopes.style.opacity = off ? '0.4' : ''; scopes.style.pointerEvents = off ? 'none' : ''; }
                // Dim the AI accordion, the Chat accordion, and the standalone Persona field.
                [modal.querySelector('#ed-prompt')?.closest('details'),
                 modal.querySelector('#ed-chat')?.closest('details'),
                 modal.querySelector('#ed-persona')?.closest('.sched-field')
                ].forEach(el => { if (el) el.style.opacity = off ? '0.45' : ''; });
            };
            fromPayload.addEventListener('change', applyGrey);
            setTimeout(applyGrey, 0);
        }
    }

    if (type === 'daemon') {
        // Stash existing trigger_config so task fields can pre-fill on edit
        const tfContainer = modal.querySelector('#ed-task-fields');
        if (tfContainer && triggerConfig) {
            tfContainer.dataset.triggerConfig = JSON.stringify(triggerConfig);
        }
        // Wire BEFORE load — _loadEventSources synchronously builds filter rows,
        // and _updateFilterPreview needs the preview selector _wireFilterRows stamps.
        _wireFilterRows(modal, '#ed-filter-rows', '#ed-filter-add', '#ed-filter-preview');
        _loadEventSources(modal, triggerConfig?.filter);

        // Rebuild filter key dropdowns + task fields when source changes
        modal.querySelector('#ed-event-source')?.addEventListener('change', e => {
            const current = _readFilterRows(modal, '#ed-filter-rows');
            _buildFilterRows(modal, '#ed-filter-rows', current, _fieldsFor(e.target.value));
            _renderTaskFields(modal);
            _updateRealtimeNote(modal);
        });
        _updateRealtimeNote(modal);   // reflect on open (edit case)
    }
}

/**
 * Read event trigger values from the modal
 * @param {HTMLElement} modal - The editor modal element
 * @returns {Object} Event trigger fields for the task data
 */
export function readEventTrigger(modal) {
    const webhookPath = modal.querySelector('#ed-webhook-path');

    if (webhookPath) {
        const whFilter = _readFilterRows(modal, '#ed-wh-filter-rows');
        const secret = modal.querySelector('#ed-webhook-secret')?.value?.trim() || undefined;
        return {
            trigger_config: {
                path: webhookPath.value.trim(),
                method: modal.querySelector('#ed-webhook-method')?.value || 'POST',
                ...(secret && { secret }),
                ...(Object.keys(whFilter).length && { filter: whFilter }),
                ...(modal.querySelector('#ed-wh-from-payload')?.checked && { chat_from_payload: true }),
            },
            schedule: '0 0 31 2 *', // never fires via cron (Feb 31)
            chance: 100,
            active_hours_start: null,
            active_hours_end: null,
        };
    }

    // Daemon type
    const rows = _readFilterRows(modal, '#ed-filter-rows');
    const filter = Object.keys(rows).length ? rows : null;

    // Collect task_fields values
    const taskFieldValues = {};
    modal.querySelectorAll('[data-task-field]').forEach(el => {
        const key = el.dataset.taskField;
        if (el.type === 'checkbox') taskFieldValues[key] = el.checked;
        else if (el.type === 'number') taskFieldValues[key] = el.value ? Number(el.value) : null;
        else taskFieldValues[key] = el.value;
    });

    return {
        trigger_config: {
            source: modal.querySelector('#ed-event-source')?.value || '',
            filter,
            ...taskFieldValues,
        },
        schedule: '0 0 31 2 *', // never fires via cron
        chance: 100,
        active_hours_start: null,
        active_hours_end: null,
    };
}

// ── Private helpers ──

async function _loadEventSources(modal, initialFilter) {
    const select = modal.querySelector('#ed-event-source');
    if (!select) return;

    // Seed SYNCHRONOUSLY before the fetch: Save reads the source select and
    // the filter rows directly, so a save landing while the fetch is in
    // flight was silently wiping both (saved filter -> null, source -> '').
    // Free-text rows now; upgraded to dropdowns when sources arrive.
    const savedSource = select.dataset.currentValue;
    try {
        if (savedSource && !Array.from(select.options).some(o => o.value === savedSource)) {
            const opt = document.createElement('option');
            opt.value = savedSource;
            opt.textContent = savedSource;
            opt.selected = true;
            select.appendChild(opt);
        }
        _buildFilterRows(modal, '#ed-filter-rows', initialFilter, null);

        const res = await fetch('/api/events/sources', { signal: AbortSignal.timeout(10000) });
        if (!res.ok) throw new Error('Failed to fetch event sources');
        const data = await res.json();
        _sourcesCache = data.sources || [];

        select.innerHTML = '<option value="">Select event source...</option>';

        if (_sourcesCache.length === 0) {
            select.innerHTML += '<option value="" disabled>No daemon plugins loaded</option>';
            return;   // rows already seeded above — don't clobber in-flight edits
        }

        // Group by plugin
        const grouped = {};
        for (const s of _sourcesCache) {
            const group = s.plugin || 'core';
            if (!grouped[group]) grouped[group] = [];
            grouped[group].push(s);
        }

        for (const [plugin, events] of Object.entries(grouped)) {
            const optgroup = document.createElement('optgroup');
            optgroup.label = plugin;
            for (const ev of events) {
                const opt = document.createElement('option');
                opt.value = ev.name;
                opt.textContent = ev.label || ev.name;
                optgroup.appendChild(opt);
            }
            select.appendChild(optgroup);
        }

        const current = select.dataset.currentValue;
        if (current) select.value = current;

        // Upgrade the seeded rows to dropdowns for the pre-selected source.
        // Prefer what's live in the DOM — the user may have edited rows
        // while the fetch was in flight.
        // ...and keep a half-typed row (partial read), and don't rebuild at all
        // under the user's cursor — text rows still save fine un-upgraded
        // (DOM-refresh hunt 2026-09-08).
        const rows = modal.querySelector('#ed-filter-rows');
        if (!editableFocused(rows)) {
            const live = _readFilterRows(modal, '#ed-filter-rows', { partial: true });
            _buildFilterRows(modal, '#ed-filter-rows',
                             Object.keys(live).length ? live : initialFilter,
                             _fieldsFor(select.value));
        }
        if (!editableFocused(modal.querySelector('#ed-task-fields'))) _renderTaskFields(modal);
    } catch (e) {
        console.error('[trigger-editor] event sources load failed:', e);
        select.innerHTML = `<option value="">Select event source...</option><option value="" disabled>Could not load sources — ${_esc(e?.message || 'unknown error')}</option>`;
        if (savedSource) {
            const opt = document.createElement('option');
            opt.value = savedSource;
            opt.textContent = savedSource;
            opt.selected = true;
            select.appendChild(opt);
        }
        // Rows were seeded synchronously above — leave them alone so an
        // edit+save during/after a failed fetch can't silently wipe them.
    }
}

// ── Filter rows (shared by daemon + webhook) ──
// The backend matcher (scheduler.filter_matches) is a flat {key: value} dict —
// string-compared, comma = OR-allowlist, `_not`/`_contains` key suffixes.
// Rows express all of it; no JSON needed.

function _fieldsFor(sourceName) {
    const fields = _sourcesCache.find(s => s.name === sourceName)?.filter_fields;
    return fields && fields.length ? fields : null;
}

function _filterRowEl(key, val, fields) {
    const row = document.createElement('div');
    row.className = 'filter-row';
    const valInput = `<input type="text" class="filter-val" placeholder="value" value="${_esc(String(val ?? ''))}">`;
    const delBtn = `<button type="button" class="filter-del" title="Remove">✕</button>`;

    if (!fields) {
        // No declared keys (webhooks, undeclared sources): free-text key
        row.classList.add('nokeys');
        row.innerHTML = `<input type="text" class="filter-custom-key" placeholder="key" value="${_esc(key)}">${valInput}${delBtn}`;
        return row;
    }

    const isCustom = key !== '' && !fields.some(f => f.key === key);
    const opts = fields.map(f =>
        `<option value="${_esc(f.key)}" ${f.key === key ? 'selected' : ''}>${_esc(f.label || f.key)}</option>`).join('');
    row.innerHTML = `
        <select class="filter-key">
            <option value="">Field…</option>
            ${opts}
            <option value="__custom__" ${isCustom ? 'selected' : ''}>Custom…</option>
        </select>
        ${delBtn}
        <input type="text" class="filter-custom-key" placeholder="key" value="${isCustom ? _esc(key) : ''}">
        ${valInput}`;
    if (isCustom) row.classList.add('custom');
    return row;
}

function _buildFilterRows(modal, containerSel, filter, fields) {
    const container = modal.querySelector(containerSel);
    if (!container) return;
    container._fields = fields;   // for + Add filter
    container.innerHTML = '';
    const entries = Object.entries(filter || {});
    if (!entries.length) entries.push(['', '']);
    for (const [k, v] of entries) container.appendChild(_filterRowEl(k, v, fields));
    _updateFilterPreview(modal, containerSel);
}

// partial: keep rows with only a key OR only a value (a rebuild mid-edit must
// not discard what the user has typed so far); the save path wants complete
// rows only.
function _readFilterRows(modal, containerSel, { partial = false } = {}) {
    const filter = {};
    modal.querySelectorAll(`${containerSel} .filter-row`).forEach(row => {
        const sel = row.querySelector('.filter-key');
        const key = (sel && sel.value && sel.value !== '__custom__')
            ? sel.value
            : row.querySelector('.filter-custom-key')?.value?.trim();
        const val = row.querySelector('.filter-val')?.value?.trim();
        if (key && val) filter[key] = val;
        else if (partial && (key || val)) filter[key || ''] = val || '';
    });
    return filter;
}

function _updateFilterPreview(modal, containerSel) {
    const container = modal.querySelector(containerSel);
    const previewSel = container?.dataset.preview;
    if (!previewSel) return;   // preview not wired yet — querySelector('') throws
    const preview = modal.querySelector(previewSel);
    if (!preview) return;
    const n = Object.keys(_readFilterRows(modal, containerSel)).length;
    preview.textContent = n ? `${n} active` : '';
}

function _wireFilterRows(modal, containerSel, addBtnSel, previewSel) {
    const container = modal.querySelector(containerSel);
    if (!container) return;
    container.dataset.preview = previewSel;

    modal.querySelector(addBtnSel)?.addEventListener('click', () => {
        container.appendChild(_filterRowEl('', '', container._fields || null));
    });
    container.addEventListener('change', e => {
        if (e.target.classList.contains('filter-key'))
            e.target.closest('.filter-row')?.classList.toggle('custom', e.target.value === '__custom__');
        _updateFilterPreview(modal, containerSel);
    });
    container.addEventListener('input', () => _updateFilterPreview(modal, containerSel));
    container.addEventListener('click', e => {
        if (e.target.classList.contains('filter-del')) {
            e.target.closest('.filter-row')?.remove();
            _updateFilterPreview(modal, containerSel);
        }
    });
}

function _updateRealtimeNote(modal) {
    const note = modal.querySelector('#ed-realtime-note');
    if (!note) return;
    const sourceName = modal.querySelector('#ed-event-source')?.value;
    const source = _sourcesCache.find(s => s.name === sourceName);
    note.style.display = source?.realtime ? '' : 'none';
}

function _renderTaskFields(modal) {
    const container = modal.querySelector('#ed-task-fields');
    if (!container) return;

    const sourceName = modal.querySelector('#ed-event-source')?.value;
    if (!sourceName) { container.innerHTML = ''; return; }

    const source = _sourcesCache.find(s => s.name === sourceName);
    const fields = source?.task_fields;
    if (!fields || fields.length === 0) { container.innerHTML = ''; return; }

    // Read current trigger_config for pre-filling (stored on container by editor)
    const existing = JSON.parse(container.dataset.triggerConfig || '{}');

    let html = '<div class="sched-section-title" style="margin-top:12px">Source Settings</div>';
    for (const f of fields) {
        const val = existing[f.key] ?? f.default ?? '';
        const help = f.help ? ` <span class="help-tip" data-tip="${_esc(f.help)}">?</span>` : '';
        const req = f.required ? ' <span style="color:var(--error)">*</span>' : '';

        if (f.type === 'select' && f.dynamic) {
            // Dynamic select — options fetched from API
            html += `<div class="sched-field">
                <label>${_esc(f.label || f.key)}${req}${help}</label>
                <select id="ed-tf-${f.key}" data-task-field="${f.key}" data-dynamic="${_esc(f.dynamic)}" data-current-value="${_esc(String(val))}">
                    <option value="">Loading...</option>
                </select></div>`;
        } else if (f.type === 'select' && f.options) {
            // Static select
            const opts = f.options.map(o => {
                const ov = typeof o === 'string' ? o : o.value;
                const ol = typeof o === 'string' ? o : (o.label || o.value);
                return `<option value="${_esc(ov)}" ${String(val) === String(ov) ? 'selected' : ''}>${_esc(ol)}</option>`;
            }).join('');
            html += `<div class="sched-field">
                <label>${_esc(f.label || f.key)}${req}${help}</label>
                <select id="ed-tf-${f.key}" data-task-field="${f.key}">${opts}</select></div>`;
        } else if (f.type === 'boolean') {
            html += `<div class="sched-checkbox">
                <label><input type="checkbox" id="ed-tf-${f.key}" data-task-field="${f.key}" ${val ? 'checked' : ''}> ${_esc(f.label || f.key)}${help}</label></div>`;
        } else if (f.type === 'number') {
            html += `<div class="sched-field">
                <label>${_esc(f.label || f.key)}${req}${help}</label>
                <input type="number" id="ed-tf-${f.key}" data-task-field="${f.key}" value="${_esc(String(val))}"
                    ${f.min != null ? `min="${f.min}"` : ''} ${f.max != null ? `max="${f.max}"` : ''}></div>`;
        } else {
            // string (default), password
            const widget = f.widget || 'text';
            html += `<div class="sched-field">
                <label>${_esc(f.label || f.key)}${req}${help}</label>
                <input type="${widget === 'password' ? 'password' : 'text'}" id="ed-tf-${f.key}" data-task-field="${f.key}"
                    value="${_esc(String(val))}" ${f.placeholder ? `placeholder="${_esc(f.placeholder)}"` : ''}></div>`;
        }
    }

    container.innerHTML = html;

    // Fetch dynamic selects
    container.querySelectorAll('select[data-dynamic]').forEach(async sel => {
        try {
            const res = await fetch(sel.dataset.dynamic);
            if (!res.ok) throw new Error();
            const data = await res.json();
            const options = data.accounts || data.options || data || [];
            sel.innerHTML = '<option value="">Select...</option>';
            for (const o of options) {
                const ov = typeof o === 'string' ? o : (o.value || o.id || o.name);
                const ol = typeof o === 'string' ? o : (o.label || o.name || o.value);
                const opt = document.createElement('option');
                opt.value = ov; opt.textContent = ol;
                sel.appendChild(opt);
            }
            const cur = sel.dataset.currentValue;
            if (cur) sel.value = cur;
        } catch {
            sel.innerHTML = '<option value="">Could not load options</option>';
        }
    });
}

function _esc(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML.replace(/"/g, '&quot;');
}
