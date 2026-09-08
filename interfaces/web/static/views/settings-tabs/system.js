// settings-tabs/system.js - System settings and danger zone
import { resetAllSettings, resetPrompts, mergeUpdates, resetChatDefaults } from '../../shared/settings-api.js';
import * as ui from '../../ui.js';
import { updateScene } from '../../features/scene.js';

// \u2500\u2500\u2500 API Tokens helpers (kept local; small surface, no need to break out) \u2500\u2500\u2500

async function listApiTokens() {
    const res = await fetch('/api/system/api-tokens', { credentials: 'same-origin' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return (await res.json()).tokens || [];
}

async function createApiToken(name) {
    const res = await fetch('/api/system/api-tokens', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name })
    });
    if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }));
        throw new Error(err.detail || `HTTP ${res.status}`);
    }
    return await res.json();
}

async function revokeApiToken(id) {
    const res = await fetch(`/api/system/api-tokens/${encodeURIComponent(id)}`, {
        method: 'DELETE',
        credentials: 'same-origin'
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
}

function _escapeHtml(s) {
    return String(s ?? '').replace(/[&<>"']/g, c => ({
        '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'
    }[c]));
}

function _fmtDate(iso) {
    if (!iso) return '\u2014';
    try { return new Date(iso).toLocaleString(); } catch { return iso; }
}

async function _renderTokenList(listEl) {
    try {
        const tokens = await listApiTokens();
        if (tokens.length === 0) {
            listEl.innerHTML = '<p class="text-muted" style="font-size:var(--font-xs);margin:0">No tokens yet. Add one above to authorize an external integration.</p>';
            return;
        }
        listEl.innerHTML = tokens.map(t => `
            <div class="api-token-row" data-id="${_escapeHtml(t.id)}"
                 style="display:flex;align-items:center;gap:10px;padding:8px;border:1px solid var(--border);border-radius:var(--radius);margin-bottom:6px;background:var(--surface)">
                <div style="flex:1;min-width:0">
                    <div style="font-weight:600;font-size:var(--font-sm)">${_escapeHtml(t.name)}</div>
                    <div class="text-muted" style="font-size:var(--font-xs);font-family:var(--mono,monospace)">
                        sk_\u2026${_escapeHtml(t.last4)} \u00B7 created ${_fmtDate(t.created_at)} \u00B7 last used ${_fmtDate(t.last_used_at)}
                    </div>
                </div>
                <button class="btn-sm danger" data-revoke-id="${_escapeHtml(t.id)}">Revoke</button>
            </div>
        `).join('');
    } catch (e) {
        listEl.innerHTML = `<p class="text-muted" style="color:var(--err)">Failed to load tokens: ${_escapeHtml(e.message)}</p>`;
    }
}

export default {
    id: 'system',
    name: 'System',
    icon: '\u26A1',
    description: 'System settings and danger zone',
    essentialKeys: ['WEB_UI_SSL_ADHOC'],
    advancedKeys: ['WEB_UI_HOST', 'WEB_UI_PORT', 'LOG_LEVEL'],

    render(ctx) {
        return `
            ${ctx.renderFields(this.essentialKeys)}
            ${ctx.renderAccordion('sys-adv', this.advancedKeys)}

            <div class="system-tools" style="margin:20px 0;padding:16px;border:1px solid var(--border);border-radius:var(--radius)">
                <h4 style="margin:0 0 10px;font-size:var(--font-sm)">Tools</h4>
                <button class="btn-primary" id="sys-setup-wizard">Run Setup Wizard</button>
                <p class="text-muted" style="font-size:var(--font-xs);margin:4px 0 0">Configure LLM, audio, voice, and identity settings step by step.</p>
            </div>

            <div class="integrity-check" style="margin:20px 0;padding:16px;border:1px solid var(--border);border-radius:var(--radius)">
                <h4 style="margin:0 0 8px;font-size:var(--font-sm)">Install Integrity</h4>
                <p class="text-muted" style="font-size:var(--font-xs);margin:0 0 12px">
                    Checks every core file against the shipped manifest (SHA256). Catches partial updates, half-applied pulls, and corruption.
                </p>
                <div id="integrity-status" style="font-size:var(--font-sm);margin-bottom:10px">Checking…</div>
                <div style="display:flex;gap:8px">
                    <button class="btn-primary" id="integrity-verify">Verify</button>
                    <button class="btn-sm danger" id="integrity-repair" style="display:none">Repair</button>
                </div>
                <div id="integrity-output" style="margin-top:10px;font-size:var(--font-xs)"></div>
            </div>

            <div class="login-password" style="margin:20px 0;padding:16px;border:1px solid var(--border);border-radius:var(--radius)">
                <h4 style="margin:0 0 8px;font-size:var(--font-sm)">Login Password</h4>
                <p class="text-muted" style="font-size:var(--font-xs);margin:0 0 12px">
                    The password for this web UI. You stay logged in after changing it.
                    Forgot it? The install guide covers the reset.
                </p>
                <form id="pw-form" autocomplete="off" style="display:flex;flex-direction:column;gap:8px;max-width:360px">
                    <input id="pw-current" type="password" placeholder="Current password" autocomplete="current-password" required
                           style="padding:6px 10px;border:1px solid var(--border);border-radius:var(--radius);background:var(--surface);color:var(--text);font-size:var(--font-sm)" />
                    <input id="pw-new" type="password" placeholder="New password (10+ characters)" autocomplete="new-password" required minlength="10"
                           style="padding:6px 10px;border:1px solid var(--border);border-radius:var(--radius);background:var(--surface);color:var(--text);font-size:var(--font-sm)" />
                    <input id="pw-confirm" type="password" placeholder="Confirm new password" autocomplete="new-password" required
                           style="padding:6px 10px;border:1px solid var(--border);border-radius:var(--radius);background:var(--surface);color:var(--text);font-size:var(--font-sm)" />
                    <div><button type="submit" class="btn-primary" id="pw-change">Change Password</button></div>
                </form>
            </div>

            <div class="api-tokens" style="margin:20px 0;padding:16px;border:1px solid var(--border);border-radius:var(--radius)">
                <h4 style="margin:0 0 8px;font-size:var(--font-sm)">API Keys</h4>
                <p class="text-muted" style="font-size:var(--font-xs);margin:0 0 12px">
                    Named bearer tokens for external integrations (mods, scripts, automation).
                    Tokens are shown ONCE at creation \u2014 save it then.
                    Independent of your login password. Revocable per-token.
                </p>

                <div style="display:flex;gap:8px;margin-bottom:12px">
                    <input id="apitok-new-name" type="text" placeholder="Token name (e.g. valheim-mod)"
                           maxlength="64"
                           style="flex:1;padding:6px 10px;border:1px solid var(--border);border-radius:var(--radius);background:var(--surface);color:var(--text);font-size:var(--font-sm)" />
                    <button class="btn-primary" id="apitok-add">+ Add</button>
                </div>

                <div id="apitok-reveal" style="display:none;margin-bottom:12px;padding:12px;border:1px solid var(--warn,#d97706);border-radius:var(--radius);background:rgba(217,119,6,0.08)">
                    <div style="font-weight:600;font-size:var(--font-sm);color:var(--warn,#d97706);margin-bottom:6px">
                        \u26A0 Save this now \u2014 it will NOT be shown again.
                    </div>
                    <div style="display:flex;gap:8px;align-items:center">
                        <code id="apitok-reveal-value" style="flex:1;padding:8px;background:var(--bg);border:1px solid var(--border);border-radius:var(--radius);font-family:var(--mono,monospace);font-size:var(--font-xs);word-break:break-all;user-select:all"></code>
                        <button class="btn-sm" id="apitok-copy">Copy</button>
                    </div>
                    <button class="btn-sm" id="apitok-dismiss" style="margin-top:8px">I saved it \u2014 dismiss</button>
                </div>

                <div id="apitok-list">Loading\u2026</div>
            </div>

            <div class="danger-zone">
                <h4>Danger Zone</h4>
                <div class="danger-section">
                    <h5>Settings</h5>
                    <button class="btn-sm danger" id="dz-reset-all">Reset All Settings</button>
                    <p class="text-muted" style="font-size:var(--font-xs);margin:4px 0 0">Reverts everything to defaults. Requires restart.</p>
                </div>
                <div class="danger-section">
                    <h5>Prompts & Personas</h5>
                    <button class="btn-primary" id="dz-merge-updates" style="margin-bottom:6px">Import App Updates</button>
                    <p class="text-muted" style="font-size:var(--font-xs);margin:0 0 10px">Adds new prompts and personas from updates without touching your stuff. Backs up first.</p>
                    <button class="btn-sm danger" id="dz-reset-prompts">Reset Prompts to Defaults</button>
                    <p class="text-muted" style="font-size:var(--font-xs);margin:4px 0 0">Overwrites all prompt files with factory versions. Creates backup first.</p>
                </div>
                <div class="danger-section">
                    <h5>Chat Defaults</h5>
                    <button class="btn-sm danger" id="dz-reset-chat">Reset Chat Defaults</button>
                </div>
            </div>
        `;
    },

    attachListeners(ctx, el) {
        el.querySelector('#sys-setup-wizard')?.addEventListener('click', () => {
            if (window.sapphireSetupWizard) {
                window.sapphireSetupWizard.open(true);
            } else {
                ui.showToast('Setup wizard plugin not loaded', 'error');
            }
        });

        // ─── Install Integrity ──────────────────────────────────────────
        const integStatus = el.querySelector('#integrity-status');
        const integVerify = el.querySelector('#integrity-verify');
        const integRepair = el.querySelector('#integrity-repair');
        const integOutput = el.querySelector('#integrity-output');

        const _badList = (items) =>
            `<div style="max-height:160px;overflow:auto;border:1px solid var(--border);border-radius:var(--radius);padding:8px;font-family:var(--mono,monospace)">${items.map(x => _escapeHtml(x)).join('<br>')}</div>`;

        async function renderIntegrity(report) {
            if (!report) {
                try {
                    const res = await fetch('/api/system/integrity', { credentials: 'same-origin' });
                    report = await res.json();
                } catch (e) {
                    integStatus.innerHTML = `<span style="color:var(--error,#dc3545)">Check failed: ${_escapeHtml(e.message)}</span>`;
                    return;
                }
            }
            if (!report.available) {
                integStatus.innerHTML = `<span class="text-muted">No manifest in this build — integrity check unavailable.</span>`;
                integRepair.style.display = 'none';
                return;
            }
            if (report.ok) {
                integStatus.innerHTML = `<span style="color:var(--success,#28a745)">✓ OK — ${report.matched}/${report.total} files match v${_escapeHtml(report.version)}</span>`;
                integRepair.style.display = 'none';
                integOutput.innerHTML = '';
            } else {
                integStatus.innerHTML = `<span style="color:var(--error,#dc3545)">⚠ ${report.mismatched.length} modified, ${report.missing.length} missing — does not match v${_escapeHtml(report.version)} (partial update?)</span>`;
                const bad = [...report.mismatched.map(f => f + '  (modified)'), ...report.missing.map(f => f + '  (missing)')];
                integOutput.innerHTML = _badList(bad);
                integRepair.style.display = '';
            }
        }

        if (integStatus) renderIntegrity();

        integVerify?.addEventListener('click', async () => {
            integStatus.textContent = 'Verifying…';
            await renderIntegrity();
        });

        integRepair?.addEventListener('click', async () => {
            integRepair.disabled = true;
            integRepair.textContent = 'Repairing…';
            try {
                const res = await fetch('/api/system/integrity/repair', {
                    method: 'POST', credentials: 'same-origin',
                    headers: { 'Content-Type': 'application/json' }
                });
                const result = await res.json();
                const lines = [
                    ...result.repaired.map(r => `✓ ${r.file} — ${r.detail}`),
                    ...(result.skipped || []).map(r => `⏸ ${r.file} — ${r.detail}`),
                    ...result.failed.map(r => `✗ ${r.file} — ${r.detail}`),
                ];
                integOutput.innerHTML = _badList(lines) + `<div style="margin-top:6px">${_escapeHtml(result.message)}</div>`;
                await renderIntegrity(result.reverify);
                ui.showToast(result.message, result.reverify.ok ? 'success' : 'warning');
            } catch (e) {
                ui.showToast(`Repair failed: ${e.message}`, 'error');
            }
            integRepair.disabled = false;
            integRepair.textContent = 'Repair';
        });

        // ─── Login Password ─────────────────────────────────────────────
        const pwForm = el.querySelector('#pw-form');
        pwForm?.addEventListener('submit', async (ev) => {
            ev.preventDefault();
            const cur = el.querySelector('#pw-current');
            const nw = el.querySelector('#pw-new');
            const cf = el.querySelector('#pw-confirm');
            const btn = el.querySelector('#pw-change');
            if (nw.value.length < 10) { ui.showToast('New password must be at least 10 characters', 'error'); nw.focus(); return; }
            if (nw.value !== cf.value) { ui.showToast('New passwords do not match', 'error'); cf.focus(); return; }
            btn.disabled = true;
            try {
                const res = await fetch('/api/system/password', {
                    method: 'POST', credentials: 'same-origin',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ current: cur.value, new: nw.value })
                });
                const body = await res.json().catch(() => ({}));
                if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);
                pwForm.reset();
                ui.showToast('Password changed — you stay logged in', 'success');
            } catch (e) {
                ui.showToast(`Change failed: ${e.message}`, 'error');
                cur.value = '';
                cur.focus();
            }
            btn.disabled = false;
        });

        // ─── API Keys section ───────────────────────────────────────────
        const listEl = el.querySelector('#apitok-list');
        const nameInput = el.querySelector('#apitok-new-name');
        const addBtn = el.querySelector('#apitok-add');
        const revealBox = el.querySelector('#apitok-reveal');
        const revealValue = el.querySelector('#apitok-reveal-value');

        // Initial load
        if (listEl) _renderTokenList(listEl);

        // Create
        addBtn?.addEventListener('click', async () => {
            const name = (nameInput?.value || '').trim();
            if (!name) {
                ui.showToast('Enter a token name first', 'error');
                return;
            }
            try {
                const created = await createApiToken(name);
                // Reveal the full token ONCE
                revealValue.textContent = created.token;
                revealBox.style.display = 'block';
                nameInput.value = '';
                await _renderTokenList(listEl);
                ui.showToast('Token created — copy and save it now', 'success');
            } catch (e) {
                ui.showToast(`Create failed: ${e.message}`, 'error');
            }
        });
        nameInput?.addEventListener('keydown', (ev) => {
            if (ev.key === 'Enter') { ev.preventDefault(); addBtn?.click(); }
        });

        // Copy revealed token
        el.querySelector('#apitok-copy')?.addEventListener('click', async () => {
            try {
                await navigator.clipboard.writeText(revealValue.textContent || '');
                ui.showToast('Copied to clipboard', 'success');
            } catch {
                ui.showToast('Copy failed — select + Ctrl-C manually', 'error');
            }
        });

        // Dismiss the reveal box (the user confirms they saved it)
        el.querySelector('#apitok-dismiss')?.addEventListener('click', () => {
            revealValue.textContent = '';
            revealBox.style.display = 'none';
        });

        // Revoke (event-delegated since rows are re-rendered on changes)
        listEl?.addEventListener('click', async (ev) => {
            const btn = ev.target.closest('[data-revoke-id]');
            if (!btn) return;
            const id = btn.getAttribute('data-revoke-id');
            const row = btn.closest('.api-token-row');
            const name = row?.querySelector('div[style*="font-weight"]')?.textContent || 'this token';
            if (!confirm(`Revoke "${name}"? Any integration using this token will stop working.`)) return;
            try {
                await revokeApiToken(id);
                await _renderTokenList(listEl);
                ui.showToast(`Revoked ${name}`, 'success');
            } catch (e) {
                ui.showToast(`Revoke failed: ${e.message}`, 'error');
            }
        });

        el.querySelector('#dz-reset-all')?.addEventListener('click', async () => {
            if (!confirm('Reset ALL settings to defaults?')) return;
            const t = prompt('Type RESET to confirm:');
            if (t?.toUpperCase() !== 'RESET') return;
            try {
                await resetAllSettings();
                ui.showToast('All settings reset. Restart to apply.', 'success');
                ctx.refreshTab();
            } catch { ui.showToast('Failed', 'error'); }
        });

        el.querySelector('#dz-reset-prompts')?.addEventListener('click', async () => {
            if (!confirm('Reset ALL prompts to factory defaults?')) return;
            const t = prompt('Type RESET to confirm:');
            if (t?.toUpperCase() !== 'RESET') return;
            try {
                await resetPrompts();
                ui.showToast('Prompts reset', 'success');
                updateScene();
            } catch { ui.showToast('Failed', 'error'); }
        });

        el.querySelector('#dz-merge-updates')?.addEventListener('click', async () => {
            if (!confirm('Import new prompts and personas from app updates?\n\nYour existing content is untouched. A backup is created first.')) return;
            try {
                const result = await mergeUpdates();
                const a = result.added || {};
                const total = (a.components||0) + (a.presets||0) + (a.monoliths||0) + (a.spice_categories||0) + (a.personas||0);
                if (total === 0) {
                    ui.showToast('Already up to date', 'info');
                } else {
                    const parts = [];
                    if (a.components) parts.push(`${a.components} components`);
                    if (a.presets) parts.push(`${a.presets} presets`);
                    if (a.monoliths) parts.push(`${a.monoliths} monoliths`);
                    if (a.spice_categories) parts.push(`${a.spice_categories} spice categories`);
                    if (a.personas) parts.push(`${a.personas} personas`);
                    ui.showToast(`Added ${parts.join(', ')}`, 'success');
                }
                updateScene();
                window.dispatchEvent(new CustomEvent('prompts-changed'));
            } catch { ui.showToast('Import failed', 'error'); }
        });

        el.querySelector('#dz-reset-chat')?.addEventListener('click', async () => {
            if (!confirm('Reset chat defaults?')) return;
            const t = prompt('Type RESET to confirm:');
            if (t?.toUpperCase() !== 'RESET') return;
            try {
                await resetChatDefaults();
                ui.showToast('Chat defaults reset', 'success');
            } catch { ui.showToast('Failed', 'error'); }
        });
    }
};
