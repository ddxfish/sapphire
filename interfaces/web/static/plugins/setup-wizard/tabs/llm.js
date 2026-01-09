// tabs/llm.js - LLM provider setup (beginner-friendly)

import {
  fetchProviderData,
  updateProvider,
  testProvider,
  refreshProviderKeyStatus
} from '../../../shared/llm-providers.js';

let providerMetadata = {};

// Beginner-friendly provider descriptions
const PROVIDER_INFO = {
  lmstudio: {
    icon: '🏠',
    name: 'LM Studio',
    tagline: 'Local & Private',
    description: 'Run AI models on your own computer. Free, private, no internet needed.',
    difficulty: 'Easy',
    requirements: 'Download LM Studio app, load a model'
  },
  claude: {
    icon: '🧠',
    name: 'Claude',
    tagline: 'By Anthropic',
    description: 'Powerful cloud AI. Great for complex tasks and conversation.',
    difficulty: 'Easy',
    requirements: 'API key from Anthropic'
  },
  openai: {
    icon: '🤖',
    name: 'OpenAI',
    tagline: 'GPT Models',
    description: 'ChatGPT-style AI. Popular and well-supported.',
    difficulty: 'Easy',
    requirements: 'API key from OpenAI'
  },
  fireworks: {
    icon: '🔥',
    name: 'Fireworks',
    tagline: 'Fast Cloud AI',
    description: 'Access to many open-source models with fast inference.',
    difficulty: 'Medium',
    requirements: 'API key from Fireworks.ai'
  },
  other: {
    icon: '⚙️',
    name: 'Other',
    tagline: 'OpenAI Compatible',
    description: 'Connect to any OpenAI-compatible API endpoint.',
    difficulty: 'Advanced',
    requirements: 'Base URL, API key, model name'
  }
};

export default {
  id: 'llm',
  name: 'LLM',
  icon: '🧠',

  async render(settings) {
    // Fetch metadata from backend
    try {
      const data = await fetchProviderData();
      providerMetadata = data.metadata || {};
    } catch (e) {
      console.warn('Could not load provider metadata:', e);
    }

    const providers = settings.LLM_PROVIDERS || {};
    const enabledProviders = Object.entries(providers)
      .filter(([_, cfg]) => cfg.enabled)
      .map(([key]) => key);

    return `
      <div class="setup-tab-header">
        <h3>🧠 Connect to an AI brain</h3>
        <p>Choose where Sapphire gets her intelligence</p>
      </div>

      <div class="help-tip">
        <span class="tip-icon">💡</span>
        <span><strong>New to AI?</strong> Start with LM Studio for a free, private experience. Just download the app and load a model!</span>
      </div>

      <div class="llm-intro">
        <p>Select a provider and configure it. You can enable multiple providers - Sapphire will automatically fall back if one isn't available.</p>
      </div>

      <div class="provider-list">
        ${this.renderProviderCards(providers, enabledProviders)}
      </div>

      <div class="help-tip">
        <span class="tip-icon">ℹ️</span>
        <span>You can configure more providers and advanced settings later in Settings → LLM.</span>
      </div>
    `;
  },

  renderProviderCards(providers, enabledProviders) {
    const order = ['lmstudio', 'claude', 'openai', 'fireworks', 'other'];
    
    return order.map(key => {
      const config = providers[key] || {};
      const info = PROVIDER_INFO[key] || {};
      const meta = providerMetadata[key] || {};
      const isEnabled = enabledProviders.includes(key);
      
      return `
        <div class="provider-simple-card ${isEnabled ? 'selected' : ''}" data-provider="${key}">
          <div class="provider-simple-header">
            <span class="icon">${info.icon}</span>
            <div class="info">
              <h4>${info.name} <span style="opacity:0.6;font-weight:normal">— ${info.tagline}</span></h4>
              <p>${info.description}</p>
            </div>
            <span class="check">✓</span>
          </div>
          
          <div class="provider-config">
            ${this.renderProviderConfig(key, config, meta, info)}
          </div>
        </div>
      `;
    }).join('');
  },

  renderProviderConfig(key, config, meta, info) {
    const fields = [];
    
    // Base URL (for non-Claude providers)
    if (key !== 'claude') {
      const defaultUrl = key === 'lmstudio' ? 'http://127.0.0.1:1234/v1' : 
                        key === 'fireworks' ? 'https://api.fireworks.ai/inference/v1' :
                        key === 'openai' ? 'https://api.openai.com/v1' : '';
      fields.push(`
        <div class="config-field">
          <label>Server URL</label>
          <input type="text" data-provider="${key}" data-field="base_url" 
                 value="${config.base_url || defaultUrl}" 
                 placeholder="${defaultUrl || 'http://your-server/v1'}">
          ${key === 'lmstudio' ? '<div class="hint">Start LM Studio and enable "Start Server" in settings</div>' : ''}
        </div>
      `);
    }

    // API Key (for cloud providers)
    if (key !== 'lmstudio') {
      const envVar = meta.api_key_env || '';
      const hasKey = config.api_key && config.api_key.trim();
      fields.push(`
        <div class="config-field">
          <label>API Key ${envVar ? `<span style="opacity:0.6">(or set ${envVar})</span>` : ''}</label>
          <input type="password" data-provider="${key}" data-field="api_key" 
                 value="" placeholder="${hasKey ? '••••••••••••' : 'Enter your API key'}">
          <div class="hint key-hint" data-provider="${key}">${hasKey ? '✓ Key is set' : ''}</div>
        </div>
      `);
    }

    // Model selection
    if (meta.model_options) {
      const options = Object.entries(meta.model_options)
        .map(([value, label]) => `<option value="${value}" ${config.model === value ? 'selected' : ''}>${label}</option>`)
        .join('');
      fields.push(`
        <div class="config-field">
          <label>Model</label>
          <select data-provider="${key}" data-field="model">
            ${options}
          </select>
        </div>
      `);
    } else if (key === 'other') {
      fields.push(`
        <div class="config-field">
          <label>Model Name</label>
          <input type="text" data-provider="${key}" data-field="model" 
                 value="${config.model || ''}" placeholder="e.g., llama3-70b">
        </div>
      `);
    }

    // Test button
    fields.push(`
      <div class="test-connection-row">
        <button class="btn btn-secondary btn-test" data-provider="${key}">
          🔌 Test Connection
        </button>
        <span class="test-connection-result" data-provider="${key}"></span>
      </div>
    `);

    return fields.join('');
  },

  async attachListeners(container, settings, updateSettings) {
    // Refresh API key status
    refreshProviderKeyStatus(container);

    // Provider card selection
    container.querySelectorAll('.provider-simple-card').forEach(card => {
      card.addEventListener('click', async (e) => {
        // Don't toggle if clicking inside config area
        if (e.target.closest('.provider-config')) return;
        if (e.target.closest('.btn-test')) return;

        const key = card.dataset.provider;
        const isSelected = card.classList.contains('selected');
        
        // Toggle enabled state
        const newState = !isSelected;
        await updateProvider(key, { enabled: newState });
        
        // Update settings cache
        if (!settings.LLM_PROVIDERS) settings.LLM_PROVIDERS = {};
        if (!settings.LLM_PROVIDERS[key]) settings.LLM_PROVIDERS[key] = {};
        settings.LLM_PROVIDERS[key].enabled = newState;

        // Update UI
        if (newState) {
          card.classList.add('selected');
        } else {
          card.classList.remove('selected');
        }
      });
    });

    // Config field changes
    container.querySelectorAll('.provider-config input, .provider-config select').forEach(input => {
      input.addEventListener('click', (e) => e.stopPropagation());
      
      input.addEventListener('change', async (e) => {
        const key = e.target.dataset.provider;
        const field = e.target.dataset.field;
        let value = e.target.value;

        if (field === 'api_key' && !value.trim()) return;

        await updateProvider(key, { [field]: value });

        // Update local settings cache
        if (!settings.LLM_PROVIDERS[key]) settings.LLM_PROVIDERS[key] = {};
        settings.LLM_PROVIDERS[key][field] = value;

        // Clear API key field after save, update hint
        if (field === 'api_key') {
          e.target.value = '';
          e.target.placeholder = '••••••••••••';
          const hint = container.querySelector(`.key-hint[data-provider="${key}"]`);
          if (hint) hint.textContent = '✓ Key saved';
        }
      });
    });

    // Test buttons
    container.querySelectorAll('.btn-test').forEach(btn => {
      btn.addEventListener('click', async (e) => {
        e.stopPropagation();
        const key = btn.dataset.provider;
        const result = container.querySelector(`.test-connection-result[data-provider="${key}"]`);
        const card = btn.closest('.provider-simple-card');

        // Collect form data
        const formData = {};
        card.querySelectorAll('.provider-config input, .provider-config select').forEach(input => {
          const field = input.dataset.field;
          if (field && input.value.trim()) {
            formData[field] = input.value;
          }
        });

        btn.disabled = true;
        btn.textContent = '⏳ Testing...';
        result.textContent = '';

        try {
          const data = await testProvider(key, formData);
          
          if (data.status === 'success') {
            result.textContent = '✓ Connected!';
            result.style.color = 'var(--accent-green, #5cb85c)';
          } else {
            result.textContent = `✗ ${data.error || 'Connection failed'}`;
            result.style.color = 'var(--accent-red, #d9534f)';
          }
        } catch (err) {
          result.textContent = `✗ ${err.message}`;
          result.style.color = 'var(--accent-red, #d9534f)';
        } finally {
          btn.disabled = false;
          btn.textContent = '🔌 Test Connection';
        }
      });
    });
  },

  validate(settings) {
    // Check if at least one provider is enabled
    const providers = settings.LLM_PROVIDERS || {};
    const hasEnabled = Object.values(providers).some(p => p.enabled);
    
    if (!hasEnabled) {
      return {
        valid: false,
        message: 'Please enable at least one AI provider to use Sapphire.'
      };
    }

    return { valid: true };
  }
};