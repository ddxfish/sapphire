// index.js - Settings modal plugin entry point
import { injectStyles } from './settings-styles.js';
import SettingsModal from './settings-modal.js';

export default {
  name: 'settings-modal',
  modal: null,

  init(container) {
    // Inject plugin styles
    injectStyles();
    
    // This plugin doesn't use the sidebar container, 
    // instead it registers a menu item in the app kebab
    
    // Get access to plugin loader through global scope
    const pluginLoader = window.pluginLoader;
    if (!pluginLoader) {
      console.error('Plugin loader not available');
      return;
    }

    // Register menu item in app kebab
    const menuButton = pluginLoader.registerIcon(this);
    if (menuButton) {
      menuButton.textContent = 'App Settings';
      menuButton.title = 'App Settings';
      menuButton.addEventListener('click', () => this.openSettings());
    }

    console.log('✔ Settings modal plugin initialized');
  },

  openSettings() {
    // Close any open kebab menus
    document.querySelectorAll('.kebab-menu.open').forEach(m => m.classList.remove('open'));
    
    if (this.modal) {
      console.log('Settings modal already open');
      return;
    }

    this.modal = new SettingsModal();
    
    // Clear reference when modal closes
    this.modal.onCloseCallback = () => {
      this.modal = null;
    };
    
    this.modal.open();
  },

  destroy() {
    if (this.modal) {
      this.modal.close();
      this.modal = null;
    }
  }
};