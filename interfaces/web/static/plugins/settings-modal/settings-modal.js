// settings-modal.js - Settings modal UI orchestrator
import settingsAPI from './settings-api.js';
import { TABS, CATEGORIES } from './settings-categories.js';
import { showToast, showActionToast } from '../../shared/toast.js';

class SettingsModal {
  constructor() {
    this.modal = null;
    this.settings = null;
    this.userOverrides = [];
    this.help = {};
    this.currentTab = 'identity';
    this.pendingChanges = {};
    // Theme state
    this.availableThemes = [];
    this.originalTheme = null;
    this.currentTheme = null;
    // Avatar paths
    this.avatarPaths = { user: null, assistant: null };
    // Wakeword models
    this.wakewordModels = ['alexa', 'hey_mycroft', 'hey_jarvis', 'hey_rhasspy', 'timer', 'weather'];
  }

  async open() {
    this.originalTheme = localStorage.getItem('sapphire-theme') || 'dark';
    this.currentTheme = this.originalTheme;
    
    await this.loadData();
    this.render();
    this.attachEventListeners();
  }

  async loadData() {
    try {
      const [settingsData, helpData] = await Promise.all([
        settingsAPI.getAllSettings(),
        settingsAPI.getSettingsHelp().catch(() => ({ help: {} }))
      ]);
      
      this.settings = settingsData.settings;
      this.userOverrides = settingsData.user_overrides || [];
      this.help = helpData.help || {};
      
      await this.loadThemes();
      await this.loadWakewordModels();
      await this.loadAvatarPaths();
    } catch (e) {
      console.error('Failed to load settings:', e);
      showToast('Failed to load settings: ' + e.message, 'error');
    }
  }

  async loadAvatarPaths() {
    try {
      const [userCheck, assistantCheck] = await Promise.all([
        settingsAPI.checkAvatar('user').catch(() => ({ exists: false })),
        settingsAPI.checkAvatar('assistant').catch(() => ({ exists: false }))
      ]);
      
      this.avatarPaths.user = userCheck.exists ? userCheck.path : '/static/users/user.png';
      this.avatarPaths.assistant = assistantCheck.exists ? assistantCheck.path : '/static/users/assistant.png';
    } catch (e) {
      console.warn('Failed to check avatars:', e);
    }
  }

  async loadThemes() {
    try {
      const res = await fetch('/static/themes/themes.json');
      if (res.ok) {
        const data = await res.json();
        this.availableThemes = data.themes || ['dark'];
      } else {
        this.availableThemes = ['dark'];
      }
    } catch (e) {
      console.warn('Could not load themes.json, using defaults:', e);
      this.availableThemes = ['dark'];
    }
  }

  async loadWakewordModels() {
    try {
      const res = await fetch('/api/settings/wakeword-models');
      if (res.ok) {
        const data = await res.json();
        this.wakewordModels = data.all || this.wakewordModels;
      }
    } catch (e) {
      console.warn('Could not load wakeword models, using defaults:', e);
    }
  }

  render() {
    this.modal = document.createElement('div');
    this.modal.className = 'settings-modal-overlay';
    this.modal.innerHTML = `
      <div class="settings-modal">
        <div class="settings-modal-header">
          <h2>Settings</h2>
          <button class="close-btn" id="settings-close">×</button>
        </div>
        
        <div class="settings-modal-tabs">
          ${this.renderTabs()}
        </div>
        
        <div class="settings-modal-content">
          ${this.renderTabContent()}
        </div>
        
        <div class="settings-modal-footer">
          <button class="btn btn-secondary" id="settings-reload">Reload</button>
          <button class="btn btn-primary" id="settings-save">Save Changes</button>
        </div>
      </div>
    `;
    
    document.body.appendChild(this.modal);
    
    requestAnimationFrame(() => {
      this.modal.classList.add('active');
    });
  }

  renderTabs() {
    return TABS.map(tab => `
      <button 
        class="tab-btn ${tab.id === this.currentTab ? 'active' : ''}" 
        data-tab="${tab.id}"
      >
        <span class="tab-icon">${tab.icon}</span>
        <span class="tab-label">${tab.name}</span>
      </button>
    `).join('');
  }

  renderTabContent() {
    return TABS.map(tab => `
      <div class="tab-content ${tab.id === this.currentTab ? 'active' : ''}" data-tab="${tab.id}">
        <div class="tab-header">
          <h3>${tab.icon} ${tab.name}</h3>
          <p>${tab.description}</p>
        </div>
        ${tab.render(this)}
      </div>
    `).join('');
  }

  // Generic settings renderer - used by tabs
  renderCategorySettings(keys) {
    return keys.map(key => {
      const value = this.settings[key];
      if (value === undefined) return '';
      
      const isOverridden = this.userOverrides.includes(key);
      const inputType = settingsAPI.getInputType(value);
      const helpText = this.help[key];
      
      return `
        <div class="setting-row ${isOverridden ? 'overridden' : ''}" data-key="${key}">
          <div class="setting-label">
            <div class="label-with-help">
              <label for="setting-${key}">${this.formatLabel(key)}</label>
              ${helpText ? `<span class="help-icon" data-key="${key}" title="Click for full details">?</span>` : ''}
            </div>
            ${isOverridden ? '<span class="override-badge">Custom</span>' : ''}
            ${helpText ? `<div class="help-text-short">${helpText.short}</div>` : ''}
          </div>
          
          <div class="setting-input">
            ${this.renderInput(key, value, inputType)}
          </div>
          
          <div class="setting-actions">
            ${isOverridden ? `<button class="btn-icon reset-btn" data-key="${key}" title="Reset to default">↺</button>` : ''}
          </div>
        </div>
      `;
    }).join('');
  }

  /**
   * Render a collapsible advanced settings accordion.
   * @param {string} id - Unique ID for this accordion (e.g., 'audio-advanced')
   * @param {string[]} keys - Setting keys to render inside
   * @param {string} title - Accordion header text (default: 'Advanced Settings')
   * @returns {string} HTML string
   */
  renderAdvancedAccordion(id, keys, title = 'Advanced Settings') {
    return `
      <div class="advanced-accordion-section" data-accordion="${id}">
        <div class="accordion-header collapsed" data-accordion-toggle="${id}">
          <span class="accordion-toggle collapsed"></span>
          <h4>${title}</h4>
        </div>
        <div class="accordion-content collapsed" data-accordion-content="${id}">
          <div class="settings-list">
            ${this.renderCategorySettings(keys)}
          </div>
        </div>
      </div>
    `;
  }

  /**
   * Attach click listeners to all accordions in a container.
   * Call this in tab's attachListeners() method.
   * @param {HTMLElement} container - The tab content container
   */
  attachAccordionListeners(container) {
    container.querySelectorAll('[data-accordion-toggle]').forEach(header => {
      header.addEventListener('click', () => {
        const id = header.dataset.accordionToggle;
        const content = container.querySelector(`[data-accordion-content="${id}"]`);
        const toggle = header.querySelector('.accordion-toggle');
        
        header.classList.toggle('collapsed');
        if (content) content.classList.toggle('collapsed');
        if (toggle) toggle.classList.toggle('collapsed');
      });
    });
  }

  renderInput(key, value, type) {
    const inputId = `setting-${key}`;
    
    // Special: WAKEWORD_MODEL dropdown
    if (key === 'WAKEWORD_MODEL') {
      const options = this.wakewordModels.map(m => {
        const display = m.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
        return `<option value="${m}" ${value === m ? 'selected' : ''}>${display}</option>`;
      }).join('');
      return `<select id="${inputId}" data-key="${key}">${options}</select>`;
    }
    
    // Special: WAKEWORD_FRAMEWORK dropdown
    if (key === 'WAKEWORD_FRAMEWORK') {
      const frameworks = ['onnx', 'tflite'];
      const options = frameworks.map(f =>
        `<option value="${f}" ${value === f ? 'selected' : ''}>${f.toUpperCase()}</option>`
      ).join('');
      return `<select id="${inputId}" data-key="${key}">${options}</select>`;
    }
    
    if (type === 'checkbox') {
      return `
        <label class="checkbox-container">
          <input type="checkbox" id="${inputId}" data-key="${key}" ${value ? 'checked' : ''}>
          <span class="checkbox-label">${value ? 'Enabled' : 'Disabled'}</span>
        </label>
      `;
    }
    
    if (type === 'json') {
      const jsonStr = JSON.stringify(value, null, 2);
      return `
        <textarea 
          id="${inputId}" 
          data-key="${key}" 
          class="json-input"
          rows="4"
        >${jsonStr}</textarea>
      `;
    }
    
    if (type === 'number') {
      return `
        <input 
          type="number" 
          id="${inputId}" 
          data-key="${key}" 
          value="${value}"
          step="any"
        >
      `;
    }
    
    return `
      <input 
        type="text" 
        id="${inputId}" 
        data-key="${key}" 
        value="${value}"
      >
    `;
  }

  formatLabel(key) {
    return key
      .replace(/_/g, ' ')
      .split(' ')
      .map(word => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase())
      .join(' ');
  }

  attachEventListeners() {
    // Core modal listeners
    this.modal.querySelector('#settings-close').addEventListener('click', () => this.close());
    
    this.modal.addEventListener('click', (e) => {
      if (e.target === this.modal) this.close();
    });
    
    this.escHandler = (e) => {
      if (e.key === 'Escape') this.close();
    };
    document.addEventListener('keydown', this.escHandler);
    
    // Tab switching
    this.modal.querySelectorAll('.tab-btn').forEach(btn => {
      btn.addEventListener('click', () => this.switchTab(btn.dataset.tab));
    });
    
    // Settings inputs
    this.modal.querySelectorAll('input, textarea, select').forEach(input => {
      input.addEventListener('change', (e) => this.handleInputChange(e));
    });
    
    // Reset buttons
    this.modal.querySelectorAll('.reset-btn').forEach(btn => {
      btn.addEventListener('click', () => this.resetSetting(btn.dataset.key));
    });
    
    // Help icons
    this.modal.querySelectorAll('.help-icon').forEach(icon => {
      icon.addEventListener('click', (e) => {
        e.stopPropagation();
        this.showHelpDetails(icon.dataset.key);
      });
    });
    
    // Footer buttons
    this.modal.querySelector('#settings-save').addEventListener('click', () => this.saveChanges());
    this.modal.querySelector('#settings-reload').addEventListener('click', () => this.reloadSettings());
    
    // Tab-specific listeners
    for (const tab of TABS) {
      if (tab.attachListeners) {
        const contentEl = this.modal.querySelector(`.tab-content[data-tab="${tab.id}"]`);
        if (contentEl) {
          tab.attachListeners(this, contentEl);
        }
      }
    }
  }

  switchTab(tabId) {
    this.currentTab = tabId;
    
    this.modal.querySelectorAll('.tab-btn').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.tab === tabId);
    });
    
    this.modal.querySelectorAll('.tab-content').forEach(content => {
      content.classList.toggle('active', content.dataset.tab === tabId);
    });
  }

  handleInputChange(e) {
    const key = e.target.dataset.key;
    // Ignore inputs without data-key (like LLM provider fields that use data-provider)
    if (!key || key === 'undefined') return;
    
    const value = e.target.type === 'checkbox' ? e.target.checked : e.target.value;
    
    this.pendingChanges[key] = value;
    
    const row = e.target.closest('.setting-row');
    if (row) {
      row.classList.add('modified');
    }
  }

  async saveChanges() {
    // Save theme to localStorage
    if (this.currentTheme !== this.originalTheme) {
      localStorage.setItem('sapphire-theme', this.currentTheme);
      this.originalTheme = this.currentTheme;
      showToast(`Theme saved: ${this.currentTheme}`, 'success');
    }
    
    // Filter out any invalid keys before checking length
    const validChanges = {};
    for (const [key, value] of Object.entries(this.pendingChanges)) {
      if (key && key !== 'undefined' && key !== 'null') {
        validChanges[key] = value;
      }
    }
    this.pendingChanges = validChanges;
    
    if (Object.keys(this.pendingChanges).length === 0) {
      if (this.currentTheme === this.originalTheme) {
        showToast('No changes to save', 'info');
      }
      return;
    }
    
    const saveBtn = this.modal?.querySelector('#settings-save');
    if (saveBtn) {
      saveBtn.disabled = true;
      saveBtn.textContent = 'Saving...';
    }
    
    try {
      const parsedChanges = {};
      for (const [key, value] of Object.entries(this.pendingChanges)) {
        const originalValue = this.settings[key];
        parsedChanges[key] = settingsAPI.parseValue(value, originalValue);
      }
      
      const result = await settingsAPI.updateSettingsBatch(parsedChanges);
      await settingsAPI.reloadSettings();
      
      showToast(`Saved ${Object.keys(parsedChanges).length} settings`, 'success');
      
      if (result.restart_required) {
        const keys = result.restart_keys || [];
        const keyList = keys.length > 0 ? keys.join(', ') : 'some settings';
        showActionToast(
          `Restart required for: ${keyList}`,
          'Restart Now',
          () => this.triggerRestart(),
          'warning',
          0  // Persistent until dismissed
        );
      }
      
      this.pendingChanges = {};
      
      if (!this.modal) return;
      
      await this.loadData();
      this.refreshContent();
      
    } catch (e) {
      console.error('Save failed:', e);
      showToast('Save failed: ' + e.message, 'error');
    } finally {
      if (saveBtn) {
        saveBtn.disabled = false;
        saveBtn.textContent = 'Save Changes';
      }
    }
  }

  async resetSetting(key) {
    if (!confirm(`Reset "${this.formatLabel(key)}" to default?`)) return;
    
    try {
      await settingsAPI.deleteSetting(key);
      showToast(`Reset ${this.formatLabel(key)}`, 'success');
      await this.loadData();
      this.refreshContent();
    } catch (e) {
      showToast('Reset failed: ' + e.message, 'error');
    }
  }

  async reloadSettings() {
    try {
      await settingsAPI.reloadSettings();
      showToast('Settings reloaded from disk', 'success');
      await this.loadData();
      this.refreshContent();
    } catch (e) {
      showToast('Reload failed: ' + e.message, 'error');
    }
  }

  async resetAll() {
    if (!confirm('⚠️ Reset ALL settings to defaults?\n\nThis will erase all your customizations!')) return;
    
    const confirmText = prompt(
      '🚨 FINAL WARNING 🚨\n\n' +
      'This will:\n' +
      '• Delete ALL your custom settings\n' +
      '• Restore factory defaults\n' +
      '• Require a restart to take effect\n\n' +
      'Type "RESET" to confirm:'
    );
    
    if (confirmText !== 'RESET') {
      showToast('Reset cancelled', 'info');
      return;
    }
    
    try {
      await settingsAPI.resetSettings();
      showToast('✓ All settings reset. Restart Sapphire to apply.', 'success');
      await this.loadData();
      this.refreshContent();
    } catch (e) {
      showToast('Reset failed: ' + e.message, 'error');
    }
  }

  async triggerRestart() {
    try {
      await fetch('/api/system/restart', { method: 'POST' });
      document.body.innerHTML = `
        <div style="position:fixed;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:20px;font-family:system-ui,sans-serif;background:#1a1a2e;">
            <div style="font-size:1.5rem;color:#888;">Restarting Sapphire...</div>
            <div id="restart-status" style="font-size:1rem;color:#666;">Waiting for server...</div>
            <button id="manual-refresh-btn" style="display:none;padding:12px 24px;font-size:1rem;cursor:pointer;background:#4a9eff;color:white;border:none;border-radius:6px;">
                Click to Refresh
            </button>
        </div>
      `;
      
      // Show manual button after 5 seconds
      setTimeout(() => {
        const btn = document.getElementById('manual-refresh-btn');
        if (btn) {
          btn.style.display = 'block';
          btn.addEventListener('click', () => window.location.reload());
        }
      }, 5000);
      
      setTimeout(() => this.pollForServer(), 2000);
    } catch (e) {
      showToast('Restart failed: ' + e.message, 'error');
    }
  }

  pollForServer(attempts = 0) {
    const statusEl = document.getElementById('restart-status');
    const maxAttempts = 30;
    
    if (attempts >= maxAttempts) {
      if (statusEl) statusEl.textContent = 'Server may be ready. Click button to refresh.';
      return;
    }
    
    if (statusEl) statusEl.textContent = `Checking server... (${attempts + 1}/${maxAttempts})`;
    
    fetch('/api/settings', { method: 'GET' })
      .then(r => {
        if (r.ok) {
          if (statusEl) statusEl.textContent = 'Server is back! Refreshing...';
          setTimeout(() => window.location.reload(), 500);
        } else {
          setTimeout(() => this.pollForServer(attempts + 1), 1000);
        }
      })
      .catch(() => {
        setTimeout(() => this.pollForServer(attempts + 1), 1000);
      });
  }

  refreshContent() {
    if (!this.modal) return;
    
    const content = this.modal.querySelector('.settings-modal-content');
    if (!content) return;
    
    content.innerHTML = this.renderTabContent();
    
    // Re-attach input listeners
    this.modal.querySelectorAll('input, textarea, select').forEach(input => {
      input.addEventListener('change', (e) => this.handleInputChange(e));
    });
    
    this.modal.querySelectorAll('.reset-btn').forEach(btn => {
      btn.addEventListener('click', () => this.resetSetting(btn.dataset.key));
    });
    
    this.modal.querySelectorAll('.help-icon').forEach(icon => {
      icon.addEventListener('click', (e) => {
        e.stopPropagation();
        this.showHelpDetails(icon.dataset.key);
      });
    });
    
    // Re-attach tab-specific listeners
    for (const tab of TABS) {
      if (tab.attachListeners) {
        const contentEl = this.modal.querySelector(`.tab-content[data-tab="${tab.id}"]`);
        if (contentEl) {
          tab.attachListeners(this, contentEl);
        }
      }
    }
  }

  showHelpDetails(key) {
    const helpText = this.help[key];
    if (!helpText) return;
    
    const popup = document.createElement('div');
    popup.className = 'help-popup-overlay';
    popup.innerHTML = `
      <div class="help-popup">
        <div class="help-popup-header">
          <h3>${this.formatLabel(key)}</h3>
          <button class="close-btn help-popup-close">×</button>
        </div>
        <div class="help-popup-content">
          <p class="help-long">${helpText.long}</p>
          ${helpText.short ? `<p class="help-short-label"><strong>Quick Summary:</strong> ${helpText.short}</p>` : ''}
        </div>
      </div>
    `;
    
    document.body.appendChild(popup);
    
    requestAnimationFrame(() => {
      popup.classList.add('active');
    });
    
    const closePopup = () => {
      popup.classList.remove('active');
      setTimeout(() => popup.remove(), 300);
    };
    
    popup.querySelector('.help-popup-close').addEventListener('click', closePopup);
    popup.addEventListener('click', (e) => {
      if (e.target === popup) closePopup();
    });
    
    const escHandler = (e) => {
      if (e.key === 'Escape') {
        closePopup();
        document.removeEventListener('keydown', escHandler);
      }
    };
    document.addEventListener('keydown', escHandler);
  }

  close() {
    if (Object.keys(this.pendingChanges).length > 0) {
      if (!confirm('You have unsaved changes. Close anyway?')) return;
    }
    
    // Revert theme if changed but not saved
    if (this.currentTheme !== this.originalTheme) {
      document.documentElement.setAttribute('data-theme', this.originalTheme);
      // Load original theme CSS
      const existingLink = document.getElementById('theme-stylesheet');
      if (existingLink) {
        existingLink.href = `/static/themes/${this.originalTheme}.css`;
      }
    }
    
    this.modal.classList.remove('active');
    
    setTimeout(() => {
      document.removeEventListener('keydown', this.escHandler);
      this.modal.remove();
      this.modal = null;
      
      if (this.onCloseCallback) {
        this.onCloseCallback();
      }
    }, 300);
  }
}

export default SettingsModal;