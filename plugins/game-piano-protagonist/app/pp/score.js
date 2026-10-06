// score.js — the judge. Pure: lane-time numbers in, a score out.
//
// SCORE = 100 × pitch × timing × hold × streak — a product (Krem's Drake
// equation), and a factor a difficulty does not score is 1.0. Easy scores
// pitch and timing on single notes; medium adds hold (you kept the key down
// for the written length) and chords; hard tightens the windows and insists
// on the written octave.
//
// The judge is optional and quiet: a pass of the loop gets one only when the
// player asked for a score, and all it ever shows is a number at the end.
//
// `window` is how far from its line a key still catches its note, early or
// late. Timing credit is full on the line and falls away, squared, to nothing
// at the edge, so the window is roomy: a note played a sixth of a second late
// on easy is still most of a note. A key only counts against the player when
// the song is not asking for it anywhere near: striking a due note twice, or
// too late to catch, costs nothing more than the note.

export const LEVELS = {
    easy:   { window: 300, lead: 2600, hold: false, strictOctave: false, labels: true },
    medium: { window: 220, lead: 2200, hold: true,  strictOctave: false, labels: true },
    hard:   { window: 140, lead: 1700, hold: true,  strictOctave: true,  labels: false },
};

// One pass of a song on the lane's clock: a slot per note, due at `at`. The
// highway draws slots; a judge marks them.
export function makeSlots(notes, startT) {
    return notes.map((note, i) => ({ i, note, at: startT + note.t * 1000, hit: null, dt: 0, held: null }));
}

// calib: the player's measured lag, taken out of every key. grace: how long
// after its window a note may still arrive (that lag, and the wire) before
// it is given up.
export function makeJudge(slots, level, calib = 0, grace = 0) {
    const L = LEVELS[level] || LEVELS.easy;
    let extra = 0, streak = 0, best = 0;
    const open = new Map();                      // key number -> slot being held

    const same = (a, b) => L.strictOctave ? a === b : (a % 12) === (b % 12);

    function hit(ev) {
        const t = ev.t - calib;
        let pick = null, pickD = Infinity, asked = false;
        for (const s of slots) {
            if (!same(s.note.n, ev.n)) continue;
            const d = Math.abs(t - s.at);
            if (d <= 2 * L.window) asked = true;
            if (s.hit === null && d <= L.window && d < pickD) { pick = s; pickD = d; }
        }
        if (!pick) {
            if (asked) return { judge: 'spare', n: ev.n };
            extra++; streak = 0;
            return { judge: 'wrong', n: ev.n };
        }
        pick.hit = t; pick.dt = t - pick.at; pick.heldFrom = ev.t;
        open.set(ev.n, pick);
        streak++; best = Math.max(best, streak);
        const a = Math.abs(pick.dt) / L.window;
        const judge = a <= 0.25 ? 'perfect' : a <= 0.6 ? 'good' : (pick.dt > 0 ? 'late' : 'early');
        return { judge, dt: pick.dt, slot: pick };
    }

    function release(ev) {
        const s = open.get(ev.n);
        if (!s) return null;
        open.delete(ev.n);
        s.held = Math.max(0, ev.t - s.heldFrom);
        return s;
    }

    // Notes whose window closed unplayed. Returns the ones that just missed.
    function sweep(nowT) {
        const missed = [];
        for (const s of slots) {
            if (s.hit === null && !s.missed && nowT > s.at + L.window + grace) { s.missed = true; streak = 0; missed.push(s); }
        }
        return missed;
    }

    function done(nowT) { return slots.every(s => s.hit !== null || s.missed) || nowT > slots[slots.length - 1].at + L.window; }

    function report(nowT) {
        const N = slots.length;
        const hits = slots.filter(s => s.hit !== null);
        const H = hits.length;
        const pitch = Math.max(0, Math.min(1, (H - 0.5 * extra) / N));
        const timing = H ? hits.reduce((a, s) => a + (1 - Math.pow(Math.abs(s.dt) / L.window, 2)), 0) / H : 0;
        let hold = 1;
        if (L.hold && H) {
            const ratios = hits.map(s => {
                const want = s.note.dur * 1000;
                if (want < 250) return 1;                              // short notes: no hold to keep
                const got = s.held !== null ? s.held : Math.max(0, (nowT ?? Infinity) - s.heldFrom);
                return Math.min(1, got / want);
            });
            hold = ratios.reduce((a, b) => a + b, 0) / ratios.length;
        }
        const str = 0.7 + 0.3 * (best / N);
        const late = H ? hits.reduce((a, s) => a + s.dt, 0) / H : 0;
        const factors = { pitch: pct(pitch), timing: pct(timing) };
        if (L.hold) factors.hold = pct(hold);
        factors.streak = pct(str);
        return { score: Math.round(100 * pitch * timing * hold * str), factors, late_ms: Math.round(late),
                 streak: best, notes: N, hit: H, extra, window: L.window };
    }

    return { slots, hit, release, sweep, done, report, level: L, get streak() { return streak; } };
}

const pct = (x) => Math.round(Math.max(0, Math.min(1, x)) * 100);

// A tap-along: the player taps 8 beats to a click; the median offset is
// their systematic lag (BLE, synth, reflexes) and is subtracted from hits.
export function calibrationOffset(expected, taps) {
    const ds = [];
    for (const e of expected) {
        let bestD = null;
        for (const t of taps) { const d = t - e; if (Math.abs(d) < 400 && (bestD === null || Math.abs(d) < Math.abs(bestD))) bestD = d; }
        if (bestD !== null) ds.push(bestD);
    }
    if (ds.length < 3) return null;
    ds.sort((a, b) => a - b);
    return Math.round(ds[Math.floor(ds.length / 2)]);
}
