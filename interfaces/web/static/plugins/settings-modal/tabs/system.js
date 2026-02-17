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
    'WEB_UI_SSL_ADHOC'
  ],

  essentialKeys: [
    'PLUGINS_ENABLED',
    'WEB_UI_SSL_ADHOC'
  ],

  advancedKeys: [
    'WEB_UI_HOST',
    'WEB_UI_PORT'
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
          <p>These actions are irreversible and will affect system configuration.</p>
          
          <div class="danger-zone-section">
            <h5>Settings</h5>
            <button class="btn btn-danger" id="settings-reset-all">Reset All Settings to Defaults</button>
            <p class="warning-text">Deletes user/settings.json and reverts everything to defaults. Requires restart.</p>
          </div>
          
          <div class="danger-zone-section">
            <h5>Prompts & Personas</h5>
            <button class="btn btn-primary" id="merge-updates" style="margin-bottom: 8px;">Import App Updates</button>
            <p class="warning-text" style="color: var(--text-secondary, #999); margin-bottom: 12px;">Adds new prompts and personas from updates without touching your stuff. Backs up first.</p>
            <div class="danger-zone-buttons">
              <button class="btn btn-danger" id="prompts-reset">Reset Prompts to Defaults</button>
            </div>
            <p class="warning-text">Overwrites all prompt files with factory versions. Creates backup first.</p>
          </div>
          
          <div class="danger-zone-section">
            <h5>Chat Defaults</h5>
            <button class="btn btn-danger" id="chat-defaults-reset">Reset Chat Defaults</button>
            <p class="warning-text">Resets default prompt, voice, and spice settings for new chats.</p>
          </div>
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
    
    const promptsResetBtn = contentEl.querySelector('#prompts-reset');
    if (promptsResetBtn) {
      promptsResetBtn.addEventListener('click', () => modal.resetPrompts());
    }

    const mergeUpdatesBtn = contentEl.querySelector('#merge-updates');
    if (mergeUpdatesBtn) {
      mergeUpdatesBtn.addEventListener('click', () => modal.mergeUpdates());
    }
    
    const chatDefaultsResetBtn = contentEl.querySelector('#chat-defaults-reset');
    if (chatDefaultsResetBtn) {
      chatDefaultsResetBtn.addEventListener('click', () => modal.resetChatDefaults());
    }
  }
};