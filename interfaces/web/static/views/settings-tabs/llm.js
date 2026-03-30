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
    generalKeys: ['LLM_MAX_HISTORY', 'CONTEXT_LIMIT', 'LLM_REQUEST_TIMEOUT', 'FORCE_THINKING', 'THINKING_PREFILL', 'IMAGE_UPLOAD_MAX_WIDTH'],

    render(ctx) {
        const coreProviders = ctx.settings.LLM_PROVIDERS || {};
        const customProviders = ctx.settings.LLM_CUSTOM_PROVIDERS || {};
        const allProviders = {...coreProviders, ...customProviders};
        const fallbackOrder = ctx.settings.LLM_FALLBACK_ORDER || Object.keys(allProviders);
        generationProfiles = ctx.settings.MODEL_GENERATION_PROFILES || {};

        // Core provider cards (in fallback order)
        const coreKeys = Object.keys(coreProviders);
        const meta = ctx.providerMeta || providerMetadata;
        const coreOrdered = fallbackOrder.filter(k => coreKeys.includes(k));
        coreKeys.forEach(k => { if (!coreOrdered.includes(k)) coreOrdered.push(k); });
        const coreCards = coreOrdered.filter(k => coreProviders[k]).map((k, i) => {
            return renderProviderCard(k, coreProviders[k], meta[k] || {}, i, generationProfiles);
        }).join('');

        // Custom provider rows
        const customKeys = Object.keys(customProviders);
        const customOrdered = fallbackOrder.filter(k => customKeys.includes(k));
        customKeys.forEach(k => { if (!customOrdered.includes(k)) customOrdered.push(k); });
        const customRows = customOrdered.filter(k => customProviders[k]).map(k => {
            const c = customProviders[k];
            const enabled = c.enabled || false;
            const statusIcon = enabled ? '\uD83D\uDFE2' : '\u26AB';
            const template = c.template || c.provider || 'openai';
            const model = c.model || '';
            return `
                <div class="custom-provider-row ${enabled ? '' : 'disabled'}" data-provider="${k}">
                    <div class="custom-provider-info">
                        <span class="custom-provider-status">${statusIcon}</span>
                        <span class="custom-provider-name">${_esc(c.display_name || k)}</span>
                        <span class="custom-provider-detail">${_esc(model)} \u00B7 ${template}</span>
                    </div>
                    <div class="custom-provider-actions">
                        <label class="toggle-switch toggle-sm" onclick="event.stopPropagation()">
                            <input type="checkbox" class="custom-provider-enabled" data-provider="${k}" ${enabled ? 'checked' : ''}>
                            <span class="toggle-slider"></span>
                        </label>
                        <button class="btn btn-sm btn-danger custom-provider-delete" data-provider="${k}" title="Remove">\u2715</button>
                    </div>
                </div>
            `;
        }).join('');

        return `
            <h4 style="margin:0 0 4px">Core Providers</h4>
            <p class="text-muted" style="margin:0 0 12px;font-size:var(--font-sm)">Drag to reorder fallback priority. Test to verify connectivity.</p>
            <div id="providers-list">${coreCards}</div>

            <div style="margin-top:24px">
                <h4 style="margin:0 0 12px">Custom Providers</h4>
                <button class="btn btn-primary" id="add-custom-provider" style="width:100%;padding:10px 16px;font-size:var(--font-md);margin-bottom:14px">+ Add Provider</button>
                <div id="custom-providers-list">
                    ${customRows || '<p class="text-muted" style="font-size:var(--font-sm)">No custom providers. Click + Add Provider to connect Fireworks, OpenRouter, LM Studio, and more.</p>'}
                </div>
                <div id="add-provider-wizard" style="display:none"></div>
            </div>

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

        // Collapse toggle
        el.querySelectorAll('.provider-header').forEach(h => {
            h.addEventListener('click', e => {
                if (e.target.closest('.provider-drag-handle')) return;
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
                    if (['use_as_fallback', 'thinking_enabled', 'cache_enabled'].includes(field)) value = e.target.checked;
                    if (field === 'thinking_budget') value = parseInt(value) || 10000;
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
            select.addEventListener('change', e => {
                const key = e.target.dataset.provider;
                const card = e.target.closest('.provider-card');
                const model = handleModelSelectChange(card, e.target.value);
                if (model) {
                    updateProvider(key, { model });
                    loadModelGenParamsIntoCard(card, model, generationProfiles);
                }
            });
        });

        // Custom model
        el.querySelectorAll('.model-custom').forEach(input => {
            input.addEventListener('change', e => {
                const key = e.target.dataset.provider;
                const card = e.target.closest('.provider-card');
                const model = e.target.value.trim();
                if (model) {
                    updateProvider(key, { model });
                    loadModelGenParamsIntoCard(card, model, generationProfiles);
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

        // Drag-drop reorder
        initProviderDragDrop(el.querySelector('#providers-list'), order => {
            // Merge core order with existing custom order
            const customKeys = Object.keys(ctx.settings.LLM_CUSTOM_PROVIDERS || {});
            const fullOrder = [...order, ...customKeys.filter(k => !order.includes(k))];
            updateFallbackOrder(fullOrder);
        });

        // Custom provider enable toggle
        el.querySelectorAll('.custom-provider-enabled').forEach(t => {
            t.addEventListener('change', async e => {
                try {
                    await updateProvider(e.target.dataset.provider, { enabled: e.target.checked });
                    const row = e.target.closest('.custom-provider-row');
                    if (row) row.classList.toggle('disabled', !e.target.checked);
                    const status = row?.querySelector('.custom-provider-status');
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
                    await fetch(`/api/llm/custom-providers/${key}`, { method: 'DELETE' });
                    showToast(`Removed: ${key}`, 'success');
                    ctx.refreshTab();
                } catch (e) {
                    showToast('Failed to remove', 'error');
                }
            });
        });

        // Custom provider row click to expand (edit inline)
        el.querySelectorAll('.custom-provider-row .custom-provider-info').forEach(info => {
            info.style.cursor = 'pointer';
            info.addEventListener('click', () => {
                const key = info.closest('.custom-provider-row').dataset.provider;
                const custom = ctx.settings.LLM_CUSTOM_PROVIDERS || {};
                const config = custom[key] || {};
                _showEditWizard(el, key, config, ctx);
            });
        });

        // Add provider button
        el.querySelector('#add-custom-provider')?.addEventListener('click', async () => {
            _showAddWizard(el, ctx);
        });
    }
};

function _esc(s) { return s ? s.replace(/</g, '&lt;').replace(/>/g, '&gt;') : ''; }

async function _showAddWizard(el, ctx) {
    const wizard = el.querySelector('#add-provider-wizard');
    if (!wizard) return;
    wizard.style.display = 'block';

    // Fetch presets
    let presets = {}, templates = [];
    try {
        const res = await fetch('/api/llm/presets');
        const data = await res.json();
        presets = data.presets || {};
        templates = data.templates || [];
    } catch (e) { console.warn('Failed to fetch presets:', e); }

    const presetOptions = Object.entries(presets).map(([k, p]) =>
        `<option value="${k}">${_esc(p.display_name)}</option>`
    ).join('');

    wizard.innerHTML = `
        <div style="padding:14px;background:var(--bg-secondary);border-radius:var(--radius-sm);border:1px solid var(--border);margin-top:12px">
            <h5 style="margin:0 0 12px">Add Provider</h5>
            <div class="field-row" style="margin-bottom:8px">
                <label>From Preset</label>
                <select id="wizard-preset" style="width:100%">
                    <option value="">-- Select a preset or choose manual --</option>
                    ${presetOptions}
                    <option value="__manual_openai__">Manual: OpenAI Compatible</option>
                    <option value="__manual_anthropic__">Manual: Anthropic Compatible</option>
                    <option value="__manual_responses__">Manual: Responses API</option>
                </select>
            </div>
            <div id="wizard-form" style="display:none">
                <div class="field-row" style="margin-bottom:8px">
                    <label>Name</label>
                    <input type="text" id="wizard-name" placeholder="my-provider" style="width:100%">
                </div>
                <div class="field-row" style="margin-bottom:8px">
                    <label>Base URL</label>
                    <input type="text" id="wizard-url" placeholder="https://api.example.com/v1" style="width:100%">
                </div>
                <div class="field-row" style="margin-bottom:8px">
                    <label>API Key</label>
                    <input type="password" id="wizard-key" placeholder="Optional" style="width:100%">
                </div>
                <div class="field-row" style="margin-bottom:8px">
                    <label>Model</label>
                    <input type="text" id="wizard-model" placeholder="model-name" style="width:100%">
                    <div id="wizard-suggested" style="margin-top:4px"></div>
                </div>
                <details style="margin-bottom:8px">
                    <summary style="cursor:pointer;font-size:var(--font-sm);color:var(--text-muted)">Advanced</summary>
                    <div style="padding:8px 0">
                        <div class="field-row" style="margin-bottom:6px">
                            <label>Temperature</label>
                            <input type="number" id="wizard-temp" value="0.7" step="0.05" min="0" max="2" style="width:80px">
                        </div>
                        <div class="field-row" style="margin-bottom:6px">
                            <label>Max Tokens</label>
                            <input type="number" id="wizard-maxtok" value="4096" step="1" min="1" style="width:80px">
                        </div>
                        <div class="field-row">
                            <label>Top P</label>
                            <input type="number" id="wizard-topp" value="0.9" step="0.05" min="0" max="1" style="width:80px">
                        </div>
                    </div>
                </details>
                <div style="display:flex;gap:8px">
                    <button class="btn btn-primary btn-sm" id="wizard-save">Add</button>
                    <button class="btn btn-sm" id="wizard-cancel">Cancel</button>
                </div>
                <div id="wizard-status" class="text-muted" style="margin-top:8px;font-size:0.85em"></div>
            </div>
        </div>
    `;

    let selectedTemplate = 'openai';
    let selectedPreset = null;

    // Preset selection
    wizard.querySelector('#wizard-preset')?.addEventListener('change', e => {
        const val = e.target.value;
        const form = wizard.querySelector('#wizard-form');
        if (!val) { form.style.display = 'none'; return; }
        form.style.display = 'block';

        if (val.startsWith('__manual_')) {
            selectedTemplate = val.replace('__manual_', '').replace('__', '');
            selectedPreset = null;
            wizard.querySelector('#wizard-name').value = '';
            wizard.querySelector('#wizard-url').value = '';
            wizard.querySelector('#wizard-model').value = '';
            wizard.querySelector('#wizard-suggested').innerHTML = '';
        } else {
            const preset = presets[val];
            if (!preset) return;
            selectedTemplate = preset.template || 'openai';
            selectedPreset = val;
            wizard.querySelector('#wizard-name').value = val;
            wizard.querySelector('#wizard-url').value = preset.base_url || '';
            wizard.querySelector('#wizard-model').value = '';
            // Suggested models
            const suggested = preset.suggested_models || [];
            if (suggested.length) {
                wizard.querySelector('#wizard-suggested').innerHTML = '<small class="text-muted">Suggested: ' +
                    suggested.map(m => `<a href="#" class="wizard-model-pick" data-model="${_esc(m.id)}" style="margin-right:6px">${_esc(m.name)}</a>`).join('') + '</small>';
                wizard.querySelectorAll('.wizard-model-pick').forEach(a => {
                    a.addEventListener('click', ev => { ev.preventDefault(); wizard.querySelector('#wizard-model').value = a.dataset.model; });
                });
            } else {
                wizard.querySelector('#wizard-suggested').innerHTML = '';
            }
            // Gen defaults from preset
            const gen = preset.generation_defaults || {};
            if (gen.temperature !== undefined) wizard.querySelector('#wizard-temp').value = gen.temperature;
            if (gen.max_tokens !== undefined) wizard.querySelector('#wizard-maxtok').value = gen.max_tokens;
            if (gen.top_p !== undefined) wizard.querySelector('#wizard-topp').value = gen.top_p;
        }
    });

    // Cancel
    wizard.querySelector('#wizard-cancel')?.addEventListener('click', () => {
        wizard.style.display = 'none';
        wizard.innerHTML = '';
    });

    // Save
    wizard.querySelector('#wizard-save')?.addEventListener('click', async () => {
        const name = wizard.querySelector('#wizard-name')?.value?.trim();
        const url = wizard.querySelector('#wizard-url')?.value?.trim();
        const key = wizard.querySelector('#wizard-key')?.value?.trim();
        const model = wizard.querySelector('#wizard-model')?.value?.trim();
        const status = wizard.querySelector('#wizard-status');

        if (!name) { status.textContent = 'Name required'; status.style.color = 'var(--error)'; return; }
        if (!url && selectedTemplate !== 'anthropic') { status.textContent = 'URL required'; status.style.color = 'var(--error)'; return; }

        const body = {
            name, template: selectedTemplate, base_url: url, model: model || '',
            display_name: selectedPreset ? (presets[selectedPreset]?.display_name || name) : name,
            is_local: url?.includes('127.0.0.1') || url?.includes('localhost') || false,
        };
        if (key) body.api_key = key;

        // Gen params
        const temp = parseFloat(wizard.querySelector('#wizard-temp')?.value);
        const maxTok = parseInt(wizard.querySelector('#wizard-maxtok')?.value);
        const topP = parseFloat(wizard.querySelector('#wizard-topp')?.value);
        if (!isNaN(temp) || !isNaN(maxTok) || !isNaN(topP)) {
            body.generation_params = {};
            if (!isNaN(temp)) body.generation_params.temperature = temp;
            if (!isNaN(maxTok)) body.generation_params.max_tokens = maxTok;
            if (!isNaN(topP)) body.generation_params.top_p = topP;
        }

        // Config hints from preset
        if (selectedPreset && presets[selectedPreset]?.config_hints) {
            Object.assign(body, presets[selectedPreset].config_hints);
        }
        if (selectedPreset && presets[selectedPreset]?.api_key_env) {
            body.api_key_env = presets[selectedPreset].api_key_env;
        }
        if (selectedPreset && presets[selectedPreset]?.auto_discover_models) {
            body.auto_discover_models = true;
        }

        const btn = wizard.querySelector('#wizard-save');
        btn.disabled = true;
        btn.textContent = 'Adding...';
        status.textContent = 'Creating provider...';
        status.style.color = 'var(--text-muted)';

        try {
            const res = await fetch('/api/llm/custom-providers', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body)
            });
            const data = await res.json();
            if (data.status === 'error' || data.detail) throw new Error(data.error || data.detail || 'Failed');
            showToast(`Added: ${data.name}`, 'success');
            wizard.style.display = 'none';
            wizard.innerHTML = '';
            ctx.refreshTab();
        } catch (e) {
            status.textContent = e.message;
            status.style.color = 'var(--error)';
            btn.disabled = false;
            btn.textContent = 'Add';
        }
    });
}

function _showEditWizard(el, key, config, ctx) {
    const wizard = el.querySelector('#add-provider-wizard');
    if (!wizard) return;
    wizard.style.display = 'block';

    wizard.innerHTML = `
        <div style="padding:14px;background:var(--bg-secondary);border-radius:var(--radius-sm);border:1px solid var(--border);margin-top:12px">
            <h5 style="margin:0 0 12px">Edit: ${_esc(config.display_name || key)}</h5>
            <div class="field-row" style="margin-bottom:8px">
                <label>Base URL</label>
                <input type="text" id="edit-url" value="${_esc(config.base_url || '')}" style="width:100%">
            </div>
            <div class="field-row" style="margin-bottom:8px">
                <label>API Key</label>
                <input type="password" id="edit-key" value="" placeholder="Enter to change" style="width:100%">
            </div>
            <div class="field-row" style="margin-bottom:8px">
                <label>Model</label>
                <input type="text" id="edit-model" value="${_esc(config.model || '')}" style="width:100%">
            </div>
            <details style="margin-bottom:8px">
                <summary style="cursor:pointer;font-size:var(--font-sm);color:var(--text-muted)">Advanced</summary>
                <div style="padding:8px 0">
                    <div class="field-row" style="margin-bottom:6px">
                        <label>Temperature</label>
                        <input type="number" id="edit-temp" value="${config.generation_params?.temperature ?? 0.7}" step="0.05" min="0" max="2" style="width:80px">
                    </div>
                    <div class="field-row" style="margin-bottom:6px">
                        <label>Max Tokens</label>
                        <input type="number" id="edit-maxtok" value="${config.generation_params?.max_tokens ?? 4096}" step="1" min="1" style="width:80px">
                    </div>
                    <div class="field-row">
                        <label>Top P</label>
                        <input type="number" id="edit-topp" value="${config.generation_params?.top_p ?? 0.9}" step="0.05" min="0" max="1" style="width:80px">
                    </div>
                </div>
            </details>
            <div style="display:flex;gap:8px">
                <button class="btn btn-primary btn-sm" id="edit-save">Save</button>
                <button class="btn btn-sm" id="edit-test">Test</button>
                <button class="btn btn-sm" id="edit-cancel">Cancel</button>
            </div>
            <span id="edit-status" class="text-muted" style="margin-left:8px;font-size:0.85em"></span>
        </div>
    `;

    wizard.querySelector('#edit-cancel')?.addEventListener('click', () => {
        wizard.style.display = 'none'; wizard.innerHTML = '';
    });

    wizard.querySelector('#edit-test')?.addEventListener('click', async () => {
        const status = wizard.querySelector('#edit-status');
        status.textContent = 'Testing...';
        status.style.color = 'var(--text-muted)';
        try {
            const formData = {
                base_url: wizard.querySelector('#edit-url')?.value,
                model: wizard.querySelector('#edit-model')?.value,
            };
            const apiKey = wizard.querySelector('#edit-key')?.value?.trim();
            if (apiKey) formData.api_key = apiKey;
            const res = await fetch(`/api/llm/test/${key}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(formData) });
            const data = await res.json();
            if (data.status === 'success') {
                status.textContent = '\u2713 ' + (data.response?.substring(0, 50) || 'Connected!');
                status.style.color = 'var(--success)';
            } else {
                status.textContent = '\u2717 ' + (data.error || 'Failed');
                status.style.color = 'var(--error)';
            }
        } catch (e) { status.textContent = '\u2717 ' + e.message; status.style.color = 'var(--error)'; }
    });

    wizard.querySelector('#edit-save')?.addEventListener('click', async () => {
        const updates = {
            base_url: wizard.querySelector('#edit-url')?.value?.trim(),
            model: wizard.querySelector('#edit-model')?.value?.trim(),
        };
        const apiKey = wizard.querySelector('#edit-key')?.value?.trim();
        if (apiKey) updates.api_key = apiKey;

        const temp = parseFloat(wizard.querySelector('#edit-temp')?.value);
        const maxTok = parseInt(wizard.querySelector('#edit-maxtok')?.value);
        const topP = parseFloat(wizard.querySelector('#edit-topp')?.value);
        updates.generation_params = {};
        if (!isNaN(temp)) updates.generation_params.temperature = temp;
        if (!isNaN(maxTok)) updates.generation_params.max_tokens = maxTok;
        if (!isNaN(topP)) updates.generation_params.top_p = topP;

        try {
            await updateProvider(key, updates);
            showToast('Provider updated', 'success');
            wizard.style.display = 'none'; wizard.innerHTML = '';
            ctx.refreshTab();
        } catch (e) { showToast('Failed to save', 'error'); }
    });
}
