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
      previewBtn: wrapper.querySelector('#pm-preview-btn'),
      exportBtn: wrapper.querySelector('#pm-export-btn')
    };
    
    this.currentPrompt = null;
    this.currentData = null;
    this.components = {};
    this.lastLoadedContentHash = null;
    this._monolithSaveTimeout = null;
    this._loadInProgress = false;
    this._listLoadInProgress = false;
    this._listLoadStarted = 0;
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
    
    // Listen for SSE events for real-time updates
    this._setupSSEHandlers();
  },
  
  _setupSSEHandlers() {
    // Dynamically import eventBus to avoid circular dependencies
    import('../../core/event-bus.js').then(eventBus => {
      // Components changed - refresh components and editor
      this._componentsChangedUnsub = eventBus.on('components_changed', async (data) => {
        console.log('[PromptManager] SSE: components_changed', data);
        if (!this._loadInProgress) {
          await this.loadComponents();
          if (this.currentPrompt && this.currentData?.type === 'assembled') {
            await this.loadPromptIntoEditor(this.currentPrompt);
          }
        }
      });
      
      // Prompt changed - refresh list and potentially editor
      this._promptChangedUnsub = eventBus.on('prompt_changed', async (data) => {
        console.log('[PromptManager] SSE: prompt_changed', data);
        if (!this._loadInProgress) {
          await this.loadPromptList();
          // If bulk change, refresh everything
          if (data?.bulk) {
            await this.loadComponents();
            await this.syncToActivePill();
          }
        }
      });
      
      // Prompt deleted - refresh list and clear editor if viewing deleted prompt
      this._promptDeletedUnsub = eventBus.on('prompt_deleted', async (data) => {
        console.log('[PromptManager] SSE: prompt_deleted', data);
        if (!this._loadInProgress) {
          await this.loadPromptList();
          if (this.currentPrompt === data?.name) {
            this.elements.editor.innerHTML = '<div class="pm-placeholder">Prompt was deleted</div>';
            this.currentPrompt = null;
            this.currentData = null;
          }
        }
      });
    });
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
    // Clean up SSE handlers
    if (this._componentsChangedUnsub) this._componentsChangedUnsub();
    if (this._promptChangedUnsub) this._promptChangedUnsub();
    if (this._promptDeletedUnsub) this._promptDeletedUnsub();
  },
  
  bindEvents() {
    this.elements.select.addEventListener('change', () => this.handleSelect());
    this.elements.newBtn.addEventListener('click', () => this.handleNew());
    this.elements.refreshBtn.addEventListener('click', () => this.handleRefresh());
    this.elements.deleteBtn.addEventListener('click', () => this.handleDelete());
    this.elements.previewBtn.addEventListener('click', () => this.handlePreview());
    this.elements.exportBtn.addEventListener('click', () => this.handleImportExport());
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
    // Prevent concurrent list loads - hard 5s timeout
    const now = Date.now();
    if (this._listLoadInProgress) {
      if ((now - this._listLoadStarted) < 5000) {
        console.log('[PromptManager] loadPromptList skipped - already in progress');
        return;
      } else {
        console.warn('[PromptManager] loadPromptList timeout - forcing restart');
      }
    }
    this._listLoadInProgress = true;
    this._listLoadStarted = now;
    
    console.log('[PromptManager] loadPromptList starting...');
    
    try {
      const prompts = await API.listPrompts();
      console.log(`[PromptManager] Got ${prompts.length} prompts from API`);
      
      const previousValue = this.elements.select.value;
      
      // Clear and rebuild - NO per-prompt API calls (they can hang)
      this.elements.select.innerHTML = '<option value="">-- Select Prompt --</option>';
      
      for (const p of prompts) {
        const opt = document.createElement('option');
        opt.value = p.name;
        const typeLabel = p.type === 'assembled' ? '(A)' : '(M)';
        opt.textContent = `${p.name} ${typeLabel}`;
        this.elements.select.appendChild(opt);
      }
      
      if (previousValue) {
        const stillExists = Array.from(this.elements.select.options).some(o => o.value === previousValue);
        if (stillExists) {
          this.elements.select.value = previousValue;
        }
      }
      
      console.log('[PromptManager] loadPromptList complete');
    } catch (e) {
      console.error('Failed to load prompt list:', e);
      this.elements.select.innerHTML = '<option value="">Error loading prompts</option>';
      showToast('Failed to load prompts', 'error');
    } finally {
      this._listLoadInProgress = false;
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
      // If 404, prompt was deleted - try to sync to active pill
      if (e.message.includes('404')) {
        console.warn(`Prompt '${name}' not found, syncing to active`);
        this.currentPrompt = null;
        this.currentData = null;
        this.elements.editor.innerHTML = '<div class="pm-placeholder">Prompt not found</div>';
        // Let the status watcher or pill sync handle finding the right prompt
        return;
      }
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
      // Force reset guards - user explicitly requested refresh
      this._listLoadInProgress = false;
      this._loadInProgress = false;
      
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
    
    this.elements.editor.querySelector('.pm-extras-select-btn')?.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      this.handleExtrasModal();
    });
    
    this.elements.editor.querySelector('.pm-emotions-select-btn')?.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      this.handleEmotionsModal();
    });
    
    this.elements.editor.querySelector('.pm-extras-edit-btn')?.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      this.handleEditExtrasDefinitions();
    });
    
    this.elements.editor.querySelector('.pm-emotions-edit-btn')?.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      this.handleEditEmotionsDefinitions();
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
        
        // Auto-select new extras/emotions in current prompt
        if ((type === 'extras' || type === 'emotions') && 
            this.currentData?.type === 'assembled' && 
            this.currentData.components) {
          const arr = this.currentData.components[type] || [];
          if (!arr.includes(key)) {
            this.currentData.components[type] = [...arr, key];
            const promptData = this.collectData();
            if (promptData) {
              await API.savePrompt(this.currentPrompt, promptData);
              await API.loadPrompt(this.currentPrompt);
              await updateScene();
            }
          }
        }
        
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
    }, { large: true });
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
      
      // Clean stale refs from current prompt
      if (this.currentData?.type === 'assembled' && this.currentData.components?.extras) {
        const validKeys = Object.keys(this.components.extras || {});
        const before = this.currentData.components.extras.length;
        this.currentData.components.extras = this.currentData.components.extras.filter(k => validKeys.includes(k));
        
        if (this.currentData.components.extras.length < before) {
          const promptData = this.collectData();
          if (promptData) {
            await API.savePrompt(this.currentPrompt, promptData);
            await API.loadPrompt(this.currentPrompt);
            await updateScene();
          }
        }
      }
      
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
      
      // Clean stale refs from current prompt
      if (this.currentData?.type === 'assembled' && this.currentData.components?.emotions) {
        const validKeys = Object.keys(this.components.emotions || {});
        const before = this.currentData.components.emotions.length;
        this.currentData.components.emotions = this.currentData.components.emotions.filter(k => validKeys.includes(k));
        
        if (this.currentData.components.emotions.length < before) {
          const promptData = this.collectData();
          if (promptData) {
            await API.savePrompt(this.currentPrompt, promptData);
            await API.loadPrompt(this.currentPrompt);
            await updateScene();
          }
        }
      }
      
      await this.loadPromptIntoEditor(this.currentPrompt);
    }, { wide: true });
  },
  
  handleEditExtrasDefinitions() {
    const availableExtras = this.components.extras || {};
    const keys = Object.keys(availableExtras);
    
    if (keys.length === 0) {
      showToast('No extras to edit. Add one first.', 'info');
      return;
    }
    
    this._showEditDefinitionsModal('extras', availableExtras, async (changes) => {
      let saved = 0;
      for (const [key, value] of Object.entries(changes)) {
        try {
          await API.saveComponent('extras', key, value);
          saved++;
        } catch (e) {
          console.error(`Failed to save extras.${key}:`, e);
        }
      }
      
      if (saved > 0) {
        showToast(`Updated ${saved} extra(s)`, 'success');
        await this.loadComponents();
        
        // Re-apply prompt to LLM since content changed
        if (this.currentPrompt) {
          await API.loadPrompt(this.currentPrompt);
          await updateScene();
        }
        
        await this.loadPromptIntoEditor(this.currentPrompt);
      }
    });
  },
  
  handleEditEmotionsDefinitions() {
    const availableEmotions = this.components.emotions || {};
    const keys = Object.keys(availableEmotions);
    
    if (keys.length === 0) {
      showToast('No emotions to edit. Add one first.', 'info');
      return;
    }
    
    this._showEditDefinitionsModal('emotions', availableEmotions, async (changes) => {
      let saved = 0;
      for (const [key, value] of Object.entries(changes)) {
        try {
          await API.saveComponent('emotions', key, value);
          saved++;
        } catch (e) {
          console.error(`Failed to save emotions.${key}:`, e);
        }
      }
      
      if (saved > 0) {
        showToast(`Updated ${saved} emotion(s)`, 'success');
        await this.loadComponents();
        
        // Re-apply prompt to LLM since content changed
        if (this.currentPrompt) {
          await API.loadPrompt(this.currentPrompt);
          await updateScene();
        }
        
        await this.loadPromptIntoEditor(this.currentPrompt);
      }
    });
  },
  
  _showEditDefinitionsModal(type, items, onSave) {
    const overlay = document.createElement('div');
    overlay.className = 'modal-overlay modal-wide';
    
    const itemsHTML = Object.entries(items).map(([key, value]) => `
      <div class="edit-def-item">
        <label for="edit-def-${key}">${key}</label>
        <textarea id="edit-def-${key}" data-key="${key}" rows="3">${value.replace(/</g, '&lt;').replace(/>/g, '&gt;')}</textarea>
      </div>
    `).join('');
    
    overlay.innerHTML = `
      <div class="modal-base">
        <div class="modal-header">
          <h3>Edit ${type.charAt(0).toUpperCase() + type.slice(1)} Definitions</h3>
          <button class="close-btn modal-x">&times;</button>
        </div>
        <div class="modal-body">
          <p style="margin:0 0 12px;color:var(--text-muted);font-size:var(--font-sm);">
            Edit the text for each ${type.slice(0, -1)}. Changes are saved when you click Save.
          </p>
          <div class="edit-def-list">${itemsHTML}</div>
        </div>
        <div class="modal-footer">
          <button class="btn btn-secondary modal-cancel">Cancel</button>
          <button class="btn btn-primary modal-save">Save Changes</button>
        </div>
      </div>
    `;
    
    document.body.appendChild(overlay);
    requestAnimationFrame(() => overlay.classList.add('active'));
    
    const close = () => {
      overlay.classList.remove('active');
      setTimeout(() => overlay.remove(), 300);
    };
    
    overlay.querySelector('.modal-x')?.addEventListener('click', close);
    overlay.querySelector('.modal-cancel')?.addEventListener('click', close);
    overlay.addEventListener('click', e => { if (e.target === overlay) close(); });
    
    const escHandler = e => {
      if (e.key === 'Escape') {
        close();
        document.removeEventListener('keydown', escHandler);
      }
    };
    document.addEventListener('keydown', escHandler);
    
    overlay.querySelector('.modal-save')?.addEventListener('click', () => {
      const changes = {};
      overlay.querySelectorAll('.edit-def-list textarea').forEach(textarea => {
        const key = textarea.dataset.key;
        const newValue = textarea.value.trim();
        const originalValue = items[key];
        
        if (newValue && newValue !== originalValue) {
          changes[key] = newValue;
        }
      });
      
      close();
      
      if (Object.keys(changes).length > 0) {
        onSave(changes);
      } else {
        showToast('No changes to save', 'info');
      }
    });
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
        console.log(`[PromptManager] Creating prompt: ${name}`);
        await API.savePrompt(name, promptData);
        
        // Force-reset guard and refresh list
        this._listLoadInProgress = false;
        await this.loadPromptList();
        
        // Select the new prompt in dropdown
        this.elements.select.value = name;
        
        // Activate it (loads into backend + editor)
        console.log(`[PromptManager] Activating new prompt: ${name}`);
        await API.loadPrompt(name);
        await this.loadPromptIntoEditor(name);
        await updateScene();
        
        showToast(`Created & activated: ${name}`, 'success');
      } catch (e) {
        console.error('[PromptManager] Create failed:', e);
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
    
    const deletedName = this.currentPrompt;
    console.log(`[PromptManager] Deleting prompt: ${deletedName}`);
    
    try {
      // Get fallback BEFORE deleting
      const promptsBefore = await API.listPrompts();
      const fallbackName = promptsBefore.find(p => p.name !== deletedName)?.name;
      
      if (!fallbackName) {
        showToast('Cannot delete the only prompt', 'error');
        return;
      }
      
      console.log(`[PromptManager] Fallback will be: ${fallbackName}`);
      
      // Prevent status watcher from running during delete
      this._loadInProgress = true;
      
      // Delete the prompt
      console.log('[PromptManager] Calling deletePrompt API...');
      await API.deletePrompt(deletedName);
      console.log('[PromptManager] Delete API complete');
      
      // IMMEDIATELY switch backend to fallback (before any UI refresh)
      console.log('[PromptManager] Switching backend to fallback...');
      await API.loadPrompt(fallbackName);
      console.log('[PromptManager] Backend switched');
      
      // Clear local state
      this.currentPrompt = null;
      this.currentData = null;
      this.elements.editor.innerHTML = '<div class="pm-placeholder">Loading...</div>';
      
      // Force-reset list guard and refresh
      this._listLoadInProgress = false;
      console.log('[PromptManager] Refreshing prompt list...');
      await this.loadPromptList();
      console.log('[PromptManager] List refresh complete');
      
      // DEFENSIVE: Remove deleted prompt from dropdown if still present
      const staleOption = Array.from(this.elements.select.options).find(o => o.value === deletedName);
      if (staleOption) {
        console.log(`[PromptManager] Removing stale option: ${deletedName}`);
        staleOption.remove();
      }
      
      // Force select to fallback
      this.elements.select.value = fallbackName;
      
      console.log('[PromptManager] Loading fallback into editor...');
      await this.loadPromptIntoEditor(fallbackName);
      console.log('[PromptManager] Editor loaded');
      
      await updateScene();
      
      this._loadInProgress = false;
      showToast('Prompt deleted', 'success');
      console.log('[PromptManager] Delete complete');
    } catch (e) {
      console.error('[PromptManager] Delete failed:', e);
      this._loadInProgress = false;
      this._listLoadInProgress = false;
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
  
  handleImportExport() {
    if (!this.currentPrompt || !this.currentData) {
      showToast('No prompt selected', 'info');
      return;
    }
    
    // Build comprehensive export data
    const exportData = {
      name: this.currentPrompt,
      prompt: this.currentData,
      components: {}
    };
    
    // For assembled prompts, include referenced component definitions
    if (this.currentData.type === 'assembled' && this.currentData.components) {
      const comp = this.currentData.components;
      const types = ['persona', 'location', 'goals', 'relationship', 'format', 'scenario'];
      
      types.forEach(type => {
        const key = comp[type];
        if (key && this.components[type]?.[key]) {
          if (!exportData.components[type]) exportData.components[type] = {};
          exportData.components[type][key] = this.components[type][key];
        }
      });
      
      // Extras and emotions (arrays)
      ['extras', 'emotions'].forEach(type => {
        const keys = comp[type] || [];
        keys.forEach(key => {
          if (this.components[type]?.[key]) {
            if (!exportData.components[type]) exportData.components[type] = {};
            exportData.components[type][key] = this.components[type][key];
          }
        });
      });
    }
    
    const jsonData = JSON.stringify(exportData, null, 2);
    
    const modalHtml = `
      <div style="display: flex; flex-direction: column; gap: 16px;">
        <div style="color: var(--text-muted); font-size: var(--font-sm); line-height: 1.4;">
          <strong>Export</strong> saves prompt definition + all referenced components.<br>
          <strong>Import</strong> merges into your prompts. Check "overwrite" to replace existing items.
        </div>
        
        <fieldset style="border: 1px solid var(--border); border-radius: var(--radius-md); padding: 12px;">
          <legend style="color: var(--text-secondary); padding: 0 8px;">Export</legend>
          <div style="display: flex; gap: 8px;">
            <button id="pm-export-clipboard" class="btn btn-secondary" style="flex: 1;">📋 Copy to Clipboard</button>
            <button id="pm-export-file" class="btn btn-secondary" style="flex: 1;">💾 Save as File</button>
          </div>
        </fieldset>
        
        <fieldset style="border: 1px solid var(--border); border-radius: var(--radius-md); padding: 12px;">
          <legend style="color: var(--text-secondary); padding: 0 8px;">Import</legend>
          <label style="display: flex; align-items: center; gap: 8px; margin-bottom: 10px; color: var(--text-secondary); font-size: var(--font-sm);">
            <input type="checkbox" id="pm-import-overwrite">
            Overwrite existing prompts and components
          </label>
          <div style="display: flex; gap: 8px;">
            <button id="pm-import-clipboard" class="btn btn-secondary" style="flex: 1;">📋 Paste from Clipboard</button>
            <button id="pm-import-file" class="btn btn-secondary" style="flex: 1;">📂 Load from File</button>
          </div>
        </fieldset>
      </div>
    `;
    
    const { close, element } = showModal(`Import/Export: ${this.currentPrompt}`, [
      { type: 'html', value: modalHtml }
    ], null);
    
    // Export to clipboard
    element.querySelector('#pm-export-clipboard')?.addEventListener('click', async () => {
      try {
        await navigator.clipboard.writeText(jsonData);
        showToast('Copied to clipboard!', 'success');
      } catch (e) {
        showToast('Failed to copy', 'error');
      }
    });
    
    // Export to file
    element.querySelector('#pm-export-file')?.addEventListener('click', () => {
      const blob = new Blob([jsonData], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${this.currentPrompt}.json`;
      a.click();
      URL.revokeObjectURL(url);
      showToast('Downloaded!', 'success');
    });
    
    // Import from clipboard - show modal with textarea
    element.querySelector('#pm-import-clipboard')?.addEventListener('click', () => {
      const overwrite = element.querySelector('#pm-import-overwrite')?.checked || false;
      
      const { close: closeImport, element: importEl } = showModal('Paste JSON', [
        { id: 'import-json', label: 'Paste exported prompt JSON below:', type: 'textarea', value: '', rows: 12 }
      ], async (data) => {
        const text = data['import-json']?.trim();
        if (!text) {
          showToast('No JSON provided', 'error');
          return;
        }
        try {
          await this._importPromptData(text, overwrite);
          close(); // Close main import/export modal
        } catch (e) {
          showToast(`Import failed: ${e.message}`, 'error');
        }
      }, { large: true });
    });
    
    // Import from file
    element.querySelector('#pm-import-file')?.addEventListener('click', () => {
      const overwrite = element.querySelector('#pm-import-overwrite')?.checked || false;
      const input = document.createElement('input');
      input.type = 'file';
      input.accept = '.json';
      input.onchange = async (e) => {
        const file = e.target.files[0];
        if (!file) return;
        try {
          const text = await file.text();
          await this._importPromptData(text, overwrite);
          close();
        } catch (e) {
          showToast(`Import failed: ${e.message}`, 'error');
        }
      };
      input.click();
    });
  },
  
  async _importPromptData(jsonText, overwrite = false) {
    const data = JSON.parse(jsonText);
    
    // Validate structure
    if (!data.prompt || !data.prompt.type || !['monolith', 'assembled'].includes(data.prompt.type)) {
      throw new Error('Invalid prompt format: missing or invalid prompt.type');
    }
    
    const promptName = data.name || this.currentPrompt;
    const existingPrompts = await API.listPrompts();
    const promptExists = existingPrompts.some(p => p.name === promptName);
    
    // Import components first (if any)
    if (data.components) {
      let imported = 0, skipped = 0;
      
      for (const [type, items] of Object.entries(data.components)) {
        for (const [key, value] of Object.entries(items)) {
          const exists = this.components[type]?.[key];
          if (exists && !overwrite) {
            skipped++;
            continue;
          }
          try {
            await API.saveComponent(type, key, value);
            imported++;
          } catch (e) {
            console.warn(`Failed to import ${type}.${key}:`, e);
          }
        }
      }
      
      if (imported > 0 || skipped > 0) {
        showToast(`Components: ${imported} imported, ${skipped} skipped`, 'info');
      }
    }
    
    // Import prompt
    let promptImported = false;
    if (promptExists && !overwrite) {
      showToast(`Prompt "${promptName}" exists - check overwrite to replace`, 'warning');
    } else {
      await API.savePrompt(promptName, data.prompt);
      promptImported = true;
      showToast(`Prompt "${promptName}" ${promptExists ? 'updated' : 'created'}!`, 'success');
    }
    
    // Force-reset guards before reloading
    this._listLoadInProgress = false;
    this._loadInProgress = false;
    
    // Reload everything
    await this.loadComponents();
    await this.loadPromptList();
    
    // Always activate and load the imported prompt
    if (promptImported) {
      console.log(`[PromptManager] Activating imported prompt: ${promptName}`);
      await API.loadPrompt(promptName);
      await this.loadPromptIntoEditor(promptName);
      this.elements.select.value = promptName;
      await updateScene();
    }
  }
};