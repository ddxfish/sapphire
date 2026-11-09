// Her mouth in three.js: plays the server's `avatar_mouth` curves (hooks/mouth.py)
// on whichever clock is carrying her voice, through the shared viseme table.
//
// Clocks, first that answers wins:
//   this tab's speech  -> audio.speechClock() (exact: the playing <audio>'s time)
//   the speakers       -> `tts_chunk_playing` + wall time since (~50-100 ms)
//   the whole-blob lane / a voice with no curve -> a gentle procedural mouth
// Device-lane curves (plays_here false) are not for this tab's mouth: the board plays them.
//
// Cues: her `<<avatar:>>` tags ride the curve of the words they were written in
// (`cues: [{track, mode, t}]`), fired through onCue() when this clock reaches t:
// she waves as she says hi. A device-lane curve's cues fire on arrival + t (the
// board's clock is not ours). `avatar_tail` = tags after her last words: fired
// when that stream's voice ends here.

const ATTACK = 0.05, RELEASE = 0.09;     // seconds a viseme eases in / out
const FOLLOW = 22;                        // how fast the shapes chase their target (1/s)
const KEEP = 48;                          // curves held

function expand(recipe) {
    const out = {};
    for (const [k, w] of Object.entries(recipe)) {
        if (k.endsWith('*')) { out[k.slice(0, -1) + 'Left'] = w; out[k.slice(0, -1) + 'Right'] = w; }
        else out[k] = w;
    }
    return out;
}

export async function createMouth({ root, audio, eventBus }) {
    const meshes = [];
    root.traverse(o => { if (o.morphTargetDictionary && o.morphTargetInfluences) meshes.push(o); });
    if (!meshes.length) return null;

    let table = {};
    try {
        const raw = await (await fetch(new URL('./visemes.json', import.meta.url))).json();
        delete raw._about;
        for (const [v, r] of Object.entries(raw)) table[v] = expand(r);
    } catch (e) { console.warn('[Avatar] viseme table missing, bands only', e); }

    // every shape any recipe (or the fallbacks) can touch — only these are ever written
    const names = new Set(['jawOpen', 'mouthFunnel', 'mouthPucker', 'mouthStretchLeft', 'mouthStretchRight']);
    for (const r of Object.values(table)) for (const k of Object.keys(r)) names.add(k);
    const slots = meshes.map(m => [...names].map(n => [n, m.morphTargetDictionary[n]]).filter(([, i]) => i !== undefined));
    const cur = Object.fromEntries([...names].map(n => [n, 0]));

    const curves = new Map();             // `${sid}:${index}:${seg}` -> curve
    const keyOf = (sid, index, seg) => `${sid}:${index}:${seg || 0}`;
    let local = null;                     // {key, start} from the speakers
    const unsubs = [], timers = new Set(), cueFns = new Set();
    const fired = new Set();              // `${key}#${i}` already fired
    const tails = new Map();              // sid -> cues waiting for her voice to end
    const elsewhere = new Set();          // sids voiced on a device, not here
    let lastSid = null;
    const fire = (cue) => cueFns.forEach(fn => { try { fn(cue); } catch (e) { console.warn('[Avatar] cue', e); } });
    const later = (ms, fn) => { const id = setTimeout(() => { timers.delete(id); fn(); }, ms); timers.add(id); };

    unsubs.push(eventBus.on('avatar_mouth', (c) => {
        if (!c) return;
        if (c.plays_here === false) {     // the board plays it: its cues on arrival + t
            elsewhere.add(c.stream_id);
            (c.cues || []).forEach(q => later(q.t * 1000, () => fire(q)));
            return;
        }
        curves.set(keyOf(c.stream_id, c.index, c.seg), c);
        while (curves.size > KEEP) curves.delete(curves.keys().next().value);
    }));
    unsubs.push(eventBus.on('tts_chunk_playing', (d) => {
        if (d) local = { key: keyOf(d.stream_id, d.index, d.seg), start: performance.now() };
    }));
    unsubs.push(eventBus.on(eventBus.Events.TTS_STOPPED, () => { local = null; }));
    unsubs.push(eventBus.on('avatar_tail', (d) => {
        if (!d?.cues?.length) return;
        if (elsewhere.has(d.stream_id)) { later(1500, () => d.cues.forEach(fire)); return; }
        if (lastSid === d.stream_id && !wasSpeaking) { d.cues.forEach(fire); return; }   // already done talking
        tails.set(d.stream_id, d.cues);
        later(60000, () => { const c = tails.get(d.stream_id); tails.delete(d.stream_id); c?.forEach(fire); });
    }));

    // where her voice is now -> {curve?, t, legacy?} or null
    function now() {
        const c = audio.speechClock?.();
        if (c?.legacy) return { legacy: true, t: c.t };
        if (c) { const key = keyOf(c.stream_id, c.index, c.seg); return { key, sid: c.stream_id, curve: curves.get(key), t: c.t }; }
        if (local) {
            const t = (performance.now() - local.start) / 1000;
            const curve = curves.get(local.key);
            if (curve && t > curve.dur + 0.3) { local = null; return null; }
            if (!curve && t > 8) { local = null; return null; }
            return { key: local.key, sid: local.key.split(':')[0], curve, t };
        }
        return null;
    }

    function sample(arr, fps, t) {
        const f = t * fps, i = Math.floor(f);
        if (i < 0 || i >= arr.length) return 0;
        const a = arr[i], b = arr[Math.min(i + 1, arr.length - 1)];
        return a + (b - a) * (f - i);
    }

    function target(at) {
        const out = {};
        if (!at) return out;
        if (at.legacy || !at.curve) {                    // a voice with no curve: soft, believable motion
            const t = at.t;
            out.jawOpen = 0.12 + 0.16 * (0.5 + 0.5 * Math.sin(t * 13.0)) * (0.6 + 0.4 * Math.sin(t * 3.7));
            return out;
        }
        const { curve, t } = at;
        const env = sample(curve.env, curve.fps, t);
        if (!curve.vis?.length || !Object.keys(table).length) {   // no phonemes: the bands carry it
            const lo = sample(curve.lo, curve.fps, t), hi = sample(curve.hi, curve.fps, t);
            out.jawOpen = env * 0.55;
            out.mouthFunnel = env * Math.min(1, lo * 0.8);
            out.mouthStretchLeft = out.mouthStretchRight = env * Math.min(1, hi * 1.5);
            return out;
        }
        for (const [start, v, dur] of curve.vis) {
            if (t < start - ATTACK) break;
            const end = start + dur;
            if (t > end + RELEASE) continue;
            const w = t < start ? (t - start + ATTACK) / ATTACK
                    : t > end ? 1 - (t - end) / RELEASE : 1;
            const r = table[v];
            if (!r) continue;
            for (const [k, x] of Object.entries(r)) out[k] = Math.max(out[k] || 0, x * w);
        }
        // loudness opens the jaw; quiet closes it (and hides alignment error)
        out.jawOpen = Math.max((out.jawOpen || 0) * (0.35 + 0.65 * env), env * 0.18);
        return out;
    }

    function cues(at) {
        if (!at?.curve?.cues) return;
        at.curve.cues.forEach((q, i) => {
            const id = `${at.key}#${i}`;
            if (q.t <= at.t && !fired.has(id)) {
                fired.add(id);
                if (fired.size > 256) fired.delete(fired.values().next().value);
                fire(q);
            }
        });
    }

    let speakingUntil = 0, wasSpeaking = false;
    return {
        update(dt) {
            const at = now();
            if (at) { speakingUntil = performance.now() + 120; lastSid = at.sid || lastSid; cues(at); }
            const speaking = performance.now() < speakingUntil;
            if (wasSpeaking && !speaking && tails.has(lastSid)) {     // her last words are out
                const c = tails.get(lastSid); tails.delete(lastSid); c.forEach(fire);
            }
            wasSpeaking = speaking;
            const tgt = target(at);
            const k = 1 - Math.exp(-FOLLOW * dt);
            for (const n of names) cur[n] += ((tgt[n] || 0) - cur[n]) * k;
            meshes.forEach((m, mi) => {
                for (const [n, i] of slots[mi]) m.morphTargetInfluences[i] = cur[n];
            });
        },
        isSpeaking: () => performance.now() < speakingUntil,
        onCue(fn) { cueFns.add(fn); return () => cueFns.delete(fn); },
        dispose() { unsubs.forEach(fn => fn && fn()); timers.forEach(clearTimeout); timers.clear(); cueFns.clear(); },
    };
}
