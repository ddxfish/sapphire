// static/motions/stars/motion.js — slow flight through a starfield.
// Classic pseudo-3D: stars carry (x, y, z); z shrinks, points stream outward
// from center and brighten as they approach. A drift, not a warp.
// Motion contract (themes-v2 P3): default export { id, name, mount, unmount }.
// settings.speed / settings.intensity = global user multipliers.

function themeRGB(varName, fallback) {
    const v = getComputedStyle(document.documentElement).getPropertyValue(varName).trim();
    const m = v.match(/^#([0-9a-f]{6})$/i);
    if (!m) return fallback;
    const n = parseInt(m[1], 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

let canvas, ctx, ro, raf = 0, stars = [], W = 0, H = 0, INT = 1;

function spawn(s) {
    s.x = (Math.random() * 2 - 1);
    s.y = (Math.random() * 2 - 1);
    s.z = 0.3 + Math.random() * 0.7;
    return s;
}

function seed() {
    const count = Math.min(400, Math.round((W * H) / 7000 * INT));
    stars = Array.from({ length: count }, () => spawn({}));
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
    mount(host, settings) {
        const SPD = +((settings || {}).speed) || 1;
        INT = +((settings || {}).intensity) || 1;
        canvas = document.createElement('canvas');
        ctx = canvas.getContext('2d');
        host.appendChild(canvas);
        const [r, g, b] = themeRGB('--text', [224, 224, 224]);
        resize(host);
        ro = new ResizeObserver(() => resize(host));
        ro.observe(host);
        let last = performance.now();
        const tick = (now) => {
            const dt = Math.min((now - last) / 1000, 0.1);
            last = now;
            ctx.clearRect(0, 0, W, H);
            const cx = W / 2, cy = H / 2;
            const scale = Math.min(W, H) * 0.5;
            for (const s of stars) {
                s.z -= 0.035 * dt * SPD;             // ~20s+ center-to-edge
                if (s.z <= 0.05) spawn(s);
                const px = cx + (s.x / s.z) * scale;
                const py = cy + (s.y / s.z) * scale;
                if (px < -4 || px > W + 4 || py < -4 || py > H + 4) { spawn(s); continue; }
                const depth = 1 - s.z;
                const a = 0.15 + depth * 0.65;
                ctx.fillStyle = `rgba(${r},${g},${b},${a.toFixed(3)})`;
                ctx.beginPath();
                ctx.arc(px, py, 0.4 + depth * 1.8, 0, Math.PI * 2);
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
