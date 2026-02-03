// index.js - Plugins modal plugin entry point
// Supports both eager and lazy loading modes

import { injectStyles } from './plugins-styles.js';
import PluginsModal from './plugins-modal.js';

// Re-export registry functions for other plugins to use
export { registerPluginSettings, unregisterPluginSettings, getRegisteredTabs } from './plugin-registry.js';

export default {
  name: 'plugins-modal',
  modal: null,

  init(container) {
    injectStyles();

    // Check if loaded lazily (menu button already exists)
    const existingBtn = document.querySelector('[data-plugin="plugins-modal"][data-lazy="true"]');
    if (existingBtn) {
      console.log('✔ Plugins modal initialized (lazy)');
      return;
    }

    // Eager mode - register menu item ourselves
    const pluginLoader = window.pluginLoader;
    if (pluginLoader) {
      const menuButton = pluginLoader.registerIcon(this);
      if (menuButton) {
        menuButton.textContent = 'Plugins';
        menuButton.title = 'Manage Plugins';
        menuButton.addEventListener('click', () => this.openPlugins());
      }
    }

    console.log('✔ Plugins modal initialized');
  },

  // Called by lazy loader when menu item is clicked
  onTrigger() {
    this.openPlugins();
  },

  openPlugins() {
    // Close any open kebab menus
    document.querySelectorAll('.kebab-menu.open').forEach(m => m.classList.remove('open'));

    if (this.modal) {
      console.log('Plugins modal already open');
      return;
    }

    this.modal = new PluginsModal();
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