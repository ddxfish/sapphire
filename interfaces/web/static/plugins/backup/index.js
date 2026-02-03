// index.js - Backup plugin entry point
// Supports both eager and lazy loading modes
import BackupModal from './backup-modal.js';
import { injectStyles } from './backup-styles.js';

const BackupPlugin = {
  name: 'backup',
  modal: null,

  async init(container) {
    injectStyles();

    // Check if loaded lazily (menu button already exists)
    const existingBtn = document.querySelector('[data-plugin="backup"][data-lazy="true"]');
    if (existingBtn) {
      console.log('✔ Backup plugin initialized (lazy)');
      return;
    }

    // Eager mode - register menu item ourselves
    if (window.pluginLoader) {
      const menuBtn = window.pluginLoader.registerIcon(this);
      if (menuBtn) {
        menuBtn.textContent = 'Backups';
        menuBtn.addEventListener('click', () => this.openModal());
      }
    }

    console.log('✔ Backup plugin initialized');
  },

  // Called by lazy loader when menu item is clicked
  onTrigger() {
    this.openModal();
  },

  openModal() {
    if (this.modal) return;

    this.modal = new BackupModal();
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

export default BackupPlugin;