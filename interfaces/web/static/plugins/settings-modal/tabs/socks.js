// tabs/socks.js - SOCKS proxy settings with credentials

import { fetchWithTimeout } from '../../../shared/fetch.js';
import { showToast } from '../../../shared/toast.js';

export default {
  id: 'socks',
  name: 'SOCKS',
  icon: '🔒',
  description: 'SOCKS5 proxy configuration',
  keys: [
    'SOCKS_ENABLED',
    'SOCKS_HOST',
    'SOCKS_PORT'
  ],

  render(modal) {
    return `
      <div class="settings-list">
        ${modal.renderCategorySettings(this.keys)}
        
        <div class="socks-credentials-section">
          <h4>Proxy Credentials</h4>
          <p class="section-desc">Stored securely outside project directory. Never included in backups.</p>
          
          <div class="credential-status" id="socks-credential-status">
            <span class="status-indicator">⏳</span>
            <span class="status-text">Checking...</span>
          </div>
          
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
          
          <div class="credential-actions">
            <button class="btn btn-sm btn-primary" id="socks-save-creds">
              <span class="btn-icon">💾</span> Save Credentials
            </button>
            <button class="btn btn-sm" id="socks-test-btn">
              <span class="btn-icon">🔌</span> Test Connection
            </button>
            <button class="btn btn-sm btn-danger" id="socks-clear-creds">
              <span class="btn-icon">🗑️</span> Clear
            </button>
          </div>
          
          <div class="test-result-row" id="socks-test-result" style="display: none;">
            <span class="test-result"></span>
          </div>
          
          <small class="field-hint">
            Credentials stored in ~/.config/sapphire/credentials.json.
            You can also use SAPPHIRE_SOCKS_USERNAME and SAPPHIRE_SOCKS_PASSWORD env vars.
          </small>
        </div>
      </div>
    `;
  },

  async attachListeners(modal, container) {
    // Check current credential status
    await this.refreshCredentialStatus(container);

    // Save credentials button
    const saveBtn = container.querySelector('#socks-save-creds');
    saveBtn?.addEventListener('click', () => this.saveCredentials(container));

    // Test connection button
    const testBtn = container.querySelector('#socks-test-btn');
    testBtn?.addEventListener('click', () => this.testConnection(container));

    // Clear credentials button
    const clearBtn = container.querySelector('#socks-clear-creds');
    clearBtn?.addEventListener('click', () => this.clearCredentials(container));
  },

  async saveCredentials(container) {
    const saveBtn = container.querySelector('#socks-save-creds');
    const username = container.querySelector('#socks-username').value;
    const password = container.querySelector('#socks-password').value;
    
    if (!username || !password) {
      showToast('Both username and password required', 'error');
      return;
    }
    
    saveBtn.disabled = true;
    saveBtn.innerHTML = '<span class="btn-icon">⏳</span> Saving...';
    
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
      saveBtn.innerHTML = '<span class="btn-icon">💾</span> Save Credentials';
    }
  },

  async testConnection(container) {
    const testBtn = container.querySelector('#socks-test-btn');
    const resultRow = container.querySelector('#socks-test-result');
    const resultSpan = resultRow?.querySelector('.test-result');
    
    testBtn.disabled = true;
    testBtn.innerHTML = '<span class="btn-icon">⏳</span> Testing...';
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
      testBtn.innerHTML = '<span class="btn-icon">🔌</span> Test Connection';
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
      
      // Hide test result
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
        // Show stars to indicate values are set
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