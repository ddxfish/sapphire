// loop.js — a song on repeat. Pure: lane-time numbers in, passes out.
//
// A loop is a row of passes on the lane's clock. Pass k starts at
// t0 + k × period, and a period is the song plus a rest: the player gets a
// breath, and the next pass is already falling when it ends. A pass carries
// its slots (what the highway draws). It gets a judge only when the player
// asked for a score: arm() puts one on, and a pass that was never armed
// judges nothing.

import { LEVELS, makeSlots, makeJudge } from './score.js';

const REST_MIN = 2000, REST_MAX = 4000;

// The breath between passes: one bar at the song's tempo, held to 2-4 seconds.
export function restMs(bpm) {
    return Math.max(REST_MIN, Math.min(REST_MAX, 4 * 60000 / bpm));
}

export function makeLoop(parsed, level, t0, calib = 0) {
    const L = LEVELS[level] || LEVELS.easy;
    const song = parsed.seconds * 1000;
    const period = song + restMs(parsed.bpm);
    // A key struck late still arrives late: the lag that calibration takes
    // out, and the wire from her box. The pass stays open for both.
    const grace = Math.max(0, calib) + 250;

    function pass(k) {
        const startT = t0 + k * period, endT = startT + song;
        const p = {
            k, startT, endT, closeT: endT + L.window + grace,
            slots: makeSlots(parsed.notes, startT), judge: null,
            arm() { p.judge = makeJudge(p.slots, level, calib, grace); },
            // A key counts toward this pass only inside it: playing through
            // the rest or the count-in is not a wrong note.
            holds(t) { const at = t - calib; return at >= startT - L.window && at <= endT + L.window; },
        };
        return p;
    }

    return { song, period, pass };
}
