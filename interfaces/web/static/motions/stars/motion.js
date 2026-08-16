// static/motions/stars/motion.js — slow-twinkling starfield.
// Motion contract (themes-v2 P3): default export { id, name, mount, unmount }.
// The host (core/motions.js) guarantees: mounted only while visible (tab +
// viewport), never under a scene image, never with prefers-reduced-motion set,
// and remounted on theme switch — so colors sampled at mount stay honest.

function themeRGB(varName, fallback) {
    const v = getComputedStyle(document.documentElement).getPropertyValue(varName).trim();
    const m = v.match(/^#([0-9a-f]{6})$/i);
    if (!m) return fallback;
    const n = parseInt(m[1], 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

let canvas, ctx, ro, raf = 0, stars = [], W = 0, H = 0;

function seed() {
    const count = Math.min(180, Math.round((W * H) / 9000));
    stars = Array.from({ length: count }, () => ({
        x: Math.random() * W, y: Math.random() * H,
        r: 0.4 + Math.random() * 1.3,
        tw: 0.3 + Math.random() * 1.2,
        ph: Math.random() * Math.PI * 2,
        v: 1.5 + Math.random() * 3.5,
        glint: Math.random() < 0.12,
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
    id: 'stars', name: 'Stars',
    mount(host) {
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
            const dt = Math.min((now - last) / 1000, 0.1);
            last = now;
            ctx.clearRect(0, 0, W, H);
            const t = now / 1000;
            for (const s of stars) {
                s.y += s.v * dt;
                if (s.y > H + 2) { s.y = -2; s.x = Math.random() * W; }
                const a = 0.25 + 0.55 * (0.5 + 0.5 * Math.sin(t * s.tw + s.ph));
                const [r, g, b] = s.glint ? trim : text;
                ctx.fillStyle = `rgba(${r},${g},${b},${a.toFixed(3)})`;
                ctx.beginPath();
                ctx.arc(s.x, s.y, s.r, 0, Math.PI * 2);
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
        stars = [];
    },
};
