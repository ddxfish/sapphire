// settings-tabs/llm.js - LLM provider configuration
// Delegates heavy lifting to shared/llm-providers.js
import {
    fetchProviderData, updateProvider, updateFallbackOrder,
    saveGenerationParams, renderProviderCard, loadModelGenParamsIntoCard,
    collectGenParamsFromCard, collectProviderFormData, initProviderDragDrop,
    refreshProviderKeyStatus, updateCardEnabledState, toggleProviderCollapse,
    handleModelSelectChange, runTestConnection
} from '../../shared/llm-providers.js';

let generationProfiles = {};
let providerMetadata = {};

export default {
    id: 'llm',
    name: 'LLM',
    icon: '\uD83E\uDDE0',
    description: 'Language model providers and fallback order',
    generalKeys: ['LLM_MAX_HISTORY', 'CONTEXT_LIMIT', 'LLM_REQUEST_TIMEOUT', 'FORCE_THINKING', 'THINKING_PREFILL', 'IMAGE_UPLOAD_MAX_WIDTH'],

    render(ctx) {
        const providers = ctx.settings.LLM_PROVIDERS || {};
        const fallbackOrder = ctx.settings.LLM_FALLBACK_ORDER || Object.keys(providers);
        generationProfiles = ctx.settings.MODEL_GENERATION_PROFILES || {};

        if (!Object.keys(providers).length) {
            return '<p class="text-muted" style="padding:20px">No providers configured</p>';
        }

        // Render in fallback order
        const ordered = [...fallbackOrder];
        Object.keys(providers).forEach(k => { if (!ordered.includes(k)) ordered.push(k); });

        const cards = ordered.filter(k => providers[k]).map((k, i) => {
            const meta = providerMetadata[k] || {};
            return renderProviderCard(k, providers[k], meta, i, generationProfiles);
        }).join('');

        return `
            <h4 style="margin:0 0 4px">Providers</h4>
            <p class="text-muted" style="margin:0 0 12px;font-size:var(--font-sm)">Drag to reorder fallback priority. Test to verify connectivity.</p>
            <div id="providers-list">${cards}</div>
            <div style="margin-top:24px">
                <h4 style="margin:0 0 12px">General</h4>
                ${ctx.renderFields(this.generalKeys)}
            </div>
        `;
    },

    async attachListeners(ctx, el) {
        // Load metadata from API
        try {
            const data = await fetchProviderData();
            providerMetadata = data.metadata || {};
            // Refresh model dropdowns with real metadata
            el.querySelectorAll('.model-select').forEach(select => {
                const key = select.dataset.provider;
                const meta = providerMetadata[key];
                if (!meta?.model_options) return;
                const cur = select.value;
                const isCustom = cur === '__custom__' || (cur && !Object.keys(meta.model_options).includes(cur));
                select.innerHTML = Object.entries(meta.model_options)
                    .map(([m, label]) => `<option value="${m}" ${cur === m ? 'selected' : ''}>${label}</option>`)
                    .join('') + `<option value="__custom__" ${isCustom ? 'selected' : ''}>Other (custom)</option>`;
                if (isCustom && cur !== '__custom__') select.value = '__custom__';
            });
        } catch {}

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
                await updateProvider(e.target.dataset.provider, { enabled: e.target.checked });
                updateCardEnabledState(e.target.closest('.provider-card'), e.target.checked);
            });
        });

        // Field changes
        el.querySelectorAll('.provider-field').forEach(input => {
            input.addEventListener('change', async e => {
                const key = e.target.dataset.provider;
                const field = e.target.dataset.field;
                if (field === 'model_select') return;

                if (field === 'api_key') {
                    if (e.target.value.trim()) {
                        await updateProvider(key, { api_key: e.target.value });
                        e.target.value = '';
                        e.target.placeholder = '\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022';
                        refreshProviderKeyStatus(el);
                    }
                    return;
                }

                let value = e.target.value;
                if (field === 'timeout') value = parseFloat(value) || 5;
                if (['use_as_fallback', 'thinking_enabled', 'cache_enabled'].includes(field)) value = e.target.checked;
                if (field === 'thinking_budget') value = parseInt(value) || 10000;
                await updateProvider(key, { [field]: value });
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
                generationProfiles = await saveGenerationParams(model, collectGenParamsFromCard(card), generationProfiles);
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
        initProviderDragDrop(el.querySelector('#providers-list'), order => updateFallbackOrder(order));
    }
};
