// core/plugins/browser/app/index.js - Apps > Browser: her tab, live, and yours to use.
//
// One socket (/ws/plugin/browser/view): JPEG frames and state come down, your
// mouse, keys, paste and address bar go up into her tab. Coordinates are sent
// in her page's own pixels (the state's width x height). Touch: a drag
// scrolls, a tap clicks. Her lane only; the network rails still hold.

let cleanupFn = null;

const CSS = `
.bv { display: flex; flex-direction: column; height: 100%; min-height: 0; gap: 6px; color: var(--text); container-type: inline-size; }
.bv-bar { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
.bv-bar button { padding: 4px 9px; }
.bv-addr { flex: 1 1 260px; min-width: 0; display: flex; gap: 6px; }
.bv-addr input { flex: 1; min-width: 0; padding: 5px 8px; }
.bv-chip { font-size: 12px; color: var(--text-muted); white-space: nowrap; }
.bv-tabs { display: flex; gap: 4px; overflow-x: auto; }
.bv-tab { font-size: 12px; padding: 3px 8px; border: 1px solid var(--border); border-radius: 6px; cursor: pointer;
          max-width: 200px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; background: transparent; color: var(--text); }
.bv-tab.on { background: var(--bg-tertiary); font-weight: 600; }
.bv-note { font-size: 13px; color: var(--text-muted); min-height: 1.2em; }
.bv-stage { flex: 1; min-height: 200px; position: relative; display: flex; align-items: center; justify-content: center;
            background: var(--bg-tertiary); border-radius: 8px; overflow: hidden; }
.bv-stage canvas { background: #fff; touch-action: none; outline: none; box-shadow: 0 0 0 1px var(--border); }
.bv-stage canvas:focus { box-shadow: 0 0 0 2px var(--accent, #4a90d9); }
.bv-sleep { position: absolute; inset: 0; display: flex; flex-direction: column; gap: 10px; align-items: center;
            justify-content: center; background: var(--bg-tertiary); }
.bv-type { display: flex; gap: 6px; }
.bv-type input { flex: 1; min-width: 0; padding: 5px 8px; }
@container (max-width: 560px) { .bv-chip, .bv-wide { display: none; } .bv-addr { flex-basis: 140px; } }
`;

function el(tag, attrs = {}, ...kids) {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
        if (k === 'class') n.className = v; else if (k.startsWith('on')) n.addEventListener(k.slice(2), v); else n.setAttribute(k, v);
    }
    for (const k of kids) n.append(k);
    return n;
}

export async function render(container) {
    cleanupFn?.();
    if (!document.getElementById('bv-css')) document.head.append(el('style', { id: 'bv-css' }, CSS));

    const canvas = el('canvas', { tabindex: '0', width: '1280', height: '800', title: 'Click to use her page; typing goes in while it is outlined' });
    const ctx = canvas.getContext('2d');
    const addr = el('input', { type: 'text', placeholder: 'Address', spellcheck: 'false', autocomplete: 'off' });
    const chip = el('span', { class: 'bv-chip' }, 'connecting...');
    const tabs = el('div', { class: 'bv-tabs' });
    const note = el('div', { class: 'bv-note' });
    const wakeBtn = el('button', { type: 'button' }, 'Wake her browser');
    const sleep = el('div', { class: 'bv-sleep' }, el('div', {}, 'Her browser is asleep.'), wakeBtn);
    const stage = el('div', { class: 'bv-stage' }, canvas, sleep);
    const typeBox = el('input', { type: 'text', placeholder: 'Type into the page (phones)', autocomplete: 'off' });
    const btn = (label, title, fn) => el('button', { type: 'button', title, onclick: fn }, label);
    const form = el('form', { class: 'bv-addr', onsubmit: (e) => { e.preventDefault(); send({ t: 'nav', what: 'go', url: addr.value }); canvas.focus(); } },
        addr, el('button', { type: 'submit' }, 'Go'));
    const root = el('div', { class: 'bv' },
        el('div', { class: 'bv-bar' },
            btn('◀', 'Back', () => send({ t: 'nav', what: 'back' })),
            btn('▶', 'Forward', () => send({ t: 'nav', what: 'forward' })),
            btn('⟳', 'Reload', () => send({ t: 'nav', what: 'reload' })),
            form, chip,
            el('button', { type: 'button', title: 'Clear browser profile: every login, site data, history and cache', onclick: forget },
                '\u{1F9F9}', el('span', { class: 'bv-wide' }, ' Clear browser profile'))),
        tabs, note, stage,
        el('form', { class: 'bv-type', onsubmit: (e) => { e.preventDefault(); if (typeBox.value) send({ t: 'text', text: typeBox.value }); typeBox.value = ''; } },
            typeBox, el('button', { type: 'submit' }, 'Type')));
    container.replaceChildren(root);

    let ws = null, alive = true, retry = 0, retryTimer = null, reloadUntil = 0;
    let vw = 1280, vh = 800, decoding = false, nextBlob = null, noteTimer = null;
    const held = new Set();

    const say = (text) => { note.textContent = text || ''; clearTimeout(noteTimer); if (text) noteTimer = setTimeout(() => { note.textContent = ''; }, 8000); };
    function send(msg) { if (ws && ws.readyState === 1) ws.send(JSON.stringify(msg)); }

    function fit() {
        const r = stage.getBoundingClientRect();
        const s = Math.min(r.width / vw, r.height / vh) || 1;
        canvas.style.width = `${Math.floor(vw * s)}px`;
        canvas.style.height = `${Math.floor(vh * s)}px`;
    }
    const ro = new ResizeObserver(fit);
    ro.observe(stage);

    async function draw(blob) {
        if (decoding) { nextBlob = blob; return; }
        decoding = true;
        try {
            const img = await createImageBitmap(blob);
            if (canvas.width !== img.width || canvas.height !== img.height) { canvas.width = img.width; canvas.height = img.height; }
            ctx.drawImage(img, 0, 0);
            img.close?.();
        } catch { /* a torn frame: the next one replaces it */ }
        decoding = false;
        if (nextBlob) { const b = nextBlob; nextBlob = null; draw(b); }
    }

    function onState(s) {
        sleep.style.display = s.asleep ? 'flex' : 'none';
        if (s.asleep) { chip.textContent = 'asleep'; tabs.replaceChildren(); return; }
        if (s.width && s.height && (s.width !== vw || s.height !== vh)) { vw = s.width; vh = s.height; fit(); }
        if (document.activeElement !== addr) addr.value = s.url || '';
        chip.textContent = s.guarded ? 'live · rails on' : 'live · rails off';
        tabs.replaceChildren(...(s.tabs || []).map((t, i) =>
            el('button', { type: 'button', class: 'bv-tab' + (t.active ? ' on' : ''), title: t.url, onclick: () => send({ t: 'tab', index: i }) },
                t.title || t.url || 'tab')));
    }

    function connect() {
        if (!alive || document.hidden) return;
        clearTimeout(retryTimer);
        chip.onclick = null;
        chip.style.cursor = '';
        const proto = location.protocol === 'https:' ? 'wss' : 'ws';
        ws = new WebSocket(`${proto}://${location.host}/ws/plugin/browser/view`);
        ws.binaryType = 'blob';
        ws.onopen = () => { retry = 0; chip.textContent = 'live'; };
        ws.onmessage = (e) => {
            if (typeof e.data !== 'string') { draw(e.data); return; }
            let m; try { m = JSON.parse(e.data); } catch { return; }
            if (m.t === 'state') onState(m); else if (m.t === 'note') say(m.text);
        };
        ws.onclose = (e) => {
            if (!alive || ws !== e.target) return;
            if (document.hidden) { chip.textContent = 'paused'; return; }    // hidden tab: watching stops, Chromium may sleep
            if (e.code === 4410) reloadUntil = Date.now() + 15000;      // the plugin is reloading: it comes back
            const why = { 4401: 'not logged in: reload the page', 4404: 'the Browser plugin is off', 4429: 'too many viewers open' }[e.code];
            if (why && Date.now() > reloadUntil) {
                chip.textContent = `${why} · retry`;
                chip.style.cursor = 'pointer';
                chip.onclick = () => { retry = 0; connect(); };
                return;
            }
            chip.textContent = 'reconnecting...';
            retryTimer = setTimeout(connect, Math.min(5000, 500 * 2 ** retry++));
        };
    }

    // --- your hands in her page -----------------------------------------------------
    const at = (e) => {
        const r = canvas.getBoundingClientRect();
        return { x: (e.clientX - r.left) * vw / r.width, y: (e.clientY - r.top) * vh / r.height };
    };
    const BUTTONS = ['left', 'middle', 'right'];
    let touch = null, moveMsg = null, moveQueued = false;

    canvas.addEventListener('pointerdown', (e) => {
        canvas.focus();
        canvas.setPointerCapture(e.pointerId);
        const p = at(e);
        if (e.pointerType === 'touch') { touch = { x: e.clientX, y: e.clientY, sx: e.clientX, sy: e.clientY, p }; return; }
        send({ t: 'mouse', kind: 'down', ...p, button: BUTTONS[e.button] || 'left' });
    });
    canvas.addEventListener('pointermove', (e) => {
        const p = at(e);
        if (e.pointerType === 'touch') {
            if (!touch) return;
            const r = canvas.getBoundingClientRect();
            send({ t: 'mouse', kind: 'wheel', ...touch.p, dx: (touch.x - e.clientX) * vw / r.width, dy: (touch.y - e.clientY) * vh / r.height });
            touch.x = e.clientX; touch.y = e.clientY;
            return;
        }
        moveMsg = { t: 'mouse', kind: 'move', ...p };
        if (!moveQueued) { moveQueued = true; requestAnimationFrame(() => { moveQueued = false; if (moveMsg) send(moveMsg); moveMsg = null; }); }
    });
    canvas.addEventListener('pointerup', (e) => {
        const p = at(e);
        if (e.pointerType === 'touch') {
            if (touch && Math.hypot(e.clientX - touch.sx, e.clientY - touch.sy) < 8) {
                send({ t: 'mouse', kind: 'down', ...p }); send({ t: 'mouse', kind: 'up', ...p });
            }
            touch = null;
            return;
        }
        send({ t: 'mouse', kind: 'up', ...p, button: BUTTONS[e.button] || 'left' });
    });
    canvas.addEventListener('wheel', (e) => {
        e.preventDefault();
        const k = e.deltaMode === 1 ? 40 : e.deltaMode === 2 ? vh : 1;
        send({ t: 'mouse', kind: 'wheel', ...at(e), dx: e.deltaX * k, dy: e.deltaY * k });
    }, { passive: false });
    canvas.addEventListener('contextmenu', (e) => e.preventDefault());
    canvas.addEventListener('keydown', (e) => {
        if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'v') return;   // the paste event brings the text
        e.preventDefault();
        if (e.key === 'F5' || ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'r')) {
            send({ t: 'nav', what: 'reload' });                           // reloads her page, not Sapphire
            return;
        }
        held.add(e.key);
        send({ t: 'key', kind: 'down', key: e.key });
    });
    canvas.addEventListener('keyup', (e) => {
        e.preventDefault();
        held.delete(e.key);
        send({ t: 'key', kind: 'up', key: e.key });
    });
    canvas.addEventListener('blur', () => {                          // no Shift left stuck down in her page
        for (const k of held) send({ t: 'key', kind: 'up', key: k });
        held.clear();
    });
    const onPaste = (e) => {
        if (document.activeElement !== canvas) return;
        const text = e.clipboardData?.getData('text');
        if (text) { e.preventDefault(); send({ t: 'text', text }); }
    };
    document.addEventListener('paste', onPaste);
    wakeBtn.addEventListener('click', () => send({ t: 'wake' }));
    // A hidden tab lets go of the socket: watching keeps her Chromium awake
    // and streaming, and a forgotten tab would do that forever.
    const onVisible = () => {
        if (document.hidden) { try { ws?.close(1000); } catch {} }
        else if (!ws || ws.readyState > 1) { retry = 0; connect(); }
    };
    document.addEventListener('visibilitychange', onVisible);

    function forget() {
        if (!confirm('Clear her browser profile?\n\nEvery login she has (and any you made here), site data, history and cache are deleted. Her browser starts fresh.')) return;
        send({ t: 'forget' });
    }

    connect();
    fit();
    cleanupFn = () => {
        alive = false;
        clearTimeout(retryTimer);
        clearTimeout(noteTimer);
        ro.disconnect();
        document.removeEventListener('paste', onPaste);
        document.removeEventListener('visibilitychange', onVisible);
        try { ws?.close(1000); } catch {}
        cleanupFn = null;
    };
    return cleanupFn;
}

export function cleanup() { cleanupFn?.(); }
