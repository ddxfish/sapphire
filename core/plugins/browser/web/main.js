// core/plugins/browser/web/main.js - pairing with the user's own browser, on her page
//
// The Sapphire extension (../extension) sends ONE signal into the tab the user
// pressed its button on: the event below, its detail a JSON string.
//   {v: 1, ask: 'hello'}                  is this page a Sapphire that can pair? (preventDefault = yes)
//   {v: 1, ask: 'pair', code, browser}    this browser asks to be a Browser device
//   {v: 1, ask: 'unpair', id}             this browser dropped its pairing (id = the code's public half)
// Everything else is here and in routes/pair.py, so pairing can change without
// a new extension: this page asks the user, names the device and adds it.
// The code is a key; it goes nowhere but that route.
import { fetchWithTimeout } from '/static/shared/fetch.js';
import { showModal, escapeHtml } from '/static/shared/modal.js';
import { showToast } from '/static/shared/notify.js';

const SIGNAL = 'sapphire-browser-bridge';
const CODE = /^[A-Za-z0-9_-]{4,32}\.[A-Za-z0-9_-]{32,}$/;
let asking = false;

async function add(code, name) {
    try {
        const d = await post('pair', { code, name });
        if (d.already && !d.enabled) showToast(`This browser is already the device "${d.label}", which is switched off in Settings > Devices.`, 'warning', 9000);
        else if (d.already) showToast(`This browser is already paired as "${d.label}". It connects in a few seconds.`, 'success', 7000);
        else showToast(`Paired as "${d.label}". It connects in a few seconds. Share a tab from the extension's button, then ask her to list browser tabs.`, 'success', 12000);
    } catch (e) {
        showToast(`Pairing did not work: ${e.message || e}`, 'error', 9000);
    }
}

async function post(path, body) {
    return await fetchWithTimeout('/api/plugin/browser/' + path, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    }, 20000) || {};
}

async function unpaired(id) {
    try {
        const d = await post('unpair', { id });
        if (!d.device) return;                           // no device here was this browser
        showModal('Remove this browser?', [
            { type: 'html', value: `<p style="margin:0">The extension in this browser was unpaired, so the device "${escapeHtml(d.label)}" `
                + 'can no longer connect. Remove it from Sapphire too?</p>' },
        ], async () => {
            try {
                await post('unpair', { id, remove: true });
                showToast(`Removed "${d.label}".`, 'success', 6000);
            } catch (e) { showToast(`It could not be removed: ${e.message || e}`, 'error', 9000); }
        }, { saveLabel: 'Remove' });
    } catch (e) { /* nothing to offer */ }
}

function onSignal(e) {
    let m = null;
    try { m = JSON.parse(e.detail); } catch (_) { return; }
    if (!m || m.v !== 1) return;
    if (m.ask === 'hello') return e.preventDefault();
    if (m.ask === 'unpair' && typeof m.id === 'string' && /^[A-Za-z0-9_-]{4,32}$/.test(m.id)) {
        e.preventDefault();
        return unpaired(m.id);
    }
    if (m.ask !== 'pair' || typeof m.code !== 'string' || !CODE.test(m.code)) return;
    e.preventDefault();
    if (asking) return;
    asking = true;
    const browser = String(m.browser || 'A browser').slice(0, 40);
    const kind = browser.split(' ')[0].replace(/[^A-Za-z]/g, '') || 'Browser';
    const done = () => { asking = false; };
    const modal = showModal('Pair this browser?', [
        { type: 'html', value: `<p style="margin:0 0 8px">${escapeHtml(browser)}, the browser you are in, asks to become a device. `
            + 'Sapphire can then read and use the tabs you share from it, with the logins that browser has.</p>'
            + '<p style="margin:0;color:var(--text-muted)">Only allow this if you just pressed Pair in the Sapphire extension.</p>' },
        { id: 'name', label: 'Name for this browser', type: 'text', value: kind },
    ], (data) => { done(); add(m.code, String(data.name || '').trim() || kind); }, { saveLabel: 'Allow' });
    setTimeout(done, 120000);                           // a dialog closed without an answer must not block the next ask
    return modal;
}

export default {
    init() {
        window.__sapphireBridgePair?.abort();           // the app reloads plugin scripts: one listener, the newest
        const stop = window.__sapphireBridgePair = new AbortController();
        document.addEventListener(SIGNAL, onSignal, { signal: stop.signal });
    },
};
