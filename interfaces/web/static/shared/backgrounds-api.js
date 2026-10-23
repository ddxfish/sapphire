// shared/backgrounds-api.js - Scene background library API helpers.
import { fetchWithTimeout } from './fetch.js';

export const listBackgrounds = () => fetchWithTimeout('/api/backgrounds');

// Upload re-encodes server-side to webp (full + thumb). Throws on 4xx (e.g. 409
// "already exists" -> caller can retry with overwrite=true).
export const uploadBackground = (name, file, overwrite = false) => {
    const fd = new FormData();
    fd.append('name', name);
    fd.append('overwrite', overwrite ? 'true' : 'false');
    fd.append('file', file);
    return fetchWithTimeout('/api/backgrounds', { method: 'POST', body: fd });
};

export const deleteBackground = (name) =>
    fetchWithTimeout(`/api/backgrounds/${encodeURIComponent(name)}`, { method: 'DELETE' });

export const backgroundUrl = (name) => `/api/backgrounds/${encodeURIComponent(name)}`;
export const backgroundThumbUrl = (name) => `/api/backgrounds/${encodeURIComponent(name)}?thumb=1`;
