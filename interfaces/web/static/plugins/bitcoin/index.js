// index.js - Bitcoin wallet settings plugin (multi-wallet)
// Settings tab in Plugins modal for Bitcoin wallet management

import { registerPluginSettings } from '../plugins-modal/plugin-registry.js';

function csrfHeaders(extra = {}) {
  const token = document.querySelector('meta[name="csrf-token"]')?.content || '';
  return { 'X-CSRF-Token': token, ...extra };
}

function injectStyles() {
  if (document.getElementById('bitcoin-plugin-styles')) return;

  const style = document.createElement('style');
  style.id = 'bitcoin-plugin-styles';
  style.textContent = `
    .btc-form {
      display: flex;
      flex-direction: column;
      gap: 16px;
    }

    .btc-wallet-list {
      display: flex;
      flex-direction: column;
      gap: 8px;
    }

    .btc-wallet-item {
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

    .btc-wallet-item:hover {
      border-color: var(--accent-blue);
      background: var(--bg-hover);
    }

    .btc-wallet-scope {
      font-weight: 600;
      font-size: 13px;
      color: var(--text);
      min-width: 80px;
    }

    .btc-wallet-addr {
      font-size: 11px;
      color: var(--text-muted);
      flex: 1;
      overflow: hidden;
      text-overflow: ellipsis;
      font-family: monospace;
    }

    .btc-add-btn {
      padding: 8px 16px;
      border: 1px dashed var(--border);
      border-radius: 8px;
      background: transparent;
      color: var(--text-muted);
      cursor: pointer;
      font-size: 13px;
      transition: all 0.15s ease;
    }

    .btc-add-btn:hover {
      border-color: var(--accent-blue);
      color: var(--accent-blue);
    }

    .btc-group {
      display: flex;
      flex-direction: column;
      gap: 6px;
    }

    .btc-group label {
      font-size: 13px;
      font-weight: 500;
      color: var(--text);
    }

    .btc-group input {
      padding: 8px 12px;
      border: 1px solid var(--border);
      border-radius: 6px;
      background: var(--bg-primary);
      color: var(--text);
      font-size: 13px;
      font-family: inherit;
    }

    .btc-group input:focus {
      outline: none;
      border-color: var(--accent-blue);
    }

    .btc-group input.mono {
      font-family: monospace;
      font-size: 12px;
    }

    .btc-hint {
      font-size: 11px;
      color: var(--text-muted);
      margin-top: 4px;
    }

    .btc-row {
      display: flex;
      gap: 8px;
      align-items: flex-start;
    }

    .btc-btn {
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

    .btc-btn:hover { background: var(--bg-hover); }
    .btc-btn:disabled { opacity: 0.6; cursor: not-allowed; }

    .btc-btn.success {
      background: var(--success-light, #d4edda);
      border-color: var(--success, #28a745);
      color: var(--success, #28a745);
    }

    .btc-btn.error {
      background: var(--error-light, #f8d7da);
      border-color: var(--error, #dc3545);
      color: var(--error, #dc3545);
    }

    .btc-delete-btn {
      padding: 6px 12px;
      border: 1px solid var(--error, #dc3545);
      border-radius: 6px;
      background: transparent;
      color: var(--error, #dc3545);
      cursor: pointer;
      font-size: 12px;
      transition: all 0.15s ease;
    }

    .btc-delete-btn:hover { background: var(--error-light, #f8d7da); }

    .btc-back-btn {
      padding: 6px 12px;
      border: 1px solid var(--border);
      border-radius: 6px;
      background: transparent;
      color: var(--text);
      cursor: pointer;
      font-size: 12px;
      transition: all 0.15s ease;
    }

    .btc-back-btn:hover { background: var(--bg-hover); }

    .btc-editor-title {
      font-size: 15px;
      font-weight: 600;
      color: var(--text);
      margin-bottom: 4px;
    }

    .btc-address-box {
      padding: 10px 14px;
      border: 1px solid var(--border);
      border-radius: 8px;
      background: var(--bg-secondary);
      font-family: monospace;
      font-size: 12px;
      word-break: break-all;
      color: var(--text);
      user-select: all;
    }

    .btc-balance-box {
      padding: 10px 14px;
      border-radius: 8px;
      font-size: 13px;
      line-height: 1.4;
    }

    .btc-balance-box.success {
      background: var(--success-light, #d4edda);
      border: 1px solid var(--success, #28a745);
      color: var(--success, #28a745);
    }

    .btc-balance-box.error {
      background: var(--error-light, #f8d7da);
      border: 1px solid var(--error, #dc3545);
      color: var(--error, #dc3545);
    }
  `;
  document.head.appendChild(style);
}


// ─── State ───────────────────────────────────────────────────────────────────

let _container = null;
let _wallets = [];


async function loadWallets() {
  try {
    const res = await fetch('/api/bitcoin/wallets');
    if (res.ok) {
      const data = await res.json();
      _wallets = data.wallets || [];
    }
  } catch (e) {
    console.warn('Failed to load bitcoin wallets:', e);
    _wallets = [];
  }
  return _wallets;
}


// ─── Wallet List View ────────────────────────────────────────────────────────

function renderWalletList(container) {
  _container = container;

  const items = _wallets.map(w => `
    <div class="btc-wallet-item" data-scope="${w.scope}">
      <span class="btc-wallet-scope">${w.scope}</span>
      <span class="btc-wallet-addr">${w.address || '(no key)'}</span>
    </div>
  `).join('');

  container.innerHTML = `
    <div class="btc-form">
      <div class="btc-hint">
        Each scope maps to a chat's bitcoin wallet setting. Wallets are managed here, scopes selected per-chat in the sidebar.
      </div>
      <div class="btc-wallet-list">
        ${items || '<div class="btc-hint">No Bitcoin wallets configured.</div>'}
      </div>
      <button type="button" class="btc-add-btn" id="btc-add-wallet">+ Add Wallet</button>
    </div>
  `;

  container.querySelectorAll('.btc-wallet-item').forEach(el => {
    el.addEventListener('click', () => {
      const scope = el.dataset.scope;
      const w = _wallets.find(w => w.scope === scope);
      renderWalletEditor(container, scope, w);
    });
  });

  container.querySelector('#btc-add-wallet').addEventListener('click', () => {
    const name = prompt('Scope name for new wallet (e.g. "sapphire", "savings"):');
    if (!name || !name.trim()) return;
    const scope = name.trim().toLowerCase().replace(/[^a-z0-9_-]/g, '_');
    if (_wallets.find(w => w.scope === scope)) {
      renderWalletEditor(container, scope, _wallets.find(w => w.scope === scope));
      return;
    }
    renderWalletEditor(container, scope, null);
  });
}


// ─── Wallet Editor View ──────────────────────────────────────────────────────

function renderWalletEditor(container, scope, wallet) {
  const existing = !!wallet?.address;

  container.innerHTML = `
    <div class="btc-form">
      <div class="btc-row" style="align-items:center;gap:12px">
        <button type="button" class="btc-back-btn" id="btc-back">\u2190 Back</button>
        <div class="btc-editor-title">${scope}</div>
      </div>

      ${existing ? `
        <div class="btc-group">
          <label>Address (receive)</label>
          <div class="btc-address-box">${wallet.address}</div>
          <div class="btc-hint">Share this address to receive Bitcoin. Click to select for copying.</div>
        </div>
      ` : ''}

      <div class="btc-group">
        <label for="btc-label">Label</label>
        <input type="text" id="btc-label" value="${wallet?.label || scope}" placeholder="e.g. Sapphire Main">
      </div>

      <div class="btc-group">
        <label for="btc-wif">Private Key (WIF)</label>
        <input type="password" id="btc-wif" class="mono" placeholder="${existing ? 'Leave blank to keep existing...' : 'Paste WIF or generate new below'}">
        <div class="btc-hint">WIF format (starts with 5, K, or L). Stored encrypted on disk. Never shared with AI.</div>
      </div>

      <div id="btc-result"></div>

      <div class="btc-row" style="gap:12px">
        ${!existing ? '<button type="button" class="btc-btn" id="btc-generate">Generate New</button>' : ''}
        <button type="button" class="btc-btn" id="btc-save">Save</button>
        ${existing ? '<button type="button" class="btc-btn" id="btc-check">Check Balance</button>' : ''}
        ${existing ? '<button type="button" class="btc-delete-btn" id="btc-delete">Delete</button>' : ''}
      </div>
    </div>
  `;

  container.querySelector('#btc-back').addEventListener('click', () => renderWalletList(container));
  container.querySelector('#btc-save').addEventListener('click', () => saveWallet(container, scope));
  container.querySelector('#btc-generate')?.addEventListener('click', () => generateWallet(container, scope));
  container.querySelector('#btc-check')?.addEventListener('click', () => checkBalance(container, scope));
  container.querySelector('#btc-delete')?.addEventListener('click', () => deleteWallet(container, scope));
}


function showResult(container, success, message) {
  const el = container.querySelector('#btc-result');
  if (!el) return;
  el.className = `btc-balance-box ${success ? 'success' : 'error'}`;
  el.textContent = message;
}


async function generateWallet(container, scope) {
  const btn = container.querySelector('#btc-generate');
  btn.disabled = true;
  btn.textContent = 'Generating...';

  try {
    const label = container.querySelector('#btc-label')?.value?.trim() || scope;
    const res = await fetch(`/api/bitcoin/wallets/${encodeURIComponent(scope)}`, {
      method: 'PUT',
      headers: csrfHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ generate: true, label })
    });
    const data = await res.json();
    if (data.success) {
      showResult(container, true, `Wallet generated! Address: ${data.address}`);
      await loadWallets();
      // Re-render editor with the new wallet
      const w = _wallets.find(w => w.scope === scope);
      setTimeout(() => renderWalletEditor(container, scope, w), 1500);
    } else {
      throw new Error(data.detail || 'Generation failed');
    }
  } catch (e) {
    showResult(container, false, `Failed: ${e.message}`);
    btn.disabled = false;
    btn.textContent = 'Generate New';
  }
}


async function saveWallet(container, scope) {
  const wif = container.querySelector('#btc-wif')?.value?.trim() || '';
  const label = container.querySelector('#btc-label')?.value?.trim() || scope;
  const btn = container.querySelector('#btc-save');

  // If no WIF and wallet exists, just update label
  const existing = _wallets.find(w => w.scope === scope);
  if (!wif && !existing) {
    showResult(container, false, 'WIF key is required for new wallets. Paste one or click Generate New.');
    return;
  }

  btn.disabled = true;
  btn.textContent = 'Saving...';

  try {
    const payload = { label };
    if (wif) payload.wif = wif;
    else {
      // Keep existing WIF — backend needs it, send empty to signal "keep"
      // Actually the backend requires WIF, so we need a flag or to re-read
      // For label-only update, we re-PUT with generate=false and let backend handle
    }

    const res = await fetch(`/api/bitcoin/wallets/${encodeURIComponent(scope)}`, {
      method: 'PUT',
      headers: csrfHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    if (data.success) {
      btn.textContent = '\u2713 Saved';
      btn.className = 'btc-btn success';
      await loadWallets();
      setTimeout(() => { btn.textContent = 'Save'; btn.className = 'btc-btn'; btn.disabled = false; }, 2000);
    } else {
      throw new Error(data.detail || 'Save failed');
    }
  } catch (e) {
    btn.textContent = '\u2717 Error';
    btn.className = 'btc-btn error';
    showResult(container, false, e.message);
    setTimeout(() => { btn.textContent = 'Save'; btn.className = 'btc-btn'; btn.disabled = false; }, 3000);
  }
}


async function checkBalance(container, scope) {
  const btn = container.querySelector('#btc-check');
  btn.disabled = true;
  btn.textContent = 'Checking...';

  try {
    const res = await fetch(`/api/bitcoin/wallets/${encodeURIComponent(scope)}/check`, {
      method: 'POST',
      headers: csrfHeaders({ 'Content-Type': 'application/json' }),
      body: '{}'
    });
    const data = await res.json();
    if (data.success) {
      showResult(container, true, `Balance: ${data.balance_btc} BTC (${data.balance_sat} sat)`);
    } else {
      showResult(container, false, data.error || 'Check failed');
    }
  } catch (e) {
    showResult(container, false, `Error: ${e.message}`);
  }

  btn.disabled = false;
  btn.textContent = 'Check Balance';
}


async function deleteWallet(container, scope) {
  if (!confirm(`Delete Bitcoin wallet "${scope}"? The private key will be permanently removed. Make sure you have a backup!`)) return;

  try {
    const res = await fetch(`/api/bitcoin/wallets/${encodeURIComponent(scope)}`, {
      method: 'DELETE',
      headers: csrfHeaders()
    });
    if (res.ok) {
      await loadWallets();
      renderWalletList(container);
    } else {
      const data = await res.json();
      alert(data.detail || 'Delete failed');
    }
  } catch (e) {
    alert('Delete failed: ' + e.message);
  }
}


// ─── Plugin Registration ────────────────────────────────────────────────────

export default {
  name: 'bitcoin',

  init(container) {
    injectStyles();

    registerPluginSettings({
      id: 'bitcoin',
      name: 'Bitcoin',
      icon: '\u20BF',
      helpText: 'Manage Bitcoin wallets for each persona/scope. Each chat can select which wallet to use via the sidebar. Private keys are encrypted on disk and never exposed to the AI.',
      render: (c) => renderWalletList(c),
      load: async () => {
        await loadWallets();
        return {};
      },
      save: async () => ({ success: true }),
      getSettings: () => ({})
    });

    console.log('\u2714 Bitcoin wallet settings registered');
  },

  destroy() {
    _container = null;
    _wallets = [];
  }
};
