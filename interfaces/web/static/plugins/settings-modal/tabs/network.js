// tabs/network.js - Network settings (SOCKS proxy + Privacy whitelist)

import { fetchWithTimeout } from '../../../shared/fetch.js';
import { showToast } from '../../../shared/toast.js';

export default {
  id: 'network',
  name: 'Network',
  icon: '🌐',
  description: 'SOCKS proxy and privacy network settings',
  keys: [
    'SOCKS_ENABLED',
    'SOCKS_HOST',
    'SOCKS_PORT',
    'SOCKS_TIMEOUT'
  ],

  render(modal) {
    const whitelist = modal.settings.PRIVACY_NETWORK_WHITELIST || [];

    return `
      <div class="settings-list">
        ${modal.renderCategorySettings(this.keys)}

        <div class="socks-credentials-section">
          <div class="socks-creds-header">
            <h4>Proxy Credentials</h4>
            <div class="credential-status" id="socks-credential-status">
              <span class="status-indicator">⏳</span>
              <span class="status-text">Checking...</span>
            </div>
          </div>

          <div class="socks-creds-grid">
            <div class="field-row">
              <label>Username</label>
              <input type="text" id="socks-username" class="socks-credential"
                     placeholder="Enter username" autocomplete="off">
            </div>
            <div class="field-row">
              <label>Password</label>
              <input type="password" id="socks-password" class="socks-credential"
                     placeholder="Enter password" autocomplete="off">
            </div>
          </div>

          <div class="socks-actions-row">
            <div class="credential-actions">
              <button class="btn btn-sm btn-primary" id="socks-save-creds">💾 Save</button>
              <button class="btn btn-sm" id="socks-test-btn">🔌 Test</button>
              <button class="btn btn-sm btn-danger" id="socks-clear-creds">🗑️</button>
            </div>
            <div class="test-result-row" id="socks-test-result" style="display: none;">
              <span class="test-result"></span>
            </div>
          </div>

          <small class="field-hint">
            Stored in ~/.config/sapphire/credentials.json or via SAPPHIRE_SOCKS_USERNAME/PASSWORD env vars.
          </small>
        </div>

        <div class="privacy-whitelist-section">
          <div class="whitelist-header">
            <div class="whitelist-title-row">
              <h4>Privacy Network Whitelist</h4>
              <span class="help-icon" data-key="PRIVACY_NETWORK_WHITELIST" title="Click for details">?</span>
            </div>
            <p class="section-desc">Allow connections to these addresses when Privacy Mode is enabled. Supports IPs, hostnames, and CIDR ranges.</p>
          </div>

          <div class="whitelist-entries" id="whitelist-entries">
            ${this.renderWhitelistEntries(whitelist)}
          </div>

          <div class="whitelist-add-row">
            <input type="text" id="whitelist-input"
                   placeholder="IP, hostname, or CIDR (e.g., 192.168.1.0/24)"
                   autocomplete="off">
            <button class="btn btn-sm btn-primary" id="whitelist-add-btn">+ Add</button>
          </div>

          <small class="field-hint">
            Common ranges: 192.168.0.0/16 (home), 10.0.0.0/8 (private), 172.16.0.0/12 (private)
          </small>
        </div>
      </div>
    `;
  },

  renderWhitelistEntries(whitelist) {
    if (!whitelist || whitelist.length === 0) {
      return '<div class="whitelist-empty">No entries configured</div>';
    }

    return whitelist.map((entry, idx) => `
      <div class="whitelist-entry" data-index="${idx}">
        <span class="entry-value">${this.escapeHtml(entry)}</span>
        <button class="btn-icon whitelist-remove" data-entry="${this.escapeHtml(entry)}" title="Remove">×</button>
      </div>
    `).join('');
  },

  escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  },

  async attachListeners(modal, container) {
    // SOCKS credentials
    await this.refreshCredentialStatus(container);

    const saveBtn = container.querySelector('#socks-save-creds');
    saveBtn?.addEventListener('click', () => this.saveCredentials(container));

    const testBtn = container.querySelector('#socks-test-btn');
    testBtn?.addEventListener('click', () => this.testConnection(container));

    const clearBtn = container.querySelector('#socks-clear-creds');
    clearBtn?.addEventListener('click', () => this.clearCredentials(container));

    // Whitelist management
    const addBtn = container.querySelector('#whitelist-add-btn');
    const input = container.querySelector('#whitelist-input');

    addBtn?.addEventListener('click', () => this.addWhitelistEntry(modal, container));

    input?.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        this.addWhitelistEntry(modal, container);
      }
    });

    // Remove buttons
    this.attachRemoveListeners(modal, container);
  },

  attachRemoveListeners(modal, container) {
    container.querySelectorAll('.whitelist-remove').forEach(btn => {
      btn.addEventListener('click', () => {
        const entry = btn.dataset.entry;
        this.removeWhitelistEntry(modal, container, entry);
      });
    });
  },

  validateEntry(entry) {
    if (!entry || !entry.trim()) {
      return { valid: false, error: 'Entry cannot be empty' };
    }

    entry = entry.trim();

    // Hostname pattern (allows localhost, domain.com, sub.domain.com)
    const hostnamePattern = /^[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?)*$/;

    // IPv4 pattern
    const ipv4Pattern = /^(\d{1,3}\.){3}\d{1,3}$/;

    // IPv4 CIDR pattern
    const cidrPattern = /^(\d{1,3}\.){3}\d{1,3}\/\d{1,2}$/;

    // IPv6 pattern (simplified)
    const ipv6Pattern = /^([0-9a-fA-F]{0,4}:){2,7}[0-9a-fA-F]{0,4}$/;

    if (hostnamePattern.test(entry)) {
      return { valid: true };
    }

    if (ipv4Pattern.test(entry)) {
      // Validate octets
      const octets = entry.split('.').map(Number);
      if (octets.every(o => o >= 0 && o <= 255)) {
        return { valid: true };
      }
      return { valid: false, error: 'Invalid IP address (octets must be 0-255)' };
    }

    if (cidrPattern.test(entry)) {
      const [ip, prefix] = entry.split('/');
      const octets = ip.split('.').map(Number);
      const prefixNum = parseInt(prefix, 10);

      if (!octets.every(o => o >= 0 && o <= 255)) {
        return { valid: false, error: 'Invalid IP in CIDR (octets must be 0-255)' };
      }
      if (prefixNum < 0 || prefixNum > 32) {
        return { valid: false, error: 'Invalid CIDR prefix (must be 0-32)' };
      }
      return { valid: true };
    }

    if (ipv6Pattern.test(entry)) {
      return { valid: true };
    }

    return { valid: false, error: 'Invalid format. Use IP, hostname, or CIDR notation' };
  },

  addWhitelistEntry(modal, container) {
    const input = container.querySelector('#whitelist-input');
    const entry = input.value.trim();

    const validation = this.validateEntry(entry);
    if (!validation.valid) {
      showToast(validation.error, 'error');
      input.focus();
      return;
    }

    const whitelist = modal.settings.PRIVACY_NETWORK_WHITELIST || [];

    // Check for duplicates
    if (whitelist.includes(entry)) {
      showToast('Entry already exists', 'warning');
      input.value = '';
      return;
    }

    // Add to list
    const newWhitelist = [...whitelist, entry];
    modal.settings.PRIVACY_NETWORK_WHITELIST = newWhitelist;
    modal.pendingChanges.PRIVACY_NETWORK_WHITELIST = newWhitelist;

    // Update UI
    const entriesEl = container.querySelector('#whitelist-entries');
    entriesEl.innerHTML = this.renderWhitelistEntries(newWhitelist);
    this.attachRemoveListeners(modal, container);

    input.value = '';
    showToast(`Added: ${entry}`, 'success');
  },

  removeWhitelistEntry(modal, container, entry) {
    const whitelist = modal.settings.PRIVACY_NETWORK_WHITELIST || [];
    const newWhitelist = whitelist.filter(e => e !== entry);

    modal.settings.PRIVACY_NETWORK_WHITELIST = newWhitelist;
    modal.pendingChanges.PRIVACY_NETWORK_WHITELIST = newWhitelist;

    // Update UI
    const entriesEl = container.querySelector('#whitelist-entries');
    entriesEl.innerHTML = this.renderWhitelistEntries(newWhitelist);
    this.attachRemoveListeners(modal, container);

    showToast(`Removed: ${entry}`, 'success');
  },

  // SOCKS credential methods
  async saveCredentials(container) {
    const saveBtn = container.querySelector('#socks-save-creds');
    const username = container.querySelector('#socks-username').value;
    const password = container.querySelector('#socks-password').value;

    if (!username || !password) {
      showToast('Both username and password required', 'error');
      return;
    }

    saveBtn.disabled = true;
    saveBtn.textContent = '⏳...';

    try {
      await fetchWithTimeout('/api/credentials/socks', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password })
      });

      showToast('SOCKS credentials saved', 'success');
      container.querySelector('#socks-username').value = '';
      container.querySelector('#socks-password').value = '';
      await this.refreshCredentialStatus(container);
    } catch (e) {
      showToast(e.message || 'Failed to save', 'error');
    } finally {
      saveBtn.disabled = false;
      saveBtn.textContent = '💾 Save';
    }
  },

  async testConnection(container) {
    const testBtn = container.querySelector('#socks-test-btn');
    const resultRow = container.querySelector('#socks-test-result');
    const resultSpan = resultRow?.querySelector('.test-result');

    testBtn.disabled = true;
    testBtn.textContent = '⏳...';
    resultRow.style.display = 'block';
    resultSpan.textContent = 'Connecting...';
    resultSpan.className = 'test-result';

    try {
      const data = await fetchWithTimeout('/api/credentials/socks/test', {
        method: 'POST'
      }, 15000);

      if (data.status === 'success') {
        resultSpan.textContent = `✓ ${data.message}`;
        resultSpan.classList.add('success');
      } else {
        resultSpan.textContent = `✗ ${data.error}`;
        resultSpan.classList.add('error');
      }
    } catch (e) {
      resultSpan.textContent = `✗ ${e.message}`;
      resultSpan.classList.add('error');
    } finally {
      testBtn.disabled = false;
      testBtn.textContent = '🔌 Test';
    }
  },

  async clearCredentials(container) {
    if (!confirm('Clear SOCKS credentials?')) return;

    const clearBtn = container.querySelector('#socks-clear-creds');
    clearBtn.disabled = true;

    try {
      await fetchWithTimeout('/api/credentials/socks', { method: 'DELETE' });
      showToast('SOCKS credentials cleared', 'success');
      await this.refreshCredentialStatus(container);

      const resultRow = container.querySelector('#socks-test-result');
      if (resultRow) resultRow.style.display = 'none';
    } catch (e) {
      showToast(e.message || 'Failed to clear', 'error');
    } finally {
      clearBtn.disabled = false;
    }
  },

  async refreshCredentialStatus(container) {
    const statusEl = container.querySelector('#socks-credential-status');
    const usernameInput = container.querySelector('#socks-username');
    const passwordInput = container.querySelector('#socks-password');

    if (!statusEl) return;

    try {
      const data = await fetchWithTimeout('/api/credentials/socks');

      if (data.has_credentials) {
        statusEl.innerHTML = `
          <span class="status-indicator status-set">✓</span>
          <span class="status-text">Credentials configured</span>
        `;
        if (usernameInput) usernameInput.placeholder = '••••••••';
        if (passwordInput) passwordInput.placeholder = '••••••••';
      } else {
        statusEl.innerHTML = `
          <span class="status-indicator status-unset">○</span>
          <span class="status-text">No credentials set</span>
        `;
        if (usernameInput) usernameInput.placeholder = 'Enter username';
        if (passwordInput) passwordInput.placeholder = 'Enter password';
      }
    } catch (e) {
      statusEl.innerHTML = `
        <span class="status-indicator status-error">✗</span>
        <span class="status-text">Could not check status</span>
      `;
    }
  }
};
