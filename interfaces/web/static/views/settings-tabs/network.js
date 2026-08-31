// settings-tabs/network.js - SOCKS proxy settings
import { fetchWithTimeout } from '../../shared/fetch.js';
import * as ui from '../../ui.js';

export default {
    id: 'network',
    name: 'Network',
    icon: '\uD83C\uDF10',
    description: 'SOCKS proxy and update-check settings',
    keys: ['SOCKS_ENABLED', 'SOCKS_HOST', 'SOCKS_PORT', 'SOCKS_TIMEOUT', 'SOCKS_ROUTE_LLM', 'SOCKS_NO_PROXY_EXTRA', 'UPDATE_CHECK_ENABLED'],

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

            <div class="net-section">
                <h4>What rides the proxy</h4>
                <div id="socks-strip" style="font-size:var(--font-sm);line-height:1.7">Loading\u2026</div>
            </div>
        `;
    },

    async attachListeners(ctx, el) {
        this.refreshCreds(el);
        this.renderStrip(el);

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

    // Trust strip — the honest lane list. Fail-closed plumbing means the
    // silent risk is inverted: lanes the env CAN'T reach going direct while
    // the user believes "everything routed". This strip is that truth.
    async renderStrip(el) {
        const strip = el.querySelector('#socks-strip');
        if (!strip) return;
        try {
            const st = await fetchWithTimeout('/api/socks/status');
            if (!st.enabled) {
                strip.innerHTML = '<span style="color:var(--text-muted)">Proxy off \u2014 all traffic goes direct.</span>';
                return;
            }
            const ok = '<span style="color:var(--success,#22c55e)">\u25CF</span> ';
            const warn = '<span style="color:var(--warning,#f59e0b)">\u25CF</span> ';
            const off = '<span style="color:var(--text-muted)">\u25CB</span> ';
            const rows = [];
            rows.push(ok + 'Web tools, search &amp; downloads \u2014 routed (DNS resolves via proxy)');
            rows.push(st.route_llm
                ? ok + 'LLM providers \u2014 routed (\u2601\uFE0F cloud; \uD83C\uDFE0 local stays direct)'
                : off + 'LLM providers \u2014 exempted (Route LLM traffic is off)');
            rows.push(ok + 'Model &amp; plugin-key downloads \u2014 routed');
            rows.push(off + 'Discord &amp; Telegram \u2014 direct (own connection libraries)');
            rows.push(off + 'Voice calls \u2014 direct (UDP media; SOCKS cannot carry it)');
            rows.push(off + 'Email \u2014 direct (IMAP/SMTP sockets)');
            if (st.no_proxy?.length)
                rows.push(off + 'Bypassed hosts: ' + st.no_proxy.join(', '));
            if (!st.httpx_socks)
                rows.push(warn + "httpx[socks] not installed \u2014 LLM/cloud requests will FAIL: pip install 'httpx[socks]'");
            for (const w of (st.warnings || []))
                rows.push(warn + w);
            rows.push('<span style="color:var(--text-muted)">Nothing falls back to direct \u2014 a dead proxy fails loudly.</span>');
            strip.innerHTML = rows.join('<br>');
        } catch {
            strip.textContent = 'Status unavailable';
        }
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
