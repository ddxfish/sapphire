// shared/plugins-api.js - API calls for plugin management

function csrfHeaders(extra = {}) {
  const token = document.querySelector('meta[name="csrf-token"]')?.content || '';
  return { 'X-CSRF-Token': token, ...extra };
}

const pluginsAPI = {
  /**
   * Get list of all plugins with enabled/locked status
   */
  async listPlugins() {
    const res = await fetch('/api/webui/plugins');
    if (!res.ok) throw new Error(`Failed to list plugins: ${res.status}`);
    return res.json();
  },

  /**
   * Get merged plugins config (for plugin loader)
   */
  async getConfig() {
    const res = await fetch('/api/webui/plugins/config');
    if (!res.ok) throw new Error(`Failed to get config: ${res.status}`);
    return res.json();
  },

  /**
   * Toggle a plugin's enabled state
   */
  async togglePlugin(pluginName) {
    const res = await fetch(`/api/webui/plugins/toggle/${pluginName}`, {
      method: 'PUT',
      headers: csrfHeaders()
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `Failed to toggle: ${res.status}`);
    }
    const data = await res.json();
    // Notify app so plugin scripts can be loaded/unloaded
    document.dispatchEvent(new CustomEvent('sapphire:plugin_toggled', { detail: data }));
    return data;
  },

  /**
   * Get settings for a specific plugin
   */
  async getSettings(pluginName) {
    const res = await fetch(`/api/webui/plugins/${pluginName}/settings`);
    if (!res.ok) throw new Error(`Failed to get settings: ${res.status}`);
    const data = await res.json();
    return data.settings || {};
  },

  /**
   * Save settings for a specific plugin
   */
  async saveSettings(pluginName, settings) {
    const res = await fetch(`/api/webui/plugins/${pluginName}/settings`, {
      method: 'PUT',
      headers: csrfHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify(settings)
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.error || `Failed to save: ${res.status}`);
    }
    return res.json();
  },

  /**
   * Reset/delete settings for a plugin
   */
  async resetSettings(pluginName) {
    const res = await fetch(`/api/webui/plugins/${pluginName}/settings`, {
      method: 'DELETE',
      headers: csrfHeaders()
    });
    if (!res.ok) throw new Error(`Failed to reset: ${res.status}`);
    return res.json();
  },

  /**
   * Install a plugin from GitHub URL or zip file.
   * Returns 409 with existing info if plugin exists and force=false.
   */
  async installPlugin({ url, file, force = false }) {
    const form = new FormData();
    if (url) form.append('url', url);
    if (file) form.append('file', file);
    if (force) form.append('force', 'true');
    const res = await fetch('/api/plugins/install', {
      method: 'POST',
      headers: csrfHeaders(),
      body: form,
    });
    if (!res.ok) {
      const e = await res.json().catch(() => ({}));
      // 409 = plugin exists, return the body for replace confirmation
      if (res.status === 409 && e.name) return { conflict: true, ...e };
      throw new Error(e.detail || `Install failed: ${res.status}`);
    }
    return res.json();
  },

  /**
   * Uninstall a user plugin (remove files, settings, state)
   */
  async uninstallPlugin(name) {
    const res = await fetch(`/api/plugins/${name}/uninstall`, {
      method: 'DELETE',
      headers: csrfHeaders(),
    });
    if (!res.ok) {
      const e = await res.json().catch(() => ({}));
      throw new Error(e.detail || `Uninstall failed: ${res.status}`);
    }
    return res.json();
  },

  /**
   * Check if a plugin has an update available on GitHub
   */
  async checkUpdate(name) {
    const res = await fetch(`/api/plugins/${name}/check-update`);
    if (!res.ok) throw new Error(`Check failed: ${res.status}`);
    return res.json();
  },
};

export default pluginsAPI;
