// modal.js - Settings modal UI component
import settingsAPI from './settings-api.js';
import { CATEGORIES, getCategoryForKey } from './settings-categories.js';
import { showToast } from '../../shared/toast.js';

class SettingsModal {
  constructor() {
    this.modal = null;
    this.settings = null;
    this.tiers = null;
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
  }

  async open() {
    // Store original theme for cancel/revert
    this.originalTheme = localStorage.getItem('sapphire-theme') || 'dark';
    this.currentTheme = this.originalTheme;
    
    await this.loadData();
    this.render();
    this.attachEventListeners();
  }

  async loadData() {
    try {
      const [settingsData, tiersData, helpData] = await Promise.all([
        settingsAPI.getAllSettings(),
        settingsAPI.getTiers(),
        settingsAPI.getSettingsHelp().catch(() => ({ help: {} }))
      ]);
      
      this.settings = settingsData.settings;
      this.userOverrides = settingsData.user_overrides || [];
      this.tiers = tiersData.tiers;
      this.help = helpData.help || {};
      
      // Load available themes
      await this.loadThemes();
      
      // Load avatar paths
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
    return Object.entries(CATEGORIES).map(([id, cat]) => `
      <button 
        class="tab-btn ${id === this.currentTab ? 'active' : ''}" 
        data-tab="${id}"
      >
        <span class="tab-icon">${cat.icon}</span>
        <span class="tab-label">${cat.name}</span>
      </button>
    `).join('');
  }

  renderTabContent() {
    return Object.entries(CATEGORIES).map(([id, cat]) => {
      if (id === 'appearance') {
        return `
          <div class="tab-content ${id === this.currentTab ? 'active' : ''}" data-tab="${id}">
            <div class="tab-header">
              <h3>${cat.icon} ${cat.name}</h3>
              <p>${cat.description}</p>
            </div>
            ${this.renderAppearanceTab()}
          </div>
        `;
      }
      
      if (id === 'identity') {
        return `
          <div class="tab-content ${id === this.currentTab ? 'active' : ''}" data-tab="${id}">
            <div class="tab-header">
              <h3>${cat.icon} ${cat.name}</h3>
              <p>${cat.description}</p>
            </div>
            <div class="settings-list">
              ${this.renderCategorySettings(cat.keys)}
            </div>
            ${this.renderAvatarUploadSection()}
          </div>
        `;
      }
      
      if (id === 'system') {
        return `
          <div class="tab-content ${id === this.currentTab ? 'active' : ''}" data-tab="${id}">
            <div class="tab-header">
              <h3>${cat.icon} ${cat.name}</h3>
              <p>${cat.description}</p>
            </div>
            ${this.renderSystemTabContent()}
          </div>
        `;
      }
      
      return `
        <div class="tab-content ${id === this.currentTab ? 'active' : ''}" data-tab="${id}">
          <div class="tab-header">
            <h3>${cat.icon} ${cat.name}</h3>
            <p>${cat.description}</p>
          </div>
          <div class="settings-list">
            ${this.renderCategorySettings(cat.keys)}
          </div>
        </div>
      `;
    }).join('');
  }

  renderAvatarUploadSection() {
    const userPath = this.avatarPaths?.user || '/static/users/user.png';
    const assistantPath = this.avatarPaths?.assistant || '/static/users/assistant.png';
    const ts = Date.now();
    
    return `
      <div class="avatar-upload-section">
        <div class="avatar-column">
          <h4>User Avatar</h4>
          <img src="${userPath}?t=${ts}" alt="User" class="avatar-preview" id="user-avatar-preview" 
               onerror="this.src='/static/users/user.png'">
          <input type="file" id="user-avatar-input" accept=".png,.jpg,.jpeg,.gif,.webp" hidden>
          <button class="btn btn-secondary" id="user-avatar-btn">Choose File</button>
          <span class="avatar-hint">PNG, JPG, GIF, WEBP • Max 4MB</span>
        </div>
        <div class="avatar-column">
          <h4>Assistant Avatar</h4>
          <img src="${assistantPath}?t=${ts}" alt="Assistant" class="avatar-preview" id="assistant-avatar-preview"
               onerror="this.src='/static/users/assistant.png'">
          <input type="file" id="assistant-avatar-input" accept=".png,.jpg,.jpeg,.gif,.webp" hidden>
          <button class="btn btn-secondary" id="assistant-avatar-btn">Choose File</button>
          <span class="avatar-hint">PNG, JPG, GIF, WEBP • Max 4MB</span>
        </div>
      </div>
    `;
  }

  renderAppearanceTab() {
    const themeOptions = this.availableThemes.map(theme => {
      const selected = theme === this.currentTheme ? 'selected' : '';
      const displayName = theme.charAt(0).toUpperCase() + theme.slice(1);
      return `<option value="${theme}" ${selected}>${displayName}</option>`;
    }).join('');

    return `
      <div class="appearance-container">
        <div class="appearance-controls">
          <div class="setting-row">
            <div class="setting-label">
              <label for="theme-select">Theme</label>
              <div class="help-text-short">Choose your visual theme</div>
            </div>
            <div class="setting-input">
              <select id="theme-select">${themeOptions}</select>
            </div>
          </div>
        </div>
        
        <div class="theme-preview">
          <div class="preview-titlebar">
            <span class="preview-dots">
              <span class="dot red"></span>
              <span class="dot yellow"></span>
              <span class="dot green"></span>
            </span>
            <span class="preview-title">Preview</span>
          </div>
          <div class="preview-content">
            <p class="preview-text">Sample text showing how content appears with this theme.</p>
            
            <div class="preview-accordion">
              <div class="preview-accordion-header">
                <span class="preview-toggle">▼</span>
                <span>Accordion Section</span>
              </div>
              <div class="preview-accordion-body">Collapsed content area</div>
            </div>
            
            <div class="preview-buttons">
              <span class="preview-btn primary">Primary</span>
              <span class="preview-btn secondary">Secondary</span>
              <span class="preview-btn danger">Danger</span>
            </div>
            
            <div class="preview-messages">
              <div class="preview-bubble user">User message bubble</div>
              <div class="preview-bubble assistant">Assistant response bubble</div>
            </div>
            
            <div class="preview-input">
              <span class="preview-input-box">Type message...</span>
              <span class="preview-send">Send</span>
            </div>
          </div>
        </div>
      </div>
    `;
  }

  renderSystemTabContent() {
    const systemCategory = CATEGORIES.system;
    return `
      <div class="system-tab-content">
        ${systemCategory.keys.length > 0 ? `
          <div class="settings-list">
            ${this.renderCategorySettings(systemCategory.keys)}
          </div>
        ` : ''}
        
        <div class="system-danger-zone">
          <h4>Danger Zone</h4>
          <p>These actions are irreversible and will affect all settings.</p>
          <button class="btn btn-danger btn-lg" id="settings-reset-all">Reset All Settings to Defaults</button>
          <p class="warning-text">This will delete your user/settings.json file and revert everything to default values. This action cannot be undone.</p>
        </div>
      </div>
    `;
  }

  renderCategorySettings(keys) {
    return keys.map(key => {
      const value = this.settings[key];
      if (value === undefined) return '';
      
      const tier = this.getTierForKey(key);
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
            ${this.renderTierBadge(tier)}
            ${isOverridden ? `<button class="btn-icon reset-btn" data-key="${key}" title="Reset to default">↺</button>` : ''}
          </div>
        </div>
      `;
    }).join('');
  }

  renderInput(key, value, type) {
    const inputId = `setting-${key}`;
    
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

  renderTierBadge(tier) {
    const badges = {
      hot: { color: 'green', text: 'Hot', title: 'Applied immediately' },
      component: { color: 'yellow', text: 'Component', title: 'Requires component reload' },
      restart: { color: 'red', text: 'Restart', title: 'Requires system restart' }
    };
    
    const badge = badges[tier] || badges.restart;
    return `<span class="tier-badge tier-${badge.color}" title="${badge.title}">${badge.text}</span>`;
  }

  formatLabel(key) {
    return key
      .replace(/_/g, ' ')
      .split(' ')
      .map(word => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase())
      .join(' ');
  }

  getTierForKey(key) {
    if (this.tiers.hot.includes(key)) return 'hot';
    if (this.tiers.component.includes(key)) return 'component';
    return 'restart';
  }

  attachEventListeners() {
    this.modal.querySelector('#settings-close').addEventListener('click', () => this.close());
    
    this.modal.addEventListener('click', (e) => {
      if (e.target === this.modal) this.close();
    });
    
    this.escHandler = (e) => {
      if (e.key === 'Escape') this.close();
    };
    document.addEventListener('keydown', this.escHandler);
    
    this.modal.querySelectorAll('.tab-btn').forEach(btn => {
      btn.addEventListener('click', () => this.switchTab(btn.dataset.tab));
    });
    
    this.modal.querySelectorAll('input, textarea').forEach(input => {
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
    
    this.modal.querySelector('#settings-save').addEventListener('click', () => this.saveChanges());
    this.modal.querySelector('#settings-reload').addEventListener('click', () => this.reloadSettings());
    
    const resetAllBtn = this.modal.querySelector('#settings-reset-all');
    if (resetAllBtn) {
      resetAllBtn.addEventListener('click', () => this.resetAll());
    }
    
    // Theme dropdown handler
    const themeSelect = this.modal.querySelector('#theme-select');
    if (themeSelect) {
      themeSelect.addEventListener('change', (e) => this.handleThemeChange(e.target.value));
    }
    
    // Avatar upload handlers
    this.attachAvatarListeners();
  }

  attachAvatarListeners() {
    const userBtn = this.modal.querySelector('#user-avatar-btn');
    const userInput = this.modal.querySelector('#user-avatar-input');
    const assistantBtn = this.modal.querySelector('#assistant-avatar-btn');
    const assistantInput = this.modal.querySelector('#assistant-avatar-input');
    
    if (userBtn && userInput) {
      userBtn.addEventListener('click', () => userInput.click());
      userInput.addEventListener('change', (e) => this.handleAvatarUpload('user', e.target.files[0]));
    }
    
    if (assistantBtn && assistantInput) {
      assistantBtn.addEventListener('click', () => assistantInput.click());
      assistantInput.addEventListener('change', (e) => this.handleAvatarUpload('assistant', e.target.files[0]));
    }
  }

  async handleAvatarUpload(role, file) {
    if (!file) return;
    
    // Check existing avatar and confirm overwrite
    const checkResult = await settingsAPI.checkAvatar(role).catch(() => ({ exists: false }));
    if (checkResult.exists) {
      if (!confirm(`Replace existing ${role} avatar?`)) {
        // Reset file input
        const input = this.modal.querySelector(`#${role}-avatar-input`);
        if (input) input.value = '';
        return;
      }
    }
    
    const btn = this.modal.querySelector(`#${role}-avatar-btn`);
    const originalText = btn?.textContent;
    if (btn) {
      btn.disabled = true;
      btn.textContent = 'Uploading...';
    }
    
    try {
      const result = await settingsAPI.uploadAvatar(role, file);
      
      // Update preview with cache-busted URL
      const preview = this.modal.querySelector(`#${role}-avatar-preview`);
      if (preview && result.path) {
        preview.src = `${result.path}?t=${Date.now()}`;
        this.avatarPaths[role] = result.path;
      }
      
      showToast(`${role.charAt(0).toUpperCase() + role.slice(1)} avatar updated`, 'success');
    } catch (e) {
      console.error('Avatar upload failed:', e);
      showToast(`Upload failed: ${e.message}`, 'error');
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.textContent = originalText;
      }
      // Reset file input
      const input = this.modal.querySelector(`#${role}-avatar-input`);
      if (input) input.value = '';
    }
  }

  handleThemeChange(themeName) {
    this.currentTheme = themeName;
    
    // Apply immediately to document
    document.documentElement.setAttribute('data-theme', themeName);
    
    // Swap theme CSS file
    this.loadThemeCSS(themeName);
  }

  loadThemeCSS(themeName) {
    const existingLink = document.getElementById('theme-stylesheet');
    const href = `/static/themes/${themeName}.css`;
    
    if (existingLink) {
      existingLink.href = href;
    } else {
      const link = document.createElement('link');
      link.id = 'theme-stylesheet';
      link.rel = 'stylesheet';
      link.href = href;
      document.head.appendChild(link);
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
    
    if (Object.keys(this.pendingChanges).length === 0) {
      if (this.currentTheme === this.originalTheme) {
        showToast('No changes to save', 'info');
      }
      return;
    }
    
    const saveBtn = this.modal.querySelector('#settings-save');
    saveBtn.disabled = true;
    saveBtn.textContent = 'Saving...';
    
    try {
      const parsedChanges = {};
      for (const [key, value] of Object.entries(this.pendingChanges)) {
        const originalValue = this.settings[key];
        parsedChanges[key] = settingsAPI.parseValue(value, originalValue);
      }
      
      const uiSettings = ['TTS_ENABLED', 'STT_ENABLED'];
      const needsUIRefresh = Object.keys(parsedChanges).some(key => uiSettings.includes(key));
      
      const result = await settingsAPI.updateSettingsBatch(parsedChanges);
      await settingsAPI.reloadSettings();
      
      showToast(`Saved ${Object.keys(parsedChanges).length} settings`, 'success');
      
      if (result.restart_required) {
        showToast('Restart required - some settings need system restart', 'info');
      } else if (result.component_reload_required) {
        showToast('Refresh recommended - reload page to see changes', 'info');
      } else if (needsUIRefresh) {
        if (confirm('Settings saved! Refresh page to see changes?')) {
          window.location.reload();
          return;
        }
      }
      
      this.pendingChanges = {};
      await this.loadData();
      this.refreshContent();
      
      this.modal.querySelectorAll('.setting-row.modified').forEach(row => {
        row.classList.remove('modified');
      });
      
    } catch (e) {
      console.error('Save failed:', e);
      showToast('Save failed: ' + e.message, 'error');
    } finally {
      saveBtn.disabled = false;
      saveBtn.textContent = 'Save Changes';
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
    if (!confirm('Reset ALL settings to defaults? This cannot be undone!')) return;
    
    try {
      await settingsAPI.resetSettings();
      showToast('All settings reset to defaults', 'success');
      await this.loadData();
      this.refreshContent();
    } catch (e) {
      showToast('Reset failed: ' + e.message, 'error');
    }
  }

  refreshContent() {
    const content = this.modal.querySelector('.settings-modal-content');
    content.innerHTML = this.renderTabContent();
    
    this.modal.querySelectorAll('input, textarea').forEach(input => {
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
    
    const resetAllBtn = this.modal.querySelector('#settings-reset-all');
    if (resetAllBtn) {
      resetAllBtn.addEventListener('click', () => this.resetAll());
    }
    
    // Re-attach theme handler
    const themeSelect = this.modal.querySelector('#theme-select');
    if (themeSelect) {
      themeSelect.addEventListener('change', (e) => this.handleThemeChange(e.target.value));
    }
    
    // Re-attach avatar handlers
    this.attachAvatarListeners();
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
      this.loadThemeCSS(this.originalTheme);
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