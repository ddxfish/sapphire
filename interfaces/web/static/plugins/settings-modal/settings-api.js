// settings-api.js - Settings API wrapper
import { fetchWithTimeout } from '../../shared/fetch.js';

class SettingsAPI {
  async getAllSettings() {
    return await fetchWithTimeout('/api/settings');
  }

  async getSetting(key) {
    return await fetchWithTimeout(`/api/settings/${encodeURIComponent(key)}`);
  }

  async updateSetting(key, value, persist = true) {
    return await fetchWithTimeout(`/api/settings/${encodeURIComponent(key)}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ value, persist })
    });
  }

  async updateSettingsBatch(settings) {
    return await fetchWithTimeout('/api/settings/batch', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ settings })
    });
  }

  async deleteSetting(key) {
    return await fetchWithTimeout(`/api/settings/${encodeURIComponent(key)}`, {
      method: 'DELETE'
    });
  }

  async resetSettings() {
    return await fetchWithTimeout('/api/settings/reset', { method: 'POST' });
  }

  async reloadSettings() {
    return await fetchWithTimeout('/api/settings/reload', { method: 'POST' });
  }

  async getSettingsHelp() {
    return await fetchWithTimeout('/api/settings/help');
  }

  async getSettingHelp(key) {
    return await fetchWithTimeout(`/api/settings/help/${encodeURIComponent(key)}`);
  }

  getInputType(value) {
    if (typeof value === 'boolean') return 'checkbox';
    if (typeof value === 'number') return 'number';
    if (typeof value === 'object') return 'json';
    return 'text';
  }

  parseValue(value, originalValue) {
    if (typeof originalValue === 'boolean') {
      return value === 'true' || value === true;
    }
    if (typeof originalValue === 'number') {
      const parsed = parseFloat(value);
      return isNaN(parsed) ? originalValue : parsed;
    }
    if (typeof originalValue === 'object') {
      try {
        return JSON.parse(value);
      } catch {
        throw new Error('Invalid JSON');
      }
    }
    return value;
  }

  async uploadAvatar(role, file) {
    const formData = new FormData();
    formData.append('file', file);
    formData.append('role', role);
    
    const res = await fetch('/api/avatar/upload', {
      method: 'POST',
      body: formData
    });
    
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.error || `Upload failed: ${res.status}`);
    }
    
    return await res.json();
  }

  async checkAvatar(role) {
    return await fetchWithTimeout(`/api/avatar/check/${encodeURIComponent(role)}`);
  }

  async resetPrompts() {
    return await fetchWithTimeout('/api/prompts/reset', { method: 'POST' });
  }

  async mergePrompts() {
    return await fetchWithTimeout('/api/prompts/merge', { method: 'POST' });
  }

  async resetChatDefaults() {
    return await fetchWithTimeout('/api/prompts/reset-chat-defaults', { method: 'POST' });
  }
}

export default new SettingsAPI();