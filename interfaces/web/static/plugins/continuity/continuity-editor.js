// continuity-editor.js - Task editor popup

import * as api from './continuity-api.js';

export default class ContinuityEditor {
  constructor(task = null, onSave = null, onClose = null) {
    this.task = task;  // null = create new, object = edit existing
    this.onSave = onSave;
    this.onClose = onClose;
    this.prompts = [];
    this.abilities = [];
    this.el = null;
  }

  async open() {
    // Fetch prompts and abilities for dropdowns
    try {
      [this.prompts, this.abilities] = await Promise.all([
        api.fetchPrompts(),
        api.fetchAbilities()
      ]);
    } catch (e) {
      console.error('Failed to fetch options:', e);
    }

    this.render();
    document.body.appendChild(this.el);
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
              <input type="number" id="task-cooldown" value="${t.cooldown_minutes ?? 60}" min="0" />
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
                <option value="auto" ${t.provider === 'auto' ? 'selected' : ''}>auto (default)</option>
                <option value="lmstudio" ${t.provider === 'lmstudio' ? 'selected' : ''}>LM Studio</option>
                <option value="claude" ${t.provider === 'claude' ? 'selected' : ''}>Claude</option>
                <option value="openai" ${t.provider === 'openai' ? 'selected' : ''}>OpenAI</option>
              </select>
            </div>
            <div class="continuity-field">
              <label for="task-model">Model (optional)</label>
              <input type="text" id="task-model" value="${t.model || ''}" placeholder="Leave blank for default" />
            </div>
          </div>

          <div class="continuity-field-row">
            <div class="continuity-field">
              <label for="task-chat-mode">Chat Mode</label>
              <select id="task-chat-mode">
                <option value="dated" ${t.chat_mode === 'dated' ? 'selected' : ''}>Dated (new chat each run)</option>
                <option value="single" ${t.chat_mode === 'single' ? 'selected' : ''}>Single (one chat per task)</option>
                <option value="fixed" ${t.chat_mode === 'fixed' ? 'selected' : ''}>Fixed (specify target)</option>
              </select>
            </div>
            <div class="continuity-field">
              <label for="task-chat-target">Chat Target</label>
              <input type="text" id="task-chat-target" value="${t.chat_target || ''}" placeholder="For fixed mode only" />
            </div>
          </div>

          <div class="continuity-checkbox">
            <input type="checkbox" id="task-tts" ${t.tts_enabled !== false ? 'checked' : ''} />
            <label for="task-tts">Enable TTS (speak responses)</label>
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

  handleClick(e) {
    const action = e.target.dataset.action;
    if (action === 'close') this.close();
    if (action === 'save') this.save();
  }

  async save() {
    const data = {
      name: this.el.querySelector('#task-name').value.trim(),
      schedule: this.el.querySelector('#task-schedule').value.trim(),
      chance: parseInt(this.el.querySelector('#task-chance').value) || 100,
      cooldown_minutes: parseInt(this.el.querySelector('#task-cooldown').value) || 60,
      iterations: parseInt(this.el.querySelector('#task-iterations').value) || 1,
      initial_message: this.el.querySelector('#task-initial-message').value.trim() || 'Hello.',
      prompt: this.el.querySelector('#task-prompt').value,
      toolset: this.el.querySelector('#task-toolset').value,
      provider: this.el.querySelector('#task-provider').value,
      model: this.el.querySelector('#task-model').value.trim(),
      chat_mode: this.el.querySelector('#task-chat-mode').value,
      chat_target: this.el.querySelector('#task-chat-target').value.trim(),
      tts_enabled: this.el.querySelector('#task-tts').checked
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