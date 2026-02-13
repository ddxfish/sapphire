// settings-tabs/network.js - SOCKS proxy and privacy whitelist
import { fetchWithTimeout } from '../../shared/fetch.js';
import * as ui from '../../ui.js';

export default {
    id: 'network',
    name: 'Network',
    icon: '\uD83C\uDF10',
    description: 'SOCKS proxy and privacy network settings',
    keys: ['SOCKS_ENABLED', 'SOCKS_HOST', 'SOCKS_PORT', 'SOCKS_TIMEOUT'],

    render(ctx) {
        const whitelist = ctx.settings.PRIVACY_NETWORK_WHITELIST || [];
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

            <div class="net-section">
                <h4>Privacy Whitelist</h4>
                <p class="text-muted" style="font-size:var(--font-sm);margin:0 0 8px">Allow these addresses when Privacy Mode is on. Supports IPs, hostnames, CIDR.</p>
                <div id="wl-entries">${this.renderEntries(whitelist)}</div>
                <div style="display:flex;gap:8px;margin-top:8px">
                    <input type="text" id="wl-input" placeholder="IP, hostname, or CIDR"
                           style="flex:1;padding:6px 8px;background:var(--input-bg);border:1px solid var(--border);border-radius:var(--radius-sm);color:var(--text);font-size:var(--font-sm)">
                    <button class="btn-sm" id="wl-add">+ Add</button>
                </div>
            </div>
        `;
    },

    renderEntries(list) {
        if (!list?.length) return '<div class="text-muted" style="font-size:var(--font-sm)">No entries</div>';
        return list.map(e => `
            <div class="wl-entry">
                <span>${esc(e)}</span>
                <button class="btn-icon danger wl-remove" data-entry="${esc(e)}">&times;</button>
            </div>
        `).join('');
    },

    validate(entry) {
        if (!entry?.trim()) return 'Entry cannot be empty';
        entry = entry.trim();
        if (/^[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?)*$/.test(entry)) return null;
        if (/^(\d{1,3}\.){3}\d{1,3}$/.test(entry)) {
            return entry.split('.').map(Number).every(o => o >= 0 && o <= 255) ? null : 'Invalid IP (octets 0-255)';
        }
        if (/^(\d{1,3}\.){3}\d{1,3}\/\d{1,2}$/.test(entry)) {
            const [ip, prefix] = entry.split('/');
            if (!ip.split('.').map(Number).every(o => o >= 0 && o <= 255)) return 'Invalid CIDR IP';
            if (parseInt(prefix) < 0 || parseInt(prefix) > 32) return 'Invalid prefix (0-32)';
            return null;
        }
        if (/^([0-9a-fA-F]{0,4}:){2,7}[0-9a-fA-F]{0,4}$/.test(entry)) return null;
        return 'Invalid format. Use IP, hostname, or CIDR';
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

        // Whitelist
        const addEntry = () => {
            const input = el.querySelector('#wl-input');
            const entry = input.value.trim();
            const err = this.validate(entry);
            if (err) { ui.showToast(err, 'error'); return; }
            const wl = ctx.settings.PRIVACY_NETWORK_WHITELIST || [];
            if (wl.includes(entry)) { ui.showToast('Already exists', 'warning'); input.value = ''; return; }
            const newWl = [...wl, entry];
            ctx.settings.PRIVACY_NETWORK_WHITELIST = newWl;
            ctx.markChanged('PRIVACY_NETWORK_WHITELIST', newWl);
            el.querySelector('#wl-entries').innerHTML = this.renderEntries(newWl);
            this.bindRemove(ctx, el);
            input.value = '';
        };

        el.querySelector('#wl-add')?.addEventListener('click', addEntry);
        el.querySelector('#wl-input')?.addEventListener('keydown', e => {
            if (e.key === 'Enter') { e.preventDefault(); addEntry(); }
        });
        this.bindRemove(ctx, el);
    },

    bindRemove(ctx, el) {
        el.querySelectorAll('.wl-remove').forEach(btn => {
            btn.addEventListener('click', () => {
                const entry = btn.dataset.entry;
                const wl = (ctx.settings.PRIVACY_NETWORK_WHITELIST || []).filter(e => e !== entry);
                ctx.settings.PRIVACY_NETWORK_WHITELIST = wl;
                ctx.markChanged('PRIVACY_NETWORK_WHITELIST', wl);
                el.querySelector('#wl-entries').innerHTML = this.renderEntries(wl);
                this.bindRemove(ctx, el);
            });
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

function esc(s) {
    const d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
}
