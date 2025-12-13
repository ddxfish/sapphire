// Prompt Manager Plugin - index.js
import { injectStyles } from './prompt-styles.js';
import { showToast } from '../../shared/toast.js';
import { showModal } from '../../shared/modal.js';
import * as API from './prompt-api.js';
import { buildMainUI, buildEditor } from './prompt-ui-builder.js';

export default {
  helpText: `Prompt Types:
- Monolith: Single text block, full manual control
- Assembled: Built from reusable components

Assembled Components:
- Persona, Location, Goals, Scenario, Relationship, Format
- Extras/Emotions: Multi-select, combined into prompt

Toolbar Buttons:
- + New prompt  • 💾 Save  • 📑 Save As
- 🗑 Delete  • 🔍 Preview  • ⚡ Activate

Editing Components:
- Select dropdown to choose component value
- + Add new component to that type
- ✎ Edit selected component text
- 🗑 Delete selected component

Tips:
- Preview shows final assembled text
- Activate loads prompt for current chat
- Changes auto-save to user/prompts/`,

  async init(container) {
    injectStyles();
    
    const wrapper = buildMainUI();
    container.appendChild(wrapper);
    
    this.elements = {
      select: wrapper.querySelector('#pm-preset-select'),
      newBtn: wrapper.querySelector('#pm-new-btn'),
      deleteBtn: wrapper.querySelector('#pm-delete-btn'),
      loadBtn: wrapper.querySelector('#pm-load-btn'),
      editor: wrapper.querySelector('#pm-editor'),
      saveBtn: wrapper.querySelector('#pm-save-btn'),
      saveasBtn: wrapper.querySelector('#pm-saveas-btn'),
      previewBtn: wrapper.querySelector('#pm-preview-btn')
    };
    
    this.currentPrompt = null;
    this.currentData = null;
    this.components = {};
    this.lastKnownPromptName = null;
    
    this.bindEvents();
    await this.loadComponents();
    await this.loadPromptList();
    this.startStatusWatcher();
  },
  
  startStatusWatcher() {
    this.statusCheckInterval = setInterval(async () => {
      try {
        const statusEl = document.querySelector('.status-prompt-name');
        if (statusEl) {
          const currentName = statusEl.textContent?.split('(')[0]?.trim();
          if (currentName && currentName !== this.lastKnownPromptName) {
            this.lastKnownPromptName = currentName;
            await this.loadComponents();
            await this.loadPromptList();
          }
        }
      } catch (e) {}
    }, 2000);
  },
  
  destroy() {
    if (this.statusCheckInterval) {
      clearInterval(this.statusCheckInterval);
    }
  },
  
  bindEvents() {
    this.elements.select.addEventListener('change', () => this.handleSelect());
    this.elements.newBtn.addEventListener('click', () => this.handleNew());
    this.elements.deleteBtn.addEventListener('click', () => this.handleDelete());
    this.elements.loadBtn.addEventListener('click', () => this.handleLoad());
    this.elements.saveBtn.addEventListener('click', () => this.handleSave());
    this.elements.saveasBtn.addEventListener('click', () => this.handleSaveAs());
    this.elements.previewBtn.addEventListener('click', () => this.handlePreview());
  },
  
  async loadComponents() {
    try {
      this.components = await API.getComponents();
    } catch (e) {
      console.error('Failed to load components:', e);
      this.components = {};
    }
  },
  
  async loadPromptList() {
    try {
      const prompts = await API.listPrompts();
      const previousValue = this.elements.select.value;
      
      this.elements.select.innerHTML = '<option value="">-- Select Prompt --</option>';
      
      for (const p of prompts) {
        const opt = document.createElement('option');
        opt.value = p.name;
        const typeLabel = p.type === 'assembled' ? '(A)' : '(M)';
        
        let charInfo = '';
        try {
          const data = await API.getPrompt(p.name);
          const charCount = data.content?.length || 0;
          charInfo = ` ${charCount}`;
        } catch (e) {}
        
        opt.textContent = `${p.name} ${typeLabel}${charInfo}`;
        this.elements.select.appendChild(opt);
      }
      
      if (previousValue) {
        const stillExists = Array.from(this.elements.select.options).some(o => o.value === previousValue);
        if (stillExists) {
          this.elements.select.value = previousValue;
        }
      }
    } catch (e) {
      console.error('Failed to load prompt list:', e);
      this.elements.select.innerHTML = '<option value="">Error loading prompts</option>';
      showToast('Failed to load prompts', 'error');
    }
  },
  
  async handleSelect() {
    const name = this.elements.select.value;
    if (!name) {
      this.elements.editor.innerHTML = '<div class="pm-placeholder">Select a prompt to edit</div>';
      return;
    }
    
    try {
      const data = await API.getPrompt(name);
      this.currentPrompt = name;
      this.currentData = data;
      this.elements.editor.innerHTML = buildEditor(data, this.components);
      
      if (data.type === 'assembled') {
        this.bindComponentButtons();
      }
    } catch (e) {
      console.error('Failed to load prompt:', e);
      this.elements.editor.innerHTML = `<div class="pm-error">Error: ${e.message}</div>`;
      showToast('Failed to load prompt', 'error');
    }
  },
  
  bindComponentButtons() {
    this.elements.editor.querySelectorAll('.inline-btn.add[data-type]').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        this.handleAddComponent(btn.dataset.type);
      });
    });
    
    this.elements.editor.querySelectorAll('.inline-btn.edit[data-type]').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        this.handleEditComponent(btn.dataset.type);
      });
    });
    
    this.elements.editor.querySelectorAll('.inline-btn.delete[data-type]').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        this.handleDeleteComponent(btn.dataset.type);
      });
    });
    
    this.elements.editor.querySelector('.pm-extras-btn')?.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      this.handleExtrasModal();
    });
    
    this.elements.editor.querySelector('.pm-emotions-btn')?.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      this.handleEmotionsModal();
    });
  },
  
  handleAddComponent(type) {
    showModal(`Add ${type.charAt(0).toUpperCase() + type.slice(1)}`, [
      { id: 'comp-key', label: 'Key Name (e.g., "bunker", "sapphire")', type: 'text' },
      { id: 'comp-value', label: 'Component Text', type: 'textarea' }
    ], async (data) => {
      const key = data['comp-key'].trim();
      const value = data['comp-value'].trim();
      
      if (!key || !value) {
        showToast('Key and value required', 'error');
        return;
      }
      
      try {
        await API.saveComponent(type, key, value);
        showToast(`${type} added!`, 'success');
        await this.loadComponents();
        await this.handleSelect();
      } catch (e) {
        showToast(`Failed: ${e.message}`, 'error');
      }
    });
  },
  
  handleEditComponent(type) {
    const selectEl = document.getElementById(`pm-${type}`);
    if (!selectEl) return;
    
    const key = selectEl.value;
    if (!key) {
      showToast('Select an option first', 'info');
      return;
    }
    
    const currentValue = this.components[type]?.[key] || '';
    
    showModal(`Edit ${type}: ${key}`, [
      { id: 'comp-value', label: 'Component Text', type: 'textarea', value: currentValue }
    ], async (data) => {
      const newValue = data['comp-value'].trim();
      
      if (!newValue) {
        showToast('Value required', 'error');
        return;
      }
      
      try {
        await API.saveComponent(type, key, newValue);
        showToast(`${type} updated!`, 'success');
        await this.loadComponents();
        await this.handleSelect();
      } catch (e) {
        showToast(`Failed: ${e.message}`, 'error');
      }
    });
  },
  
  handleDeleteComponent(type) {
    const selectEl = document.getElementById(`pm-${type}`);
    if (!selectEl) return;
    
    const key = selectEl.value;
    if (!key) {
      showToast('Select an option first', 'info');
      return;
    }
    
    if (!confirm(`Delete ${type}.${key}?`)) return;
    
    API.deleteComponent(type, key).then(async () => {
      showToast(`${type} deleted!`, 'success');
      await this.loadComponents();
      await this.handleSelect();
    }).catch(e => {
      showToast(`Failed: ${e.message}`, 'error');
    });
  },
  
  handleExtrasModal() {
    if (!this.currentData || this.currentData.type !== 'assembled') return;
    
    const currentExtras = this.currentData.components?.extras || [];
    const availableExtras = this.components.extras || {};
    
    const formattedOptions = {};
    Object.entries(availableExtras).forEach(([key, content]) => {
      const preview = content.length > 100 ? content.substring(0, 100) + '...' : content;
      formattedOptions[key] = `${key}:<br>${preview}`;
    });
    
    showModal('Select Extras', [
      {
        id: 'extras',
        label: 'Available Extras',
        type: 'checkboxes',
        options: formattedOptions,
        selected: currentExtras
      }
    ], (data) => {
      this.currentData.components.extras = data.extras || [];
      const display = document.getElementById('pm-extras-display');
      if (display) {
        display.textContent = this.currentData.components.extras.join(', ') || 'none';
      }
    });
  },
  
  handleEmotionsModal() {
    if (!this.currentData || this.currentData.type !== 'assembled') return;
    
    const currentEmotions = this.currentData.components?.emotions || [];
    const availableEmotions = this.components.emotions || {};
    
    const formattedOptions = {};
    Object.entries(availableEmotions).forEach(([key, content]) => {
      const preview = content.length > 100 ? content.substring(0, 100) + '...' : content;
      formattedOptions[key] = `${key}:<br>${preview}`;
    });
    
    showModal('Select Emotions', [
      {
        id: 'emotions',
        label: 'Available Emotions',
        type: 'checkboxes',
        options: formattedOptions,
        selected: currentEmotions
      }
    ], (data) => {
      this.currentData.components.emotions = data.emotions || [];
      const display = document.getElementById('pm-emotions-display');
      if (display) {
        display.textContent = this.currentData.components.emotions.join(', ') || 'none';
      }
    });
  },
  
  async handleLoad() {
    const name = this.currentPrompt;
    if (!name) {
      showToast('No prompt selected', 'info');
      return;
    }
    
    try {
      await API.loadPrompt(name);
      showToast(`Loaded: ${name}`, 'success');
      this.lastKnownPromptName = name;
    } catch (e) {
      showToast(`Failed to load: ${e.message}`, 'error');
    }
  },
  
  handleNew() {
    showModal('Create New Prompt', [
      { id: 'prompt-name', label: 'Prompt Name', type: 'text' },
      { id: 'prompt-type', label: 'Type', type: 'select', options: ['monolith', 'assembled'], value: 'monolith' }
    ], async (data) => {
      const name = data['prompt-name'].trim();
      if (!name) {
        showToast('Name required', 'error');
        return;
      }
      
      const promptData = {
        name: name,
        type: data['prompt-type'],
        [data['prompt-type'] === 'monolith' ? 'content' : 'components']: 
          data['prompt-type'] === 'monolith' 
            ? 'Enter your prompt here...' 
            : { persona: 'sapphire', location: 'default', goals: 'none', relationship: 'friend', format: 'conversational', scenario: 'default', extras: [], emotions: [] }
      };
      
      try {
        await API.savePrompt(name, promptData);
        showToast('Prompt created!', 'success');
        await this.loadPromptList();
        this.elements.select.value = name;
        await this.handleSelect();
      } catch (e) {
        showToast(`Failed: ${e.message}`, 'error');
      }
    });
  },
  
  collectData() {
    if (!this.currentData) return null;
    
    const type = this.currentData.type || 'monolith';
    
    if (type === 'monolith') {
      return {
        name: this.currentPrompt,
        type: 'monolith',
        content: document.getElementById('pm-content')?.value || ''
      };
    } else if (type === 'assembled') {
      return {
        name: this.currentPrompt,
        type: 'assembled',
        components: {
          persona: document.getElementById('pm-persona')?.value || 'default',
          location: document.getElementById('pm-location')?.value || 'default',
          goals: document.getElementById('pm-goals')?.value || 'default',
          relationship: document.getElementById('pm-relationship')?.value || 'default',
          format: document.getElementById('pm-format')?.value || 'default',
          scenario: document.getElementById('pm-scenario')?.value || 'none',
          extras: this.currentData.components?.extras || [],
          emotions: this.currentData.components?.emotions || []
        }
      };
    }
    
    return null;
  },
  
  async handleSave() {
    if (!this.currentPrompt) {
      showToast('No prompt selected', 'error');
      return;
    }
    
    const data = this.collectData();
    if (!data) {
      showToast('Failed to collect data', 'error');
      return;
    }
    
    try {
      await API.savePrompt(this.currentPrompt, data);
      showToast('Prompt saved!', 'success');
      await this.loadPromptList();
      this.elements.select.value = this.currentPrompt;
      await this.handleSelect();
    } catch (e) {
      showToast(`Save failed: ${e.message}`, 'error');
    }
  },
  
  async handleDelete() {
    if (!this.currentPrompt) {
      showToast('No prompt selected', 'error');
      return;
    }
    
    if (!confirm(`Delete prompt "${this.currentPrompt}"?`)) return;
    
    try {
      await API.deletePrompt(this.currentPrompt);
      showToast('Prompt deleted', 'success');
      await this.loadPromptList();
      this.elements.editor.innerHTML = '<div class="pm-placeholder">Select a prompt to edit</div>';
      this.currentPrompt = null;
      this.currentData = null;
    } catch (e) {
      showToast(`Delete failed: ${e.message}`, 'error');
    }
  },
  
  handlePreview() {
    if (!this.currentPrompt || !this.currentData) {
      showToast('No prompt selected', 'info');
      return;
    }
    
    let previewContent = '';
    const charCount = this.currentData.content?.length || 0;
    
    if (this.currentData.type === 'monolith') {
      previewContent = this.currentData.content || '';
    } else if (this.currentData.type === 'assembled') {
      previewContent = this.currentData.content || 'No assembled content available';
    }
    
    showModal(`Preview: ${this.currentPrompt}`, [
      {
        id: 'preview-display',
        type: 'html',
        value: `
          <div style="margin-bottom: 12px; color: var(--text-muted); font-size: var(--font-base);">
            ${this.currentData.type === 'monolith' ? 'Monolith' : 'Assembled'} • ${charCount} characters
          </div>
          <textarea readonly style="
            width: 100%;
            min-height: 400px;
            background: var(--bg-tertiary);
            border: 1px solid var(--border-light);
            color: var(--text-light);
            padding: 12px;
            border-radius: var(--radius-md);
            font-size: var(--font-base);
            font-family: var(--font-mono);
            line-height: 1.5;
            resize: vertical;
            white-space: pre-wrap;
            word-wrap: break-word;
          ">${previewContent}</textarea>
        `
      }
    ], null);
  },
  
  async handleSaveAs() {
    if (!this.currentPrompt) {
      showToast('No prompt selected', 'error');
      return;
    }
    
    const name = prompt('Save as (new name):');
    if (!name || !name.trim()) return;
    
    const data = this.collectData();
    if (!data) {
      showToast('Failed to collect data', 'error');
      return;
    }
    
    data.name = name.trim();
    
    try {
      await API.savePrompt(name.trim(), data);
      showToast(`Saved as "${name}"`, 'success');
      await this.loadPromptList();
      this.elements.select.value = name.trim();
      await this.handleSelect();
    } catch (e) {
      showToast(`Save As failed: ${e.message}`, 'error');
    }
  }
};