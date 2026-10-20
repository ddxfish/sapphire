// Corpus for shared/notify.js, run under node by tests/test_notify_js.py. No
// real DOM: a stub records what the toast lane appends, a stub fetch records
// what goes to the server. Covers the lane (three at once, the rest wait),
// the legacy showToast mapping (warning/error -> a row with a text key,
// success/info -> a flash), the threshold (rows obey it, a flash never), the
// mirror's rows()/counts() through the bus, and goTo's target shapes.
const appended = [];
const container = { appendChild(el) { appended.push(el); } };
function fakeEl(tag) {
    const el = {
        tag, children: [], _cls: new Set(), style: {}, listeners: {}, textContent: '', title: '', removed: false,
        set className(v) { el._cls = new Set(String(v).split(/\s+/).filter(Boolean)); },
        get className() { return [...el._cls].join(' '); },
        classList: {
            add: c => el._cls.add(c), remove: c => el._cls.delete(c), contains: c => el._cls.has(c),
            toggle(c, f) { (f === undefined ? !el._cls.has(c) : f) ? el._cls.add(c) : el._cls.delete(c); },
        },
        appendChild(c) { el.children.push(c); return c; },
        append(...cs) { el.children.push(...cs); },
        addEventListener(t, fn) { (el.listeners[t] ||= []).push(fn); },
        click(t = 'click') { for (const fn of el.listeners[t] || []) fn({ stopPropagation() {}, target: el }); },
        remove() { const i = appended.indexOf(el); if (i >= 0) appended.splice(i, 1); el.removed = true; },
        scrollIntoView() {}, querySelector: () => null, dispatchEvent() { el.dispatched = (el.dispatched || 0) + 1; return true; },
    };
    return el;
}
const byId = {};
globalThis.document = {
    getElementById: id => id === 'toast-container' ? container : (byId[id] || null),
    createElement: fakeEl, querySelector: () => null,
};
globalThis.window = { addEventListener() {}, location: {} };
globalThis.location = { hash: '' };
globalThis.history = { replaceState() {} };
globalThis.sessionStorage = { _m: {}, getItem(k) { return this._m[k] ?? null; }, setItem(k, v) { this._m[k] = v; } };
globalThis.EventSource = class { close() {} };
globalThis.CustomEvent = class { constructor(type, init) { this.type = type; this.detail = init?.detail; } };
globalThis.CSS = { escape: s => s };
const timers = [];
globalThis.setTimeout = fn => { timers.push(fn); return timers.length; };   // never fire on their own
globalThis.clearTimeout = () => {};
const posts = [];
const serverRows = [];            // what the fake server keeps: GET /api/notifications lists it
let serial = 0;
globalThis.fetch = async (url, opts = {}) => {
    const method = opts.method || 'GET';
    const body = opts.body ? JSON.parse(opts.body) : null;
    posts.push({ url, method, body });
    let answer = {};
    if (body) {
        const row = { id: `r${++serial}`, ...body, state: 'open', read: false, ts: serial, updated: serial, progress: null };
        serverRows.push(row);
        answer = { id: row.id, row };
    } else if (method === 'GET') {
        answer = { rows: serverRows.map(r => ({ ...r })), counts: {} };
    }
    return { ok: true, status: 200, headers: { get: () => 'application/json' }, json: async () => answer };
};

const m = await import('../../interfaces/web/static/shared/notify.js');
const eb = await import('../../interfaces/web/static/core/event-bus.js');
let n = 0;
const ok = (cond, msg) => { if (!cond) throw new Error('FAIL: ' + msg); n++; };
const tick = () => new Promise(r => setImmediate(r));

// ── the lane: three at once, the rest wait their turn ──
m.flash('one'); m.flash('two'); m.flash('three'); m.flash('four');
ok(appended.length === 3, 'three toasts show, the fourth waits');
ok(appended[0].className === 'toast info' && appended[0].children[0].textContent === 'one', 'a flash is an info toast with its text');
appended[0].children[1].click();                                   // the close button
ok(appended.length === 3 && appended[2].children[0].textContent === 'four', 'closing one lets the next in');
ok(appended.every(el => el.children[1].className === 'toast-close'), 'every toast has a close button');
for (const el of [...appended]) el.children[1].click();
ok(appended.length === 0, 'lane empties');

// ── legacy showToast: warning/error -> a row with a text key, success/info -> a flash ──
m.showToast('Lost the server', 'error', 10000);
await tick();
ok(posts.length === 1 && posts[0].method === 'POST' && posts[0].url === '/api/notifications', 'an error toast posts a row');
ok(posts[0].body.level === 'error' && posts[0].body.key.startsWith('msg:'), 'with level error and a text key');
ok(appended.length === 1 && appended[0].classList.contains('error'), 'and paints the toast at once');
m.showToast('Lost the server', 'error');
await tick();
ok(posts[1].body.key === posts[0].body.key, 'the same words give the same key: one row, re-fired');
ok(appended.length === 2, 'and the re-fire toasts again');
for (const el of [...appended]) el.children[1].click();
m.showToast('Copied', 'success');
m.showToast('Heads up', 'info', 0);
await tick();
ok(posts.length === 2, 'success and info never post');
ok(appended.length === 2 && appended[0].classList.contains('success') && appended[1].classList.contains('info'), 'they flash');
ok(appended[1].style.cursor === 'pointer', 'a sticky flash is click-to-dismiss');
for (const el of [...appended]) el.children[1].click();

// ── the threshold applies to rows, never to a flash ──
m.applySettings({ NOTIFY_TOAST_SECONDS: '7', NOTIFY_TOAST_MIN_LEVEL: 'error' });
ok(m.settings().seconds === 7 && m.settings().minLevel === 'error', 'settings applied');
m.applySettings({ NOTIFY_TOAST_SECONDS: 0, NOTIFY_TOAST_MIN_LEVEL: 'loud' });
ok(m.settings().seconds === 7 && m.settings().minLevel === 'error', 'junk settings ignored');
const before = posts.length;
const id = await m.notify({ title: 'quiet', level: 'warning', go: { view: 'settings', tab: 'tts' } });
ok(id === `r${serial}` && posts.length === before + 1, 'a warning row still posts');
ok(appended.length === 0, 'but below the threshold it does not toast');
m.flash('still here', 'info');
ok(appended.length === 1, 'a flash always shows');
appended[0].children[1].click();
m.applySettings({ NOTIFY_TOAST_MIN_LEVEL: 'success' });

// ── the mirror: rows() and counts() through the bus ──
m.init({ settings: {} });
await tick();
ok(m.rows().some(r => r.id === id) && m.counts().yellow === 1, 'the posted row is mirrored and counts yellow');
eb.dispatch('notification', { id: 'dl', state: 'active', level: 'info', title: 'Downloading voice', updated: 50, ts: 50, read: false, progress: { indeterminate: true }, go: null, source: 'piper' });
ok(m.counts().active === 1 && m.rows()[0].id === 'dl', 'an active row counts and sorts first');
ok(appended.length === 1 && appended[0].children[0].textContent === 'Downloading voice', 'an active row toasts when it starts');
appended[0].children[1].click();
eb.dispatch('notification_progress', { id: 'dl', progress: { done: 5, total: 10, percent: 50, indeterminate: false }, updated: 51 });
ok(m.rows()[0].progress.percent === 50 && appended.length === 0, 'a tick moves the bar and never toasts');
eb.dispatch('notification', { id: 'dl', state: 'done', level: 'success', title: 'Voice ready', updated: 52, ts: 50, read: false, progress: { percent: 100 }, go: null, source: 'piper' });
ok(appended.length === 1 && appended[0].classList.contains('success'), 'the end toasts green');
ok(m.counts().active === 0, 'and it is no longer active');
appended[0].children[1].click();
eb.dispatch('notification', { id: 'e1', state: 'open', level: 'critical', title: 'DB check failed', body: 'halted', updated: 60, ts: 60, read: false, go: { view: 'settings', tab: 'backup' }, source: 'core' });
ok(m.counts().red === 3, 'critical counts red (with r1 and r2, the two legacy error posts the fake server kept)');
ok(appended.length === 1 && appended[0].children[0].textContent === 'DB check failed: halted', 'the toast reads title: body');
ok(appended[0].style.cursor === 'pointer', 'a row toast is a link');
eb.dispatch('notifications_read', {});
ok(m.counts().red === 0 && m.counts().yellow === 0, 'read-all clears the counts');
eb.dispatch('notification_gone', { id: 'e1' });
ok(!m.rows().some(r => r.id === 'e1'), 'gone is gone');
ok(m.rows({ level: 'success' }).length === 1 && m.rows({ source: 'piper' }).length === 1, 'filters by level and source');
appended[0].children[1].click();

// ── goTo: the target shapes ──
byId['view-settings'] = fakeEl('div');
await m.goTo({ view: 'settings', tab: 'tts' });
ok(globalThis.window._settingsTab === 'tts' || byId['view-settings'].dispatched, 'a tab rides the settings door');
await m.goTo({ hash: '#store/plugins/x' });
ok(globalThis.location.hash === '#store/plugins/x', 'a hash target sets the hash');
await m.goTo(null);
ok(true, 'no target is a no-op');

console.log(`${n} passed`);
