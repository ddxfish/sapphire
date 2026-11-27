// Her face in a browser: THE player for the avatar feed's browser door (core/avatar_door.py).
// Whichever avatar is her face hands the hub slices of (voice, frames) for the same span;
// this places each slice's voice and frames together on WebAudio's clock, read where the
// ear is, so her mouth cannot drift from her voice. Each slice goes after the last, so a
// source may send ahead of her voice: they wait here as JPEG bytes, decoded a second early.
// Lifted from FlashHead's web/player.js (the clock, the placing, the reasons), minus what
// was FlashHead's own. Any picture shape: the canvas takes the frames' own.
//
//   const p = createFacePlayer(canvas, { onTalk, onShape, onGone });
//   await p.start();   // resolves when the stream ends (stop() ends it)
//   p.stop(); p.hush();   // hush: a tap on her face stops her, here, at her source and at the pump

const MARGIN = 0.15;            // a late slice (or the first) starts this far ahead: the audio path's own room
const DECODE_AHEAD = 1.0;       // frames are decoded this long before their moment
const OVER = 2.25;              // in the quiet, holding more than this ahead is a player left behind: that idle is skipped
const csrf = () => document.querySelector('meta[name="csrf-token"]')?.content || '';

export function createFacePlayer(canvas, { onTalk = () => {}, onShape = () => {}, onGone = () => {}, sound = true } = {}) {
    const g = canvas.getContext('2d');
    let ctx = null, abort = null, raf = 0, next = 0, frames = [], sources = [];
    let talkEnd = 0, talking = false, sid = '', shape = '';

    // The moment now being heard: the device's own timestamp (every buffer between), else
    // the latencies it reports. Her mouth follows the ear, not the scheduler.
    function heard() {
        const ts = ctx.getOutputTimestamp?.();
        if (ts && ts.contextTime > 0) {
            const t = ts.contextTime + Math.max(0, performance.now() - ts.performanceTime) / 1000;
            if (t <= ctx.currentTime && t > ctx.currentTime - 1) return t;
        }
        return ctx.currentTime - (ctx.baseLatency || 0) - (ctx.outputLatency || 0);
    }

    function drop(f) { f.dead = true; f.bmp?.close(); f.bmp = null; }

    function draw() {
        raf = requestAnimationFrame(draw);
        if (!ctx) return;
        const now = heard();
        if ((now < talkEnd) !== talking) onTalk(talking = now < talkEnd);
        for (const f of frames) {
            if (f.t > now + DECODE_AHEAD) break;
            if (!f.p) f.p = createImageBitmap(f.blob).then(b => { if (f.dead) b.close(); else f.bmp = b; }).catch(() => {});
        }
        let show = null;
        while (frames.length && frames[0].t <= now) {
            if (show) drop(show);                           // never shown: its moment passed
            show = frames.shift();
        }
        if (show && !show.bmp) { drop(show); show = null; } // not decoded in time: the last frame holds
        if (!show) return;
        const key = `${show.bmp.width}x${show.bmp.height}`;
        if (key !== shape) {                                // her picture's own shape, whatever it is
            shape = key;
            canvas.width = show.bmp.width;
            canvas.height = show.bmp.height;
            onShape(show.bmp.width, show.bmp.height);
        }
        g.drawImage(show.bmp, 0, 0);
        drop(show);
    }

    function flush() {                  // she was stopped: cut what is scheduled
        const now = heard();
        sources.forEach(s => { try { s.stop(); } catch {} });
        sources = [];
        frames = frames.filter(f => (f.t <= now) || (drop(f), false));
        next = 0;
        talkEnd = 0;
    }

    function place(head, audio, blobs) {
        const dur = head.frames.length ? head.frames.length / head.fps : head.audio / 2 / head.sr;
        const ahead = next - ctx.currentTime;   // what was still to play when this came
        if (document.hidden) {                  // nobody draws in a hidden tab: what passed goes, what is to come stays
            const now = heard();
            frames = frames.filter(f => f.t > now || (drop(f), false));
        }
        if (head.kind !== 'speech' && ahead > OVER) return;            // this stretch of her idle is skipped: she is nearer now
        const t = ahead > 0.01 ? next : ctx.currentTime + MARGIN;      // on time: straight on, no gap; late: a fresh margin
        if (audio) {
            const pcm = new Int16Array(audio.buffer);
            const buf = ctx.createBuffer(1, pcm.length, head.sr);
            const ch = buf.getChannelData(0);
            for (let i = 0; i < pcm.length; i++) ch[i] = pcm[i] / 32768;
            const src = ctx.createBufferSource();
            src.buffer = buf;
            src.connect(ctx.destination);
            src.start(t);
            sources.push(src);
            src.onended = () => { sources = sources.filter(s => s !== src); };
        }
        next = t + dur;
        if (head.kind === 'speech' && head.sid) { talkEnd = t + dur; sid = head.sid; }
        blobs.forEach((blob, i) => frames.push({ t: t + i / head.fps, blob, bmp: null, p: null, dead: false }));
    }

    async function start() {
        ctx = new AudioContext();
        ctx.onstatechange = () => {             // a device change or sleep stops the clock: nothing else starts it again
            if (ctx && ctx.state === 'suspended') ctx.resume().catch(() => {});
        };
        next = 0;
        abort = new AbortController();
        raf = requestAnimationFrame(draw);
        const r = await fetch(`/api/avatar_feed/stream?sound=${sound ? 1 : 0}`, { signal: abort.signal });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const reader = r.body.getReader();
        let parts = [], have = 0;               // bytes as they came: joined only when a whole packet is here
        const join = () => {
            if (parts.length === 1) return parts[0];
            const all = new Uint8Array(have);
            let at = 0;
            for (const p of parts) { all.set(p, at); at += p.length; }
            parts = [all];
            return all;
        };
        for (;;) {
            const { value, done } = await reader.read();
            if (done) break;
            parts.push(value); have += value.length;
            for (;;) {
                if (have < 4) break;
                let buf = join();
                const hl = new DataView(buf.buffer, buf.byteOffset).getUint32(0);
                if (have < 4 + hl) break;
                const head = JSON.parse(new TextDecoder().decode(buf.subarray(4, 4 + hl)));
                const total = 4 + hl + (head.audio || 0) + (head.frames || []).reduce((a, b) => a + b, 0);
                if (have < total) break;
                if (head.kind === 'flush') flush();
                else if (head.kind === 'gone') onGone(head.why || '');
                else if (head.kind === 'speech' || head.kind === 'idle') {
                    let p = 4 + hl;
                    const audio = head.audio ? buf.slice(p, p + head.audio) : null;
                    p += head.audio || 0;
                    place(head, audio, head.frames.map(n => new Blob([buf.subarray(p, p += n)], { type: 'image/jpeg' })));
                }
                parts = total < have ? [buf.slice(total)] : [];
                have -= total;
            }
        }
    }

    function stop() {
        abort?.abort();
        cancelAnimationFrame(raf);
        talkEnd = 0;
        if (talking) onTalk(talking = false);
        frames.forEach(drop);
        frames = [];
        sources = [];
        ctx?.close();
        ctx = null;
        shape = '';
    }

    // Her face was tapped: her voice stops here now, then at her source (the rest of the
    // reply, every viewer) and at the pump (so the next sentence never starts her again).
    function hush() {
        const id = sid;
        flush();
        fetch('/api/avatar_feed/stop', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf() },
                                         body: JSON.stringify({ sid: id }) }).catch(() => {});
        import('/static/audio.js').then(a => { a.stop(true); if (id) a.stopLocalTts(id); }).catch(() => {});
    }

    return { start, stop, hush };
}
