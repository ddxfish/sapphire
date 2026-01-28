// shared/llm-providers.js - Reusable LLM provider UI components
// Used by: settings-modal/tabs/llm.js, setup-wizard

import { fetchWithTimeout } from './fetch.js';

// ============================================================================
// API Functions
// ============================================================================

/**
 * Fetch all provider configs and metadata from backend.
 * Backend PROVIDER_METADATA is the source of truth.
 */
export async function fetchProviderData() {
  const data = await fetchWithTimeout('/api/llm/providers');
  return {
    providers: data.providers || [],
    fallbackOrder: data.fallback_order || [],
    metadata: data.metadata || {},
    config: {} // Will be populated from settings if needed
  };
}

/**
 * Update a provider's configuration.
 */
export async function updateProvider(key, updates) {
  const res = await fetch(`/api/llm/providers/${key}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(updates)
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.error || `Failed to update provider: ${res.status}`);
  }
  return await res.json();
}

/**
 * Update the fallback order.
 */
export async function updateFallbackOrder(order) {
  const res = await fetch('/api/llm/fallback-order', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ order })
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.error || `Failed to update fallback order: ${res.status}`);
  }
  return await res.json();
}

/**
 * Test a provider connection.
 * @param {string} key - Provider key
 * @param {object} formData - Override values from form (base_url, api_key, model, timeout)
 */
export async function testProvider(key, formData = {}) {
  const res = await fetch(`/api/llm/test/${key}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(formData)
  });
  return await res.json();
}

/**
 * Save generation params for a model.
 * @param {string} modelName - Model identifier
 * @param {object} params - Generation parameters
 * @param {object} allProfiles - Current MODEL_GENERATION_PROFILES cache
 */
export async function saveGenerationParams(modelName, params, allProfiles) {
  const updated = { ...allProfiles, [modelName]: params };
  const res = await fetch('/api/settings/MODEL_GENERATION_PROFILES', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ value: updated })
  });
  if (!res.ok) {
    throw new Error('Failed to save generation profiles');
  }
  return updated;
}

// ============================================================================
// Render Functions
// ============================================================================

/**
 * Render a single provider card.
 * @param {string} key - Provider key (e.g., 'claude', 'lmstudio')
 * @param {object} config - Provider config from LLM_PROVIDERS
 * @param {object} meta - Provider metadata from PROVIDER_METADATA
 * @param {number} idx - Position in fallback order (0-based)
 * @param {object} genProfiles - MODEL_GENERATION_PROFILES for gen params
 */
export function renderProviderCard(key, config, meta, idx, genProfiles = {}) {
  const displayName = config.display_name || meta.display_name || key;
  const isEnabled = config.enabled || false;
  const isLocal = meta.is_local || false;
  const currentModel = config.model || '';
  const useAsFallback = config.use_as_fallback !== false;

  return `
    <div class="provider-card ${isEnabled ? 'enabled' : 'disabled'}" data-provider="${key}" draggable="true">
      <div class="provider-header" data-provider="${key}">
        <div class="provider-title">
          <span class="provider-drag-handle" title="Drag to reorder">⋮⋮</span>
          <span class="provider-order">${idx + 1}</span>
          <span class="provider-icon">${isLocal ? '🏠' : '☁️'}</span>
          <span class="provider-name">${displayName}</span>
        </div>
        <label class="toggle-switch" onclick="event.stopPropagation()">
          <input type="checkbox" class="provider-enabled" data-provider="${key}" ${isEnabled ? 'checked' : ''}>
          <span class="toggle-slider"></span>
        </label>
      </div>
      
      <div class="provider-fields collapsed" data-provider="${key}">
        <div class="provider-fields-grid">
          ${renderProviderFields(key, config, meta)}
        </div>
        ${renderGenerationParams(key, currentModel, genProfiles)}
        <div class="provider-actions">
          <button class="btn btn-sm btn-test" data-provider="${key}">
            <span class="btn-icon">🔌</span> Test Connection
          </button>
          <span class="test-result" data-provider="${key}"></span>
        </div>
        <div class="auto-fallback-row">
          <label class="checkbox-inline">
            <input type="checkbox" class="provider-field fallback-toggle" data-provider="${key}" data-field="use_as_fallback" 
                   ${useAsFallback ? 'checked' : ''}>
            <span>Include in Auto fallback</span>
          </label>
        </div>
      </div>
    </div>
  `;
}

/**
 * Render provider-specific input fields.
 */
export function renderProviderFields(key, config, meta) {
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

  // API Key
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

  // Model selection
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

  // Timeout
  fields.push(`
    <div class="field-row">
      <label>Timeout (sec)</label>
      <input type="number" class="provider-field" data-provider="${key}" data-field="timeout" 
             value="${config.timeout || meta.default_timeout || 10.0}" step="0.1" min="0.1" max="60">
    </div>
  `);

  // Claude-specific: Extended thinking settings
  if (key === 'claude') {
    const thinkingEnabled = config.thinking_enabled !== false;
    const thinkingBudget = config.thinking_budget || 10000;
    const cacheEnabled = config.cache_enabled || false;
    const cacheTtl = config.cache_ttl || '5m';
    
    fields.push(`
      <div class="field-row claude-thinking-row">
        <label class="checkbox-inline">
          <input type="checkbox" class="provider-field thinking-toggle" data-provider="${key}" data-field="thinking_enabled" 
                 ${thinkingEnabled ? 'checked' : ''}>
          <span>Extended Thinking</span>
        </label>
      </div>
      <div class="field-row thinking-budget-row" data-provider="${key}" ${thinkingEnabled ? '' : 'style="display:none"'}>
        <label>Thinking Budget (tokens)</label>
        <input type="number" class="provider-field thinking-budget" data-provider="${key}" data-field="thinking_budget" 
               value="${thinkingBudget}" step="1000" min="1024" max="32000">
        <small class="field-hint">Higher = deeper reasoning, slower responses</small>
      </div>
      <div class="field-row claude-cache-row">
        <label class="checkbox-inline">
          <input type="checkbox" class="provider-field cache-toggle" data-provider="${key}" data-field="cache_enabled" 
                 ${cacheEnabled ? 'checked' : ''}>
          <span>Prompt Caching</span>
        </label>
        <small class="field-hint">90% cheaper on repeated prompts (cache reads)</small>
      </div>
      <div class="field-row cache-ttl-row" data-provider="${key}" ${cacheEnabled ? '' : 'style="display:none"'}>
        <label>Cache TTL</label>
        <select class="provider-field cache-ttl" data-provider="${key}" data-field="cache_ttl">
          <option value="5m" ${cacheTtl === '5m' ? 'selected' : ''}>5 minutes (cheaper writes)</option>
          <option value="1h" ${cacheTtl === '1h' ? 'selected' : ''}>1 hour (2x write cost)</option>
        </select>
        <small class="field-hint">Cache persists between requests within TTL</small>
      </div>
    `);
  }

  return fields.join('');
}

/**
 * Render generation parameters section.
 */
export function renderGenerationParams(providerKey, modelName, genProfiles = {}) {
  const fallback = genProfiles['__fallback__'] || {};
  const defaults = { 
    temperature: 0.7, 
    top_p: 0.9, 
    max_tokens: 4096, 
    presence_penalty: 0.1, 
    frequency_penalty: 0.1, 
    ...fallback 
  };
  const params = genProfiles[modelName] || defaults;

  const temp = params.temperature ?? defaults.temperature;
  const topP = params.top_p ?? defaults.top_p;
  const maxTokens = params.max_tokens ?? defaults.max_tokens;
  const presencePen = params.presence_penalty ?? defaults.presence_penalty;
  const freqPen = params.frequency_penalty ?? defaults.frequency_penalty;

  return `
    <div class="generation-params-section" data-provider="${providerKey}" data-model="${modelName}">
      <div class="generation-params-label">Generation <span class="gen-model-hint">${modelName || 'no model'}</span></div>
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
          <label>Max Tok</label>
          <input type="number" class="gen-param-input" data-provider="${providerKey}" data-param="max_tokens" 
                 value="${maxTokens}" step="1" min="1" max="128000">
        </div>
        <div class="gen-param">
          <label>Pres</label>
          <input type="number" class="gen-param-input" data-provider="${providerKey}" data-param="presence_penalty" 
                 value="${presencePen}" step="0.05" min="-2" max="2">
        </div>
        <div class="gen-param">
          <label>Freq</label>
          <input type="number" class="gen-param-input" data-provider="${providerKey}" data-param="frequency_penalty" 
                 value="${freqPen}" step="0.05" min="-2" max="2">
        </div>
      </div>
    </div>
  `;
}

// ============================================================================
// UI Helpers
// ============================================================================

/**
 * Get generation params from profile cache.
 */
export function getGenerationParams(modelName, genProfiles) {
  const fallback = genProfiles['__fallback__'] || {};
  const defaults = { 
    temperature: 0.7, 
    top_p: 0.9, 
    max_tokens: 4096, 
    presence_penalty: 0.1, 
    frequency_penalty: 0.1, 
    ...fallback 
  };
  return genProfiles[modelName] || defaults;
}

/**
 * Update generation param inputs in a card when model changes.
 */
export function loadModelGenParamsIntoCard(card, modelName, genProfiles) {
  const params = getGenerationParams(modelName, genProfiles);
  const defaults = getGenerationParams('__fallback__', genProfiles);

  const section = card.querySelector('.generation-params-section');
  if (!section) return;
  
  section.dataset.model = modelName;

  const hint = section.querySelector('.gen-model-hint');
  if (hint) hint.textContent = modelName || 'no model';

  const setVal = (param, val) => {
    const input = card.querySelector(`.gen-param-input[data-param="${param}"]`);
    if (input) input.value = val;
  };

  setVal('temperature', params.temperature ?? defaults.temperature);
  setVal('top_p', params.top_p ?? defaults.top_p);
  setVal('max_tokens', params.max_tokens ?? defaults.max_tokens);
  setVal('presence_penalty', params.presence_penalty ?? defaults.presence_penalty);
  setVal('frequency_penalty', params.frequency_penalty ?? defaults.frequency_penalty);
}

/**
 * Collect generation params from card inputs.
 */
export function collectGenParamsFromCard(card) {
  const params = {};
  card.querySelectorAll('.gen-param-input').forEach(input => {
    const p = input.dataset.param;
    params[p] = p === 'max_tokens' ? parseInt(input.value) : parseFloat(input.value);
  });
  return params;
}

/**
 * Collect form data from a provider card for testing.
 */
export function collectProviderFormData(card) {
  const formData = {};

  card.querySelectorAll('.provider-field').forEach(input => {
    const field = input.dataset.field;
    if (!field || field === 'model_select') return;
    if (field === 'api_key' && !input.value.trim()) return;
    formData[field] = input.value;
  });

  // Handle model select vs custom
  const modelSelect = card.querySelector('.model-select');
  const modelCustom = card.querySelector('.model-custom');
  if (modelSelect && modelSelect.value !== '__custom__') {
    formData.model = modelSelect.value;
  } else if (modelCustom && modelCustom.value.trim()) {
    formData.model = modelCustom.value.trim();
  }

  return formData;
}

// ============================================================================
// Drag and Drop
// ============================================================================

/**
 * Initialize drag-drop reordering on provider cards.
 * @param {HTMLElement} listContainer - The container with .provider-card elements
 * @param {function} onReorder - Callback with new order array
 */
export function initProviderDragDrop(listContainer, onReorder) {
  if (!listContainer) return;

  let dragCard = null;

  const getDragAfterElement = (container, y) => {
    const elements = [...container.querySelectorAll('.provider-card:not(.dragging)')];
    return elements.reduce((closest, child) => {
      const box = child.getBoundingClientRect();
      const offset = y - box.top - box.height / 2;
      if (offset < 0 && offset > closest.offset) {
        return { offset, element: child };
      }
      return closest;
    }, { offset: Number.NEGATIVE_INFINITY }).element;
  };

  listContainer.querySelectorAll('.provider-card').forEach(card => {
    const handle = card.querySelector('.provider-drag-handle');

    handle?.addEventListener('mousedown', () => {
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
      
      // Collect new order and update numbers
      const cards = [...listContainer.querySelectorAll('.provider-card')];
      const order = cards.map(c => c.dataset.provider);
      
      cards.forEach((c, idx) => {
        const orderEl = c.querySelector('.provider-order');
        if (orderEl) orderEl.textContent = idx + 1;
      });

      if (onReorder) onReorder(order);
    });

    card.addEventListener('dragover', (e) => {
      e.preventDefault();
      if (!dragCard || dragCard === card) return;

      const afterElement = getDragAfterElement(listContainer, e.clientY);
      if (afterElement == null) {
        listContainer.appendChild(dragCard);
      } else {
        listContainer.insertBefore(dragCard, afterElement);
      }
    });

    document.addEventListener('mouseup', () => {
      card.setAttribute('draggable', 'true');
    });
  });
}

// ============================================================================
// Provider Status
// ============================================================================

/**
 * Refresh API key status hints for all providers in container.
 * Updates both hint text and input placeholder to show stars when key exists.
 */
export async function refreshProviderKeyStatus(container) {
  try {
    const res = await fetch('/api/llm/providers');
    if (!res.ok) return {};
    
    const data = await res.json();
    const status = {};

    for (const p of data.providers || []) {
      status[p.key] = p;
      
      const hint = container.querySelector(`.key-hint[data-provider="${p.key}"]`);
      const input = container.querySelector(`.api-key-field[data-provider="${p.key}"]`);
      
      if (p.has_config_key) {
        // User has set a key in Sapphire - this takes priority
        if (hint) {
          hint.textContent = '✓ Set in Sapphire';
          hint.className = 'field-hint key-hint key-set';
        }
        if (input) {
          input.placeholder = '••••••••••••••••';
        }
      } else if (p.has_env_key) {
        // Key from environment variable
        if (hint) {
          hint.textContent = `✓ From ${p.env_var} (enter key to override)`;
          hint.className = 'field-hint key-hint key-env';
        }
        if (input) {
          input.placeholder = '•••••••• (from env)';
        }
      } else {
        // No key set
        if (hint) {
          hint.textContent = '';
          hint.className = 'field-hint key-hint';
        }
        if (input) {
          input.placeholder = 'Enter API key';
        }
      }
    }

    return status;
  } catch (e) {
    console.warn('Failed to refresh provider status:', e);
    return {};
  }
}

/**
 * Update card visual state after enable/disable toggle.
 */
export function updateCardEnabledState(card, enabled) {
  if (enabled) {
    card.classList.add('enabled');
    card.classList.remove('disabled');
  } else {
    card.classList.remove('enabled');
    card.classList.add('disabled');
  }
}

/**
 * Toggle collapse state of provider fields.
 */
export function toggleProviderCollapse(card) {
  const fields = card.querySelector('.provider-fields');
  if (fields) {
    fields.classList.toggle('collapsed');
  }
}

/**
 * Handle model select change - show/hide custom input.
 */
export function handleModelSelectChange(card, selectValue) {
  const customRow = card.querySelector('.model-custom-row');
  const customInput = card.querySelector('.model-custom');

  if (selectValue === '__custom__') {
    customRow?.classList.remove('hidden');
    customInput?.focus();
    return null; // No model to save yet
  } else {
    customRow?.classList.add('hidden');
    return selectValue; // Return model to save
  }
}

// ============================================================================
// Test Connection UI
// ============================================================================

/**
 * Run test connection with UI feedback.
 * @param {string} key - Provider key
 * @param {HTMLElement} container - Container with test button and result span
 * @param {object} formData - Form data to test with
 */
export async function runTestConnection(key, container, formData = {}) {
  const btn = container.querySelector(`.btn-test[data-provider="${key}"]`);
  const result = container.querySelector(`.test-result[data-provider="${key}"]`);

  if (!btn || !result) return;

  btn.disabled = true;
  btn.innerHTML = '<span class="btn-icon">⏳</span> Testing...';
  result.textContent = '';
  result.className = 'test-result';

  try {
    const data = await testProvider(key, formData);

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
}