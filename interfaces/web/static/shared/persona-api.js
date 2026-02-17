// shared/persona-api.js - Persona API helpers
import { fetchWithTimeout } from './fetch.js';

export const listPersonas = () => fetchWithTimeout('/api/personas');

export const getPersona = (name) => fetchWithTimeout(`/api/personas/${encodeURIComponent(name)}`);

export const createPersona = (data) => fetchWithTimeout('/api/personas', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data)
});

export const updatePersona = (name, data) => fetchWithTimeout(`/api/personas/${encodeURIComponent(name)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data)
});

export const deletePersona = (name) => fetchWithTimeout(`/api/personas/${encodeURIComponent(name)}`, {
    method: 'DELETE'
});

export const duplicatePersona = (name, newName) => fetchWithTimeout(`/api/personas/${encodeURIComponent(name)}/duplicate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name: newName })
});

export const loadPersona = (name) => fetchWithTimeout(`/api/personas/${encodeURIComponent(name)}/load`, {
    method: 'POST'
});

export const createFromChat = (name) => fetchWithTimeout('/api/personas/from-chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name })
});

export const uploadAvatar = async (name, file) => {
    const formData = new FormData();
    formData.append('file', file);
    const res = await fetch(`/api/personas/${encodeURIComponent(name)}/avatar`, {
        method: 'POST',
        body: formData
    });
    if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || 'Upload failed');
    }
    return res.json();
};

export const deleteAvatar = (name) => fetchWithTimeout(`/api/personas/${encodeURIComponent(name)}/avatar`, {
    method: 'DELETE'
});

export function avatarUrl(name) {
    return `/api/personas/${encodeURIComponent(name)}/avatar`;
}

export function avatarFallback(name, color) {
    const initial = (name || '?')[0].toUpperCase();
    const c = color || '#888';
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><circle cx="50" cy="50" r="48" fill="${c}18" stroke="${c}" stroke-width="3"/><text x="50" y="54" text-anchor="middle" dominant-baseline="middle" font-family="system-ui,sans-serif" font-size="44" font-weight="600" fill="${c}">${initial}</text></svg>`;
    return `data:image/svg+xml,${encodeURIComponent(svg)}`;
}

export function avatarImg(name, color, cls, avatar) {
    const fb = avatarFallback(name, color);
    const src = avatar ? avatarUrl(name) : fb;
    const onerror = avatar ? `this.onerror=null;this.src='${fb}'` : '';
    return `<img class="${cls}" src="${src}" alt="" loading="lazy"${onerror ? ` onerror="${onerror}"` : ''}>`;
}
