// plugins/telegram/web/index.js — Settings tab for Telegram plugin
// Renders API credentials (from manifest schema) + account management UI

import { registerPluginSettings } from '/static/shared/plugin-registry.js';
import { renderSettingsForm, readSettingsForm } from '/static/shared/plugin-settings-renderer.js';
import pluginsAPI from '/static/shared/plugins-api.js';

const PLUGIN_NAME = 'telegram';
const CSRF = () => document.querySelector('meta[name="csrf-token"]')?.content || '';

// Schema for the standard settings (mirrors manifest)
const SETTINGS_SCHEMA = [
    { key: 'api_id', type: 'string', label: 'API ID', default: '', help: 'From https://my.telegram.org — Apps section. One pair covers all accounts.' },
    { key: 'api_hash', type: 'string', label: 'API Hash', default: '', help: 'From https://my.telegram.org — Apps section.' },
];

registerPluginSettings({
    id: PLUGIN_NAME,
    name: 'Telegram',
    icon: '\u2708\ufe0f',
    helpText: 'Telegram Client API — accounts and daemon settings',

    render(container, settings) {
        container.innerHTML = `
            <div id="tg-settings-section"></div>
            <hr style="border-color:var(--border);margin:16px 0">
            <h4 style="margin:0 0 12px">Accounts</h4>
            <div id="tg-accounts-list"></div>
            <div id="tg-auth-wizard" style="display:none"></div>
            <button class="btn btn-sm" id="tg-add-account" style="margin-top:12px">+ Add Account</button>
        `;

        // Render standard settings (saved via header "Save Changes" button)
        renderSettingsForm(container.querySelector('#tg-settings-section'), SETTINGS_SCHEMA, settings);

        // Load accounts
        _loadAccounts(container);

        // Add account button
        container.querySelector('#tg-add-account')?.addEventListener('click', () => {
            _showAuthWizard(container);
        });
    },

    load: () => pluginsAPI.getSettings(PLUGIN_NAME),
    save: (s) => pluginsAPI.saveSettings(PLUGIN_NAME, s),
    getSettings: (box) => readSettingsForm(box, SETTINGS_SCHEMA),
});

async function _loadAccounts(container) {
    const list = container.querySelector('#tg-accounts-list');
    if (!list) return;

    try {
        const res = await fetch('/api/plugin/telegram/accounts');
        if (!res.ok) throw new Error('Failed to fetch accounts');
        const data = await res.json();
        const accounts = data.accounts || [];

        if (accounts.length === 0) {
            list.innerHTML = '<p class="text-muted" style="font-size:0.9em">No accounts configured. Add one to get started.</p>';
            return;
        }

        list.innerHTML = accounts.map(a => `
            <div class="setting-row" style="padding:10px 0;border-bottom:1px solid var(--border)" data-account="${_esc(a.name)}">
                <div class="setting-label">
                    <label>${_esc(a.label || a.name)}${a.username ? ` <span class="text-muted">@${_esc(a.username)}</span>` : ''}</label>
                    <div class="setting-help">${a.phone || ''} ${a.connected ? '<span style="color:var(--success)">Connected</span>' : '<span class="text-muted">Disconnected</span>'}</div>
                </div>
                <div class="setting-input">
                    <button class="btn btn-sm btn-danger tg-delete-account" data-name="${_esc(a.name)}">Remove</button>
                </div>
            </div>
        `).join('');

        // Wire delete buttons
        list.querySelectorAll('.tg-delete-account').forEach(btn => {
            btn.addEventListener('click', async () => {
                const name = btn.dataset.name;
                if (!confirm(`Remove account "${name}"? The session will be deleted.`)) return;
                btn.disabled = true;
                btn.textContent = 'Removing...';
                try {
                    await fetch(`/api/plugin/telegram/accounts/${name}`, {
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

function _showAuthWizard(container) {
    const wizard = container.querySelector('#tg-auth-wizard');
    if (!wizard) return;
    wizard.style.display = 'block';

    wizard.innerHTML = `
        <div style="padding:14px;background:var(--bg-secondary);border-radius:var(--radius-sm);border:1px solid var(--border);margin-top:12px">
            <h5 style="margin:0 0 10px">Add Telegram Account</h5>
            <div class="setting-row" style="padding:4px 0">
                <div class="setting-label"><label>Account Name</label><div class="setting-help">A short label like "personal" or "work"</div></div>
                <div class="setting-input"><input type="text" id="tg-auth-name" placeholder="personal" style="width:100%"></div>
            </div>
            <div class="setting-row" style="padding:4px 0">
                <div class="setting-label"><label>Phone Number</label><div class="setting-help">International format with country code</div></div>
                <div class="setting-input"><input type="text" id="tg-auth-phone" placeholder="+1234567890" style="width:100%"></div>
            </div>
            <div style="display:flex;gap:8px;margin-top:10px">
                <button class="btn btn-primary btn-sm" id="tg-auth-send">Send Code</button>
                <button class="btn btn-sm" id="tg-auth-cancel">Cancel</button>
            </div>
            <div id="tg-auth-step2" style="display:none;margin-top:12px">
                <div class="setting-row" style="padding:4px 0">
                    <div class="setting-label"><label>Verification Code</label><div class="setting-help">Check your Telegram app for the code</div></div>
                    <div class="setting-input"><input type="text" id="tg-auth-code" placeholder="12345" style="width:100%"></div>
                </div>
                <button class="btn btn-primary btn-sm" id="tg-auth-verify">Verify</button>
            </div>
            <div id="tg-auth-step-2fa" style="display:none;margin-top:12px">
                <div class="setting-row" style="padding:4px 0">
                    <div class="setting-label"><label>2FA Password</label><div class="setting-help">Your Telegram two-step verification password</div></div>
                    <div class="setting-input"><input type="password" id="tg-auth-2fa" style="width:100%"></div>
                </div>
                <button class="btn btn-primary btn-sm" id="tg-auth-submit-2fa">Submit</button>
            </div>
            <div id="tg-auth-status" class="text-muted" style="margin-top:8px;font-size:0.85em"></div>
        </div>
    `;

    let accountName = '';

    // Cancel
    wizard.querySelector('#tg-auth-cancel')?.addEventListener('click', () => {
        wizard.style.display = 'none';
        wizard.innerHTML = '';
    });

    // Send code
    wizard.querySelector('#tg-auth-send')?.addEventListener('click', async () => {
        const nameInput = wizard.querySelector('#tg-auth-name');
        const phoneInput = wizard.querySelector('#tg-auth-phone');
        accountName = nameInput?.value?.trim();
        const phone = phoneInput?.value?.trim();

        if (!accountName || !phone) {
            _setStatus(wizard, 'Account name and phone number required', true);
            return;
        }

        const btn = wizard.querySelector('#tg-auth-send');
        btn.disabled = true;
        btn.textContent = 'Sending...';
        _setStatus(wizard, 'Sending verification code...');

        try {
            const res = await fetch('/api/plugin/telegram/accounts', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': CSRF() },
                body: JSON.stringify({ phone, account_name: accountName })
            });
            const data = await res.json();
            if (data.error) throw new Error(data.error);

            _setStatus(wizard, 'Code sent! Check your Telegram app.');
            wizard.querySelector('#tg-auth-step2').style.display = 'block';
            wizard.querySelector('#tg-auth-code')?.focus();
        } catch (e) {
            _setStatus(wizard, e.message, true);
            btn.disabled = false;
            btn.textContent = 'Send Code';
        }
    });

    // Verify code
    wizard.querySelector('#tg-auth-verify')?.addEventListener('click', async () => {
        const code = wizard.querySelector('#tg-auth-code')?.value?.trim();
        if (!code) { _setStatus(wizard, 'Enter the code', true); return; }

        const btn = wizard.querySelector('#tg-auth-verify');
        btn.disabled = true;
        btn.textContent = 'Verifying...';
        _setStatus(wizard, 'Verifying...');

        try {
            const res = await fetch('/api/plugin/telegram/auth/code', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': CSRF() },
                body: JSON.stringify({ account_name: accountName, code })
            });
            const data = await res.json();
            if (data.error) throw new Error(data.error);

            if (data.status === 'needs_2fa') {
                _setStatus(wizard, '2FA required. Enter your password.');
                wizard.querySelector('#tg-auth-step-2fa').style.display = 'block';
                wizard.querySelector('#tg-auth-2fa')?.focus();
                return;
            }

            // Success
            _setStatus(wizard, `Authenticated as ${data.display_name} (@${data.username || '?'})`);
            setTimeout(() => {
                wizard.style.display = 'none';
                wizard.innerHTML = '';
                _loadAccounts(container);
            }, 1500);
        } catch (e) {
            _setStatus(wizard, e.message, true);
            btn.disabled = false;
            btn.textContent = 'Verify';
        }
    });

    // 2FA submit
    wizard.querySelector('#tg-auth-submit-2fa')?.addEventListener('click', async () => {
        const password = wizard.querySelector('#tg-auth-2fa')?.value;
        if (!password) { _setStatus(wizard, 'Enter your 2FA password', true); return; }

        const btn = wizard.querySelector('#tg-auth-submit-2fa');
        btn.disabled = true;
        btn.textContent = 'Verifying...';

        try {
            const res = await fetch('/api/plugin/telegram/auth/2fa', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': CSRF() },
                body: JSON.stringify({ account_name: accountName, password })
            });
            const data = await res.json();
            if (data.error) throw new Error(data.error);

            _setStatus(wizard, `Authenticated as ${data.display_name} (@${data.username || '?'})`);
            setTimeout(() => {
                wizard.style.display = 'none';
                wizard.innerHTML = '';
                _loadAccounts(container);
            }, 1500);
        } catch (e) {
            _setStatus(wizard, e.message, true);
            btn.disabled = false;
            btn.textContent = 'Submit';
        }
    });
}

function _setStatus(wizard, msg, isError = false) {
    const el = wizard.querySelector('#tg-auth-status');
    if (!el) return;
    el.textContent = msg;
    el.style.color = isError ? 'var(--error)' : 'var(--text-muted)';
}

function _esc(str) {
    if (!str) return '';
    const d = document.createElement('div');
    d.textContent = str;
    return d.innerHTML;
}

export default { init() {} };
