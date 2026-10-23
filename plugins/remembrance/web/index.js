// Remembrance — offsite encrypted backup vault settings panel.
// The app encrypts before upload; the server is blind to your data + password.
import { registerPluginSettings } from '/static/shared/plugin-registry.js';

const P = '/api/plugin/remembrance';

function csrf() {
    const m = document.querySelector('meta[name="csrf-token"]');
    return m ? m.getAttribute('content') : '';
}

async function api(method, path, body) {
    const opt = { method, headers: { 'X-CSRF-Token': csrf() } };
    if (body !== undefined) { opt.headers['Content-Type'] = 'application/json'; opt.body = JSON.stringify(body); }
    const r = await fetch(`${P}/${path}`, opt);
    let data = {}; try { data = await r.json(); } catch {}
    if (!r.ok) throw new Error(data.detail || data.error || `HTTP ${r.status}`);
    return data;
}

function fmtBytes(n) {
    n = Number(n) || 0;
    if (n >= 1024 * 1024 * 1024) return (n / (1024 ** 3)).toFixed(2) + ' GB';
    if (n >= 1024 * 1024) return (n / (1024 ** 2)).toFixed(1) + ' MB';
    if (n >= 1024) return (n / 1024).toFixed(0) + ' KB';
    return n + ' B';
}
function esc(s) {
    return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// Masked password prompt (window.prompt shows plaintext). Resolves value or null.
function passwordPrompt(message) {
    return new Promise((resolve) => {
        const o = document.createElement('div');
        o.className = 'modal-overlay active';
        o.innerHTML = `<div class="modal-base" style="max-width:420px;width:92vw;padding:18px">
            <div style="font-size:var(--font-sm);margin-bottom:10px;line-height:1.5">${esc(message)}</div>
            <input type="password" id="rpw" autocomplete="off" style="width:100%;padding:8px 10px;background:var(--bg-tertiary);border:1px solid var(--border);border-radius:6px;color:var(--text-bright);box-sizing:border-box;margin-bottom:12px">
            <div style="display:flex;gap:8px;justify-content:flex-end">
                <button class="btn-sm" id="rpwc">Cancel</button><button class="btn-sm" id="rpwo">OK</button>
            </div></div>`;
        document.body.appendChild(o);
        const inp = o.querySelector('#rpw'); inp.focus();
        const done = (v) => { document.removeEventListener('keydown', k); o.remove(); resolve(v); };
        const k = (e) => { if (e.key === 'Escape') done(null); else if (e.key === 'Enter') done(inp.value); };
        document.addEventListener('keydown', k);
        o.addEventListener('click', (e) => { if (e.target === o) done(null); });
        o.querySelector('#rpwc').addEventListener('click', () => done(null));
        o.querySelector('#rpwo').addEventListener('click', () => done(inp.value));
    });
}

const box = (inner, mt = 14) =>
    `<div style="margin-top:${mt}px;padding:12px 14px;background:var(--bg-secondary);border-radius:8px">${inner}</div>`;
const input = (id, val, ph, type = 'text') =>
    `<input type="${type}" id="${id}" value="${esc(val)}" placeholder="${esc(ph)}" style="width:100%;padding:7px 10px;background:var(--bg-tertiary);border:1px solid var(--border);border-radius:6px;color:var(--text-bright);box-sizing:border-box;margin-bottom:8px">`;

function render(c) {
    c.innerHTML = `<div id="rmb-root" style="font-size:var(--font-sm);line-height:1.5">
        <div style="color:var(--text-muted);font-size:var(--font-xs);margin-bottom:4px">
            Offsite encrypted backups. Your data is encrypted here, with your backup password, before it ever leaves — the vault stores ciphertext only and can't read it.
        </div>
        <div id="rmb-pwwarn"></div>
        <div id="rmb-result"></div>

        ${box(`<div style="font-weight:600;margin-bottom:8px">Vault connection</div>
            ${input('rmb-url', '', 'https://remembrance.sapphireblue.dev')}
            ${input('rmb-tenant', '', 'Tenant ID')}
            ${input('rmb-key', '', 'API key — leave blank to keep existing', 'password')}
            <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
                <button class="btn-sm" id="rmb-save-acct">Save connection</button>
                <button class="btn-sm" id="rmb-test">Test</button>
                <span id="rmb-conn" style="font-size:var(--font-xs);color:var(--text-secondary)"></span>
            </div>`)}

        ${box(`<div style="font-weight:600;margin-bottom:8px">Encryption password</div>
            <div style="font-size:var(--font-xs);color:var(--text-muted);margin-bottom:8px;line-height:1.6">
                Backups are encrypted with this password before they leave this machine — the vault only ever stores ciphertext. Nothing uploads without it.
            </div>
            <div style="background:rgba(224,108,108,0.12);border:1px solid var(--danger,#e06c6c);border-radius:6px;padding:9px 11px;font-size:var(--font-xs);line-height:1.6;margin-bottom:8px">
                &#9888; <strong>Write your password down.</strong> If you lose it, your offsite backups are <strong>gone for good</strong> &mdash; there is no recovery. Not even us.
            </div>
            ${input('rmb-pw', '', 'Set the encryption password…', 'password')}
            <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
                <button class="btn-sm" id="rmb-save-pw">Save password</button>
                <span id="rmb-pwmsg" style="font-size:var(--font-xs);color:var(--text-secondary)"></span>
            </div>`)}

        ${box(`<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
                <div style="font-weight:600">Storage</div>
                <button class="btn-sm" id="rmb-refresh">Refresh</button>
            </div>
            <div id="rmb-bar" style="height:10px;background:var(--bg-tertiary);border-radius:5px;overflow:hidden;margin-bottom:6px">
                <div id="rmb-bar-fill" style="height:100%;width:0;background:var(--accent,#6ab0f3)"></div></div>
            <div id="rmb-usage" style="font-size:var(--font-xs);color:var(--text-secondary)">—</div>
            <div id="rmb-list" style="margin-top:10px"></div>`)}

        ${box(`<div style="font-weight:600;margin-bottom:8px">Back up now (offsite)</div>
            <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
                <select id="rmb-cad" style="padding:6px;background:var(--bg-tertiary);border:1px solid var(--border);border-radius:6px;color:var(--text-bright)">
                    <option value="manual">manual (kept long-term)</option>
                    <option value="daily">daily</option><option value="weekly">weekly</option><option value="monthly">monthly</option>
                </select>
                <input type="text" id="rmb-comment" placeholder="optional label…" style="flex:1;min-width:140px;padding:6px 10px;background:var(--bg-tertiary);border:1px solid var(--border);border-radius:6px;color:var(--text-bright)">
                <button class="btn-sm" id="rmb-backup">Back up now</button>
                <span id="rmb-bmsg" style="font-size:var(--font-xs);color:var(--text-secondary)"></span>
            </div>`)}

        ${box(`<div style="font-weight:600;margin-bottom:8px">Settings</div>
            <label style="font-size:var(--font-xs);color:var(--text-muted)">Extra excludes (offsite only, one per line — added to the Backup page list)</label>
            <textarea id="rmb-extra" rows="3" style="width:100%;margin:4px 0 8px;padding:7px 10px;background:var(--bg-tertiary);border:1px solid var(--border);border-radius:6px;color:var(--text-bright);box-sizing:border-box;font-family:monospace;font-size:var(--font-xs)"></textarea>
            <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:center">
                <label style="font-size:var(--font-xs)">Max size (MB) <input type="number" id="rmb-cap" style="width:80px;padding:5px;background:var(--bg-tertiary);border:1px solid var(--border);border-radius:6px;color:var(--text-bright)"></label>
                <label style="font-size:var(--font-xs)">Cron hour (0–23, blank=off) <input type="number" id="rmb-hour" min="0" max="23" style="width:64px;padding:5px;background:var(--bg-tertiary);border:1px solid var(--border);border-radius:6px;color:var(--text-bright)"></label>
                <label style="font-size:var(--font-xs);display:flex;align-items:center;gap:5px"><input type="checkbox" id="rmb-auto"> Auto-backup on cron</label>
                <button class="btn-sm" id="rmb-save-cfg">Save settings</button>
                <span id="rmb-cmsg" style="font-size:var(--font-xs);color:var(--text-secondary)"></span>
            </div>`)}
    </div>`;

    const $ = (id) => c.querySelector('#' + id);

    $('rmb-save-acct').addEventListener('click', async () => {
        try {
            await api('PUT', 'account', { server_url: $('rmb-url').value, tenant_id: $('rmb-tenant').value, api_key: $('rmb-key').value });
            $('rmb-key').value = ''; $('rmb-conn').textContent = '✓ saved';
            refresh(c);
        } catch (e) { $('rmb-conn').textContent = '✗ ' + e.message; }
    });
    $('rmb-test').addEventListener('click', async () => {
        $('rmb-conn').textContent = 'testing…';
        try { const r = await api('POST', 'test'); $('rmb-conn').textContent = r.ok ? '✓ connected' : '✗ ' + (r.error || 'failed'); }
        catch (e) { $('rmb-conn').textContent = '✗ ' + e.message; }
    });
    $('rmb-save-pw').addEventListener('click', async () => {
        const pw = $('rmb-pw').value;
        if (!pw) { $('rmb-pwmsg').textContent = 'Enter a password first'; return; }
        try {
            const r = await api('PUT', 'password', { password: pw });
            if (!r.ok) throw new Error(r.error || 'failed');
            $('rmb-pw').value = '';
            $('rmb-pwmsg').textContent = '✓ saved — write it down!';
            refresh(c);
        } catch (e) { $('rmb-pwmsg').textContent = '✗ ' + e.message; }
    });
    $('rmb-refresh').addEventListener('click', () => refresh(c));
    $('rmb-save-cfg').addEventListener('click', async () => {
        try {
            const r = await api('PUT', 'config', {
                offsite_extra_patterns: $('rmb-extra').value,
                offsite_max_mb: parseInt($('rmb-cap').value || '2048', 10),
                offsite_cron_hour: $('rmb-hour').value === '' ? null : parseInt($('rmb-hour').value, 10),
                auto_enabled: $('rmb-auto').checked,
            });
            $('rmb-cmsg').textContent = r.ok ? '✓ saved' : '✗ failed';
        } catch (e) { $('rmb-cmsg').textContent = '✗ ' + e.message; }
    });
    $('rmb-backup').addEventListener('click', async () => {
        $('rmb-bmsg').textContent = 'backing up…';
        try {
            const r = await api('POST', 'backup', { cadence: $('rmb-cad').value, comment: $('rmb-comment').value });
            if (r.ok) { $('rmb-bmsg').textContent = `✓ uploaded (${fmtBytes(r.size_bytes)})`; $('rmb-comment').value = ''; refresh(c); }
            else $('rmb-bmsg').textContent = '✗ ' + r.error;
        } catch (e) { $('rmb-bmsg').textContent = '✗ ' + e.message; }
    });

    refresh(c);
}

async function refresh(c) {
    const $ = (id) => c.querySelector('#' + id);
    // config
    try {
        const cfg = await api('GET', 'config');
        $('rmb-url').value = cfg.server_url || '';
        $('rmb-tenant').value = cfg.tenant_id || '';
        $('rmb-key').placeholder = cfg.has_api_key ? 'API key saved — blank to keep' : 'API key';
        $('rmb-extra').value = (cfg.offsite_extra_patterns || []).join('\n');
        $('rmb-cap').value = cfg.offsite_max_mb ?? 2048;
        $('rmb-hour').value = (cfg.offsite_cron_hour ?? '') === null ? '' : (cfg.offsite_cron_hour ?? '');
        $('rmb-auto').checked = !!cfg.auto_enabled;
        // password warning
        const pw = cfg.backup_password_status;
        $('rmb-pw').placeholder = pw === 'ok' ? 'Password saved — enter a new one to change it' : 'Set the encryption password…';
        $('rmb-pwwarn').innerHTML = (pw && pw !== 'ok')
            ? `<div style="background:rgba(224,108,108,0.15);border:1px solid var(--danger,#e06c6c);border-radius:6px;padding:9px 11px;margin-bottom:8px;font-size:var(--font-xs);line-height:1.5">⚠ Offsite backups require an encryption password. ${pw === 'missing' ? 'Set one' : 'The saved one can\'t be read — re-enter it'} in the <strong>Encryption password</strong> box below. Nothing uploads without it.</div>` : '';
        // last result
        const lr = cfg.last_result;
        $('rmb-result').innerHTML = lr ? `<div style="background:${lr.ok ? 'rgba(108,204,108,0.12)' : 'rgba(224,108,108,0.12)'};border:1px solid ${lr.ok ? 'rgba(108,204,108,0.5)' : 'var(--danger,#e06c6c)'};border-radius:6px;padding:8px 11px;margin-bottom:8px;font-size:var(--font-xs)">${lr.ok ? '✓' : '✗'} ${esc(lr.message)} <span style="color:var(--text-muted)">(${esc(lr.ts)})</span></div>` : '';
    } catch (e) { /* not configured yet */ }

    // status (storage + list)
    try {
        const st = await api('GET', 'status');
        if (!st.ok) { $('rmb-usage').textContent = st.error || 'not connected'; $('rmb-list').innerHTML = ''; $('rmb-bar-fill').style.width = '0'; return; }
        const used = st.usage_bytes || 0, quota = st.quota_bytes || 1;
        $('rmb-bar-fill').style.width = Math.min(100, used / quota * 100).toFixed(1) + '%';
        $('rmb-usage').textContent = `${fmtBytes(used)} / ${fmtBytes(quota)} used · ${(st.backups || []).length} backups`;
        const rows = (st.backups || []).map(b => `
            <div style="display:flex;align-items:center;gap:8px;padding:6px 0;border-top:1px solid var(--border);font-size:var(--font-xs)">
                <span style="flex:1">${esc((b.created_at || '').replace('T', ' ').slice(0, 19))} · <strong>${esc(b.cadence)}</strong>${b.comment ? ' · ' + esc(b.comment) : ''}</span>
                <span style="color:var(--text-secondary)">${fmtBytes(b.size_bytes)}</span>
                <a class="btn-icon" href="${P}/download/${encodeURIComponent(b.id)}" title="Download encrypted backup (decrypt with tools/decrypt_backup.py)" download>⬇</a>
                <button class="btn-icon rmb-restore" data-id="${esc(b.id)}" title="Restore this backup (restarts Sapphire)">↻</button>
                <button class="btn-icon danger rmb-del" data-id="${esc(b.id)}" title="Delete from vault">✕</button>
            </div>`).join('');
        $('rmb-list').innerHTML = rows || '<div style="color:var(--text-muted);font-size:var(--font-xs);padding-top:6px">No offsite backups yet.</div>';
        $('rmb-list').querySelectorAll('.rmb-del').forEach(btn => btn.addEventListener('click', async () => {
            if (!confirm('Delete this backup from the vault? This cannot be undone.')) return;
            try { const r = await api('DELETE', `backup/${encodeURIComponent(btn.dataset.id)}`); if (r.ok) refresh(c); else alert(r.error); }
            catch (e) { alert(e.message); }
        }));
        $('rmb-list').querySelectorAll('.rmb-restore').forEach(btn => btn.addEventListener('click', async () => {
            const pw = await passwordPrompt('Enter your backup password to decrypt + restore this offsite backup.\n\nSapphire will RESTART and replace your current data (kept as user.old for rollback).');
            if (pw === null) return;
            if (!confirm('Restore this offsite backup? Sapphire restarts and replaces your current data.')) return;
            try {
                const r = await api('POST', 'restore', { backup_id: btn.dataset.id, password: pw });
                if (r.ok) alert('Restoring… Sapphire is restarting. This page will reconnect shortly.');
                else alert('Restore failed: ' + r.error);
            } catch (e) { alert('Restore failed: ' + e.message); }
        }));
    } catch (e) { $('rmb-usage').textContent = e.message; }
}

registerPluginSettings({
    id: 'remembrance',
    name: 'Remembrance',
    icon: '🛰️',
    helpText: 'Offsite encrypted backups to your Remembrance vault. The app encrypts with your backup password before upload; the server stores ciphertext only.',
    render,
    load: async () => ({}),
});
