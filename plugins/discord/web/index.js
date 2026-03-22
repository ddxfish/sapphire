// plugins/discord/web/index.js — Settings tab for Discord plugin
// Bot token management — much simpler than Telegram (no phone auth)

import { registerPluginSettings } from '/static/shared/plugin-registry.js';

const PLUGIN_NAME = 'discord';
const CSRF = () => document.querySelector('meta[name="csrf-token"]')?.content || '';

registerPluginSettings({
    id: PLUGIN_NAME,
    name: 'Discord',
    icon: '🎮',
    helpText: 'Discord bot accounts. Create a bot at discord.com/developers, enable Message Content Intent, and paste the token here.',

    render(container, settings) {
        container.innerHTML = `
            <h4 style="margin:0 0 12px">Bot Accounts</h4>
            <div id="dc-accounts-list"></div>
            <div id="dc-add-form" style="display:none"></div>
            <button class="btn btn-sm" id="dc-add-account" style="margin-top:12px">+ Add Bot</button>
        `;

        _loadAccounts(container);

        container.querySelector('#dc-add-account')?.addEventListener('click', () => {
            _showAddForm(container);
        });
    },

    load: async () => ({}),
    save: async () => ({ success: true }),
    getSettings: () => ({}),
});


async function _loadAccounts(container) {
    const list = container.querySelector('#dc-accounts-list');
    if (!list) return;

    try {
        const res = await fetch('/api/plugin/discord/accounts');
        if (!res.ok) throw new Error('Failed to fetch accounts');
        const data = await res.json();
        const accounts = data.accounts || [];

        if (accounts.length === 0) {
            list.innerHTML = '<p class="text-muted" style="font-size:0.9em">No bots configured. Add one to get started.</p>';
            return;
        }

        list.innerHTML = accounts.map(a => `
            <div class="setting-row" style="padding:10px 0;border-bottom:1px solid var(--border)" data-account="${_esc(a.name)}">
                <div class="setting-label">
                    <label>${_esc(a.bot_name || a.name)}</label>
                    <div class="setting-help">
                        ${a.connected
                            ? '<span style="color:var(--success)">Connected</span>'
                            : '<span class="text-muted">Disconnected</span>'}
                        ${a.bot_id ? ` \u2022 ID: ${a.bot_id}` : ''}
                    </div>
                </div>
                <div class="setting-input" style="display:flex;gap:8px">
                    <button class="btn btn-sm dc-test-account" data-name="${_esc(a.name)}">Test</button>
                    <button class="btn btn-sm btn-danger dc-delete-account" data-name="${_esc(a.name)}">Remove</button>
                </div>
            </div>
        `).join('');

        // Wire test buttons
        list.querySelectorAll('.dc-test-account').forEach(btn => {
            btn.addEventListener('click', async () => {
                const name = btn.dataset.name;
                btn.disabled = true;
                btn.textContent = 'Testing...';
                try {
                    const res = await fetch(`/api/plugin/discord/accounts/${name}/test`, {
                        method: 'POST',
                        headers: { 'X-CSRF-Token': CSRF() }
                    });
                    const data = await res.json();
                    if (data.success) {
                        btn.textContent = `\u2713 ${data.bot_name}`;
                        btn.className = 'btn btn-sm success';
                        _loadAccounts(container);
                    } else {
                        btn.textContent = '\u2717 Failed';
                        btn.className = 'btn btn-sm btn-danger';
                    }
                } catch (e) {
                    btn.textContent = 'Error';
                }
                setTimeout(() => { btn.textContent = 'Test'; btn.className = 'btn btn-sm'; btn.disabled = false; }, 3000);
            });
        });

        // Wire delete buttons
        list.querySelectorAll('.dc-delete-account').forEach(btn => {
            btn.addEventListener('click', async () => {
                const name = btn.dataset.name;
                if (!confirm(`Remove bot "${name}"?`)) return;
                btn.disabled = true;
                btn.textContent = 'Removing...';
                try {
                    await fetch(`/api/plugin/discord/accounts/${name}`, {
                        method: 'DELETE',
                        headers: { 'X-CSRF-Token': CSRF() }
                    });
                    _loadAccounts(container);
                } catch (e) {
                    btn.disabled = false;
                    btn.textContent = 'Remove';
                }
            });
        });
    } catch (e) {
        list.innerHTML = `<p style="color:var(--error)">Could not load accounts: ${e.message}</p>`;
    }
}


function _showAddForm(container) {
    const form = container.querySelector('#dc-add-form');
    if (!form) return;
    form.style.display = 'block';

    form.innerHTML = `
        <div style="padding:14px;background:var(--bg-secondary);border-radius:var(--radius-sm);border:1px solid var(--border);margin-top:12px">
            <h5 style="margin:0 0 10px">Add Discord Bot</h5>
            <div class="setting-row" style="padding:4px 0">
                <div class="setting-label">
                    <label>Account Name</label>
                    <div class="setting-help">A short label like "sapphire" or "modbot"</div>
                </div>
                <div class="setting-input"><input type="text" id="dc-add-name" placeholder="sapphire" style="width:100%"></div>
            </div>
            <div class="setting-row" style="padding:4px 0">
                <div class="setting-label">
                    <label>Bot Token</label>
                    <div class="setting-help">From discord.com/developers &rarr; Bot &rarr; Reset Token</div>
                </div>
                <div class="setting-input"><input type="password" id="dc-add-token" placeholder="paste bot token" style="width:100%"></div>
            </div>
            <div style="display:flex;gap:8px;margin-top:10px">
                <button class="btn btn-primary btn-sm" id="dc-add-save">Add Bot</button>
                <button class="btn btn-sm" id="dc-add-cancel">Cancel</button>
            </div>
            <div id="dc-add-status" class="text-muted" style="margin-top:8px;font-size:0.85em"></div>
        </div>
    `;

    form.querySelector('#dc-add-cancel')?.addEventListener('click', () => {
        form.style.display = 'none';
        form.innerHTML = '';
    });

    form.querySelector('#dc-add-save')?.addEventListener('click', async () => {
        const name = form.querySelector('#dc-add-name')?.value?.trim();
        const token = form.querySelector('#dc-add-token')?.value?.trim();
        const status = form.querySelector('#dc-add-status');

        if (!name || !token) {
            if (status) { status.textContent = 'Name and token required'; status.style.color = 'var(--error)'; }
            return;
        }

        const btn = form.querySelector('#dc-add-save');
        btn.disabled = true;
        btn.textContent = 'Adding...';

        try {
            const res = await fetch('/api/plugin/discord/accounts', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': CSRF() },
                body: JSON.stringify({ account_name: name, token })
            });
            const data = await res.json();
            if (data.error) throw new Error(data.error);

            if (status) { status.textContent = `Added ${name}. Bot connecting...`; status.style.color = 'var(--success)'; }
            setTimeout(() => {
                form.style.display = 'none';
                form.innerHTML = '';
                _loadAccounts(container);
            }, 1500);
        } catch (e) {
            if (status) { status.textContent = e.message; status.style.color = 'var(--error)'; }
            btn.disabled = false;
            btn.textContent = 'Add Bot';
        }
    });
}


function _esc(str) {
    if (!str) return '';
    const d = document.createElement('div');
    d.textContent = str;
    return d.innerHTML;
}

export default { init() {} };
