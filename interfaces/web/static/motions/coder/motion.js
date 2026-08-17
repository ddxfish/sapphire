// static/motions/coder/motion.js — glyph rain, tinted from the theme trim.
// Steps on a fixed cadence inside rAF; trails via destination-out fade.
// Motion contract (themes-v2 P3): default export { id, name, mount, unmount }.
// settings.speed / settings.intensity = global user multipliers.

function themeRGB(varName, fallback) {
    const v = getComputedStyle(document.documentElement).getPropertyValue(varName).trim();
    const m = v.match(/^#([0-9a-f]{6})$/i);
    if (!m) return fallback;
    const n = parseInt(m[1], 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

const GLYPHS = 'ｱｲｳｴｵｶｷｸｹｺｻｼｽｾｿﾀﾁﾂﾃﾄﾅﾆﾇﾈﾉ0123456789Z:・.=*+-<>';
const CELL = 14;

let canvas, ctx, ro, raf = 0, drops = [], active = [], W = 0, H = 0, INT = 1;

function resize(host) {
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    W = host.clientWidth; H = host.clientHeight;
    canvas.width = Math.max(1, Math.round(W * dpr));
    canvas.height = Math.max(1, Math.round(H * dpr));
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const cols = Math.ceil(W / CELL);
    drops = Array.from({ length: cols }, () => -Math.floor(Math.random() * 40));
    // intensity thins the active columns (deterministic per column)
    const frac = Math.min(1, 0.62 * INT);
    active = Array.from({ length: cols }, (_, i) => ((i * 2654435761) % 100) / 100 < frac);
}

export default {
    id: 'coder', name: 'Coder',
    mount(host, settings) {
        const SPD = +((settings || {}).speed) || 1;
        INT = +((settings || {}).intensity) || 1;
        canvas = document.createElement('canvas');
        ctx = canvas.getContext('2d');
        host.appendChild(canvas);
        const [r, g, b] = themeRGB('--trim', [51, 255, 51]);
        resize(host);
        ro = new ResizeObserver(() => resize(host));
        ro.observe(host);
        const stepEvery = 0.09 / SPD;
        let last = performance.now(), acc = 0;
        const tick = (now) => {
            acc += Math.min((now - last) / 1000, 0.25);
            last = now;
            while (acc >= stepEvery) {
                acc -= stepEvery;
                ctx.globalCompositeOperation = 'destination-out';
                ctx.fillStyle = 'rgba(0,0,0,0.14)';
                ctx.fillRect(0, 0, W, H);
                ctx.globalCompositeOperation = 'source-over';
                ctx.font = `${CELL}px monospace`;
                for (let i = 0; i < drops.length; i++) {
                    if (!active[i]) continue;
                    const y = drops[i] * CELL;
                    if (y > 0 && y < H + CELL) {
                        const ch = GLYPHS[(Math.random() * GLYPHS.length) | 0];
                        const head = Math.random() < 0.12;
                        ctx.fillStyle = head
                            ? `rgba(${Math.min(255, r + 90)},${Math.min(255, g + 90)},${Math.min(255, b + 90)},0.9)`
                            : `rgba(${r},${g},${b},0.55)`;
                        ctx.fillText(ch, i * CELL, y);
                    }
                    drops[i]++;
                    if (y > H && Math.random() < 0.025) drops[i] = -Math.floor(Math.random() * 20);
                }
            }
            raf = requestAnimationFrame(tick);
        };
        raf = requestAnimationFrame(tick);
    },
    unmount() {
        cancelAnimationFrame(raf);
        if (ro) ro.disconnect();
        if (canvas) canvas.remove();
        canvas = ctx = ro = null;
        drops = []; active = [];
    },
};
