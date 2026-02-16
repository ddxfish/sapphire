// index.js - Email settings plugin
// Settings tab in Plugins modal for email (Gmail) configuration

import { registerPluginSettings } from '../plugins-modal/plugin-registry.js';

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
  `;
  document.head.appendChild(style);
}


async function testConnection(container) {
  const getEl = (id) => container.querySelector(`#${id}`) || document.getElementById(id);

  const btn = getEl('email-test-btn');
  const addrInput = getEl('email-address');
  const pwInput = getEl('email-password');
  const imapInput = getEl('email-imap');

  const address = addrInput?.value?.trim() || '';
  const app_password = pwInput?.value?.trim() || '';
  const imap_server = imapInput?.value?.trim() || '';

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

    const res = await fetch('/api/webui/plugins/email/test', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
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
  setTimeout(() => {
    btn.textContent = 'Test';
    btn.className = 'email-test-btn';
    btn.title = '';
  }, 5000);
}


async function clearCredentials(container) {
  try {
    const res = await fetch('/api/webui/plugins/email/credentials', { method: 'DELETE' });
    if (res.ok) {
      const getEl = (id) => container.querySelector(`#${id}`) || document.getElementById(id);
      const addrInput = getEl('email-address');
      const pwInput = getEl('email-password');
      const status = getEl('email-pw-status');
      if (addrInput) addrInput.value = '';
      if (pwInput) pwInput.value = '';
      if (status) {
        status.textContent = 'Not set';
        status.className = 'email-pw-status missing';
      }
    }
  } catch (e) {
    console.error('Failed to clear email credentials:', e);
  }
}


function renderForm(container, settings) {
  const s = settings || {};

  container.innerHTML = `
    <div class="email-form">
      <div class="email-group">
        <label for="email-address">Email Address</label>
        <input type="email" id="email-address" value="${s.address || ''}" placeholder="you@gmail.com">
      </div>

      <div class="email-group">
        <label for="email-password">App Password</label>
        <div class="email-row">
          <input type="password" id="email-password" placeholder="Enter app password to update...">
          <span class="email-pw-status missing" id="email-pw-status">Checking...</span>
        </div>
        <div class="email-hint">
          Google requires an <strong>App Password</strong> (not your regular password).<br>
          <a href="https://myaccount.google.com/apppasswords" target="_blank" rel="noopener" style="color:var(--accent-blue)">
            Create one here
          </a> — requires 2FA enabled on your Google account.<br>
          Leave blank to keep existing password. Password is encrypted on disk.
        </div>
      </div>

      <div class="email-row" style="gap:12px">
        <button type="button" class="email-test-btn" id="email-test-btn">Test</button>
        <button type="button" class="email-clear-btn" id="email-clear-btn">Clear Credentials</button>
      </div>

      <div class="email-servers">
        <div class="email-servers-title">Server Settings</div>
        <div class="email-group">
          <label for="email-imap">IMAP Server</label>
          <input type="text" id="email-imap" value="${s.imap_server || 'imap.gmail.com'}" placeholder="imap.gmail.com">
        </div>
        <div class="email-group" style="margin-top:12px">
          <label for="email-smtp">SMTP Server</label>
          <input type="text" id="email-smtp" value="${s.smtp_server || 'smtp.gmail.com'}" placeholder="smtp.gmail.com">
        </div>
        <div class="email-hint" style="margin-top:8px">
          Defaults work for Gmail. Change only if using a different provider.
        </div>
      </div>
    </div>
  `;

  container.querySelector('#email-test-btn').addEventListener('click', () => testConnection(container));
  container.querySelector('#email-clear-btn').addEventListener('click', () => clearCredentials(container));

  // Check credential status
  checkStatus(container);
}


async function checkStatus(container) {
  const status = container.querySelector('#email-pw-status') || document.getElementById('email-pw-status');
  if (!status) return;

  try {
    const res = await fetch('/api/webui/plugins/email/credentials');
    const data = await res.json();

    if (data.has_credentials) {
      status.textContent = '\u2713 Stored';
      status.className = 'email-pw-status stored';
    } else {
      status.textContent = 'Not set';
      status.className = 'email-pw-status missing';
    }
  } catch (e) {
    status.textContent = '?';
    status.className = 'email-pw-status missing';
  }
}


async function saveSettings(settings) {
  // Extract credential fields — they go to credentials manager, not settings file
  const address = settings._address || '';
  const app_password = settings._app_password || '';
  const imap_server = settings._imap_server || 'imap.gmail.com';
  const smtp_server = settings._smtp_server || 'smtp.gmail.com';

  if (address) {
    try {
      const payload = { address, imap_server, smtp_server };
      if (app_password) payload.app_password = app_password;

      const res = await fetch('/api/webui/plugins/email/credentials', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      const data = await res.json();
      console.log('Email: Credentials save result:', data);
    } catch (e) {
      console.error('Failed to save email credentials:', e);
    }
  }

  // Nothing to save to settings file — all email config is in credentials
  return { success: true };
}


function getFormSettings(container) {
  const getVal = (id) => {
    const el = container.querySelector(`#${id}`) || document.getElementById(id);
    return el?.value || '';
  };

  return {
    _address: getVal('email-address').trim(),
    _app_password: getVal('email-password').trim(),
    _imap_server: getVal('email-imap').trim() || 'imap.gmail.com',
    _smtp_server: getVal('email-smtp').trim() || 'smtp.gmail.com',
  };
}


export default {
  name: 'email',

  init(container) {
    injectStyles();

    registerPluginSettings({
      id: 'email',
      name: 'Email',
      icon: '\uD83D\uDCE7',
      helpText: 'Connect a Gmail account so the AI can read and send email. Requires a Google App Password (2FA must be enabled). Only whitelisted contacts in Mind \u2192 People can receive email.',
      render: renderForm,
      load: async () => {
        try {
          const res = await fetch('/api/webui/plugins/email/credentials');
          if (res.ok) return await res.json();
        } catch (e) {
          console.warn('Failed to load email settings:', e);
        }
        return {};
      },
      save: saveSettings,
      getSettings: getFormSettings
    });

    console.log('\u2714 Email settings registered');
  },

  destroy() {
    // Nothing to clean up
  }
};
