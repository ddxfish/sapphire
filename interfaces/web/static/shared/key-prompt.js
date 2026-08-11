// shared/key-prompt.js — masked passphrase dialog (promoted from the backup
// tab's passwordPrompt, which closes on every submit — the vault needs
// retry-in-place on a wrong key, plus a setup mode and a secondary action).
//
// keyPrompt(opts) resolves:
//   { key }            — passphrase accepted (validate passed, or no validate)
//   { secondary: true} — the optional secondary button was clicked
//   null               — cancelled (Escape / overlay click / Cancel)
//
// opts:
//   title       — dialog heading
//   message     — body text (plain text, escaped)
//   mode        — 'unlock' (one input) | 'setup' (key + confirm + warnings)
//   secondaryLabel — optional extra button (e.g. "Turn privacy off")
//   validate    — optional async (key) => '' | 'error text'. Non-empty keeps
//                 the dialog OPEN with the error inline (retry-in-place).

const esc = s => String(s ?? '').replace(/[&<>"']/g,
    c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

export function keyPrompt({ title = 'Vault', message = '', mode = 'unlock',
                            secondaryLabel = null, validate = null } = {}) {
    return new Promise((resolve) => {
        const setup = mode === 'setup';
        const overlay = document.createElement('div');
        overlay.className = 'modal-overlay active';
        // Real <form> + autocomplete hints on purpose: password managers key
        // on form submission with new-password/current-password fields — bare
        // inputs never trigger the "save this password?" offer. The hidden
        // username field gives PMs an identity to file the entry under.
        overlay.innerHTML = `
            <div class="modal-base" style="max-width:440px;width:92vw;padding:18px">
                <div style="font-weight:600;margin-bottom:8px">${esc(title)}</div>
                ${message ? `<div style="font-size:var(--font-sm);margin-bottom:10px;line-height:1.5">${esc(message)}</div>` : ''}
                <form id="kp-form" action="#" method="dialog">
                    <input type="text" name="username" autocomplete="username"
                        value="sapphire-vault" readonly hidden>
                    <input type="password" id="kp-key" name="password"
                        autocomplete="${setup ? 'new-password' : 'current-password'}" placeholder="Passphrase"
                        style="width:100%;padding:8px 10px;background:var(--bg-tertiary);border:1px solid var(--border);border-radius:6px;color:var(--text-bright);box-sizing:border-box;margin-bottom:8px">
                    ${setup ? `
                    <input type="password" id="kp-confirm" name="confirm" autocomplete="new-password" placeholder="Confirm passphrase"
                        style="width:100%;padding:8px 10px;background:var(--bg-tertiary);border:1px solid var(--border);border-radius:6px;color:var(--text-bright);box-sizing:border-box;margin-bottom:8px">
                    <div style="font-size:var(--font-xs);color:var(--text-dim);line-height:1.5;margin-bottom:8px">
                        ⚠️ There is no recovery — a lost passphrase is a lost vault.<br>
                        Names of vault prompts you actually use stay visible in the app; keep them neutral.
                    </div>` : ''}
                    <div id="kp-error" style="color:#ef4444;font-size:var(--font-xs);min-height:16px;margin-bottom:8px"></div>
                    <div class="modal-actions" style="display:flex;gap:8px;justify-content:flex-end;align-items:center">
                        ${secondaryLabel ? `<button type="button" class="btn-sm" id="kp-secondary" style="margin-right:auto">${esc(secondaryLabel)}</button>` : ''}
                        <button type="button" class="btn-sm" id="kp-cancel">Cancel</button>
                        <button type="submit" class="btn-sm btn-primary" id="kp-ok">${setup ? 'Create vault' : 'Unlock'}</button>
                    </div>
                </form>
            </div>`;
        document.body.appendChild(overlay);
        const keyInput = overlay.querySelector('#kp-key');
        const errEl = overlay.querySelector('#kp-error');
        const okBtn = overlay.querySelector('#kp-ok');
        keyInput.focus();

        const done = (val) => { document.removeEventListener('keydown', onKey); overlay.remove(); resolve(val); };

        const submit = async () => {
            const key = keyInput.value;
            errEl.textContent = '';
            if (!key) { errEl.textContent = 'Passphrase must not be empty.'; return; }
            if (setup) {
                const confirm = overlay.querySelector('#kp-confirm')?.value;
                if (key !== confirm) { errEl.textContent = 'Passphrases do not match.'; return; }
            }
            if (validate) {
                okBtn.disabled = true;
                let err = '';
                try { err = await validate(key) || ''; }
                catch (e) { err = e?.message || 'Failed'; }
                okBtn.disabled = false;
                if (err) { errEl.textContent = err; keyInput.select(); return; }  // retry-in-place
            }
            done({ key });
        };

        const onKey = (e) => {
            if (e.key === 'Escape') done(null);
        };
        document.addEventListener('keydown', onKey);
        overlay.addEventListener('click', (e) => { if (e.target === overlay) done(null); });
        overlay.querySelector('#kp-cancel').addEventListener('click', () => done(null));
        // Submit via the FORM (Enter included) — password managers watch the
        // submit event; a plain button click never triggers the save offer.
        overlay.querySelector('#kp-form').addEventListener('submit', (e) => {
            e.preventDefault();
            submit();
        });
        overlay.querySelector('#kp-secondary')?.addEventListener('click', () => done({ secondary: true }));
    });
}
