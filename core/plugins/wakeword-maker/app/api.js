// api.js - the plugin's doors, plus the three core doors the page needs (plugin settings, env build).
const BASE = '/api/plugin/wakeword-maker';
const NAME = 'wakeword-maker';
const csrf = () => document.querySelector('meta[name="csrf-token"]')?.content || '';

async function call(url, { method = 'GET', body, form } = {}) {
    const opts = { method, headers: { 'X-CSRF-Token': csrf() } };
    if (form) opts.body = form;
    else if (body !== undefined) { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body); }
    const r = await fetch(url, opts);
    let data = null;
    try { data = await r.json(); } catch { /* not json */ }
    if (!r.ok) {
        const err = new Error(data?.error || data?.detail || `HTTP ${r.status}`);
        err.status = r.status; err.data = data;
        throw err;
    }
    return data;
}

export const api = (path, opts) => call(BASE + path, opts);
export const getSettings = () => call(`/api/webui/plugins/${NAME}/settings`);
export const putSettings = (patch) => call(`/api/webui/plugins/${NAME}/settings`, { method: 'PUT', body: patch });
export const envStatus = () => call(`/api/plugins/${NAME}/env-status`);
export const buildEnv = () => call(`/api/plugins/${NAME}/build-env`, { method: 'POST' });
export const audioUrl = (slug, collection, cid) => `${BASE}/projects/${slug}/clips/${collection}/${cid}/audio`;

export const fmtBytes = (n) => {
    if (n == null) return '';
    if (n >= 1e9) return (n / 1e9).toFixed(n >= 1e10 ? 0 : 1) + ' GB';
    if (n >= 1e6) return (n / 1e6).toFixed(0) + ' MB';
    if (n >= 1e3) return (n / 1e3).toFixed(0) + ' kB';
    return n + ' B';
};
export const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
