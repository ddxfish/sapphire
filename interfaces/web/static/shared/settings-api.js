// shared/settings-api.js - Settings API wrapper
import { fetchWithTimeout } from './fetch.js';

export async function getAllSettings() {
    return await fetchWithTimeout('/api/settings');
}

export async function updateSettingsBatch(settings) {
    return await fetchWithTimeout('/api/settings/batch', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ settings })
    });
}

export async function deleteSetting(key) {
    return await fetchWithTimeout(`/api/settings/${encodeURIComponent(key)}`, { method: 'DELETE' });
}

export async function resetAllSettings() {
    return await fetchWithTimeout('/api/settings/reset', { method: 'POST' });
}

export async function reloadSettings() {
    return await fetchWithTimeout('/api/settings/reload', { method: 'POST' });
}

export async function getSettingsHelp() {
    return await fetchWithTimeout('/api/settings/help');
}

export async function uploadAvatar(role, file) {
    const formData = new FormData();
    formData.append('file', file);
    formData.append('role', role);
    const res = await fetch('/api/avatar/upload', { method: 'POST', body: formData });
    if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.error || `Upload failed: ${res.status}`);
    }
    return await res.json();
}

export async function checkAvatar(role) {
    return await fetchWithTimeout(`/api/avatar/check/${encodeURIComponent(role)}`);
}

export async function resetPrompts() {
    return await fetchWithTimeout('/api/prompts/reset', { method: 'POST' });
}

export async function mergePrompts() {
    return await fetchWithTimeout('/api/prompts/merge', { method: 'POST' });
}

export async function mergeUpdates() {
    return await fetchWithTimeout('/api/system/merge-updates', { method: 'POST' });
}

export async function resetChatDefaults() {
    return await fetchWithTimeout('/api/prompts/reset-chat-defaults', { method: 'POST' });
}

// Type inference helpers
export function getInputType(value) {
    if (typeof value === 'boolean') return 'checkbox';
    if (typeof value === 'number') return 'number';
    if (typeof value === 'object') return 'json';
    return 'text';
}

export function parseValue(value, originalValue) {
    if (typeof originalValue === 'boolean') return value === 'true' || value === true;
    if (typeof originalValue === 'number') {
        const parsed = parseFloat(value);
        return isNaN(parsed) ? originalValue : parsed;
    }
    if (typeof originalValue === 'object') {
        try { return JSON.parse(value); }
        catch { throw new Error('Invalid JSON'); }
    }
    return value;
}
