// static/motions/snow/motion.js — falling flakes with a soft twinkle and a
// gentle sway. (Born as "Stars" in P3; Krem correctly identified it as snow.)
// Motion contract (themes-v2 P3): default export { id, name, mount, unmount }.
// settings.speed = global user multiplier. Colors sampled at mount — the host
// remounts on theme switch.

function themeRGB(varName, fallback) {
    const v = getComputedStyle(document.documentElement).getPropertyValue(varName).trim();
    const m = v.match(/^#([0-9a-f]{6})$/i);
    if (!m) return fallback;
    const n = parseInt(m[1], 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

let canvas, ctx, ro, raf = 0, flakes = [], W = 0, H = 0, INT = 1;

function seed() {
    const count = Math.min(320, Math.round((W * H) / 9000 * INT));
    flakes = Array.from({ length: count }, () => ({
        x: Math.random() * W, y: Math.random() * H,
        r: 0.6 + Math.random() * 1.6,
        tw: 0.3 + Math.random() * 1.0,
        ph: Math.random() * Math.PI * 2,
        v: 8 + Math.random() * 12,
        amp: 3 + Math.random() * 8,
        freq: 0.15 + Math.random() * 0.35,
        glint: Math.random() < 0.1,
    }));
}

function resize(host) {
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    W = host.clientWidth; H = host.clientHeight;
    canvas.width = Math.max(1, Math.round(W * dpr));
    canvas.height = Math.max(1, Math.round(H * dpr));
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    seed();
}

export default {
    id: 'snow', name: 'Snow',
    mount(host, settings) {
        const SPD = +((settings || {}).speed) || 1;
        INT = +((settings || {}).intensity) || 1;
        canvas = document.createElement('canvas');
        ctx = canvas.getContext('2d');
        host.appendChild(canvas);
        const text = themeRGB('--text', [224, 224, 224]);
        const trim = themeRGB('--trim', [74, 158, 255]);
        resize(host);
        ro = new ResizeObserver(() => resize(host));
        ro.observe(host);
        let last = performance.now();
        const tick = (now) => {
            const dt = Math.min((now - last) / 1000, 0.1) * SPD;
            last = now;
            ctx.clearRect(0, 0, W, H);
            const t = now / 1000;
            for (const f of flakes) {
                f.y += f.v * dt;
                if (f.y > H + 2) { f.y = -2; f.x = Math.random() * W; }
                const x = f.x + Math.sin(t * f.freq * Math.PI * 2 * SPD + f.ph) * f.amp;
                const a = 0.3 + 0.5 * (0.5 + 0.5 * Math.sin(t * f.tw * SPD + f.ph));
                const [r, g, b] = f.glint ? trim : text;
                ctx.fillStyle = `rgba(${r},${g},${b},${a.toFixed(3)})`;
                ctx.beginPath();
                ctx.arc(x, f.y, f.r, 0, Math.PI * 2);
                ctx.fill();
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
        flakes = [];
    },
};
