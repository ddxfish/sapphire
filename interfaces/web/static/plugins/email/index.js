// index.js - Email settings plugin (multi-account)
// Settings tab in Plugins modal for email configuration

import { registerPluginSettings } from '../plugins-modal/plugin-registry.js';

function csrfHeaders(extra = {}) {
  const token = document.querySelector('meta[name="csrf-token"]')?.content || '';
  return { 'X-CSRF-Token': token, ...extra };
}

function injectStyles() {
  if (document.getElementById('email-plugin-styles')) return;

  const style = document.createElement('style');
  style.id = 'email-plugin-styles';
  style.textContent = `
    .email-form {
      display: flex;
      flex-direction: column;
      gap: 16px;
    }

    .email-account-list {
      display: flex;
      flex-direction: column;
      gap: 8px;
    }

    .email-account-item {
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 10px 14px;
      border: 1px solid var(--border);
      border-radius: 8px;
      background: var(--bg-secondary);
      cursor: pointer;
      transition: all 0.15s ease;
    }

    .email-account-item:hover {
      border-color: var(--accent-blue);
      background: var(--bg-hover);
    }

    .email-account-item.active {
      border-color: var(--accent-blue);
    }

    .email-account-scope {
      font-weight: 600;
      font-size: 13px;
      color: var(--text);
      min-width: 80px;
    }

    .email-account-addr {
      font-size: 12px;
      color: var(--text-muted);
      flex: 1;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    .email-add-btn {
      padding: 8px 16px;
      border: 1px dashed var(--border);
      border-radius: 8px;
      background: transparent;
      color: var(--text-muted);
      cursor: pointer;
      font-size: 13px;
      transition: all 0.15s ease;
    }

    .email-add-btn:hover {
      border-color: var(--accent-blue);
      color: var(--accent-blue);
    }

    .email-group {
      display: flex;
      flex-direction: column;
      gap: 6px;
    }

    .email-group label {
      font-size: 13px;
      font-weight: 500;
      color: var(--text);
    }

    .email-group input {
      padding: 8px 12px;
      border: 1px solid var(--border);
      border-radius: 6px;
      background: var(--bg-primary);
      color: var(--text);
      font-size: 13px;
      font-family: inherit;
    }

    .email-group input:focus {
      outline: none;
      border-color: var(--accent-blue);
    }

    .email-hint {
      font-size: 11px;
      color: var(--text-muted);
      margin-top: 4px;
    }

    .email-row {
      display: flex;
      gap: 8px;
      align-items: flex-start;
    }

    .email-row input {
      flex: 1;
    }

    .email-test-btn {
      padding: 8px 14px;
      border: 1px solid var(--border);
      border-radius: 6px;
      background: var(--bg-tertiary);
      color: var(--text);
      cursor: pointer;
      font-size: 13px;
      white-space: nowrap;
      transition: all 0.15s ease;
    }

    .email-test-btn:hover {
      background: var(--bg-hover);
    }

    .email-test-btn:disabled {
      opacity: 0.6;
      cursor: not-allowed;
    }

    .email-test-btn.success {
      background: var(--success-light, #d4edda);
      border-color: var(--success, #28a745);
      color: var(--success, #28a745);
    }

    .email-test-btn.error {
      background: var(--error-light, #f8d7da);
      border-color: var(--error, #dc3545);
      color: var(--error, #dc3545);
    }

    .email-pw-status {
      padding: 6px 12px;
      border-radius: 6px;
      font-size: 12px;
      white-space: nowrap;
    }

    .email-pw-status.stored {
      background: var(--success-light, #d4edda);
      color: var(--success, #28a745);
    }

    .email-pw-status.missing {
      background: var(--warning-light, #fff3cd);
      color: var(--warning, #856404);
    }

    .email-servers {
      border-top: 1px solid var(--border);
      padding-top: 16px;
      margin-top: 8px;
    }

    .email-servers-title {
      font-size: 14px;
      font-weight: 600;
      color: var(--text);
      margin-bottom: 12px;
    }

    .email-clear-btn {
      padding: 6px 12px;
      border: 1px solid var(--error, #dc3545);
      border-radius: 6px;
      background: transparent;
      color: var(--error, #dc3545);
      cursor: pointer;
      font-size: 12px;
      transition: all 0.15s ease;
    }

    .email-clear-btn:hover {
      background: var(--error-light, #f8d7da);
    }

    .email-back-btn {
      padding: 6px 12px;
      border: 1px solid var(--border);
      border-radius: 6px;
      background: transparent;
      color: var(--text);
      cursor: pointer;
      font-size: 12px;
      transition: all 0.15s ease;
    }

    .email-back-btn:hover {
      background: var(--bg-hover);
    }

    .email-editor-title {
      font-size: 15px;
      font-weight: 600;
      color: var(--text);
      margin-bottom: 4px;
    }
  `;
  document.head.appendChild(style);
}


// ─── State ───────────────────────────────────────────────────────────────────

let _container = null;
let _accounts = [];


async function loadAccounts() {
  try {
    const res = await fetch('/api/email/accounts');
    if (res.ok) {
      const data = await res.json();
      _accounts = data.accounts || [];
    }
  } catch (e) {
    console.warn('Failed to load email accounts:', e);
    _accounts = [];
  }
  return _accounts;
}


// ─── Account List View ──────────────────────────────────────────────────────

function renderAccountList(container) {
  _container = container;

  const items = _accounts.map(a => `
    <div class="email-account-item" data-scope="${a.scope}">
      <span class="email-account-scope">${a.scope}</span>
      <span class="email-account-addr">${a.address || '(no address)'}</span>
    </div>
  `).join('');

  container.innerHTML = `
    <div class="email-form">
      <div class="email-hint">
        Each scope name maps to a chat's email scope setting. Accounts are managed in Settings, scopes are selected per-chat in the sidebar.
      </div>
      <div class="email-account-list">
        ${items || '<div class="email-hint">No email accounts configured.</div>'}
      </div>
      <button type="button" class="email-add-btn" id="email-add-account">+ Add Account</button>
    </div>
  `;

  // Click handlers
  container.querySelectorAll('.email-account-item').forEach(el => {
    el.addEventListener('click', () => {
      const scope = el.dataset.scope;
      const acct = _accounts.find(a => a.scope === scope);
      renderAccountEditor(container, scope, acct);
    });
  });

  container.querySelector('#email-add-account').addEventListener('click', () => {
    const name = prompt('Scope name for new account (e.g. "sapphire", "anita"):');
    if (!name || !name.trim()) return;
    const scope = name.trim().toLowerCase().replace(/[^a-z0-9_-]/g, '_');
    if (_accounts.find(a => a.scope === scope)) {
      renderAccountEditor(container, scope, _accounts.find(a => a.scope === scope));
      return;
    }
    renderAccountEditor(container, scope, null);
  });
}


// ─── Account Editor View ────────────────────────────────────────────────────

function renderAccountEditor(container, scope, acct) {
  const s = acct || {};

  container.innerHTML = `
    <div class="email-form">
      <div class="email-row" style="align-items:center;gap:12px">
        <button type="button" class="email-back-btn" id="email-back">\u2190 Back</button>
        <div class="email-editor-title">${scope}</div>
      </div>

      <div class="email-group">
        <label for="email-address">Email Address</label>
        <input type="email" id="email-address" value="${s.address || ''}" placeholder="you@example.com">
      </div>

      <div class="email-group">
        <label for="email-password">App Password</label>
        <div class="email-row">
          <input type="password" id="email-password" placeholder="${acct ? 'Leave blank to keep existing...' : 'Enter app password'}">
          <span class="email-pw-status ${acct ? 'stored' : 'missing'}" id="email-pw-status">${acct ? '\u2713 Stored' : 'Not set'}</span>
        </div>
        <div class="email-hint">
          For Gmail, use an <a href="https://myaccount.google.com/apppasswords" target="_blank" rel="noopener" style="color:var(--accent-blue)">App Password</a> (2FA required).<br>
          For self-hosted (Dovecot etc.), use the account password. Encrypted on disk.
        </div>
      </div>

      <div class="email-servers">
        <div class="email-servers-title">Server Settings</div>
        <div class="email-group">
          <label for="email-imap">IMAP Server</label>
          <div class="email-row">
            <input type="text" id="email-imap" value="${s.imap_server || 'imap.gmail.com'}" placeholder="imap.gmail.com">
            <input type="number" id="email-imap-port" value="${s.imap_port || 993}" placeholder="993" style="max-width:80px" min="1" max="65535">
          </div>
        </div>
        <div class="email-group" style="margin-top:12px">
          <label for="email-smtp">SMTP Server</label>
          <div class="email-row">
            <input type="text" id="email-smtp" value="${s.smtp_server || 'smtp.gmail.com'}" placeholder="smtp.gmail.com">
            <input type="number" id="email-smtp-port" value="${s.smtp_port || 465}" placeholder="465" style="max-width:80px" min="1" max="65535">
          </div>
        </div>
        <div class="email-hint" style="margin-top:8px">
          Gmail defaults shown. Change for other providers (e.g. mail.yourdomain.com).
        </div>
      </div>

      <div class="email-row" style="gap:12px">
        <button type="button" class="email-test-btn" id="email-save-btn">Save</button>
        <button type="button" class="email-test-btn" id="email-test-btn">Test</button>
        ${acct ? '<button type="button" class="email-clear-btn" id="email-delete-btn">Delete</button>' : ''}
      </div>
    </div>
  `;

  container.querySelector('#email-back').addEventListener('click', () => renderAccountList(container));
  container.querySelector('#email-save-btn').addEventListener('click', () => saveAccount(container, scope));
  container.querySelector('#email-test-btn').addEventListener('click', () => testAccount(container, scope));
  container.querySelector('#email-delete-btn')?.addEventListener('click', () => deleteAccount(container, scope));
}


async function saveAccount(container, scope) {
  const address = container.querySelector('#email-address')?.value?.trim() || '';
  const app_password = container.querySelector('#email-password')?.value?.trim() || '';
  const imap_server = container.querySelector('#email-imap')?.value?.trim() || 'imap.gmail.com';
  const smtp_server = container.querySelector('#email-smtp')?.value?.trim() || 'smtp.gmail.com';
  const imap_port = parseInt(container.querySelector('#email-imap-port')?.value) || 993;
  const smtp_port = parseInt(container.querySelector('#email-smtp-port')?.value) || 465;

  if (!address) {
    alert('Email address is required');
    return;
  }

  const payload = { address, imap_server, smtp_server, imap_port, smtp_port };
  if (app_password) payload.app_password = app_password;

  const btn = container.querySelector('#email-save-btn');
  btn.disabled = true;
  btn.textContent = 'Saving...';

  try {
    const res = await fetch(`/api/email/accounts/${encodeURIComponent(scope)}`, {
      method: 'PUT',
      headers: csrfHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    if (data.success) {
      btn.textContent = '\u2713 Saved';
      btn.className = 'email-test-btn success';
      // Update password status
      const status = container.querySelector('#email-pw-status');
      if (status) { status.textContent = '\u2713 Stored'; status.className = 'email-pw-status stored'; }
      // Refresh list
      await loadAccounts();
      setTimeout(() => { btn.textContent = 'Save'; btn.className = 'email-test-btn'; btn.disabled = false; }, 2000);
    } else {
      throw new Error(data.detail || 'Save failed');
    }
  } catch (e) {
    btn.textContent = '\u2717 Error';
    btn.className = 'email-test-btn error';
    btn.title = e.message;
    setTimeout(() => { btn.textContent = 'Save'; btn.className = 'email-test-btn'; btn.disabled = false; btn.title = ''; }, 3000);
  }
}


async function testAccount(container, scope) {
  const btn = container.querySelector('#email-test-btn');
  const address = container.querySelector('#email-address')?.value?.trim() || '';
  const app_password = container.querySelector('#email-password')?.value?.trim() || '';
  const imap_server = container.querySelector('#email-imap')?.value?.trim() || '';
  const imap_port = parseInt(container.querySelector('#email-imap-port')?.value) || 993;

  if (!address && !app_password) {
    btn.textContent = 'No credentials';
    btn.className = 'email-test-btn error';
    setTimeout(() => { btn.textContent = 'Test'; btn.className = 'email-test-btn'; }, 3000);
    return;
  }

  btn.disabled = true;
  btn.textContent = 'Testing...';
  btn.className = 'email-test-btn';

  try {
    const payload = {};
    if (address) payload.address = address;
    if (app_password) payload.app_password = app_password;
    if (imap_server) payload.imap_server = imap_server;
    payload.imap_port = imap_port;

    const res = await fetch(`/api/email/accounts/${encodeURIComponent(scope)}/test`, {
      method: 'POST',
      headers: csrfHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify(payload)
    });
    const data = await res.json();

    if (data.success) {
      btn.textContent = `\u2713 Connected (${data.message_count} msgs)`;
      btn.className = 'email-test-btn success';
    } else {
      btn.textContent = '\u2717 Failed';
      btn.className = 'email-test-btn error';
      btn.title = data.error || 'Connection failed';
    }
  } catch (e) {
    btn.textContent = '\u2717 Error';
    btn.className = 'email-test-btn error';
    btn.title = e.message;
  }

  btn.disabled = false;
  setTimeout(() => { btn.textContent = 'Test'; btn.className = 'email-test-btn'; btn.title = ''; }, 5000);
}


async function deleteAccount(container, scope) {
  if (!confirm(`Delete email account "${scope}"? This cannot be undone.`)) return;

  try {
    const res = await fetch(`/api/email/accounts/${encodeURIComponent(scope)}`, {
      method: 'DELETE',
      headers: csrfHeaders()
    });
    if (res.ok) {
      await loadAccounts();
      renderAccountList(container);
    } else {
      const data = await res.json();
      alert(data.detail || 'Delete failed');
    }
  } catch (e) {
    console.error('Failed to delete email account:', e);
    alert('Delete failed: ' + e.message);
  }
}


// ─── Plugin Registration ────────────────────────────────────────────────────

function renderForm(container, settings) {
  renderAccountList(container);
}


async function saveSettings(settings) {
  // Individual account saves happen in the editor — nothing to do here
  return { success: true };
}


function getFormSettings(container) {
  // No form-level settings to collect — accounts are saved individually
  return {};
}


export default {
  name: 'email',

  init(container) {
    injectStyles();

    registerPluginSettings({
      id: 'email',
      name: 'Email',
      icon: '\uD83D\uDCE7',
      helpText: 'Configure email accounts for each persona/scope. Each chat can select which email account to use via the sidebar. Supports Gmail (app passwords), Dovecot, and any IMAP/SMTP server.',
      render: renderForm,
      load: async () => {
        await loadAccounts();
        return {};
      },
      save: saveSettings,
      getSettings: getFormSettings
    });

    console.log('\u2714 Email settings registered');
  },

  destroy() {
    _container = null;
    _accounts = [];
  }
};
