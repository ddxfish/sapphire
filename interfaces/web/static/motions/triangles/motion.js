// static/motions/triangles/motion.js — bouncing ribbon triangles with slowly
// cycling color, trailing via canvas persistence (destination-out fade).
// Motion contract (themes-v2 P3): default export { id, name, mount, unmount }.
// settings.speed / settings.intensity = global user multipliers.

function themeRGB(varName, fallback) {
    const v = getComputedStyle(document.documentElement).getPropertyValue(varName).trim();
    const m = v.match(/^#([0-9a-f]{6})$/i);
    if (!m) return fallback;
    const n = parseInt(m[1], 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

function rgbToHue([r, g, b]) {
    r /= 255; g /= 255; b /= 255;
    const max = Math.max(r, g, b), min = Math.min(r, g, b), d = max - min;
    if (!d) return 200;
    let h;
    if (max === r) h = ((g - b) / d) % 6;
    else if (max === g) h = (b - r) / d + 2;
    else h = (r - g) / d + 4;
    return ((h * 60) + 360) % 360;
}

let canvas, ctx, ro, raf = 0, polys = [], W = 0, H = 0, INT = 1;

function makePoly() {
    return Array.from({ length: 3 }, () => ({
        x: Math.random() * W, y: Math.random() * H,
        vx: (Math.random() < 0.5 ? -1 : 1) * (40 + Math.random() * 50),
        vy: (Math.random() < 0.5 ? -1 : 1) * (40 + Math.random() * 50),
    }));
}

function resize(host) {
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    W = host.clientWidth; H = host.clientHeight;
    canvas.width = Math.max(1, Math.round(W * dpr));
    canvas.height = Math.max(1, Math.round(H * dpr));
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const n = Math.max(1, Math.min(4, Math.round(2 * INT)));
    polys = Array.from({ length: n }, makePoly);
}

export default {
    id: 'triangles', name: 'Triangles',
    mount(host, settings) {
        const SPD = +((settings || {}).speed) || 1;
        INT = +((settings || {}).intensity) || 1;
        canvas = document.createElement('canvas');
        ctx = canvas.getContext('2d');
        host.appendChild(canvas);
        const baseHue = rgbToHue(themeRGB('--trim', [74, 158, 255]));
        resize(host);
        ro = new ResizeObserver(() => resize(host));
        ro.observe(host);
        let last = performance.now();
        const tick = (now) => {
            const dt = Math.min((now - last) / 1000, 0.1) * SPD;
            last = now;
            ctx.globalCompositeOperation = 'destination-out';
            ctx.fillStyle = `rgba(0,0,0,${Math.min(1, 1.1 * dt).toFixed(4)})`;
            ctx.fillRect(0, 0, W, H);
            ctx.globalCompositeOperation = 'source-over';
            const t = now / 1000;
            polys.forEach((pts, i) => {
                for (const p of pts) {
                    p.x += p.vx * dt; p.y += p.vy * dt;
                    if (p.x < 0) { p.x = 0; p.vx = Math.abs(p.vx); }
                    if (p.x > W) { p.x = W; p.vx = -Math.abs(p.vx); }
                    if (p.y < 0) { p.y = 0; p.vy = Math.abs(p.vy); }
                    if (p.y > H) { p.y = H; p.vy = -Math.abs(p.vy); }
                }
                const hue = (baseHue + t * 14 * SPD + i * (360 / polys.length)) % 360;
                ctx.strokeStyle = `hsla(${hue.toFixed(0)}, 80%, 62%, 0.55)`;
                ctx.lineWidth = 1.2;
                ctx.beginPath();
                ctx.moveTo(pts[0].x, pts[0].y);
                for (let k = 1; k < pts.length; k++) ctx.lineTo(pts[k].x, pts[k].y);
                ctx.closePath();
                ctx.stroke();
            });
            raf = requestAnimationFrame(tick);
        };
        raf = requestAnimationFrame(tick);
    },
    unmount() {
        cancelAnimationFrame(raf);
        if (ro) ro.disconnect();
        if (canvas) canvas.remove();
        canvas = ctx = ro = null;
        polys = [];
    },
};
