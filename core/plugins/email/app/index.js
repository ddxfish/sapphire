// The Email page: your mail by folder (inbox, sent, drafts, archive, spam,
// trash and your own folders), a reading pane, and compose / reply / forward.
// It talks to routes/mail.py; every action is one request and one visit to
// the mail server. Nothing is kept in the browser but the last account,
// folder and view you used.
//
// HTML mail is hostile input. Plain text is shown when the mail has it;
// formatted mail is drawn only inside a sandboxed frame (no script, no access
// to the app) whose own policy blocks every remote load until you ask for
// that mail's pictures.
//
// App contract: docs/plugin-author/APPS.md. All state lives inside render(),
// which returns its own teardown.
import { fetchWithTimeout } from '/static/shared/fetch.js';
import { escapeHtml, showConfirm } from '/static/shared/modal.js';
import { showToast } from '/static/shared/notify.js';
import { saveUrl } from '/static/shared/download.js';

const API = '/api/plugin/email';
const ACCOUNTS = '/api/email/accounts';
const REFRESH_MS = 60000;                      // a quiet look for new mail while the page is on screen
const COUNTS_MS = 30000;                       // folder counts are asked for apart, at most this often
const KEEP_VIEWS = 60, KEEP_MAILS = 40;        // how much of what you looked at stays in this tab's memory
const WAIT = 60000;
const NO_COUNT = new Set(['sent', 'trash', 'archive']);       // an unread count there says nothing
const CERT_WORDS = /certificate|could not prove|being intercepted/i;       // a server that was refused before login
const MOVED = { archive: 'Archived', trash: 'Moved to Trash', spam: 'Marked as spam', inbox: 'Moved to Inbox' };

let cssLink = null;
let cssUsers = 0;

function remember(key, value) {
    try {
        if (value === undefined) return localStorage.getItem('sapphire.email.' + key);
        localStorage.setItem('sapphire.email.' + key, value);
    } catch { /* storage blocked: the page still works */ }
    return null;
}

const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;

function when(ts) {
    if (!ts) return '';
    const d = new Date(ts * 1000), now = new Date();
    if (d.toDateString() === now.toDateString()) return d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
    if (d.getFullYear() === now.getFullYear()) return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
    return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
}

function whenLong(ts, fallback) {
    if (!ts) return fallback || '';
    return new Date(ts * 1000).toLocaleString(undefined,
        { weekday: 'short', year: 'numeric', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
}

function sizeWords(n) {
    if (n >= 1048576) return `${(n / 1048576).toFixed(1)} MB`;
    return `${Math.max(1, Math.round(n / 1024))} KB`;
}

const names = list => (list || []).map(p => p.name || p.addr).join(', ');

// One person as it goes into an address box: 'Ann Lee <ann@a.org>' or the bare address.
function addrText(p) {
    const name = (p.name || '').trim();
    if (!name || name === p.addr || name === p.addr.split('@')[0]) return p.addr;
    return /[,;<>"@()]/.test(name) ? `"${name.replace(/"/g, '')}" <${p.addr}>` : `${name} <${p.addr}>`;
}
const addrLine = list => (list || []).map(addrText).join(', ');

function prefixed(subject, prefix) {
    const s = (subject || '').trim();
    return s.toLowerCase().startsWith(prefix.toLowerCase() + ':') ? s : `${prefix}: ${s}`;
}

// The whole document of the sandboxed frame. Its policy is the wall: nothing
// loads but what is already inside the mail, plus https pictures when asked.
// (No <base> here: the frame inherits the app's policy, which forbids one. The
// server strips relative addresses and marks every link to open in a new tab.)
function frameDoc(html, pictures) {
    const csp = `default-src 'none'; style-src 'unsafe-inline'; img-src data:${pictures ? ' https:' : ''}`;
    return '<!doctype html><html><head><meta charset="utf-8">'
        + `<meta http-equiv="Content-Security-Policy" content="${csp}">`
        + '<meta name="referrer" content="no-referrer">'
        + '<style>html{background:#fff}body{margin:12px;color:#111;font:14px/1.45 system-ui,sans-serif;'
        + 'overflow-wrap:anywhere}img{max-width:100%;height:auto}table{max-width:100%}</style>'
        + `</head><body>${html}</body></html>`;
}

// -- the page -----------------------------------------------------------------

export function render(root) {
    if (!cssLink) {
        cssLink = document.createElement('link');
        cssLink.rel = 'stylesheet';
        const v = document.querySelector('meta[name="boot-version"]')?.content || '';
        cssLink.href = `/plugin-web/email/app/email.css?v=${v}`;
        document.head.appendChild(cssLink);
    }
    cssUsers++;

    const st = {
        accounts: [], scope: '', address: '',
        folder: 'inbox', folders: [], rows: [], total: 0, page: 1, pages: 1, perPage: 50, validity: 0,
        q: '', unreadOnly: false,
        where: remember('where') === 'here' ? 'here' : 'all',       // a search looks in all mail unless told otherwise
        everywhere: false,                                          // the list on screen is from several folders
        sel: new Set(),
        openId: '', open: null, view: 'text', pictures: false, frameBlocked: false,
        seq: 0, mseq: 0, loaded: false, lastLoad: 0, busy: 0, dead: false,
        modals: new Set(),
        // What was last seen, kept in this tab's memory only (never on disk): a
        // folder you come back to paints at once and is then checked for changes.
        views: new Map(),              // account + folder + page + search -> the list as last loaded
        shown: '',                     // which of those is on screen
        mails: new Map(),              // account + id -> an opened mail
        folderLists: new Map(),        // account -> its folders with their counts
        countsAt: new Map(), countsDirty: false, countsBusy: '', countsAgain: false,
    };
    const keep = (map, key, value, limit) => {
        map.delete(key);
        map.set(key, value);
        while (map.size > limit) map.delete(map.keys().next().value);
    };

    root.innerHTML = `
        <div class="em-root" data-pane="list">
            <div class="em-bar">
                <button class="btn btn-primary em-compose">Compose</button>
                <select class="em-account" title="Which account" aria-label="Account" hidden></select>
                <input type="search" class="em-search" placeholder="Search mail: words, from:ann, to:, subject:, is:unread, has:attachment" aria-label="Search mail"
                    title='Words, "an exact phrase", from:  to:  cc:  subject:  body:  is:unread  is:starred  has:attachment  after:2026-11-01  before:  on:  newer_than:7d  in:sent  -word to leave out. Press Enter.'>
                <select class="em-where" title="Where the search looks" aria-label="Where the search looks">
                    <option value="all">All mail</option><option value="here">This folder</option>
                </select>
                <label class="checkbox-wrap em-unread-wrap"><input type="checkbox" class="em-unread"><span>Unread only</span></label>
                <button class="btn em-refresh" title="Check for new mail">Refresh</button>
                <button class="btn em-accounts" title="Add or change email accounts (Settings, Plugins, Email)">Accounts</button>
                <span class="em-status" role="status"></span>
            </div>
            <div class="em-main">
                <nav class="em-folders" aria-label="Folders"></nav>
                <section class="em-list">
                    <div class="em-list-bar"></div>
                    <div class="em-rows"><div class="em-empty">Loading your mail...</div></div>
                </section>
                <section class="em-read"><div class="em-empty">Pick an email to read it here.</div></section>
            </div>
        </div>`;
    const $ = sel => root.querySelector(sel);
    const rootEl = $('.em-root'), foldersEl = $('.em-folders'), barEl = $('.em-list-bar');
    const rowsEl = $('.em-rows'), readEl = $('.em-read'), statusEl = $('.em-status');

    // -- talking to the server ---------------------------------------------------

    function busy(on) {
        st.busy = Math.max(0, st.busy + (on ? 1 : -1));
        statusEl.textContent = st.busy ? 'Working...' : '';
        $('.em-refresh').disabled = st.busy > 0;
    }

    const post = (path, body) => { st.countsDirty = true; return fetchWithTimeout(`${API}/${path}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ scope: st.scope, ...body }),
    }, WAIT); };

    // An id the server no longer takes (the folder was rebuilt there): what is
    // on screen is stale, so list again.
    function stale(e) {
        if (!/not for this mailbox as it is now/i.test(e?.message || '')) return false;
        showToast('That list was out of date (the folder changed on the mail server). Here is the fresh one.', 'info');
        closeReader();
        st.sel.clear();
        load();
        return true;
    }

    async function loadAccounts() {
        let data;
        try {
            data = await fetchWithTimeout(ACCOUNTS, {}, 20000);
        } catch (e) {
            if (st.dead) return;
            rowsEl.innerHTML = `<div class="em-empty em-error">${escapeHtml(e.message || "Couldn't load your email accounts.")}
                <div><button class="btn em-retry">Try again</button></div></div>`;
            return;
        }
        if (st.dead) return;
        st.accounts = (data?.accounts || []).filter(a => a.address);
        if (!st.accounts.length) {
            foldersEl.innerHTML = '';
            barEl.innerHTML = '';
            rowsEl.innerHTML = `<div class="em-empty">No email account is set up yet.<br>
                Add one in <a href="#settings" class="em-to-settings">Settings</a>, under Plugins, Email. Then come back here.</div>`;
            // Settings opens on the Email card (the same switch a notification's "go" uses).
            rowsEl.querySelector('.em-to-settings')?.addEventListener('click', () => { window._settingsTab = 'email'; });
            readEl.innerHTML = '';
            root.querySelectorAll('.em-compose, .em-search, .em-unread, .em-refresh').forEach(el => { el.disabled = true; });
            return;
        }
        const saved = remember('account');
        st.scope = (st.accounts.find(a => a.scope === saved) || st.accounts[0]).scope;
        const sel = $('.em-account');
        sel.innerHTML = st.accounts.map(a =>
            `<option value="${escapeHtml(a.scope)}">${escapeHtml(a.address)}</option>`).join('');
        sel.value = st.scope;
        sel.hidden = st.accounts.length < 2;
        const folder = remember('folder.' + st.scope);
        if (folder) st.folder = folder;
        load();
    }

    const viewKey = () => [st.scope, st.folder, st.page, st.q, st.unreadOnly ? 1 : 0, st.q ? st.where : ''].join('\n');

    // Put one loaded list on screen. The folder counts are not part of it:
    // they are kept per account and laid over the folder names.
    function show(data, key) {
        const sameView = st.shown === key;
        st.shown = key;
        st.everywhere = !!data.everywhere;
        if (!data.q) st.folder = data.folder;               // a search (in:sent) shows results, it does not move you
        st.address = data.account?.address || '';
        st.rows = data.rows || [];
        st.total = data.total || 0;
        st.page = data.page || 1;
        st.pages = data.pages || 1;
        st.perPage = data.per_page || 50;
        st.validity = data.validity || 0;
        const old = new Map((st.folderLists.get(st.scope) || []).map(f => [f.key, f]));
        st.folders = (data.folders || []).map(f => ({ ...f, total: old.get(f.key)?.total ?? f.total ?? null,
            unread: old.get(f.key)?.unread ?? f.unread ?? null }));
        st.folderLists.set(st.scope, st.folders);
        const here = new Set(st.rows.map(r => r.id));
        st.sel = new Set([...st.sel].filter(id => here.has(id)));
        paintFolders();
        paintList(sameView);
    }

    // The folder counts, on their own request (one question per folder to the
    // mail server, the slow part). It never holds up a click on the list.
    async function loadCounts(force = false) {
        const scope = st.scope;
        if (st.dead || !scope) return;
        if (!force && !st.countsDirty && Date.now() - (st.countsAt.get(scope) || 0) < COUNTS_MS) return;
        if (st.countsBusy) { st.countsAgain = true; return; }
        st.countsBusy = scope;
        st.countsDirty = false;
        let data = null;
        try {
            data = await fetchWithTimeout(`${API}/counts?scope=${encodeURIComponent(scope)}`, {}, WAIT);
        } catch { /* the badges keep what they had; the next load asks again */ }
        st.countsBusy = '';
        if (st.dead) return;
        if (data?.folders) {
            st.countsAt.set(scope, Date.now());
            st.folderLists.set(scope, data.folders);
            if (st.scope === scope) {
                st.folders = data.folders;
                paintFolders();
                paintListBar();                            // "Move to..." lists the folders too
                if (st.rows.length) paintList();           // rows carry folder names; an empty list is not ours to judge here
            }
        }
        if (st.countsAgain) { st.countsAgain = false; loadCounts(st.scope !== scope); }
    }

    async function load({ quiet = false, counts = true } = {}) {
        if (st.dead || !st.scope) return;
        const mine = ++st.seq;
        st.lastTry = Date.now();
        const key = viewKey();
        const seen = st.views.get(key);
        if (seen && st.shown !== key) show(seen, key);      // what was here last time, at once; then look again
        const p = new URLSearchParams({ scope: st.scope, folder: st.folder, page: String(st.page), counts: '0' });
        if (st.q) p.set('q', st.q);
        if (st.q && st.where === 'all') p.set('all', '1');
        if (st.unreadOnly) p.set('unread', '1');
        // Not seen in this tab yet (a fresh page, a reload, a restart): the server
        // side keeps each folder as it was last listed, in a file on this machine.
        // That answers in a few milliseconds; the real answer follows and replaces it.
        let live = false;
        if (!seen && !st.q && !st.unreadOnly) {
            fetchWithTimeout(`${API}/folder?${p}&cached=1`, {}, 8000).then(old => {
                if (st.dead || live || mine !== st.seq || !old || old.miss) return;
                if (old.folders?.length && !(st.folderLists.get(st.scope) || []).length) st.folderLists.set(st.scope, old.folders);
                show(old, '');                              // '' = not the real thing yet: the live answer repaints
                if (!quiet) statusEl.textContent = 'Checking for new mail...';
            }).catch(() => { /* no kept copy: the list shows when the server answers */ });
        }
        if (!quiet) busy(true);
        let data;
        try {
            data = await fetchWithTimeout(`${API}/folder?${p}`, {}, WAIT);
        } catch (e) {
            if (!quiet) busy(false);
            if (st.dead || mine !== st.seq) return;
            const hadCopy = st.shown === '' && st.rows.length > 0 && !seen;      // the kept copy is on screen
            // A folder that was renamed or removed elsewhere: fall back to the inbox once.
            // Not for a search: "in:nosuch" is a mistake in the words, not a reason to leave the folder.
            if (!st.q && st.folder !== 'inbox' && /no folder named|has no .* folder/i.test(e.message || '')) {
                st.folder = 'inbox';
                st.page = 1;
                remember('folder.' + st.scope, 'inbox');
                return load({ quiet, counts });
            }
            const certy = CERT_WORDS.test(e.message || '');
            if (/not configured|no mail server set/i.test(e.message || '') && st.accounts.some(a => a.scope === st.scope)) {
                return loadAccounts();                       // the account was removed or emptied in another tab
            }
            if (certy) {
                // Nothing is read from a server that cannot prove itself, so the list last seen is
                // not shown as if it were current: the person gets the certificate and the choice.
                // Once, not at every quiet refresh.
                statusEl.textContent = '';
                if (st.certFor === st.scope && quiet) return;
                st.certFor = st.scope;
                live = true;
                st.rows = [];
                st.total = 0;
                st.pages = 1;
                st.shown = '';
                paintListBar();
                rowsEl.innerHTML = `<div class="em-empty em-error">${escapeHtml(e.message)}
                    <div><button class="btn em-retry">Try again</button></div></div>`;
                certPanel(mine, rowsEl);
            } else if (hadCopy) {
                // Offline or the server said no: what was last seen stays up, and says so.
                statusEl.textContent = '';
                if (!quiet) showToast(`${e.message || "Couldn't reach the mail server."} Showing the list as it was last seen.`, 'error');
            } else if (!quiet || !st.loaded) {
                live = true;
                st.rows = [];
                st.total = 0;
                st.pages = 1;
                st.shown = '';
                paintListBar();
                rowsEl.innerHTML = `<div class="em-empty em-error">${escapeHtml(e.message || "Couldn't load your mail.")}
                    <div><button class="btn em-retry">Try again</button></div></div>`;
            }
            return;
        }
        live = true;
        st.certFor = '';
        if (!quiet) busy(false);
        if (st.dead || !data) return;
        keep(st.views, key, data, KEEP_VIEWS);              // kept even if you already clicked on
        if (mine !== st.seq) return;
        st.loaded = true;
        st.lastLoad = Date.now();
        show(data, key);
        remember('folder.' + st.scope, st.folder);
        if (counts || st.countsDirty) loadCounts();
    }

    // -- a mail server whose certificate cannot be verified ----------------------------
    // The server side refused it before any password was sent. Here the person
    // sees what the server showed and may trust exactly that certificate.

    const fingerprint = fp => (String(fp || '').toUpperCase().match(/.{1,2}/g) || []).join(':');

    // `into`: where to draw it (the list, or a box inside the compose dialog). `after`: what to do once trusted.
    async function certPanel(mine, into, after) {
        let data;
        try {
            data = await fetchWithTimeout(`${API}/certs?scope=${encodeURIComponent(st.scope)}`, {}, 30000);
        } catch { return; }
        if (st.dead || mine !== st.seq || !into.isConnected) return;
        const certs = (data?.servers || []).filter(c => (c.state === 'untrusted' || c.state === 'changed') && c.fingerprint);
        if (!certs.length) return;
        // A big provider's certificate always verifies: one that does not is somebody in between, never a thing to trust.
        const known = certs.some(c => c.known);
        const cards = certs.map(c => `
            <div class="em-cert-card">
                <div><b>${escapeHtml(c.host)}</b>, port ${c.port} (${c.which === 'smtp' ? 'sending' : 'reading mail'})</div>
                <div>Why it was refused: ${escapeHtml(c.reason || 'it could not be verified')}${c.state === 'changed' ? '. It is NOT the certificate you trusted before' : ''}</div>
                ${c.subject ? `<div>It says it is for: ${escapeHtml(c.subject)}${c.self_signed ? ' (signed by itself)' : ''}</div>` : ''}
                ${c.expires ? `<div>It says it is good until: ${escapeHtml(c.expires)}</div>` : ''}
                <div>Fingerprint (SHA-256): <code>${escapeHtml(fingerprint(c.fingerprint))}</code></div>
            </div>`).join('');
        into.innerHTML = `<div class="em-empty em-cert">
            <div class="em-error">${known
                ? 'Something between this machine and your mail provider is answering in its place, so nothing was sent to it, your password included.'
                : 'This mail server could not prove who it is, so nothing was sent to it, your password included.'}</div>
            ${cards}
            <div>${known
                ? 'This provider always has a certificate that checks out, so this one cannot be trusted. It may be security software on this machine ("scan encrypted connections"), a company or hotel network, or an attack. Try another network.'
                : 'What a certificate says about itself proves nothing: anyone between you and the server could show one like this. Trust it only if this is your own server, or its admin gave you this fingerprint.'}</div>
            <div>${known ? '' : `<button class="btn em-trust">Trust ${certs.length > 1 ? 'these certificates' : 'this certificate'}</button>`}
                ${into === rowsEl ? '<button class="btn em-retry">Try again</button>' : ''}</div></div>`;
        into.querySelector('.em-trust')?.addEventListener('click', e => { e.stopPropagation(); trustCerts(certs, after); });
    }

    function trustCerts(certs, after) {
        if (!certs?.length) return;
        const hosts = certs.map(c => c.host).filter((h, i, all) => all.indexOf(h) === i).join(' and ');
        showConfirm(`Trust ${hosts}? Sapphire will send this account's password to a server showing this certificate, and only this one.`,
            async () => {
                try {
                    for (const c of certs) {
                        await post('trust', { host: c.host, port: c.port, which: c.which, fingerprint: c.fingerprint });
                    }
                    st.certFor = '';
                    if (after) return after();
                    showToast('Trusted. Loading your mail...', 'success');
                    load();
                } catch (e) {
                    showToast(e.message || "Couldn't trust that certificate.", 'error');
                }
            }, { title: 'Trust this certificate', saveLabel: 'Trust' });
    }

    // -- folders -------------------------------------------------------------------

    const folderOf = key => st.folders.find(f => f.key === key);
    const hasRole = role => st.folders.some(f => f.role === role);

    function paintFolders() {
        foldersEl.innerHTML = st.folders.map(f => {
            const n = f.key === 'drafts' ? f.total : (NO_COUNT.has(f.key) ? 0 : f.unread);
            return `<button class="em-folder${f.key === st.folder ? ' active' : ''}" data-key="${escapeHtml(f.key)}"
                title="${escapeHtml(f.label)}"><span class="em-folder-name">${escapeHtml(f.label)}</span>${
                n ? `<span class="em-count">${n}</span>` : ''}</button>`;
        }).join('');
    }

    function setFolder(key) {
        st.folder = key;
        st.page = 1;
        st.q = '';
        st.sel.clear();
        $('.em-search').value = '';
        closeReader();
        st.rows = [];
        st.total = 0;
        st.pages = 1;
        st.shown = '';
        paintListBar();
        rowsEl.innerHTML = '<div class="em-empty">Loading...</div>';
        paintFolders();
        load();
    }

    // -- the list ------------------------------------------------------------------

    // Where a mail is. In a search over all mail that is its own folder, not
    // the one the search was started from.
    const homeOf = r => (r && (r.where || r.folder)) || st.folder;
    const homesOfSelection = () => [...new Set(st.rows.filter(r => st.sel.has(r.id)).map(homeOf))];

    function moveOptions(homes = [st.folder]) {
        const here = homes.length === 1 ? homes[0] : '';
        return '<option value="">Move to...</option>' + st.folders.filter(f => f.key !== here)
            .map(f => `<option value="${escapeHtml(f.key)}">${escapeHtml(f.label)}</option>`).join('');
    }

    // The buttons that fit where the mail IS: no Archive for archived mail,
    // "Not spam" only for spam, "Restore" only for trash.
    function actionButtons(cls, homes = [st.folder]) {
        const all = key => homes.length > 0 && homes.every(h => h === key);
        const none = key => !homes.includes(key);
        const out = [];
        if (!all('archive')) out.push(`<button class="btn ${cls}" data-act="archive" title="Out of the inbox, kept in the archive">Archive</button>`);
        if (all('spam')) out.push(`<button class="btn ${cls}" data-act="inbox" title="Back to the inbox">Not spam</button>`);
        else if (hasRole('spam') && none('spam')) out.push(`<button class="btn ${cls}" data-act="spam" title="Move to the spam folder">Spam</button>`);
        if (all('trash')) out.push(`<button class="btn ${cls}" data-act="inbox" title="Back to the inbox">Restore</button>`);
        else if (hasRole('trash') && none('trash')) out.push(`<button class="btn ${cls} hover-danger" data-act="trash" title="Move to Trash (it can be moved back)">Trash</button>`);
        return out.join('');
    }

    function paintListBar() {
        const n = st.sel.size;
        const first = st.total ? (st.page - 1) * st.perPage + 1 : 0;
        const last = Math.min(st.total, st.page * st.perPage);
        const all = st.rows.length > 0 && n === st.rows.length;
        barEl.innerHTML = `
            <input type="checkbox" class="em-all" title="Select every email on this page" aria-label="Select all"${all ? ' checked' : ''}${st.rows.length ? '' : ' disabled'}>
            ${n ? `<span class="em-picked">${n} selected</span>
                <span class="em-bulk">${actionButtons('em-bulk-btn', homesOfSelection())}
                    <button class="btn em-bulk-btn" data-mark="read">Read</button>
                    <button class="btn em-bulk-btn" data-mark="unread">Unread</button>
                    <select class="em-bulk-move" aria-label="Move the selected emails">${moveOptions(homesOfSelection())}</select>
                </span>` : '<span class="em-spacer"></span>'}
            <span class="em-range">${!st.total ? '' : (st.everywhere
                ? (st.total > st.rows.length ? `newest ${st.rows.length} of ${st.total}` : `${st.total} found`)
                : `${first} to ${last} of ${st.total}`)}</span>
            <button class="btn em-prev" title="Newer" aria-label="Newer"${st.page > 1 ? '' : ' disabled'}>&#8249;</button>
            <button class="btn em-next" title="Older" aria-label="Older"${st.page < st.pages ? '' : ' disabled'}>&#8250;</button>`;
    }

    function rowHtml(r) {
        const home = r.where || r.folder || st.folder;           // a search result says which folder it is in
        const out = home === 'sent' || home === 'drafts';
        const tag = st.everywhere ? `<span class="em-tag">${escapeHtml(folderOf(home)?.label || home)}</span>` : '';
        const who = out ? `To: ${names(r.to) || '(nobody yet)'}` : (names(r.from) || 'Unknown');
        const cls = ['em-row', r.unread ? 'unread' : '', r.id === st.openId ? 'open' : '', st.sel.has(r.id) ? 'picked' : ''].join(' ');
        return `<div class="${cls}" data-id="${escapeHtml(r.id)}" tabindex="0" role="button">
            <input type="checkbox" class="em-check" aria-label="Select"${st.sel.has(r.id) ? ' checked' : ''}>
            <button class="em-star${r.starred ? ' on' : ''}" title="${r.starred ? 'Remove the star' : 'Star'}"
                aria-pressed="${r.starred ? 'true' : 'false'}">${r.starred ? '&#9733;' : '&#9734;'}</button>
            <div class="em-row-main">
                <div class="em-row-top"><span class="em-who">${escapeHtml(who)}</span><span class="em-date">${escapeHtml(when(r.ts))}</span></div>
                <div class="em-row-sub">${tag}<span class="em-subject">${escapeHtml(r.subject)}</span>${
                    r.answered ? '<span class="em-mark" title="You replied">&#8617;</span>' : ''}${
                    r.attach ? '<span class="em-mark" title="Has an attachment">&#128206;</span>' : ''}</div>
            </div>
        </div>`;
    }

    // keepScroll: the same list painted again stays where you were; another page or search starts at the top.
    function paintList(keepScroll = true) {
        paintListBar();
        if (!st.rows.length) {
            const why = st.q ? `Nothing in ${st.everywhere ? 'your mail' : 'this folder'} matches "${st.q}".`
                : (st.unreadOnly ? 'No unread mail here.' : 'This folder is empty.');
            rowsEl.innerHTML = `<div class="em-empty">${escapeHtml(why)}</div>`;
            return;
        }
        const top = keepScroll ? rowsEl.scrollTop : 0;
        rowsEl.innerHTML = st.rows.map(rowHtml).join('');
        rowsEl.scrollTop = top;
    }

    // -- changing mail ---------------------------------------------------------------

    async function moveIds(ids, to) {
        if (!ids.length || !to) return;
        busy(true);
        let res;
        try {
            res = await post('move', { ids, to });
        } catch (e) {
            busy(false);
            if (!stale(e)) showToast(e.message || "That didn't move. Try again.", 'error');
            return;
        }
        busy(false);
        if (st.dead) return;
        const moved = res.moved || [], skipped = res.skipped || [];
        const label = MOVED[to] || `Moved to ${folderOf(to)?.label || to}`;
        if (moved.length) showToast(`${label}: ${plural(moved.length, 'email')}.`, 'success');
        if (skipped.length) showToast(`${plural(skipped.length, 'email')} not moved: ${skipped[0].why}.`, 'warning');
        if (moved.includes(st.openId)) closeReader();
        moved.forEach(id => st.sel.delete(id));
        load({ quiet: true });
    }

    async function markIds(ids, mark) {
        if (!ids.length) return;
        // Show it at once; the quiet reload after puts the truth (and the counts) back.
        for (const r of st.rows) {
            if (!ids.includes(r.id)) continue;
            if (mark === 'read' || mark === 'unread') r.unread = mark === 'unread';
            else r.starred = mark === 'starred';
        }
        if (st.open && ids.includes(st.open.id)) {
            if (mark === 'starred' || mark === 'unstarred') st.open.starred = mark === 'starred';
            paintReader();                                  // its Star button says what the row says
        }
        paintList();
        try {
            const res = await post('mark', { ids, mark });
            if (res.skipped?.length) showToast(`${plural(res.skipped.length, 'email')} not changed: ${res.skipped[0].why}.`, 'warning');
        } catch (e) {
            if (!stale(e)) showToast(e.message || "That didn't save. Try again.", 'error');
        }
        if (!st.dead) load({ quiet: true });
    }

    // -- reading -----------------------------------------------------------------------

    function setPane(pane) { rootEl.dataset.pane = pane; }

    function closeReader() {
        st.mseq++;
        st.openId = '';
        st.open = null;
        setPane('list');
        readEl.innerHTML = '<div class="em-empty">Pick an email to read it here.</div>';
        rowsEl.querySelectorAll('.em-row.open').forEach(el => el.classList.remove('open'));
    }

    async function openMessage(id) {
        const mine = ++st.mseq;
        st.openId = id;
        st.open = null;
        setPane('read');
        readEl.innerHTML = `<div class="em-read-bar"><button class="btn em-back">&#8249; Back</button></div>
            <div class="em-empty">Opening...</div>`;
        rowsEl.querySelectorAll('.em-row').forEach(el => el.classList.toggle('open', el.dataset.id === id));
        const mailKey = [st.scope, id].join('\n');           // the id carries its own check code
        let m = st.mails.get(mailKey);
        if (m) {
            // Opened before in this tab: it is on screen at once. Reading it again is still told to the server.
            const now = st.rows.find(r => r.id === id);
            if (now) { m.starred = !!now.starred; m.answered = !!now.answered; }       // flags may have changed since
            if (now?.unread) post('mark', { ids: [id], mark: 'read' }).then(res => {
                // Opened from this tab's memory: the server is the one that knows if it is still there.
                if (res?.skipped?.length && !st.dead && st.openId === id) { closeReader(); load({ quiet: true }); }
            }).catch(() => {});
        } else try {
            const p = new URLSearchParams({ scope: st.scope, id, read: '1' });
            m = await fetchWithTimeout(`${API}/message?${p}`, {}, WAIT);
            if (m) keep(st.mails, mailKey, m, KEEP_MAILS);
        } catch (e) {
            if (st.dead || mine !== st.mseq) return;
            if (stale(e)) return;
            readEl.innerHTML = `<div class="em-read-bar"><button class="btn em-back">&#8249; Back</button></div>
                <div class="em-empty em-error">${escapeHtml(e.message || "Couldn't open that email.")}</div>`;
            if (/no email /i.test(e.message || '')) load({ quiet: true });       // it was moved or deleted elsewhere
            return;
        }
        if (st.dead || mine !== st.mseq || !m) return;
        st.open = m;
        st.pictures = false;
        // Plain text when the mail has it, unless you chose Formatted before.
        if (!m.html || st.frameBlocked) st.view = 'text';
        else if (!m.has_plain) st.view = 'html';
        else st.view = remember('view') === 'html' ? 'html' : 'text';
        const row = st.rows.find(r => r.id === id);
        st.openHome = homeOf(row) || m.folder || st.folder;  // where this mail is: its buttons go by that
        if (row?.unread) {                                   // opening it read it
            row.unread = false;
            st.countsDirty = true;
            const f = folderOf(st.openHome);
            if (f && f.unread) f.unread -= 1;
            paintFolders();
            paintList();
        }
        paintReader();
    }

    const peopleHtml = list => (list || []).map(p =>
        `<span class="em-person">${escapeHtml(p.name || p.addr)} <span class="em-addr">&lt;${escapeHtml(p.addr)}&gt;</span></span>`).join(', ');

    function paintReader() {
        const m = st.open;
        if (!m) return;
        const files = m.attachments || [];
        readEl.innerHTML = `
            <div class="em-read-bar">
                <button class="btn em-back">&#8249; Back</button>
                <button class="btn btn-primary em-do" data-do="reply">Reply</button>
                <button class="btn em-do" data-do="reply_all">Reply all</button>
                <button class="btn em-do" data-do="forward">Forward</button>
                ${actionButtons('em-act', [st.openHome || st.folder])}
                <select class="em-move" aria-label="Move this email">${moveOptions([st.openHome || st.folder])}</select>
                <button class="btn em-do" data-do="unread">Mark unread</button>
                <button class="btn em-do" data-do="star">${m.starred ? 'Remove star' : 'Star'}</button>
            </div>
            <div class="em-head">
                <h3 class="em-h-subject">${escapeHtml(m.subject)}</h3>
                <div class="em-h-line"><span class="em-h-key">From</span><span>${peopleHtml(m.from) || 'Unknown'}</span></div>
                ${m.to?.length ? `<div class="em-h-line"><span class="em-h-key">To</span><span>${peopleHtml(m.to)}</span></div>` : ''}
                ${m.cc?.length ? `<div class="em-h-line"><span class="em-h-key">Cc</span><span>${peopleHtml(m.cc)}</span></div>` : ''}
                <div class="em-h-line"><span class="em-h-key">Date</span><span>${escapeHtml(whenLong(m.ts, m.date))}</span></div>
            </div>
            ${files.length ? `<div class="em-files">${files.map(f =>
                `<button class="em-file" data-n="${f.n}" title="Download ${escapeHtml(f.name)}">&#128206; ${escapeHtml(f.name)}
                    <span class="em-file-size">${escapeHtml(sizeWords(f.size))}</span></button>`).join('')}</div>` : ''}
            <div class="em-viewbar"></div>
            <div class="em-body"></div>`;
        paintBody();
    }

    function paintBody() {
        const m = st.open;
        const box = readEl.querySelector('.em-body'), bar = readEl.querySelector('.em-viewbar');
        if (!m || !box) return;
        const formatted = st.view === 'html' && !!m.html;
        let tools = '';
        if (m.html && !st.frameBlocked) {
            tools += `<span class="em-views">
                <button class="em-view-btn${formatted ? '' : ' active'}" data-view="text">Plain text</button>
                <button class="em-view-btn${formatted ? ' active' : ''}" data-view="html">Formatted</button></span>`;
        }
        if (formatted && m.remote && !st.pictures) {
            tools += `<span class="em-note">Remote pictures are blocked, so the sender cannot tell you opened this.</span>
                <button class="btn em-pictures">Show remote pictures</button>`;
        }
        if (st.frameBlocked && m.html) tools += '<span class="em-note">This browser will not draw formatted mail here, so the plain text is shown.</span>';
        if (m.text_cut && !formatted) tools += '<span class="em-note">This email is very long; the first part is shown.</span>';
        bar.innerHTML = tools;
        bar.hidden = !tools;
        if (formatted) {
            const frame = document.createElement('iframe');
            frame.className = 'em-frame';
            frame.title = 'The email, as its sender formatted it';
            frame.setAttribute('sandbox', 'allow-popups allow-popups-to-escape-sandbox');
            frame.setAttribute('referrerpolicy', 'no-referrer');
            frame.srcdoc = frameDoc(m.html, st.pictures);
            box.replaceChildren(frame);
        } else {
            const text = document.createElement('div');
            text.className = 'em-text';
            text.textContent = m.text || '(no text content)';
            box.replaceChildren(text);
        }
    }

    // If the app's own policy refuses the frame, fall back to the text for good.
    function onPolicy(e) {
        const what = e.effectiveDirective || e.violatedDirective || '';
        if (!what.startsWith('frame-src') || !String(e.blockedURI || '').startsWith('about')) return;
        if (st.dead || st.frameBlocked) return;
        st.frameBlocked = true;
        st.view = 'text';
        paintBody();
    }

    function download(n) {
        const m = st.open, f = m?.attachments?.find(a => a.n === n);
        if (!f) return;
        const p = new URLSearchParams({ scope: st.scope, id: m.id, n: String(n) });
        saveUrl(`${API}/attachment?${p}`, f.name);
    }

    // -- writing -------------------------------------------------------------------------

    // mode: 'new', 'reply', 'reply_all' or 'forward'; m: the open mail for the last three.
    function openCompose(mode, m) {
        const reply = mode === 'reply' || mode === 'reply_all';
        const fwd = mode === 'forward';
        const to = reply ? addrLine(mode === 'reply_all' ? m.reply.to_all : m.reply.to) : '';
        const cc = mode === 'reply_all' ? addrLine(m.reply.cc_all) : '';
        const subject = reply ? prefixed(m.subject, 'Re') : (fwd ? prefixed(m.subject, 'Fwd') : '');
        const title = { new: 'New email', reply: 'Reply', reply_all: 'Reply to all', forward: 'Forward' }[mode];
        const files = fwd ? (m.attachments || []).length : 0;
        const note = reply ? 'The email you are answering is quoted under your text when it is sent.'
            : (fwd ? `The email is added under your text${files ? `, with its ${plural(files, 'attachment')}` : ''}.` : '');

        const overlay = document.createElement('div');
        overlay.className = 'modal-overlay em-modal';
        overlay.innerHTML = `<div class="modal-base">
            <div class="modal-header"><h3>${title}</h3><button class="close-btn em-x" aria-label="Close">&times;</button></div>
            <div class="modal-body em-form">
                <div class="em-from">From ${escapeHtml(st.address)}</div>
                <div class="modal-field"><label for="em-f-to">To</label>
                    <input type="text" id="em-f-to" value="${escapeHtml(to)}" placeholder="name@example.com, another@example.com" autocomplete="off"></div>
                <div class="em-row2">
                    <div class="modal-field"><label for="em-f-cc">Cc</label><input type="text" id="em-f-cc" value="${escapeHtml(cc)}" autocomplete="off"></div>
                    <div class="modal-field"><label for="em-f-bcc">Bcc</label><input type="text" id="em-f-bcc" autocomplete="off"></div>
                </div>
                <div class="modal-field"><label for="em-f-subject">Subject</label>
                    <input type="text" id="em-f-subject" maxlength="500" value="${escapeHtml(subject)}"></div>
                <div class="modal-field"><label for="em-f-body">Message</label>
                    <textarea id="em-f-body" rows="12"></textarea></div>
                ${note ? `<div class="em-note">${escapeHtml(note)}</div>` : ''}
                <div class="em-form-error" role="alert" hidden></div>
                <div class="em-form-cert"></div>
            </div>
            <div class="modal-footer">
                <button class="btn em-x">Cancel</button>
                <span class="em-spacer"></span>
                <button class="btn em-draft" title="Keep it in Drafts, send nothing">Save draft</button>
                <button class="btn btn-primary em-send">Send</button>
            </div></div>`;
        document.body.appendChild(overlay);
        requestAnimationFrame(() => overlay.classList.add('active'));

        const q = id => overlay.querySelector('#em-f-' + id);
        const errBox = overlay.querySelector('.em-form-error');
        const fail = msg => { errBox.textContent = msg; errBox.hidden = false; };
        let working = false;
        // One per dialog. A Send that is pressed again (the first answer was slow) carries the
        // same token, and the server sends a mail with a given token only once.
        const token = (crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`);

        const shut = () => {
            if (working) return;                             // leaving the page mid-send must not eat the dialog
            st.modals.delete(shut);
            overlay.classList.remove('active');
            setTimeout(() => overlay.remove(), 200);
        };
        st.modals.add(shut);
        // Only the buttons close it: a slip of the mouse or Esc must not lose a half-written mail.
        const cancel = () => {
            if (working) return;
            if (!q('body').value.trim()) return shut();
            showConfirm('Discard this email? What you wrote will be lost.', shut, { title: 'Discard email', saveLabel: 'Discard' });
        };
        overlay.querySelectorAll('.em-x').forEach(b => b.addEventListener('click', cancel));

        async function go(draft) {
            if (working) return;
            errBox.hidden = true;
            if (!draft && !q('to').value.trim()) return fail('Add at least one address in To.');
            working = true;
            overlay.querySelectorAll('.modal-footer button').forEach(b => { b.disabled = true; });
            let res;
            try {
                res = await post('send', {
                    token,                                  // the same on a retry: the server sends a mail once
                    mode: mode === 'reply_all' ? 'reply' : mode, id: m?.id || '', draft,
                    to: q('to').value, cc: q('cc').value, bcc: q('bcc').value,
                    subject: q('subject').value, body: q('body').value,
                });
            } catch (e) {
                working = false;
                const words = /took too long/i.test(e.message || '')
                    ? 'This is taking long. It may still be going out: press Send again to see how it ended, it will not go twice.'
                    : (e.message || "That didn't send. Try again.");
                if (!overlay.isConnected) {
                    // The page was left while this was going out: the dialog is gone, the outcome is still owed.
                    return showToast(`Your email was not ${draft ? 'saved' : 'sent'}. ${words}`, 'error');
                }
                overlay.querySelectorAll('.modal-footer button').forEach(b => { b.disabled = false; });
                fail(words);
                if (CERT_WORDS.test(e.message || '')) {
                    certPanel(st.seq, overlay.querySelector('.em-form-cert'), () => {
                        overlay.querySelector('.em-form-cert').innerHTML = '';
                        fail('Trusted. Press Send again.');
                    });
                }
                return;
            }
            working = false;
            shut();
            // Said even after the page was left: a toast is the whole app's, and "did it go?" must have an answer.
            showToast((draft ? 'Draft saved.' : 'Sent.') + (res?.note ? ` ${res.note}` : ''), res?.note ? 'warning' : 'success');
            if (st.dead) return;
            if (reply && !draft) {
                const row = st.rows.find(r => r.id === m.id);
                if (row) row.answered = true;
            }
            load({ quiet: true });
        }
        overlay.querySelector('.em-send').addEventListener('click', () => go(false));
        overlay.querySelector('.em-draft').addEventListener('click', () => go(true));
        setTimeout(() => (reply ? q('body') : q('to')).focus(), 50);
    }

    // -- clicks --------------------------------------------------------------------------

    function onRowsClick(e) {
        if (e.target.closest('.em-retry')) return st.scope ? load() : loadAccounts();
        const rowEl = e.target.closest('.em-row');
        if (!rowEl) return;
        const id = rowEl.dataset.id;
        const row = st.rows.find(r => r.id === id);
        if (!row) return;
        if (e.target.closest('.em-check')) {
            if (e.target.checked) st.sel.add(id); else st.sel.delete(id);
            rowEl.classList.toggle('picked', e.target.checked);
            return paintListBar();
        }
        if (e.target.closest('.em-star')) return markIds([id], row.starred ? 'unstarred' : 'starred');
        openMessage(id);
    }

    function onBarClick(e) {
        if (e.target.closest('.em-prev') && st.page > 1) { st.page--; st.sel.clear(); return load({ counts: false }); }
        if (e.target.closest('.em-next') && st.page < st.pages) { st.page++; st.sel.clear(); return load({ counts: false }); }
        const ids = [...st.sel];
        const act = e.target.closest('[data-act]');
        if (act) return moveIds(ids, act.dataset.act);
        const mark = e.target.closest('[data-mark]');
        if (mark) return markIds(ids, mark.dataset.mark);
    }

    function onBarChange(e) {
        if (e.target.classList.contains('em-all')) {
            st.sel = new Set(e.target.checked ? st.rows.map(r => r.id) : []);
            return paintList();
        }
        if (e.target.classList.contains('em-bulk-move') && e.target.value) return moveIds([...st.sel], e.target.value);
    }

    function onReadClick(e) {
        if (e.target.closest('.em-back')) return closeReader();
        const m = st.open;
        if (!m) return;
        const file = e.target.closest('.em-file');
        if (file) return download(+file.dataset.n);
        const view = e.target.closest('.em-view-btn');
        if (view) {
            st.view = view.dataset.view;
            remember('view', st.view);
            return paintBody();
        }
        if (e.target.closest('.em-pictures')) { st.pictures = true; return paintBody(); }
        const act = e.target.closest('[data-act]');
        if (act) return moveIds([m.id], act.dataset.act);
        const what = e.target.closest('[data-do]')?.dataset.do;
        if (what === 'reply' || what === 'reply_all' || what === 'forward') return openCompose(what, m);
        if (what === 'unread') { const id = m.id; closeReader(); return markIds([id], 'unread'); }
        if (what === 'star') {
            markIds([m.id], m.starred ? 'unstarred' : 'starred');       // flips m.starred before it waits
            e.target.closest('[data-do]').textContent = m.starred ? 'Remove star' : 'Star';
        }
    }

    function onReadChange(e) {
        if (e.target.classList.contains('em-move') && e.target.value && st.open) moveIds([st.open.id], e.target.value);
    }

    function onSearch() {
        const q = $('.em-search').value.trim();
        if (q === st.q) return;
        st.q = q;
        st.page = 1;
        st.sel.clear();
        load({ counts: false });
    }

    // -- wiring --------------------------------------------------------------------------

    rowsEl.addEventListener('click', onRowsClick);
    rowsEl.addEventListener('keydown', e => {
        if (e.key === 'Enter' && e.target.classList.contains('em-row')) openMessage(e.target.dataset.id);
    });
    barEl.addEventListener('click', onBarClick);
    barEl.addEventListener('change', onBarChange);
    readEl.addEventListener('click', onReadClick);
    readEl.addEventListener('change', onReadChange);
    foldersEl.addEventListener('click', e => {
        const b = e.target.closest('.em-folder');
        if (b) setFolder(b.dataset.key);
    });
    $('.em-compose').addEventListener('click', () => openCompose('new', null));
    $('.em-refresh').addEventListener('click', () => { st.countsDirty = true; load(); });
    $('.em-accounts').addEventListener('click', () => { window._settingsTab = 'email'; location.hash = '#settings'; });
    $('.em-search').addEventListener('keydown', e => { if (e.key === 'Enter') onSearch(); });
    $('.em-search').addEventListener('search', onSearch);
    $('.em-where').value = st.where;
    $('.em-where').addEventListener('change', e => {
        st.where = e.target.value === 'here' ? 'here' : 'all';
        remember('where', st.where);
        if (!st.q) return;
        st.page = 1;
        st.sel.clear();
        load({ counts: false });
    });
    $('.em-unread').addEventListener('change', e => {
        st.unreadOnly = e.target.checked;
        st.page = 1;
        st.sel.clear();
        load({ counts: false });
    });
    $('.em-account').addEventListener('change', e => {
        st.scope = e.target.value;
        remember('account', st.scope);
        st.address = st.accounts.find(a => a.scope === st.scope)?.address || '';    // Compose says "From" at once
        st.folders = st.folderLists.get(st.scope) || [];
        st.rows = [];
        st.shown = '';
        st.folder = remember('folder.' + st.scope) || 'inbox';
        st.page = 1;
        st.q = '';
        st.sel.clear();
        $('.em-search').value = '';
        closeReader();
        paintFolders();
        rowsEl.innerHTML = '<div class="em-empty">Loading your mail...</div>';
        load();
    });
    document.addEventListener('securitypolicyviolation', onPolicy);

    const onScreen = () => !st.dead && root.isConnected && document.visibilityState === 'visible' && root.offsetParent !== null;
    const refresh = () => {
        // lastTry, not the last success: coming back to the tab fires two events at once, and a
        // first load that failed (the server was down) must be tried again without a click.
        if (onScreen() && st.scope && Date.now() - (st.lastTry || 0) > REFRESH_MS / 2) load({ quiet: st.loaded });
    };
    const timer = setInterval(refresh, REFRESH_MS);
    window.addEventListener('focus', refresh);
    document.addEventListener('visibilitychange', refresh);

    loadAccounts();

    return () => {
        st.dead = true;
        clearInterval(timer);
        window.removeEventListener('focus', refresh);
        document.removeEventListener('visibilitychange', refresh);
        document.removeEventListener('securitypolicyviolation', onPolicy);
        [...st.modals].forEach(close => { try { close(); } catch { /* already gone */ } });
        if (--cssUsers <= 0 && cssLink) { cssLink.remove(); cssLink = null; cssUsers = 0; }
    };
}
