// static/motions/fireflies/motion.js — a few wandering points of glow that
// pulse as they roam. Tinted from --trim at mount (host remounts on theme
// switch). Motion contract (themes-v2 P3): default export
// { id, name, mount, unmount }.

function themeRGB(varName, fallback) {
    const v = getComputedStyle(document.documentElement).getPropertyValue(varName).trim();
    const m = v.match(/^#([0-9a-f]{6})$/i);
    if (!m) return fallback;
    const n = parseInt(m[1], 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

let canvas, ctx, ro, raf = 0, flies = [], W = 0, H = 0, INT = 1;

function retarget(f) {
    f.tx = Math.random() * W;
    f.ty = Math.random() * H;
    f.until = performance.now() + 2000 + Math.random() * 4000;
}

function seed() {
    const count = Math.max(3, Math.min(28, Math.round((W * H) / 60000 * INT)));
    flies = Array.from({ length: count }, () => {
        const f = {
            x: Math.random() * W, y: Math.random() * H,
            vx: 0, vy: 0,
            pulse: 0.5 + Math.random() * 0.9,
            ph: Math.random() * Math.PI * 2,
        };
        retarget(f);
        return f;
    });
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
    id: 'fireflies', name: 'Fireflies',
    mount(host, settings) {
        const SPD = +((settings || {}).speed) || 1;
        INT = +((settings || {}).intensity) || 1;
        canvas = document.createElement('canvas');
        ctx = canvas.getContext('2d');
        host.appendChild(canvas);
        const [r, g, b] = themeRGB('--trim', [212, 160, 32]);
        resize(host);
        ro = new ResizeObserver(() => resize(host));
        ro.observe(host);
        let last = performance.now();
        const tick = (now) => {
            const dt = Math.min((now - last) / 1000, 0.1) * SPD;
            last = now;
            ctx.clearRect(0, 0, W, H);
            const t = now / 1000;
            for (const f of flies) {
                if (now > f.until) retarget(f);
                // ease velocity toward the current target — smooth wandering
                f.vx += ((f.tx - f.x) * 0.15 - f.vx) * (0.8 * dt);
                f.vy += ((f.ty - f.y) * 0.15 - f.vy) * (0.8 * dt);
                f.x += f.vx * dt;
                f.y += f.vy * dt;
                const a = 0.12 + 0.4 * Math.max(0, Math.sin(t * f.pulse * Math.PI + f.ph));
                const glow = ctx.createRadialGradient(f.x, f.y, 0, f.x, f.y, 10);
                glow.addColorStop(0, `rgba(${r},${g},${b},${a.toFixed(3)})`);
                glow.addColorStop(1, `rgba(${r},${g},${b},0)`);
                ctx.fillStyle = glow;
                ctx.beginPath();
                ctx.arc(f.x, f.y, 10, 0, Math.PI * 2);
                ctx.fill();
                ctx.fillStyle = `rgba(${r},${g},${b},${Math.min(1, a * 2.2).toFixed(3)})`;
                ctx.beginPath();
                ctx.arc(f.x, f.y, 1.6, 0, Math.PI * 2);
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
        flies = [];
    },
};
