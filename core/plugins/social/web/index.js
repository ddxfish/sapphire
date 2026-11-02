// core/plugins/social/web/index.js - the Social card: Outbox on top, profiles with
// their connections, the daily-round button, the plugin's settings at the bottom.
// Shell: shared/account-manager.js (profiles = the "accounts"); provider forms come
// from each provider's config_schema through shared/plugin-settings-renderer.js.

import { registerPluginSettings } from '/static/shared/plugin-registry.js';
import { createAccountManager } from '/static/shared/account-manager.js';
import { renderSettingsForm, readSettingsForm } from '/static/shared/plugin-settings-renderer.js';
import pluginsAPI from '/static/shared/plugins-api.js';
import { flash } from '/static/shared/notify.js';

const API = '/api/plugin/social';
let _providers = [];
let _personas = [];

const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const when = ts => ts ? new Date(ts * 1000).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '';

function csrf() { return document.querySelector('meta[name="csrf-token"]')?.content || ''; }

async function call(method, path, body) {
    const res = await fetch(`${API}/${path}`, {
        method, headers: { 'X-CSRF-Token': csrf(), 'Content-Type': 'application/json' },
        body: body === undefined ? undefined : JSON.stringify(body),
    });
    let data = {};
    try { data = await res.json(); } catch { /* no body */ }
    if (!res.ok && !data.detail && !data.error) data.detail = `HTTP ${res.status}`;
    if (data.error && !data.detail) data.detail = data.error;
    return data;
}

function injectStyles() {
    if (document.getElementById('social-card-styles')) return;
    const st = document.createElement('style');
    st.id = 'social-card-styles';
    st.textContent = `
        .so-section { margin: 12px 0; padding: 10px 12px; background: var(--bg-secondary); border: 1px solid var(--border); border-radius: var(--radius-md); }
        .so-head { display: flex; align-items: center; gap: 10px; margin-bottom: 6px; }
        .so-muted { color: var(--text-muted); font-size: var(--font-sm); }
        .so-ok { color: var(--success); } .so-failed { color: var(--error); } .so-posted { color: var(--success); } .so-rejected { color: var(--text-muted); }
        .so-row, .so-conn { display: flex; gap: 10px; align-items: flex-start; padding: 8px 0; border-top: 1px solid var(--border); }
        .so-row .so-body, .so-conn-main { flex: 1; min-width: 0; }
        .so-text { white-space: pre-wrap; word-break: break-word; }
        .so-thumb { width: 64px; height: 64px; object-fit: cover; border-radius: var(--radius-sm); flex: none; }
        .so-actions { display: flex; gap: 6px; flex-wrap: wrap; align-items: center; }
        .so-conn label { display: flex; align-items: center; gap: 4px; white-space: nowrap; }
        .so-form { margin-top: 8px; padding: 10px; border: 1px dashed var(--border-light); border-radius: var(--radius-md); }
        .so-form .setting-row { padding: 6px 0; }
        .so-banner { padding: 8px 10px; border-radius: var(--radius-sm); margin-bottom: 8px; font-size: var(--font-sm); }
        .so-banner.ok { background: var(--success-light, rgba(76,175,80,.12)); border: 1px solid var(--success); }
        .so-banner.bad { background: var(--error-light, rgba(239,83,80,.12)); border: 1px solid var(--error); }
        code.so-uri { user-select: all; font-size: var(--font-sm); }
    `;
    document.head.appendChild(st);
}

async function loadProviders() {
    const d = await call('GET', 'providers');
    _providers = d.providers || [];
}
const provider = id => _providers.find(p => p.provider === id);

// ── the shell ────────────────────────────────────────────────────────────────
const manager = createAccountManager({
    prefix: 'social',
    entityName: 'Profile',
    listEndpoint: `${API}/profiles`,
    listKey: 'profiles',
    deleteEndpoint: name => `${API}/profiles/${encodeURIComponent(name)}`,
    formatItem: p => ({
        name: esc(p.label || p.name),
        detail: p.connections?.length
            ? p.connections.map(c => `${esc(c.icon || '')} ${esc(c.name)}${c.identity ? ' ' + esc(c.identity) : ''}${c.connected ? '' : ' (not connected)'}`).join(' · ')
            : 'no connections yet',
    }),
    hint: 'Start with ONE profile (your name, say). A profile is who posts, and it holds every platform connection at once: one Facebook Page, one X account, one Instagram… Make a second profile only for a second persona with her own accounts. A chat picks its profile in Mind › Social, or the persona does.',
    addLabel: '+ Add Profile',
    addPrompt: 'Profile name (e.g. "sapphire", "anita"):',
    listHeader: '<div id="social-outbox"></div>',
    listFooter: (footer, helpers) => {
        const top = footer.closest('.am-form')?.querySelector('#social-outbox');
        if (top) paintOutbox(top);
        paintSettings(footer);
    },
    renderEditor: renderProfileEditor,
});

// ── Outbox ───────────────────────────────────────────────────────────────────
async function paintOutbox(el) {
    const d = await call('GET', 'outbox');
    const rows = d.rows || [];
    const drafts = rows.filter(r => r.status === 'draft');
    const history = rows.filter(r => r.status !== 'draft').slice(0, 12);
    const lc = d.last_connect;
    const banner = lc && (Date.now() / 1000 - (lc.ts || 0)) < 900
        ? `<div class="so-banner ${lc.ok ? 'ok' : 'bad'}">Connect ${esc(lc.connection)}: ${esc(lc.message)}</div>` : '';
    const row = (r, draft) => `
        <div class="so-row" data-id="${esc(r.id)}">
            ${r.image_count ? `<img class="so-thumb" src="${API}/outbox/${esc(r.id)}/image/0" alt="">` : ''}
            <div class="so-body">
                <div class="so-muted">${esc(r.profile)}/${esc(r.connection)} · ${when(r.created)}${r.result?.reply_to ? ' · reply' : ''}${draft ? '' : ` · <span class="so-${esc(r.status)}">${esc(r.status)}</span>`}${r.result?.url ? ` · <a href="${esc(r.result.url)}" target="_blank" rel="noopener">open</a>` : ''}${r.result?.error ? ` · <span class="so-failed">${esc(r.result.error)}</span>` : ''}</div>
                <div class="so-text">${esc(r.text)}</div>
                ${r.link ? `<div class="so-muted">${esc(r.link)}</div>` : ''}
            </div>
            ${draft ? '<div class="so-actions"><button type="button" class="am-action-btn so-approve">Post</button><button type="button" class="am-action-btn so-reject">Reject</button></div>' : ''}
        </div>`;
    el.innerHTML = `
        <div class="so-section">
            ${banner}
            <div class="so-head"><b>Outbox</b><span class="so-muted">${drafts.length ? `${drafts.length} waiting for you` : 'nothing waiting'}</span>
                ${history.length ? '<button type="button" class="am-action-btn so-clear" style="margin-left:auto">Clear history</button>' : ''}</div>
            ${drafts.map(r => row(r, true)).join('')}
            ${history.length ? `<details><summary class="so-muted">Recent (${history.length})</summary>${history.map(r => row(r, false)).join('')}</details>` : ''}
        </div>`;
    el.querySelectorAll('.so-approve').forEach(b => b.addEventListener('click', async () => {
        const id = b.closest('.so-row').dataset.id;
        b.disabled = true; b.textContent = 'Posting…';
        const r = await call('POST', `outbox/${encodeURIComponent(id)}/approve`);
        flash(r.success ? `Posted: ${r.detail}` : `Not posted: ${r.detail}`, r.success ? 'success' : 'error');
        paintOutbox(el);
    }));
    el.querySelectorAll('.so-reject').forEach(b => b.addEventListener('click', async () => {
        const id = b.closest('.so-row').dataset.id;
        await call('POST', `outbox/${encodeURIComponent(id)}/reject`);
        paintOutbox(el);
    }));
    el.querySelector('.so-clear')?.addEventListener('click', async () => {
        await call('DELETE', 'outbox');
        paintOutbox(el);
    });
}

// ── settings (the manifest's, drawn here because settingsUI is "plugin") ────
async function paintSettings(footer) {
    const d = await call('GET', 'config');
    if (!d.schema?.length) return;
    const box = document.createElement('div');
    box.className = 'so-section';
    box.innerHTML = '<div class="so-head"><b>Settings</b><button type="button" class="am-action-btn" id="so-settings-save" style="margin-left:auto">Save</button></div><div id="so-settings"></div>';
    footer.appendChild(box);
    const form = box.querySelector('#so-settings');
    renderSettingsForm(form, d.schema, d.values || {});
    box.querySelector('#so-settings-save').addEventListener('click', async () => {
        try {
            await pluginsAPI.saveSettings('social', readSettingsForm(form, d.schema));
            flash('Social settings saved', 'success');
        } catch (e) {
            flash(`Save failed: ${e.message}`, 'error');
        }
    });
}

// ── one profile ─────────────────────────────────────────────────────────────
async function renderProfileEditor(body, name, item, helpers) {
    if (!item) {
        const d = await call('PUT', `profiles/${encodeURIComponent(name)}`, { label: name });
        if (!d.success) { helpers.showResult(false, d.detail || 'Could not create the profile'); return; }
        await manager.loadItems();
        item = manager.getItems().find(i => i.scope === d.name) || { name: d.name, scope: d.name, label: d.name, connections: [] };
    }
    await loadProviders();
    if (!_personas.length) {
        try { _personas = (await (await fetch('/api/personas')).json()).personas || []; } catch { _personas = []; }
    }
    const profile = item.name;
    body.innerHTML = `
        <div class="am-group">
            <label for="so-label">Label</label>
            <div class="am-row" style="gap:8px"><input type="text" id="so-label" value="${esc(item.label || '')}"><button type="button" class="am-action-btn" id="so-label-save">Save</button></div>
            <div class="am-hint">What the Mind › Social dropdown shows.</div>
        </div>
        <div class="so-section">
            <div class="so-head"><b>Connections</b><button type="button" class="am-action-btn" id="so-add-conn" style="margin-left:auto">+ Add connection</button></div>
            <div id="so-conns"></div>
            <div id="so-conn-form"></div>
        </div>
        <div class="so-section">
            <div class="so-head"><b>Daily round</b></div>
            <div class="am-hint">Makes a Triggers task: at this time, as this persona, with this profile as its Social scope, she reads her inbox, looks for news and posts (or queues drafts). Edit or delete it under Triggers.</div>
            <div class="am-row" style="gap:8px;flex-wrap:wrap;align-items:center">
                <select id="so-persona">${_personas.map(p => `<option value="${esc(p.name)}">${esc(p.name)}</option>`).join('') || '<option value="">(default persona)</option>'}</select>
                <input type="time" id="so-time" value="09:00">
                <input type="text" id="so-chat" value="social" placeholder="chat" style="width:120px">
                <button type="button" class="am-action-btn" id="so-round">Create task</button>
            </div>
        </div>`;

    const refresh = async () => {
        await manager.loadItems();
        item = manager.getItems().find(i => i.scope === profile) || item;
        paintConns();
    };

    body.querySelector('#so-label-save').addEventListener('click', async () => {
        const d = await call('PUT', `profiles/${encodeURIComponent(profile)}`, { label: body.querySelector('#so-label').value.trim() });
        helpers.showResult(!!d.success, d.success ? 'Label saved' : (d.detail || 'Save failed'));
        if (d.success) await manager.loadItems();
    });
    body.querySelector('#so-add-conn').addEventListener('click', () => openConnForm(null));
    body.querySelector('#so-round').addEventListener('click', async () => {
        const d = await call('POST', 'round', {
            profile, persona: body.querySelector('#so-persona').value,
            time: body.querySelector('#so-time').value || '09:00', chat: body.querySelector('#so-chat').value.trim() || 'social',
        });
        helpers.showResult(!!d.success, d.success ? `Task "${d.name}" made for ${d.time} — see Triggers` : (d.detail || 'Could not make the task'));
    });

    function paintConns() {
        const el = body.querySelector('#so-conns');
        const conns = item.connections || [];
        if (!conns.length) { el.innerHTML = '<div class="am-hint">No connections yet. Add one per platform — they all live in this profile.</div>'; return; }
        el.innerHTML = conns.map(c => `
            <div class="so-conn" data-conn="${esc(c.name)}" data-provider="${esc(c.provider)}">
                <div class="so-conn-main">
                    <b>${esc(c.icon || '')} ${esc(c.name)}</b> <span class="so-muted">${esc(c.provider_label)}${c.identity ? ' · ' + esc(c.identity) : ''}${c.loaded ? '' : ' · provider off'}</span>
                    <div class="so-muted">${c.connected ? '<span class="so-ok">connected</span>' : '<span class="so-failed">not connected</span>'}${c.meta?.expires_at ? ' · token until ' + when(c.meta.expires_at) : ''}</div>
                </div>
                <label class="so-muted">mode <select class="so-mode"><option value="draft"${c.mode === 'draft' ? ' selected' : ''}>draft</option><option value="auto"${c.mode === 'auto' ? ' selected' : ''}>auto</option></select></label>
                <label class="so-muted" title="Mark posts as AI-made where the platform has a flag"><input type="checkbox" class="so-disclose"${c.disclose ? ' checked' : ''}> AI label</label>
                <div class="so-actions">
                    ${provider(c.provider)?.auth === 'oauth' ? `<button type="button" class="am-action-btn so-connect">${c.connected ? 'Reconnect' : 'Connect'}</button>` : ''}
                    <button type="button" class="am-action-btn so-test">Test</button>
                    <button type="button" class="am-action-btn so-edit">Edit</button>
                    <button type="button" class="am-delete-btn so-del">Delete</button>
                </div>
            </div>`).join('');
        el.querySelectorAll('.so-conn').forEach(row => {
            const cname = row.dataset.conn, prov = row.dataset.provider;
            const put = async patch => {
                const d = await call('PUT', `profiles/${encodeURIComponent(profile)}/connections/${encodeURIComponent(cname)}`, { provider: prov, ...patch });
                if (!d.success) helpers.showResult(false, d.detail || 'Save failed');
                return d;
            };
            row.querySelector('.so-mode').addEventListener('change', e => put({ mode: e.target.value }));
            row.querySelector('.so-disclose').addEventListener('change', e => put({ disclose: e.target.checked }));
            row.querySelector('.so-connect')?.addEventListener('click', () => {
                window.location.href = `${API}/connect/${encodeURIComponent(prov)}/${encodeURIComponent(profile)}/${encodeURIComponent(cname)}`;
            });
            row.querySelector('.so-test').addEventListener('click', async () => {
                const d = await call('POST', `profiles/${encodeURIComponent(profile)}/connections/${encodeURIComponent(cname)}/test`);
                helpers.showResult(!!d.success, d.success ? `OK: ${d.detail}` : (d.detail || 'Test failed'));
                await refresh();
            });
            row.querySelector('.so-edit').addEventListener('click', () => openConnForm(item.connections.find(c => c.name === cname)));
            row.querySelector('.so-del').addEventListener('click', async () => {
                if (!confirm(`Delete connection "${cname}"? Its tokens go with it.`)) return;
                await call('DELETE', `profiles/${encodeURIComponent(profile)}/connections/${encodeURIComponent(cname)}`);
                await refresh();
            });
        });
    }

    function openConnForm(existing) {
        const el = body.querySelector('#so-conn-form');
        if (!_providers.length) { el.innerHTML = '<div class="am-hint">No providers are loaded.</div>'; return; }
        const first = existing ? provider(existing.provider) : _providers[0];
        el.innerHTML = `
            <div class="so-form">
                <div class="am-row" style="gap:8px;align-items:center;flex-wrap:wrap">
                    <input type="text" id="so-conn-name" placeholder="name (e.g. page, main)" value="${esc(existing?.name || '')}"${existing ? ' disabled' : ''} style="width:160px">
                    <select id="so-conn-provider"${existing ? ' disabled' : ''}>${_providers.map(p => `<option value="${esc(p.provider)}"${p.provider === first?.provider ? ' selected' : ''}>${esc(p.icon)} ${esc(p.label)}</option>`).join('')}</select>
                </div>
                <div class="am-hint" id="so-conn-notes"></div>
                <div id="so-conn-fields"></div>
                <div class="am-hint" id="so-conn-redirect"></div>
                <div class="am-row" style="gap:8px;margin-top:8px"><button type="button" class="am-action-btn" id="so-conn-save">Save</button><button type="button" class="am-action-btn" id="so-conn-cancel">Cancel</button></div>
            </div>`;
        const sel = el.querySelector('#so-conn-provider');
        const fields = el.querySelector('#so-conn-fields');
        let p = first;
        const paintFields = () => {
            p = provider(sel.value) || first;
            el.querySelector('#so-conn-notes').textContent = p?.notes || '';
            const values = existing ? { ...(existing.config || {}) } : {};
            (existing?.secrets_set || []).forEach(k => { values[k] = '•'; });
            renderSettingsForm(fields, p?.config_schema || [], values);
            el.querySelector('#so-conn-redirect').innerHTML = p?.auth === 'oauth'
                ? `Redirect URI to paste into the ${esc(p.label)} developer console: <code class="so-uri">${esc(location.origin)}${API}/callback/${esc(p.provider)}</code>`
                : '';
        };
        paintFields();
        sel.addEventListener('change', paintFields);
        el.querySelector('#so-conn-cancel').addEventListener('click', () => { el.innerHTML = ''; });
        el.querySelector('#so-conn-save').addEventListener('click', async () => {
            const cname = (existing?.name || el.querySelector('#so-conn-name').value).trim().toLowerCase().replace(/[^a-z0-9_-]/g, '-');
            if (!cname) { helpers.showResult(false, 'Give the connection a name'); return; }
            const config = readSettingsForm(fields, p?.config_schema || []);
            const d = await call('PUT', `profiles/${encodeURIComponent(profile)}/connections/${encodeURIComponent(cname)}`, { provider: p.provider, label: cname, config });
            if (!d.success) { helpers.showResult(false, d.detail || 'Save failed'); return; }
            el.innerHTML = '';
            helpers.showResult(true, p.auth === 'oauth' && !d.connection?.connected ? 'Saved. Now click Connect on it.' : 'Saved.');
            await refresh();
        });
    }

    paintConns();
}

export default {
    name: 'social',
    init(container) {
        injectStyles();
        registerPluginSettings({
            id: 'social',
            name: 'Social',
            icon: '📣',
            helpText: 'One profile holds all of your platform connections; a second profile is for a second persona. Drafts wait in the Outbox until you post them.',
            render: c => manager.renderList(c),
            load: async () => { await loadProviders(); await manager.loadItems(); return {}; },
        });
    },
    destroy() {},
};
