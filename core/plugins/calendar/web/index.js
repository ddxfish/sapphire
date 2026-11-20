// core/plugins/calendar/web/index.js - the Accounts panel, and the Settings card
//
// ONE panel, drawn in two places: Settings > Plugins > Calendar (this file's
// default export registers the card), and the Calendar page's Accounts button
// (app/index.js imports mountAccounts). Its doors are routes/accounts.py and
// routes/links.py; what it shows is connect.view() on the server.
//
// The shape it draws, top down:
//   a calendar (what a chat picks; its events live on this machine)
//     an account (Google, or a login at a CalDAV provider)
//       that account's calendars, each ticked in or left out
//     calendar addresses (.ics): read-only, no login
//
// Three regions, painted apart so a refresh never touches what a person is
// typing: the top (which calendar), the list (accounts and their calendars),
// and the form (add or edit). While a linked calendar is being read the list
// is asked for again every few seconds; the form region is never repainted by
// that, and neither is the list while the person has something in it open.
//
// Every word from the server goes through escapeHtml before it is drawn.
import { fetchWithTimeout } from '/static/shared/fetch.js';
import { escapeHtml, showConfirm } from '/static/shared/modal.js';
import { showToast } from '/static/shared/notify.js';
import { registerPluginSettings } from '/static/shared/plugin-registry.js';
import { renderSettingsForm, readSettingsForm } from '/static/shared/plugin-settings-renderer.js';
import pluginsAPI from '/static/shared/plugins-api.js';

const API = '/api/plugin/calendar';
const POLL_MS = 4000;                          // while the server is reading a linked calendar
const POLL_MAX_MS = 30000;
const ICONS = { google: 'G', icloud: '☁', fastmail: 'F', yahoo: 'Y', nextcloud: 'N', caldav: '⇄' };
const enc = encodeURIComponent;
const esc = escapeHtml;
let mounts = 0;

function injectStyles() {
    if (document.getElementById('cal-accounts-styles')) return;
    const st = document.createElement('style');
    st.id = 'cal-accounts-styles';
    st.textContent = `
        .ca-root { display: flex; flex-direction: column; gap: 10px; color: var(--text); font-size: var(--font-md, 0.875rem); }
        .ca-root.busy { opacity: 0.6; }
        .ca-list, .ca-top, .ca-formbox { display: flex; flex-direction: column; gap: 10px; }
        .ca-muted { color: var(--text-muted); font-size: var(--font-sm, 0.8rem); line-height: 1.45; overflow-wrap: anywhere; }
        .ca-error { color: var(--error, var(--warning)); font-size: var(--font-sm, 0.8rem); line-height: 1.45; overflow-wrap: anywhere; }
        .ca-box { border: 1px solid var(--border); border-radius: var(--radius-md, 8px); background: var(--bg-secondary); padding: 10px 12px; }
        .ca-head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
        .ca-icon { width: 26px; height: 26px; border-radius: 50%; flex: none; display: flex; align-items: center; justify-content: center;
                   background: var(--bg-tertiary, var(--border)); font-weight: 700; font-size: 0.8rem; }
        .ca-name { font-weight: 600; overflow-wrap: anywhere; }
        .ca-grow { flex: 1; min-width: 120px; }
        .ca-btns { display: flex; gap: 6px; flex-wrap: wrap; }
        .ca-btns .btn, .ca-row .btn { padding: 2px 10px; }
        .ca-row { display: flex; align-items: flex-start; gap: 8px; padding: 6px 0 0 4px; }
        .ca-row label { display: flex; align-items: flex-start; gap: 8px; cursor: pointer; flex: none; max-width: 60%; }
        .ca-row input[type="checkbox"] { margin-top: 3px; flex: none; }
        .ca-dot { width: 10px; height: 10px; border-radius: 50%; margin-top: 5px; flex: none; background: var(--border); }
        .ca-dot.c0 { background: var(--trim, var(--primary)); } .ca-dot.c1 { background: var(--accent-cyan, var(--primary)); }
        .ca-dot.c2 { background: var(--accent-orange, var(--warning)); } .ca-dot.c3 { background: var(--success); }
        .ca-dot.c4 { background: var(--accent-blue, var(--primary)); } .ca-dot.c5 { background: var(--warning); }
        label.ca-field { display: flex; flex-direction: column; gap: 4px; margin-top: 8px; }
        label.ca-field > span { font-size: var(--font-sm, 0.8rem); color: var(--text-secondary, var(--text)); }
        label.ca-field input, label.ca-field select { width: 100%; box-sizing: border-box; }
        .ca-line { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
        .ca-line select { width: auto; max-width: 100%; }
        .ca-tiles { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 8px; margin-top: 8px; }
        .ca-tile { display: flex; align-items: center; gap: 8px; padding: 8px 10px; border: 1px solid var(--border); border-radius: var(--radius-md, 8px);
                   background: var(--bg, transparent); color: var(--text); cursor: pointer; text-align: left; font: inherit; }
        .ca-tile:hover:not(:disabled) { border-color: var(--primary); }
        .ca-tile:disabled { opacity: 0.55; cursor: default; }
        .ca-adv summary { cursor: pointer; color: var(--text-muted); font-size: var(--font-sm, 0.8rem); margin-top: 8px; }
        .ca-foot { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
        .ca-form-error { margin-top: 8px; padding: 6px 8px; border: 1px solid var(--error, var(--warning)); border-radius: var(--radius-sm, 4px); }
        .ca-card h4 { margin: 14px 0 8px; font-size: var(--font-md, 0.875rem); color: var(--text-secondary, var(--text)); }
    `;
    document.head.appendChild(st);
}

function ago(stamp) {
    if (!stamp) return 'not read yet';
    const s = Math.max(0, Date.now() / 1000 - stamp);
    if (s < 90) return 'read just now';
    if (s < 3600) return `read ${Math.round(s / 60)} min ago`;
    if (s < 86400) return `read ${Math.round(s / 3600)} h ago`;
    return `read ${Math.round(s / 86400)} d ago`;
}

// The panel. el: where to draw. opts.calendar: the calendar to open on.
// opts.onChange: called after anything that changes what the Calendar page shows.
// opts.focus: put the keyboard on the panel once it has loaded (a dialog).
// Returns { destroy }.
export function mountAccounts(el, opts = {}) {
    injectStyles();
    const uid = `ca${++mounts}`;
    let calendar = opts.calendar || 'default';
    let state = null;
    let form = null;            // null | {kind:'pick'} | {kind:'caldav', preset, edit} | {kind:'ics'}
    let dead = false;
    let timer = null;
    let wait = POLL_MS;
    let working = 0;
    el.innerHTML = `<div class="ca-root"><div class="ca-top"></div>
        <div class="ca-list"><div class="ca-muted">Loading the accounts...</div></div><div class="ca-formbox"></div></div>`;
    const root = el.querySelector('.ca-root');
    const top = root.querySelector('.ca-top');
    const list = root.querySelector('.ca-list');
    const formBox = root.querySelector('.ca-formbox');

    const send = (path, method = 'GET', data) => fetchWithTimeout(`${API}/${path}`, {
        method, headers: { 'Content-Type': 'application/json' }, body: data ? JSON.stringify(data) : undefined,
    }, 60000);
    const view = (fresh, of = calendar) => send(`accounts?calendar=${enc(of)}${fresh ? '&fresh=1' : ''}`);
    // An answer is only taken for the calendar now shown: a late one for another is dropped.
    const take = res => { if (res?.accounts && res.calendar === calendar) { state = res; return true; } return false; };

    function busy(on) {
        working += on ? 1 : -1;
        const now = working > 0;
        root.classList.toggle('busy', now);
        root.inert = now;                                   // no clicks and no keys while a change is on its way
        root.setAttribute('aria-busy', now ? 'true' : 'false');
    }

    function formError(words) {
        const box = formBox.querySelector('.ca-form-error');
        if (!box) return false;
        box.textContent = words;
        box.hidden = !words;
        return true;
    }

    // One change the person asked for: the panel waits, then shows what the server now holds.
    async function run(work, okWords) {
        if (dead) return false;
        const formBefore = form;
        busy(true);
        formError('');
        try {
            const res = await work();
            if (dead) return true;
            take(res);
            if (okWords) showToast(okWords, 'success');
            paintTop();
            paintList();
            if (!form || form !== formBefore) paintForm();  // a form still being filled in is left as it is
            opts.onChange?.();
            return true;
        } catch (e) {
            if (dead) return false;
            const words = e.message || "That didn't work.";
            if (!state) {
                list.innerHTML = `<div class="ca-error">${esc(words)}</div><div class="ca-foot"><button class="btn ca-retry">Try again</button></div>`;
            } else if (!(form && formError(words))) {       // in a form the words stay beside what was typed
                showToast(words, 'error');
                try { take(await view()); } catch { /* keep what is shown */ }
                if (!dead) paintList();                     // a tick or a choice that did not happen goes back
            }
            return false;
        } finally {
            if (!dead) busy(false);
            schedule();
        }
    }
    // then(): run a change, then read the whole view again
    const then = async call => { await call; return view(); };

    // -- the list: asked for again while something is being read --------------------

    function schedule() {
        clearTimeout(timer);
        if (!dead && state?.refreshing) timer = setTimeout(poll, wait);
    }

    async function poll() {
        if (dead || working) return schedule();
        try {
            const got = take(await view());
            wait = POLL_MS;
            // Not under the person's hands: a field or a menu of the list that is open stays as it is.
            if (got && !dead && !working && !list.contains(document.activeElement) && !list.querySelector('details[open]')) paintList();
        } catch {
            wait = Math.min(wait * 2, POLL_MAX_MS);         // the server is busy or away: ask less often
            if (!dead) timer = setTimeout(poll, wait);
            return;
        }
        schedule();
    }

    function calRow(acct, c) {
        const l = c.link;
        const bits = [c.writable ? '' : 'read-only', l ? (l.refreshing ? 'reading...' : ago(l.synced_at)) : ''].filter(Boolean).join(' - ');
        return `<div class="ca-row">
            <label><input type="checkbox" class="ca-tick" data-acct="${esc(acct.id)}" data-key="${esc(c.key)}" data-link="${esc(l?.id || '')}"${l ? ' checked' : ''}>
                <span class="ca-dot${l ? ' c' + (l.color % 6) : ''}"></span><span>${esc(c.name)}</span></label>
            <span class="ca-grow">${bits ? `<span class="ca-muted">${esc(bits)}</span>` : ''}
                ${l?.error ? `<div class="ca-error">${esc(l.error)}</div>` : ''}${l?.note ? `<div class="ca-muted">${esc(l.note)}</div>` : ''}</span>
        </div>`;
    }

    function acctBox(a) {
        const google = a.provider === 'google';
        const sub = google ? (a.connected ? `connected for "${esc(calendar)}"` : 'not connected')
            : [a.detail, a.host].filter(Boolean).map(esc).join(' - ') + (a.email_account ? ` (login of the email account "${esc(a.email_account)}")` : '');
        const buttons = google
            ? `<button class="btn ca-gconnect" title="Sign in at Google again">${a.connected ? 'Reconnect' : 'Connect'}</button>${a.connected ? '<button class="btn hover-danger ca-gdisconnect" title="Sign this calendar out of Google. Its Google calendars stay listed until you untick them.">Disconnect</button>' : ''}`
            : `<button class="btn ca-edit" data-acct="${esc(a.id)}">Edit</button><button class="btn hover-danger ca-remove" data-acct="${esc(a.id)}" title="Take this account and its calendars off this page">Remove</button>`;
        return `<div class="ca-box">
            <div class="ca-head"><span class="ca-icon">${esc(ICONS[a.preset] || ICONS.caldav)}</span>
                <div class="ca-grow"><div class="ca-name">${esc(a.label)}</div><div class="ca-muted">${sub}</div></div>
                <div class="ca-btns">${buttons}</div></div>
            ${a.error ? `<div class="ca-error">${esc(a.error)}</div>` : ''}
            ${a.calendars.map(c => calRow(a, c)).join('') || (a.error ? '' : '<div class="ca-muted">This account has no calendars.</div>')}
            ${google ? ownClient(a.connected) : ''}
        </div>`;
    }

    function ownClient(connected) {
        const cid = state.google_client?.client_id || '';
        return `<details class="ca-adv"><summary>Use your own Google client (advanced)</summary>
            <div class="ca-muted">Leave this alone for Easy Connect. With your own OAuth client, add this redirect URI to it: <code>${esc(location.origin)}/api/plugin/calendar/callback</code>${connected ? ' After saving, press Reconnect: the connection made before belongs to the other client.' : ''}</div>
            <label class="ca-field"><span>Client ID</span><input type="text" class="ca-g-cid" autocomplete="off" value="${esc(cid)}"></label>
            <label class="ca-field"><span>Client secret</span><input type="password" class="ca-g-secret" autocomplete="new-password" placeholder="Leave blank to keep the saved one"></label>
            <div class="ca-foot" style="margin-top:8px"><button class="btn ca-gsave">Save client</button></div>
        </details>`;
    }

    function linkRow(l) {
        return `<div class="ca-row"><span class="ca-dot c${l.color % 6}"></span>
            <span class="ca-grow"><span>${esc(l.name)}</span> <span class="ca-muted">${esc([l.where, 'read-only', l.refreshing ? 'reading...' : ago(l.synced_at)].filter(Boolean).join(' - '))}</span>
                ${l.error ? `<div class="ca-error">${esc(l.error)}</div>` : ''}${l.note ? `<div class="ca-muted">${esc(l.note)}</div>` : ''}</span>
            <button class="btn hover-danger ca-unlink" data-link="${esc(l.id)}" data-name="${esc(l.name)}">Remove</button></div>`;
    }

    const nothing = () => !state.accounts.length && !state.addresses.length && !state.loose.length;

    function paintList() {
        if (dead || !state) return;
        list.innerHTML = `
            <div class="ca-muted">Events always live on this machine. An account adds its calendars beside them: tick the ones to show. Unticking a calendar or removing an account only takes it off this page; nothing is deleted at the provider.</div>
            ${nothing() ? '<div class="ca-box ca-muted">No accounts yet. This calendar works on its own; add an account to see and change a Google, iCloud, Fastmail, Nextcloud or other calendar here.</div>' : ''}
            ${state.accounts.map(acctBox).join('')}
            ${state.addresses.length ? `<div class="ca-box"><div class="ca-head"><span class="ca-icon">↗</span><div class="ca-name">Calendar addresses</div></div>${state.addresses.map(linkRow).join('')}</div>` : ''}
            ${state.loose.length ? `<div class="ca-box"><div class="ca-name">Linked, but their account is gone</div>${state.loose.map(linkRow).join('')}</div>` : ''}
            ${state.targets.length ? `<div class="ca-line"><label for="${uid}-target">New events go to</label>
                <select id="${uid}-target" class="ca-target"><option value="local"${state.target ? '' : ' selected'}>This machine only</option>
                    ${state.targets.map(l => `<option value="${esc(l.id)}"${l.id === state.target ? ' selected' : ''}>${esc(l.name)}</option>`).join('')}</select>
                <span class="ca-muted">Also where your AI's new events go in chats that use this calendar.</span></div>` : ''}`;
    }

    // -- the form: only ever painted by the person's own steps -----------------------

    function pickForm() {
        const g = state.accounts.find(a => a.provider === 'google');
        return `<div class="ca-box"><div class="ca-name">Add an account to "${esc(calendar)}"</div>
            <div class="ca-tiles">
                <button class="ca-tile ca-pick" data-kind="google"${g?.connected ? ' disabled' : ''}><span class="ca-icon">G</span><span>Google${g?.connected ? ' (connected)' : ''}</span></button>
                ${state.presets.map(p => `<button class="ca-tile ca-pick" data-kind="caldav" data-preset="${esc(p.id)}"><span class="ca-icon">${esc(ICONS[p.id] || ICONS.caldav)}</span><span>${esc(p.label)}</span></button>`).join('')}
                <button class="ca-tile ca-pick" data-kind="ics"><span class="ca-icon">↗</span><span>A calendar address (.ics)</span></button>
            </div>
            <div class="ca-muted" style="margin-top:8px">Google signs in on Google's own page and comes back to the Calendar page. Google may warn that the app is not verified: choose Advanced, then Continue. The others take a login here; most providers want an app password, not your normal one. Microsoft (Outlook) is not here yet: its published calendar address works as a calendar address, read-only.</div>
            ${g ? '' : ownClient(false)}
            <div class="ca-error ca-form-error" role="alert" hidden></div>
            <div class="ca-foot" style="margin-top:8px"><button class="btn ca-cancel">Cancel</button></div></div>`;
    }

    function caldavForm() {
        const p = state.presets.find(x => x.id === form.preset) || state.presets[state.presets.length - 1];
        const a = form.edit || null;
        const mails = state.email_logins || [];
        return `<div class="ca-box"><div class="ca-name">${a ? `Edit "${esc(a.label)}"` : `Add ${esc(p.label)} to "${esc(calendar)}"`}</div>
            <div class="ca-muted">${esc(p.help)}</div>
            ${p.ask_server ? `<label class="ca-field"><span>Server address</span><input type="text" class="ca-f-server" inputmode="url" autocomplete="off" spellcheck="false" placeholder="https://cloud.example.com" value="${esc(a?.server || '')}"></label>` : ''}
            <label class="ca-field"><span>${esc(p.user_label)}</span><input type="text" class="ca-f-user" autocomplete="off" spellcheck="false" value="${esc(a?.username || '')}"></label>
            ${mails.length ? `<label class="ca-field"><span>Password from</span><select class="ca-f-mail">
                <option value="">a password typed here</option>
                ${mails.map(m => `<option value="${esc(m.scope)}"${a?.email_account === m.scope ? ' selected' : ''}>the email account "${esc(m.scope)}" (${esc(m.address)})</option>`).join('')}
            </select></label><div class="ca-muted">iCloud, Fastmail and Yahoo use one app password for mail and calendar. Borrowing it keeps one copy: change it under Email and both follow.</div>` : ''}
            <label class="ca-field ca-f-pass-field"><span>Password (an app password where the provider asks for one)</span><input type="password" class="ca-f-pass" autocomplete="new-password" placeholder="${a && !a.email_account ? 'Leave blank to keep the saved one' : ''}"></label>
            ${a ? `<label class="ca-field"><span>Name shown here</span><input type="text" class="ca-f-label" maxlength="40" value="${esc(a.label)}"></label>` : ''}
            <div class="ca-error ca-form-error" role="alert" hidden></div>
            <div class="ca-foot" style="margin-top:10px"><button class="btn btn-primary ca-save ca-primary">${a ? 'Test and save' : 'Test and add'}</button><button class="btn ca-cancel">Cancel</button>
                <span class="ca-muted">The login is tried first. Nothing is kept if the server refuses it.</span></div></div>`;
    }

    function icsForm() {
        return `<div class="ca-box"><div class="ca-name">Add a calendar address to "${esc(calendar)}"</div>
            <div class="ca-muted">An .ics or webcal address: holidays, a team calendar, the "secret address in iCal format" another app gives. Read-only.</div>
            <label class="ca-field"><span>Address</span><input type="text" class="ca-f-url" inputmode="url" placeholder="https://..." autocomplete="off" spellcheck="false"></label>
            <label class="ca-field"><span>Name (optional)</span><input type="text" class="ca-f-name" maxlength="40"></label>
            <div class="ca-error ca-form-error" role="alert" hidden></div>
            <div class="ca-foot" style="margin-top:10px"><button class="btn btn-primary ca-addics ca-primary">Add</button><button class="btn ca-cancel">Cancel</button></div></div>`;
    }

    function paintForm() {
        if (dead || !state) return;
        formBox.innerHTML = form ? (form.kind === 'pick' ? pickForm() : form.kind === 'ics' ? icsForm() : caldavForm())
            : `<div class="ca-foot"><button class="btn btn-primary ca-add">+ Add account</button>
                ${nothing() ? '' : '<button class="btn ca-sync" title="Read every linked calendar again now">Refresh now</button>'}</div>`;
        syncPassField();
        (formBox.querySelector('.ca-f-server') || formBox.querySelector('.ca-f-user') || formBox.querySelector('.ca-f-url'))?.focus();
    }

    function paintTop() {
        if (dead || !state) return;
        top.innerHTML = state.calendars.length > 1 ? `<div class="ca-line"><label for="${uid}-cal">Calendar</label>
            <select id="${uid}-cal" class="ca-cal">${state.calendars.map(n => `<option value="${esc(n)}"${n === calendar ? ' selected' : ''}>${esc(n)}</option>`).join('')}</select>
            <span class="ca-muted">Each calendar has its own accounts. A chat picks its calendar in the chat's settings.</span></div>` : '';
    }

    function paint() { paintTop(); paintList(); paintForm(); }

    function syncPassField() {
        const pick = formBox.querySelector('.ca-f-mail');
        const field = formBox.querySelector('.ca-f-pass-field');
        if (!pick || !field) return;
        field.hidden = !!pick.value;
        const user = formBox.querySelector('.ca-f-user');
        const mail = (state.email_logins || []).find(m => m.scope === pick.value);
        if (mail && user && !user.value.trim()) user.value = mail.address;
    }

    const val = sel => (formBox.querySelector(sel)?.value || '').trim();

    async function switchTo(name) {
        const before = calendar;
        calendar = name;
        form = null;
        busy(true);
        try {
            const res = await view(false, name);
            if (dead || calendar !== name) return;
            state = res;
        } catch (e) {
            if (dead) return;
            calendar = before;                                // still showing the old one: say so, and say why
            showToast(e.message || "That calendar's accounts didn't load.", 'error');
        } finally {
            if (!dead) { busy(false); paint(); schedule(); }
        }
    }

    root.addEventListener('change', e => {
        const t = e.target;
        if (t.classList.contains('ca-cal')) switchTo(t.value);
        else if (t.classList.contains('ca-target')) run(() => then(send('target', 'POST', { calendar, to: t.value })));
        else if (t.classList.contains('ca-f-mail')) syncPassField();
        else if (t.classList.contains('ca-tick')) {
            const { acct, key, link } = t.dataset;
            if (!t.checked) return run(() => then(send(`links/${enc(link)}`, 'DELETE')));
            const body = acct === 'google' ? { calendar, kind: 'google', google_id: key } : { calendar, kind: 'caldav', account: acct, key };
            run(() => then(send('links', 'POST', body)));
        }
    });

    root.addEventListener('keydown', e => {
        if (!form || !formBox.contains(e.target)) return;
        if (e.key === 'Escape') { e.stopPropagation(); form = null; paintForm(); }      // leave the form, not a dialog around it
        else if (e.key === 'Enter' && e.target.matches('input')) {
            const main = e.target.closest('details')?.querySelector('.ca-gsave') || formBox.querySelector('.ca-primary');
            if (main) { e.preventDefault(); main.click(); }
        }
    });

    root.addEventListener('click', e => {
        const hit = sel => e.target.closest(sel);
        if (hit('.ca-retry')) return run(() => view());
        if (!state) return;
        if (hit('.ca-add')) { form = { kind: 'pick' }; return paintForm(); }
        if (hit('.ca-cancel')) { form = null; return paintForm(); }
        if (hit('.ca-pick')) {
            const b = hit('.ca-pick');
            if (b.dataset.kind === 'google') { window.location.href = `${API}/auth?scope=${enc(calendar)}`; return; }
            form = b.dataset.kind === 'ics' ? { kind: 'ics' } : { kind: 'caldav', preset: b.dataset.preset };
            return paintForm();
        }
        if (hit('.ca-edit')) {
            const a = state.accounts.find(x => x.id === hit('.ca-edit').dataset.acct);
            if (a) { form = { kind: 'caldav', preset: a.preset, edit: a }; paintForm(); }
            return;
        }
        if (hit('.ca-save')) {
            const a = form?.edit || null;
            const body = { calendar, preset: form.preset, server: val('.ca-f-server'), username: val('.ca-f-user'),
                           password: formBox.querySelector('.ca-f-pass')?.value || '', email_account: val('.ca-f-mail'), label: val('.ca-f-label') };
            if (body.email_account) body.password = '';
            return run(async () => {
                const res = await (a ? send(`accounts/${enc(a.id)}`, 'PUT', body) : send('accounts', 'POST', body));
                form = null;                                  // only once the server took it: a refusal keeps what was typed
                return res;
            }, a ? 'Saved. The login works.' : 'Account added. Its calendars are loading.');
        }
        if (hit('.ca-remove')) {
            const a = state.accounts.find(x => x.id === hit('.ca-remove').dataset.acct);
            if (!a) return;
            const n = a.calendars.filter(c => c.link).length;
            return showConfirm(`Remove the account "${a.label}" from "${calendar}"? Its ${n} linked calendar${n === 1 ? '' : 's'} leave${n === 1 ? 's' : ''} this page. Nothing is deleted at the provider.`,
                () => run(() => send(`accounts/${enc(a.id)}?calendar=${enc(calendar)}`, 'DELETE')), { title: 'Remove account', saveLabel: 'Remove' });
        }
        if (hit('.ca-unlink')) {
            const b = hit('.ca-unlink');
            return showConfirm(`Remove "${b.dataset.name}" from "${calendar}"? Its events leave this page. Nothing is deleted where that calendar is kept.`,
                () => run(() => then(send(`links/${enc(b.dataset.link)}`, 'DELETE'))), { title: 'Remove calendar', saveLabel: 'Remove' });
        }
        if (hit('.ca-addics')) {
            const url = val('.ca-f-url');
            if (!url) return formError('Paste the calendar address first.');
            return run(async () => { const res = await then(send('links', 'POST', { calendar, kind: 'ics', url, name: val('.ca-f-name') })); form = null; return res; }, 'Calendar linked.');
        }
        if (hit('.ca-gconnect')) { window.location.href = `${API}/auth?scope=${enc(calendar)}`; return; }      // Google's page takes over this tab
        if (hit('.ca-gdisconnect')) {
            return showConfirm(`Disconnect Google from "${calendar}"? Its calendars stay listed but stop refreshing until you connect again. Untick them to take them off this page.`,
                () => run(() => then(send('disconnect', 'POST', { scope: calendar }))), { title: 'Disconnect Google', saveLabel: 'Disconnect' });
        }
        if (hit('.ca-gsave')) {
            const box = hit('.ca-gsave').closest('details');      // the one that was filled in
            const client_id = (box.querySelector('.ca-g-cid').value || '').trim();
            const client_secret = (box.querySelector('.ca-g-secret').value || '').trim();
            const saved = state.google_client?.client_id || '';
            if (client_id && client_id !== saved && !client_secret) return showToast('A new client ID needs its client secret too.', 'error');
            const connected = state.accounts.some(a => a.provider === 'google' && a.connected);
            return run(() => then(fetchWithTimeout(`/api/gcal/accounts/${enc(calendar)}`, {
                method: 'PUT', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ client_id, client_secret, calendar_id: state.google_client?.calendar_id || 'primary', label: calendar }),
            }, 20000)), !client_id ? 'Back to Easy Connect.' : (connected ? 'Client saved. Now press Reconnect.' : 'Client saved. Now connect Google.'));
        }
        if (hit('.ca-sync')) {
            return run(async () => {
                const res = await send('sync', 'POST', { calendar });
                if (res.failed?.length) showToast(`${res.failed[0].name}: ${res.failed[0].detail}`, 'error');
                return view(true);
            });
        }
    });

    (async () => {
        try {
            const res = await view();
            if (dead) return;
            state = res;
            calendar = res.calendar;
            paint();
            if (opts.focus) formBox.querySelector('.ca-add')?.focus();
            schedule();
        } catch (e) {
            if (!dead) list.innerHTML = `<div class="ca-error">${esc(e.message || "The accounts didn't load.")}</div><div class="ca-foot"><button class="btn ca-retry">Try again</button></div>`;
        }
    })();
    return { destroy() { dead = true; clearTimeout(timer); } };
}

// -- the Settings card ---------------------------------------------------------------

let schema = [];
let cardPanel = null;           // the panel of the card as last drawn: one at a time

export default {
    name: 'calendar',
    init() {
        registerPluginSettings({
            id: 'calendar',
            name: 'Calendar',
            icon: '📆',
            helpText: 'The accounts whose calendars show beside the events kept on this machine: Google, iCloud, Fastmail, Nextcloud, Yahoo, any CalDAV server, or a calendar address. The Calendar page has the same list under its Accounts button.',
            load: async () => {
                try {
                    const d = await fetchWithTimeout(`${API}/config`, {}, 20000);
                    schema = d.schema || [];
                    return d.values || {};
                } catch {                                    // the accounts are still worth showing without the two settings
                    schema = [];
                    return {};
                }
            },
            render: (box, settings) => {
                injectStyles();
                cardPanel?.destroy();
                box.innerHTML = `<div class="ca-card"><h4>Accounts</h4>
                    <div class="ca-muted" style="margin-bottom:8px">Accounts save with their own buttons. "Save Changes" at the bottom of this page is for the settings under them.</div>
                    <div class="ca-host"></div><h4>Settings</h4><div class="ca-settings"></div></div>`;
                cardPanel = mountAccounts(box.querySelector('.ca-host'));
                renderSettingsForm(box.querySelector('.ca-settings'), schema, settings || {});
            },
            getSettings: box => readSettingsForm(box.querySelector('.ca-settings'), schema),
            save: s => (schema.length ? pluginsAPI.saveSettings('calendar', s) : Promise.resolve()),
            reset: () => pluginsAPI.resetSettings('calendar'),
        });
    },
    destroy() { cardPanel?.destroy(); cardPanel = null; },
};
