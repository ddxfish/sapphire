// Prompt Manager Plugin - index.js
// Simplified: Editor always shows active prompt. Select = activate. Edit = auto-save & apply.
import { injectStyles } from './prompt-styles.js';
import { showToast } from '../../shared/toast.js';
import { showModal } from '../../shared/modal.js';
import * as API from './prompt-api.js';
import { buildMainUI, buildEditor } from './prompt-ui-builder.js';
import { getInitDataSync } from '../../shared/init-data.js';
import { fetchWithTimeout } from '../../shared/fetch.js';

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
    this._lastEditorSaveTime = 0;
    
    this.bindEvents();
    await this.loadComponents();
    await this.loadPromptList();
    
    // Read active prompt from init data (server source of truth)
    const initData = getInitDataSync();
    const activeName = initData?.prompts?.current_name;
    if (activeName) {
      const option = Array.from(this.elements.select.options).find(o => o.value === activeName);
      if (option) {
        this.elements.select.value = activeName;
        await this.loadPromptIntoEditor(activeName);
      }
    }
    
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
      
      // Prompt changed - sync editor to the changed prompt
      this._promptChangedUnsub = eventBus.on('prompt_changed', async (data) => {
        console.log('[PromptManager] SSE: prompt_changed', data);
        if (!this._loadInProgress) {
          if (data?.bulk) {
            await this.loadComponents();
            await this.loadPromptList();
            await this._syncFromServer();
          } else if (data?.name && data?.action === 'loaded') {
            // A different prompt was activated - switch to it
            if (this.currentPrompt !== data.name) {
              await this.loadPromptList();
              const option = Array.from(this.elements.select.options).find(o => o.value === data.name);
              if (option) {
                console.log(`[PromptManager] Switching editor to: ${data.name}`);
                this.elements.select.value = data.name;
                await this.loadPromptIntoEditor(data.name);
              }
            }
          } else if (data?.name && data?.action === 'saved') {
            // Content was saved (by AI tools, another tab, etc.)
            // Reload editor if viewing this prompt, unless WE just saved it
            if (data.name === this.currentPrompt && !this._userIsEditing() &&
                Date.now() - this._lastEditorSaveTime > 2000) {
              console.log(`[PromptManager] External save detected, reloading: ${data.name}`);
              await this.loadPromptIntoEditor(data.name);
            }
            await this.loadPromptList();
          } else {
            await this.loadPromptList();
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
    await this._syncFromServer();
    showToast('Prompts refreshed', 'info');
  },
  
  async _syncFromServer() {
    try {
      const status = await fetchWithTimeout('/api/status', {}, 5000);
      if (status?.prompt_name && status.prompt_name !== this.currentPrompt) {
        const option = Array.from(this.elements.select.options).find(o => o.value === status.prompt_name);
        if (option) {
          this.elements.select.value = status.prompt_name;
          await this.loadPromptIntoEditor(status.prompt_name);
        }
      }
    } catch (e) {
      console.warn('[PromptManager] _syncFromServer failed:', e);
    }
  },

  startStatusWatcher() {
    // Poll every 60s as fallback when SSE is down
    this.statusCheckInterval = setInterval(async () => {
      if (window.eventBus?.isConnected?.()) return;
      if (this._loadInProgress) return;
      if (Date.now() - this._lastLoadTime < 5000) return;

      try {
        // Sync active prompt from server (not pill DOM)
        const status = await fetchWithTimeout('/api/status', {}, 5000);
        if (status?.prompt_name && status.prompt_name !== this.currentPrompt) {
          this._loadInProgress = true;
          try {
            this.elements.select.value = status.prompt_name;
            await this.loadComponents();
            await this.loadPromptIntoEditor(status.prompt_name);
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
    }, 60000);
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
        const lockIcon = p.privacy_required ? '🔒 ' : '';
        opt.textContent = `${lockIcon}${p.name} ${typeLabel}`;
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
      // Don't wipe the dropdown on error - leave existing items
      // Only show toast if dropdown is empty (first load failure)
      if (this.elements.select.options.length <= 1) {
        this.elements.select.innerHTML = '<option value="">Error loading prompts</option>';
        showToast('Failed to load prompts', 'error');
      }
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

    const previousPrompt = this.currentPrompt;

    try {
      // ACTIVATE the prompt immediately
      await API.loadPrompt(name);
      await updateScene();

      // Then load into editor
      await this.loadPromptIntoEditor(name);

      showToast(`Prompt: ${name}`, 'success');
    } catch (e) {
      console.error('Failed to switch prompt:', e);

      // Reset dropdown to previous selection on failure
      if (previousPrompt) {
        this.elements.select.value = previousPrompt;
      }

      // Show specific error for privacy requirement
      if (e.privacyRequired) {
        showToast(`🔒 ${e.message}`, 'error');
      } else {
        showToast(`Failed: ${e.message}`, 'error');
      }
    }
  },
  
  async loadPromptIntoEditor(name) {
    // Skip if already loading this prompt or if load in progress
    if (this._loadInProgress && this.currentPrompt === name) return;
    
    try {
      const data = await API.getPrompt(name);

      // Strip dead component references (imported from another system)
      if (data.type === 'assembled' && data.components && this.components) {
        for (const [type, value] of Object.entries(data.components)) {
          if (!Array.isArray(value)) continue;
          const available = this.components[type];
          if (!available) continue;
          const cleaned = value.filter(k => k in available);
          if (cleaned.length !== value.length) {
            const dead = value.filter(k => !(k in available));
            console.warn(`[PromptManager] Stripped dead ${type} refs:`, dead);
            data.components[type] = cleaned;
          }
        }
      }

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

    // Privacy checkbox triggers immediate save
    const privacyCheckbox = document.getElementById('pm-privacy-required');
    if (privacyCheckbox) {
      privacyCheckbox.addEventListener('change', () => this.autoSaveMonolith());
    }
  },
  
  async autoSaveMonolith() {
    if (!this.currentPrompt || this.currentData?.type !== 'monolith') return;

    const textarea = document.getElementById('pm-content');
    if (!textarea) return;

    const privacyRequired = document.getElementById('pm-privacy-required')?.checked || false;

    const data = {
      name: this.currentPrompt,
      type: 'monolith',
      content: textarea.value,
      privacy_required: privacyRequired
    };

    try {
      this._lastEditorSaveTime = Date.now();
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
      this._lastEditorSaveTime = Date.now();
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
      await this._syncFromServer();
      showToast('Refreshed', 'success');
    } catch (e) {
      console.error('Refresh failed:', e);
      showToast('Refresh failed', 'error');
    }
  },
  
  bindComponentButtons() {
    // Auto-save when any component dropdown changes
    const componentSelects = ['character', 'location', 'goals', 'relationship', 'format', 'scenario'];
    componentSelects.forEach(type => {
      const select = document.getElementById(`pm-${type}`);
      if (select) {
        select.addEventListener('change', () => this._handleComponentChange(type, select.value));
      }
    });

    // All pencil buttons use unified modal handler
    this.elements.editor.querySelectorAll('.pm-component-edit').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        this.handleComponentModal(btn.dataset.type);
      });
    });

    // Privacy checkbox triggers immediate save for assembled prompts
    const privacyCheckbox = document.getElementById('pm-privacy-required');
    if (privacyCheckbox) {
      privacyCheckbox.addEventListener('change', async () => {
        const data = this.collectData();
        if (!data) return;
        try {
          this._lastEditorSaveTime = Date.now();
          await API.savePrompt(this.currentPrompt, data);
          await API.loadPrompt(this.currentPrompt);
          await updateScene();
        } catch (e) {
          console.warn('Privacy save failed:', e);
        }
      });
    }
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
            : { character: 'sapphire', location: 'default', goals: 'none', relationship: 'friend', format: 'conversational', scenario: 'default', extras: [], emotions: [] }
      };
      
      try {
        console.log(`[PromptManager] Creating prompt: ${name}`);
        await API.savePrompt(name, promptData);

        // Force-reset guard and refresh list
        this._listLoadInProgress = false;
        await this.loadPromptList();

        // Verify option exists in dropdown (defensive)
        let option = Array.from(this.elements.select.options).find(o => o.value === name);
        if (!option) {
          // Option missing - manually add it
          console.warn(`[PromptManager] Option not found after refresh, adding manually`);
          option = document.createElement('option');
          option.value = name;
          option.textContent = `${name} (${promptData.type === 'assembled' ? 'A' : 'M'})`;
          this.elements.select.appendChild(option);
        }

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
    const privacyRequired = document.getElementById('pm-privacy-required')?.checked || false;

    if (type === 'monolith') {
      return {
        name: this.currentPrompt,
        type: 'monolith',
        content: document.getElementById('pm-content')?.value || '',
        privacy_required: privacyRequired
      };
    } else if (type === 'assembled') {
      return {
        name: this.currentPrompt,
        type: 'assembled',
        privacy_required: privacyRequired,
        components: {
          character: document.getElementById('pm-character')?.value || 'default',
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
      const types = ['character', 'location', 'goals', 'relationship', 'format', 'scenario'];
      
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

      showModal('Paste JSON', [
        { id: 'import-json', label: 'Paste exported prompt JSON below:', type: 'textarea', value: '', rows: 12 }
      ], async (data) => {
        const text = data['import-json']?.trim();
        if (!text) {
          showToast('No JSON provided', 'error');
          return;
        }
        await this._promptAndImport(text, overwrite, close);
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
          await this._promptAndImport(text, overwrite, close);
        } catch (e) {
          showToast(`Import failed: ${e.message}`, 'error');
        }
      };
      input.click();
    });
  },
  
  async _promptAndImport(jsonText, overwrite, closeParentModal) {
    // Parse first to get original name
    let data;
    try {
      data = JSON.parse(jsonText);
    } catch (e) {
      showToast('Invalid JSON format', 'error');
      return;
    }

    const originalName = data.name || 'imported_prompt';

    // Small delay to let the Paste JSON modal finish closing
    await new Promise(r => setTimeout(r, 350));

    // Ask user for import name
    showModal('Import As', [
      { id: 'import-name', label: 'Prompt name:', type: 'text', value: originalName }
    ], async (formData) => {
      const newName = formData['import-name']?.trim();
      if (!newName) {
        showToast('Name is required', 'error');
        return;
      }
      try {
        await this._importPromptData(jsonText, overwrite, newName);
        closeParentModal();
      } catch (e) {
        showToast(`Import failed: ${e.message}`, 'error');
      }
    });
  },

  async _importPromptData(jsonText, overwrite = false, nameOverride = null) {
    const data = JSON.parse(jsonText);

    // Validate structure
    if (!data.prompt || !data.prompt.type || !['monolith', 'assembled'].includes(data.prompt.type)) {
      throw new Error('Invalid prompt format: missing or invalid prompt.type');
    }

    const promptName = nameOverride || data.name || this.currentPrompt;

    // Import components (always import new; overwrite controls existing)
    if (data.components) {
      const newItems = {};
      const conflicts = [];
      for (const [type, items] of Object.entries(data.components)) {
        for (const [key, value] of Object.entries(items)) {
          if (this.components[type]?.[key]) {
            conflicts.push({ type, key, value, label: `${type}: ${key}` });
          } else {
            if (!newItems[type]) newItems[type] = {};
            newItems[type][key] = value;
          }
        }
      }

      // Confirm overwrite for existing components
      if (overwrite && conflicts.length > 0) {
        const msg = `The following components already exist and will be OVERWRITTEN:\n\n${conflicts.map(c => c.label).join('\n')}\n\nProceed?`;
        if (!confirm(msg)) {
          showToast('Import cancelled', 'info');
          return;
        }
      }

      let imported = 0;
      // Always import new components
      for (const [type, items] of Object.entries(newItems)) {
        for (const [key, value] of Object.entries(items)) {
          try {
            await API.saveComponent(type, key, value);
            imported++;
          } catch (e) {
            console.warn(`Failed to import ${type}.${key}:`, e);
          }
        }
      }
      // Overwrite existing only if checkbox is on
      if (overwrite) {
        for (const { type, key, value } of conflicts) {
          try {
            await API.saveComponent(type, key, value);
            imported++;
          } catch (e) {
            console.warn(`Failed to import ${type}.${key}:`, e);
          }
        }
      }

      if (imported > 0) {
        const skipped = overwrite ? 0 : conflicts.length;
        showToast(`${imported} component(s) imported${skipped ? `, ${skipped} existing skipped` : ''}`, 'info');
      } else if (conflicts.length > 0 && !overwrite) {
        showToast(`${conflicts.length} component(s) already exist (check overwrite to replace)`, 'info');
      }
    }

    // Save prompt (always — user chose the name via Import As dialog)
    await API.savePrompt(promptName, data.prompt);
    showToast(`Prompt "${promptName}" imported!`, 'success');
    const promptImported = true;

    // Force-reset guards before reloading
    this._listLoadInProgress = false;
    this._loadInProgress = false;

    // Reload everything
    await this.loadComponents();
    await this.loadPromptList();

    // Always activate and load the imported prompt
    if (promptImported) {
      await API.loadPrompt(promptName);
      await this.loadPromptIntoEditor(promptName);
      this.elements.select.value = promptName;
      await updateScene();
    }
  },

  handleComponentModal(type) {
    if (!this.currentData || this.currentData.type !== 'assembled') return;

    const isMultiSelect = type === 'extras' || type === 'emotions';
    const availableItems = this.components[type] || {};
    const typeLabel = type.charAt(0).toUpperCase() + type.slice(1);

    // Get current selection
    let currentSelected;
    if (isMultiSelect) {
      currentSelected = this.currentData.components?.[type] || [];
    } else {
      currentSelected = this.currentData.components?.[type] || '';
    }

    const overlay = document.createElement('div');
    overlay.className = 'modal-overlay modal-wide';

    // Build row HTML helper
    const buildRowHTML = (key, value, selected) => {
      const escapedValue = value.replace(/</g, '&lt;').replace(/>/g, '&gt;');
      const inputType = isMultiSelect ? 'checkbox' : 'radio';
      const inputName = isMultiSelect ? '' : 'name="component-select"';
      const isChecked = isMultiSelect
        ? (selected.includes(key) ? 'checked' : '')
        : (selected === key ? 'checked' : '');

      return `
        <div class="combined-edit-row" data-key="${key}">
          <input type="${inputType}" ${inputName} class="combined-edit-check" data-key="${key}" ${isChecked}>
          <div class="combined-edit-content">
            <label class="combined-edit-label">${key}</label>
            <textarea class="combined-edit-textarea" data-key="${key}" rows="4">${escapedValue}</textarea>
          </div>
          <button class="combined-edit-delete" data-key="${key}" title="Delete ${key}">✕</button>
        </div>
      `;
    };

    const buildListHTML = (items, selected) => {
      const keys = Object.keys(items).sort((a, b) => a.localeCompare(b));
      if (keys.length === 0) {
        return '<div style="color:var(--text-muted);padding:12px;text-align:center;">No items yet. Click + to add one.</div>';
      }
      return keys.map(key => buildRowHTML(key, items[key], selected)).join('');
    };

    const helpText = isMultiSelect
      ? 'Check to enable, edit text. Red ✕ deletes immediately.'
      : 'Select one, edit text. Red ✕ deletes immediately.';

    overlay.innerHTML = `
      <div class="modal-base">
        <div class="modal-header">
          <h3>Edit ${typeLabel}</h3>
          <button class="close-btn modal-x">&times;</button>
        </div>
        <div class="modal-body">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
            <span style="color:var(--text-muted);font-size:var(--font-sm);">${helpText}</span>
            <button class="btn btn-secondary combined-edit-add" style="padding:4px 12px;">+ Add New</button>
          </div>
          <div class="combined-edit-list">${buildListHTML(availableItems, currentSelected)}</div>
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

    // Track current selection for refreshes
    let liveSelected = isMultiSelect ? [...currentSelected] : currentSelected;

    // Refresh modal using components data (from API response, no refetch needed)
    const refreshModal = (componentsData, newKey = null) => {
      console.log(`[ComponentModal] Refreshing ${type} from response data`);

      // Update the cached components
      this.components = componentsData;
      const newItems = componentsData[type] || {};
      const newKeys = Object.keys(newItems).sort((a, b) => a.localeCompare(b));
      console.log(`[ComponentModal] Got ${newKeys.length} items:`, newKeys);

      // Clean up selection to only valid keys
      if (isMultiSelect) {
        liveSelected = liveSelected.filter(k => newKeys.includes(k));
        // Auto-select new item if provided
        if (newKey && newKeys.includes(newKey) && !liveSelected.includes(newKey)) {
          liveSelected.push(newKey);
        }
      } else {
        if (newKey && newKeys.includes(newKey)) {
          liveSelected = newKey;
        } else if (!newKeys.includes(liveSelected)) {
          liveSelected = newKeys[0] || '';
        }
      }

      // Rebuild modal list
      const listEl = overlay.querySelector('.combined-edit-list');
      if (listEl) {
        listEl.innerHTML = buildListHTML(newItems, liveSelected);
        bindDeleteHandlers();
      }

      // Also update the UI in the main editor
      if (!isMultiSelect) {
        // Update dropdown for single-select types
        const selectEl = document.getElementById(`pm-${type}`);
        if (selectEl) {
          const currentVal = liveSelected;
          selectEl.innerHTML = newKeys.map(k =>
            `<option value="${k}" ${k === currentVal ? 'selected' : ''}>${k}</option>`
          ).join('') || `<option value="">No ${type}s</option>`;
        }
      } else {
        // Update display div for multi-select types
        const displayEl = document.getElementById(`pm-${type}-display`);
        if (displayEl) {
          displayEl.textContent = liveSelected.length > 0 ? liveSelected.join(', ') : 'none';
        }
      }
    };

    // Bind delete handlers
    const bindDeleteHandlers = () => {
      overlay.querySelectorAll('.combined-edit-delete').forEach(btn => {
        btn.addEventListener('click', async () => {
          const key = btn.dataset.key;
          if (!confirm(`Delete "${key}"?\n\nThis cannot be undone.`)) return;

          try {
            const result = await API.deleteComponent(type, key);
            showToast(`Deleted ${key}`, 'success');

            // Remove from selection
            if (isMultiSelect) {
              liveSelected = liveSelected.filter(k => k !== key);
            } else if (liveSelected === key) {
              liveSelected = '';
            }

            // Use returned components data instead of refetching
            if (result.components) {
              refreshModal(result.components);
            }
          } catch (e) {
            console.error(`Failed to delete ${type}.${key}:`, e);
            showToast(`Failed to delete ${key}`, 'error');
          }
        });
      });
    };
    bindDeleteHandlers();

    // Add new component inline
    const addNewInline = () => {
      const listEl = overlay.querySelector('.combined-edit-list');
      if (!listEl || listEl.querySelector('.new-piece-row')) return; // one at a time

      const row = document.createElement('div');
      row.className = 'combined-edit-row new-piece-row';
      row.innerHTML = `
        <div class="combined-edit-content">
          <input type="text" class="new-piece-name" placeholder="Piece name" style="width:100%;padding:6px 8px;background:var(--input-bg);border:1px solid var(--input-focus-border);border-radius:var(--radius-sm);color:var(--text-light);font-size:var(--font-sm);font-weight:600;font-family:var(--font-mono);">
          <textarea class="new-piece-text" placeholder="Piece content" rows="4" style="width:100%;padding:8px;background:var(--input-bg);border:1px solid var(--input-border);border-radius:var(--radius-sm);color:var(--text);font-size:var(--font-sm);font-family:var(--font-mono);resize:vertical;min-height:80px;"></textarea>
        </div>
        <button class="btn btn-primary new-piece-confirm" style="padding:4px 12px;align-self:flex-start;margin-top:2px;">Add</button>
      `;
      listEl.prepend(row);
      row.querySelector('.new-piece-name').focus();

      const confirm = async () => {
        const name = row.querySelector('.new-piece-name').value.trim();
        const text = row.querySelector('.new-piece-text').value.trim();
        if (!name || !text) { showToast('Name and content required', 'error'); return; }

        try {
          const result = await API.saveComponent(type, name, text);
          showToast(`${typeLabel} added!`, 'success');
          // Auto-select the new item
          if (isMultiSelect) {
            if (!liveSelected.includes(name)) liveSelected.push(name);
          } else {
            liveSelected = name;
          }
          if (result.components) refreshModal(result.components, name);
        } catch (e) {
          showToast(`Failed: ${e.message}`, 'error');
        }
      };

      row.querySelector('.new-piece-confirm').addEventListener('click', confirm);
      row.querySelector('.new-piece-name').addEventListener('keydown', e => {
        if (e.key === 'Enter') { e.preventDefault(); row.querySelector('.new-piece-text').focus(); }
      });
      row.querySelector('.new-piece-text').addEventListener('keydown', e => {
        if (e.key === 'Enter' && e.ctrlKey) { e.preventDefault(); confirm(); }
      });
    };
    overlay.querySelector('.combined-edit-add')?.addEventListener('click', addNewInline);

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

    overlay.querySelector('.modal-save')?.addEventListener('click', async () => {
      // Auto-add any pending new piece before saving
      const pendingRow = overlay.querySelector('.new-piece-row');
      if (pendingRow) {
        const name = pendingRow.querySelector('.new-piece-name')?.value.trim();
        const text = pendingRow.querySelector('.new-piece-text')?.value.trim();
        if (name && text) {
          pendingRow.querySelector('.new-piece-confirm')?.click();
          // Wait for API save + modal refresh
          await new Promise(r => setTimeout(r, 500));
        }
      }

      const textChanges = {};
      let selectedValue;

      if (isMultiSelect) {
        selectedValue = [];
        overlay.querySelectorAll('.combined-edit-row').forEach(row => {
          const key = row.dataset.key;
          const checkbox = row.querySelector('.combined-edit-check');
          const textarea = row.querySelector('.combined-edit-textarea');
          const newValue = textarea.value.trim();
          const originalValue = this.components[type]?.[key];

          if (checkbox.checked) {
            selectedValue.push(key);
          }

          if (newValue && newValue !== originalValue) {
            textChanges[key] = newValue;
          }
        });
      } else {
        // Single select - find the checked radio
        const checkedRadio = overlay.querySelector('.combined-edit-check:checked');
        selectedValue = checkedRadio?.dataset.key || '';

        overlay.querySelectorAll('.combined-edit-row').forEach(row => {
          const key = row.dataset.key;
          const textarea = row.querySelector('.combined-edit-textarea');
          const newValue = textarea.value.trim();
          const originalValue = this.components[type]?.[key];

          if (newValue && newValue !== originalValue) {
            textChanges[key] = newValue;
          }
        });
      }

      close();

      // Save text changes - use returned components from last save
      let textSaved = 0;
      let latestComponents = null;
      for (const [key, value] of Object.entries(textChanges)) {
        try {
          const result = await API.saveComponent(type, key, value);
          if (result.components) {
            latestComponents = result.components;
          }
          textSaved++;
        } catch (e) {
          console.error(`Failed to save ${type}.${key}:`, e);
        }
      }
      if (textSaved > 0) {
        showToast(`Updated ${textSaved} item(s)`, 'success');
      }

      // Update cached components from API response
      if (latestComponents) {
        this.components = latestComponents;
      }

      // Update selection — sync in-memory, DOM, then save
      this.currentData.components[type] = selectedValue;
      if (!isMultiSelect) {
        const selectEl = document.getElementById(`pm-${type}`);
        if (selectEl) {
          // Ensure option exists before setting value
          if (!Array.from(selectEl.options).some(o => o.value === selectedValue)) {
            selectEl.add(new Option(selectedValue, selectedValue));
          }
          selectEl.value = selectedValue;
        }
      }
      const promptData = this.collectData();
      // Double-check component was set (collectData reads DOM which can desync)
      if (promptData?.components) {
        promptData.components[type] = selectedValue;
      }
      if (promptData) {
        try {
          this._lastEditorSaveTime = Date.now();
          await API.savePrompt(this.currentPrompt, promptData);
          await API.loadPrompt(this.currentPrompt);
        } catch (e) {
          console.warn('Failed to save selection:', e);
        }
      }

      // Refresh editor and scene
      await this.loadPromptIntoEditor(this.currentPrompt);
      await updateScene();
    });
  }
};