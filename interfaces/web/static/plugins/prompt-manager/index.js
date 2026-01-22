// Prompt Manager Plugin - index.js
// Simplified: Editor always shows active prompt. Select = activate. Edit = auto-save & apply.
import { injectStyles } from './prompt-styles.js';
import { showToast } from '../../shared/toast.js';
import { showModal } from '../../shared/modal.js';
import * as API from './prompt-api.js';
import { buildMainUI, buildEditor } from './prompt-ui-builder.js';

// Import updateScene to sync pill after changes
async function updateScene() {
  const { updateScene: doUpdate } = await import('../../features/scene.js');
  return doUpdate();
}

export default {
  helpText: `Prompt Types:
- Monolith: Single text block, full manual control
- Assembled: Built from reusable components

Assembled Components:
- Persona, Location, Goals, Scenario, Relationship, Format
- Extras/Emotions: Multi-select, combined into prompt

Toolbar Buttons:
- + New prompt  • 🔄 Refresh  • 🗑 Delete  • 🔍 Preview

Editing:
- Select prompt = activates it immediately
- All changes auto-save and apply instantly
- No manual save needed`,

  async init(container) {
    injectStyles();
    
    const wrapper = buildMainUI();
    container.appendChild(wrapper);
    
    this.elements = {
      select: wrapper.querySelector('#pm-preset-select'),
      newBtn: wrapper.querySelector('#pm-new-btn'),
      refreshBtn: wrapper.querySelector('#pm-refresh-btn'),
      deleteBtn: wrapper.querySelector('#pm-delete-btn'),
      editor: wrapper.querySelector('#pm-editor'),
      previewBtn: wrapper.querySelector('#pm-preview-btn')
    };
    
    this.currentPrompt = null;
    this.currentData = null;
    this.components = {};
    this.lastLoadedContentHash = null;
    this._monolithSaveTimeout = null;
    this._loadInProgress = false;
    this._lastLoadTime = 0;
    
    this.bindEvents();
    await this.loadComponents();
    await this.loadPromptList();
    
    // Sync to currently active prompt from pill
    await this.syncToActivePill();
    
    this.startStatusWatcher();
    
    // Listen for external prompt changes (reset/merge from settings)
    this._promptsChangedHandler = () => this.handleExternalPromptsChange();
    window.addEventListener('prompts-changed', this._promptsChangedHandler);
  },
  
  async handleExternalPromptsChange() {
    // Prompts were reset/merged externally - refresh everything
    await this.loadComponents();
    await this.loadPromptList();
    await this.syncToActivePill();
    showToast('Prompts refreshed', 'info');
  },
  
  async syncToActivePill() {
    // Read active prompt from pill element
    const pillText = document.querySelector('#prompt-pill .pill-text');
    if (!pillText) return;
    
    // Format is "PromptName (2.4k)" - extract just the name
    const fullText = pillText.textContent || '';
    const activeName = fullText.split(' (')[0].trim();
    
    if (activeName && activeName !== 'Loading...') {
      // Set dropdown to match and load it
      const option = Array.from(this.elements.select.options).find(o => o.value === activeName);
      if (option) {
        this.elements.select.value = activeName;
        await this.loadPromptIntoEditor(activeName);
      }
    }
  },
  
  startStatusWatcher() {
    this.statusCheckInterval = setInterval(async () => {
      // Skip if a load is already in progress or was recent
      if (this._loadInProgress) return;
      if (Date.now() - this._lastLoadTime < 1500) return;
      
      try {
        // Check if pill prompt changed externally
        const pillText = document.querySelector('#prompt-pill .pill-text');
        if (!pillText) return;
        
        const fullText = pillText.textContent || '';
        const pillPromptName = fullText.split(' (')[0].trim();
        
        // If pill shows different prompt than editor, sync editor to pill
        if (pillPromptName && pillPromptName !== 'Loading...' && pillPromptName !== this.currentPrompt) {
          this._loadInProgress = true;
          try {
            this.elements.select.value = pillPromptName;
            await this.loadComponents();
            await this.loadPromptIntoEditor(pillPromptName);
          } finally {
            this._loadInProgress = false;
            this._lastLoadTime = Date.now();
          }
        }
        
        // Auto-refresh: check if currently loaded prompt changed on disk (external edit)
        if (this.currentPrompt && !this._userIsEditing() && !this._loadInProgress) {
          const freshData = await API.getPrompt(this.currentPrompt);
          const freshHash = this._hashPromptData(freshData);
          if (freshHash !== this.lastLoadedContentHash) {
            this._loadInProgress = true;
            try {
              await this.loadComponents();
              this.currentData = freshData;
              this.lastLoadedContentHash = freshHash;
              this.elements.editor.innerHTML = buildEditor(freshData, this.components);
              this.bindEditorEvents();
            } finally {
              this._loadInProgress = false;
              this._lastLoadTime = Date.now();
            }
          }
        }
      } catch (e) {}
    }, 2000);
  },
  
  _hashPromptData(data) {
    return JSON.stringify(data);
  },
  
  _userIsEditing() {
    const active = document.activeElement;
    if (!active) return false;
    const editor = this.elements.editor;
    return editor && editor.contains(active);
  },
  
  destroy() {
    if (this.statusCheckInterval) {
      clearInterval(this.statusCheckInterval);
    }
    if (this._monolithSaveTimeout) {
      clearTimeout(this._monolithSaveTimeout);
    }
    if (this._promptsChangedHandler) {
      window.removeEventListener('prompts-changed', this._promptsChangedHandler);
    }
  },
  
  bindEvents() {
    this.elements.select.addEventListener('change', () => this.handleSelect());
    this.elements.newBtn.addEventListener('click', () => this.handleNew());
    this.elements.refreshBtn.addEventListener('click', () => this.handleRefresh());
    this.elements.deleteBtn.addEventListener('click', () => this.handleDelete());
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
      this.currentPrompt = null;
      this.currentData = null;
      this.lastLoadedContentHash = null;
      return;
    }
    
    try {
      // ACTIVATE the prompt immediately
      await API.loadPrompt(name);
      await updateScene();
      
      // Then load into editor
      await this.loadPromptIntoEditor(name);
      
      showToast(`Prompt: ${name}`, 'success');
    } catch (e) {
      console.error('Failed to switch prompt:', e);
      showToast(`Failed: ${e.message}`, 'error');
    }
  },
  
  async loadPromptIntoEditor(name) {
    // Skip if already loading this prompt or if load in progress
    if (this._loadInProgress && this.currentPrompt === name) return;
    
    try {
      const data = await API.getPrompt(name);
      this.currentPrompt = name;
      this.currentData = data;
      this.lastLoadedContentHash = this._hashPromptData(data);
      this.elements.editor.innerHTML = buildEditor(data, this.components);
      this.bindEditorEvents();
    } catch (e) {
      console.error('Failed to load prompt:', e);
      this.elements.editor.innerHTML = `<div class="pm-error">Error: ${e.message}</div>`;
    }
  },
  
  bindEditorEvents() {
    if (!this.currentData) return;
    
    if (this.currentData.type === 'assembled') {
      this.bindComponentButtons();
    } else if (this.currentData.type === 'monolith') {
      this.bindMonolithAutoSave();
    }
  },
  
  bindMonolithAutoSave() {
    const textarea = document.getElementById('pm-content');
    if (!textarea) return;
    
    // Debounced auto-save on input
    textarea.addEventListener('input', () => {
      if (this._monolithSaveTimeout) {
        clearTimeout(this._monolithSaveTimeout);
      }
      this._monolithSaveTimeout = setTimeout(() => this.autoSaveMonolith(), 1000);
    });
    
    // Immediate save on blur
    textarea.addEventListener('blur', () => {
      if (this._monolithSaveTimeout) {
        clearTimeout(this._monolithSaveTimeout);
      }
      this.autoSaveMonolith();
    });
  },
  
  async autoSaveMonolith() {
    if (!this.currentPrompt || this.currentData?.type !== 'monolith') return;
    
    const textarea = document.getElementById('pm-content');
    if (!textarea) return;
    
    const data = {
      name: this.currentPrompt,
      type: 'monolith',
      content: textarea.value
    };
    
    try {
      await API.savePrompt(this.currentPrompt, data);
      await API.loadPrompt(this.currentPrompt);
      await updateScene();
      this.lastLoadedContentHash = this._hashPromptData(data);
    } catch (e) {
      console.warn('Auto-save failed:', e);
    }
  },
  
  async _handleComponentChange(type, value) {
    if (this.currentData?.components) {
      this.currentData.components[type] = value;
    }
    
    const data = this.collectData();
    if (!data) return;
    
    try {
      await API.savePrompt(this.currentPrompt, data);
      await API.loadPrompt(this.currentPrompt);
      await updateScene();
    } catch (e) {
      console.warn('Component change save failed:', e);
    }
  },
  
  async handleRefresh() {
    try {
      await this.loadComponents();
      await this.loadPromptList();
      await this.syncToActivePill();
      showToast('Refreshed', 'success');
    } catch (e) {
      console.error('Refresh failed:', e);
      showToast('Refresh failed', 'error');
    }
  },
  
  bindComponentButtons() {
    // Auto-save when any component dropdown changes
    const componentSelects = ['persona', 'location', 'goals', 'relationship', 'format', 'scenario'];
    componentSelects.forEach(type => {
      const select = document.getElementById(`pm-${type}`);
      if (select) {
        select.addEventListener('change', () => this._handleComponentChange(type, select.value));
      }
    });
    
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
    
    this.elements.editor.querySelector('.pm-extras-delete-btn')?.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      this.handleDeleteExtrasModal();
    });
    
    this.elements.editor.querySelector('.pm-emotions-delete-btn')?.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      this.handleDeleteEmotionsModal();
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
        await this.loadPromptIntoEditor(this.currentPrompt);
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
        
        // Re-apply to LLM since component text changed
        await API.loadPrompt(this.currentPrompt);
        await updateScene();
        
        await this.loadPromptIntoEditor(this.currentPrompt);
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
      await this.loadPromptIntoEditor(this.currentPrompt);
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
      formattedOptions[key] = `<strong>${key}:</strong><br><pre style="white-space:pre-wrap;margin:4px 0 8px;max-height:200px;overflow-y:auto;">${content}</pre>`;
    });
    
    showModal('Select Extras', [
      {
        id: 'extras-select',
        label: 'Active extras (multi-select)',
        type: 'checkboxes',
        options: formattedOptions,
        selected: currentExtras
      }
    ], async (data) => {
      const selected = data['extras-select'] || [];
      this.currentData.components.extras = selected;
      
      const promptData = this.collectData();
      if (!promptData) return;
      
      try {
        await API.savePrompt(this.currentPrompt, promptData);
        await API.loadPrompt(this.currentPrompt);
        await updateScene();
        showToast('Extras updated', 'success');
        await this.loadPromptIntoEditor(this.currentPrompt);
      } catch (e) {
        showToast(`Failed: ${e.message}`, 'error');
      }
    }, { wide: true });
  },
  
  handleEmotionsModal() {
    if (!this.currentData || this.currentData.type !== 'assembled') return;
    
    const currentEmotions = this.currentData.components?.emotions || [];
    const availableEmotions = this.components.emotions || {};
    
    const formattedOptions = {};
    Object.entries(availableEmotions).forEach(([key, content]) => {
      formattedOptions[key] = `<strong>${key}:</strong><br><pre style="white-space:pre-wrap;margin:4px 0 8px;max-height:200px;overflow-y:auto;">${content}</pre>`;
    });
    
    showModal('Select Emotions', [
      {
        id: 'emotions-select',
        label: 'Active emotions (multi-select)',
        type: 'checkboxes',
        options: formattedOptions,
        selected: currentEmotions
      }
    ], async (data) => {
      const selected = data['emotions-select'] || [];
      this.currentData.components.emotions = selected;
      
      const promptData = this.collectData();
      if (!promptData) return;
      
      try {
        await API.savePrompt(this.currentPrompt, promptData);
        await API.loadPrompt(this.currentPrompt);
        await updateScene();
        showToast('Emotions updated', 'success');
        await this.loadPromptIntoEditor(this.currentPrompt);
      } catch (e) {
        showToast(`Failed: ${e.message}`, 'error');
      }
    }, { wide: true });
  },
  
  handleDeleteExtrasModal() {
    const availableExtras = this.components.extras || {};
    const keys = Object.keys(availableExtras);
    
    if (keys.length === 0) {
      showToast('No extras to delete', 'info');
      return;
    }
    
    const formattedOptions = {};
    Object.entries(availableExtras).forEach(([key, content]) => {
      formattedOptions[key] = `<strong>${key}:</strong><br><pre style="white-space:pre-wrap;margin:4px 0 8px;max-height:200px;overflow-y:auto;">${content}</pre>`;
    });
    
    showModal('Delete Extras', [
      {
        id: 'delete-extras',
        label: 'Select extras to DELETE (permanent)',
        type: 'checkboxes',
        options: formattedOptions,
        selected: []
      }
    ], async (data) => {
      const toDelete = data['delete-extras'] || [];
      if (toDelete.length === 0) {
        showToast('Nothing selected', 'info');
        return;
      }
      
      if (!confirm(`Delete ${toDelete.length} extra(s)?\n\n${toDelete.join(', ')}\n\nThis cannot be undone.`)) {
        return;
      }
      
      let deleted = 0;
      for (const key of toDelete) {
        try {
          await API.deleteComponent('extras', key);
          deleted++;
        } catch (e) {
          console.error(`Failed to delete extras.${key}:`, e);
        }
      }
      
      showToast(`Deleted ${deleted} extra(s)`, 'success');
      await this.loadComponents();
      await this.loadPromptIntoEditor(this.currentPrompt);
    }, { wide: true });
  },
  
  handleDeleteEmotionsModal() {
    const availableEmotions = this.components.emotions || {};
    const keys = Object.keys(availableEmotions);
    
    if (keys.length === 0) {
      showToast('No emotions to delete', 'info');
      return;
    }
    
    const formattedOptions = {};
    Object.entries(availableEmotions).forEach(([key, content]) => {
      formattedOptions[key] = `<strong>${key}:</strong><br><pre style="white-space:pre-wrap;margin:4px 0 8px;max-height:200px;overflow-y:auto;">${content}</pre>`;
    });
    
    showModal('Delete Emotions', [
      {
        id: 'delete-emotions',
        label: 'Select emotions to DELETE (permanent)',
        type: 'checkboxes',
        options: formattedOptions,
        selected: []
      }
    ], async (data) => {
      const toDelete = data['delete-emotions'] || [];
      if (toDelete.length === 0) {
        showToast('Nothing selected', 'info');
        return;
      }
      
      if (!confirm(`Delete ${toDelete.length} emotion(s)?\n\n${toDelete.join(', ')}\n\nThis cannot be undone.`)) {
        return;
      }
      
      let deleted = 0;
      for (const key of toDelete) {
        try {
          await API.deleteComponent('emotions', key);
          deleted++;
        } catch (e) {
          console.error(`Failed to delete emotions.${key}:`, e);
        }
      }
      
      showToast(`Deleted ${deleted} emotion(s)`, 'success');
      await this.loadComponents();
      await this.loadPromptIntoEditor(this.currentPrompt);
    }, { wide: true });
  },
  
  handleNew() {
    showModal('Create New Prompt', [
      { id: 'prompt-name', label: 'Prompt Name', type: 'text' },
      { id: 'prompt-type', label: 'Type', type: 'select', options: ['assembled', 'monolith'], value: 'assembled' }
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
        
        // Select and activate the new prompt
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
      
      // Sync to whatever prompt is now active
      await this.syncToActivePill();
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
  }
};