// tabs/llm.js - LLM provider settings with draggable cards for fallback order

let providerStatus = {};
let generationProfiles = {};  // Cache of MODEL_GENERATION_PROFILES

export default {
  id: 'llm',
  name: 'LLM',
  icon: '🧠',
  description: 'Language model providers and fallback configuration',
  keys: [
    'LLM_MAX_HISTORY',
    'LLM_MAX_TOKENS',
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
    
    // Cache generation profiles for use in handlers
    generationProfiles = modal.settings.MODEL_GENERATION_PROFILES || {};

    if (!hasProviders) {
      return `<div class="llm-settings"><p class="no-providers">No providers configured</p></div>`;
    }

    const providerSections = this.renderProviderSections(modal, providers, fallbackOrder);
    const generalSettings = this.renderGeneralSettings(modal);

    return `
      <div class="llm-settings">
        <div class="llm-providers-section">
          <h4>Providers</h4>
          <p class="section-desc">Configure LLM providers. Drag to set fallback order. Test each to verify connectivity.</p>
          <div class="providers-list" id="providers-list">
            ${providerSections}
          </div>
        </div>
        
        <div class="llm-general-section">
          <h4>General Settings</h4>
          ${generalSettings}
        </div>
      </div>
    `;
  },

  renderProviderSections(modal, providers, fallbackOrder) {
    const defaultMeta = {
      lmstudio: { display_name: 'LM Studio', is_local: true, model_options: null, required_fields: ['base_url'], default_timeout: 0.3 },
      claude: { display_name: 'Claude', is_local: false, model_options: {
        'claude-sonnet-4-5': 'Sonnet 4.5',
        'claude-haiku-4-5': 'Haiku 4.5',
        'claude-opus-4-5': 'Opus 4.5'
      }, required_fields: ['api_key', 'model'], api_key_env: 'ANTHROPIC_API_KEY', default_timeout: 10.0 },
      fireworks: { display_name: 'Fireworks', is_local: false, model_options: {
        'accounts/fireworks/models/qwen3-235b-a22b-thinking-2507': 'Qwen3 235B Thinking',
        'accounts/fireworks/models/qwen3-coder-480b-a35b-instruct': 'Qwen3 Coder 480B',
        'accounts/fireworks/models/kimi-k2-thinking': 'Kimi K2 Thinking',
        'accounts/fireworks/models/qwq-32b': 'QwQ 32B',
        'accounts/fireworks/models/gpt-oss-120b': 'GPT-OSS 120B',
        'accounts/fireworks/models/deepseek-v3p2': 'DeepSeek V3.2',
        'accounts/fireworks/models/qwen3-vl-235b-a22b-thinking': 'Qwen3 VL 235B Thinking',
        'accounts/fireworks/models/glm-4p7': 'GLM 4.7',
        'accounts/fireworks/models/minimax-m2p1': 'MiniMax M2.1'
      }, required_fields: ['base_url', 'api_key', 'model'], api_key_env: 'FIREWORKS_API_KEY', default_timeout: 10.0 },
      openai: { display_name: 'OpenAI', is_local: false, model_options: {
        'gpt-4o': 'GPT-4o',
        'gpt-4o-mini': 'GPT-4o Mini',
        'gpt-4-turbo': 'GPT-4 Turbo',
        'o1': 'o1',
        'o1-mini': 'o1 Mini'
      }, required_fields: ['base_url', 'api_key', 'model'], api_key_env: 'OPENAI_API_KEY', default_timeout: 10.0 },
      other: { display_name: 'Other (OpenAI Compatible)', is_local: false, model_options: null, required_fields: ['base_url', 'api_key', 'model'], default_timeout: 10.0 }
    };
    
    // Render in fallback order, append any missing providers at end
    const orderedKeys = [...fallbackOrder];
    Object.keys(providers).forEach(key => {
      if (!orderedKeys.includes(key)) orderedKeys.push(key);
    });
    
    return orderedKeys.filter(key => providers[key]).map((key, idx) => {
      const config = providers[key];
      const meta = defaultMeta[key] || {};
      const displayName = config.display_name || meta.display_name || key;
      const isEnabled = config.enabled || false;
      const isLocal = meta.is_local || false;
      
      // Get current model for this provider
      const currentModel = config.model || '';
      
      return `
        <div class="provider-card ${isEnabled ? 'enabled' : 'disabled'}" data-provider="${key}" draggable="true">
          <div class="provider-header" data-provider="${key}">
            <div class="provider-title">
              <span class="provider-drag-handle" title="Drag to reorder">⋮⋮</span>
              <span class="provider-order">${idx + 1}</span>
              <span class="provider-icon">${isLocal ? '🏠' : '☁️'}</span>
              <span class="provider-name">${displayName}</span>
              <span class="provider-status ${isEnabled ? 'on' : 'off'}">${isEnabled ? '●' : '○'}</span>
              <span class="collapse-indicator">▼</span>
            </div>
            <label class="toggle-switch" onclick="event.stopPropagation()">
              <input type="checkbox" class="provider-enabled" data-provider="${key}" ${isEnabled ? 'checked' : ''}>
              <span class="toggle-slider"></span>
            </label>
          </div>
          
          <div class="provider-fields collapsed" data-provider="${key}">
            <div class="provider-fields-grid">
              ${this.renderProviderFields(key, config, meta)}
            </div>
            ${this.renderGenerationParams(key, currentModel)}
            <div class="provider-actions">
              <button class="btn btn-sm btn-test" data-provider="${key}">
                <span class="btn-icon">🔌</span> Test Connection
              </button>
              <span class="test-result" data-provider="${key}"></span>
            </div>
          </div>
        </div>
      `;
    }).join('');
  },

  renderProviderFields(key, config, meta) {
    const fields = [];
    const required = meta.required_fields || [];
    
    if (required.includes('base_url') || config.base_url !== undefined) {
      fields.push(`
        <div class="field-row">
          <label>Base URL</label>
          <input type="text" class="provider-field" data-provider="${key}" data-field="base_url" 
                 value="${config.base_url || ''}" placeholder="http://127.0.0.1:1234/v1">
        </div>
      `);
    }
    
    if (required.includes('api_key') || (!meta.is_local && key !== 'lmstudio')) {
      const envVar = config.api_key_env || meta.api_key_env || '';
      const hasConfigKey = config.api_key && config.api_key.trim();
      const displayValue = hasConfigKey ? '••••••••••••••••' : '';
      
      fields.push(`
        <div class="field-row">
          <label>API Key ${envVar ? `<span class="env-hint">(or ${envVar})</span>` : ''}</label>
          <input type="password" class="provider-field api-key-field" data-provider="${key}" data-field="api_key" 
                 value="" placeholder="${displayValue || 'Enter API key'}">
          <small class="field-hint key-hint" data-provider="${key}"></small>
        </div>
      `);
    }
    
    if (meta.model_options && typeof meta.model_options === 'object') {
      const currentModel = config.model || '';
      const modelKeys = Object.keys(meta.model_options);
      const isCustom = currentModel && !modelKeys.includes(currentModel);
      
      fields.push(`
        <div class="field-row">
          <label>Model</label>
          <select class="provider-field model-select" data-provider="${key}" data-field="model_select">
            ${modelKeys.map(m => 
              `<option value="${m}" ${currentModel === m ? 'selected' : ''}>${meta.model_options[m]}</option>`
            ).join('')}
            <option value="__custom__" ${isCustom ? 'selected' : ''}>Other (custom)</option>
          </select>
        </div>
        <div class="field-row model-custom-row ${isCustom ? '' : 'hidden'}" data-provider="${key}">
          <label>Custom Model</label>
          <input type="text" class="provider-field model-custom" 
                 data-provider="${key}" data-field="model" 
                 value="${isCustom ? currentModel : ''}" placeholder="Custom model name">
        </div>
      `);
    } else if (required.includes('model')) {
      fields.push(`
        <div class="field-row">
          <label>Model</label>
          <input type="text" class="provider-field" data-provider="${key}" data-field="model" 
                 value="${config.model || ''}" placeholder="Model name">
        </div>
      `);
    }
    
    fields.push(`
      <div class="field-row">
        <label>Timeout (sec)</label>
        <input type="number" class="provider-field" data-provider="${key}" data-field="timeout" 
               value="${config.timeout || meta.default_timeout || 10.0}" step="0.1" min="0.1" max="60">
      </div>
    `);
    
    return fields.join('');
  },

  renderGenerationParams(providerKey, modelName) {
    // Look up this model's profile, fall back to __fallback__ or defaults
    const fallback = generationProfiles['__fallback__'] || {};
    const defaults = { temperature: 0.7, top_p: 0.9, max_tokens: 4096, presence_penalty: 0.1, frequency_penalty: 0.1, ...fallback };
    const params = generationProfiles[modelName] || defaults;
    
    const temp = params.temperature ?? defaults.temperature;
    const topP = params.top_p ?? defaults.top_p;
    const maxTokens = params.max_tokens ?? defaults.max_tokens;
    const presencePen = params.presence_penalty ?? defaults.presence_penalty;
    const freqPen = params.frequency_penalty ?? defaults.frequency_penalty;
    
    return `
      <div class="generation-params-section" data-provider="${providerKey}" data-model="${modelName}">
        <div class="generation-params-label">Generation Defaults <span class="gen-model-hint">(${modelName || 'no model selected'})</span></div>
        <div class="generation-params-grid">
          <div class="gen-param">
            <label>Temp</label>
            <input type="number" class="gen-param-input" data-provider="${providerKey}" data-param="temperature" 
                   value="${temp}" step="0.05" min="0" max="2">
          </div>
          <div class="gen-param">
            <label>Top P</label>
            <input type="number" class="gen-param-input" data-provider="${providerKey}" data-param="top_p" 
                   value="${topP}" step="0.05" min="0" max="1">
          </div>
          <div class="gen-param">
            <label>n_tokens</label>
            <input type="number" class="gen-param-input" data-provider="${providerKey}" data-param="max_tokens" 
                   value="${maxTokens}" step="1" min="1" max="128000">
          </div>
          <div class="gen-param">
            <label>Pres Pen</label>
            <input type="number" class="gen-param-input" data-provider="${providerKey}" data-param="presence_penalty" 
                   value="${presencePen}" step="0.05" min="-2" max="2">
          </div>
          <div class="gen-param">
            <label>Freq Pen</label>
            <input type="number" class="gen-param-input" data-provider="${providerKey}" data-param="frequency_penalty" 
                   value="${freqPen}" step="0.05" min="-2" max="2">
          </div>
        </div>
      </div>
    `;
  },

  renderGeneralSettings(modal) {
    const generalKeys = ['LLM_MAX_HISTORY', 'LLM_MAX_TOKENS', 'LLM_REQUEST_TIMEOUT', 'FORCE_THINKING', 'THINKING_PREFILL'];
    return `
      <div class="settings-list">
        ${modal.renderCategorySettings(generalKeys)}
      </div>
    `;
  },

  attachListeners(modal, container) {
    this.refreshProviderStatus(container);
    
    // Provider header click - toggle collapse
    container.querySelectorAll('.provider-header').forEach(header => {
      header.addEventListener('click', (e) => {
        if (e.target.closest('.provider-drag-handle')) return;
        const card = header.closest('.provider-card');
        const fields = card.querySelector('.provider-fields');
        const indicator = header.querySelector('.collapse-indicator');
        
        fields.classList.toggle('collapsed');
        indicator.textContent = fields.classList.contains('collapsed') ? '▼' : '▲';
      });
    });
    
    // Provider enable/disable toggles
    container.querySelectorAll('.provider-enabled').forEach(toggle => {
      toggle.addEventListener('change', async (e) => {
        const key = e.target.dataset.provider;
        const enabled = e.target.checked;
        
        await this.updateProvider(key, { enabled });
        
        const card = e.target.closest('.provider-card');
        const status = card.querySelector('.provider-status');
        if (enabled) {
          card.classList.add('enabled');
          card.classList.remove('disabled');
          status.classList.add('on');
          status.classList.remove('off');
          status.textContent = '●';
        } else {
          card.classList.remove('enabled');
          card.classList.add('disabled');
          status.classList.remove('on');
          status.classList.add('off');
          status.textContent = '○';
        }
      });
    });
    
    // Field changes
    container.querySelectorAll('.provider-field').forEach(input => {
      input.addEventListener('change', (e) => this.handleFieldChange(e, container));
    });
    
    // Generation param changes - save to MODEL_GENERATION_PROFILES
    container.querySelectorAll('.gen-param-input').forEach(input => {
      input.addEventListener('change', (e) => this.handleGenParamChange(e, container));
    });
    
    // Model select -> load that model's generation params
    container.querySelectorAll('.model-select').forEach(select => {
      select.addEventListener('change', (e) => {
        const key = e.target.dataset.provider;
        const card = e.target.closest('.provider-card');
        const customRow = card.querySelector('.model-custom-row');
        const customInput = card.querySelector('.model-custom');
        
        if (e.target.value === '__custom__') {
          customRow?.classList.remove('hidden');
          customInput?.focus();
        } else {
          customRow?.classList.add('hidden');
          this.updateProvider(key, { model: e.target.value });
          this.loadModelGenParams(e.target.value, card);
        }
      });
    });
    
    // Custom model input - load params when user finishes typing
    container.querySelectorAll('.model-custom').forEach(input => {
      input.addEventListener('change', (e) => {
        const card = e.target.closest('.provider-card');
        if (e.target.value.trim()) {
          this.loadModelGenParams(e.target.value.trim(), card);
        }
      });
    });
    
    // Test buttons
    container.querySelectorAll('.btn-test').forEach(btn => {
      btn.addEventListener('click', (e) => this.testProvider(e.target.closest('.btn-test').dataset.provider, container));
    });
    
    // Provider card drag-drop for reordering
    this.initCardDragDrop(container);
  },

  loadModelGenParams(modelName, card) {
    // Load from cached profiles
    const fallback = generationProfiles['__fallback__'] || {};
    const defaults = { temperature: 0.7, top_p: 0.9, max_tokens: 4096, presence_penalty: 0.1, frequency_penalty: 0.1, ...fallback };
    const params = generationProfiles[modelName] || defaults;
    
    // Update UI inputs
    const section = card.querySelector('.generation-params-section');
    section.dataset.model = modelName;
    
    // Update hint
    const hint = section.querySelector('.gen-model-hint');
    if (hint) hint.textContent = `(${modelName || 'no model selected'})`;
    
    card.querySelector('.gen-param-input[data-param="temperature"]').value = params.temperature ?? defaults.temperature;
    card.querySelector('.gen-param-input[data-param="top_p"]').value = params.top_p ?? defaults.top_p;
    card.querySelector('.gen-param-input[data-param="max_tokens"]').value = params.max_tokens ?? defaults.max_tokens;
    card.querySelector('.gen-param-input[data-param="presence_penalty"]').value = params.presence_penalty ?? defaults.presence_penalty;
    card.querySelector('.gen-param-input[data-param="frequency_penalty"]').value = params.frequency_penalty ?? defaults.frequency_penalty;
  },

  async handleGenParamChange(e, container) {
    const card = e.target.closest('.provider-card');
    const section = card.querySelector('.generation-params-section');
    const modelName = section.dataset.model;
    
    if (!modelName) {
      console.warn('No model selected, cannot save generation params');
      return;
    }
    
    // Gather all params from UI
    const genParams = {};
    card.querySelectorAll('.gen-param-input').forEach(input => {
      const p = input.dataset.param;
      genParams[p] = p === 'max_tokens' ? parseInt(input.value) : parseFloat(input.value);
    });
    
    // Update local cache
    generationProfiles[modelName] = genParams;
    
    // Save to MODEL_GENERATION_PROFILES via API
    try {
      const res = await fetch('/api/settings/MODEL_GENERATION_PROFILES', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ value: generationProfiles })
      });
      if (!res.ok) {
        console.error('Failed to save generation profiles:', await res.json());
      }
    } catch (e) {
      console.error('Error saving generation profiles:', e);
    }
  },

  async refreshProviderStatus(container) {
    try {
      const res = await fetch('/api/llm/providers');
      if (!res.ok) return;
      const data = await res.json();
      
      for (const p of data.providers || []) {
        providerStatus[p.key] = p;
        const hint = container.querySelector(`.key-hint[data-provider="${p.key}"]`);
        if (hint) {
          if (p.has_config_key) {
            hint.textContent = '✓ Set in Sapphire';
            hint.className = 'field-hint key-hint key-set';
          } else if (p.has_env_key) {
            hint.textContent = `✓ From env var ${p.env_var}`;
            hint.className = 'field-hint key-hint key-env';
          } else {
            hint.textContent = '';
            hint.className = 'field-hint key-hint';
          }
        }
      }
    } catch (e) {
      console.warn('Failed to refresh provider status:', e);
    }
  },

  async handleFieldChange(e, container) {
    const key = e.target.dataset.provider;
    const field = e.target.dataset.field;
    let value = e.target.value;
    
    if (field === 'model_select') {
      if (value !== '__custom__') {
        await this.updateProvider(key, { model: value });
      }
      return;
    }
    
    if (field === 'model') {
      await this.updateProvider(key, { model: value });
      return;
    }
    
    if (field === 'api_key') {
      if (value.trim()) {
        await this.updateProvider(key, { api_key: value });
        e.target.value = '';
        e.target.placeholder = '••••••••••••••••';
        this.refreshProviderStatus(container);
      }
      return;
    }
    
    if (field === 'timeout') {
      value = parseFloat(value) || 5.0;
    }
    
    await this.updateProvider(key, { [field]: value });
  },

  async updateProvider(key, updates) {
    try {
      const res = await fetch(`/api/llm/providers/${key}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(updates)
      });
      
      if (!res.ok) {
        console.error('Failed to update provider:', await res.json());
      }
    } catch (e) {
      console.error('Error updating provider:', e);
    }
  },

  async testProvider(key, container) {
    const btn = container.querySelector(`.btn-test[data-provider="${key}"]`);
    const result = container.querySelector(`.test-result[data-provider="${key}"]`);
    
    btn.disabled = true;
    btn.innerHTML = '<span class="btn-icon">⏳</span> Testing...';
    result.textContent = '';
    result.className = 'test-result';
    
    try {
      const card = container.querySelector(`.provider-card[data-provider="${key}"]`);
      const formData = {};
      
      card.querySelectorAll('.provider-field').forEach(input => {
        const field = input.dataset.field;
        if (!field || field === 'model_select') return;
        if (field === 'api_key' && !input.value.trim()) return;
        formData[field] = input.value;
      });
      
      const modelSelect = card.querySelector('.model-select');
      const modelCustom = card.querySelector('.model-custom');
      if (modelSelect && modelSelect.value !== '__custom__') {
        formData.model = modelSelect.value;
      } else if (modelCustom && modelCustom.value.trim()) {
        formData.model = modelCustom.value.trim();
      }
      
      const res = await fetch(`/api/llm/test/${key}`, { 
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(formData)
      });
      const data = await res.json();
      
      if (data.status === 'success') {
        const response = data.response?.substring(0, 60) || 'Connected!';
        result.textContent = `✓ ${response}`;
        result.classList.add('success');
      } else {
        const errorMsg = data.error || 'Unknown error';
        const details = data.details || '';
        result.textContent = `✗ ${errorMsg}${details ? ': ' + details : ''}`;
        result.classList.add('error');
      }
    } catch (e) {
      result.textContent = `✗ Network error: ${e.message}`;
      result.classList.add('error');
    } finally {
      btn.disabled = false;
      btn.innerHTML = '<span class="btn-icon">🔌</span> Test Connection';
    }
  },

  initCardDragDrop(container) {
    const list = container.querySelector('#providers-list');
    if (!list) return;
    
    let dragCard = null;
    
    list.querySelectorAll('.provider-card').forEach(card => {
      const handle = card.querySelector('.provider-drag-handle');
      
      handle.addEventListener('mousedown', () => {
        card.setAttribute('draggable', 'true');
      });
      
      card.addEventListener('dragstart', (e) => {
        if (!e.target.closest('.provider-drag-handle') && e.target !== card) {
          e.preventDefault();
          return;
        }
        dragCard = card;
        card.classList.add('dragging');
        e.dataTransfer.effectAllowed = 'move';
      });
      
      card.addEventListener('dragend', () => {
        card.classList.remove('dragging');
        this.saveProviderOrder(container);
      });
      
      card.addEventListener('dragover', (e) => {
        e.preventDefault();
        if (!dragCard || dragCard === card) return;
        
        const afterElement = this.getDragAfterElement(list, e.clientY);
        if (afterElement == null) {
          list.appendChild(dragCard);
        } else {
          list.insertBefore(dragCard, afterElement);
        }
      });
      
      document.addEventListener('mouseup', () => {
        card.setAttribute('draggable', 'true');
      });
    });
  },

  getDragAfterElement(container, y) {
    const elements = [...container.querySelectorAll('.provider-card:not(.dragging)')];
    return elements.reduce((closest, child) => {
      const box = child.getBoundingClientRect();
      const offset = y - box.top - box.height / 2;
      if (offset < 0 && offset > closest.offset) {
        return { offset, element: child };
      }
      return closest;
    }, { offset: Number.NEGATIVE_INFINITY }).element;
  },

  async saveProviderOrder(container) {
    const list = container.querySelector('#providers-list');
    const cards = [...list.querySelectorAll('.provider-card')];
    const order = cards.map(card => card.dataset.provider);
    
    cards.forEach((card, idx) => {
      const orderEl = card.querySelector('.provider-order');
      if (orderEl) orderEl.textContent = idx + 1;
    });
    
    try {
      await fetch('/api/llm/fallback-order', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ order })
      });
    } catch (e) {
      console.error('Failed to save provider order:', e);
    }
  }
};