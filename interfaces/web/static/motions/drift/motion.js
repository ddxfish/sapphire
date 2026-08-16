// static/motions/drift/motion.js — soft motes drifting upward. The Abyss
// theme's bundle default (bioluminescent plankton); tinted from --trim at
// mount — the host remounts on theme switch so the color always tracks.
// Motion contract (themes-v2 P3): default export { id, name, mount, unmount }.

function themeRGB(varName, fallback) {
    const v = getComputedStyle(document.documentElement).getPropertyValue(varName).trim();
    const m = v.match(/^#([0-9a-f]{6})$/i);
    if (!m) return fallback;
    const n = parseInt(m[1], 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

let canvas, ctx, ro, raf = 0, motes = [], W = 0, H = 0;

function spawn(fromBottom) {
    return {
        x: Math.random() * W,
        y: fromBottom ? H + 6 : Math.random() * H,
        r: 1 + Math.random() * 2.5,
        v: 6 + Math.random() * 14,
        amp: 4 + Math.random() * 14,
        freq: 0.2 + Math.random() * 0.5,
        ph: Math.random() * Math.PI * 2,
        a: 0.10 + Math.random() * 0.25,
    };
}

function seed() {
    const count = Math.min(70, Math.round((W * H) / 22000));
    motes = Array.from({ length: count }, () => spawn(false));
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
    id: 'drift', name: 'Drift',
    mount(host) {
        canvas = document.createElement('canvas');
        ctx = canvas.getContext('2d');
        host.appendChild(canvas);
        const [r, g, b] = themeRGB('--trim', [64, 200, 224]);
        resize(host);
        ro = new ResizeObserver(() => resize(host));
        ro.observe(host);
        let last = performance.now();
        const tick = (now) => {
            const dt = Math.min((now - last) / 1000, 0.1);
            last = now;
            ctx.clearRect(0, 0, W, H);
            const t = now / 1000;
            for (let i = 0; i < motes.length; i++) {
                const m = motes[i];
                m.y -= m.v * dt;
                if (m.y < -8) motes[i] = spawn(true);
                const x = m.x + Math.sin(t * m.freq * Math.PI * 2 + m.ph) * m.amp;
                const glow = ctx.createRadialGradient(x, m.y, 0, x, m.y, m.r * 3);
                glow.addColorStop(0, `rgba(${r},${g},${b},${m.a.toFixed(3)})`);
                glow.addColorStop(1, `rgba(${r},${g},${b},0)`);
                ctx.fillStyle = glow;
                ctx.beginPath();
                ctx.arc(x, m.y, m.r * 3, 0, Math.PI * 2);
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
        motes = [];
    },
};
