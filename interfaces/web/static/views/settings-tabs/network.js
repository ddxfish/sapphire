// settings-tabs/network.js - SOCKS proxy settings
import { fetchWithTimeout } from '../../shared/fetch.js';
import * as ui from '../../ui.js';

export default {
    id: 'network',
    name: 'Network',
    icon: '\uD83C\uDF10',
    description: 'SOCKS proxy settings',
    keys: ['SOCKS_ENABLED', 'SOCKS_HOST', 'SOCKS_PORT', 'SOCKS_TIMEOUT'],

    render(ctx) {
        return `
            ${ctx.renderFields(this.keys)}

            <div class="net-section">
                <h4>Proxy Credentials</h4>
                <div class="net-cred-status" id="socks-status">Checking...</div>
                <div style="display:flex;gap:12px;margin-bottom:8px">
                    <div style="flex:1">
                        <label style="display:block;font-size:var(--font-sm);color:var(--text-secondary);margin-bottom:4px">Username</label>
                        <input type="text" id="socks-user" placeholder="Enter username" autocomplete="off"
                               style="width:100%;padding:6px 8px;background:var(--input-bg);border:1px solid var(--border);border-radius:var(--radius-sm);color:var(--text);font-size:var(--font-sm)">
                    </div>
                    <div style="flex:1">
                        <label style="display:block;font-size:var(--font-sm);color:var(--text-secondary);margin-bottom:4px">Password</label>
                        <input type="password" id="socks-pass" placeholder="Enter password" autocomplete="off"
                               style="width:100%;padding:6px 8px;background:var(--input-bg);border:1px solid var(--border);border-radius:var(--radius-sm);color:var(--text);font-size:var(--font-sm)">
                    </div>
                </div>
                <div style="display:flex;gap:8px">
                    <button class="btn-sm" id="socks-save">Save</button>
                    <button class="btn-sm" id="socks-test">Test</button>
                    <button class="btn-sm danger" id="socks-clear">Clear</button>
                </div>
                <div class="net-test-result" id="socks-result" style="display:none"></div>
            </div>
        `;
    },

    async attachListeners(ctx, el) {
        this.refreshCreds(el);

        el.querySelector('#socks-save')?.addEventListener('click', async () => {
            const user = el.querySelector('#socks-user').value;
            const pass = el.querySelector('#socks-pass').value;
            if (!user || !pass) { ui.showToast('Both fields required', 'error'); return; }
            try {
                await fetchWithTimeout('/api/credentials/socks', {
                    method: 'PUT', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ username: user, password: pass })
                });
                ui.showToast('Credentials saved', 'success');
                el.querySelector('#socks-user').value = '';
                el.querySelector('#socks-pass').value = '';
                this.refreshCreds(el);
            } catch (e) { ui.showToast(e.message || 'Failed', 'error'); }
        });

        el.querySelector('#socks-test')?.addEventListener('click', async () => {
            const result = el.querySelector('#socks-result');
            result.style.display = 'block';
            result.textContent = 'Testing...';
            result.className = 'net-test-result';
            try {
                const data = await fetchWithTimeout('/api/credentials/socks/test', { method: 'POST' }, 15000);
                result.textContent = data.status === 'success' ? `\u2713 ${data.message}` : `\u2717 ${data.error}`;
                result.classList.add(data.status === 'success' ? 'success' : 'error');
            } catch (e) {
                result.textContent = `\u2717 ${e.message}`;
                result.classList.add('error');
            }
        });

        el.querySelector('#socks-clear')?.addEventListener('click', async () => {
            if (!confirm('Clear SOCKS credentials?')) return;
            try {
                await fetchWithTimeout('/api/credentials/socks', { method: 'DELETE' });
                ui.showToast('Cleared', 'success');
                this.refreshCreds(el);
            } catch { ui.showToast('Failed', 'error'); }
        });
    },

    async refreshCreds(el) {
        const status = el.querySelector('#socks-status');
        if (!status) return;
        try {
            const data = await fetchWithTimeout('/api/credentials/socks');
            status.innerHTML = data.has_credentials
                ? '<span style="color:var(--success,#22c55e)">\u2713 Configured</span>'
                : '<span style="color:var(--text-muted)">\u25CB Not set</span>';
        } catch {
            status.innerHTML = '<span style="color:var(--error)">\u2717 Error</span>';
        }
    }
};
