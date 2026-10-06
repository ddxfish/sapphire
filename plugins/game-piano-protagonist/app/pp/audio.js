// audio.js — a small Web Audio piano: the song plays through it as it scrolls,
// and the player's own keys can too (the computer-keys lane has no other sound).
// Two oscillators and an envelope per note; nothing is loaded from anywhere.

export function makePiano() {
    let ac = null, master = null;
    const live = new Map();          // note -> voice (held or ringing)
    const scheduled = new Set();     // voices queued ahead of time (the song)

    function ensure() {
        if (ac) return ac;
        const Ctx = window.AudioContext || window.webkitAudioContext;
        if (!Ctx) return null;
        ac = new Ctx();
        master = ac.createGain();
        master.gain.value = 0.35;
        const comp = ac.createDynamicsCompressor();
        master.connect(comp).connect(ac.destination);
        return ac;
    }

    const hz = (n) => 440 * Math.pow(2, (n - 69) / 12);

    // The audio-clock time at which a note has to start so that it is HEARD
    // at a performance.now() moment. The output timestamp pairs what is at
    // the speakers with the clock, so the wait in the audio pipe (a sound
    // server, Bluetooth) comes out; without it the song trails its own bars
    // and whoever plays by ear plays late.
    function at(perfMs) {
        if (!ensure()) return 0;
        const out = ac.getOutputTimestamp ? ac.getOutputTimestamp() : null;
        const t = out && out.performanceTime > 0
            ? out.contextTime + (perfMs - out.performanceTime) / 1000
            : ac.currentTime + (perfMs - performance.now()) / 1000 - (ac.outputLatency || 0);
        return Math.max(ac.currentTime, t);
    }

    function voice(n, vel, t0) {
        const g = ac.createGain();
        const f = ac.createBiquadFilter();
        f.type = 'lowpass';
        f.frequency.value = 1800 + vel * 30;
        const o1 = ac.createOscillator(), o2 = ac.createOscillator();
        o1.type = 'triangle'; o1.frequency.value = hz(n);
        o2.type = 'sine'; o2.frequency.value = hz(n) * 2;
        const g2 = ac.createGain(); g2.gain.value = 0.25;
        o1.connect(f); o2.connect(g2).connect(f); f.connect(g).connect(master);
        const peak = 0.12 + 0.5 * (vel / 127);
        g.gain.setValueAtTime(0.0001, t0);
        g.gain.exponentialRampToValueAtTime(peak, t0 + 0.008);
        g.gain.exponentialRampToValueAtTime(peak * 0.35, t0 + 0.35);
        g.gain.exponentialRampToValueAtTime(peak * 0.12, t0 + 2.5);
        o1.start(t0); o2.start(t0);
        const v = { g, o1, o2, done: false };
        v.release = (t) => {
            if (v.done) return;
            v.done = true;
            const tt = Math.max(t, ac.currentTime);
            g.gain.cancelScheduledValues(tt);
            g.gain.setValueAtTime(Math.max(g.gain.value, 0.0001), tt);
            g.gain.exponentialRampToValueAtTime(0.0001, tt + 0.12);
            o1.stop(tt + 0.15); o2.stop(tt + 0.15);
        };
        return v;
    }

    return {
        resume() { const c = ensure(); if (c && c.state === 'suspended') c.resume(); },
        ready() { return !!ensure(); },
        noteOn(n, vel = 90) {
            if (!ensure()) return;
            this.noteOff(n);
            live.set(n, voice(n, vel, ac.currentTime));
        },
        noteOff(n) {
            const v = live.get(n);
            if (v) { v.release(ac.currentTime); live.delete(n); }
        },
        // A note at a performance.now() time, for a length — queued ahead.
        play(n, vel, perfMs, durSec) {
            if (!ensure()) return;
            const t0 = at(perfMs);
            const v = voice(n, vel, t0);
            v.release(t0 + Math.max(0.08, durSec || 0.3));
            scheduled.add(v);
            setTimeout(() => scheduled.delete(v), (t0 - ac.currentTime + (durSec || 0.3) + 0.3) * 1000);
        },
        // Everything queued ahead goes quiet; keys that are held ring on.
        stopScheduled() {
            for (const v of scheduled) { try { v.g.gain.cancelScheduledValues(0); v.o1.stop(); v.o2.stop(); } catch (_) { /* already stopped */ } }
            scheduled.clear();
        },
        stopAll() {
            if (!ac) return;
            for (const v of live.values()) v.release(ac.currentTime);
            live.clear();
            this.stopScheduled();
        },
        close() { this.stopAll(); if (ac) { try { ac.close(); } catch (_) { /* fine */ } ac = null; } },
    };
}
