// shared/store-api.js — thin client for the in-app Plugin Store proxy.
//
// All endpoints are auth-gated server-side. fetchWithTimeout handles the
// 401 redirect to /login if the session expires mid-browse.

import { fetchWithTimeout } from './fetch.js';


export async function getStoreStatus() {
    return fetchWithTimeout('/api/store/status');
}


export async function listStorePlugins({
    q = null,
    category = null,
    featured = null,
    sort = null,
    page = 1,
    perPage = 20,
} = {}) {
    const params = new URLSearchParams();
    if (q) params.set('q', q);
    if (category) params.set('category', category);
    if (featured === true) params.set('featured', 'true');
    if (sort) params.set('sort', sort);
    if (page && page !== 1) params.set('page', String(page));
    if (perPage && perPage !== 20) params.set('per_page', String(perPage));
    const qs = params.toString();
    return fetchWithTimeout(`/api/store/plugins/list${qs ? '?' + qs : ''}`);
}


export async function getStorePlugin(slug) {
    return fetchWithTimeout(`/api/store/plugins/${encodeURIComponent(slug)}`);
}


export async function getStoreCategories() {
    return fetchWithTimeout('/api/store/categories');
}


/**
 * Install a plugin from the store. Posts source + store_slug so the
 * installed plugin's plugin_state captures Store provenance (Stage 4 fields).
 */
export async function installFromStore({ githubUrl, storeSlug }) {
    const fd = new FormData();
    fd.append('url', githubUrl);
    fd.append('source', 'store');
    if (storeSlug) fd.append('store_slug', storeSlug);
    return fetchWithTimeout('/api/plugins/install', {
        method: 'POST',
        body: fd,
    });
}


/* ── Persona store (sapphire-prompts/v1) ──────────────────────────────────── */

export async function listStorePersonas({
    q = null,
    category = null,
    featured = null,
    sort = null,
    page = 1,
    perPage = 20,
} = {}) {
    const params = new URLSearchParams();
    if (q) params.set('q', q);
    if (category) params.set('category', category);
    if (featured === true) params.set('featured', 'true');
    if (sort) params.set('sort', sort);
    if (page && page !== 1) params.set('page', String(page));
    if (perPage && perPage !== 20) params.set('per_page', String(perPage));
    const qs = params.toString();
    return fetchWithTimeout(`/api/store/personas/list${qs ? '?' + qs : ''}`);
}


export async function getStorePersona(slug) {
    return fetchWithTimeout(`/api/store/personas/${encodeURIComponent(slug)}`);
}


export async function getStorePersonaCategories() {
    return fetchWithTimeout('/api/store/personas/categories');
}


/** Download a persona's PNG card server-side and import it. */
export async function installPersonaFromStore(slug, { overwrite_prompt = false, overwrite_avatar = false, overwrite_persona = false, keep_components = [] } = {}) {
    const qs = new URLSearchParams();
    if (overwrite_prompt) qs.set('overwrite_prompt', 'true');
    if (overwrite_avatar) qs.set('overwrite_avatar', 'true');
    if (overwrite_persona) qs.set('overwrite_persona', 'true');
    if (keep_components && keep_components.length) qs.set('keep_components', keep_components.join(','));
    const q = qs.toString();
    return fetchWithTimeout(`/api/store/personas/${encodeURIComponent(slug)}/install${q ? '?' + q : ''}`, {
        method: 'POST',
    });
}
