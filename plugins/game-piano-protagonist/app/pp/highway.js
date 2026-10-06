// highway.js — the note highway: bars fall onto a drawn keyboard and land on
// the line the instant they are due. Everything is drawn from TIME, not from
// frame counts: frame(now) places every bar from the lane clock, so a dropped
// frame slides nothing. Colour is by pitch class (every C is C-coloured).
//
// What it says about the playing is kind only: a caught bar glows and pops, a
// clean catch throws a few sparks, and a scored pass lands its number in the
// corner with a burst. A miss draws nothing.

const HUES = [0, 30, 55, 80, 120, 165, 190, 215, 245, 275, 305, 335];
const BLACK = new Set([1, 3, 6, 8, 10]);
const NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B'];
const PARTY = 2200;                              // ms the corner number stays rainbow

export function makeHighway(canvas) {
    const cx = canvas.getContext('2d');
    let W = 0, H = 0, dpr = 1;
    let lo = 48, hi = 83, nWhite = 21, whiteW = 20, kbH = 90, hitY = 0;
    let slots = [], labels = true, guide = false, lead = 2200, windowMs = 110;
    let startT = 0, endT = 0, countdown = false;
    const lit = new Map();                       // note -> {since, kind}
    const fx = [];                               // transient effects
    let hud = null;                              // the corner number: {value, from, shown, t0}
    let nowFn = () => 0;

    function resize() {
        const r = canvas.parentElement.getBoundingClientRect();
        W = Math.max(200, r.width); H = Math.max(200, r.height);
        dpr = window.devicePixelRatio || 1;
        canvas.width = W * dpr; canvas.height = H * dpr;
        canvas.style.width = W + 'px'; canvas.style.height = H + 'px';
        cx.setTransform(dpr, 0, 0, dpr, 0, 0);
        layout();
    }

    function layout() {
        kbH = Math.max(64, Math.min(120, H * 0.17));
        hitY = H - kbH;
        nWhite = 0;
        for (let n = lo; n <= hi; n++) if (!BLACK.has(n % 12)) nWhite++;
        whiteW = W / nWhite;
    }

    const MIN_SPAN = 48;                         // four octaves, a 49-key keyboard
    let caps = {};                               // note -> key cap drawn on the key

    // The key range: whole octaves around the song, at least four, grown
    // on both sides in turn so the song sits in the middle. A range that
    // tops out on a C ends the keyboard on that C, as pianos do.
    function setRange(min, max) {
        lo = Math.floor(min / 12) * 12;
        hi = max % 12 === 0 ? max : Math.ceil((max + 1) / 12) * 12 - 1;
        let side = 0;
        while (hi - lo < MIN_SPAN) {
            if (side++ % 2 === 0 && lo >= 24) lo -= 12;
            else if (hi <= 96) hi += 12;
            else lo -= 12;
        }
        layout();
    }

    function setCaps(map) { caps = map || {}; }

    function whiteIndex(n) {
        let i = 0;
        for (let k = lo; k < n; k++) if (!BLACK.has(k % 12)) i++;
        return i;
    }

    function keyRect(n) {
        if (n < lo || n > hi) return null;
        if (BLACK.has(n % 12)) {
            const x = whiteIndex(n) * whiteW - whiteW * 0.3;
            return { x, w: whiteW * 0.6, black: true };
        }
        return { x: whiteIndex(n) * whiteW, w: whiteW, black: false };
    }

    const hue = (n) => HUES[n % 12];
    const color = (n, a = 1, l = 55) => `hsla(${hue(n)}, 80%, ${l}%, ${a})`;

    // The song on the stage: its key range and how its bars read. The bars
    // themselves come pass by pass (setPass).
    function setLesson(parsed, opts) {
        labels = opts.labels !== false;
        lead = opts.lead; windowMs = opts.window;
        const ns = parsed.notes.map(n => n.n);
        setRange(Math.min(...ns), Math.max(...ns));
    }

    // The bars to draw now (the pass that is up and the one falling behind
    // it), and the span the progress line and the count-in read.
    function setPass(list, start, end, count = false) {
        slots = list; startT = start; endT = end; countdown = !!count;
    }

    // The song plays itself: the keys light as its notes sound.
    function setGuide(on) {
        guide = !!on;
        if (!guide) for (const [n, l] of lit) if (l.kind === 'her') lit.delete(n);
    }

    function keyDown(n, kind = 'you') { lit.set(n, { since: nowFn(), kind }); }
    function keyUp(n) { lit.delete(n); }

    function burst(x, y, count, reach = 1) {
        const t0 = performance.now();
        for (let i = 0; i < count; i++) {
            const a = Math.random() * Math.PI * 2, v = (40 + Math.random() * 150) * reach;
            fx.push({ kind: 'spark', x, y, vx: Math.cos(a) * v, vy: Math.sin(a) * v - 50 * reach, t0,
                      life: 600 + Math.random() * 800, hue: Math.random() * 360, size: 1.6 + Math.random() * 2.6 });
        }
    }

    // A caught note: a ring where it landed, and sparks for a clean one.
    function flash(slot, judge) {
        const r = keyRect(slot.note.n); if (!r) return;
        const x = r.x + r.w / 2;
        fx.push({ kind: 'pop', x, y: hitY, t0: performance.now(), n: slot.note.n });
        if (judge === 'perfect') burst(x, hitY - 4, 6, 0.45);
    }

    // The corner number: a pass scored. It counts up from the last one, goes
    // rainbow for a moment and throws sparks, more of them for a higher score.
    // null takes it off the stage.
    function score(value) {
        if (value === null || value === undefined) { hud = null; return; }
        const from = hud ? hud.shown : 0;
        hud = { value, from, shown: from, t0: performance.now() };
        burst(W - 44, 32, 16 + Math.round(value * 0.5));
    }

    function frame(now) {
        nowFn = () => now;
        cx.clearRect(0, 0, W, H);
        const pxMs = hitY / lead;
        // lane guides
        cx.strokeStyle = 'rgba(255,255,255,0.05)';
        cx.lineWidth = 1;
        for (let n = lo; n <= hi; n++) {
            if (n % 12 !== 0) continue;
            const r = keyRect(n);
            cx.beginPath(); cx.moveTo(r.x + 0.5, 0); cx.lineTo(r.x + 0.5, hitY); cx.stroke();
        }
        // the band a key counts in, and the line
        const band = windowMs * pxMs;
        cx.fillStyle = 'rgba(255,255,255,0.04)';
        cx.fillRect(0, hitY - band, W, band);
        cx.fillStyle = 'rgba(255,255,255,0.75)';
        cx.fillRect(0, hitY - 1.5, W, 3);
        // the song lights the keys that are sounding — one pass over every
        // slot first, so a pitch that comes round twice stays lit by the one
        // that is sounding now
        if (guide) {
            const sounding = new Set();
            for (const s of slots) if (s.at <= now && now < s.at + s.note.dur * 1000) sounding.add(s.note.n);
            for (const [n, l] of lit) if (l.kind === 'her' && !sounding.has(n)) lit.delete(n);
            for (const n of sounding) if (lit.get(n)?.kind !== 'you') lit.set(n, { since: now, kind: 'her' });
        }
        // bars
        const chordSpans = new Map();
        for (const s of slots) {
            const r = keyRect(s.note.n); if (!r) continue;
            const yBot = hitY - (s.at - now) * pxMs;
            const h = Math.max(14, s.note.dur * 1000 * pxMs);
            const yTop = yBot - h;
            if (yTop > hitY || yBot < -4) continue;
            const x = r.x + (r.black ? 1 : 3), w = r.w - (r.black ? 2 : 6);
            const drawTop = Math.max(yTop, -4), drawBot = Math.min(yBot, hitY);
            const caught = s.hit !== null;
            if (caught) { cx.shadowColor = color(s.note.n, 1, 70); cx.shadowBlur = 16; }
            cx.fillStyle = color(s.note.n, caught ? 0.95 : 0.8, caught ? 68 : 52);
            round(cx, x, drawTop, w, Math.max(2, drawBot - drawTop), 5);
            cx.fill();
            cx.shadowBlur = 0;
            cx.strokeStyle = caught ? 'rgba(255,255,255,0.85)' : color(s.note.n, 1, 78); cx.lineWidth = 1.5; cx.stroke();
            if (labels && !r.black && h >= 18 && yBot <= hitY) {
                cx.fillStyle = 'rgba(0,0,0,0.75)'; cx.font = `600 ${Math.min(13, whiteW * 0.42)}px system-ui, sans-serif`;
                cx.textAlign = 'center'; cx.textBaseline = 'bottom';
                cx.fillText(s.note.name, x + w / 2, yBot - 3);
            }
            if (s.note.chord) {
                const key = `${s.at}:${s.note.group}`;               // two passes share group numbers
                const sp = chordSpans.get(key) || { x0: Infinity, x1: -Infinity, y: Infinity, name: s.note.chord };
                sp.x0 = Math.min(sp.x0, r.x); sp.x1 = Math.max(sp.x1, r.x + r.w); sp.y = Math.min(sp.y, yTop);
                chordSpans.set(key, sp);
            }
        }
        // chord names ride above their bars
        for (const sp of chordSpans.values()) {
            if (sp.y < 14 || sp.y > hitY) continue;
            cx.font = '700 15px system-ui, sans-serif'; cx.textAlign = 'center'; cx.textBaseline = 'bottom';
            const tw = cx.measureText(sp.name).width + 14, cxm = (sp.x0 + sp.x1) / 2;
            cx.fillStyle = 'rgba(20,22,30,0.85)';
            round(cx, cxm - tw / 2, sp.y - 24, tw, 20, 10); cx.fill();
            cx.fillStyle = 'rgba(255,255,255,0.92)';
            cx.fillText(sp.name, cxm, sp.y - 7);
        }
        keyboard();
        // the count-in before the first bar lands
        if (countdown && now < startT && slots.length) {
            const s = Math.ceil((startT - now) / 1000);
            if (s <= 3) {
                cx.fillStyle = 'rgba(255,255,255,0.85)'; cx.font = '800 72px system-ui, sans-serif';
                cx.textAlign = 'center'; cx.textBaseline = 'middle';
                cx.fillText(String(s), W / 2, hitY * 0.45);
            }
        }
        // progress through this pass
        if (endT > startT) {
            const p = Math.max(0, Math.min(1, (now - startT) / (endT - startT)));
            cx.fillStyle = 'rgba(255,255,255,0.18)'; cx.fillRect(0, 0, W, 3);
            cx.fillStyle = 'rgba(255,255,255,0.7)'; cx.fillRect(0, 0, W * p, 3);
        }
        corner();
        effects();
    }

    function keyboard() {
        const y = hitY + 1;
        // whites
        for (let n = lo; n <= hi; n++) {
            if (BLACK.has(n % 12)) continue;
            const r = keyRect(n);
            const l = lit.get(n);
            cx.fillStyle = l ? (l.kind === 'her' ? color(n, 0.9, 70) : color(n, 1, 60)) : '#ecebe6';
            round(cx, r.x + 1, y, r.w - 2, kbH - 2, 4); cx.fill();
            cx.strokeStyle = 'rgba(0,0,0,0.35)'; cx.lineWidth = 1; cx.stroke();
            if (caps[n]) {
                cx.fillStyle = 'rgba(0,0,0,0.78)'; cx.font = `700 ${Math.min(14, whiteW * 0.5)}px system-ui, sans-serif`;
                cx.textAlign = 'center'; cx.textBaseline = 'bottom';
                cx.fillText(caps[n], r.x + r.w / 2, y + kbH - (n % 12 === 0 ? 20 : 6));
            }
            if (n % 12 === 0) {
                cx.fillStyle = 'rgba(0,0,0,0.5)'; cx.font = `600 ${Math.min(12, whiteW * 0.4)}px system-ui, sans-serif`;
                cx.textAlign = 'center'; cx.textBaseline = 'bottom';
                cx.fillText(`C${n / 12 - 1}`, r.x + r.w / 2, y + kbH - 6);
            }
        }
        // blacks
        for (let n = lo; n <= hi; n++) {
            if (!BLACK.has(n % 12)) continue;
            const r = keyRect(n);
            const l = lit.get(n);
            cx.fillStyle = l ? color(n, 1, 55) : '#1b1d26';
            round(cx, r.x, y, r.w, kbH * 0.62, 3); cx.fill();
            cx.strokeStyle = 'rgba(255,255,255,0.12)'; cx.stroke();
            if (caps[n]) {
                cx.fillStyle = 'rgba(255,255,255,0.75)'; cx.font = `700 ${Math.min(12, r.w * 0.7)}px system-ui, sans-serif`;
                cx.textAlign = 'center'; cx.textBaseline = 'bottom';
                cx.fillText(caps[n], r.x + r.w / 2, y + kbH * 0.62 - 5);
            }
        }
    }

    // The score of the last pass, upper right, like a counter in a game.
    function corner() {
        if (!hud) return;
        const age = performance.now() - hud.t0;
        const k = Math.min(1, age / 700);
        hud.shown = Math.round(hud.from + (hud.value - hud.from) * (1 - Math.pow(1 - k, 3)));
        const swell = 1 + 0.45 * Math.max(0, 1 - age / 350);          // it lands big, then settles
        cx.save();
        cx.translate(W - 16, 14); cx.scale(swell, swell);
        cx.font = '800 30px system-ui, sans-serif'; cx.textAlign = 'right'; cx.textBaseline = 'top';
        const text = String(hud.shown), tw = cx.measureText(text).width;
        cx.fillStyle = 'rgba(12,14,20,0.6)';
        round(cx, -tw - 10, -6, tw + 20, 42, 10); cx.fill();
        if (age < PARTY) {
            const g = cx.createLinearGradient(-tw, 0, 0, 0);
            for (let i = 0; i <= 4; i++) g.addColorStop(i / 4, `hsl(${(age * 0.35 + i * 70) % 360}, 95%, 68%)`);
            cx.fillStyle = g;
        } else cx.fillStyle = 'rgba(255,255,255,0.92)';
        cx.fillText(text, 0, 0);
        cx.restore();
    }

    function effects() {
        const t = performance.now();
        for (let i = fx.length - 1; i >= 0; i--) {
            const f = fx[i];
            const age = t - f.t0;
            if (f.kind === 'pop') {
                if (age > 420) { fx.splice(i, 1); continue; }
                const k = age / 420;
                cx.strokeStyle = color(f.n, 1 - k, 75); cx.lineWidth = 3 * (1 - k) + 1;
                cx.beginPath(); cx.arc(f.x, f.y, 6 + 26 * k, 0, Math.PI * 2); cx.stroke();
            } else if (f.kind === 'spark') {
                if (age > f.life) { fx.splice(i, 1); continue; }
                const k = age / f.life, s = age / 1000;
                const r = f.size * (1 - 0.6 * k) * (0.75 + 0.25 * Math.sin(age / 60 + f.hue));   // it twinkles
                cx.globalCompositeOperation = 'lighter';
                cx.globalAlpha = 1 - k * k;
                cx.fillStyle = `hsl(${(f.hue + age * 0.25) % 360}, 95%, 68%)`;
                star(cx, f.x + f.vx * s, f.y + f.vy * s + 140 * s * s, r);          // a little gravity
                cx.globalAlpha = 1;
                cx.globalCompositeOperation = 'source-over';
            }
        }
    }

    // A four-point sparkle.
    function star(c, x, y, r) {
        const R = r * 2.2;
        c.beginPath();
        c.moveTo(x, y - R); c.quadraticCurveTo(x, y, x + R, y);
        c.quadraticCurveTo(x, y, x, y + R); c.quadraticCurveTo(x, y, x - R, y);
        c.quadraticCurveTo(x, y, x, y - R);
        c.fill();
    }

    function round(c, x, y, w, h, r) {
        r = Math.min(r, w / 2, h / 2);
        c.beginPath();
        c.moveTo(x + r, y); c.lineTo(x + w - r, y); c.quadraticCurveTo(x + w, y, x + w, y + r);
        c.lineTo(x + w, y + h - r); c.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
        c.lineTo(x + r, y + h); c.quadraticCurveTo(x, y + h, x, y + h - r);
        c.lineTo(x, y + r); c.quadraticCurveTo(x, y, x + r, y); c.closePath();
    }

    const ro = new ResizeObserver(resize);
    ro.observe(canvas.parentElement);
    resize();

    return { setLesson, setPass, setGuide, setCaps, frame, keyDown, keyUp, flash, score, resize,
             clear() { slots = []; lit.clear(); fx.length = 0; startT = endT = 0; frame(0); },
             destroy() { ro.disconnect(); }, pitchName: (n) => NAMES[n % 12] };
}
