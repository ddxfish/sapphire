// backup-modal.js - Backup manager modal UI
import backupAPI from './backup-api.js';
import { showToast } from '../../shared/toast.js';

class BackupModal {
  constructor() {
    this.modal = null;
    this.backups = { daily: [], weekly: [], monthly: [], manual: [] };
    this.settings = { daily: 7, weekly: 4, monthly: 3, manual: 5, enabled: true };
    this.originalSettings = null;
    this.expandedSections = { daily: false, weekly: false, monthly: false, manual: false };
    this.onCloseCallback = null;
  }

  async open() {
    await this.loadData();
    this.originalSettings = { ...this.settings };
    this.render();
    this.attachEventListeners();
  }

  async loadData() {
    try {
      const [backupData, settingsData] = await Promise.all([
        backupAPI.list(),
        backupAPI.getSettings()
      ]);
      this.backups = backupData.backups;
      this.settings = settingsData;
    } catch (e) {
      console.error('Failed to load backup data:', e);
      showToast('Failed to load backup data: ' + e.message, 'error');
    }
  }

  formatSize(bytes) {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / (1024 * 1024)).toFixed(2) + ' MB';
  }

  getTotalStats() {
    let totalSize = 0;
    let totalCount = 0;
    for (const type of ['daily', 'weekly', 'monthly', 'manual']) {
      for (const backup of this.backups[type]) {
        totalSize += backup.size;
        totalCount++;
      }
    }
    return { totalSize, totalCount };
  }

  render() {
    const stats = this.getTotalStats();
    const listsHtml = this.renderAllSections();

    this.modal = document.createElement('div');
    this.modal.className = 'backup-modal-overlay';
    this.modal.innerHTML = [
      '<div class="backup-modal">',
      '  <div class="backup-modal-header">',
      '    <h2>💾 Backup Manager</h2>',
      '    <button class="close-btn" id="backup-close">✕</button>',
      '  </div>',
      '  <div class="backup-modal-content styled-scrollbar">',
      '    <div class="backup-stats">',
      '      <span>📊 ' + stats.totalCount + ' backups</span>',
      '      <span>💿 ' + this.formatSize(stats.totalSize) + ' total</span>',
      '    </div>',
      '    <div class="backup-settings-section">',
      '      <h3>Retention Settings</h3>',
      '      <div class="backup-settings-grid">',
      '        <label><span>Daily</span><input type="number" id="backup-keep-daily" value="' + this.settings.daily + '" min="0" max="30"></label>',
      '        <label><span>Weekly</span><input type="number" id="backup-keep-weekly" value="' + this.settings.weekly + '" min="0" max="12"></label>',
      '        <label><span>Monthly</span><input type="number" id="backup-keep-monthly" value="' + this.settings.monthly + '" min="0" max="12"></label>',
      '        <label><span>Manual</span><input type="number" id="backup-keep-manual" value="' + this.settings.manual + '" min="0" max="20"></label>',
      '      </div>',
      '    </div>',
      '    <div class="backup-action-section">',
      '      <button class="btn btn-primary" id="backup-now"><span>⚡</span> Backup Now</button>',
      '    </div>',
      '    <div class="backup-lists">' + listsHtml + '</div>',
      '  </div>',
      '  <div class="backup-modal-footer">',
      '    <button class="btn btn-secondary" id="backup-cancel">Cancel</button>',
      '    <button class="btn btn-primary" id="backup-save">Save Settings</button>',
      '  </div>',
      '</div>'
    ].join('\n');

    document.body.appendChild(this.modal);
    requestAnimationFrame(() => this.modal.classList.add('active'));
  }

  renderAllSections() {
    return [
      this.renderBackupSection('daily', 'Daily Backups'),
      this.renderBackupSection('weekly', 'Weekly Backups'),
      this.renderBackupSection('monthly', 'Monthly Backups'),
      this.renderBackupSection('manual', 'Manual Backups')
    ].join('');
  }

  renderBackupSection(type, title) {
    const backups = this.backups[type] || [];
    const isExpanded = this.expandedSections[type];
    const count = backups.length;
    const limit = this.settings[type];
    const toggleClass = isExpanded ? 'accordion-toggle' : 'accordion-toggle collapsed';
    const contentClass = isExpanded ? 'backup-section-content' : 'backup-section-content collapsed';

    let itemsHtml = '';
    if (backups.length === 0) {
      itemsHtml = '<div class="backup-empty">No backups</div>';
    } else {
      itemsHtml = backups.map(b => this.renderBackupItem(b)).join('');
    }

    return [
      '<div class="backup-section" data-type="' + type + '">',
      '  <div class="backup-section-header" data-type="' + type + '">',
      '    <span class="' + toggleClass + '"></span>',
      '    <span class="backup-section-title">' + title + '</span>',
      '    <span class="backup-section-count">' + count + '/' + limit + '</span>',
      '  </div>',
      '  <div class="' + contentClass + '">' + itemsHtml + '</div>',
      '</div>'
    ].join('');
  }

  renderBackupItem(backup) {
    return [
      '<div class="backup-item" data-filename="' + backup.filename + '">',
      '  <div class="backup-item-info">',
      '    <span class="backup-item-date">' + backup.date + ' ' + backup.time + '</span>',
      '    <span class="backup-item-size">' + this.formatSize(backup.size) + '</span>',
      '  </div>',
      '  <div class="backup-item-actions">',
      '    <button class="inline-btn download-btn" title="Download">⬇</button>',
      '    <button class="inline-btn delete delete-btn" title="Delete">🗑</button>',
      '  </div>',
      '</div>'
    ].join('');
  }

  attachEventListeners() {
    this.modal.querySelector('#backup-close').addEventListener('click', () => this.close());
    this.modal.querySelector('#backup-cancel').addEventListener('click', () => this.close());
    this.modal.addEventListener('click', (e) => {
      if (e.target === this.modal) this.close();
    });

    this.escHandler = (e) => {
      if (e.key === 'Escape') this.close();
    };
    document.addEventListener('keydown', this.escHandler);

    this.modal.querySelector('#backup-save').addEventListener('click', () => this.saveSettings());
    this.modal.querySelector('#backup-now').addEventListener('click', () => this.createBackup());

    this.attachListEventListeners();

    ['daily', 'weekly', 'monthly', 'manual'].forEach(type => {
      const input = this.modal.querySelector('#backup-keep-' + type);
      input.addEventListener('change', () => {
        this.settings[type] = parseInt(input.value, 10) || 0;
      });
    });
  }

  attachListEventListeners() {
    this.modal.querySelectorAll('.backup-section-header').forEach(header => {
      header.addEventListener('click', () => this.toggleSection(header.dataset.type));
    });

    this.modal.querySelectorAll('.download-btn').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const filename = btn.closest('.backup-item').dataset.filename;
        this.downloadBackup(filename);
      });
    });

    this.modal.querySelectorAll('.delete-btn').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const filename = btn.closest('.backup-item').dataset.filename;
        this.deleteBackup(filename);
      });
    });
  }

  toggleSection(type) {
    this.expandedSections[type] = !this.expandedSections[type];
    const section = this.modal.querySelector('.backup-section[data-type="' + type + '"]');
    const toggle = section.querySelector('.accordion-toggle');
    const content = section.querySelector('.backup-section-content');

    toggle.classList.toggle('collapsed', !this.expandedSections[type]);
    content.classList.toggle('collapsed', !this.expandedSections[type]);
  }

  async createBackup() {
    const btn = this.modal.querySelector('#backup-now');
    btn.disabled = true;
    btn.innerHTML = '<span>⏳</span> Creating...';

    try {
      const result = await backupAPI.create('manual');
      showToast('Backup created: ' + result.filename, 'success');
      await this.refreshBackups();
    } catch (e) {
      showToast('Backup failed: ' + e.message, 'error');
    } finally {
      btn.disabled = false;
      btn.innerHTML = '<span>⚡</span> Backup Now';
    }
  }

  downloadBackup(filename) {
    const url = backupAPI.getDownloadUrl(filename);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    a.click();
  }

  async deleteBackup(filename) {
    if (!confirm('Delete backup ' + filename + '?')) return;

    try {
      await backupAPI.delete(filename);
      showToast('Backup deleted', 'success');
      await this.refreshBackups();
    } catch (e) {
      showToast('Delete failed: ' + e.message, 'error');
    }
  }

  async refreshBackups() {
    try {
      const data = await backupAPI.list();
      this.backups = data.backups;
      this.refreshBackupLists();
      this.refreshStats();
    } catch (e) {
      console.error('Failed to refresh backups:', e);
    }
  }

  refreshBackupLists() {
    const listsContainer = this.modal.querySelector('.backup-lists');
    listsContainer.innerHTML = this.renderAllSections();
    this.attachListEventListeners();
  }

  refreshStats() {
    const stats = this.getTotalStats();
    const statsEl = this.modal.querySelector('.backup-stats');
    statsEl.innerHTML = '<span>📊 ' + stats.totalCount + ' backups</span><span>💿 ' + this.formatSize(stats.totalSize) + ' total</span>';
  }

  async saveSettings() {
    const btn = this.modal.querySelector('#backup-save');
    btn.disabled = true;
    btn.textContent = 'Saving...';

    try {
      await backupAPI.saveSettings(this.settings);
      this.originalSettings = { ...this.settings };
      showToast('Backup settings saved', 'success');
    } catch (e) {
      showToast('Save failed: ' + e.message, 'error');
    } finally {
      btn.disabled = false;
      btn.textContent = 'Save Settings';
    }
  }

  hasUnsavedChanges() {
    return JSON.stringify(this.settings) !== JSON.stringify(this.originalSettings);
  }

  close() {
    if (this.hasUnsavedChanges()) {
      if (!confirm('You have unsaved changes. Close anyway?')) return;
    }

    this.modal.classList.remove('active');
    setTimeout(() => {
      document.removeEventListener('keydown', this.escHandler);
      this.modal.remove();
      this.modal = null;
      
      // Notify plugin that modal is closed
      if (this.onCloseCallback) {
        this.onCloseCallback();
      }
    }, 300);
  }
}

export default BackupModal;