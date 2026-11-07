// settings-tabs/privacy.js - Privacy: prompt-vault administration + local-first toggles
// (vault plan step 8, ruled own-tab by Krem 2026-08-09). The eyeball in the
// chat sidebar is the daily driver; this card is administration. Also the
// rescued home of METRICS_ENABLED — the `privacy` settings group existed
// with that one key rendered by nothing.
import * as ui from '../../ui.js';
import { keyPrompt } from '../../shared/key-prompt.js';
import { vaultSetup, vaultUnlock, vaultLock, vaultRekey, vaultStatus, vaultDelete } from '../../shared/vault-api.js';

// Delete-vault's two asks (2026-11-07: a user who couldn't get Default out of
// the vault even with a picture per click). typed: the words that arm the
// button, or null for a plain "I understand". Resolves true / false.
function understand({ title, html, typed = null, okLabel = 'I understand' }) {
    return new Promise((resolve) => {
        const overlay = document.createElement('div');
        overlay.className = 'modal-overlay active';
        overlay.innerHTML = `
            <div class="modal-base" style="max-width:460px;width:92vw;padding:18px">
                <div style="font-weight:600;margin-bottom:8px">${title}</div>
                <div style="font-size:var(--font-sm);line-height:1.5;margin-bottom:12px">${html}</div>
                ${typed ? `<input type="text" id="pu-typed" autocomplete="off" placeholder="${typed}"
                    style="width:100%;padding:8px 10px;background:var(--bg-tertiary);border:1px solid var(--border);border-radius:6px;color:var(--text-bright);box-sizing:border-box;margin-bottom:12px">` : ''}
                <div class="modal-actions" style="display:flex;gap:8px;justify-content:flex-end">
                    <button type="button" class="btn-sm" id="pu-cancel">Cancel</button>
                    <button type="button" class="btn-sm" id="pu-ok"
                        style="background:#ef4444;color:#fff;border-color:#ef4444"${typed ? ' disabled' : ''}>${okLabel}</button>
                </div>
            </div>`;
        document.body.appendChild(overlay);
        const ok = overlay.querySelector('#pu-ok');
        const input = overlay.querySelector('#pu-typed');
        const done = (v) => { document.removeEventListener('keydown', onKey); overlay.remove(); resolve(v); };
        const onKey = (e) => { if (e.key === 'Escape') done(false); };
        document.addEventListener('keydown', onKey);
        overlay.addEventListener('click', (e) => { if (e.target === overlay) done(false); });
        overlay.querySelector('#pu-cancel').onclick = () => done(false);
        ok.onclick = () => done(true);
        if (input) {
            input.oninput = () => { ok.disabled = input.value.trim().toUpperCase() !== typed; };
            input.focus();
        } else {
            overlay.querySelector('#pu-cancel').focus();   // the safe button holds focus
        }
    });
}

export default {
    id: 'privacy',
    name: 'Privacy',
    icon: '\u{1F5DD}',
    description: 'Prompt vault administration, idle auto-lock, and privacy toggles',
    essentialKeys: ['VAULT_IDLE_MINUTES', 'PRIVATE_ALLOW_UNFLAGGED_TOOLS', 'METRICS_ENABLED'],

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
                <p class="text-muted" style="font-size:var(--font-sm);line-height:1.5">
                    Private chats encrypt with the same key — messages, images, and
                    settings. Note: backups made <b>before</b> a chat went private still
                    hold its earlier history in plaintext (nothing can rewrite the past).
                    You can mark any old public chat private in Chat Manager at any
                    time — its whole history encrypts on the spot.
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
                    ${v.exists ? '<button class="btn-sm" id="pv-delete" style="margin-left:auto;color:#ef4444">Delete vault</button>' : ''}
                </div>`;

            card.querySelector('#pv-setup')?.addEventListener('click', async () => {
                const res = await keyPrompt({
                    title: 'Set up your prompt vault',
                    message: 'Pick a passphrase for your vault.',
                    mode: 'setup',
                    validate: async (k) => { await vaultSetup(k); return ''; },
                });
                if (res?.key) { ui.showToast('Vault created and unlocked', 'success'); paint(); }
            });
            card.querySelector('#pv-unlock')?.addEventListener('click', async () => {
                const res = await keyPrompt({
                    title: 'Unlock the vault',
                    message: "Enter your vault passphrase. Your private chats will show up, and public chats won't send while it's open.",
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
            card.querySelector('#pv-delete')?.addEventListener('click', async () => {
                const first = await understand({
                    title: 'Delete the vault?',
                    html: `<ul style="margin:0 0 10px;padding-left:18px">
                            <li><b>Every private chat is deleted</b>: messages, images, settings.
                                That includes Default if it's private.</li>
                            <li>Every private prompt in the vault is deleted.</li>
                            <li>There is no undo.</li>
                        </ul>
                        Remember your passphrase? Don't delete. Unlock the vault instead,
                        then make a chat public in Chat › Manage (🔓 on its row).`,
                });
                if (!first) return;
                const second = await understand({
                    title: 'Last check',
                    html: 'This deletes the vault and all its private chats for good. '
                        + 'Type <b>I UNDERSTAND</b> to go ahead.',
                    typed: 'I UNDERSTAND',
                    okLabel: 'Delete vault',
                });
                if (!second) return;
                try {
                    await vaultDelete('I UNDERSTAND');
                    ui.showToast('Vault deleted. Private chats are gone; your public chats are untouched.', 'success');
                } catch (e) {
                    ui.showToast(e?.message || 'Vault delete failed', 'error');
                }
                paint();
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
