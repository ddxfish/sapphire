// continuity-editor.js - Task editor popup

import * as api from './continuity-api.js';

export default class ContinuityEditor {
  constructor(task = null, onSave = null, onClose = null) {
    this.task = task;  // null = create new, object = edit existing
    this.onSave = onSave;
    this.onClose = onClose;
    this.prompts = [];
    this.abilities = [];
    this.llmProviders = [];
    this.llmMetadata = {};
    this.memoryScopes = [];
    this.el = null;
  }

  async open() {
    // Fetch all dropdown data in parallel
    try {
      const [prompts, abilities, llmData, scopes] = await Promise.all([
        api.fetchPrompts(),
        api.fetchAbilities(),
        api.fetchLLMProviders(),
        api.fetchMemoryScopes()
      ]);
      this.prompts = prompts;
      this.abilities = abilities;
      this.llmProviders = llmData.providers || [];
      this.llmMetadata = llmData.metadata || {};
      this.memoryScopes = scopes;
    } catch (e) {
      console.error('Failed to fetch options:', e);
    }

    this.render();
    document.body.appendChild(this.el);
    
    // Setup provider change handler
    const providerSelect = this.el.querySelector('#task-provider');
    if (providerSelect) {
      providerSelect.addEventListener('change', () => this.updateModelOptions());
      this.updateModelOptions();
    }
  }

  close() {
    if (this.el) {
      this.el.remove();
      this.el = null;
    }
    if (this.onClose) this.onClose();
  }

  render() {
    const isEdit = !!this.task;
    const t = this.task || {};

    // Build provider options - only enabled ones
    const providerOptions = this.llmProviders
      .filter(p => p.enabled)
      .map(p => `<option value="${p.key}" ${t.provider === p.key ? 'selected' : ''}>${p.display_name}${p.is_local ? ' 🏠' : ' ☁️'}</option>`)
      .join('');

    // Build memory scope options
    const memoryScopeOptions = this.memoryScopes
      .map(s => `<option value="${s.name}" ${t.memory_scope === s.name ? 'selected' : ''}>${s.name} (${s.count})</option>`)
      .join('');

    this.el = document.createElement('div');
    this.el.className = 'continuity-editor-overlay';
    this.el.innerHTML = `
      <div class="continuity-editor">
        <div class="continuity-editor-header">
          <h3>${isEdit ? 'Edit Task' : 'New Task'}</h3>
          <button class="continuity-close" data-action="close">&times;</button>
        </div>
        <div class="continuity-editor-body">
          <div class="continuity-field">
            <label for="task-name">Task Name *</label>
            <input type="text" id="task-name" value="${this.escapeHtml(t.name || '')}" placeholder="Morning Greeting" />
          </div>

          <div class="continuity-field">
            <label for="task-schedule">Schedule (Cron) *</label>
            <input type="text" id="task-schedule" value="${t.schedule || '0 9 * * *'}" placeholder="0 9 * * *" />
            <span class="continuity-field-hint">minute hour day month weekday — e.g., "0 9 * * *" = 9:00 AM daily</span>
          </div>

          <div class="continuity-field-row">
            <div class="continuity-field">
              <label for="task-chance">Chance (%)</label>
              <input type="number" id="task-chance" value="${t.chance ?? 100}" min="1" max="100" />
            </div>
            <div class="continuity-field">
              <label for="task-cooldown">Cooldown (min)</label>
              <input type="number" id="task-cooldown" value="${t.cooldown_minutes ?? 1}" min="0" />
            </div>
            <div class="continuity-field">
              <label for="task-iterations">Iterations</label>
              <input type="number" id="task-iterations" value="${t.iterations ?? 1}" min="1" max="10" />
            </div>
          </div>

          <div class="continuity-field">
            <label for="task-initial-message">Initial Message</label>
            <textarea id="task-initial-message" placeholder="What should the AI receive as the first message?">${this.escapeHtml(t.initial_message || '')}</textarea>
          </div>

          <div class="continuity-field">
            <label for="task-chat-target">Chat Name</label>
            <input type="text" id="task-chat-target" value="${this.escapeHtml(t.chat_target || '')}" placeholder="Leave blank for auto-dated name" />
            <span class="continuity-field-hint">Blank = new dated chat each run. Filled = reuse same chat.</span>
          </div>

          <div class="continuity-field-row">
            <div class="continuity-field">
              <label for="task-prompt">Prompt</label>
              <select id="task-prompt">
                <option value="default">default</option>
                ${this.prompts.map(p => `<option value="${p.name}" ${t.prompt === p.name ? 'selected' : ''}>${p.name}</option>`).join('')}
              </select>
            </div>
            <div class="continuity-field">
              <label for="task-toolset">Toolset</label>
              <select id="task-toolset">
                <option value="none" ${t.toolset === 'none' ? 'selected' : ''}>none</option>
                <option value="default" ${t.toolset === 'default' ? 'selected' : ''}>default</option>
                ${this.abilities.map(a => `<option value="${a.name}" ${t.toolset === a.name ? 'selected' : ''}>${a.name}</option>`).join('')}
              </select>
            </div>
          </div>

          <div class="continuity-field-row">
            <div class="continuity-field">
              <label for="task-provider">LLM Provider</label>
              <select id="task-provider">
                <option value="auto" ${t.provider === 'auto' || !t.provider ? 'selected' : ''}>Auto (default)</option>
                ${providerOptions}
              </select>
            </div>
            <div class="continuity-field" id="model-field" style="display: none;">
              <label for="task-model">Model</label>
              <select id="task-model">
                <option value="">Provider default</option>
              </select>
            </div>
            <div class="continuity-field" id="model-custom-field" style="display: none;">
              <label for="task-model-custom">Model</label>
              <input type="text" id="task-model-custom" value="${this.escapeHtml(t.model || '')}" placeholder="Model name" />
            </div>
          </div>

          <div class="continuity-field">
            <label for="task-memory-scope">Memory Scope</label>
            <select id="task-memory-scope">
              <option value="none" ${t.memory_scope === 'none' ? 'selected' : ''}>None (disabled)</option>
              <option value="default" ${!t.memory_scope || t.memory_scope === 'default' ? 'selected' : ''}>default</option>
              ${memoryScopeOptions}
            </select>
          </div>

          <div class="continuity-checkbox">
            <input type="checkbox" id="task-tts" ${t.tts_enabled !== false ? 'checked' : ''} />
            <label for="task-tts">Enable TTS (speak responses)</label>
          </div>

          <div class="continuity-checkbox">
            <input type="checkbox" id="task-background" ${t.background ? 'checked' : ''} />
            <label for="task-background">Background task (no UI switching, ephemeral)</label>
          </div>

          <div class="continuity-checkbox">
            <input type="checkbox" id="task-inject-datetime" ${t.inject_datetime ? 'checked' : ''} />
            <label for="task-inject-datetime">Inject date/time in system prompt</label>
          </div>
        </div>
        <div class="continuity-editor-footer">
          <button class="cancel-btn" data-action="close">Cancel</button>
          <button class="save-btn" data-action="save">Save Task</button>
        </div>
      </div>
    `;

    this.el.addEventListener('click', (e) => this.handleClick(e));
    
    // Close on overlay click
    this.el.addEventListener('click', (e) => {
      if (e.target === this.el) this.close();
    });
  }

  updateModelOptions() {
    const providerSelect = this.el.querySelector('#task-provider');
    const modelField = this.el.querySelector('#model-field');
    const modelCustomField = this.el.querySelector('#model-custom-field');
    const modelSelect = this.el.querySelector('#task-model');
    const modelCustom = this.el.querySelector('#task-model-custom');
    
    const providerKey = providerSelect?.value || 'auto';
    const currentModel = this.task?.model || '';
    
    // Hide both by default
    modelField.style.display = 'none';
    modelCustomField.style.display = 'none';
    
    if (providerKey === 'auto' || providerKey === 'none' || !providerKey) {
      return;
    }
    
    const meta = this.llmMetadata[providerKey];
    const providerConfig = this.llmProviders.find(p => p.key === providerKey);
    
    if (meta?.model_options && Object.keys(meta.model_options).length > 0) {
      // Provider has predefined model options - show dropdown
      const defaultModel = providerConfig?.model || '';
      const defaultLabel = defaultModel ? 
        `Provider default (${meta.model_options[defaultModel] || defaultModel})` : 
        'Provider default';
      
      modelSelect.innerHTML = `<option value="">${defaultLabel}</option>` +
        Object.entries(meta.model_options)
          .map(([k, v]) => `<option value="${k}"${k === currentModel ? ' selected' : ''}>${v}</option>`)
          .join('');
      
      // Add current model if it's custom (not in list)
      if (currentModel && !meta.model_options[currentModel]) {
        modelSelect.innerHTML += `<option value="${currentModel}" selected>${currentModel}</option>`;
      }
      
      modelField.style.display = '';
    } else if (providerKey === 'other' || providerKey === 'lmstudio') {
      // Free-form model input for "other" and lmstudio
      modelCustom.value = currentModel || '';
      modelCustomField.style.display = '';
    }
  }

  handleClick(e) {
    const action = e.target.dataset.action;
    if (action === 'close') this.close();
    if (action === 'save') this.save();
  }

  async save() {
    // Get model value from whichever field is visible
    const modelField = this.el.querySelector('#model-field');
    const modelSelect = this.el.querySelector('#task-model');
    const modelCustom = this.el.querySelector('#task-model-custom');
    
    let modelValue = '';
    if (modelField?.style.display !== 'none' && modelSelect) {
      modelValue = modelSelect.value || '';
    } else if (modelCustom && this.el.querySelector('#model-custom-field')?.style.display !== 'none') {
      modelValue = modelCustom.value.trim() || '';
    }

    const data = {
      name: this.el.querySelector('#task-name').value.trim(),
      schedule: this.el.querySelector('#task-schedule').value.trim(),
      chance: parseInt(this.el.querySelector('#task-chance').value) || 100,
      cooldown_minutes: (v => isNaN(v) ? 1 : v)(parseInt(this.el.querySelector('#task-cooldown').value)),
      iterations: parseInt(this.el.querySelector('#task-iterations').value) || 1,
      initial_message: this.el.querySelector('#task-initial-message').value.trim() || 'Hello.',
      chat_target: this.el.querySelector('#task-chat-target').value.trim(),
      prompt: this.el.querySelector('#task-prompt').value,
      toolset: this.el.querySelector('#task-toolset').value,
      provider: this.el.querySelector('#task-provider').value,
      model: modelValue,
      memory_scope: this.el.querySelector('#task-memory-scope').value,
      tts_enabled: this.el.querySelector('#task-tts').checked,
      background: this.el.querySelector('#task-background').checked,
      inject_datetime: this.el.querySelector('#task-inject-datetime').checked
    };

    if (!data.name) {
      alert('Task name is required');
      return;
    }

    if (!data.schedule) {
      alert('Schedule is required');
      return;
    }

    try {
      if (this.task) {
        await api.updateTask(this.task.id, data);
      } else {
        await api.createTask(data);
      }
      
      if (this.onSave) this.onSave();
      this.close();
    } catch (e) {
      alert('Error: ' + e.message);
    }
  }

  escapeHtml(str) {
    if (!str) return '';
    return str.replace(/&/g, '&amp;')
              .replace(/</g, '&lt;')
              .replace(/>/g, '&gt;')
              .replace(/"/g, '&quot;');
  }
}