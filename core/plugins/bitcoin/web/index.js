// Bitcoin wallet settings plugin (multi-wallet)
// Uses shared account-manager for list/navigation, custom editor for wallet management.

import { registerPluginSettings } from '/static/shared/plugin-registry.js';
import { createAccountManager } from '/static/shared/account-manager.js';

const SECURITY_BANNER = `
    <div style="padding:14px 16px;border:2px solid #ff6b35;border-radius:10px;background:rgba(255,107,53,0.08);font-size:12px;line-height:1.6;color:var(--text)">
        <div style="font-size:14px;font-weight:700;color:#ff6b35;margin-bottom:8px">\u26A0 Backup Your Keys</div>
        Private keys are <strong style="color:#ff6b35">AES-encrypted</strong> and stored in <code>~/.config/sapphire/credentials.json</code>.
        This file is <strong style="color:#ff6b35">not included in Sapphire backups</strong>.
        <ul style="margin:6px 0;padding-left:18px">
            <li style="margin-bottom:3px">Keys are <strong style="color:#ff6b35">permanently lost</strong> if: machine dies, OS reinstall, <code>~/.config/sapphire/</code> is deleted, or the salt file is removed</li>
            <li style="margin-bottom:3px">Encryption is machine-bound &mdash; the key file cannot be decrypted on another machine</li>
            <li style="margin-bottom:3px">Use <strong style="color:#ff6b35">Export Backup</strong> on each wallet to save a plaintext WIF you can restore anywhere</li>
        </ul>
        <div style="margin-top:8px;padding:8px 12px;background:rgba(255,107,53,0.12);border-radius:6px;font-weight:600;text-align:center;color:#ff6b35">Export your wallet backups now. No backup = no recovery.</div>
    </div>`;

const manager = createAccountManager({
    prefix: 'btc',
    entityName: 'Wallet',
    listEndpoint: '/api/bitcoin/wallets',
    listKey: 'wallets',
    deleteEndpoint: (scope) => `/api/bitcoin/wallets/${encodeURIComponent(scope)}`,
    formatItem: (item) => ({
        name: item.label && item.label !== item.scope ? item.label : item.scope,
        detail: item.address || '(no key)'
    }),
    listHeader: SECURITY_BANNER,
    hint: 'Each scope maps to a chat. Select which wallet to use per-chat in the sidebar.',
    addLabel: '+ Add Wallet',
    addPrompt: 'Scope name for new wallet (e.g. "sapphire", "savings"):',
    renderEditor: renderWalletEditor,
    listFooter: renderImportButton,
});


async function showBackupGate(body, scope, address, helpers) {
    // Fetch the WIF via export endpoint
    let wif, label;
    try {
        const res = await fetch(`/api/bitcoin/wallets/${encodeURIComponent(scope)}/export`);
        if (!res.ok) throw new Error('Export failed');
        const data = await res.json();
        wif = data.wif;
        label = data.label;
    } catch {
        // If export fails, still let them through — don't brick the UI
        helpers.reloadList();
        return;
    }

    body.innerHTML = `
        <div style="padding:20px;border:2px solid #ff6b35;border-radius:12px;background:rgba(255,107,53,0.06);text-align:center">
            <div style="font-size:28px;margin-bottom:12px">\uD83D\uDD12</div>
            <div style="font-size:16px;font-weight:700;color:#ff6b35;margin-bottom:8px">Wallet Created — Back It Up Now</div>
            <div style="font-size:13px;color:var(--text);margin-bottom:6px">
                Address: <code style="font-size:11px;word-break:break-all">${address}</code>
            </div>
            <div style="font-size:13px;color:var(--text);margin-bottom:16px;line-height:1.5">
                This key is <strong>encrypted and machine-bound</strong>. If this machine dies, the key dies with it.<br>
                Download a backup now. It takes one click. Future you will be grateful.
            </div>
            <div style="display:flex;gap:10px;justify-content:center;flex-wrap:wrap">
                <button type="button" class="am-action-btn" id="btc-gate-download" style="background:#ff6b35;color:white;border-color:#ff6b35;font-weight:600;padding:10px 20px;font-size:14px">
                    \u2B07 Download Backup
                </button>
                <button type="button" class="am-action-btn" id="btc-gate-copy" style="padding:10px 16px">
                    Copy WIF
                </button>
            </div>
            <div id="btc-gate-status" style="margin-top:10px;font-size:12px;min-height:18px"></div>
            <button type="button" id="btc-gate-skip" style="margin-top:16px;background:none;border:none;color:var(--text-muted);cursor:pointer;font-size:11px;text-decoration:underline;opacity:0.6">
                I enjoy living dangerously — skip backup
            </button>
        </div>
    `;

    body.querySelector('#btc-gate-download').addEventListener('click', () => {
        const bundle = { scope, label, address, wif };
        const blob = new Blob([JSON.stringify(bundle, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url; a.download = `btc-${scope}.json`; a.click();
        URL.revokeObjectURL(url);
        body.querySelector('#btc-gate-status').textContent = '\u2713 Backup downloaded';
        body.querySelector('#btc-gate-status').style.color = 'var(--success, #28a745)';
        // Replace skip link with a big obvious Done button
        const skip = body.querySelector('#btc-gate-skip');
        const done = document.createElement('button');
        done.type = 'button';
        done.className = 'am-action-btn';
        done.style.cssText = 'margin-top:16px;background:var(--success,#28a745);color:white;border-color:var(--success,#28a745);font-weight:600;padding:10px 24px;font-size:14px';
        done.textContent = '\u2713 Done — Back to Wallets';
        done.addEventListener('click', () => helpers.reloadList());
        skip.replaceWith(done);
    });

    body.querySelector('#btc-gate-copy').addEventListener('click', async () => {
        try {
            await navigator.clipboard.writeText(wif);
            body.querySelector('#btc-gate-status').textContent = '\u2713 WIF copied to clipboard';
            body.querySelector('#btc-gate-status').style.color = 'var(--success, #28a745)';
        } catch {
            body.querySelector('#btc-gate-status').textContent = 'Copy failed';
            body.querySelector('#btc-gate-status').style.color = 'var(--error, #dc3545)';
        }
    });

    body.querySelector('#btc-gate-skip').addEventListener('click', () => {
        helpers.reloadList();
    });
}


function renderImportButton(footer, helpers) {
    footer.innerHTML = `<button type="button" class="am-add-btn" id="btc-import-file">\u21E5 Import from File</button>`;
    footer.querySelector('#btc-import-file').addEventListener('click', () => {
        const input = document.createElement('input');
        input.type = 'file';
        input.accept = '.json';
        input.addEventListener('change', async () => {
            const file = input.files[0];
            if (!file) return;
            try {
                const text = await file.text();
                const data = JSON.parse(text);
                if (!data.wif) { alert('Invalid wallet file: missing WIF key'); return; }

                const scope = data.scope || prompt('Scope name for this wallet:');
                if (!scope?.trim()) return;
                const clean = scope.trim().toLowerCase().replace(/[^a-z0-9_-]/g, '_');

                const res = await fetch(`/api/bitcoin/wallets/${encodeURIComponent(clean)}`, {
                    method: 'PUT',
                    headers: helpers.csrfHeaders({ 'Content-Type': 'application/json' }),
                    body: JSON.stringify({ wif: data.wif, label: data.label || clean })
                });
                const result = await res.json();
                if (result.success) {
                    helpers.reloadList();
                } else {
                    alert(result.detail || 'Import failed');
                }
            } catch (e) {
                alert('Import failed: ' + e.message);
            }
        });
        input.click();
    });
}


function renderWalletEditor(body, scope, wallet, helpers) {
    const existing = !!wallet?.address;

    body.innerHTML = `
        ${existing ? `
            <div class="am-group">
                <label>Address (receive)</label>
                <div style="padding:10px 14px;border:1px solid var(--border);border-radius:8px;background:var(--bg-secondary);font-family:monospace;font-size:12px;word-break:break-all;color:var(--text);user-select:all">${wallet.address}</div>
                <div class="am-hint">Share this address to receive Bitcoin. Click to select for copying.</div>
            </div>
        ` : ''}

        <div class="am-group">
            <label for="btc-label">Nickname</label>
            <input type="text" id="btc-label" value="${wallet?.label || scope}" placeholder="e.g. Sapphire Main Wallet">
        </div>

        <div class="am-group">
            <label for="btc-wif">Private Key (WIF)</label>
            <input type="password" id="btc-wif" style="font-family:monospace;font-size:12px" placeholder="${existing ? 'Leave blank to keep existing...' : 'Paste WIF or generate new below'}">
            <div class="am-hint">WIF format (starts with 5, K, or L). Stored encrypted on disk. Never shared with AI.</div>
        </div>

        <div id="btc-export-area"></div>

        <div class="am-row" style="gap:12px;flex-wrap:wrap">
            ${!existing ? '<button type="button" class="am-action-btn" id="btc-generate">Generate New</button>' : ''}
            <button type="button" class="am-action-btn" id="btc-save">Save</button>
            ${existing ? '<button type="button" class="am-action-btn" id="btc-check">Check Balance</button>' : ''}
            ${existing ? '<button type="button" class="am-action-btn" id="btc-export">Export Backup</button>' : ''}
        </div>
    `;

    // Save
    body.querySelector('#btc-save').addEventListener('click', async () => {
        const btn = body.querySelector('#btc-save');
        const wif = body.querySelector('#btc-wif').value.trim();
        const label = body.querySelector('#btc-label').value.trim() || scope;

        if (!wif && !existing) {
            helpers.showResult(false, 'WIF key is required for new wallets. Paste one or click Generate New.');
            return;
        }

        btn.disabled = true;
        btn.textContent = 'Saving...';

        try {
            const payload = { label };
            if (wif) payload.wif = wif;

            const res = await fetch(`/api/bitcoin/wallets/${encodeURIComponent(scope)}`, {
                method: 'PUT',
                headers: helpers.csrfHeaders({ 'Content-Type': 'application/json' }),
                body: JSON.stringify(payload)
            });
            const data = await res.json();
            if (data.success) {
                btn.textContent = '\u2713 Saved';
                btn.className = 'am-action-btn success';
                await manager.loadItems();
            } else {
                throw new Error(data.detail || 'Save failed');
            }
        } catch (e) {
            btn.textContent = '\u2717 Error';
            btn.className = 'am-action-btn error';
            helpers.showResult(false, e.message);
        }
        setTimeout(() => { btn.textContent = 'Save'; btn.className = 'am-action-btn'; btn.disabled = false; }, 2000);
    });

    // Generate
    body.querySelector('#btc-generate')?.addEventListener('click', async () => {
        const btn = body.querySelector('#btc-generate');
        btn.disabled = true;
        btn.textContent = 'Generating...';

        try {
            const label = body.querySelector('#btc-label').value.trim() || scope;
            const res = await fetch(`/api/bitcoin/wallets/${encodeURIComponent(scope)}`, {
                method: 'PUT',
                headers: helpers.csrfHeaders({ 'Content-Type': 'application/json' }),
                body: JSON.stringify({ generate: true, label })
            });
            const data = await res.json();
            if (data.success) {
                await manager.loadItems();
                showBackupGate(body, scope, data.address, helpers);
                return;
            } else {
                throw new Error(data.detail || 'Generation failed');
            }
        } catch (e) {
            helpers.showResult(false, `Failed: ${e.message}`);
            btn.disabled = false;
            btn.textContent = 'Generate New';
        }
    });

    // Check Balance
    body.querySelector('#btc-check')?.addEventListener('click', async () => {
        const btn = body.querySelector('#btc-check');
        btn.disabled = true;
        btn.textContent = 'Checking...';

        try {
            const res = await fetch(`/api/bitcoin/wallets/${encodeURIComponent(scope)}/check`, {
                method: 'POST',
                headers: helpers.csrfHeaders({ 'Content-Type': 'application/json' }),
                body: '{}'
            });
            const data = await res.json();
            if (data.success) {
                helpers.showResult(true, `Balance: ${data.balance_btc} BTC (${data.balance_sat} sat)`);
            } else {
                helpers.showResult(false, data.error || 'Check failed');
            }
        } catch (e) {
            helpers.showResult(false, `Error: ${e.message}`);
        }

        btn.disabled = false;
        btn.textContent = 'Check Balance';
    });

    // Export
    body.querySelector('#btc-export')?.addEventListener('click', async () => {
        const btn = body.querySelector('#btc-export');
        btn.disabled = true;
        btn.textContent = 'Exporting...';

        try {
            const res = await fetch(`/api/bitcoin/wallets/${encodeURIComponent(scope)}/export`);
            if (!res.ok) throw new Error((await res.json()).detail || 'Export failed');
            const data = await res.json();

            const area = body.querySelector('#btc-export-area');
            area.innerHTML = `
                <div class="am-group">
                    <label>WIF Private Key (keep this secret!)</label>
                    <div style="padding:10px 14px;border:1px solid var(--border);border-radius:8px;background:var(--bg-secondary);font-family:monospace;font-size:12px;word-break:break-all;color:var(--text);user-select:all">${data.wif}</div>
                    <div class="am-row" style="margin-top:6px;gap:8px">
                        <button type="button" class="am-action-btn" id="btc-copy-wif">Copy WIF</button>
                        <button type="button" class="am-action-btn" id="btc-download">Download .json</button>
                        <button type="button" class="am-action-btn" id="btc-hide-export">Hide</button>
                    </div>
                </div>
            `;

            area.querySelector('#btc-copy-wif').addEventListener('click', async () => {
                try {
                    await navigator.clipboard.writeText(data.wif);
                    helpers.showResult(true, 'WIF copied to clipboard');
                } catch { helpers.showResult(false, 'Copy failed'); }
            });

            area.querySelector('#btc-download').addEventListener('click', () => {
                const bundle = { scope: data.scope, label: data.label, address: data.address, wif: data.wif };
                const blob = new Blob([JSON.stringify(bundle, null, 2)], { type: 'application/json' });
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url; a.download = `btc-${scope}.json`; a.click();
                URL.revokeObjectURL(url);
                helpers.showResult(true, 'Downloaded wallet backup');
            });

            area.querySelector('#btc-hide-export').addEventListener('click', () => { area.innerHTML = ''; });

        } catch (e) {
            helpers.showResult(false, `Export failed: ${e.message}`);
        }

        btn.disabled = false;
        btn.textContent = 'Export Backup';
    });
}


export default {
    name: 'bitcoin',

    init(container) {
        registerPluginSettings({
            id: 'bitcoin',
            name: 'Bitcoin',
            icon: '\u20BF',
            helpText: 'Manage Bitcoin wallets for each persona/scope. Each chat can select which wallet to use via the sidebar. Private keys are encrypted on disk and never exposed to the AI.',
            render: (c) => manager.renderList(c),
            load: async () => { await manager.loadItems(); return {}; },
        });
    },

    destroy() {}
};
