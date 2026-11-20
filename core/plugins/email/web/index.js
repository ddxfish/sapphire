// Email settings plugin (multi-account)
// Uses shared account-manager for list/navigation, custom editor for IMAP/SMTP config.

import { registerPluginSettings } from '/static/shared/plugin-registry.js';
import { createAccountManager } from '/static/shared/account-manager.js';

const manager = createAccountManager({
    prefix: 'email',
    entityName: 'Account',
    listEndpoint: '/api/email/accounts',
    listKey: 'accounts',
    deleteEndpoint: (scope) => `/api/email/accounts/${encodeURIComponent(scope)}`,
    formatItem: (item) => ({
        name: item.scope,
        detail: item.auth_type === 'oauth2'
            ? `${item.address || '(no address)'} \u2022 O365`
            : item.address || '(no address)'
    }),
    hint: 'Each scope maps to a chat. Select which email to use per-chat in the sidebar.',
    addLabel: '+ Add Account',
    addPrompt: 'A short name for this account (e.g. "personal", "work"):',
    renderEditor: renderEmailEditor,
});


// The big mail providers: picking one fills the four server fields and says
// which kind of password that provider takes. "Other" leaves them to type.
const LINK = 'target="_blank" rel="noopener" style="color:var(--accent-blue)"';
const PROVIDERS = [
    { id: 'gmail', label: 'Gmail / Google Workspace', domains: ['gmail.com', 'googlemail.com'],
      imap: ['imap.gmail.com', 993], smtp: ['smtp.gmail.com', 465],
      hint: `Google takes an <a href="https://myaccount.google.com/apppasswords" ${LINK}>app password</a> here, not your normal password. 2-Step Verification must be on.` },
    { id: 'yahoo', label: 'Yahoo Mail', domains: ['yahoo.com', 'ymail.com', 'rocketmail.com'],
      imap: ['imap.mail.yahoo.com', 993], smtp: ['smtp.mail.yahoo.com', 465],
      hint: 'Yahoo takes an app password here, not your normal password: Account security, Generate app password.' },
    { id: 'icloud', label: 'iCloud Mail', domains: ['icloud.com', 'me.com', 'mac.com'],
      imap: ['imap.mail.me.com', 993], smtp: ['smtp.mail.me.com', 587],
      hint: `Apple takes an app-specific password here, made at <a href="https://account.apple.com" ${LINK}>account.apple.com</a>.` },
    { id: 'fastmail', label: 'Fastmail', domains: ['fastmail.com', 'fastmail.fm'],
      imap: ['imap.fastmail.com', 993], smtp: ['smtp.fastmail.com', 465],
      hint: 'Fastmail takes an app password here: Settings, Privacy & Security, App passwords.' },
    { id: 'aol', label: 'AOL Mail', domains: ['aol.com'],
      imap: ['imap.aol.com', 993], smtp: ['smtp.aol.com', 465],
      hint: 'AOL takes an app password here, not your normal password: Account security, Generate app password.' },
    { id: 'other', label: 'Other (type the servers)', domains: [],
      hint: 'Your own server or another provider: type its two server names below. Your normal mail password, unless the provider says to make an app password.' },
];
const providerOf = (address, imap) => PROVIDERS.find(p => p.imap && p.imap[0] === String(imap || '').toLowerCase())
    || PROVIDERS.find(p => p.domains.includes(String(address || '').split('@')[1]?.toLowerCase()));
const esc = t => String(t ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const fingerprint = fp => (String(fp || '').toUpperCase().match(/.{1,2}/g) || []).join(':');

function certCard(c) {
    return `<div style="margin:8px 0;padding:10px 12px;border:1px solid var(--border);border-radius:8px;background:var(--bg-secondary);font-size:12px;line-height:1.6;overflow-wrap:anywhere">
        <div><b>${esc(c.host)}</b>, port ${c.port} (${c.which === 'smtp' ? 'sending' : 'reading mail'})</div>
        <div>Why it was refused: ${esc(c.reason || 'it could not be verified')}${c.changed || c.state === 'changed' ? '. It is NOT the certificate you trusted before' : ''}</div>
        ${c.subject ? `<div>It says it is for: ${esc(c.subject)}${c.self_signed ? ' (signed by itself)' : ''}</div>` : ''}
        ${c.expires ? `<div>It says it is good until: ${esc(c.expires)}</div>` : ''}
        ${c.fingerprint ? `<div>Fingerprint (SHA-256): <code style="font-size:11px;word-break:break-all">${esc(fingerprint(c.fingerprint))}</code></div>`
            : '<div>Its certificate could not be read, so there is nothing to trust yet. Run the test again.</div>'}
    </div>`;
}

function renderEmailEditor(body, scope, item, helpers) {
    const s = item || {};

    // An account that signs in with Microsoft (set up by an earlier version). This version has no
    // screen to set one up or to renew its sign-in, and says so instead of pointing at one.
    if (s.auth_type === 'oauth2') {
        body.innerHTML = `
            <div style="padding:16px;border:1px solid var(--border);border-radius:8px;background:var(--bg-secondary)">
                <div style="font-size:14px;font-weight:600;color:var(--text);margin-bottom:8px">Microsoft 365 account</div>
                <div style="font-size:13px;color:var(--text-muted);line-height:1.5">
                    <strong>${esc(s.address || scope)}</strong> signs in with Microsoft.<br>
                    It keeps working for as long as that sign-in renews itself. This version of Sapphire has no screen
                    to set such an account up or to sign it in again: if it stops working, the only thing to do here
                    is Delete it.
                </div>
            </div>
        `;
        return;
    }

    body.innerHTML = `
        <div class="am-group">
            <label for="email-address">Email Address</label>
            <input type="email" id="email-address" value="${esc(s.address || '')}" placeholder="you@example.com">
        </div>

        <div class="am-group">
            <label for="email-provider">Mail provider</label>
            <select id="email-provider">
                ${PROVIDERS.map(p => `<option value="${p.id}">${p.label}</option>`).join('')}
            </select>
        </div>

        <div class="am-group">
            <label for="email-password">Password</label>
            <div class="am-row">
                <input type="password" id="email-password" placeholder="${item ? 'Leave blank to keep existing...' : 'Enter password'}">
                <span class="am-action-btn" style="cursor:default;padding:6px 12px;font-size:12px" id="email-pw-status">
                    ${item ? 'Saved earlier' : 'Not set'}
                </span>
            </div>
            <div class="am-hint"><span id="email-provider-hint"></span><br>Kept encrypted on this machine.</div>
        </div>

        <div style="border-top:1px solid var(--border);padding-top:16px;margin-top:8px">
            <div style="font-size:14px;font-weight:600;color:var(--text);margin-bottom:12px">Server Settings</div>
            <div class="am-group">
                <label for="email-imap">IMAP Server</label>
                <div class="am-row">
                    <input type="text" id="email-imap" value="${esc(s.imap_server || '')}" placeholder="imap.example.com">
                    <input type="number" id="email-imap-port" value="${parseInt(s.imap_port) || 993}" placeholder="993" style="max-width:80px" min="1" max="65535">
                </div>
            </div>
            <div class="am-group" style="margin-top:12px">
                <label for="email-smtp">SMTP Server</label>
                <div class="am-row">
                    <input type="text" id="email-smtp" value="${esc(s.smtp_server || '')}" placeholder="smtp.example.com">
                    <input type="number" id="email-smtp-port" value="${parseInt(s.smtp_port) || 465}" placeholder="465" style="max-width:80px" min="1" max="65535">
                </div>
            </div>
            <div class="am-hint" style="margin-top:8px">
                Reading mail (IMAP) is port 993. Sending (SMTP) is 465, or 587 where the provider says so.
            </div>
        </div>

        <div class="am-row" style="gap:12px;margin-top:8px">
            <button type="button" class="am-action-btn" id="email-save-btn">Save</button>
            <button type="button" class="am-action-btn" id="email-test-btn">Test</button>
        </div>
        <div class="am-hint" style="margin-top:6px">Test logs in for reading and for sending with what is typed above. No email is sent.</div>
        <div id="email-test-out"></div>
    `;

    // Provider: known from the servers already saved, else from the address as it is typed.
    const $ = sel => body.querySelector(sel);
    const providerSel = $('#email-provider');
    const servers = () => [$('#email-imap').value.trim(), $('#email-imap-port').value, $('#email-smtp').value.trim(), $('#email-smtp-port').value];
    const setServers = ([imap, imapPort, smtp, smtpPort]) => {
        $('#email-imap').value = imap; $('#email-imap-port').value = imapPort;
        $('#email-smtp').value = smtp; $('#email-smtp-port').value = smtpPort;
    };
    let filled = '';        // what a provider pick put in, so a corrected address (or another pick) may replace it
    let own = null;         // servers the person typed or had saved, kept while a pick is on screen
    const applyProvider = (fill) => {
        const p = PROVIDERS.find(x => x.id === providerSel.value) || PROVIDERS[PROVIDERS.length - 1];
        $('#email-provider-hint').innerHTML = p.hint;
        if (!fill) return;
        const now = servers();
        if ((now[0] || now[2]) && now.join('\n') !== filled) own = now;       // theirs, not a pick's: remember it
        if (p.imap) {
            setServers([p.imap[0], p.imap[1], p.smtp[0], p.smtp[1]]);
            filled = servers().join('\n');
        } else if (own) {
            setServers(own);            // back to "Other": what they had comes back (the arrow keys walk through every pick)
            filled = '';
        }
    };
    providerSel.value = (providerOf(s.address, s.imap_server) || { id: 'other' }).id;
    applyProvider(false);
    providerSel.addEventListener('change', () => applyProvider(true));
    $('#email-address').addEventListener('change', () => {
        // Only while the servers are empty or still what a pick filled in: never overwrite what was typed or saved.
        const now = servers();
        if ((now[0] || now[2]) && now.join('\n') !== filled) return;
        const p = providerOf($('#email-address').value, '');
        if (p) { providerSel.value = p.id; applyProvider(true); }
    });
    // A verdict belongs to what was typed when it was given: any change takes it off the screen.
    const stale = () => {
        const out = $('#email-test-out');
        if (out) out.innerHTML = '';
        body.closest('.am-form')?.querySelector('.am-result')?.remove();
    };
    body.querySelectorAll('input, select').forEach(el => el.addEventListener('input', stale));
    let known = !!item;     // false until this account has been saved once

    // Save
    body.querySelector('#email-save-btn').addEventListener('click', async () => {
        const btn = body.querySelector('#email-save-btn');
        const address = body.querySelector('#email-address').value.trim();
        if (!address) { helpers.showResult(false, 'Email address is required'); return; }
        if (scope.toLowerCase() === 'none') {
            // In every chat, the email choice "none" means email is off: an account by that name could never be picked.
            helpers.showResult(false, 'An account cannot be called "none". Go back and add it under another name.');
            return;
        }
        if (!known && !body.querySelector('#email-password').value.trim()) {
            helpers.showResult(false, 'Type the password first.');
            return;
        }

        btn.disabled = true;
        btn.textContent = 'Saving...';

        const payload = {
            address,
            imap_server: body.querySelector('#email-imap').value.trim(),
            smtp_server: body.querySelector('#email-smtp').value.trim(),
            imap_port: parseInt(body.querySelector('#email-imap-port').value) || 993,
            smtp_port: parseInt(body.querySelector('#email-smtp-port').value) || 465,
        };
        const pw = body.querySelector('#email-password').value.trim();
        if (pw) payload.app_password = pw;

        try {
            const res = await fetch(`/api/email/accounts/${encodeURIComponent(scope)}`, {
                method: 'PUT',
                headers: helpers.csrfHeaders({ 'Content-Type': 'application/json' }),
                body: JSON.stringify(payload)
            });
            const data = await res.json();
            if (data.success) {
                btn.textContent = 'Saved';
                btn.className = 'am-action-btn success';
                const status = body.querySelector('#email-pw-status');
                if (status && pw) { status.textContent = '\u2713 Stored'; status.className = 'am-action-btn success'; }
                known = true;
                body.querySelector('#email-password').value = '';
                body.querySelector('#email-password').placeholder = 'Leave blank to keep existing...';
                stale();
                await manager.loadItems();
            } else {
                throw new Error(data.detail || 'Save failed');
            }
        } catch (e) {
            btn.textContent = 'Error';
            btn.className = 'am-action-btn error';
            helpers.showResult(false, esc(e.message));        // showResult writes HTML: never raw text
        }
        setTimeout(() => { btn.textContent = 'Save'; btn.className = 'am-action-btn'; btn.disabled = false; }, 3000);
    });

    // Test: the plugin's own check (reading AND sending, certificates verified), with what is typed.
    const testOut = $('#email-test-out');
    const typed = () => {
        const payload = {
            scope,
            address: $('#email-address').value.trim(),
            imap_server: $('#email-imap').value.trim(),
            imap_port: parseInt($('#email-imap-port').value) || 993,
            smtp_server: $('#email-smtp').value.trim(),
            smtp_port: parseInt($('#email-smtp-port').value) || 465,
        };
        const pw = $('#email-password').value.trim();
        if (pw) payload.app_password = pw;
        return payload;
    };
    const send = async (path, payload) => {
        const res = await fetch(`/api/plugin/email/${path}`, {
            method: 'POST',
            headers: helpers.csrfHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify(payload),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.success === false) throw new Error(data.detail || data.error || `Sapphire turned that down (${res.status}).`);
        return data;
    };

    let resetTimer = null;
    async function runTest() {
        const btn = $('#email-test-btn');
        clearTimeout(resetTimer);
        if (!$('#email-imap').value.trim() || !$('#email-smtp').value.trim()) {
            // Save would store the blank; a test that quietly used the old server would then disagree with it.
            return helpers.showResult(false, 'Fill in both server names first.');
        }
        body.closest('.am-form')?.querySelector('.am-result')?.remove();
        btn.disabled = true;
        btn.textContent = 'Testing...';
        btn.className = 'am-action-btn';
        testOut.innerHTML = '';
        try {
            const data = await send('test', typed());
            const line = (label, half, good) => `<div style="margin:4px 0;color:${half.ok ? 'var(--success, #4caf50)' : 'var(--error-text, #e57373)'}">
                ${half.ok ? '\u2713' : '\u2717'} <b>${label}:</b> ${esc(half.ok ? good : half.words)}</div>`;
            const certs = data.certs || [];
            const canTrust = certs.length > 0 && certs.every(c => c.fingerprint && !c.known);
            testOut.innerHTML = `<div style="margin-top:10px;font-size:13px;line-height:1.5">
                ${line('Reading mail', data.reading, `logged in, ${data.reading.count ?? 0} emails in the inbox`)}
                ${line('Sending', data.sending, 'logged in')}
                ${certs.map(certCard).join('')}
                ${canTrust ? `<div class="am-hint">What a certificate says about itself proves nothing: anyone between you and the server could show one like this. Trust it only if this is your own server, or its admin gave you this fingerprint.</div>
                    <button type="button" class="am-action-btn" id="email-trust-btn">Trust ${certs.length > 1 ? 'these certificates' : 'this certificate'}</button>` : ''}
                ${certs.some(c => c.known) ? '<div class="am-hint">This provider always has a certificate that checks out, so this one cannot be trusted. It may be security software on this machine ("scan encrypted connections"), a company or hotel network, or an attack. Try another network.</div>' : ''}
                ${data.works && !known ? '<div class="am-hint">It works. Press Save to keep this account.</div>' : ''}
            </div>`;
            btn.textContent = data.works ? '\u2713 Works' : '\u2717 Failed';
            btn.className = `am-action-btn ${data.works ? 'success' : 'error'}`;
            testOut.querySelector('#email-trust-btn')?.addEventListener('click', async (ev) => {
                const trustBtn = ev.currentTarget;
                if (trustBtn.disabled) return;
                const hosts = certs.map(c => c.host).filter((h, i, all) => all.indexOf(h) === i).join(' and ');
                if (!confirm(`Trust ${hosts}? Sapphire will send this account's password to a server showing this certificate, and only this one.`)) return;
                trustBtn.disabled = true;
                try {
                    for (const c of certs) await send('trust', { host: c.host, port: c.port, which: c.which, fingerprint: c.fingerprint });
                    reloadTrusted?.();
                    runTest();
                } catch (e) {
                    trustBtn.disabled = false;
                    helpers.showResult(false, esc(e.message));
                }
            });
        } catch (e) {
            btn.textContent = '\u2717 Failed';
            btn.className = 'am-action-btn error';
            helpers.showResult(false, esc(e.message));
        }
        btn.disabled = false;
        resetTimer = setTimeout(() => { btn.textContent = 'Test'; btn.className = 'am-action-btn'; }, 6000);
    }
    $('#email-test-btn').addEventListener('click', runTest);
}


let reloadTrusted = null;      // the list below the accounts, redrawn after a certificate is trusted

// Certificates the person trusted (a server that cannot be verified the normal way), each with a way back.
function renderTrusted(box) {
    const draw = (list) => {
        if (!list?.length) { box.innerHTML = ''; return; }
        box.innerHTML = `<div style="margin-top:20px;padding:12px 16px;border:1px solid var(--border);border-radius:8px;background:var(--bg-secondary)">
            <div style="font-size:13px;font-weight:600;color:var(--text)">Trusted certificates</div>
            <div style="font-size:11px;color:var(--text-muted);margin-bottom:8px">Mail servers you chose to trust although their certificate could not be verified. Sapphire accepts exactly that certificate there and no other.</div>
            ${list.map((c, i) => `<div style="display:flex;align-items:center;gap:12px;font-size:12px;margin:6px 0">
                <div style="flex:1;min-width:0"><b>${esc(c.host)}</b>, port ${c.port}<br>
                    <code style="font-size:10px;word-break:break-all;color:var(--text-muted)">${esc(fingerprint(c.fingerprint))}</code></div>
                <button type="button" class="am-action-btn" data-forget="${i}">Forget</button>
            </div>`).join('')}
        </div>`;
        box.querySelectorAll('[data-forget]').forEach(btn => btn.addEventListener('click', async () => {
            const c = list[+btn.dataset.forget];
            const headers = { 'Content-Type': 'application/json' };
            const csrf = document.querySelector('meta[name="csrf-token"]')?.content;
            if (csrf) headers['X-CSRF-Token'] = csrf;
            btn.disabled = true;
            let why = '';
            try {
                const res = await fetch('/api/plugin/email/untrust', { method: 'POST', headers, body: JSON.stringify({ host: c.host, port: c.port }) });
                const data = await res.json().catch(() => ({}));
                if (res.ok && data.success !== false && Array.isArray(data.trusted)) return draw(data.trusted);
                why = data.detail || `Sapphire turned that down (${res.status}).`;
            } catch (e) {
                why = "Can't reach Sapphire.";
            }
            // Not forgotten: the list stays as it is (an empty box here would say nothing is trusted).
            btn.disabled = false;
            const note = document.createElement('div');
            note.style.cssText = 'font-size:11px;color:var(--error-text, #e57373)';
            note.textContent = `Not forgotten: ${why} It is still trusted. Reload the page and try again.`;
            btn.parentElement.after(note);
        }));
    };
    const load = () => fetch('/api/plugin/email/certs').then(r => r.json()).then(d => draw(d.trusted || [])).catch(() => {});
    reloadTrusted = load;
    load();
}


function renderWithDaemonSettings(container) {
    // Poll interval setting above accounts
    const wrapper = document.createElement('div');
    wrapper.innerHTML = `
        <div style="margin-bottom:20px;padding:12px 16px;border:1px solid var(--border);border-radius:8px;background:var(--bg-secondary);display:flex;flex-direction:column;gap:12px">
            <div style="display:flex;align-items:center;gap:12px">
                <label for="email-poll-interval" style="font-size:13px;font-weight:600;color:var(--text);white-space:nowrap">Poll Interval</label>
                <input type="number" id="email-poll-interval" min="30" max="3600" style="width:80px" value="120">
                <span style="font-size:12px;color:var(--text-muted)">seconds (at least 30)</span>
            </div>
            <div style="display:flex;align-items:center;gap:12px">
                <label class="pm-toggle" style="flex-shrink:0">
                    <input type="checkbox" id="email-allow-all">
                    <span class="pm-slider"></span>
                </label>
                <div>
                    <span style="font-size:13px;font-weight:600;color:var(--text)">Allow sending to any address</span>
                    <div style="font-size:11px;color:var(--text-muted)">When off, AI can only email whitelisted contacts. When on, AI can email anyone directly.</div>
                </div>
            </div>
            <div style="display:flex;align-items:center;gap:12px">
                <label class="pm-toggle" style="flex-shrink:0">
                    <input type="checkbox" id="email-reply-all" checked>
                    <span class="pm-slider"></span>
                </label>
                <div>
                    <span style="font-size:13px;font-weight:600;color:var(--text)">Allow reply-all</span>
                    <div style="font-size:11px;color:var(--text-muted)">When on, a reply can go to everyone on the email (they are already on the thread). When off, replies go to the sender only.</div>
                </div>
            </div>
            <div style="display:flex;align-items:center;gap:12px;margin-top:10px">
                <label class="pm-toggle" style="flex-shrink:0">
                    <input type="checkbox" id="email-reply-anyone">
                    <span class="pm-slider"></span>
                </label>
                <div>
                    <span style="font-size:13px;font-weight:600;color:var(--text)">Auto-reply to anyone</span>
                    <div style="font-size:11px;color:var(--text-muted)">Off: a reply sent while nobody is watching (a new-email task's automatic reply, or her own reply in that task's run) only goes to people in People with 'Allow email' ticked. On: it answers anyone who writes in. Never automated mail, never the same mail twice, at most 10 a day per sender and 30 an hour per account.</div>
                </div>
            </div>
            <div style="display:flex;align-items:center;gap:12px;margin-top:10px">
                <label class="pm-toggle" style="flex-shrink:0">
                    <input type="checkbox" id="email-keep-index" checked>
                    <span class="pm-slider"></span>
                </label>
                <div>
                    <span style="font-size:13px;font-weight:600;color:var(--text)">Remember mail lists on this machine</span>
                    <div style="font-size:11px;color:var(--text-muted)">Lets the Email page open at once: it keeps who each mail is from, its subject and date in a file here, never the mail itself. Turning this off deletes what was kept.</div>
                </div>
            </div>
        </div>
    `;
    container.appendChild(wrapper);

    // The switches are wired up front, and say so when they could not be read or saved: a switch
    // showing "off" for "allow sending to any address" while it is on is worse than no switch.
    const q = id => container.querySelector(id);
    const inputs = ['#email-poll-interval', '#email-allow-all', '#email-reply-all', '#email-reply-anyone', '#email-keep-index'].map(q);
    const note = document.createElement('div');
    note.style.cssText = 'font-size:12px;color:var(--error-text, #e57373)';
    wrapper.firstElementChild.appendChild(note);
    const save = async (patch, undo) => {
        note.textContent = '';
        try {
            const headers = { 'Content-Type': 'application/json' };
            const tok = document.querySelector('meta[name="csrf-token"]')?.content;
            if (tok) headers['X-CSRF-Token'] = tok;
            const res = await fetch('/api/webui/plugins/email/settings', { method: 'PUT', headers, body: JSON.stringify(patch) });
            if (!res.ok) throw new Error(String(res.status));
        } catch {
            undo();
            note.textContent = "That setting was not saved. Reload the page and try again.";
        }
    };
    const toggle = (id, key, { confirmOn } = {}) => q(id).addEventListener('change', () => {
        const box = q(id);
        if (confirmOn && box.checked && !confirm(confirmOn)) { box.checked = false; return; }
        save({ [key]: box.checked }, () => { box.checked = !box.checked; });
    });
    toggle('#email-allow-all', 'allow_all_recipients',
           { confirmOn: 'WARNING: This allows the AI to email anyone, not just whitelisted contacts. Are you sure?' });
    toggle('#email-reply-all', 'allow_reply_all');
    toggle('#email-reply-anyone', 'auto_reply_anyone');
    toggle('#email-keep-index', 'keep_mail_index');
    let pollWas = 120;
    q('#email-poll-interval').addEventListener('change', () => {
        const box = q('#email-poll-interval');
        const val = Math.min(3600, Math.max(30, parseInt(box.value) || 120));
        box.value = val;
        save({ poll_interval: val }, () => { box.value = pollWas; }).then(() => { if (!note.textContent) pollWas = val; });
    });

    inputs.forEach(el => { el.disabled = true; });
    fetch('/api/webui/plugins/email/settings').then(r => { if (!r.ok) throw new Error(String(r.status)); return r.json(); }).then(data => {
        const s = data.settings || {};
        if (s.poll_interval != null) { q('#email-poll-interval').value = s.poll_interval; pollWas = s.poll_interval; }
        q('#email-allow-all').checked = !!s.allow_all_recipients;
        q('#email-reply-all').checked = s.allow_reply_all !== false;
        q('#email-reply-anyone').checked = s.auto_reply_anyone === true;
        q('#email-keep-index').checked = s.keep_mail_index !== false;
        inputs.forEach(el => { el.disabled = false; });
    }).catch(() => {
        note.textContent = "Couldn't read these settings, so they are not shown as they are. Reload the page.";
    });

    // Accounts below — render into a sub-container so renderList doesn't wipe our settings
    const accountsDiv = document.createElement('div');
    container.appendChild(accountsDiv);
    manager.renderList(accountsDiv);

    const trustedDiv = document.createElement('div');
    container.appendChild(trustedDiv);
    renderTrusted(trustedDiv);
}

export default {
    name: 'email',

    init(container) {
        registerPluginSettings({
            id: 'email',
            name: 'Email',
            icon: '\uD83D\uDCE7',
            helpText: 'Configure email accounts for each persona/scope. Each chat can select which email account to use via the sidebar. Works with any IMAP/SMTP server.',
            render: (c) => renderWithDaemonSettings(c),
            load: async () => { await manager.loadItems(); return {}; },
        });
    },

    destroy() {}
};
