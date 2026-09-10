// settings-tabs/llm.js - LLM provider configuration
// Delegates heavy lifting to shared/llm-providers.js
import {
    fetchProviderData, updateProvider, updateFallbackOrder,
    saveGenerationParams, renderProviderCard, loadModelGenParamsIntoCard,
    collectGenParamsFromCard, collectProviderFormData, initProviderDragDrop,
    refreshProviderKeyStatus, updateCardEnabledState, toggleProviderCollapse,
    handleModelSelectChange, runTestConnection
} from '../../shared/llm-providers.js';
import { showToast } from '../../shared/toast.js';

let generationProfiles = {};
let providerMetadata = {};

export default {
    id: 'llm',
    name: 'LLM',
    icon: '\uD83E\uDDE0',
    description: 'Language model providers and fallback order',
    generalKeys: ['LLM_MAX_HISTORY', 'CONTEXT_LIMIT', 'LLM_REQUEST_TIMEOUT', 'FORCE_THINKING', 'THINKING_PREFILL', 'SPICE_DELIVERY'],

    render(ctx) {
        const coreProviders = ctx.getValue('LLM_PROVIDERS') || {};
        const customProviders = ctx.getValue('LLM_CUSTOM_PROVIDERS') || {};
        const allProviders = {...coreProviders, ...customProviders};
        const fallbackOrder = ctx.getValue('LLM_FALLBACK_ORDER') || Object.keys(allProviders);
        generationProfiles = ctx.getValue('MODEL_GENERATION_PROFILES') || {};
        const meta = ctx.providerMeta || providerMetadata;

        // One unified list in fallback order \u2014 core and custom mixed, stragglers appended
        const ordered = fallbackOrder.filter(k => allProviders[k]);
        Object.keys(allProviders).forEach(k => { if (!ordered.includes(k)) ordered.push(k); });

        const rows = ordered.map((k, i) =>
            coreProviders[k]
                ? renderProviderCard(k, coreProviders[k], meta[k] || {}, i, generationProfiles)
                : _renderCustomRow(k, customProviders[k], i)
        ).join('');

        return `
            <h4 style="margin:0 0 4px">Providers</h4>
            <p class="text-muted" style="margin:0 0 12px;font-size:var(--font-sm)">Drag to reorder \u2014 Auto tries top to bottom. Core providers toggle off but can't be removed.</p>
            <div id="providers-list">${rows}</div>

            <button class="btn btn-primary" id="add-custom-provider" style="width:100%;padding:10px 16px;font-size:var(--font-md);margin:2px 0 8px">+ Add Provider</button>
            ${Object.keys(customProviders).length ? '' : '<p class="text-muted" style="font-size:var(--font-sm)">Connect Fireworks, OpenRouter, LM Studio, and more \u2014 added providers join this list and reorder like the rest.</p>'}
            <div id="add-provider-wizard" style="display:none"></div>

            <div style="margin-top:24px">
                <h4 style="margin:0 0 12px">General</h4>
                ${ctx.renderFields(this.generalKeys)}
            </div>
        `;
    },

    async attachListeners(ctx, el) {
        // Sync local metadata cache from ctx (pre-fetched in loadData)
        if (ctx.providerMeta && Object.keys(ctx.providerMeta).length) {
            providerMetadata = ctx.providerMeta;
        }

        refreshProviderKeyStatus(el);

        // Collapse toggle (core cards + custom edit accordions). Ignore clicks
        // on the drag handle and on the custom rows' inline controls.
        el.querySelectorAll('.provider-header').forEach(h => {
            h.addEventListener('click', e => {
                if (e.target.closest('.provider-drag-handle') || e.target.closest('.custom-provider-actions')) return;
                toggleProviderCollapse(h.closest('.provider-card'));
            });
        });

        // Enable toggle
        el.querySelectorAll('.provider-enabled').forEach(t => {
            t.addEventListener('change', async e => {
                try {
                    await updateProvider(e.target.dataset.provider, { enabled: e.target.checked });
                    updateCardEnabledState(e.target.closest('.provider-card'), e.target.checked);
                } catch (err) {
                    showToast('Failed to update provider', 'error');
                    e.target.checked = !e.target.checked;
                }
            });
        });

        // Field changes
        el.querySelectorAll('.provider-field').forEach(input => {
            input.addEventListener('change', async e => {
                const key = e.target.dataset.provider;
                const field = e.target.dataset.field;
                if (field === 'model_select') return;

                try {
                    if (field === 'api_key') {
                        if (e.target.value.trim()) {
                            await updateProvider(key, { api_key: e.target.value });
                            e.target.value = '';
                            e.target.placeholder = '\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022';
                            refreshProviderKeyStatus(el);
                            showToast('API key saved', 'success', 2000);
                        }
                        return;
                    }

                    let value = e.target.value;
                    if (field === 'timeout') value = parseFloat(value) || 5;
                    if (['use_as_fallback', 'thinking_enabled', 'cache_enabled', 'disable_thinking', 'disable_thinking_qwen'].includes(field)) value = e.target.checked;
                    await updateProvider(key, { [field]: value });
                    showToast('Provider settings saved', 'success', 2000);
                } catch (err) {
                    showToast('Failed to save provider settings', 'error');
                }
            });
        });

        // Thinking/cache toggle visibility
        el.querySelectorAll('.thinking-toggle, .cache-toggle').forEach(t => {
            t.addEventListener('change', e => {
                const prov = e.target.dataset.provider;
                const type = e.target.classList.contains('thinking-toggle') ? 'thinking' : 'cache';
                const val = el.querySelector(`.toggle-value[data-toggle="${type}"][data-provider="${prov}"]`);
                if (val) val.classList.toggle('hidden', !e.target.checked);
            });
        });

        // Generation params
        el.querySelectorAll('.gen-param-input').forEach(input => {
            input.addEventListener('change', async () => {
                const card = input.closest('.provider-card');
                const model = card.querySelector('.generation-params-section')?.dataset.model;
                if (!model) return;
                try {
                    generationProfiles = await saveGenerationParams(model, collectGenParamsFromCard(card), generationProfiles);
                    showToast('Model params saved', 'success', 2000);
                } catch (e) {
                    showToast('Failed to save model params', 'error');
                }
            });
        });

        // Model select
        el.querySelectorAll('.model-select').forEach(select => {
            select.addEventListener('change', async e => {
                const key = e.target.dataset.provider;
                const card = e.target.closest('.provider-card');
                const model = handleModelSelectChange(card, e.target.value);
                if (model) {
                    try {
                        await updateProvider(key, { model });
                        loadModelGenParamsIntoCard(card, model, generationProfiles);
                    } catch (err) {
                        showToast(`Failed to save model: ${err.message || err}`, 'error');
                    }
                }
            });
        });

        // Custom model
        el.querySelectorAll('.model-custom').forEach(input => {
            input.addEventListener('change', async e => {
                const key = e.target.dataset.provider;
                const card = e.target.closest('.provider-card');
                const model = e.target.value.trim();
                if (model) {
                    try {
                        await updateProvider(key, { model });
                        loadModelGenParamsIntoCard(card, model, generationProfiles);
                    } catch (err) {
                        showToast(`Failed to save model: ${err.message || err}`, 'error');
                    }
                }
            });
        });

        // Test connection
        el.querySelectorAll('.btn-test').forEach(btn => {
            btn.addEventListener('click', () => {
                const key = btn.dataset.provider;
                const card = el.querySelector(`.provider-card[data-provider="${key}"]`);
                runTestConnection(key, el, collectProviderFormData(card));
            });
        });

        // Auto-enroll stragglers (fork 3A, 2026-07-16): the header says "Auto
        // tries top to bottom", so the rendered list must BE the stored order.
        // Providers missing from LLM_FALLBACK_ORDER (e.g. openai on older
        // installs) rendered numbered but Auto never tried them.
        {
            const storedOrder = ctx.getValue('LLM_FALLBACK_ORDER') || [];
            const renderedOrder = [...el.querySelectorAll('#providers-list > [data-provider]')]
                .map(c => c.dataset.provider);
            if (renderedOrder.length && JSON.stringify(renderedOrder) !== JSON.stringify(storedOrder)) {
                updateFallbackOrder(renderedOrder)
                    .then(() => { ctx.settings.LLM_FALLBACK_ORDER = renderedOrder; })
                    .catch(() => {});   // next drag persists it
            }
        }

        // Drag-drop reorder — the list IS the full fallback order (core + custom)
        initProviderDragDrop(el.querySelector('#providers-list'), async order => {
            try {
                await updateFallbackOrder(order);
            } catch (e) {
                // Fire-and-forget left the UI showing an order the disk never
                // got — surface the failure so the user re-drags.
                showToast(`Failed to save provider order: ${e.message}`, 'error');
            }
        });

        // Custom provider enable toggle
        el.querySelectorAll('.custom-provider-enabled').forEach(t => {
            t.addEventListener('change', async e => {
                try {
                    await updateProvider(e.target.dataset.provider, { enabled: e.target.checked });
                    const card = e.target.closest('.provider-card');
                    if (card) {
                        card.classList.toggle('disabled', !e.target.checked);
                        card.classList.toggle('enabled', e.target.checked);
                    }
                    const status = card?.querySelector('.custom-provider-status');
                    if (status) status.textContent = e.target.checked ? '\uD83D\uDFE2' : '\u26AB';
                } catch (err) {
                    showToast('Failed to update provider', 'error');
                    e.target.checked = !e.target.checked;
                }
            });
        });

        // Custom provider delete
        el.querySelectorAll('.custom-provider-delete').forEach(btn => {
            btn.addEventListener('click', async () => {
                const key = btn.dataset.provider;
                if (!confirm(`Remove provider "${key}"?`)) return;
                try {
                    const res = await fetch(`/api/llm/custom-providers/${key}`, { method: 'DELETE' });
                    if (!res.ok) {
                        const err = await res.json().catch(() => ({}));
                        throw new Error(err.detail || `HTTP ${res.status}`);
                    }
                    showToast(`Removed: ${key}`, 'success');
                    ctx.refreshTab();
                } catch (e) {
                    showToast(`Failed to remove: ${e.message}`, 'error');
                }
            });
        });

        // Bind each custom card's pre-rendered edit form (accordion body)
        Object.entries(ctx.getValue('LLM_CUSTOM_PROVIDERS') || {}).forEach(([k, c]) => {
            const host = el.querySelector(`.provider-card[data-provider="${k}"] .provider-fields`);
            if (host) _bindProviderForm(host, 'pf-' + k, ctx, k, {}, c || {});
        });

        // Add provider button
        el.querySelector('#add-custom-provider')?.addEventListener('click', async () => {
            _showAddWizard(el, ctx);
        });
    }
};

function _esc(s) { return s ? s.replace(/</g, '&lt;').replace(/>/g, '&gt;') : ''; }

// Custom provider card — same structure as core cards (header + accordion body)
// so the shared collapse/drag machinery treats both identically. The body holds
// the edit form, pre-rendered at build time (instant open, no fetch).
function _renderCustomRow(k, c, i) {
    const enabled = c.enabled || false;
    const statusIcon = enabled ? '🟢' : '⚫';
    const template = c.template || c.provider || 'openai';
    const model = c.model || '';
    return `
        <div class="provider-card custom-card ${enabled ? 'enabled' : 'disabled'}" data-provider="${k}">
            <div class="provider-header" data-provider="${k}">
                <div class="provider-title">
                    <span class="provider-drag-handle" title="Drag to reorder">⋮⋮</span>
                    <span class="provider-order">${i + 1}</span>
                    <span class="custom-provider-status">${statusIcon}</span>
                    <span class="provider-icon">${c.is_local ? '\uD83C\uDFE0' : '\u2601\uFE0F'}</span>
                    <span class="provider-name">${_esc(c.display_name || k)}</span>
                    <span class="custom-provider-detail">${_esc(model)} · ${template}</span>
                </div>
                <div class="custom-provider-actions">
                    ${c.supports_images ? '<span class="vision-badge" title="Vision model — sees images">👁</span>' : ''}
                    <button class="btn btn-sm btn-danger custom-provider-delete" data-provider="${k}" title="Remove">✕</button>
                    <label class="toggle-switch toggle-sm" onclick="event.stopPropagation()">
                        <input type="checkbox" class="custom-provider-enabled" data-provider="${k}" ${enabled ? 'checked' : ''}>
                        <span class="toggle-slider"></span>
                    </label>
                </div>
            </div>
            <div class="provider-fields collapsed">
                ${_providerFormHtml('pf-' + k, c, { mode: 'edit' })}
            </div>
        </div>
    `;
}

// ── Shared "Advanced" fields (single source of truth for Add + Edit wizards) ──
// Renders temp/max-tokens/top-p, the optional sampling knobs (penalties / top-k:
// blank = never sent), the universal disable-thinking toggle + raw extra_body
// escape hatch. `prefix` namespaces the element ids ('wizard' | 'edit').
const _OPTIONAL_KNOBS = [
    // [id, key, label, step, min, max]
    ['pres', 'presence_penalty', 'Presence penalty', '0.05', '-2', '2'],
    ['freq', 'frequency_penalty', 'Frequency penalty', '0.05', '-2', '2'],
    ['rep', 'repeat_penalty', 'Repeat penalty', '0.01', '0', '3'],
    ['topk', 'top_k', 'Top K', '1', '0', ''],
];

function _advancedFieldsHtml(prefix, v = {}) {
    const temp = v.temperature ?? 0.7;
    const maxTok = v.max_tokens ?? 4096;
    const topP = v.top_p ?? 0.9;
    const knobs = _OPTIONAL_KNOBS.map(([id, key, label, step, min, max]) => `
                <div class="field-row" style="margin-bottom:6px">
                    <label>${label}</label>
                    <input type="number" id="${prefix}-${id}" value="${v[key] ?? ''}" placeholder="off" step="${step}" min="${min}"${max ? ` max="${max}"` : ''} style="width:80px">
                </div>`).join('');
    const noThink = v.disable_thinking ? 'checked' : '';
    const extraBody = v.extra_body ? (typeof v.extra_body === 'string' ? v.extra_body : JSON.stringify(v.extra_body)) : '';
    return `
        <details style="margin-bottom:8px">
            <summary style="cursor:pointer;font-size:var(--font-sm);color:var(--text-muted)">Advanced</summary>
            <div style="padding:8px 0">
                <div class="field-row" style="margin-bottom:6px">
                    <label>Temperature</label>
                    <input type="number" id="${prefix}-temp" value="${temp}" step="0.05" min="0" max="2" style="width:80px">
                </div>
                <div class="field-row" style="margin-bottom:6px">
                    <label>Max Tokens</label>
                    <input type="number" id="${prefix}-maxtok" value="${maxTok}" step="1" min="1" style="width:80px">
                </div>
                <div class="field-row" style="margin-bottom:6px">
                    <label>Top P</label>
                    <input type="number" id="${prefix}-topp" value="${topP}" step="0.05" min="0" max="1" style="width:80px">
                </div>${knobs}
                <div class="text-muted" style="font-size:0.8em;margin-left:24px;margin-top:2px">
                    Blank = not sent. Repeat penalty and Top K are local-model knobs (LM Studio, llama.cpp — repeat 1.05–1.1 or presence 1–1.5 tames a looping Qwen). A provider that rejects one is retried without it and a warning names it.
                </div>
                <div class="field-row" style="margin-top:10px;padding-top:8px;border-top:1px solid var(--border)">
                    <label class="checkbox-inline">
                        <input type="checkbox" id="${prefix}-no-think" ${noThink}>
                        <span>Disable thinking (best effort)</span>
                    </label>
                </div>
                <div class="text-muted" style="font-size:0.8em;margin-left:24px;margin-top:2px">
                    Tries the right switch per model — GLM/Z.AI, Qwen, Fireworks reasoning, and Claude (incl. Anthropic-compatible). OpenAI o-series/GPT-5 and Gemini can't be disabled from here yet — hit 🧠 Thinking to see what a given model actually does. Cuts latency by skipping the reasoning phase.
                </div>
                <div class="field-row" style="margin-top:8px;align-items:flex-start">
                    <label style="padding-top:4px">Extra body (JSON)</label>
                    <textarea id="${prefix}-extra-body" rows="2" style="flex:1;font-family:monospace;font-size:0.85em"
                        placeholder='{"thinking": {"type": "disabled"}}'>${extraBody}</textarea>
                </div>
                <div class="text-muted" style="font-size:0.8em;margin-left:24px;margin-top:2px">
                    Advanced escape hatch — merged verbatim into the request. Use if the checkbox doesn't cover your model (it wins over the checkbox).
                </div>
            </div>
        </details>`;
}

// Read the shared Advanced fields back out. Returns {ok, generation_params,
// disable_thinking, extra_body} or {ok:false, error} on invalid extra_body JSON.
function _readAdvancedFields(root, prefix) {
    const g = id => root.querySelector(`#${prefix}-${id}`);
    const temp = parseFloat(g('temp')?.value);
    const maxTok = parseInt(g('maxtok')?.value);
    const topP = parseFloat(g('topp')?.value);
    const generation_params = {};
    if (!isNaN(temp)) generation_params.temperature = temp;
    if (!isNaN(maxTok)) generation_params.max_tokens = maxTok;
    if (!isNaN(topP)) generation_params.top_p = topP;
    for (const [id, key] of _OPTIONAL_KNOBS) {
        const raw = (g(id)?.value ?? '').trim();
        const n = key === 'top_k' ? parseInt(raw) : parseFloat(raw);
        if (raw !== '' && !isNaN(n)) generation_params[key] = n;   // blank = never sent
    }
    const disable_thinking = g('no-think')?.checked || false;
    const rawExtra = (g('extra-body')?.value || '').trim();
    let extra_body = '';
    if (rawExtra) {
        try { JSON.parse(rawExtra); extra_body = rawExtra; }
        catch (e) { return { ok: false, error: 'Extra body is not valid JSON' }; }
    }
    return { ok: true, generation_params, disable_thinking, extra_body };
}

// ── Unified Add/Edit provider form ───────────────────────────────────────────
// ONE builder + ONE binder for both flows (they used to be two hand-written
// wizards that kept diverging). Edit forms live in each custom card's accordion
// (prefix pf-<key>); the Add form lives under + Add Provider (prefix pf-new).
// The only true divergence left is the save call: POST create vs PUT update.

function _providerFormHtml(prefix, config = {}, opts = {}) {
    const isAdd = opts.mode === 'add';
    const gen = config.generation_params || {};
    const shared = `
        <div class="field-row" style="margin-bottom:8px">
            <label>Base URL</label>
            <input type="text" id="${prefix}-url" value="${_esc(config.base_url || '')}" placeholder="https://api.example.com/v1" style="width:100%">
        </div>
        <div class="field-row" style="margin-bottom:8px">
            <label>API Key</label>
            <input type="password" id="${prefix}-key" value="" placeholder="${isAdd ? 'Optional' : 'Enter to change'}" style="width:100%">
        </div>
        <div class="field-row" style="margin-bottom:8px">
            <label>Model</label>
            <input type="text" id="${prefix}-model" value="${_esc(config.model || '')}" placeholder="model-name" style="width:100%">
            <div id="${prefix}-suggested" style="margin-top:4px"></div>
        </div>
        <div class="field-row" style="margin-bottom:8px">
            <label class="checkbox-inline" title="Turn on for VLMs whose name doesn't include a vision token (e.g. Qwen3 27B without 'VL'). Restart to apply."><input type="checkbox" id="${prefix}-vision" ${config.supports_images ? 'checked' : ''}> Vision model — can see images</label>
        </div>
        <div class="field-row" style="margin-bottom:8px">
            <label class="checkbox-inline"><input type="checkbox" id="${prefix}-local" ${config.is_local ? 'checked' : ''}> Local / private server (allowed in private chats)</label>
        </div>
        <div class="field-row" style="margin-bottom:8px">
            <label class="checkbox-inline"><input type="checkbox" id="${prefix}-fallback" ${config.use_as_fallback !== false ? 'checked' : ''}> Include in Auto fallback</label>
        </div>
        ${_advancedFieldsHtml(prefix, {
            temperature: gen.temperature,
            max_tokens: gen.max_tokens,
            top_p: gen.top_p,
            presence_penalty: gen.presence_penalty,
            frequency_penalty: gen.frequency_penalty,
            repeat_penalty: gen.repeat_penalty,
            top_k: gen.top_k,
            disable_thinking: (config.disable_thinking ?? config.disable_thinking_qwen),
            extra_body: config.extra_body,
        })}
        <div style="display:flex;gap:8px;align-items:center">
            <button class="btn btn-primary btn-sm" id="${prefix}-save">${isAdd ? 'Add' : 'Save'}</button>
            ${isAdd
                ? `<button class="btn btn-sm" id="${prefix}-cancel">Cancel</button>`
                : `<button class="btn btn-sm" id="${prefix}-test">Test</button>
                   <button class="btn btn-sm" id="${prefix}-test-think" title="Provoke reasoning, then check whether 'Disable thinking' actually suppresses it (2 quick calls)">🧠 Thinking</button>`}
        </div>
        <div id="${prefix}-status" class="text-muted" style="margin-top:8px;font-size:0.85em"></div>
    `;

    if (!isAdd) return `<div class="provider-form">${shared}</div>`;

    return `
        <div class="provider-form">
            <div class="field-row" style="margin-bottom:8px">
                <label>From Preset</label>
                <select id="${prefix}-preset" style="width:100%">
                    <option value="">-- Select a preset or choose manual --</option>
                    ${opts.presetOptions || ''}
                    <option value="__manual_openai__">Manual: OpenAI Compatible</option>
                    <option value="__manual_anthropic__">Manual: Anthropic Compatible</option>
                    <option value="__manual_responses__">Manual: Responses API</option>
                </select>
            </div>
            <div id="${prefix}-body" style="display:none">
                <div class="field-row" style="margin-bottom:8px">
                    <label>Name</label>
                    <input type="text" id="${prefix}-name" placeholder="my-provider" style="width:100%">
                </div>
                ${shared}
            </div>
        </div>
    `;
}

function _bindProviderForm(root, prefix, ctx, key = null, presets = {}, config = {}) {
    const g = id => root.querySelector(`#${prefix}-${id}`);
    const status = g('status');
    const setStatus = (msg, color = 'var(--text-muted)') => {
        if (status) { status.textContent = msg; status.style.color = color; }
    };

    // ── Add mode: preset picker drives template + prefills ──
    let selectedTemplate = 'openai';
    let selectedPreset = null;

    g('preset')?.addEventListener('change', e => {
        const val = e.target.value;
        const body = g('body');
        if (!val) { body.style.display = 'none'; return; }
        body.style.display = 'block';

        if (val.startsWith('__manual_')) {
            selectedTemplate = val.replace('__manual_', '').replace('__', '');
            selectedPreset = null;
            g('name').value = '';
            g('url').value = '';
            g('model').value = '';
            g('suggested').innerHTML = '';
            g('local').checked = false;
        } else {
            const preset = presets[val];
            if (!preset) return;
            selectedTemplate = preset.template || 'openai';
            selectedPreset = val;
            // Pre-fill Name with the friendly display_name (editable) — this IS the
            // friendly name; the backend derives the key by sanitizing it.
            g('name').value = preset.display_name || val;
            g('url').value = preset.base_url || '';
            g('model').value = '';
            // Visible pre-fill for loopback presets (LM Studio, Ollama) — the
            // checkbox is the source of truth, user can untick before saving
            g('local').checked = /127\.0\.0\.1|localhost/.test(preset.base_url || '');
            const suggested = preset.suggested_models || [];
            if (suggested.length) {
                g('suggested').innerHTML = '<small class="text-muted">Suggested: ' +
                    suggested.map(m => `<a href="#" class="wizard-model-pick" data-model="${_esc(m.id)}" style="margin-right:6px">${_esc(m.name)}</a>`).join('') + '</small>';
                g('suggested').querySelectorAll('.wizard-model-pick').forEach(a => {
                    a.addEventListener('click', ev => { ev.preventDefault(); g('model').value = a.dataset.model; });
                });
            } else {
                g('suggested').innerHTML = '';
            }
            const genDefaults = preset.generation_defaults || {};
            if (genDefaults.temperature !== undefined) g('temp').value = genDefaults.temperature;
            if (genDefaults.max_tokens !== undefined) g('maxtok').value = genDefaults.max_tokens;
            if (genDefaults.top_p !== undefined) g('topp').value = genDefaults.top_p;
        }
    });

    g('cancel')?.addEventListener('click', () => {
        const wizard = root.closest('#add-provider-wizard') || root.querySelector('#add-provider-wizard');
        const host = wizard || root;
        host.style.display = 'none';
        host.innerHTML = '';
    });

    // ── Edit mode: connection + thinking probes (need an existing key) ──
    g('test')?.addEventListener('click', async () => {
        setStatus('Testing...');
        try {
            const formData = { base_url: g('url')?.value, model: g('model')?.value };
            const apiKey = g('key')?.value?.trim();
            if (apiKey) formData.api_key = apiKey;
            const res = await fetch(`/api/llm/test/${key}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(formData) });
            const data = await res.json();
            if (data.status === 'success') setStatus('✓ ' + (data.response?.substring(0, 50) || 'Connected!'), 'var(--success)');
            else setStatus('✗ ' + (data.error || 'Failed'), 'var(--error)');
        } catch (e) { setStatus('✗ ' + e.message, 'var(--error)'); }
    });

    g('test-think')?.addEventListener('click', async () => {
        setStatus('Probing reasoning… (2 quick calls)');
        try {
            const adv = _readAdvancedFields(root, prefix);
            const payload = {
                base_url: g('url')?.value,
                model: g('model')?.value,
                disable_thinking: adv.ok ? adv.disable_thinking : false,
                extra_body: adv.ok ? adv.extra_body : '',
            };
            const apiKey = g('key')?.value?.trim();
            if (apiKey) payload.api_key = apiKey;
            const res = await fetch(`/api/llm/test-thinking/${key}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
            const data = await res.json();
            if (data.status === 'success') {
                const b = data.baseline, d = data.disabled;
                const counts = `baseline: ${b.reasoning_chars} think / ${b.prose_chars} prose`
                    + (data.suppress_active ? ` · disabled: ${d.reasoning_chars} think / ${d.prose_chars} prose` : '');
                status.innerHTML = `${_esc(data.verdict)}<br><small class="text-muted">${counts}</small>`;
                status.style.color = data.ok === true ? 'var(--success)' : (data.ok === false ? 'var(--error)' : 'var(--text-muted)');
            } else {
                let msg = '✗ ' + (data.error || 'Failed');
                if (data.baseline) msg += ` — baseline reasoned ${data.baseline.reasoning_chars} chars`;
                setStatus(msg, 'var(--error)');
            }
        } catch (e) { setStatus('✗ ' + e.message, 'var(--error)'); }
    });

    // ── Save: the one real Add/Edit divergence — POST create vs PUT update ──
    g('save')?.addEventListener('click', async () => {
        const adv = _readAdvancedFields(root, prefix);
        if (!adv.ok) { setStatus(adv.error, 'var(--error)'); return; }

        const common = {
            base_url: g('url')?.value?.trim(),
            model: g('model')?.value?.trim() || '',
            is_local: g('local')?.checked || false,
            use_as_fallback: g('fallback')?.checked ?? true,
            disable_thinking: adv.disable_thinking,
            extra_body: adv.extra_body,
        };
        const apiKey = g('key')?.value?.trim();
        if (apiKey) common.api_key = apiKey;

        // Vision override is tri-state in the backend: true/false force, absent
        // (None) falls through to the model-name heuristic. Only send an explicit
        // false if the provider already had a forced value — otherwise leaving the
        // box unticked keeps auto-detection alive.
        const vision = g('vision')?.checked || false;
        if (vision) common.supports_images = true;
        else if (typeof config.supports_images === 'boolean') common.supports_images = false;

        if (key) {
            // Edit → PUT
            common.generation_params = adv.generation_params;
            try {
                await updateProvider(key, common);
                showToast('Provider updated', 'success');
                ctx.refreshTab();
            } catch (e) { showToast('Failed to save', 'error'); }
            return;
        }

        // Add → POST
        const name = g('name')?.value?.trim();
        if (!name) { setStatus('Name required', 'var(--error)'); return; }
        // No anthropic exception: the backend requires base_url for ALL custom
        // providers (anthropic_compat genuinely raises without one) — skipping
        // the check here just moved the failure server-side after key storage.
        if (!common.base_url) { setStatus('URL required', 'var(--error)'); return; }

        const body = { ...common, name, display_name: name, template: selectedTemplate };
        if (Object.keys(adv.generation_params).length) body.generation_params = adv.generation_params;
        if (selectedPreset && presets[selectedPreset]?.config_hints) Object.assign(body, presets[selectedPreset].config_hints);
        if (selectedPreset && presets[selectedPreset]?.api_key_env) body.api_key_env = presets[selectedPreset].api_key_env;
        if (selectedPreset && presets[selectedPreset]?.auto_discover_models) body.auto_discover_models = true;

        const btn = g('save');
        btn.disabled = true;
        btn.textContent = 'Adding...';
        setStatus('Creating provider...');
        try {
            const res = await fetch('/api/llm/custom-providers', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body)
            });
            const data = await res.json();
            if (data.status === 'error' || data.detail) throw new Error(data.error || data.detail || 'Failed');
            showToast(`Added: ${data.name}`, 'success');
            const wizard = root.closest('#add-provider-wizard') || root;
            wizard.style.display = 'none';
            wizard.innerHTML = '';
            ctx.refreshTab();
        } catch (e) {
            setStatus(e.message, 'var(--error)');
            btn.disabled = false;
            btn.textContent = 'Add';
        }
    });
}

async function _showAddWizard(el, ctx) {
    const wizard = el.querySelector('#add-provider-wizard');
    if (!wizard) return;
    wizard.style.display = 'block';

    let presets = {};
    try {
        const res = await fetch('/api/llm/presets');
        const data = await res.json();
        presets = data.presets || {};
    } catch (e) { console.warn('Failed to fetch presets:', e); }

    const presetOptions = Object.entries(presets).map(([k, p]) =>
        `<option value="${k}">${_esc(p.display_name)}</option>`
    ).join('');

    wizard.innerHTML = `
        <div style="padding:14px;background:var(--bg-secondary);border-radius:var(--radius-sm);border:1px solid var(--border);margin-top:12px">
            <h5 style="margin:0 0 12px">Add Provider</h5>
            ${_providerFormHtml('pf-new', {}, { mode: 'add', presetOptions })}
        </div>
    `;
    _bindProviderForm(wizard, 'pf-new', ctx, null, presets);
}
