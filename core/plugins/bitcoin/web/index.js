// Bitcoin wallet settings plugin (multi-wallet)
// Uses shared account-manager for list/navigation, custom editor for wallet management.
// Every write goes through this plugin's own routes (/api/plugin/bitcoin/...): they never
// replace a key without being told which one, and never repeat a typed key in an error.

import { registerPluginSettings } from '/static/shared/plugin-registry.js';
import { createAccountManager } from '/static/shared/account-manager.js';
import { saveJson } from '/static/shared/download.js';
import { ensureExtra } from '/static/shared/extras.js';
import pluginsAPI from '/static/shared/plugins-api.js';

const API = '/api/plugin/bitcoin';

// account-manager puts what it is given straight into the page: everything that
// came from a file or the server is escaped here first.
const esc = (s) => String(s ?? '').replace(/[&<>"']/g,
    c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

// The list says '' for a key this machine cannot decrypt and '(invalid key)' for one that is not a key.
const stateOf = (wallet) => !wallet ? 'new'
    : wallet.address === '' ? 'unreadable'
    : wallet.address === '(invalid key)' ? 'invalid' : 'ok';

const SECURITY_BANNER = `
    <div style="padding:14px 16px;border:2px solid #ff6b35;border-radius:10px;background:rgba(255,107,53,0.08);font-size:12px;line-height:1.6;color:var(--text)">
        <div style="font-size:14px;font-weight:700;color:#ff6b35;margin-bottom:8px">⚠ Backup Your Keys</div>
        Private keys are <strong style="color:#ff6b35">AES-encrypted</strong> and stored in <code>~/.config/sapphire/credentials.json</code>.
        This file is <strong style="color:#ff6b35">not included in Sapphire backups</strong>.
        <ul style="margin:6px 0;padding-left:18px">
            <li style="margin-bottom:3px">Keys are <strong style="color:#ff6b35">permanently lost</strong> if: machine dies, OS reinstall, <code>~/.config/sapphire/</code> is deleted, or the salt file is removed</li>
            <li style="margin-bottom:3px">The encryption is tied to this machine's <strong style="color:#ff6b35">name and the user Sapphire runs as</strong>. Change either one (a renamed computer, a rebuilt Docker container without a fixed hostname) and the keys cannot be read until it is changed back</li>
            <li style="margin-bottom:3px">Use <strong style="color:#ff6b35">Export Backup</strong> on each wallet to save a plaintext WIF you can restore anywhere</li>
        </ul>
        <div style="margin-top:8px;padding:8px 12px;background:rgba(255,107,53,0.12);border-radius:6px;font-weight:600;text-align:center;color:#ff6b35">Export your wallet backups now. No backup = no recovery.</div>
    </div>`;

const manager = createAccountManager({
    prefix: 'btc',
    entityName: 'Wallet',
    listEndpoint: '/api/bitcoin/wallets',
    listKey: 'wallets',
    deleteEndpoint: (scope) => `${API}/wallet/${encodeURIComponent(scope)}`,
    formatItem: (item) => ({
        name: esc(item.label && item.label !== item.scope ? item.label : item.scope),
        detail: { ok: esc(item.address),
                  unreadable: '⚠ key cannot be read on this machine',
                  invalid: '⚠ stored key is not valid' }[stateOf(item)]
    }),
    listHeader: SECURITY_BANNER,
    hint: 'Each scope maps to a chat. Select which wallet to use per-chat in the sidebar.',
    addLabel: '+ Add Wallet',
    addPrompt: 'Scope name for new wallet (e.g. "sapphire", "savings"):',
    renderEditor: renderWalletEditor,
    listFooter: renderListFooter,
});


// A result line, unless the editor it belongs to is gone (a slow answer must
// not land in the next wallet's editor).
function say(body, helpers, ok, message) {
    if (body.isConnected) helpers.showResult(ok, esc(message));
}


// Make, rename or re-key a wallet. The server answers 409 with both addresses
// when a key would be replaced; only a yes here sends it again naming the old one.
// Returns the saved wallet, or null when the person said no. Throws on failure.
async function saveWallet(scope, payload, helpers) {
    const put = async (body) => {
        const res = await fetch(`${API}/wallet/${encodeURIComponent(scope)}`, {
            method: 'PUT',
            headers: helpers.csrfHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify(body)
        });
        return { res, data: await res.json().catch(() => ({})) };
    };
    let { res, data } = await put(payload);
    if (res.status === 409 && data.needs_replace) {
        const ask = data.state === 'ok'
            ? `Replace the key in wallet "${scope}"?\n\nNow:  ${data.old_address}\nNew:  ${data.new_address}\n\n`
              + 'The key it has now is gone for good unless you exported a backup. Bitcoin at the old address stays at the old address.'
            : `Wallet "${scope}" holds a key this machine cannot read.\n\nSaving puts this key in its place (address ${data.new_address}) `
              + 'and the unreadable one is gone for good.\n\nDo this only when the new key is that wallet\'s backup, or the old wallet is empty.';
        if (!confirm(ask)) return null;
        ({ res, data } = await put({ ...payload, replace: data.state === 'ok' ? data.old_address : 'unreadable' }));
    }
    if (!res.ok || !data.success) throw new Error(data.error || data.detail || `Save failed (${res.status})`);
    return data;
}


async function showBackupGate(body, scope, address, helpers) {
    // Until the backup is taken (or refused in so many words), there is no way back to the list.
    const top = body.closest('.am-form');
    top?.querySelectorAll('#btc-back, #btc-delete').forEach(el => { el.style.display = 'none'; });

    let wif, label;
    try {
        const res = await fetch(`/api/bitcoin/wallets/${encodeURIComponent(scope)}/export`);
        if (!res.ok) throw new Error(`the server answered ${res.status}`);
        const data = await res.json();
        if (!data.wif) throw new Error('the key came back empty');
        wif = data.wif;
        label = data.label;
    } catch (e) {
        if (!body.isConnected) return;
        body.innerHTML = `
            <div style="padding:20px;border:2px solid var(--error,#dc3545);border-radius:12px;text-align:center">
                <div style="font-size:15px;font-weight:700;color:var(--error,#dc3545);margin-bottom:8px">Wallet created, but its backup could not be fetched</div>
                <div style="font-size:13px;color:var(--text);margin-bottom:14px;line-height:1.5">
                    ${esc(e.message)}. The wallet <code>${esc(scope)}</code> exists and has <strong>no backup</strong> yet.
                    Do not send Bitcoin to it until Export Backup works.
                </div>
                <button type="button" class="am-action-btn" id="btc-gate-retry">Try again</button>
                <button type="button" class="am-action-btn" id="btc-gate-leave" style="margin-left:8px">Back to wallets</button>
            </div>`;
        body.querySelector('#btc-gate-retry').addEventListener('click', () => showBackupGate(body, scope, address, helpers));
        body.querySelector('#btc-gate-leave').addEventListener('click', () => helpers.reloadList());
        return;
    }
    if (!body.isConnected) return;

    body.innerHTML = `
        <div style="padding:20px;border:2px solid #ff6b35;border-radius:12px;background:rgba(255,107,53,0.06);text-align:center">
            <div style="font-size:28px;margin-bottom:12px">🔒</div>
            <div style="font-size:16px;font-weight:700;color:#ff6b35;margin-bottom:8px">Wallet Created — Back It Up Now</div>
            <div style="font-size:13px;color:var(--text);margin-bottom:6px">
                Address: <code style="font-size:11px;word-break:break-all">${esc(address)}</code>
            </div>
            <div style="font-size:13px;color:var(--text);margin-bottom:16px;line-height:1.5">
                This key is <strong>encrypted and machine-bound</strong>. If this machine dies, the key dies with it.<br>
                Download a backup now. It takes one click. Future you will be grateful.
            </div>
            <div style="display:flex;gap:10px;justify-content:center;flex-wrap:wrap">
                <button type="button" class="am-action-btn" id="btc-gate-download" style="background:#ff6b35;color:white;border-color:#ff6b35;font-weight:600;padding:10px 20px;font-size:14px">
                    ⬇ Download Backup
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
        saveJson(`btc-${scope}.json`, bundle);   // a late revoke: same-tick lost the file on Brave
        body.querySelector('#btc-gate-status').textContent = '✓ Backup downloaded';
        body.querySelector('#btc-gate-status').style.color = 'var(--success, #28a745)';
        // Replace skip link with a big obvious Done button
        const skip = body.querySelector('#btc-gate-skip');
        if (!skip) return;
        const done = document.createElement('button');
        done.type = 'button';
        done.className = 'am-action-btn';
        done.style.cssText = 'margin-top:16px;background:var(--success,#28a745);color:white;border-color:var(--success,#28a745);font-weight:600;padding:10px 24px;font-size:14px';
        done.textContent = '✓ Done — Back to Wallets';
        done.addEventListener('click', () => helpers.reloadList());
        skip.replaceWith(done);
    });

    body.querySelector('#btc-gate-copy').addEventListener('click', async () => {
        try {
            await navigator.clipboard.writeText(wif);
            body.querySelector('#btc-gate-status').textContent = '✓ WIF copied to clipboard';
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


// Under the list: Import from File, and the limits on what she may send.
function renderListFooter(footer, helpers) {
    footer.innerHTML = `
        <button type="button" class="am-add-btn" id="btc-import-file">⇥ Import from File</button>
        <div class="am-group" id="btc-limits" style="margin-top:18px;padding:14px 16px;border:1px solid var(--border);border-radius:10px">
            <label style="font-size:0.875rem;font-weight:600">What she may send</label>
            <div class="am-hint" style="margin-top:0">A send over a limit is refused and she is told why. 0 means no limit.</div>
            <div class="am-row" style="flex-wrap:wrap;gap:12px;margin-top:6px">
                <div class="am-group" style="flex:1;min-width:150px">
                    <label for="btc-max-send">Most at once (BTC)</label>
                    <input type="text" id="btc-max-send" inputmode="decimal" autocomplete="off">
                </div>
                <div class="am-group" style="flex:1;min-width:150px">
                    <label for="btc-max-day">Most per wallet in 24 hours (BTC)</label>
                    <input type="text" id="btc-max-day" inputmode="decimal" autocomplete="off">
                </div>
                <div class="am-group" style="flex:1;min-width:150px">
                    <label for="btc-max-fee">Highest network fee (sat/byte)</label>
                    <input type="text" id="btc-max-fee" inputmode="numeric" autocomplete="off">
                </div>
            </div>
            <label style="display:flex;gap:8px;align-items:flex-start;font-weight:400;margin-top:6px;cursor:pointer">
                <input type="checkbox" id="btc-machine-sends" style="margin-top:2px">
                <span>Also send on turns no person started (agents, scheduled tasks, messages over MCP).
                Off: she only sends when you typed, spoke or used a device.</span>
            </label>
            <div class="am-row" style="align-items:center;gap:12px;margin-top:6px">
                <button type="button" class="am-action-btn" id="btc-limits-save" disabled>Save limits</button>
                <span id="btc-limits-status" style="font-size:0.75rem;color:var(--text-muted)">Loading…</span>
            </div>
        </div>`;

    const $ = (id) => footer.querySelector(id);
    const status = (text, bad) => {
        $('#btc-limits-status').textContent = text;
        $('#btc-limits-status').style.color = bad ? 'var(--error,#dc3545)' : 'var(--text-muted)';
    };
    pluginsAPI.getSettings('bitcoin').then(s => {
        if (!footer.isConnected) return;
        $('#btc-max-send').value = s.max_send_btc ?? '';
        $('#btc-max-day').value = s.max_day_btc ?? '';
        $('#btc-max-fee').value = s.max_fee_rate ?? '';
        $('#btc-machine-sends').checked = s.machine_sends === true;
        $('#btc-limits-save').disabled = false;
        status('');
    }).catch(e => status(`Could not load the limits: ${e.message}`, true));

    $('#btc-limits-save').addEventListener('click', async () => {
        const amount = (id, name) => {
            const text = $(id).value.trim();
            if (!/^\d+(\.\d{1,8})?$/.test(text)) throw new Error(`${name}: a number in BTC, up to 8 decimal places (0 for no limit).`);
            return text;
        };
        let settings;
        try {
            const fee = $('#btc-max-fee').value.trim();
            if (!/^\d+$/.test(fee) || Number(fee) < 1) throw new Error('Highest network fee: a whole number, 1 or more.');
            settings = {
                max_send_btc: amount('#btc-max-send', 'Most at once'),
                max_day_btc: amount('#btc-max-day', 'Most per wallet in 24 hours'),
                max_fee_rate: Number(fee),
                machine_sends: $('#btc-machine-sends').checked,
            };
        } catch (e) { status(e.message, true); return; }
        if ((Number(settings.max_send_btc) === 0 || Number(settings.max_day_btc) === 0)
            && !confirm('0 means no limit: she could send the whole wallet in one go. Save anyway?')) return;
        $('#btc-limits-save').disabled = true;
        try {
            await pluginsAPI.saveSettings('bitcoin', settings);
            status('✓ Saved');
        } catch (e) { status(`Not saved: ${e.message}`, true); }
        if (footer.isConnected) $('#btc-limits-save').disabled = false;
    });

    $('#btc-import-file').addEventListener('click', () => {
        const input = document.createElement('input');
        input.type = 'file';
        input.accept = '.json';
        input.addEventListener('change', async () => {
            const file = input.files[0];
            if (!file) return;
            try {
                const data = JSON.parse(await file.text());
                if (typeof data?.wif !== 'string' || !data.wif.trim()) { alert('Invalid wallet file: missing WIF key'); return; }

                const scope = (typeof data.scope === 'string' && data.scope) || prompt('Scope name for this wallet:');
                if (!scope?.trim()) return;
                const clean = scope.trim().toLowerCase().replace(/[^a-z0-9_-]/g, '_');
                const label = typeof data.label === 'string' ? data.label : clean;

                // an existing wallet with another key: saveWallet asks first, showing both addresses
                const saved = await saveWallet(clean, { wif: data.wif, label }, helpers);
                if (saved) helpers.reloadList();
            } catch (e) {
                alert('Import failed: ' + e.message);
            }
        });
        input.click();
    });
}


// `retries`: right after an install the server reloads the plugin, and for a
// moment its route is not there (404).
async function loadQR(body, scope, retries = 0) {
    const box = body.querySelector('#btc-qr');
    const amount = body.querySelector('#btc-qr-amount').value.trim();
    let res, data;
    try {
        res = await fetch(`${API}/qr/${encodeURIComponent(scope)}?amount=${encodeURIComponent(amount)}`);
        data = await res.json();
    } catch {
        res = null;
    }
    // the editor was closed, or the amount changed again, while this was out
    if (!box.isConnected || amount !== body.querySelector('#btc-qr-amount').value.trim()) return;

    if (res?.status === 404 && retries > 0) {
        setTimeout(() => loadQR(body, scope, retries - 1), 1000);
        return;
    }
    box.textContent = '';
    if (res?.ok && data?.png) {
        const img = document.createElement('img');
        img.src = `data:image/png;base64,${data.png}`;
        img.alt = data.uri;
        img.title = data.uri;
        img.style.cssText = 'display:block;width:220px;height:220px;border-radius:8px;image-rendering:pixelated';
        const uri = document.createElement('code');
        uri.textContent = data.uri;
        uri.style.cssText = 'display:block;margin-top:6px;font-size:11px;word-break:break-all;user-select:all';
        box.append(img, uri);
        return;
    }
    box.textContent = data?.error || data?.detail || 'Could not load the QR code.';
    if (data?.missing) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'am-action-btn';
        btn.style.marginLeft = '8px';
        btn.textContent = 'Install';
        btn.addEventListener('click', async () => {
            try {
                if (await ensureExtra('plugin:bitcoin')) loadQR(body, scope, 5);
            } catch (e) {
                box.textContent = e.message;
            }
        });
        box.append(btn);
    }
}


// What this wallet sent, from the plugin's own ledger: the record that stays
// true when a chat lost its copy (a reply stopped mid-send).
async function loadSends(body, scope) {
    const box = body.querySelector('#btc-sends');
    let data;
    try {
        const res = await fetch(`${API}/sends/${encodeURIComponent(scope)}`);
        if (!res.ok) return;
        data = await res.json();
    } catch { return; }
    if (!box.isConnected || !data.sends?.length) return;

    const words = { sent: 'sent', failed: 'not sent', unknown: 'outcome not known', signed: 'outcome not known' };
    const colors = { sent: 'var(--success,#28a745)', failed: 'var(--text-muted)' };
    const label = document.createElement('label');
    label.textContent = 'Sent from this wallet';
    box.append(label);
    for (const row of data.sends) {
        const line = document.createElement('div');
        line.style.cssText = 'display:flex;gap:10px;flex-wrap:wrap;font-size:0.75rem;padding:4px 0;border-bottom:1px solid var(--border)';
        const cell = (text, css = '') => { const s = document.createElement('span'); s.textContent = text; s.style.cssText = css; return s; };
        const link = document.createElement('a');
        link.href = `https://mempool.space/tx/${encodeURIComponent(row.txid)}`;
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        link.textContent = `${String(row.txid).slice(0, 12)}…`;
        link.title = row.txid;
        line.append(
            cell(new Date(row.ts * 1000).toLocaleString(), 'color:var(--text-muted)'),
            cell(`${row.btc} BTC`, 'font-weight:600'),
            cell(`to ${row.to}`, 'font-family:monospace;word-break:break-all'),
            cell(words[row.status] || row.status, `color:${colors[row.status] || 'var(--error,#dc3545)'};font-weight:600`),
            link);
        if (row.why) line.title = row.why;
        box.append(line);
    }
}


function renderWalletEditor(body, scope, wallet, helpers) {
    const state = stateOf(wallet);
    const existing = state === 'ok';
    const broken = state === 'unreadable' || state === 'invalid';

    body.innerHTML = `
        ${existing ? `
            <div class="am-group">
                <label>Address (receive)</label>
                <div style="padding:10px 14px;border:1px solid var(--border);border-radius:8px;background:var(--bg-secondary);font-family:monospace;font-size:12px;word-break:break-all;color:var(--text);user-select:all">${esc(wallet.address)}</div>
                <div class="am-hint">Share this address to receive Bitcoin. Click to select for copying.</div>
            </div>
            <div class="am-group">
                <label for="btc-qr-amount">Pay QR code</label>
                <div id="btc-qr" style="min-height:24px;font-size:12px;color:var(--text-muted)"></div>
                <input type="text" id="btc-qr-amount" inputmode="decimal" autocomplete="off" style="max-width:220px;margin-top:8px" placeholder="Amount in BTC (optional)">
                <div class="am-hint">Scan with a phone wallet to send Bitcoin to this wallet.</div>
            </div>
        ` : ''}

        ${broken ? `
            <div style="padding:14px 16px;border:2px solid var(--error,#dc3545);border-radius:10px;font-size:13px;line-height:1.6;color:var(--text)">
                <div style="font-weight:700;color:var(--error,#dc3545);margin-bottom:6px">⚠ ${state === 'unreadable'
                    ? 'This wallet has a key, but this machine cannot read it'
                    : 'The key stored for this wallet is not a valid key'}</div>
                ${state === 'unreadable' ? `
                    The encrypted key is still in the credentials file. It is tied to the machine's name and the user
                    Sapphire runs as: if one of those changed (a renamed computer, a rebuilt Docker container, a file copied
                    from another machine), <strong>changing it back and restarting Sapphire brings the wallet back</strong>.
                    Saving a key here replaces the unreadable one for good.` : `
                    Saving a key here replaces it.`}
                To restore from a backup, paste the backup's private key below, or use <strong>Import from File</strong> on the wallet list.
            </div>
        ` : `
            <div class="am-group">
                <label for="btc-label">Nickname</label>
                <input type="text" id="btc-label" autocomplete="off" placeholder="e.g. Sapphire Main Wallet">
            </div>
        `}

        <div class="am-group">
            <label for="btc-wif">${broken ? 'Private key from your backup (WIF)' : existing ? 'Replace the private key (WIF)' : 'Private Key (WIF)'}</label>
            <input type="text" id="btc-wif" autocomplete="off" autocapitalize="off" autocorrect="off" spellcheck="false"
                style="font-family:monospace;font-size:12px;-webkit-text-security:disc"
                placeholder="${existing ? 'Leave blank to keep the key it has' : broken ? 'Paste the WIF from the backup' : 'Paste WIF or generate new below'}">
            <div class="am-hint">WIF format (starts with 5, K, or L). Stored encrypted on disk. Never shared with AI.</div>
        </div>

        <div id="btc-export-area"></div>

        <div class="am-row" style="gap:12px;flex-wrap:wrap">
            ${state === 'new' ? '<button type="button" class="am-action-btn" id="btc-generate">Generate New</button>' : ''}
            <button type="button" class="am-action-btn" id="btc-save">${broken ? 'Restore from this key' : 'Save'}</button>
            ${existing ? '<button type="button" class="am-action-btn" id="btc-check">Check Balance</button>' : ''}
            ${existing ? '<button type="button" class="am-action-btn" id="btc-export">Export Backup</button>' : ''}
        </div>

        ${existing ? '<div class="am-group" id="btc-sends"></div>' : ''}
    `;
    // set as a value, never written into the markup: a nickname is free text
    const labelInput = body.querySelector('#btc-label');
    if (labelInput) labelInput.value = wallet?.label || scope;

    // Pay QR code: drawn by the server from the stored key, redrawn when the amount changes
    if (existing) {
        const amount = body.querySelector('#btc-qr-amount');
        let timer;
        amount.addEventListener('input', () => {
            // a code for the old amount must not stay scannable while a new one is typed
            body.querySelector('#btc-qr').textContent = '';
            clearTimeout(timer);
            timer = setTimeout(() => loadQR(body, scope), 400);
        });
        loadQR(body, scope);
        loadSends(body, scope);
    }

    // one write at a time: Save and Generate lock each other
    const saveBtn = body.querySelector('#btc-save');
    const genBtn = body.querySelector('#btc-generate');
    const lock = (on) => { saveBtn.disabled = on; if (genBtn) genBtn.disabled = on; };

    // Save
    saveBtn.addEventListener('click', async () => {
        const wif = body.querySelector('#btc-wif').value.trim();
        const payload = {};
        if (labelInput) payload.label = labelInput.value.trim() || scope;
        if (wif) payload.wif = wif;

        if (!wif && !existing) {
            say(body, helpers, false, broken ? 'Paste the private key from your backup first.'
                : 'WIF key is required for new wallets. Paste one or click Generate New.');
            return;
        }

        lock(true);
        saveBtn.textContent = 'Saving...';
        try {
            const saved = await saveWallet(scope, payload, helpers);
            if (!body.isConnected) return;
            if (saved?.created) { helpers.reloadList(); return; }      // a new wallet: back to the list, where it now shows
            if (saved) {
                // draw the editor again from what is stored now: the address and the QR follow the key
                const items = await manager.loadItems();
                if (!body.isConnected) return;
                renderWalletEditor(body, scope, items.find(i => i.scope === scope) || null, helpers);
                say(body, helpers, true, wif ? `Saved. Wallet "${scope}" now pays to ${saved.address}` : 'Saved.');
                return;
            }
        } catch (e) {
            say(body, helpers, false, e.message);
        }
        if (!body.isConnected) return;
        saveBtn.textContent = broken ? 'Restore from this key' : 'Save';
        lock(false);
    });

    // Generate (new wallets only; the server refuses it for a name that has a wallet)
    genBtn?.addEventListener('click', async () => {
        lock(true);
        genBtn.textContent = 'Generating...';
        try {
            const saved = await saveWallet(scope, { generate: true, label: labelInput?.value.trim() || scope }, helpers);
            if (!body.isConnected) return;
            if (saved) {
                await manager.loadItems();
                showBackupGate(body, scope, saved.address, helpers);
                return;
            }
        } catch (e) {
            say(body, helpers, false, `Failed: ${e.message}`);
        }
        if (!body.isConnected) return;
        genBtn.textContent = 'Generate New';
        lock(false);
    });

    // Check Balance
    body.querySelector('#btc-check')?.addEventListener('click', async () => {
        const btn = body.querySelector('#btc-check');
        btn.disabled = true;
        btn.textContent = 'Checking...';

        try {
            const res = await fetch(`${API}/check/${encodeURIComponent(scope)}`, {
                method: 'POST',
                headers: helpers.csrfHeaders({ 'Content-Type': 'application/json' }),
                body: '{}'
            });
            const data = await res.json().catch(() => ({}));
            if (res.ok) {
                say(body, helpers, true, `Wallet "${scope}": ${data.balance_btc} BTC (${data.balance_sat} sat)`);
            } else {
                say(body, helpers, false, data.error || data.detail || 'Check failed');
            }
        } catch (e) {
            say(body, helpers, false, `Error: ${e.message}`);
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
            if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || 'Export failed');
            const data = await res.json();
            if (!body.isConnected) return;

            const area = body.querySelector('#btc-export-area');
            area.innerHTML = `
                <div class="am-group">
                    <label>WIF Private Key for "${esc(scope)}" (keep this secret!)</label>
                    <div style="padding:10px 14px;border:1px solid var(--border);border-radius:8px;background:var(--bg-secondary);font-family:monospace;font-size:12px;word-break:break-all;color:var(--text);user-select:all">${esc(data.wif)}</div>
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
                    say(body, helpers, true, 'WIF copied to clipboard');
                } catch { say(body, helpers, false, 'Copy failed'); }
            });

            area.querySelector('#btc-download').addEventListener('click', () => {
                const bundle = { scope: data.scope, label: data.label, address: data.address, wif: data.wif };
                saveJson(`btc-${scope}.json`, bundle);
                say(body, helpers, true, 'Downloaded wallet backup');
            });

            area.querySelector('#btc-hide-export').addEventListener('click', () => { area.innerHTML = ''; });

        } catch (e) {
            say(body, helpers, false, `Export failed: ${e.message}`);
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
            icon: '₿',
            helpText: 'Manage Bitcoin wallets for each persona/scope. Each chat can select which wallet to use via the sidebar. Private keys are encrypted on disk and never exposed to the AI.',
            render: (c) => manager.renderList(c),
            load: async () => { await manager.loadItems(); return {}; },
        });
    },

    destroy() {}
};
