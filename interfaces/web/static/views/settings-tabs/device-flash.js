// settings-tabs/device-flash.js - a new board from the browser (tmp/board-flash-web-plan.md)
//
// + Add Device > "New board (USB)". The board is written over USB from this
// browser (Web Serial, esptool-js), then told its name, the WiFi, and what
// Sapphire gave it (POST /api/devices/provision: its keys, her address, her
// certificate) over the same port, with the firmware's own `setup {json}`
// console line. Its address is never typed: it is learned when the board
// calls in. This page is a courier. The firmware comes from Sapphire
// (GET /api/devices/firmware), never from the internet directly.
//
// A `lane` is where the board is plugged in. Web Serial (this computer) is
// the one lane for now; a server lane (the board on Sapphire's computer,
// pyserial + esptool there) would speak the same four verbs: connect, flash,
// ask, reopen.

import { showModal, escapeHtml as esc } from '../../shared/modal.js';
import { fetchWithTimeout } from '../../shared/fetch.js';
import { showToast } from '../../shared/toast.js';

const API = '/api/devices';
const FLASH_BAUD = 460800;
const BOOT_WAIT = 2500;        // ms a board takes to boot: a UART-bridge board resets when its port opens
const JOIN_WAIT = 45000;       // ms for WiFi to join
const COME_BACK = 8000;        // ms a native-USB board gets to reappear after its reset, before "unplug it"
const ASK_WAIT = 15000;        // ms for one console answer

const sleep = ms => new Promise(r => setTimeout(r, ms));
const fail = msg => { throw new Error(msg); };

// ---- the firmware's console over an open port -------------------------------
// One line in; log lines come out until one `>> {json}` answers.

class Console {
    constructor(port, log) { this.port = port; this.log = log; this.buf = ''; this.pending = null; }

    async open() {
        await this.port.open({ baudRate: 115200 });
        // A UART-bridge board boots into its program or its ROM loader by how
        // DTR and RTS sit when the port opens, and the browser picks them. So:
        // the classic run-mode reset (EN low with IO0 high, then let go), the
        // one esptool uses. A board with no such circuit ignores it.
        try {
            await this.port.setSignals({ dataTerminalReady: false, requestToSend: true });
            await sleep(100);
            await this.port.setSignals({ dataTerminalReady: false, requestToSend: false });
        } catch { /* not every driver has the lines */ }
        this.reader = this.port.readable.getReader();
        this.writer = this.port.writable.getWriter();
        this.dec = new TextDecoder();
    }

    async close() {
        this.pending?.catch(() => {}); this.pending = null;       // a read cut short is not an error
        for (const step of [() => this.reader?.cancel(), () => this.reader?.releaseLock(),
                            () => this.writer?.releaseLock(), () => this.port.close()]) {
            try { await step(); } catch { /* already gone */ }
        }
        this.reader = this.writer = null;
    }

    async line(ms) {
        const until = Date.now() + ms;
        for (;;) {
            const i = this.buf.indexOf('\n');
            if (i >= 0) { const l = this.buf.slice(0, i).replace(/\r$/, ''); this.buf = this.buf.slice(i + 1); return l; }
            const left = until - Date.now();
            if (left <= 0) return null;
            this.pending ??= this.reader.read();             // one read at a time; a timed-out one is kept
            const got = await Promise.race([this.pending, sleep(left).then(() => 'late')]);
            if (got === 'late') return null;
            this.pending = null;
            if (got.done) fail('The port closed.');
            this.buf += this.dec.decode(got.value, { stream: true });
        }
    }

    async ask(cmd, ms = ASK_WAIT) {
        this.buf = '';
        await this.writer.write(new TextEncoder().encode(cmd + '\n'));
        const until = Date.now() + ms;
        for (;;) {
            const l = await this.line(until - Date.now());
            if (l === null) fail(`The board did not answer "${cmd.split(' ')[0]}".`);
            if (!l.startsWith('>> ')) { if (l.trim()) this.log(l); continue; }
            try { return JSON.parse(l.slice(3)); } catch { /* cut by a log line: wait for the next */ }
        }
    }
}

// ---- the Web Serial lane ------------------------------------------------------

function webSerialLane(log) {
    let port = null, transport = null, loader = null, con = null, tools = null;
    const info = () => port?.getInfo?.() || {};
    const same = p => { const a = p.getInfo(), b = info(); return a.usbVendorId === b.usbVendorId && a.usbProductId === b.usbProductId; };

    // the port again after a reset: the same one (a UART bridge) or the one
    // that reappears (native USB re-enumerates). Resolves when it is open.
    async function reopen(onWaiting) {
        if (con) { await con.close(); con = null; }
        if (transport) {                              // still in the bootloader from connect(): let it run
            await loader?.after('hard_reset').catch(() => {});
            await transport.disconnect().catch(() => {});
            loader = transport = null;
        }
        const until = Date.now() + COME_BACK;
        let warned = false;
        for (;;) {
            try {
                con = new Console(port, log);
                await con.open();
                await sleep(BOOT_WAIT);
                return;
            } catch (e) {
                con = null;
                const back = (await navigator.serial.getPorts()).find(same);
                if (back && back !== port) { port = back; continue; }
                if (!warned && Date.now() > until) { warned = true; onWaiting?.(); }
                await sleep(500);
            }
        }
    }

    return {
        name: 'this computer',
        available: () => !!navigator.serial,

        async connect(onStatus) {
            tools ??= await import('../../vendor/esptool-js.bundle.js');
            try {
                port = await navigator.serial.requestPort();
            } catch (e) {
                if (e.name === 'NotFoundError') return null;         // the picker was closed
                throw e;
            }
            onStatus?.('Looking for the board on that port: this takes a few seconds...');
            transport = new tools.Transport(port, false);
            loader = new tools.ESPLoader({
                transport, baudrate: FLASH_BAUD, romBaudrate: 115200,
                terminal: { clean() {}, writeLine: log, write: log },
            });
            try {
                const text = await loader.main();                     // "ESP32-D0WD-V3 (revision 3)": the silicon
                return { family: loader.chip?.CHIP_NAME || family(text), text };   // "ESP32", "ESP32-S3": what firmware fits
            } catch (e) {
                await transport.disconnect().catch(() => {});
                throw new Error(portProblem(e));
            }
        },

        async flash(board, parts, onProgress) {
            await loader.writeFlash({
                fileArray: parts.map(p => ({ data: p.data, address: p.offset })),
                flashSize: board.flash.size, flashMode: board.flash.mode, flashFreq: board.flash.freq,
                eraseAll: true, compress: true,
                reportProgress: (i, written, total) => onProgress(i, written, total),
            });
            await loader.after('hard_reset').catch(() => {});
            await transport.disconnect().catch(() => {});
            loader = transport = null;
        },

        reopen,
        ask: (cmd, ms) => con.ask(cmd, ms),
        async close() { if (con) await con.close(); con = null; await transport?.disconnect().catch(() => {}); },
    };
}

function portProblem(e) {
    const m = String(e?.message || e);
    if (/access denied|permission|EACCES/i.test(m)) return 'The port could not be opened. On Linux, add yourself to the dialout group (sudo usermod -aG dialout $USER), then log out and in.';
    if (/already open|in use|busy/i.test(m)) return 'The port is in use. Close any serial monitor on it and try again.';
    if (/timed out|Failed to connect/i.test(m)) return 'No bootloader answered. Hold the BOOT button while plugging the board in, then try again.';
    return m;
}

// ---- the wizard ----------------------------------------------------------------

const call = (method, path, body) => fetchWithTimeout(API + path, {
    method, headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
}, 30000);

async function partBytes(board, p) {
    const r = await fetch(`${API}/firmware/${board.id}/${p.path}`, { credentials: 'same-origin' });
    if (!r.ok) {
        let why = `HTTP ${r.status}`;
        try { why = (await r.json()).detail || why; } catch { /* not json */ }
        fail(`Could not get ${p.path}: ${why}`);
    }
    return { ...p, data: new Uint8Array(await r.arrayBuffer()) };
}

// "esp32s3", "ESP32-S3", "ESP32-S3 (QFN56)" -> ESP32-S3; "ESP32-D0WD-V3 (revision 3)" -> ESP32
const family = s => (String(s || '').toUpperCase().replace(/\s+/g, '').replace(/^ESP32(?=[SCH]\d)/, 'ESP32-')
    .match(/^ESP32(-[SCH]\d+)?|^ESP8266/) || [''])[0];

export function openFlash(onDone) {
    const modal = showModal('\u{1F4E1} New board (USB)', [{ type: 'html', value: '<div id="flash"></div>' }], null, { wide: true });
    const body = modal.element.querySelector('#flash');
    const footer = modal.element.querySelector('.modal-footer');
    const said = [];                              // the last lines from the flasher and the board, for an error
    const log = s => {
        const text = String(s).replace(/\r?\n?$/, '');
        if (!text.trim()) return;
        said.push(text); if (said.length > 60) said.shift();
        const el = body.querySelector('#flash-log');
        if (el) { el.textContent += text + '\n'; el.scrollTop = el.scrollHeight; }
    };
    const status = text => { const el = body.querySelector('#flash-status'); if (el) el.textContent = text; };
    const lane = webSerialLane(log);
    let chip = { family: '', text: '' }, board = null, given = null, button = null;
    // the modal has no close event: when it leaves the page, let the port go
    const gone = new MutationObserver(() => { if (!modal.element.isConnected) { gone.disconnect(); hold(false); lane.close(); } });
    gone.observe(document.body, { childList: true });

    // While the board is being written or set up, nothing closes this: not
    // the X, Esc, the backdrop, nor leaving the page (a slipped click left a
    // board half written, 2026-10-07). The browser's own leave prompt is the
    // only one it allows; its words are its own.
    let busy = false;
    const leaving = e => { e.preventDefault(); e.returnValue = ''; };
    const keep = e => {
        if (!busy) return;
        if (e.type === 'keydown' && e.key !== 'Escape') return;
        if (e.type !== 'keydown' && !(e.target === modal.element || e.target.closest('.modal-x, .modal-close, .modal-cancel'))) return;
        e.stopImmediatePropagation();
        if (e.type !== 'mousedown') showToast('Still working on the board. Wait for it to finish.', 'warning');
    };
    const hold = on => {
        if (on === busy) return;
        busy = on;
        const way = on ? 'addEventListener' : 'removeEventListener';
        window[way]('beforeunload', leaving);
        document[way]('keydown', keep, true);
        modal.element[way]('click', keep, true);
        modal.element[way]('mousedown', keep, true);
    };

    const screen = (title, html, foot) => {
        button?.remove(); button = null;
        body.innerHTML = `<b style="display:block;margin-bottom:10px">${title}</b>${html}
            <p class="setting-help" id="flash-status" style="margin-top:8px;min-height:1.2em"></p>
            <details style="margin-top:6px"><summary class="setting-help" style="cursor:pointer">Details: what the flasher and the board said</summary>
            <pre id="flash-log" style="max-height:140px;overflow:auto;font-size:0.75em;opacity:0.7;margin:6px 0 0;white-space:pre-wrap"></pre></details>`;
        const pre = body.querySelector('#flash-log');
        pre.textContent = said.slice(-20).join('\n') + (said.length ? '\n' : '');
        if (foot) {
            button = document.createElement('button');
            button.className = 'btn btn-primary';
            button.textContent = foot.text;
            footer.prepend(button);
            button.addEventListener('click', async () => {
                button.disabled = true;
                try { await foot.run(); } catch (e) {
                    hold(false);
                    status(e.message);
                    const d = body.querySelector('details'); if (d) d.open = true;      // the chatter explains an error
                    showToast(e.message, 'error'); button.disabled = false;
                }
            });
        }
        return body;
    };
    const bar = (pct, text) => {
        const el = body.querySelector('#flash-bar');
        if (el) { el.firstElementChild.style.width = `${pct}%`; el.nextElementSibling.textContent = text; }
    };

    // 1. plug it in
    const connect = () => {
        if (!lane.available()) {
            return screen('This step needs Chrome or Edge',
                `<p class="setting-help">This browser cannot talk to a USB port (Web Serial). Open this page in Chrome or Edge on the computer the board is plugged into.</p>`);
        }
        screen('Plug the board in',
            `<p class="setting-help">Plug the board into <b>this</b> computer with a data cable (many USB cables carry power only), then press Connect and pick its port.</p>`,
            { text: 'Connect', run: async () => {
                status('Pick the port in the browser\'s window...');
                chip = await lane.connect(status);
                if (!chip) { status(''); button.disabled = false; return; }
                await pick();
            } });
    };

    // 2. which board: only the ones this chip can run
    const pick = async () => {
        screen(`Found an ${esc(chip.family)}`, '<p class="setting-help">Reading the firmware list...</p>');
        const fw = await call('GET', '/firmware');
        const fit = fw.boards.filter(b => family(b.chipFamily) === chip.family);
        if (!fit.length) {
            // nothing to offer: say why, and let the source be fixed right here
            screen(`Found an ${esc(chip.family)}`, `<p class="setting-help">${esc(fw.error || `No firmware for an ${chip.family} (${chip.text}) in the firmware source.`)}</p>
                <div class="settings-grid"><div class="setting-row"><div class="setting-label"><label>Firmware source</label>
                    <div class="setting-help">The firmware release URL, or a folder on Sapphire's computer with an index.json.</div></div>
                    <div class="setting-input"><input type="text" id="fl-source" value="${esc(fw.source || '')}" placeholder="https://.../index.json's folder"></div></div></div>`,
                { text: 'Save and look again', run: async () => {
                    await fetchWithTimeout('/api/settings/batch', { method: 'PUT', headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ settings: { DEVICE_FIRMWARE_SOURCE: body.querySelector('#fl-source').value.trim() } }) }, 15000);
                    await pick();
                } });
            return;
        }
        screen(`Found an ${esc(chip.family)}`, `<p class="setting-help">${esc(chip.text)}. Which board is it?</p>
            <div class="ui-grid ui-grid-sm">${fit.map((b, i) => `
                <button type="button" class="ui-card" data-board="${esc(b.id)}" style="text-align:left;font:inherit;cursor:pointer${i ? '' : ';outline:2px solid var(--primary)'}">
                    <div class="ui-card-title">${esc(b.name)}</div>
                    <div class="ui-card-body">firmware ${esc(b.version)}</div></button>`).join('')}</div>
            <p class="setting-help" style="margin-top:10px">Already running Sapphire's firmware? <a href="#" id="fl-settings-only">Only change its name, WiFi or Sapphire's address</a>, without installing.</p>`,
            { text: 'Install', run: () => install() });
        board = fit[0];
        body.querySelector('#fl-settings-only').addEventListener('click', async e => {
            e.preventDefault();
            button.disabled = true;
            try {
                status('Starting the board...');
                await lane.reopen(() => status('Unplug the board and plug it back in.'));
                await setup();
            } catch (err) { status(err.message); showToast(err.message, 'error'); button.disabled = false; }
        });
        body.querySelectorAll('[data-board]').forEach(c => c.addEventListener('click', () => {
            board = fit.find(b => b.id === c.dataset.board);
            body.querySelectorAll('[data-board]').forEach(x => x.style.outline = x === c ? '2px solid var(--primary)' : '');
        }));
    };

    // 3. write it
    const install = async () => {
        hold(true);
        screen(`Installing ${esc(board.name)} ${esc(board.version)}`, `
            <div id="flash-bar" style="height:8px;background:var(--bg-tertiary,#333);border-radius:4px;overflow:hidden"><div style="height:100%;width:0;background:var(--primary)"></div></div>
            <p class="setting-help" style="margin-top:6px">Getting the firmware from Sapphire...</p>`);
        const parts = [];
        for (const p of board.parts) parts.push(await partBytes(board, p));
        const total = parts.reduce((n, p) => n + p.data.length, 0);
        let before = 0;
        await lane.flash(board, parts, (i, written, size) => {
            if (i > 0 && written === 0) before = parts.slice(0, i).reduce((n, p) => n + p.data.length, 0);
            const done = before + written;
            bar(Math.round(100 * done / total), `${Math.round(done / 1024)} of ${Math.round(total / 1024)} KB`);
        });
        bar(100, 'Written. Waiting for it to start...');
        await lane.reopen(() => bar(100, 'Unplug the board and plug it back in.'));
        hold(false);
        await setup();
    };

    // 4. its name and WiFi
    const setup = async () => {
        status('Asking the board which networks it can see...');
        let nets = [], here = { sapphire: '', addresses: [] };
        try { nets = (await lane.ask('scan', 20000)).networks || []; } catch (e) { log(`(no network list: ${e.message})`); }
        try { here = await call('GET', '/here'); } catch (e) { log(`(no address guess: ${e.message})`); }
        const names = [...new Set(nets.map(n => n.ssid || n).filter(Boolean))];
        screen('Name it and give it the WiFi', `
            <div class="settings-grid">
                <div class="setting-row"><div class="setting-label"><label>Name</label><div class="setting-help">What Sapphire calls it.</div></div>
                    <div class="setting-input"><input type="text" id="fl-name" value="${esc(board.id)}" maxlength="33"></div></div>
                <div class="setting-row"><div class="setting-label"><label>WiFi</label><div class="setting-help">${names.length ? 'What the board can see. ' : ''}Type one it cannot see yet.</div></div>
                    <div class="setting-input">${names.length ? `<select id="fl-pick">${names.map(n => `<option>${esc(n)}</option>`).join('')}<option value="">Other network...</option></select>` : ''}
                        <input type="text" id="fl-ssid" placeholder="network name" ${names.length ? 'style="display:none;margin-top:6px"' : ''}></div></div>
                <div class="setting-row"><div class="setting-label"><label>Password</label></div>
                    <div class="setting-input"><input type="password" id="fl-pass" autocomplete="off"></div></div>
                <div class="setting-row"><div class="setting-label"><label>Sapphire's address</label>
                    <div class="setting-help">Where the board will find her: this computer, as the house sees it. With a VPN on, make sure this is the house address, not the tunnel's.</div></div>
                    <div class="setting-input"><input type="text" id="fl-sapphire" value="${esc(here.sapphire)}" placeholder="https://192.168.1.2:8073">
                        ${here.addresses.length > 1 ? `<div class="setting-help" style="margin-top:4px">also here: ${here.addresses.slice(1).map(esc).join(', ')}</div>` : ''}</div></div>
            </div>`,
            { text: 'Finish', run: () => finish() });
        const pickEl = body.querySelector('#fl-pick'), ssidEl = body.querySelector('#fl-ssid');
        pickEl?.addEventListener('change', () => { ssidEl.style.display = pickEl.value ? 'none' : ''; if (!pickEl.value) ssidEl.focus(); });
        body.querySelector('#fl-name').focus();
    };

    // 5. Sapphire's side, then the board's, then wait for WiFi
    const finish = async () => {
        const name = body.querySelector('#fl-name').value.trim();
        const pickEl = body.querySelector('#fl-pick');
        const ssid = (pickEl?.value || body.querySelector('#fl-ssid').value).trim();
        const pass = body.querySelector('#fl-pass').value;
        const sapphire = body.querySelector('#fl-sapphire').value.trim();
        if (!name) fail('Give the board a name.');
        if (!ssid) fail('Which WiFi?');
        if (!sapphire) fail("Where is Sapphire? Her address is needed.");
        hold(true);
        given = await call('POST', '/provision', { label: name, driver: 'satellite', sapphire });
        const said = await lane.ask('setup ' + JSON.stringify({
            name: given.id, wifi_ssid: ssid, wifi_password: pass,
            key: given.token, voice_key: given.voice_key, sapphire: given.sapphire, cert: given.cert || '',
        }));
        if (!said.ok) fail(said.error || 'The board refused its settings.');
        screen(`Joining ${esc(ssid)}`, `<p class="setting-help" id="fl-join">The board has its settings and is restarting to join the WiFi...</p>`);
        await sleep(1500);
        await lane.reopen(() => { body.querySelector('#fl-join').textContent = 'Unplug the board and plug it back in.'; });
        const until = Date.now() + JOIN_WAIT;
        while (Date.now() < until) {
            let s = {};
            try { s = await lane.ask('show', 5000); } catch { /* still booting */ }
            if (s.ip) {
                // its address, before it has called in: the device window can reach it now,
                // and its status says whether it reaches Sapphire ("link to Sapphire")
                try { await call('PUT', `/${given.id}`, { parts: { satellite: { url: `http://${s.ip}` } } }); }
                catch (e) { log(`(could not store its address: ${e.message})`); }
                hold(false);
                showToast(`${given.id} is on the WiFi at ${s.ip}`, 'success');
                modal.close();
                onDone?.(given.id);
                return;
            }
            await sleep(2000);
        }
        hold(false);
        screen('It has not joined yet', `<p class="setting-help">No WiFi after ${JOIN_WAIT / 1000} seconds. A wrong password is the usual reason. Fix it and try again, or close this: the board keeps trying, and shows up in the list by itself when it gets on.</p>`,
            { text: 'Try again', run: () => setup() });
    };

    connect();
}
