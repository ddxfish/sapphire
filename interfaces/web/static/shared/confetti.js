// shared/confetti.js - a burst of confetti over the page (the setup wizard's
// last step). Two cannons from the bottom corners on one fixed canvas that
// removes itself when the burst is over. Under prefers-reduced-motion the
// pieces drift down calmly instead (fewer, slower, no cannons), and the
// console says so: a celebration that never shows is a bug report waiting.
export function burst({ count = 180, ms = 2800 } = {}) {
    const calm = !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    if (calm) { count = Math.min(count, 50); ms = Math.max(ms, 3200); console.info('[confetti] reduced motion: a calm fall'); }
    const c = document.createElement('canvas');
    c.className = 'confetti-canvas';
    document.body.appendChild(c);
    const ctx = c.getContext('2d');
    const dpr = window.devicePixelRatio || 1;
    c.width = innerWidth * dpr; c.height = innerHeight * dpr;
    const colors = ['#4fa3ff', '#ffd166', '#ef476f', '#06d6a0', '#b388ff', '#ffffff'];
    const primary = getComputedStyle(document.documentElement).getPropertyValue('--primary').trim();
    if (primary && primary !== 'transparent') colors.push(primary, primary);
    const ps = Array.from({ length: count }, (_, i) => {
        const left = i % 2 === 0;
        const p = {
            w: (6 + Math.random() * 6) * dpr, h: (3 + Math.random() * 4) * dpr,
            a: Math.random() * Math.PI, va: (Math.random() - 0.5) * 0.3,
            color: colors[Math.floor(Math.random() * colors.length)],
        };
        if (calm) Object.assign(p, { x: Math.random() * c.width, y: -Math.random() * c.height * 0.6,
                                     vx: (Math.random() - 0.5) * 0.6 * dpr, vy: (2 + Math.random() * 2) * dpr });
        else Object.assign(p, { x: c.width * (left ? 0.06 : 0.94), y: c.height * 0.85,
                                vx: (left ? 1 : -1) * (6 + Math.random() * 14) * dpr, vy: -(14 + Math.random() * 14) * dpr });
        return p;
    });
    const g = calm ? 0 : 0.42 * dpr, t0 = performance.now();
    function frame(t) {
        const k = (t - t0) / ms;
        ctx.clearRect(0, 0, c.width, c.height);
        ctx.globalAlpha = k > 0.7 ? Math.max(0, 1 - (k - 0.7) / 0.3) : 1;
        for (const p of ps) {
            p.vy += g; p.vx *= 0.985; p.x += p.vx; p.y += p.vy; p.a += p.va;
            ctx.save(); ctx.translate(p.x, p.y); ctx.rotate(p.a);
            ctx.fillStyle = p.color; ctx.fillRect(-p.w / 2, -p.h / 2, p.w, p.h);
            ctx.restore();
        }
        if (k < 1) requestAnimationFrame(frame); else c.remove();
    }
    requestAnimationFrame(frame);
}
