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

export function avatarUrl(name) {
    return `/api/personas/${encodeURIComponent(name)}/avatar`;
}
