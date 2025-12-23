// plugins-modal.js - Plugins management modal UI

import pluginsAPI from './plugins-api.js';
import { getRegisteredTabs } from './plugin-registry.js';
import { showToast } from '../../shared/toast.js';

class PluginsModal {
  constructor() {
    this.modal = null;
    this.plugins = [];
    this.lockedPlugins = [];
    this.currentTab = 'plugins';
    this.pendingChanges = false;
    this.onCloseCallback = null;
  }

  async open() {
    await this.loadData();
    this.render();
    this.attachEventListeners();
  }

  async loadData() {
    try {
      const data = await pluginsAPI.listPlugins();
      this.plugins = data.plugins || [];
      this.lockedPlugins = data.locked || [];
    } catch (e) {
      console.error('Failed to load plugins:', e);
      showToast('Failed to load plugins: ' + e.message, 'error');
    }
  }

  render() {
    const registeredTabs = getRegisteredTabs();
    
    this.modal = document.createElement('div');
    this.modal.className = 'plugins-modal-overlay';
    this.modal.innerHTML = `
      <div class="plugins-modal">
        <div class="plugins-modal-header">
          <h2>Plugins</h2>
          <button class="close-btn" id="plugins-close">×</button>
        </div>
        
        <div class="plugins-modal-tabs">
          <button class="tab-btn active" data-tab="plugins">
            <span class="tab-icon">🔌</span>
            <span class="tab-label">Plugins</span>
          </button>
          ${registeredTabs.map(tab => `
            <button class="tab-btn" data-tab="${tab.id}">
              <span class="tab-icon">${tab.icon}</span>
              <span class="tab-label">${tab.name}</span>
            </button>
          `).join('')}
        </div>
        
        <div class="plugins-modal-content">
          ${this.renderPluginsTab()}
          ${registeredTabs.map(tab => this.renderRegisteredTab(tab)).join('')}
        </div>
        
        <div class="plugins-modal-footer">
          <button class="btn btn-secondary" id="plugins-cancel">Close</button>
          <button class="btn btn-primary" id="plugins-save">Save</button>
        </div>
      </div>
    `;
    
    document.body.appendChild(this.modal);
    
    // Load registered tab content
    this.loadRegisteredTabs(registeredTabs);
    
    requestAnimationFrame(() => {
      this.modal.classList.add('active');
    });
  }

  renderPluginsTab() {
    const helpTip = 'Toggle plugins on/off. Locked plugins (settings, plugins) cannot be disabled. Changes require a page reload.';
    
    return `
      <div class="plugins-tab-content active" data-tab="plugins">
        <div class="plugins-help-tip">${helpTip}</div>
        <div class="plugin-list">
          ${this.plugins.map(p => this.renderPluginItem(p)).join('')}
        </div>
        <div class="plugins-reload-notice" id="reload-notice">
          Plugin changes require a page reload to take effect.
          <button id="reload-now-btn">Reload Now</button>
        </div>
      </div>
    `;
  }

  renderPluginItem(plugin) {
    const isLocked = this.lockedPlugins.includes(plugin.name);
    const lockedClass = isLocked ? 'locked' : '';
    const enabledClass = plugin.enabled ? 'enabled' : '';
    
    return `
      <div class="plugin-item ${lockedClass}" data-plugin="${plugin.name}">
        <div class="plugin-item-info">
          <div class="plugin-item-title">${plugin.title}</div>
          <div class="plugin-item-name">${plugin.name}</div>
        </div>
        <div class="plugin-item-toggle ${enabledClass} ${lockedClass}" 
             data-plugin="${plugin.name}"
             title="${isLocked ? 'This plugin cannot be disabled' : 'Click to toggle'}">
        </div>
      </div>
    `;
  }

  renderRegisteredTab(tab) {
    return `
      <div class="plugins-tab-content" data-tab="${tab.id}">
        ${tab.helpText ? `<div class="plugins-help-tip">${tab.helpText}</div>` : ''}
        <div class="registered-tab-content" id="tab-content-${tab.id}"></div>
      </div>
    `;
  }

  async loadRegisteredTabs(tabs) {
    for (const tab of tabs) {
      const container = this.modal.querySelector(`#tab-content-${tab.id}`);
      if (container && tab.render) {
        try {
          // Load settings first
          const settings = await tab.load();
          // Render UI with settings
          tab.render(container, settings);
        } catch (e) {
          console.error(`Failed to load tab ${tab.id}:`, e);
          container.innerHTML = `<p style="color: var(--danger)">Failed to load: ${e.message}</p>`;
        }
      }
    }
  }

  attachEventListeners() {
    // Close button
    this.modal.querySelector('#plugins-close').addEventListener('click', () => this.close());
    this.modal.querySelector('#plugins-cancel').addEventListener('click', () => this.close());
    
    // Click outside to close
    this.modal.addEventListener('click', (e) => {
      if (e.target === this.modal) this.close();
    });
    
    // Escape key
    this.escHandler = (e) => {
      if (e.key === 'Escape') this.close();
    };
    document.addEventListener('keydown', this.escHandler);
    
    // Tab switching
    this.modal.querySelectorAll('.tab-btn').forEach(btn => {
      btn.addEventListener('click', () => this.switchTab(btn.dataset.tab));
    });
    
    // Plugin toggles
    this.modal.querySelectorAll('.plugin-item-toggle').forEach(toggle => {
      if (!toggle.classList.contains('locked')) {
        toggle.addEventListener('click', () => this.togglePlugin(toggle.dataset.plugin));
      }
    });
    
    // Reload button
    const reloadBtn = this.modal.querySelector('#reload-now-btn');
    if (reloadBtn) {
      reloadBtn.addEventListener('click', () => window.location.reload());
    }
    
    // Save button
    this.modal.querySelector('#plugins-save').addEventListener('click', () => this.saveAll());
  }

  switchTab(tabId) {
    this.currentTab = tabId;
    
    this.modal.querySelectorAll('.tab-btn').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.tab === tabId);
    });
    
    this.modal.querySelectorAll('.plugins-tab-content').forEach(content => {
      content.classList.toggle('active', content.dataset.tab === tabId);
    });
  }

  async togglePlugin(pluginName) {
    const toggle = this.modal.querySelector(`.plugin-item-toggle[data-plugin="${pluginName}"]`);
    if (!toggle || toggle.classList.contains('locked')) return;
    
    try {
      const result = await pluginsAPI.togglePlugin(pluginName);
      
      // Update UI
      toggle.classList.toggle('enabled', result.enabled);
      
      // Update local state
      const plugin = this.plugins.find(p => p.name === pluginName);
      if (plugin) plugin.enabled = result.enabled;
      
      // Show reload notice
      if (result.reload_required) {
        this.pendingChanges = true;
        this.modal.querySelector('#reload-notice').classList.add('visible');
      }
      
      showToast(`${pluginName} ${result.enabled ? 'enabled' : 'disabled'}`, 'success');
    } catch (e) {
      console.error('Toggle failed:', e);
      showToast('Failed to toggle: ' + e.message, 'error');
    }
  }

  async saveAll() {
    const registeredTabs = getRegisteredTabs();
    let saved = 0;
    
    for (const tab of registeredTabs) {
      if (tab.getSettings && tab.save) {
        try {
          const container = this.modal.querySelector(`#tab-content-${tab.id}`);
          const settings = tab.getSettings(container);
          if (settings && Object.keys(settings).length > 0) {
            await tab.save(settings);
            saved++;
          }
        } catch (e) {
          console.error(`Failed to save ${tab.id}:`, e);
          showToast(`Failed to save ${tab.name}: ${e.message}`, 'error');
        }
      }
    }
    
    if (saved > 0) {
      showToast(`Saved ${saved} plugin settings`, 'success');
    }
    
    if (this.pendingChanges) {
      if (confirm('Plugin changes require a page reload. Reload now?')) {
        window.location.reload();
      }
    } else if (saved === 0) {
      this.close();
    }
  }

  close() {
    this.modal.classList.remove('active');
    
    setTimeout(() => {
      document.removeEventListener('keydown', this.escHandler);
      this.modal.remove();
      this.modal = null;
      
      if (this.onCloseCallback) {
        this.onCloseCallback();
      }
    }, 200);
  }
}

export default PluginsModal;