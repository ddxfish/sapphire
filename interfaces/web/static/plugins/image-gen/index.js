// index.js - Image Generation settings plugin
// This plugin only injects a settings tab into the Plugins modal
// No sidebar presence, no gear menu entry - settings only

import { registerPluginSettings } from '../plugins-modal/plugin-registry.js';
import pluginsAPI from '../plugins-modal/plugins-api.js';

// Default settings matching image.py
const DEFAULTS = {
  api_url: 'http://localhost:5153',
  negative_prompt: 'ugly, deformed, noisy, blurry, distorted, grainy, low quality, bad anatomy, jpeg artifacts',
  static_keywords: 'wide shot',
  character_descriptions: {
    'me': 'A sexy short woman with long brown hair and blue eyes',
    'you': 'A tall handsome man with brown hair and brown eyes'
  },
  defaults: {
    height: 1024,
    width: 1024,
    steps: 23,
    cfg_scale: 3.0,
    scheduler: 'dpm++_2m_karras'
  }
};

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
  `;
  document.head.appendChild(style);
}

function renderForm(container, settings) {
  const s = { ...DEFAULTS, ...settings };
  s.defaults = { ...DEFAULTS.defaults, ...(settings.defaults || {}) };
  s.character_descriptions = { ...DEFAULTS.character_descriptions, ...(settings.character_descriptions || {}) };
  
  container.innerHTML = `
    <div class="image-gen-form">
      <div class="image-gen-group">
        <label for="ig-api-url">SDXL API URL</label>
        <input type="text" id="ig-api-url" value="${s.api_url}" placeholder="http://localhost:5153">
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
}

function getFormSettings(container) {
  return {
    api_url: container.querySelector('#ig-api-url')?.value || DEFAULTS.api_url,
    negative_prompt: container.querySelector('#ig-negative')?.value || DEFAULTS.negative_prompt,
    static_keywords: container.querySelector('#ig-keywords')?.value || DEFAULTS.static_keywords,
    character_descriptions: {
      'me': container.querySelector('#ig-char-me')?.value || DEFAULTS.character_descriptions['me'],
      'you': container.querySelector('#ig-char-you')?.value || DEFAULTS.character_descriptions['you']
    },
    defaults: {
      width: parseInt(container.querySelector('#ig-width')?.value) || DEFAULTS.defaults.width,
      height: parseInt(container.querySelector('#ig-height')?.value) || DEFAULTS.defaults.height,
      steps: parseInt(container.querySelector('#ig-steps')?.value) || DEFAULTS.defaults.steps,
      cfg_scale: parseFloat(container.querySelector('#ig-cfg')?.value) || DEFAULTS.defaults.cfg_scale,
      scheduler: container.querySelector('#ig-scheduler')?.value || DEFAULTS.defaults.scheduler
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
      load: () => pluginsAPI.getSettings('image-gen'),
      save: (settings) => pluginsAPI.saveSettings('image-gen', settings),
      getSettings: getFormSettings
    });
    
    console.log('✔ Image-gen settings registered');
  },
  
  destroy() {
    // Nothing to clean up - settings tab is managed by plugins-modal
  }
};