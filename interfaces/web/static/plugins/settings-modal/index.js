// index.js - Settings modal plugin entry point
// Supports both eager and lazy loading modes
import { injectStyles } from './settings-styles.js';
import SettingsModal from './settings-modal.js';

export default {
  name: 'settings-modal',
  modal: null,

  init(container) {
    // Inject plugin styles
    injectStyles();

    // Check if loaded lazily (menu button already exists)
    const existingBtn = document.querySelector('[data-plugin="settings-modal"][data-lazy="true"]');
    if (existingBtn) {
      // Lazy mode - button already registered by plugin-loader
      console.log('✔ Settings modal plugin initialized (lazy)');
      return;
    }

    // Eager mode - register menu item ourselves
    const pluginLoader = window.pluginLoader;
    if (pluginLoader) {
      const menuButton = pluginLoader.registerIcon(this);
      if (menuButton) {
        menuButton.textContent = 'App Settings';
        menuButton.title = 'App Settings';
        menuButton.addEventListener('click', () => this.openSettings());
      }
    }

    console.log('✔ Settings modal plugin initialized');
  },

  // Called by lazy loader when menu item is clicked
  onTrigger() {
    this.openSettings();
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