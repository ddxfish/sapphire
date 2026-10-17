// WordPress settings plugin (multi-site).
// Global config (destructive toggle + rotation) on top; shared account-manager for the sites.
// The PIN shows inside the Mind > WordPress scope dropdown - this panel is just setup.

import { registerPluginSettings } from '/static/shared/plugin-registry.js';
import { createAccountManager } from '/static/shared/account-manager.js';

const CONFIG_URL = '/api/plugin/wordpress/config';

function csrf() {
    const m = document.querySelector('meta[name="csrf-token"]');
    return m ? m.getAttribute('content') : '';
}
function csrfHeaders(extra = {}) {
    return { 'X-CSRF-Token': csrf(), ...extra };
}

const manager = createAccountManager({
    prefix: 'wp',
    entityName: 'Site',
    listEndpoint: '/api/plugin/wordpress/accounts',
    listKey: 'accounts',
    deleteEndpoint: (scope) => `/api/plugin/wordpress/accounts/${encodeURIComponent(scope)}`,
    formatItem: (item) => ({
        name: item.friendly_name || item.scope,
        detail: (item.unsupervised
                    ? '⚠ UNSUPERVISED — no PIN required  ·  '
                    : (item.pin ? `🔑 PIN ${item.pin}  ·  ` : ''))
            + (item.base_url || '')
            + (item.has_password ? '' : ' — no Application Password yet'),
    }),
    hint: 'Each site maps to a chat scope. Pick which site Sapphire can use per-chat in the Mind > WordPress dropdown.',
    addLabel: '+ Add Site',
    addPrompt: 'Short name for this site (e.g. "sapphireblue", "blog"):',
    renderEditor: renderSiteEditor,
});

function renderSiteEditor(body, scope, item, helpers) {
    const s = item || {};
    body.innerHTML = `
        <div class="am-group">
            <label for="wp-base-url">Site URL</label>
            <input type="text" id="wp-base-url" value="${s.base_url || ''}" placeholder="https://sapphireblue.dev">
            <div class="am-hint">The site's home URL. The REST API is found automatically.</div>
        </div>
        <div class="am-group">
            <label for="wp-username">Username</label>
            <input type="text" id="wp-username" value="${s.username || ''}" placeholder="WordPress username">
        </div>
        <div class="am-group">
            <label for="wp-app-password">Application Password</label>
            <input type="password" id="wp-app-password" value="" placeholder="${item ? 'Leave blank to keep existing…' : 'xxxx xxxx xxxx xxxx xxxx xxxx'}">
            <div class="am-hint">WordPress admin → Users → Profile → Application Passwords. Not your login password.</div>
        </div>
        <div class="am-row" style="gap:12px;margin-top:8px">
            <button type="button" class="am-action-btn" id="wp-save-btn">Save</button>
        </div>
    `;
    body.querySelector('#wp-save-btn').addEventListener('click', async () => {
        const btn = body.querySelector('#wp-save-btn');
        const base_url = body.querySelector('#wp-base-url').value.trim();
        const username = body.querySelector('#wp-username').value.trim();
        const app_password = body.querySelector('#wp-app-password').value.trim();
        if (!base_url || !username) {
            helpers.showResult(false, 'Site URL and username are required');
            return;
        }
        btn.disabled = true;
        btn.textContent = 'Saving…';
        try {
            const res = await fetch(`/api/plugin/wordpress/accounts/${encodeURIComponent(scope)}`, {
                method: 'PUT',
                headers: helpers.csrfHeaders({ 'Content-Type': 'application/json' }),
                body: JSON.stringify({ base_url, username, app_password, label: scope }),
            });
            const data = await res.json();
            if (data.success) {
                btn.textContent = 'Saved';
                btn.className = 'am-action-btn success';
                await manager.loadItems();
            } else {
                throw new Error(data.detail || 'Save failed');
            }
        } catch (e) {
            btn.textContent = 'Error';
            btn.className = 'am-action-btn error';
            helpers.showResult(false, e.message);
        }
        setTimeout(() => { btn.textContent = 'Save'; btn.className = 'am-action-btn'; btn.disabled = false; }, 3000);
    });
}

function renderConfig(container) {
    const box = document.createElement('div');
    box.className = 'am-group';
    box.style.cssText = 'border:1px solid var(--border,#333);border-radius:8px;padding:12px;margin-bottom:14px;transition:all .15s';
    box.innerHTML = `
        <label style="display:flex;align-items:center;gap:8px;cursor:pointer;font-weight:600">
            <input type="checkbox" id="wp-unsupervised">
            <span>⚠ Allow destructive operations unsupervised (no PIN)</span>
        </label>
        <div class="am-hint">By default, destructive actions (permanent delete, user delete, plugin toggle,
        settings change) require a PIN — shown next to each site below and in the Mind &gt; WordPress dropdown,
        burning after each use. <b>Check this only to let Sapphire perform those actions on her own, with no PIN.</b></div>
        <div class="am-row" id="wp-rotation-row" style="gap:8px;margin-top:8px">
            <label for="wp-rotation">PIN rotation</label>
            <select id="wp-rotation">
                <option value="burn_on_use">Burn on use (new PIN after each action)</option>
                <option value="static">Static (don't rotate)</option>
            </select>
        </div>
    `;
    container.appendChild(box);

    const cb = box.querySelector('#wp-unsupervised');
    const rotRow = box.querySelector('#wp-rotation-row');
    const rot = box.querySelector('#wp-rotation');

    function paint() {
        // Red + alarm when unsupervised (dangerous); rotation only matters when PIN-gated.
        if (cb.checked) {
            box.style.borderColor = 'var(--error,#dc3545)';
            box.style.background = 'rgba(220,53,69,0.10)';
            rotRow.style.display = 'none';
        } else {
            box.style.borderColor = 'var(--border,#333)';
            box.style.background = '';
            rotRow.style.display = '';
        }
    }

    fetch(CONFIG_URL).then(r => r.json()).then(cfg => {
        cb.checked = !!cfg.unsupervised;
        rot.value = cfg.rotation || 'burn_on_use';
        paint();
    }).catch(() => {});

    async function saveConfig() {
        paint();
        try {
            await fetch(CONFIG_URL, {
                method: 'PUT',
                headers: csrfHeaders({ 'Content-Type': 'application/json' }),
                body: JSON.stringify({ unsupervised: cb.checked, rotation: rot.value }),
            });
            await manager.loadItems();  // labels reflect PIN vs unsupervised
        } catch (e) { /* non-fatal */ }
    }
    cb.addEventListener('change', saveConfig);
    rot.addEventListener('change', saveConfig);
}

export default {
    name: 'wordpress',
    init(container) {
        registerPluginSettings({
            id: 'wordpress',
            name: 'WordPress',
            icon: '🌐',
            helpText: 'Manage WordPress sites. Pick which site Sapphire uses per-chat in the Mind > WordPress dropdown. Destructive actions are PIN-gated.',
            render: (c) => {
                renderConfig(c);
                const listWrap = document.createElement('div');
                c.appendChild(listWrap);
                manager.renderList(listWrap);
            },
            load: async () => { await manager.loadItems(); return {}; },
        });
    },
    destroy() {},
};
