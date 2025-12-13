// backup-api.js - API wrapper for backup endpoints

const backupAPI = {
  async list() {
    const res = await fetch('/api/backup/list');
    if (!res.ok) throw new Error('Failed to list backups');
    return res.json();
  },

  async create(type = 'manual') {
    const res = await fetch('/api/backup/create', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ type })
    });
    if (!res.ok) throw new Error('Failed to create backup');
    return res.json();
  },

  async delete(filename) {
    const res = await fetch(`/api/backup/delete/${encodeURIComponent(filename)}`, {
      method: 'DELETE'
    });
    if (!res.ok) throw new Error('Failed to delete backup');
    return res.json();
  },

  getDownloadUrl(filename) {
    return `/api/backup/download/${encodeURIComponent(filename)}`;
  },

  async getSettings() {
    const res = await fetch('/api/settings');
    if (!res.ok) throw new Error('Failed to load settings');
    const data = await res.json();
    return {
      daily: data.settings.BACKUPS_KEEP_DAILY ?? 7,
      weekly: data.settings.BACKUPS_KEEP_WEEKLY ?? 4,
      monthly: data.settings.BACKUPS_KEEP_MONTHLY ?? 3,
      manual: data.settings.BACKUPS_KEEP_MANUAL ?? 5,
      enabled: data.settings.BACKUPS_ENABLED ?? true
    };
  },

  async saveSettings(settings) {
    const updates = {
      BACKUPS_KEEP_DAILY: settings.daily,
      BACKUPS_KEEP_WEEKLY: settings.weekly,
      BACKUPS_KEEP_MONTHLY: settings.monthly,
      BACKUPS_KEEP_MANUAL: settings.manual,
      BACKUPS_ENABLED: settings.enabled
    };

    const res = await fetch('/api/settings/batch', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ settings: updates })
    });
    if (!res.ok) throw new Error('Failed to save settings');
    return res.json();
  }
};

export default backupAPI;