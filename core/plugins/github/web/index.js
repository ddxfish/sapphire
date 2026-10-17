// GitHub plugin settings — multi-account manager.
// PAT-paste flow with optional Validate that auto-fills username from api.github.com/user.

import { registerPluginSettings } from '/static/shared/plugin-registry.js';
import { createAccountManager } from '/static/shared/account-manager.js';

const manager = createAccountManager({
    prefix: 'gh',
    entityName: 'Account',
    listEndpoint: '/api/github/accounts',
    listKey: 'accounts',
    deleteEndpoint: (scope) => `/api/github/accounts/${encodeURIComponent(scope)}`,
    formatItem: (item) => ({
        name: item.label || item.scope,
        detail: item.has_token ? `${item.username || '(no username)'} — token saved` : 'No token'
    }),
    hint: 'Each scope is a separate GitHub identity. Pick which one is active per-chat in the sidebar dropdown.',
    addLabel: '+ Add GitHub Account',
    addPrompt: 'Scope name for this account (e.g. "default", "sapphireprime", "work"):',
    listHeader: `<div class="am-hint" style="line-height:1.4">
        Use a <a href="https://github.com/settings/tokens?type=beta" target="_blank" rel="noopener">fine-grained PAT</a>
        with Contents (RW), Issues (RW), Pull requests (RW), and Administration (RW).
        Repository access scoped to specific repos is recommended.
    </div>`,
    renderEditor: renderEditor,
});


function renderEditor(body, scope, item, helpers) {
    const s = item || {};

    body.innerHTML = `
        <div class="am-group">
            <label for="gh-username">GitHub Username</label>
            <input type="text" id="gh-username" value="${escapeAttr(s.username || '')}" placeholder="auto-filled by Validate">
            <div class="am-hint">Filled automatically when you click Validate. You can also type it.</div>
        </div>

        <div class="am-group">
            <label for="gh-pat">Personal Access Token</label>
            <input type="password" id="gh-pat" value="" placeholder="${item && s.has_token ? 'Leave blank to keep existing token' : 'ghp_... or github_pat_...'}">
            <div class="am-hint">Stored encrypted at rest. Never logged. Never sent anywhere except api.github.com.</div>
        </div>

        <div class="am-group">
            <label for="gh-label">Display Label (optional)</label>
            <input type="text" id="gh-label" value="${escapeAttr(s.label || scope)}" placeholder="${scope}">
            <div class="am-hint">Shown in the sidebar dropdown. Defaults to the scope name.</div>
        </div>

        <div class="am-row" style="gap:12px;margin-top:8px">
            <button type="button" class="am-action-btn" id="gh-validate-btn">Validate</button>
            <button type="button" class="am-action-btn" id="gh-save-btn">Save</button>
        </div>

        <div id="gh-validate-result"></div>
    `;

    const usernameEl = body.querySelector('#gh-username');
    const patEl = body.querySelector('#gh-pat');
    const labelEl = body.querySelector('#gh-label');
    const validateBtn = body.querySelector('#gh-validate-btn');
    const saveBtn = body.querySelector('#gh-save-btn');
    const resultEl = body.querySelector('#gh-validate-result');

    validateBtn.addEventListener('click', async () => {
        const pat = patEl.value.trim();
        if (!pat) {
            helpers.showResult(false, 'Paste a PAT first to validate.');
            return;
        }

        validateBtn.disabled = true;
        validateBtn.textContent = 'Validating...';
        resultEl.innerHTML = '';

        try {
            const res = await fetch('/api/plugin/github/validate', {
                method: 'POST',
                headers: helpers.csrfHeaders({ 'Content-Type': 'application/json' }),
                body: JSON.stringify({ pat })
            });
            const data = await res.json();
            if (data.valid) {
                if (data.username) usernameEl.value = data.username;
                resultEl.innerHTML = `<div class="am-hint" style="color:var(--success,#28a745);font-size:12px;margin-top:4px">
                    ✓ Valid token for <strong>${escapeHtml(data.username)}</strong>${data.name ? ` (${escapeHtml(data.name)})` : ''}
                    <br><span style="opacity:0.85">Scopes: ${escapeHtml(data.scopes || '(fine-grained PAT)')}</span>
                </div>`;
                validateBtn.textContent = 'Valid';
                validateBtn.className = 'am-action-btn success';
            } else {
                resultEl.innerHTML = `<div class="am-hint" style="color:var(--error,#dc3545);font-size:12px;margin-top:4px">
                    ✗ ${escapeHtml(data.error || 'Token invalid')}
                </div>`;
                validateBtn.textContent = 'Invalid';
                validateBtn.className = 'am-action-btn error';
            }
        } catch (e) {
            resultEl.innerHTML = `<div class="am-hint" style="color:var(--error,#dc3545);font-size:12px;margin-top:4px">
                ✗ ${escapeHtml(e.message)}
            </div>`;
            validateBtn.textContent = 'Error';
            validateBtn.className = 'am-action-btn error';
        }

        setTimeout(() => {
            validateBtn.textContent = 'Validate';
            validateBtn.className = 'am-action-btn';
            validateBtn.disabled = false;
        }, 3000);
    });

    saveBtn.addEventListener('click', async () => {
        const username = usernameEl.value.trim();
        const pat = patEl.value.trim();
        const label = labelEl.value.trim() || scope;

        if (!username) {
            helpers.showResult(false, 'Username is required. Click Validate to auto-fill, or type it.');
            return;
        }
        if (!item && !pat) {
            helpers.showResult(false, 'Personal access token is required.');
            return;
        }

        saveBtn.disabled = true;
        saveBtn.textContent = 'Saving...';

        try {
            const res = await fetch(`/api/github/accounts/${encodeURIComponent(scope)}`, {
                method: 'PUT',
                headers: helpers.csrfHeaders({ 'Content-Type': 'application/json' }),
                body: JSON.stringify({ username, pat, label })
            });
            const data = await res.json();
            if (res.ok && data.success) {
                saveBtn.textContent = 'Saved';
                saveBtn.className = 'am-action-btn success';
                await manager.loadItems();
                setTimeout(() => helpers.reloadList(), 600);
            } else {
                throw new Error(data.detail || 'Save failed');
            }
        } catch (e) {
            saveBtn.textContent = 'Error';
            saveBtn.className = 'am-action-btn error';
            helpers.showResult(false, e.message);
        }

        setTimeout(() => {
            saveBtn.textContent = 'Save';
            saveBtn.className = 'am-action-btn';
            saveBtn.disabled = false;
        }, 3000);
    });
}


function escapeAttr(s) {
    return String(s || '').replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;');
}

function escapeHtml(s) {
    return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}


export default {
    name: 'github',

    init(container) {
        registerPluginSettings({
            id: 'github',
            name: 'GitHub',
            icon: '🐙',
            helpText: 'Manage GitHub accounts. Each scope is a separate identity — the AI uses the active scope’s PAT for repo / file / issue / search calls.',
            render: (c) => manager.renderList(c),
            load: async () => { await manager.loadItems(); return {}; },
        });
    },

    destroy() {}
};
