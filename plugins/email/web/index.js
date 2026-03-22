// Email settings plugin (multi-account)
// Uses shared account-manager for list/navigation, custom editor for IMAP/SMTP config.

import { registerPluginSettings } from '/static/shared/plugin-registry.js';
import { createAccountManager } from '/static/shared/account-manager.js';

const manager = createAccountManager({
    prefix: 'email',
    entityName: 'Account',
    listEndpoint: '/api/email/accounts',
    listKey: 'accounts',
    deleteEndpoint: (scope) => `/api/email/accounts/${encodeURIComponent(scope)}`,
    formatItem: (item) => ({
        name: item.scope,
        detail: item.auth_type === 'oauth2'
            ? `${item.address || '(no address)'} \u2022 O365`
            : item.address || '(no address)'
    }),
    hint: 'Each scope maps to a chat. Select which email to use per-chat in the sidebar.',
    addLabel: '+ Add Account',
    addPrompt: 'Scope name for new account (e.g. "sapphire", "anita"):',
    renderEditor: renderEmailEditor,
});


function renderEmailEditor(body, scope, item, helpers) {
    const s = item || {};

    // OAuth accounts are managed by the O365 plugin
    if (s.auth_type === 'oauth2') {
        body.innerHTML = `
            <div style="padding:16px;border:1px solid var(--border);border-radius:8px;background:var(--bg-secondary)">
                <div style="font-size:14px;font-weight:600;color:var(--text);margin-bottom:8px">Microsoft 365 Account</div>
                <div style="font-size:13px;color:var(--text-muted)">
                    <strong>${s.address || scope}</strong> is connected via OAuth.<br>
                    Manage this account in <strong>Settings → O365 Email</strong>.
                </div>
            </div>
        `;
        return;
    }

    body.innerHTML = `
        <div class="am-group">
            <label for="email-address">Email Address</label>
            <input type="email" id="email-address" value="${s.address || ''}" placeholder="you@example.com">
        </div>

        <div class="am-group">
            <label for="email-password">Password</label>
            <div class="am-row">
                <input type="password" id="email-password" placeholder="${item ? 'Leave blank to keep existing...' : 'Enter password'}">
                <span class="am-action-btn${item ? ' success' : ''}" style="cursor:default;padding:6px 12px;font-size:12px" id="email-pw-status">
                    ${item ? '\u2713 Stored' : 'Not set'}
                </span>
            </div>
            <div class="am-hint">
                Your IMAP/SMTP account password. Encrypted on disk.<br>
                Gmail users: requires an <a href="https://myaccount.google.com/apppasswords" target="_blank" rel="noopener" style="color:var(--accent-blue)">App Password</a> (2FA must be enabled).
            </div>
        </div>

        <div style="border-top:1px solid var(--border);padding-top:16px;margin-top:8px">
            <div style="font-size:14px;font-weight:600;color:var(--text);margin-bottom:12px">Server Settings</div>
            <div class="am-group">
                <label for="email-imap">IMAP Server</label>
                <div class="am-row">
                    <input type="text" id="email-imap" value="${s.imap_server || ''}" placeholder="imap.example.com">
                    <input type="number" id="email-imap-port" value="${s.imap_port || 993}" placeholder="993" style="max-width:80px" min="1" max="65535">
                </div>
            </div>
            <div class="am-group" style="margin-top:12px">
                <label for="email-smtp">SMTP Server</label>
                <div class="am-row">
                    <input type="text" id="email-smtp" value="${s.smtp_server || ''}" placeholder="smtp.example.com">
                    <input type="number" id="email-smtp-port" value="${s.smtp_port || 465}" placeholder="465" style="max-width:80px" min="1" max="65535">
                </div>
            </div>
            <div class="am-hint" style="margin-top:8px">
                Enter your mail server addresses (e.g. mail.yourdomain.com, imap.gmail.com).
            </div>
        </div>

        <div class="am-row" style="gap:12px;margin-top:8px">
            <button type="button" class="am-action-btn" id="email-save-btn">Save</button>
            <button type="button" class="am-action-btn" id="email-test-btn">Test</button>
        </div>
    `;

    // Save
    body.querySelector('#email-save-btn').addEventListener('click', async () => {
        const btn = body.querySelector('#email-save-btn');
        const address = body.querySelector('#email-address').value.trim();
        if (!address) { helpers.showResult(false, 'Email address is required'); return; }

        btn.disabled = true;
        btn.textContent = 'Saving...';

        const payload = {
            address,
            imap_server: body.querySelector('#email-imap').value.trim(),
            smtp_server: body.querySelector('#email-smtp').value.trim(),
            imap_port: parseInt(body.querySelector('#email-imap-port').value) || 993,
            smtp_port: parseInt(body.querySelector('#email-smtp-port').value) || 465,
        };
        const pw = body.querySelector('#email-password').value.trim();
        if (pw) payload.app_password = pw;

        try {
            const res = await fetch(`/api/email/accounts/${encodeURIComponent(scope)}`, {
                method: 'PUT',
                headers: helpers.csrfHeaders({ 'Content-Type': 'application/json' }),
                body: JSON.stringify(payload)
            });
            const data = await res.json();
            if (data.success) {
                btn.textContent = 'Saved';
                btn.className = 'am-action-btn success';
                const status = body.querySelector('#email-pw-status');
                if (status) { status.textContent = '\u2713 Stored'; status.className = 'am-action-btn success'; }
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

    // Test
    body.querySelector('#email-test-btn').addEventListener('click', async () => {
        const btn = body.querySelector('#email-test-btn');
        const address = body.querySelector('#email-address').value.trim();
        const app_password = body.querySelector('#email-password').value.trim();

        if (!address && !app_password) {
            btn.textContent = 'No credentials';
            btn.className = 'am-action-btn error';
            setTimeout(() => { btn.textContent = 'Test'; btn.className = 'am-action-btn'; }, 3000);
            return;
        }

        btn.disabled = true;
        btn.textContent = 'Testing...';
        btn.className = 'am-action-btn';

        try {
            const payload = {};
            if (address) payload.address = address;
            if (app_password) payload.app_password = app_password;
            payload.imap_server = body.querySelector('#email-imap').value.trim();
            payload.imap_port = parseInt(body.querySelector('#email-imap-port').value) || 993;

            const res = await fetch(`/api/email/accounts/${encodeURIComponent(scope)}/test`, {
                method: 'POST',
                headers: helpers.csrfHeaders({ 'Content-Type': 'application/json' }),
                body: JSON.stringify(payload)
            });
            const data = await res.json();

            if (data.success) {
                btn.textContent = `\u2713 Connected (${data.message_count} msgs)`;
                btn.className = 'am-action-btn success';
                helpers.showResult(true, `Connected to ${data.server || 'server'} \u2014 ${data.message_count} messages in inbox`);
            } else {
                btn.textContent = '\u2717 Failed';
                btn.className = 'am-action-btn error';
                helpers.showResult(false, data.error || 'Connection failed', data.detail);
            }
        } catch (e) {
            btn.textContent = '\u2717 Error';
            btn.className = 'am-action-btn error';
            helpers.showResult(false, `Request failed: ${e.message}`);
        }

        btn.disabled = false;
        setTimeout(() => { btn.textContent = 'Test'; btn.className = 'am-action-btn'; }, 5000);
    });
}


function renderWithDaemonSettings(container) {
    // Poll interval setting above accounts
    const wrapper = document.createElement('div');
    wrapper.innerHTML = `
        <div style="margin-bottom:20px;padding:12px 16px;border:1px solid var(--border);border-radius:8px;background:var(--bg-secondary);display:flex;flex-direction:column;gap:12px">
            <div style="display:flex;align-items:center;gap:12px">
                <label for="email-poll-interval" style="font-size:13px;font-weight:600;color:var(--text);white-space:nowrap">Poll Interval</label>
                <input type="number" id="email-poll-interval" min="30" max="3600" style="width:80px" value="120">
                <span style="font-size:12px;color:var(--text-muted)">seconds (min 30, requires restart)</span>
            </div>
            <div style="display:flex;align-items:center;gap:12px">
                <label class="pm-toggle" style="flex-shrink:0">
                    <input type="checkbox" id="email-allow-all">
                    <span class="pm-slider"></span>
                </label>
                <div>
                    <span style="font-size:13px;font-weight:600;color:var(--text)">Allow sending to any address</span>
                    <div style="font-size:11px;color:var(--text-muted)">When off, AI can only email whitelisted contacts. When on, AI can email anyone directly. Requires plugin reload.</div>
                </div>
            </div>
        </div>
    `;
    container.appendChild(wrapper);

    // Load current values
    const _csrf = () => document.querySelector('meta[name="csrf-token"]')?.content;
    const _saveSettings = async (patch) => {
        const headers = { 'Content-Type': 'application/json' };
        const tok = _csrf();
        if (tok) headers['X-CSRF-Token'] = tok;
        await fetch('/api/webui/plugins/email/settings', { method: 'PUT', headers, body: JSON.stringify(patch) });
    };

    fetch('/api/webui/plugins/email/settings').then(r => r.json()).then(data => {
        const s = data.settings || {};
        const pollInput = container.querySelector('#email-poll-interval');
        const allowToggle = container.querySelector('#email-allow-all');

        if (pollInput && s.poll_interval != null) pollInput.value = s.poll_interval;
        if (allowToggle) allowToggle.checked = !!s.allow_all_recipients;

        pollInput?.addEventListener('change', () => {
            const val = Math.max(30, parseInt(pollInput.value) || 120);
            pollInput.value = val;
            _saveSettings({ poll_interval: val });
        });

        allowToggle?.addEventListener('change', () => {
            if (allowToggle.checked) {
                if (!confirm('WARNING: This allows the AI to email anyone — not just whitelisted contacts. Are you sure?')) {
                    allowToggle.checked = false;
                    return;
                }
            }
            _saveSettings({ allow_all_recipients: allowToggle.checked });
        });
    }).catch(() => {});

    // Accounts below — render into a sub-container so renderList doesn't wipe our settings
    const accountsDiv = document.createElement('div');
    container.appendChild(accountsDiv);
    manager.renderList(accountsDiv);
}

export default {
    name: 'email',

    init(container) {
        registerPluginSettings({
            id: 'email',
            name: 'Email',
            icon: '\uD83D\uDCE7',
            helpText: 'Configure email accounts for each persona/scope. Each chat can select which email account to use via the sidebar. Works with any IMAP/SMTP server.',
            render: (c) => renderWithDaemonSettings(c),
            load: async () => { await manager.loadItems(); return {}; },
            save: async () => ({ success: true }),
            getSettings: () => ({})
        });
    },

    destroy() {}
};
