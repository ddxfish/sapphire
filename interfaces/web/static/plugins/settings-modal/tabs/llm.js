// tabs/llm.js - LLM provider settings tab
// Uses shared/llm-providers.js for reusable components

import {
  fetchProviderData,
  updateProvider,
  updateFallbackOrder,
  saveGenerationParams,
  renderProviderCard,
  loadModelGenParamsIntoCard,
  collectGenParamsFromCard,
  collectProviderFormData,
  initProviderDragDrop,
  refreshProviderKeyStatus,
  updateCardEnabledState,
  toggleProviderCollapse,
  handleModelSelectChange,
  runTestConnection
} from '../../../shared/llm-providers.js';

let generationProfiles = {};
let providerMetadata = {};

export default {
  id: 'llm',
  name: 'LLM',
  icon: '🧠',
  description: 'Language model providers and fallback configuration',
  keys: [
    'LLM_MAX_HISTORY',
    'CONTEXT_LIMIT',
    'LLM_REQUEST_TIMEOUT',
    'LLM_PROVIDERS',
    'LLM_FALLBACK_ORDER',
    'MODEL_GENERATION_PROFILES',
    'FORCE_THINKING',
    'THINKING_PREFILL'
  ],

  render(modal) {
    const providers = modal.settings.LLM_PROVIDERS || {};
    const fallbackOrder = modal.settings.LLM_FALLBACK_ORDER || Object.keys(providers);
    const hasProviders = Object.keys(providers).length > 0;

    // Cache for handlers
    generationProfiles = modal.settings.MODEL_GENERATION_PROFILES || {};

    if (!hasProviders) {
      return `<div class="llm-settings"><p class="no-providers">No providers configured</p></div>`;
    }

    return `
      <div class="llm-settings">
        <div class="llm-providers-section">
          <h4>Providers</h4>
          <p class="section-desc">Configure LLM providers. Drag to set fallback order. Test each to verify connectivity.</p>
          <div class="providers-list" id="providers-list">
            ${this.renderProviderCards(providers, fallbackOrder)}
          </div>
        </div>
        
        <div class="llm-general-section">
          <h4>General Settings</h4>
          ${this.renderGeneralSettings(modal)}
        </div>
      </div>
    `;
  },

  renderProviderCards(providers, fallbackOrder) {
    // Render in fallback order, append any missing providers at end
    const orderedKeys = [...fallbackOrder];
    Object.keys(providers).forEach(key => {
      if (!orderedKeys.includes(key)) orderedKeys.push(key);
    });

    return orderedKeys
      .filter(key => providers[key])
      .map((key, idx) => {
        const config = providers[key];
        const meta = providerMetadata[key] || this.getDefaultMeta(key);
        return renderProviderCard(key, config, meta, idx, generationProfiles);
      })
      .join('');
  },

  getDefaultMeta(key) {
    // Fallback metadata if not yet loaded from API
    const defaults = {
      lmstudio: { display_name: 'LM Studio', is_local: true, model_options: null, required_fields: ['base_url'], default_timeout: 0.3 },
      claude: { display_name: 'Claude', is_local: false, model_options: { 'claude-sonnet-4-5': 'Sonnet 4.5', 'claude-haiku-4-5': 'Haiku 4.5', 'claude-opus-4-5': 'Opus 4.5' }, required_fields: ['api_key', 'model'], api_key_env: 'ANTHROPIC_API_KEY', default_timeout: 10.0 },
      fireworks: { display_name: 'Fireworks', is_local: false, model_options: { 'accounts/fireworks/models/qwen3-235b-a22b-thinking-2507': 'Qwen3 235B Thinking' }, required_fields: ['base_url', 'api_key', 'model'], api_key_env: 'FIREWORKS_API_KEY', default_timeout: 10.0 },
      openai: { display_name: 'OpenAI', is_local: false, model_options: { 'gpt-5.2': 'GPT-5.2 (Flagship)', 'gpt-5.2-pro': 'GPT-5.2 Pro', 'gpt-5.1': 'GPT-5.1', 'gpt-5-mini': 'GPT-5 Mini', 'gpt-4o': 'GPT-4o (Legacy)' }, required_fields: ['base_url', 'api_key', 'model'], api_key_env: 'OPENAI_API_KEY', default_timeout: 10.0 },
      other: { display_name: 'Other (OpenAI Compatible)', is_local: false, model_options: null, required_fields: ['base_url', 'api_key', 'model'], default_timeout: 10.0 }
    };
    return defaults[key] || {};
  },

  refreshModelDropdowns(container) {
    // Update model dropdowns with real metadata after API fetch
    container.querySelectorAll('.model-select').forEach(select => {
      const key = select.dataset.provider;
      const meta = providerMetadata[key];
      if (!meta?.model_options) return;
      
      const currentValue = select.value;
      const modelKeys = Object.keys(meta.model_options);
      const isCustom = currentValue === '__custom__' || (currentValue && !modelKeys.includes(currentValue));
      
      // Rebuild options
      select.innerHTML = modelKeys.map(m =>
        `<option value="${m}" ${currentValue === m ? 'selected' : ''}>${meta.model_options[m]}</option>`
      ).join('') + `<option value="__custom__" ${isCustom ? 'selected' : ''}>Other (custom)</option>`;
      
      // Preserve custom selection
      if (isCustom && currentValue !== '__custom__') {
        select.value = '__custom__';
      }
    });
  },

  renderGeneralSettings(modal) {
    const generalKeys = ['LLM_MAX_HISTORY', 'CONTEXT_LIMIT', 'LLM_REQUEST_TIMEOUT', 'FORCE_THINKING', 'THINKING_PREFILL'];
    return `
      <div class="settings-list">
        ${modal.renderCategorySettings(generalKeys)}
      </div>
    `;
  },

  async attachListeners(modal, container) {
    // Load metadata from API (source of truth)
    try {
      const data = await fetchProviderData();
      providerMetadata = data.metadata || {};
      
      // Refresh model dropdowns with real metadata (fixes race condition)
      this.refreshModelDropdowns(container);
    } catch (e) {
      console.warn('Could not load provider metadata:', e);
    }

    // Refresh API key status hints
    refreshProviderKeyStatus(container);

    // Provider header click - toggle collapse
    container.querySelectorAll('.provider-header').forEach(header => {
      header.addEventListener('click', (e) => {
        if (e.target.closest('.provider-drag-handle')) return;
        const card = header.closest('.provider-card');
        toggleProviderCollapse(card);
      });
    });

    // Provider enable/disable toggles
    container.querySelectorAll('.provider-enabled').forEach(toggle => {
      toggle.addEventListener('change', async (e) => {
        const key = e.target.dataset.provider;
        const enabled = e.target.checked;
        await updateProvider(key, { enabled });
        updateCardEnabledState(e.target.closest('.provider-card'), enabled);
      });
    });

    // Field changes
    container.querySelectorAll('.provider-field').forEach(input => {
      input.addEventListener('change', (e) => this.handleFieldChange(e, container));
    });

    // Generation param changes
    container.querySelectorAll('.gen-param-input').forEach(input => {
      input.addEventListener('change', (e) => this.handleGenParamChange(e));
    });

    // Model select changes
    container.querySelectorAll('.model-select').forEach(select => {
      select.addEventListener('change', (e) => {
        const key = e.target.dataset.provider;
        const card = e.target.closest('.provider-card');
        const model = handleModelSelectChange(card, e.target.value);
        if (model) {
          updateProvider(key, { model });
          loadModelGenParamsIntoCard(card, model, generationProfiles);
        }
      });
    });

    // Custom model input
    container.querySelectorAll('.model-custom').forEach(input => {
      input.addEventListener('change', (e) => {
        const key = e.target.dataset.provider;
        const card = e.target.closest('.provider-card');
        const model = e.target.value.trim();
        if (model) {
          updateProvider(key, { model });
          loadModelGenParamsIntoCard(card, model, generationProfiles);
        }
      });
    });

    // Test buttons
    container.querySelectorAll('.btn-test').forEach(btn => {
      btn.addEventListener('click', (e) => {
        const key = e.target.closest('.btn-test').dataset.provider;
        const card = container.querySelector(`.provider-card[data-provider="${key}"]`);
        const formData = collectProviderFormData(card);
        runTestConnection(key, container, formData);
      });
    });

    // Drag-drop reordering
    initProviderDragDrop(
      container.querySelector('#providers-list'),
      (order) => updateFallbackOrder(order)
    );
  },

  async handleFieldChange(e, container) {
    const key = e.target.dataset.provider;
    const field = e.target.dataset.field;
    let value = e.target.value;

    if (field === 'model_select') return; // Handled separately

    if (field === 'api_key') {
      if (value.trim()) {
        await updateProvider(key, { api_key: value });
        e.target.value = '';
        e.target.placeholder = '••••••••••••••••';
        refreshProviderKeyStatus(container);
      }
      return;
    }

    if (field === 'timeout') {
      value = parseFloat(value) || 5.0;
    }

    // Handle checkbox fields
    if (field === 'use_as_fallback') {
      value = e.target.checked;
    }

    await updateProvider(key, { [field]: value });
  },

  async handleGenParamChange(e) {
    const card = e.target.closest('.provider-card');
    const section = card.querySelector('.generation-params-section');
    const modelName = section?.dataset.model;

    if (!modelName) {
      console.warn('No model selected, cannot save generation params');
      return;
    }

    const params = collectGenParamsFromCard(card);
    generationProfiles = await saveGenerationParams(modelName, params, generationProfiles);
  }
};