// settings-tabs/privacy.js - Privacy: prompt-vault administration + local-first toggles
// (vault plan step 8, ruled own-tab by Krem 2026-08-09). The eyeball in the
// chat sidebar is the daily driver; this card is administration. Also the
// rescued home of METRICS_ENABLED — the `privacy` settings group existed
// with that one key rendered by nothing.
import * as ui from '../../ui.js';
import { keyPrompt } from '../../shared/key-prompt.js';
import { vaultSetup, vaultUnlock, vaultLock, vaultRekey, vaultStatus } from '../../shared/vault-api.js';

export default {
    id: 'privacy',
    name: 'Privacy',
    icon: '\u{1F5DD}',
    description: 'Prompt vault administration, idle auto-lock, and privacy toggles',
    essentialKeys: ['VAULT_IDLE_MINUTES', 'METRICS_ENABLED'],

    render(ctx) {
        const card = ctx.managed
            ? '<p class="text-muted" style="font-size:var(--font-sm)">The vault is disabled in managed mode.</p>'
            : '<div id="pv-vault-card" style="padding:10px 12px;background:var(--bg-tertiary);border:1px solid var(--border);border-radius:8px;margin-bottom:14px">Loading vault status…</div>';
        return `
            <div class="settings-group">
                <h3>\u{1F5DD} Prompt Vault</h3>
                <p class="text-muted" style="font-size:var(--font-sm);line-height:1.5">
                    Encrypted prompt store (scrypt + AES-256-GCM). Unlocked, its prompts merge
                    into the app; locked, they exist nowhere. The chat sidebar's eyeball is the
                    daily driver — this card is administration. There is no key recovery:
                    a lost passphrase is a lost vault.
                </p>
                ${card}
            </div>
            ${ctx.renderFields(this.essentialKeys)}
        `;
    },

    attachListeners(ctx, el) {
        const card = el.querySelector('#pv-vault-card');
        if (!card) return;   // managed mode

        const paint = async () => {
            const v = await vaultStatus();
            const state = !v.exists ? 'no vault yet'
                : v.unlocked ? '\u{1F513} unlocked' : '\u{1F512} locked';
            card.innerHTML = `
                <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap">
                    <span>Status: <b>${state}</b></span>
                    ${!v.exists ? '<button class="btn-sm btn-primary" id="pv-setup">Create vault</button>' : ''}
                    ${v.exists && !v.unlocked ? '<button class="btn-sm btn-primary" id="pv-unlock">Unlock</button>' : ''}
                    ${v.exists && v.unlocked ? '<button class="btn-sm" id="pv-lock">Lock now</button>' : ''}
                    ${v.exists ? '<button class="btn-sm" id="pv-rekey">Change key</button>' : ''}
                </div>`;

            card.querySelector('#pv-setup')?.addEventListener('click', async () => {
                const res = await keyPrompt({
                    title: 'Set up your prompt vault',
                    message: 'Private prompts live behind an encrypted vault. Pick a passphrase.',
                    mode: 'setup',
                    validate: async (k) => { await vaultSetup(k); return ''; },
                });
                if (res?.key) { ui.showToast('Vault created and unlocked', 'success'); paint(); }
            });
            card.querySelector('#pv-unlock')?.addEventListener('click', async () => {
                const res = await keyPrompt({
                    title: 'Unlock the vault',
                    message: 'Enter your vault passphrase.',
                    validate: async (k) => { await vaultUnlock(k); return ''; },
                });
                if (res?.key) { ui.showToast('Vault unlocked', 'success'); paint(); }
            });
            card.querySelector('#pv-rekey')?.addEventListener('click', async () => {
                const res = await keyPrompt({
                    title: 'Change vault passphrase',
                    message: 'Enter your current passphrase and pick a new one. '
                        + 'Works locked or unlocked — the lock state is kept.',
                    mode: 'rekey',
                    validate: async (k, current) => { await vaultRekey(current, k); return ''; },
                });
                if (res?.key) { ui.showToast('Vault passphrase changed', 'success'); paint(); }
            });
            card.querySelector('#pv-lock')?.addEventListener('click', async () => {
                try {
                    await vaultLock();
                    ui.showToast('Vault locked', 'success');
                } catch (e) {
                    ui.showToast(e?.message || 'Failed to lock vault', 'error');
                }
                paint();
            });
        };
        paint();
    }
};
