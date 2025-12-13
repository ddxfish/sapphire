// index.js - Backup plugin entry point
import BackupModal from './backup-modal.js';
import { injectStyles } from './backup-styles.js';

const BackupPlugin = {
  name: 'backup',
  modal: null,

  async init(container) {
    injectStyles();

    // Register in app kebab menu
    if (window.pluginLoader) {
      const menuBtn = window.pluginLoader.registerIcon(this);
      if (menuBtn) {
        menuBtn.textContent = 'Backups';
        menuBtn.addEventListener('click', () => this.openModal());
      }
    }

    console.log('Backup plugin initialized');
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