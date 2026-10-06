// lanes.js — where the keys come from. Three lanes, one shape:
//
//   lane.now()        the lane's clock, in ms. Hits arrive stamped in it, the
//                     lesson is scheduled in it, and the scorer never sees
//                     anything else — so the time a note spends on a wire
//                     changes when it is DRAWN, not whether it counted.
//   lane.toPerf(t)    that clock's moment as a performance.now() time, for
//                     drawing and for the audio clock.
//   lane.start(cb)    begin handing {on, n, v, t} to cb;  lane.stop() ends it.
//   lane.sounds       true when the player's keys already make sound somewhere
//                     (the FM-1 at the desk) — the board stays quiet then.
//
// 'server'  = the keyboard linked to the machine she runs on, through the MIDI
//             plugin's tap (SSE). Its clock is HER box's; the offset is measured from
//             tap/clock with a few round trips, quickest one kept.
// 'webmidi' = a MIDI keyboard on this browser's machine (Web MIDI).
// 'qwerty'  = the computer's keys laid out like a piano with middle C under G:
//             A S D F = F3 G3 A3 B3, G H J K L ; ' = C4 D4 E4 F4 G4 A4 B4, and
//             the row above holds the black keys where a piano has them
//             (W E R · Y U · O P [). Z / X shift the octave. The lesson chords
//             (F3-A3-C4, G3-B3-D4, A3-C4-E4) all sit under the hands this way.

const TAP = '/api/plugin/midi/tap';
const PERF = { now: () => performance.now(), toPerf: (t) => t };

// ── Sapphire's keyboard ─────────────────────────────────────────────────────

export function serverLane() {
    let es = null, off = 0;
    const lane = {
        id: 'server', label: "Sapphire's keyboard", sounds: true, ports: '', detail: '',
        now: () => performance.now() + off,
        toPerf: (t) => t - off,
        async probe() {
            let best = null, ports = '', ok = false, detail = '';
            for (let i = 0; i < 3; i++) {
                const p0 = performance.now();
                let r;
                try { r = await fetch(`${TAP}/clock`, { credentials: 'same-origin', cache: 'no-store' }); }
                catch (e) { detail = 'Could not reach her box.'; break; }
                const p1 = performance.now();
                if (r.status === 404) { detail = 'The MIDI plugin is not on her box.'; break; }
                if (!r.ok) { detail = `Her box answered ${r.status}.`; break; }
                const j = await r.json();
                ok = !!j.ok; ports = j.ports || ''; detail = j.detail || '';
                const rtt = p1 - p0;
                if (!best || rtt < best.rtt) best = { rtt, off: (j.now + rtt / 2) - p1 };
            }
            if (best) off = best.off;
            lane.ports = ports; lane.detail = detail;
            lane.rtt = best ? best.rtt : null;
            return { ok, detail: ok ? `${ports} — ${Math.round(best.rtt)}ms away` : (detail || 'No keyboard is linked to her box.') };
        },
        start(onNote, onLost) {
            this.stop();
            es = new EventSource(TAP);
            es.onmessage = (e) => {
                let m;
                try { m = JSON.parse(e.data); } catch (_) { return; }
                if (m.type === 'note') onNote({ on: !!m.on, n: m.n, v: m.v, t: m.t });
                else if (m.type === 'gone') { onLost?.(m.detail || 'The keys went away.'); this.stop(); }
            };
            es.onerror = () => { if (es && es.readyState === EventSource.CLOSED) onLost?.('Lost the keys on her box.'); };
        },
        stop() { if (es) { es.close(); es = null; } },
    };
    return lane;
}

// ── A MIDI keyboard in this browser ─────────────────────────────────────────

export function webMidiLane() {
    let access = null, bound = [], chosen = 'all';
    const lane = {
        id: 'webmidi', label: 'This browser', sounds: false, ...PERF,
        supported: () => !!navigator.requestMIDIAccess,
        async probe() {
            if (!navigator.requestMIDIAccess) return { ok: false, detail: 'This browser has no Web MIDI (Brave, Chrome and Edge do; Firefox asks to install a permission).' };
            try { access = access || await navigator.requestMIDIAccess({ sysex: false }); }
            catch (e) { return { ok: false, detail: 'Web MIDI was refused — check the site permission.' }; }
            const ins = this.inputs();
            return { ok: ins.length > 0, detail: ins.length ? ins.map(i => i.name).join(', ') : 'No MIDI input on this machine.' };
        },
        inputs() { return access ? [...access.inputs.values()].map(i => ({ id: i.id, name: i.name || i.id })) : []; },
        choose(id) { chosen = id || 'all'; },
        onChange(fn) { if (access) access.onstatechange = fn; },
        start(onNote) {
            this.stop();
            if (!access) return;
            for (const inp of access.inputs.values()) {
                if (chosen !== 'all' && inp.id !== chosen) continue;
                inp.onmidimessage = (e) => {
                    const [st, n, v] = e.data;
                    const kind = st & 0xf0;
                    if (kind === 0x90 && v > 0) onNote({ on: true, n, v, t: e.timeStamp });
                    else if (kind === 0x80 || (kind === 0x90 && v === 0)) onNote({ on: false, n, v: 0, t: e.timeStamp });
                };
                bound.push(inp);
            }
        },
        stop() { for (const inp of bound) inp.onmidimessage = null; bound = []; },
    };
    return lane;
}

// ── The computer's keys ─────────────────────────────────────────────────────

// code -> semitones from the base note (C4 by default). Whites on the home
// row, blacks on the row above, each between the two whites it sits between.
export const KEYMAP = {
    KeyA: -7, KeyW: -6, KeyS: -5, KeyE: -4, KeyD: -3, KeyR: -2, KeyF: -1,
    KeyG: 0, KeyY: 1, KeyH: 2, KeyU: 3, KeyJ: 4, KeyK: 5, KeyO: 6, KeyL: 7, KeyP: 8,
    Semicolon: 9, BracketLeft: 10, Quote: 11,
};
export const KEYCAP = {
    KeyA: 'A', KeyW: 'W', KeyS: 'S', KeyE: 'E', KeyD: 'D', KeyR: 'R', KeyF: 'F', KeyG: 'G', KeyY: 'Y',
    KeyH: 'H', KeyU: 'U', KeyJ: 'J', KeyK: 'K', KeyO: 'O', KeyL: 'L', KeyP: 'P', Semicolon: ';',
    BracketLeft: '[', Quote: "'",
};
const OCTAVE = { KeyZ: -1, KeyX: 1 };

export function qwertyLane() {
    let down = null, up = null, blur = null;
    const held = new Map();                       // code -> note number it struck
    const lane = {
        id: 'qwerty', label: 'Computer keys', sounds: false, base: 60, ...PERF,
        async probe() { return { ok: true, detail: 'G is middle C: A S D F · G H J K L ; \' — Z / X shift the octave' }; },
        // {note number: key cap} for the keyboard on screen
        caps() { const out = {}; for (const [code, off] of Object.entries(KEYMAP)) out[lane.base + off] = KEYCAP[code]; return out; },
        start(onNote, _onLost, onOctave) {
            this.stop();
            const typing = (e) => {
                const t = e.target;
                return t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable);
            };
            down = (e) => {
                if (typing(e) || e.ctrlKey || e.metaKey || e.altKey) return;
                if (OCTAVE[e.code] !== undefined) {
                    if (!e.repeat) { lane.base = Math.max(24, Math.min(96, lane.base + 12 * OCTAVE[e.code])); onOctave?.(lane.base); }
                    e.preventDefault(); return;
                }
                const off = KEYMAP[e.code];
                if (off === undefined) return;
                e.preventDefault();
                if (e.repeat || held.has(e.code)) return;
                const n = lane.base + off;
                held.set(e.code, n);
                onNote({ on: true, n, v: 90, t: e.timeStamp });
            };
            up = (e) => {
                const n = held.get(e.code);
                if (n === undefined) return;
                held.delete(e.code);
                e.preventDefault();
                onNote({ on: false, n, v: 0, t: e.timeStamp });
            };
            blur = () => { for (const n of held.values()) onNote({ on: false, n, v: 0, t: performance.now() }); held.clear(); };
            document.addEventListener('keydown', down, true);
            document.addEventListener('keyup', up, true);
            window.addEventListener('blur', blur);
        },
        stop() {
            if (down) document.removeEventListener('keydown', down, true);
            if (up) document.removeEventListener('keyup', up, true);
            if (blur) window.removeEventListener('blur', blur);
            down = up = blur = null; held.clear();
        },
    };
    return lane;
}

export function makeLanes() {
    return { server: serverLane(), webmidi: webMidiLane(), qwerty: qwertyLane() };
}
