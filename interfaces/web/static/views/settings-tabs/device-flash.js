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

        async connect() {
            tools ??= await import('../../vendor/esptool-js.bundle.js');
            try {
                port = await navigator.serial.requestPort();
            } catch (e) {
                if (e.name === 'NotFoundError') return null;         // the picker was closed
                throw e;
            }
            transport = new tools.Transport(port, false);
            loader = new tools.ESPLoader({
                transport, baudrate: FLASH_BAUD, romBaudrate: 115200,
                terminal: { clean() {}, writeLine: log, write: log },
            });
            try {
                return await loader.main();                           // "ESP32", "ESP32-S3", ...
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

const chipOf = s => String(s || '').toUpperCase().replace(/\s+/g, '').replace(/^ESP32(?=[A-Z])/, 'ESP32-');   // "esp32s3" -> ESP32-S3

export function openFlash(onDone) {
    const modal = showModal('\u{1F4E1} New board (USB)', [{ type: 'html', value: '<div id="flash"></div>' }], null, { wide: true });
    const body = modal.element.querySelector('#flash');
    const footer = modal.element.querySelector('.modal-footer');
    const log = s => { const el = body.querySelector('#flash-log'); if (el) { el.textContent += s.replace(/\r?\n?$/, '') + '\n'; el.scrollTop = el.scrollHeight; } };
    const lane = webSerialLane(log);
    let chip = '', board = null, given = null, button = null;
    // the modal has no close event: when it leaves the page, let the port go
    const gone = new MutationObserver(() => { if (!modal.element.isConnected) { gone.disconnect(); lane.close(); } });
    gone.observe(document.body, { childList: true });

    const screen = (title, html, foot) => {
        button?.remove(); button = null;
        body.innerHTML = `<b style="display:block;margin-bottom:10px">${title}</b>${html}
            <pre id="flash-log" style="max-height:110px;overflow:auto;font-size:0.75em;opacity:0.7;margin:10px 0 0;white-space:pre-wrap"></pre>`;
        if (foot) {
            button = document.createElement('button');
            button.className = 'btn btn-primary';
            button.textContent = foot.text;
            footer.prepend(button);
            button.addEventListener('click', async () => {
                button.disabled = true;
                try { await foot.run(); } catch (e) { showToast(e.message, 'error'); button.disabled = false; }
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
                chip = await lane.connect();
                if (!chip) { button.disabled = false; return; }
                await pick();
            } });
    };

    // 2. which board: only the ones this chip can run
    const pick = async () => {
        screen(`Found an ${esc(chip)}`, '<p class="setting-help">Reading the firmware list...</p>');
        const fw = await call('GET', '/firmware');
        const fit = fw.boards.filter(b => chipOf(b.chipFamily) === chipOf(chip));
        if (!fit.length) {
            // nothing to offer: say why, and let the source be fixed right here
            screen(`Found an ${esc(chip)}`, `<p class="setting-help">${esc(fw.error || `No firmware for an ${chip} in the firmware source.`)}</p>
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
        screen(`Found an ${esc(chip)}`, `<p class="setting-help">Which board is it?</p>
            <div class="ui-grid ui-grid-sm">${fit.map((b, i) => `
                <button type="button" class="ui-card" data-board="${esc(b.id)}" style="text-align:left;font:inherit;cursor:pointer${i ? '' : ';outline:2px solid var(--primary)'}">
                    <div class="ui-card-title">${esc(b.name)}</div>
                    <div class="ui-card-body">firmware ${esc(b.version)}</div></button>`).join('')}</div>`,
            { text: 'Install', run: () => install() });
        board = fit[0];
        body.querySelectorAll('[data-board]').forEach(c => c.addEventListener('click', () => {
            board = fit.find(b => b.id === c.dataset.board);
            body.querySelectorAll('[data-board]').forEach(x => x.style.outline = x === c ? '2px solid var(--primary)' : '');
        }));
    };

    // 3. write it
    const install = async () => {
        screen(`Installing ${esc(board.name)} ${esc(board.version)}`, `
            <div id="flash-bar" style="height:8px;background:var(--bg-tertiary,#333);border-radius:4px;overflow:hidden"><div style="height:100%;width:0;background:var(--primary)"></div></div>
            <p class="setting-help" style="margin-top:6px">Getting the firmware...</p>`);
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
        await setup();
    };

    // 4. its name and WiFi
    const setup = async () => {
        let nets = [];
        try { nets = (await lane.ask('scan', 20000)).networks || []; } catch { /* an older firmware: typed */ }
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
        if (!name) fail('Give the board a name.');
        if (!ssid) fail('Which WiFi?');
        given ??= await call('POST', '/provision', { label: name, driver: 'satellite' });
        const said = await lane.ask('setup ' + JSON.stringify({
            name: given.id, wifi_ssid: ssid, wifi_password: pass,
            key: given.token, voice_key: given.voice_key, sapphire: given.sapphire, cert: given.cert || '',
        }));
        if (!said.ok) fail(said.error || 'The board refused its settings.');
        screen(`Joining ${esc(ssid)}`, `<p class="setting-help" id="fl-join">The board is restarting and joining the WiFi...</p>`);
        await sleep(1500);
        await lane.reopen(() => { body.querySelector('#fl-join').textContent = 'Unplug the board and plug it back in.'; });
        const until = Date.now() + JOIN_WAIT;
        while (Date.now() < until) {
            let s = {};
            try { s = await lane.ask('show', 5000); } catch { /* still booting */ }
            if (s.ip) {
                showToast(`${given.id} is on the WiFi at ${s.ip}`, 'success');
                modal.close();
                onDone?.(given.id);
                return;
            }
            await sleep(2000);
        }
        screen('It has not joined yet', `<p class="setting-help">No WiFi after ${JOIN_WAIT / 1000} seconds. A wrong password is the usual reason. Fix it and try again, or close this: the board keeps trying, and shows up in the list by itself when it gets on.</p>`,
            { text: 'Try again', run: () => setup() });
    };

    connect();
}
