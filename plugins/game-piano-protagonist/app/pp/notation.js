// notation.js — the MIDI plugin's notation, read into a note list the highway can drop.
//
//   token = note[:beats] | [note note ...][:beats] | R[:beats] | CHORD[:beats]
//   note  = letter + optional #/b + octave (C4 = middle C). '|' barlines are
//   ignored. A CHORD is a name with no octave digit (C, Am, F#m, G7) and
//   expands through the chord table — a note always carries its octave, so
//   the two can never be confused.
//
// parse() -> { notes: [{t, dur, n, name, chord, group}], beats, bpm, chords }
// where t and dur are SECONDS from the start, group numbers the simultaneous
// strikes (one group per chord or single note) and chord is the name when a
// token was a chord.

const PITCH = { C: 0, D: 2, E: 4, F: 5, G: 7, A: 9, B: 11 };
const NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B'];
const NOTE = /^([A-Ga-g])([#b]?)(-?\d)$/;
const CHORD = /^[A-G][#b]?(m|7|m7|dim)?$/;

export function noteNum(name) {
    const m = NOTE.exec(name);
    if (!m) throw new Error(`Bad note '${name}'. Notes look like C4, F#3, Bb5.`);
    const n = PITCH[m[1].toUpperCase()] + ({ '#': 1, b: -1, '': 0 })[m[2]] + 12 * (parseInt(m[3], 10) + 1);
    if (n < 0 || n > 127) throw new Error(`Note '${name}' is outside the MIDI range.`);
    return n;
}

export function noteName(n) { return `${NAMES[n % 12]}${Math.floor(n / 12) - 1}`; }
export const pitchClass = (n) => NAMES[n % 12];

export function parse(text, bpm, chords = {}) {
    bpm = Number(bpm) || 120;
    const spb = 60 / bpm;
    const notes = [];
    const used = new Set();
    let beats = 0, group = 0;
    const toks = String(text || '').replace(/\|/g, ' ').match(/\[[^\]]*\]\S*|\S+/g) || [];
    for (const tok of toks) {
        let body = tok, len = '';
        const i = tok.lastIndexOf(':');
        if (i > 0) { body = tok.slice(0, i); len = tok.slice(i + 1); }
        const dur = len ? parseFloat(len) : 1;
        if (!(dur > 0)) throw new Error(`Bad length in '${tok}'. Lengths are beats, like C4:2 or R:0.5.`);
        if (body.toUpperCase() !== 'R') {
            let names, chord = null;
            if (body.startsWith('[')) names = body.replace(/[[\]]/g, ' ').replace(/,/g, ' ').trim().split(/\s+/);
            else if (CHORD.test(body)) {
                if (!chords[body]) throw new Error(`No chord called '${body}'.`);
                names = chords[body]; chord = body; used.add(body);
            } else names = [body];
            for (const name of names) {
                const n = noteNum(name);
                notes.push({ t: beats * spb, dur: dur * spb * 0.95, beats: dur, n, name: noteName(n), chord, group });
            }
            group++;
        }
        beats += dur;
    }
    if (!notes.length) throw new Error('There are no notes in that. Example: C4 E4 G4 [C4 E4 G4]:2');
    return { notes, beats, bpm, seconds: beats * spb, chords: [...used] };
}

// "name | bpm | notation" or "name | bpm | tier | notation", one lesson per line.
export function parseUserSongs(text, chords) {
    const out = [], errors = [];
    String(text || '').split('\n').forEach((line, i) => {
        line = line.trim();
        if (!line || line.startsWith('#')) return;
        const parts = line.split('|').map(s => s.trim());
        if (parts.length < 3) { errors.push(`line ${i + 1}: needs name | bpm | notation`); return; }
        const [title, bpmRaw, ...rest] = parts;
        let tier = 'medium';
        if (rest.length > 1 && /^(easy|medium|hard)$/i.test(rest[0])) tier = rest.shift().toLowerCase();
        const notes = rest.join(' ');
        const bpm = parseFloat(bpmRaw);
        if (!(bpm >= 20 && bpm <= 400)) { errors.push(`line ${i + 1}: bpm has to be 20-400`); return; }
        try {
            const p = parse(notes, bpm, chords);
            out.push({ id: `user-${i + 1}`, kind: p.chords.length ? 'chords' : 'song', tier, title, bpm, notes, user: true });
        } catch (e) { errors.push(`line ${i + 1}: ${e.message}`); }
    });
    return { lessons: out, errors };
}
