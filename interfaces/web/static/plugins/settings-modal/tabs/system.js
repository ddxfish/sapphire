// tabs/system.js - System settings and danger zone

export default {
  id: 'system',
  name: 'System',
  icon: '⚡',
  description: 'System and advanced settings',
  
  keys: [
    'PLUGINS_ENABLED',
    'WEB_UI_HOST',
    'WEB_UI_PORT',
    'WEB_UI_SSL_ADHOC',
    'API_HOST',
    'API_PORT'
  ],

  essentialKeys: [
    'PLUGINS_ENABLED',
    'WEB_UI_SSL_ADHOC'
  ],

  advancedKeys: [
    'WEB_UI_HOST',
    'WEB_UI_PORT',
    'API_HOST',
    'API_PORT'
  ],

  render(modal) {
    return `
      <div class="system-tab-content">
        <div class="settings-list">
          ${modal.renderCategorySettings(this.essentialKeys)}
        </div>
        
        ${modal.renderAdvancedAccordion('system-advanced', this.advancedKeys)}
        
        <div style="margin-bottom: 24px;"></div>
        
        <div class="system-danger-zone">
          <h4>Danger Zone</h4>
          <p>These actions are irreversible and will affect all settings.</p>
          <button class="btn btn-danger btn-lg" id="settings-reset-all">Reset All Settings to Defaults</button>
          <p class="warning-text">This will delete your user/settings.json file and revert everything to default values. This action cannot be undone.</p>
        </div>
      </div>
    `;
  },

  attachListeners(modal, contentEl) {
    modal.attachAccordionListeners(contentEl);
    
    const resetAllBtn = contentEl.querySelector('#settings-reset-all');
    if (resetAllBtn) {
      resetAllBtn.addEventListener('click', () => modal.resetAll());
    }
  }
};