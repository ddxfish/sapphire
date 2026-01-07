// tabs/llm.js - LLM provider settings with test buttons and model selection

let providerStatus = {};  // Track has_env_key, has_config_key per provider

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
    'GENERATION_DEFAULTS',
    'FORCE_THINKING',
    'THINKING_PREFILL'
  ],

  render(modal) {
    const providers = modal.settings.LLM_PROVIDERS || {};
    const hasProviders = Object.keys(providers).length > 0;

    if (!hasProviders) {
      return `<div class="llm-settings"><p class="no-providers">No providers configured</p></div>`;
    }

    const providerSections = this.renderProviderSections(modal, providers);
    const fallbackSection = this.renderFallbackOrder(modal);
    const generalSettings = this.renderGeneralSettings(modal);

    return `
      <div class="llm-settings">
        <div class="llm-providers-section">
          <h4>Providers</h4>
          <p class="section-desc">Configure LLM providers. Test each to verify connectivity.</p>
          ${providerSections}
        </div>
        
        <div class="llm-fallback-section">
          <h4>Fallback Order</h4>
          <p class="section-desc">Order in which providers are tried when set to "Auto".</p>
          ${fallbackSection}
        </div>
        
        <div class="llm-general-section">
          <h4>General Settings</h4>
          ${generalSettings}
        </div>
      </div>
    `;
  },

  renderProviderSections(modal, providers) {
    const defaultMeta = {
      lmstudio: { display_name: 'LM Studio', is_local: true, model_options: null, required_fields: ['base_url'], default_timeout: 0.3 },
      claude: { display_name: 'Claude', is_local: false, model_options: {
        'claude-sonnet-4-5': 'Sonnet 4.5',
        'claude-haiku-4-5': 'Haiku 4.5',
        'claude-opus-4-5': 'Opus 4.5'
      }, required_fields: ['api_key', 'model'], api_key_env: 'ANTHROPIC_API_KEY', default_timeout: 10.0 },
      fireworks: { display_name: 'Fireworks', is_local: false, model_options: {
        'accounts/fireworks/models/glm-4p7': 'GLM 4.7',
        'accounts/fireworks/models/minimax-m2p1': 'MiniMax M2.1',
        'accounts/fireworks/models/deepseek-v3p2': 'DeepSeek V3.2',
        'accounts/fireworks/models/qwen3-vl-235b-a22b-thinking': 'Qwen3 VL 235B Thinking'
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
    
    return Object.entries(providers).map(([key, config]) => {
      const meta = defaultMeta[key] || {};
      const displayName = config.display_name || meta.display_name || key;
      const isEnabled = config.enabled || false;
      const isLocal = meta.is_local || false;
      
      return `
        <div class="provider-card ${isEnabled ? 'enabled' : 'disabled'}" data-provider="${key}">
          <div class="provider-header" data-provider="${key}">
            <div class="provider-title">
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
    
    // Base URL
    if (required.includes('base_url') || config.base_url !== undefined) {
      fields.push(`
        <div class="field-row">
          <label>Base URL</label>
          <input type="text" class="provider-field" data-provider="${key}" data-field="base_url" 
                 value="${config.base_url || ''}" placeholder="http://127.0.0.1:1234/v1">
        </div>
      `);
    }
    
    // API Key - show status indicator
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
    
    // Model selection - handle dict (friendly names) vs null (free text)
    if (meta.model_options && typeof meta.model_options === 'object') {
      // Dict with {value: friendly_name}
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
        <div class="field-row model-custom-row ${isCustom ? '' : 'hidden'}">
          <label>Custom Model</label>
          <input type="text" class="provider-field model-custom" 
                 data-provider="${key}" data-field="model" 
                 value="${isCustom ? currentModel : ''}" placeholder="Custom model name">
        </div>
      `);
    } else if (required.includes('model')) {
      // Free-form model entry (for "other" provider or providers without preset options)
      fields.push(`
        <div class="field-row">
          <label>Model</label>
          <input type="text" class="provider-field" data-provider="${key}" data-field="model" 
                 value="${config.model || ''}" placeholder="Model name">
        </div>
      `);
    }
    
    // Timeout
    fields.push(`
      <div class="field-row">
        <label>Timeout (sec)</label>
        <input type="number" class="provider-field" data-provider="${key}" data-field="timeout" 
               value="${config.timeout || meta.default_timeout || 10.0}" step="0.1" min="0.1" max="60">
      </div>
    `);
    
    return fields.join('');
  },

  renderFallbackOrder(modal) {
    const order = modal.settings.LLM_FALLBACK_ORDER || [];
    const providers = modal.settings.LLM_PROVIDERS || {};
    
    return `
      <div class="fallback-list" id="fallback-order-list">
        ${order.map((key, idx) => {
          const config = providers[key] || {};
          const displayName = config.display_name || key;
          const isEnabled = config.enabled || false;
          return `
            <div class="fallback-item ${isEnabled ? '' : 'disabled'}" data-key="${key}">
              <span class="drag-handle">⋮⋮</span>
              <span class="fallback-num">${idx + 1}</span>
              <span class="fallback-name">${displayName}</span>
              <span class="fallback-status">${isEnabled ? '✓' : '✗'}</span>
            </div>
          `;
        }).join('')}
      </div>
      <p class="fallback-hint">Drag to reorder. Only enabled providers will be used.</p>
    `;
  },

  renderGeneralSettings(modal) {
    const generalKeys = ['LLM_MAX_HISTORY', 'LLM_MAX_TOKENS', 'LLM_REQUEST_TIMEOUT', 'GENERATION_DEFAULTS', 'FORCE_THINKING', 'THINKING_PREFILL'];
    return `
      <div class="settings-list">
        ${modal.renderCategorySettings(generalKeys)}
      </div>
    `;
  },

  attachListeners(modal, container) {
    // Fetch provider status (env keys, etc) and update UI
    this.refreshProviderStatus(container);
    
    // Provider header click - toggle collapse
    container.querySelectorAll('.provider-header').forEach(header => {
      header.addEventListener('click', (e) => {
        const key = header.dataset.provider;
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
        
        this.refreshFallbackList(modal, container);
      });
    });
    
    // Field changes
    container.querySelectorAll('.provider-field').forEach(input => {
      input.addEventListener('change', (e) => this.handleFieldChange(e, container));
    });
    
    // Model select -> custom toggle
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
        }
      });
    });
    
    // Test buttons
    container.querySelectorAll('.btn-test').forEach(btn => {
      btn.addEventListener('click', (e) => this.testProvider(e.target.closest('.btn-test').dataset.provider, container));
    });
    
    // Fallback order drag-drop
    this.initDragDrop(container, modal);
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
        // Update UI to show key is set
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
      // LLM changes auto-save silently - no toast needed for every field change
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
      // Collect current form values for this provider
      const card = container.querySelector(`.provider-card[data-provider="${key}"]`);
      const formData = {};
      
      card.querySelectorAll('.provider-field').forEach(input => {
        const field = input.dataset.field;
        if (!field || field === 'model_select') return;
        if (field === 'api_key' && !input.value.trim()) return;
        formData[field] = input.value;
      });
      
      // Get model from dropdown or custom field
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
        // Show error + details for rich feedback
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

  initDragDrop(container, modal) {
    const list = container.querySelector('#fallback-order-list');
    if (!list) return;
    
    let dragItem = null;
    
    list.querySelectorAll('.fallback-item').forEach(item => {
      item.draggable = true;
      
      item.addEventListener('dragstart', () => {
        dragItem = item;
        item.classList.add('dragging');
      });
      
      item.addEventListener('dragend', () => {
        item.classList.remove('dragging');
        this.saveFallbackOrder(container);
      });
      
      item.addEventListener('dragover', (e) => {
        e.preventDefault();
        const afterElement = this.getDragAfterElement(list, e.clientY);
        if (afterElement == null) {
          list.appendChild(dragItem);
        } else {
          list.insertBefore(dragItem, afterElement);
        }
      });
    });
  },

  getDragAfterElement(container, y) {
    const elements = [...container.querySelectorAll('.fallback-item:not(.dragging)')];
    return elements.reduce((closest, child) => {
      const box = child.getBoundingClientRect();
      const offset = y - box.top - box.height / 2;
      if (offset < 0 && offset > closest.offset) {
        return { offset, element: child };
      }
      return closest;
    }, { offset: Number.NEGATIVE_INFINITY }).element;
  },

  async saveFallbackOrder(container) {
    const list = container.querySelector('#fallback-order-list');
    const order = [...list.querySelectorAll('.fallback-item')].map(item => item.dataset.key);
    
    try {
      await fetch('/api/llm/fallback-order', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ order })
      });
      
      list.querySelectorAll('.fallback-item').forEach((item, idx) => {
        item.querySelector('.fallback-num').textContent = idx + 1;
      });
    } catch (e) {
      console.error('Failed to save fallback order:', e);
    }
  },

  refreshFallbackList(modal, container) {
    const list = container.querySelector('#fallback-order-list');
    if (!list) return;
    
    list.querySelectorAll('.fallback-item').forEach(item => {
      const key = item.dataset.key;
      const toggle = container.querySelector(`.provider-enabled[data-provider="${key}"]`);
      const isEnabled = toggle ? toggle.checked : false;
      
      if (isEnabled) {
        item.classList.remove('disabled');
        item.querySelector('.fallback-status').textContent = '✓';
      } else {
        item.classList.add('disabled');
        item.querySelector('.fallback-status').textContent = '✗';
      }
    });
  }
};