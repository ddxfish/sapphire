// static/motions/nibblers/motion.js — light trails racing the grid: cycles
// in theme-derived colors, right-angle turns, fading wakes (canvas
// persistence via destination-out fade — no history buffers).
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

const DIRS = [[1, 0], [0, 1], [-1, 0], [0, -1]];

let canvas, ctx, ro, raf = 0, cycles = [], W = 0, H = 0;

function spawnCycle(hue) {
    return {
        x: Math.random() * W, y: Math.random() * H,
        dir: Math.floor(Math.random() * 4),
        turnIn: 0.5 + Math.random() * 2,
        hue,
    };
}

function resize(host) {
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    W = host.clientWidth; H = host.clientHeight;
    canvas.width = Math.max(1, Math.round(W * dpr));
    canvas.height = Math.max(1, Math.round(H * dpr));
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
}

export default {
    id: 'nibblers', name: 'Nibblers',
    mount(host, settings) {
        const SPD = +((settings || {}).speed) || 1;
        const INT = +((settings || {}).intensity) || 1;
        canvas = document.createElement('canvas');
        ctx = canvas.getContext('2d');
        host.appendChild(canvas);
        const baseHue = rgbToHue(themeRGB('--trim', [74, 158, 255]));
        resize(host);
        const n = Math.max(1, Math.min(6, Math.round(3 * INT)));
        cycles = Array.from({ length: n }, (_, i) => spawnCycle((baseHue + i * (360 / n)) % 360));
        ro = new ResizeObserver(() => resize(host));
        ro.observe(host);
        let last = performance.now();
        const tick = (now) => {
            const dt = Math.min((now - last) / 1000, 0.1);
            last = now;
            ctx.globalCompositeOperation = 'destination-out';
            ctx.fillStyle = `rgba(0,0,0,${Math.min(1, 1.6 * dt).toFixed(4)})`;
            ctx.fillRect(0, 0, W, H);
            ctx.globalCompositeOperation = 'source-over';
            const v = 70 * SPD;
            for (const c of cycles) {
                c.turnIn -= dt;
                if (c.turnIn <= 0) {
                    c.dir = (c.dir + (Math.random() < 0.5 ? 1 : 3)) % 4;  // 90° left/right
                    c.turnIn = 0.5 + Math.random() * 2;
                }
                const [dx, dy] = DIRS[c.dir];
                const nx = c.x + dx * v * dt, ny = c.y + dy * v * dt;
                if (nx < 0 || nx > W || ny < 0 || ny > H) {
                    Object.assign(c, spawnCycle(c.hue));   // respawn, no cross-screen streak
                    continue;
                }
                ctx.strokeStyle = `hsla(${c.hue.toFixed(0)}, 85%, 60%, 0.8)`;
                ctx.lineWidth = 2;
                ctx.lineCap = 'round';
                ctx.beginPath();
                ctx.moveTo(c.x, c.y);
                ctx.lineTo(nx, ny);
                ctx.stroke();
                ctx.fillStyle = `hsla(${c.hue.toFixed(0)}, 90%, 78%, 0.95)`;
                ctx.beginPath();
                ctx.arc(nx, ny, 2.2, 0, Math.PI * 2);
                ctx.fill();
                c.x = nx; c.y = ny;
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
        cycles = [];
    },
};
