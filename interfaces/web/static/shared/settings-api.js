// shared/settings-api.js - Settings API wrapper
import { fetchWithTimeout } from './fetch.js';
import { wordsOf } from './fetch.js';

export async function getAllSettings() {
    return await fetchWithTimeout('/api/settings');
}

export async function updateSettingsBatch(settings, opts = {}) {
    const body = { settings };
    if (opts.confirm_embedding_swap) body.confirm_embedding_swap = true;
    return await fetchWithTimeout('/api/settings/batch', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
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
    const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
    const res = await fetch('/api/avatar/upload', { method: 'POST', headers: { 'X-CSRF-Token': csrf }, body: formData });
    if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(wordsOf(err, res.status));
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
    // `typeof null === 'object'` in JavaScript (legacy quirk). A null default
    // means "no canonical type" — typically a polymorphic setting that can hold
    // null, a string, or a number depending on user choice (e.g. audio device).
    // Don't try to JSON.parse the value — pass it through and let the backend
    // accept whatever type the UI control produces. 2026-05-16 fix.
    if (originalValue !== null && typeof originalValue === 'object') {
        // If value is ALREADY an object/array (came from a custom control via
        // ctx.markChanged with a JS structure rather than a serialized string),
        // pass it through — JSON.parse would coerce arrays to comma-strings
        // via .toString() and throw, breaking the whole save batch. The
        // backend accepts the parsed structure natively. Privacy whitelist
        // (network.js) is the canonical case — its markChanged passes a JS
        // array, not a string. 2026-05-16 fix.
        if (value !== null && typeof value === 'object') return value;
        try { return JSON.parse(value); }
        catch { throw new Error('Invalid JSON'); }
    }
    return value;
}
