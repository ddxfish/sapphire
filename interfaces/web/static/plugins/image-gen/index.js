// index.js - Image Generation settings plugin
// This plugin only injects a settings tab into the Plugins modal
// No sidebar presence, no gear menu entry - settings only

import { registerPluginSettings } from '../plugins-modal/plugin-registry.js';
import pluginsAPI from '../plugins-modal/plugins-api.js';

// Cached defaults from backend (fetched once)
let DEFAULTS = null;

// Minimal fallback if backend fetch fails
const FALLBACK = {
  api_url: 'http://localhost:5153',
  negative_prompt: '',
  static_keywords: '',
  character_descriptions: { 'me': '', 'you': '' },
  defaults: { height: 1024, width: 1024, steps: 23, cfg_scale: 3.0, scheduler: 'dpm++_2m_karras' }
};

async function loadDefaults() {
  if (DEFAULTS) return DEFAULTS;
  
  try {
    const res = await fetch('/api/webui/plugins/image-gen/defaults');
    if (res.ok) {
      DEFAULTS = await res.json();
      console.log('✔ Image-gen defaults loaded from backend');
      return DEFAULTS;
    }
  } catch (e) {
    console.warn('Failed to load image-gen defaults, using fallback:', e);
  }
  
  DEFAULTS = FALLBACK;
  return DEFAULTS;
}

const SCHEDULERS = [
  'dpm++_2m_karras',
  'dpm++_2m',
  'dpm++_sde_karras',
  'dpm++_sde',
  'euler_a',
  'euler',
  'heun',
  'lms'
];

function injectStyles() {
  if (document.getElementById('image-gen-styles')) return;
  
  const style = document.createElement('style');
  style.id = 'image-gen-styles';
  style.textContent = `
    .image-gen-form {
      display: flex;
      flex-direction: column;
      gap: 16px;
    }
    
    .image-gen-group {
      display: flex;
      flex-direction: column;
      gap: 6px;
    }
    
    .image-gen-group label {
      font-size: 13px;
      font-weight: 500;
      color: var(--text);
    }
    
    .image-gen-group input,
    .image-gen-group textarea,
    .image-gen-group select {
      padding: 8px 12px;
      border: 1px solid var(--border);
      border-radius: 6px;
      background: var(--bg-primary);
      color: var(--text);
      font-size: 13px;
    }
    
    .image-gen-group input:focus,
    .image-gen-group textarea:focus,
    .image-gen-group select:focus {
      outline: none;
      border-color: var(--accent-blue);
    }
    
    .image-gen-group input.error,
    .image-gen-group textarea.error {
      border-color: var(--error, #e74c3c);
    }
    
    .image-gen-group textarea {
      resize: vertical;
      min-height: 60px;
    }
    
    .image-gen-row {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 12px;
    }
    
    .image-gen-row-3 {
      display: grid;
      grid-template-columns: 1fr 1fr 1fr;
      gap: 12px;
    }
    
    .image-gen-section {
      border-top: 1px solid var(--border);
      padding-top: 16px;
      margin-top: 8px;
    }
    
    .image-gen-section-title {
      font-size: 14px;
      font-weight: 600;
      color: var(--text);
      margin-bottom: 12px;
    }
    
    .image-gen-hint {
      font-size: 11px;
      color: var(--text-muted);
      margin-top: 4px;
    }
    
    .image-gen-url-row {
      display: flex;
      gap: 8px;
      align-items: flex-start;
    }
    
    .image-gen-url-row input {
      flex: 1;
    }
    
    .image-gen-test-btn {
      padding: 8px 14px;
      border: 1px solid var(--border);
      border-radius: 6px;
      background: var(--bg-tertiary);
      color: var(--text);
      cursor: pointer;
      font-size: 13px;
      white-space: nowrap;
      transition: all 0.15s ease;
    }
    
    .image-gen-test-btn:hover {
      background: var(--bg-hover);
    }
    
    .image-gen-test-btn:disabled {
      opacity: 0.6;
      cursor: not-allowed;
    }
    
    .image-gen-test-btn.success {
      background: var(--success-light, #d4edda);
      border-color: var(--success, #28a745);
      color: var(--success, #28a745);
    }
    
    .image-gen-test-btn.error {
      background: var(--error-light, #f8d7da);
      border-color: var(--error, #dc3545);
      color: var(--error, #dc3545);
    }
    
    .image-gen-preview {
      background: var(--bg-tertiary);
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 12px;
      margin-top: 12px;
    }
    
    .image-gen-preview-title {
      font-size: 12px;
      font-weight: 600;
      color: var(--text-muted);
      margin-bottom: 8px;
      text-transform: uppercase;
      letter-spacing: 0.5px;
    }
    
    .image-gen-preview-input {
      width: 100%;
      padding: 8px 12px;
      border: 1px solid var(--border);
      border-radius: 6px;
      background: var(--bg-primary);
      color: var(--text);
      font-size: 13px;
      margin-bottom: 8px;
    }
    
    .image-gen-preview-output {
      padding: 10px 12px;
      background: var(--bg-primary);
      border-radius: 6px;
      font-size: 13px;
      color: var(--text);
      line-height: 1.5;
      word-break: break-word;
    }
    
    .image-gen-preview-output .replaced {
      background: var(--accent-blue-light, rgba(74, 158, 255, 0.2));
      padding: 1px 4px;
      border-radius: 3px;
    }
    
    .image-gen-error-msg {
      font-size: 11px;
      color: var(--error, #e74c3c);
      margin-top: 4px;
    }
  `;
  document.head.appendChild(style);
}

async function testConnection(container) {
  const btn = container.querySelector('#ig-test-btn');
  const urlInput = container.querySelector('#ig-api-url');
  const url = urlInput.value.trim();
  
  if (!url) {
    btn.textContent = 'No URL';
    btn.className = 'image-gen-test-btn error';
    return;
  }
  
  btn.disabled = true;
  btn.textContent = 'Testing...';
  btn.className = 'image-gen-test-btn';
  
  try {
    const res = await fetch('/api/webui/plugins/image-gen/test-connection', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url })
    });
    const data = await res.json();
    
    if (data.success) {
      btn.textContent = '✓ Connected';
      btn.className = 'image-gen-test-btn success';
    } else {
      btn.textContent = '✗ Failed';
      btn.className = 'image-gen-test-btn error';
      btn.title = data.error || 'Connection failed';
    }
  } catch (e) {
    btn.textContent = '✗ Error';
    btn.className = 'image-gen-test-btn error';
    btn.title = e.message;
  }
  
  btn.disabled = false;
  
  // Reset after 5 seconds
  setTimeout(() => {
    btn.textContent = 'Test';
    btn.className = 'image-gen-test-btn';
    btn.title = '';
  }, 5000);
}

function updatePreview(container) {
  const d = DEFAULTS || FALLBACK;
  const input = container.querySelector('#ig-preview-input');
  const output = container.querySelector('#ig-preview-output');
  const meDesc = container.querySelector('#ig-char-me')?.value || d.character_descriptions['me'];
  const youDesc = container.querySelector('#ig-char-you')?.value || d.character_descriptions['you'];
  
  let text = input.value;
  
  // Replace and highlight
  const meReplaced = text.replace(/\bme\b/i, `<span class="replaced">${meDesc}</span>`);
  const youReplaced = meReplaced.replace(/\byou\b/i, `<span class="replaced">${youDesc}</span>`);
  
  output.innerHTML = youReplaced || '<em>Type a sample prompt above...</em>';
}

function validateForm(container) {
  let valid = true;
  const errors = [];
  
  // URL validation
  const urlInput = container.querySelector('#ig-api-url');
  const url = urlInput.value.trim();
  if (!url) {
    urlInput.classList.add('error');
    errors.push('API URL is required');
    valid = false;
  } else if (!url.startsWith('http://') && !url.startsWith('https://')) {
    urlInput.classList.add('error');
    errors.push('API URL must start with http:// or https://');
    valid = false;
  } else {
    urlInput.classList.remove('error');
  }
  
  // Character descriptions
  const meInput = container.querySelector('#ig-char-me');
  const youInput = container.querySelector('#ig-char-you');
  
  if (!meInput.value.trim()) {
    meInput.classList.add('error');
    errors.push('"me" description is required');
    valid = false;
  } else {
    meInput.classList.remove('error');
  }
  
  if (!youInput.value.trim()) {
    youInput.classList.add('error');
    errors.push('"you" description is required');
    valid = false;
  } else {
    youInput.classList.remove('error');
  }
  
  // Numeric ranges
  const width = parseInt(container.querySelector('#ig-width')?.value);
  const height = parseInt(container.querySelector('#ig-height')?.value);
  const steps = parseInt(container.querySelector('#ig-steps')?.value);
  const cfg = parseFloat(container.querySelector('#ig-cfg')?.value);
  
  if (width < 256 || width > 2048) errors.push('Width must be 256-2048');
  if (height < 256 || height > 2048) errors.push('Height must be 256-2048');
  if (steps < 1 || steps > 100) errors.push('Steps must be 1-100');
  if (cfg < 1 || cfg > 20) errors.push('CFG must be 1-20');
  
  return { valid, errors };
}

function renderForm(container, settings) {
  const d = DEFAULTS || FALLBACK;
  const s = { ...d, ...settings };
  s.defaults = { ...d.defaults, ...(settings.defaults || {}) };
  s.character_descriptions = { ...d.character_descriptions, ...(settings.character_descriptions || {}) };
  
  container.innerHTML = `
    <div class="image-gen-form">
      <div class="image-gen-group">
        <label for="ig-api-url">SDXL API URL</label>
        <div class="image-gen-url-row">
          <input type="text" id="ig-api-url" value="${s.api_url}" placeholder="http://localhost:5153">
          <button type="button" class="image-gen-test-btn" id="ig-test-btn">Test</button>
        </div>
        <div class="image-gen-hint">URL of your SDXL image generation server</div>
      </div>
      
      <div class="image-gen-group">
        <label for="ig-negative">Negative Prompt</label>
        <textarea id="ig-negative" rows="2" placeholder="Things to avoid in generated images">${s.negative_prompt}</textarea>
      </div>
      
      <div class="image-gen-group">
        <label for="ig-keywords">Static Keywords</label>
        <input type="text" id="ig-keywords" value="${s.static_keywords}" placeholder="wide shot">
        <div class="image-gen-hint">Always appended to image prompts</div>
      </div>
      
      <div class="image-gen-section">
        <div class="image-gen-section-title">Character Descriptions</div>
        <div class="image-gen-hint" style="margin-top: -8px; margin-bottom: 12px;">
          The AI writes "me" for itself and "you" for the human. These get replaced with physical descriptions.
        </div>
        
        <div class="image-gen-group">
          <label for="ig-char-me">"me" (the AI)</label>
          <input type="text" id="ig-char-me" value="${s.character_descriptions['me']}">
        </div>
        
        <div class="image-gen-group">
          <label for="ig-char-you">"you" (the human)</label>
          <input type="text" id="ig-char-you" value="${s.character_descriptions['you']}">
        </div>
        
        <div class="image-gen-preview">
          <div class="image-gen-preview-title">Preview Replacement</div>
          <input type="text" class="image-gen-preview-input" id="ig-preview-input" 
                 placeholder="Type a test prompt, e.g.: me and you walking in the park"
                 value="me and you walking in the park">
          <div class="image-gen-preview-output" id="ig-preview-output"></div>
        </div>
      </div>
      
      <div class="image-gen-section">
        <div class="image-gen-section-title">Generation Defaults</div>
        
        <div class="image-gen-row">
          <div class="image-gen-group">
            <label for="ig-width">Width</label>
            <input type="number" id="ig-width" value="${s.defaults.width}" min="256" max="2048" step="64">
          </div>
          <div class="image-gen-group">
            <label for="ig-height">Height</label>
            <input type="number" id="ig-height" value="${s.defaults.height}" min="256" max="2048" step="64">
          </div>
        </div>
        
        <div class="image-gen-row-3">
          <div class="image-gen-group">
            <label for="ig-steps">Steps</label>
            <input type="number" id="ig-steps" value="${s.defaults.steps}" min="1" max="100">
          </div>
          <div class="image-gen-group">
            <label for="ig-cfg">CFG Scale</label>
            <input type="number" id="ig-cfg" value="${s.defaults.cfg_scale}" min="1" max="20" step="0.5">
          </div>
          <div class="image-gen-group">
            <label for="ig-scheduler">Scheduler</label>
            <select id="ig-scheduler">
              ${SCHEDULERS.map(sch => `<option value="${sch}" ${sch === s.defaults.scheduler ? 'selected' : ''}>${sch}</option>`).join('')}
            </select>
          </div>
        </div>
      </div>
    </div>
  `;
  
  // Attach event listeners
  container.querySelector('#ig-test-btn').addEventListener('click', () => testConnection(container));
  
  // Preview updates
  const previewInput = container.querySelector('#ig-preview-input');
  const meInput = container.querySelector('#ig-char-me');
  const youInput = container.querySelector('#ig-char-you');
  
  previewInput.addEventListener('input', () => updatePreview(container));
  meInput.addEventListener('input', () => updatePreview(container));
  youInput.addEventListener('input', () => updatePreview(container));
  
  // Initial preview
  updatePreview(container);
}

function getFormSettings(container) {
  const d = DEFAULTS || FALLBACK;
  
  // Validate first
  const { valid, errors } = validateForm(container);
  
  if (!valid) {
    console.warn('Image-gen validation errors:', errors);
    // Still return settings, but validation errors are shown visually
  }
  
  return {
    api_url: container.querySelector('#ig-api-url')?.value?.trim() || d.api_url,
    negative_prompt: container.querySelector('#ig-negative')?.value || d.negative_prompt,
    static_keywords: container.querySelector('#ig-keywords')?.value || d.static_keywords,
    character_descriptions: {
      'me': container.querySelector('#ig-char-me')?.value || d.character_descriptions['me'],
      'you': container.querySelector('#ig-char-you')?.value || d.character_descriptions['you']
    },
    defaults: {
      width: parseInt(container.querySelector('#ig-width')?.value) || d.defaults.width,
      height: parseInt(container.querySelector('#ig-height')?.value) || d.defaults.height,
      steps: parseInt(container.querySelector('#ig-steps')?.value) || d.defaults.steps,
      cfg_scale: parseFloat(container.querySelector('#ig-cfg')?.value) || d.defaults.cfg_scale,
      scheduler: container.querySelector('#ig-scheduler')?.value || d.defaults.scheduler
    }
  };
}

export default {
  name: 'image-gen',
  
  init(container) {
    injectStyles();
    
    // Register settings tab in Plugins modal
    registerPluginSettings({
      id: 'image-gen',
      name: 'Image Generation',
      icon: '🖼️',
      helpText: 'Configure SDXL image generation. The AI uses "me" and "you" in scene descriptions, which get replaced with physical descriptions.',
      render: renderForm,
      load: async () => {
        // Load defaults first (cached after first call)
        await loadDefaults();
        return pluginsAPI.getSettings('image-gen');
      },
      save: (settings) => pluginsAPI.saveSettings('image-gen', settings),
      getSettings: getFormSettings
    });
    
    console.log('✔ Image-gen settings registered');
  },
  
  destroy() {
    // Nothing to clean up - settings tab is managed by plugins-modal
  }
};