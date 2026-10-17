// Piano Protagonist — the board. A free-mount game on the Game Room rail
// ({mount, unmount}: we own the stage). We have Guitar Hero at home.
//
// One mode: pick a song and loop it. The notes fall (pp/highway.js) on the
// lane's clock (pp/lanes.js), pass after pass with a rest between
// (pp/loop.js), and the keys are live the whole time, loop or no loop. Two
// toggles: hear the song as it scrolls (pp/audio.js), and a score for each
// pass (pp/score.js) — a number in the corner, never a stop.
//
// Nothing is judged out loud and nothing is kept. The server holds the
// session and the "Your songs" setting (games/piano-protagonist/engine.py);
// she has no part in it yet.
import { parse, parseUserSongs } from './pp/notation.js';
import { makePiano } from './pp/audio.js';
import { makeLanes } from './pp/lanes.js';
import { LEVELS, makeSlots, calibrationOffset } from './pp/score.js';
import { makeLoop } from './pp/loop.js';
import { makeHighway } from './pp/highway.js';

console.log('[PP] Piano Protagonist 0.3.1');
const bootV = () => document.querySelector('meta[name="boot-version"]')?.content || '';
const GAME = 'piano-protagonist';
const LS = (k) => `pp:${k}`;
const MODES = ['chords', 'songs', 'combo'];
const FAR = 1e12;                                // a lane time that never comes: bars that sit still
let CONTENT = null;

async function loadContent() {
    if (CONTENT) return;
    const base = new URL('./pp', import.meta.url).pathname;
    CONTENT = await fetch(`${base}/content.json?v=${bootV()}`).then(r => r.json());
}

const CSS = `
.pp { display:flex; flex-direction:column; flex:1 1 auto; min-height:0; gap:8px; }
.pp-row { display:flex; align-items:center; gap:8px; flex-wrap:wrap; font-size:0.9em; color:var(--text-secondary,#8a8fa3); }
.pp-row select, .pp-row input[type=range] { max-width: 220px; }
.pp-row .pp-status { flex:1 1 160px; min-width:120px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.pp-status.ok { color: var(--success, #5ee6a0); }
.pp-status.bad { color: var(--warning, #f0a050); }
.pp-seg { display:inline-flex; border:1px solid var(--border,#2a3040); border-radius:8px; overflow:hidden; }
.pp-seg button { padding:5px 10px; border:0; background:transparent; color:var(--text-secondary,#8a8fa3); cursor:pointer; font-size:0.92em; }
.pp-seg button.on { background:var(--bg-secondary,#1c2230); color:var(--text,#e6e6ef); font-weight:600; }
.pp-head { display:flex; align-items:center; gap:10px; flex-wrap:wrap; }
.pp-title { font-weight:700; font-size:1.05em; color:var(--text,#e6e6ef); }
.pp-sub { color:var(--text-secondary,#8a8fa3); font-size:0.88em; }
.pp-hud { margin-left:auto; display:flex; gap:12px; font-variant-numeric:tabular-nums; color:var(--text-secondary,#8a8fa3); font-size:0.9em; }
.pp-tog { opacity:0.6; }
.pp-tog.on { opacity:1; color:var(--text,#e6e6ef); border-color:var(--accent,#5ab0ff);
             box-shadow:0 0 0 1px var(--accent,#5ab0ff), 0 0 12px rgba(90,176,255,0.4); }
.pp-wrap { position:relative; flex:1 1 auto; min-height:280px; border-radius:12px; overflow:hidden;
           background: linear-gradient(180deg, #0f1118 0%, #171a24 100%); border:1px solid var(--border,#2a3040); }
.pp-wrap canvas { position:absolute; inset:0; display:block; }
.pp-overlay { position:absolute; inset:0; display:flex; flex-direction:column; align-items:center; justify-content:center;
              gap:12px; background:rgba(10,12,18,0.72); backdrop-filter:blur(2px); text-align:center; padding:20px; }
.pp-note { color:var(--text-secondary,#8a8fa3); font-size:0.9em; max-width:460px; }
.pp-err { color: var(--warning, #f0a050); font-size:0.85em; }
.pp-kbd { font-family: ui-monospace, monospace; background:var(--bg-secondary,#1c2230); padding:1px 5px; border-radius:4px; }
`;

let G = null;

export const game = {
    id: GAME,
    async mount(el, ctx) {
        await loadContent();
        const st = ctx.state();
        if (!st || !st.round) { splash(el, ctx); return; }
        boot(el, ctx);
    },
    unmount() { teardown(); },
};

function splash(el, ctx) {
    el.innerHTML = `
      <div class="pk-splash">
        <h2>🎹 Piano Protagonist</h2>
        <p>We have Guitar Hero at home. Pick a song and loop it: the notes fall toward a keyboard and you play along on real keys — a MIDI keyboard here, the one linked to her machine, or this one's keys with middle C under <span class="pp-kbd">G</span>. Nobody is keeping score unless you ask.</p>
        <button class="pk-btn pk-btn-primary" id="pp-new">Sit down at the piano</button>
      </div>`;
    el.querySelector('#pp-new').onclick = async () => {
        try {
            await ctx.api(`play/${GAME}/new-session`, 'POST', { session: ctx.session() });
            boot(el, ctx);
        } catch (e) { ctx.showError(e.message); }
    };
}

function teardown() {
    if (!G) return;
    cancelAnimationFrame(G.raf);
    clearTimeout(G.timer);
    G.lane?.stop();
    G.piano.close();
    G.highway?.destroy();
    document.getElementById('pp-css')?.remove();
    G = null;
}

function store(k, v) { try { localStorage.setItem(LS(k), JSON.stringify(v)); } catch (_) { /* private window */ } }
function load(k, d) { try { const v = localStorage.getItem(LS(k)); return v === null ? d : JSON.parse(v); } catch (_) { return d; } }
const oneOf = (v, list, d) => list.includes(v) ? v : d;

// ── boot ─────────────────────────────────────────────────────────────────────

function boot(el, ctx) {
    teardown();
    if (!document.getElementById('pp-css')) {
        const s = document.createElement('style'); s.id = 'pp-css'; s.textContent = CSS; document.head.appendChild(s);
    }
    G = {
        el, ctx, esc: ctx.esc,
        difficulty: oneOf(load('difficulty', 'easy'), Object.keys(LEVELS), 'easy'),
        mode: oneOf(load('mode', 'combo'), MODES, 'combo'),
        lanes: makeLanes(), lane: null, laneOk: false,
        piano: makePiano(), hear: true, calib: 0, speed: load('speed', 1) || 1,
        tune: load('tune', true) !== false, scoring: load('score', false) === true,
        lessons: CONTENT.lessons.slice(), userErrors: [], lesson: null, parsed: null,
        phase: 'idle', loop: null, previewAt: 0, raf: 0, timer: 0,
    };
    buildDom();
    G.highway = makeHighway(G.el.querySelector('#pp-canvas'));
    G.highway.clear();
    loadUserSongs();
    pickLane(load('lane', null), true);
    paintLessons();
    chooseLesson(load('lesson', null) || randomLesson()?.id);
    G.raf = requestAnimationFrame(tick);
}

function buildDom() {
    G.el.innerHTML = `
      <div class="pp">
        <div class="pp-row">
          <span>Keys</span>
          <select id="pp-lane">
            <option value="server">Sapphire's keyboard</option>
            <option value="webmidi">This browser (MIDI)</option>
            <option value="qwerty">Computer keys</option>
          </select>
          <select id="pp-input" style="display:none"></select>
          <span class="pp-status" id="pp-lane-status">…</span>
          <label title="Sound your own keys through the browser"><input type="checkbox" id="pp-hear"> hear my keys</label>
          <button class="pk-btn pk-btn-sm" id="pp-calib" title="Tap along with eight clicks; your lag is measured and taken out of the timing when a pass is scored">Calibrate</button>
        </div>
        <div class="pp-row">
          <span>Difficulty</span>
          <span class="pp-seg" id="pp-diff">${Object.keys(LEVELS).map(d => `<button data-d="${d}">${d}</button>`).join('')}</span>
          <span>Shelf</span>
          <span class="pp-seg" id="pp-mode">${MODES.map(m => `<button data-m="${m}">${m}</button>`).join('')}</span>
          <select id="pp-lesson" style="min-width:200px"></select>
          <button class="pk-btn pk-btn-sm" id="pp-random" title="A song from the shelf, at random">🎲</button>
          <span>Speed</span>
          <select id="pp-speed" title="The song runs at this fraction of its written tempo">
            <option value="0.25">¼</option><option value="0.5">½</option><option value="0.75">¾</option><option value="1">1×</option>
          </select>
        </div>
        <div class="pp-head">
          <span class="pp-title" id="pp-title"></span>
          <span class="pp-sub" id="pp-sub"></span>
          <span class="pp-hud" id="pp-hud"></span>
          <button class="pk-btn pk-btn-sm pp-tog" id="pp-tune"></button>
          <button class="pk-btn pk-btn-sm pp-tog" id="pp-score">✨ Score</button>
          <button class="pk-btn pk-btn-sm" id="pp-go"></button>
        </div>
        <div class="pp-wrap" id="pp-wrap"><canvas id="pp-canvas"></canvas><div id="pp-overlay"></div></div>
        <div class="pp-err" id="pp-errors"></div>
      </div>`;
    const $ = (s) => G.el.querySelector(s);
    $('#pp-lane').onchange = (e) => pickLane(e.target.value);
    $('#pp-input').onchange = (e) => { G.lanes.webmidi.choose(e.target.value); store('midi_input', e.target.value); if (G.lane?.id === 'webmidi') G.lane.start(onNote); };
    $('#pp-hear').onchange = (e) => { G.hear = e.target.checked; store(`hear:${G.lane?.id}`, G.hear); G.piano.resume(); };
    $('#pp-calib').onclick = () => calibrate();
    $('#pp-diff').onclick = (e) => { const d = e.target.dataset.d; if (d) setDifficulty(d); };
    $('#pp-mode').onclick = (e) => { const m = e.target.dataset.m; if (m) setMode(m); };
    $('#pp-lesson').onchange = (e) => chooseLesson(e.target.value);
    $('#pp-random').onclick = () => { const l = randomLesson(); if (l) chooseLesson(l.id); };
    $('#pp-speed').value = String(G.speed);
    $('#pp-speed').onchange = (e) => setSpeed(parseFloat(e.target.value) || 1);
    $('#pp-tune').onclick = () => setTune(!G.tune);
    $('#pp-score').onclick = () => setScoring(!G.scoring);
    $('#pp-go').onclick = () => { if (G.loop) stopLoop(); else startLoop(); };
    paintSegs();
    paintToggles();
}

const SPEED = { 0.25: '¼', 0.5: '½', 0.75: '¾', 1: '' };

function paintCaps() {
    G.highway.setCaps(G.lane?.id === 'qwerty' ? G.lane.caps() : {});
}

function paintSegs() {
    G.el.querySelectorAll('#pp-diff button').forEach(b => b.classList.toggle('on', b.dataset.d === G.difficulty));
    G.el.querySelectorAll('#pp-mode button').forEach(b => b.classList.toggle('on', b.dataset.m === G.mode));
    G.ctx.stageInfo(`${G.esc(G.lane?.label || '')} · ${G.esc(G.difficulty)}${SPEED[G.speed] ? ' · ' + SPEED[G.speed] + ' speed' : ''}`);
}

// The two toggles light when they are on; the speaker is crossed out when
// the song is silent. The third button starts the loop or stops it.
function paintToggles() {
    const tune = G.el.querySelector('#pp-tune'), score = G.el.querySelector('#pp-score'), go = G.el.querySelector('#pp-go');
    tune.textContent = G.tune ? '🔊' : '🔇';
    tune.classList.toggle('on', G.tune);
    tune.title = G.tune ? 'The song plays as it scrolls. Click for silent bars.' : 'The bars are silent. Click to hear the song as it scrolls.';
    score.classList.toggle('on', G.scoring);
    score.title = G.scoring ? 'Each pass leaves a score in the corner. Click to play unscored.' : 'Nobody is counting. Click for a score after each pass.';
    go.textContent = G.loop ? '⏹ Stop' : '▶ Loop';
    go.classList.toggle('pk-btn-primary', !G.loop);
}

function paintHud() {
    G.el.querySelector('#pp-hud').innerHTML = G.calib ? `<span title="taken out of your timing when a pass is scored">lag ${G.calib}ms</span>` : '';
}

function setSpeed(x) {
    G.speed = x; store('speed', x);
    if (G.lesson) chooseLesson(G.lesson.id);
    paintSegs();
}

function setTune(on) {
    G.tune = on; store('tune', on);
    G.piano.resume();
    if (G.loop) {
        G.highway.setGuide(on);
        if (on) playTune(G.loop.cur); else G.piano.stopScheduled();
    }
    paintToggles();
}

// A score starts with a whole pass: one that is already under way stays
// unscored and the next one counts.
function setScoring(on) {
    G.scoring = on; store('score', on);
    if (!on) { if (G.loop) G.loop.cur.judge = null; G.highway.score(null); }
    else if (G.loop && G.lane.now() < G.loop.cur.startT) G.loop.cur.arm();
    paintToggles();
}

// ── songs ────────────────────────────────────────────────────────────────────

async function loadUserSongs() {
    try {
        const r = await G.ctx.api(`play/${GAME}/settings`);
        const { lessons, errors } = parseUserSongs(r?.settings?.user_songs || '', CONTENT.chords);
        if (!G) return;
        G.lessons = CONTENT.lessons.concat(lessons);
        G.userErrors = errors;
        paintLessons();
        if (!G.lesson) chooseLesson(randomLesson()?.id);
    } catch (e) { console.warn('[PP] settings', e); }
}

function shelf() {
    return G.lessons.filter(l => G.mode === 'combo' || (G.mode === 'chords' ? l.kind === 'chords' : l.kind === 'song'));
}

const TIER = { easy: 0, medium: 1, hard: 2 };

function randomLesson() {
    const pool = shelf();
    const fit = pool.filter(l => TIER[l.tier] <= TIER[G.difficulty]);
    const from = fit.length ? fit : pool;
    if (!from.length) return null;
    const notNow = from.filter(l => l.id !== G.lesson?.id);
    const pick = (notNow.length ? notNow : from);
    return pick[Math.floor(Math.random() * pick.length)];
}

function paintLessons() {
    const sel = G.el.querySelector('#pp-lesson');
    const groups = [['Songs', l => l.kind === 'song' && !l.user], ['Chords', l => l.kind === 'chords' && !l.user], ['Yours', l => l.user]];
    sel.innerHTML = groups.map(([name, f]) => {
        const rows = shelf().filter(f);
        if (!rows.length) return '';
        return `<optgroup label="${name}">` + rows.map(l =>
            `<option value="${G.esc(l.id)}">${G.esc(l.title)} · ${l.tier}</option>`).join('') + '</optgroup>';
    }).join('');
    if (G.lesson) sel.value = G.lesson.id;
    G.el.querySelector('#pp-errors').textContent = G.userErrors.length ? `Your songs: ${G.userErrors.join(' · ')}` : '';
}

// A change under a running loop restarts it with the change in. This ends
// what is running (a loop, a calibration) and says whether a loop was.
function halt() {
    clearTimeout(G.timer);
    G.el.querySelector('#pp-overlay').innerHTML = '';
    if (G.phase === 'calib') {
        G.calibTaps = null;
        G.piano.stopScheduled();
        G.phase = 'idle';
        return false;
    }
    return stopLoop();
}

function chooseLesson(id) {
    const l = G.lessons.find(x => x.id === id) || shelf()[0];
    if (!l) return;
    let parsed = null;
    try { parsed = parse(l.notes, l.bpm * G.speed, CONTENT.chords); } catch (e) { G.ctx.showError(`${l.title}: ${e.message}`); return; }
    const looping = halt();
    G.lesson = l;
    G.parsed = parsed;
    store('lesson', l.id);
    const sel = G.el.querySelector('#pp-lesson');
    if (sel.value !== l.id) sel.value = l.id;
    G.el.querySelector('#pp-title').textContent = l.title;
    const chords = parsed.chords.length ? ` · ${parsed.chords.join(' ')}` : '';
    const speed = SPEED[G.speed] ? ` · at ${SPEED[G.speed]} speed` : '';
    G.el.querySelector('#pp-sub').textContent = `${l.kind === 'chords' ? 'chords' : 'tune'} · ${l.bpm} bpm${speed} · ${parsed.notes.length} notes · ${Math.round(parsed.seconds)}s${chords}`;
    G.highway.clear();
    G.highway.score(null);
    if (looping) startLoop(); else preview();
}

// No loop running: the bars sit still above the keys so the shape is readable.
function preview() {
    if (!G.parsed) return;
    const L = LEVELS[G.difficulty];
    G.highway.setLesson(G.parsed, { labels: L.labels, lead: L.lead, window: L.window });
    G.highway.setGuide(false);
    G.highway.setPass(makeSlots(G.parsed.notes, FAR), FAR, FAR + G.parsed.seconds * 1000);
    G.previewAt = FAR - L.lead * 0.55;
}

function setDifficulty(d) {
    if (!LEVELS[d] || d === G.difficulty) return;
    const looping = halt();
    G.difficulty = d; store('difficulty', d);
    paintSegs();
    G.highway.score(null);
    if (looping) startLoop(); else preview();
}

function setMode(m) {
    if (m === G.mode) return;
    G.mode = m; store('mode', m);
    paintSegs(); paintLessons();
    if (!shelf().some(l => l.id === G.lesson?.id)) chooseLesson(randomLesson()?.id);
}

// ── lanes ────────────────────────────────────────────────────────────────────

async function pickLane(id, auto = false) {
    const lanes = G.lanes;
    const status = G.el.querySelector('#pp-lane-status');
    const order = id ? [id] : ['server', 'qwerty'];          // Web MIDI asks permission — only on request
    const looping = halt();                                  // a loop lives on its lane's clock
    G.lane?.stop();
    G.lane = null; G.laneOk = false;
    for (const key of order) {
        const lane = lanes[key];
        if (!lane) continue;
        status.textContent = `looking for ${lane.label}…`; status.className = 'pp-status';
        const res = await lane.probe();
        if (!G) return;
        if (res.ok || !auto) {
            G.lane = lane; G.laneOk = res.ok;
            status.textContent = res.detail; status.className = 'pp-status ' + (res.ok ? 'ok' : 'bad');
            break;
        }
    }
    if (!G.lane) { G.lane = lanes.qwerty; G.laneOk = true; status.textContent = 'A W S E D F T G Y H U J K'; status.className = 'pp-status ok'; }
    G.el.querySelector('#pp-lane').value = G.lane.id;
    store('lane', G.lane.id);
    G.calib = load(`calib:${G.lane.id}`, 0) || 0;
    G.hear = load(`hear:${G.lane.id}`, !G.lane.sounds);
    G.el.querySelector('#pp-hear').checked = G.hear;
    const inSel = G.el.querySelector('#pp-input');
    if (G.lane.id === 'webmidi') {
        const paintInputs = () => {
            const ins = G.lanes.webmidi.inputs();
            inSel.innerHTML = `<option value="all">every input</option>` + ins.map(i => `<option value="${G.esc(i.id)}">${G.esc(i.name)}</option>`).join('');
            const want = load('midi_input', 'all');
            inSel.value = ins.some(i => i.id === want) ? want : 'all';
            G.lanes.webmidi.choose(inSel.value);
            inSel.style.display = '';
        };
        paintInputs();
        G.lanes.webmidi.onChange(() => { if (G?.lane?.id === 'webmidi') { paintInputs(); G.lane.start(onNote); } });
    } else inSel.style.display = 'none';
    if (G.laneOk) G.lane.start(onNote, (why) => { if (!G) return; status.textContent = why; status.className = 'pp-status bad'; G.laneOk = false; },
                               (base) => { status.textContent = `G is C${base / 12 - 1}`; paintCaps(); });
    paintCaps();
    paintSegs();
    paintHud();
    if (looping) startLoop();
}

// Every key lights and (with "hear my keys") sounds, loop or no loop. A pass
// that is being scored also hears it; a catch pops, anything else is let be.
function onNote(ev) {
    if (!G) return;
    const pass = G.loop?.cur;
    if (ev.on) {
        G.highway.keyDown(ev.n);
        if (G.hear) G.piano.noteOn(ev.n, ev.v || 90);
        if (pass?.judge && pass.holds(ev.t)) {
            const r = pass.judge.hit(ev);
            if (r.slot) G.highway.flash(r.slot, r.judge);
        } else if (G.phase === 'calib' && G.calibTaps) G.calibTaps.push(ev.t);
    } else {
        G.highway.keyUp(ev.n);
        if (G.hear) G.piano.noteOff(ev.n);
        pass?.judge?.release(ev);
    }
}

// ── the loop ─────────────────────────────────────────────────────────────────

function startLoop() {
    if (!G.parsed || !G.lane) return;
    halt();
    G.piano.resume();
    const L = LEVELS[G.difficulty];
    const t0 = G.lane.now() + Math.max(L.lead, 2000) + 600;
    G.loop = { ...makeLoop(G.parsed, G.difficulty, t0, G.calib), cur: null, next: null };
    G.phase = 'loop';
    G.highway.setLesson(G.parsed, { labels: L.labels, lead: L.lead, window: L.window });
    G.highway.setGuide(G.tune);
    G.highway.score(null);
    take(G.loop.pass(0));
    paintToggles();
}

// Returns whether a loop was running.
function stopLoop() {
    if (!G.loop) return false;
    G.loop = null;
    G.phase = 'idle';
    G.piano.stopScheduled();
    paintToggles();
    preview();
    return true;
}

// This pass is up, and the one after it is already falling behind it.
function take(pass) {
    const lp = G.loop;
    lp.cur = pass;
    lp.next = lp.pass(pass.k + 1);
    if (G.scoring) pass.arm();
    if (G.tune) playTune(pass);
    G.highway.setPass(pass.slots.concat(lp.next.slots), pass.startT, pass.endT, pass.k === 0);
}

// The song through the browser, queued on the audio clock: what is left of
// this pass from now on.
function playTune(pass) {
    const now = G.lane.now();
    for (const s of pass.slots) if (s.at > now + 30) G.piano.play(s.note.n, 88, G.lane.toPerf(s.at), s.note.dur);
}

function tick() {
    if (!G) return;
    G.raf = requestAnimationFrame(tick);
    const lane = G.lane;
    if (!lane) return;
    if (G.phase === 'idle') { G.highway.frame(G.previewAt); return; }
    const now = lane.now();
    if (G.phase === 'loop') {
        const cur = G.loop.cur;
        cur.judge?.sweep(now);
        if (now >= cur.closeT) {
            // the pass is over: its score lands in the corner (a pass nobody
            // played leaves the number alone) and the next one is up
            const card = cur.judge?.report(now);
            if (card?.hit) { G.highway.score(card.score); explain(cur.k, card); }
            take(G.loop.next);
        }
    }
    G.highway.frame(now);
}

// How a pass's number was made, for the console: the stage shows the number only.
function explain(k, card) {
    const parts = Object.entries(card.factors).map(([name, v]) => `${name} ${v}%`).join(' × ');
    const lean = Math.abs(card.late_ms) < 15 ? 'on the beat' : `${Math.abs(card.late_ms)}ms ${card.late_ms > 0 ? 'late' : 'early'} on average`;
    console.log(`[PP] pass ${k + 1}: ${card.score} = ${parts} · ${card.hit}/${card.notes} caught, ${card.extra} wrong, ${lean}`);
}

// ── calibration ──────────────────────────────────────────────────────────────

function calibrate() {
    if (!G.lane || !G.laneOk) { G.ctx.showError('Pick a lane with keys first.'); return; }
    G.piano.resume();
    halt();
    G.phase = 'calib';
    G.calibTaps = [];
    const beat = 600, count = 8;
    const t0 = G.lane.now() + 1500;
    const expected = [];
    for (let i = 0; i < count; i++) {
        const t = t0 + i * beat;
        expected.push(t);
        G.piano.play(i === 0 ? 96 : 84, 100, G.lane.toPerf(t), 0.06);
    }
    G.el.querySelector('#pp-overlay').innerHTML = `
      <div class="pp-overlay">
        <div class="pp-title">Tap along</div>
        <div class="pp-note">Eight clicks are coming. Press any key on each one. Your lag — Bluetooth, synth, reflexes — is measured once and taken out of the timing on this lane.</div>
      </div>`;
    G.timer = setTimeout(() => {
        if (!G || G.phase !== 'calib') return;
        const off = calibrationOffset(expected, G.calibTaps);
        G.calibTaps = null;
        G.phase = 'idle';
        if (off === null) {
            G.el.querySelector('#pp-overlay').innerHTML = `<div class="pp-overlay"><div class="pp-note">Not enough taps landed near the clicks. Try again when you're ready.</div></div>`;
        } else {
            G.calib = off; store(`calib:${G.lane.id}`, off);
            G.el.querySelector('#pp-overlay').innerHTML = `<div class="pp-overlay"><div class="pp-title">${off > 0 ? `${off}ms late` : off < 0 ? `${-off}ms early` : 'dead on'}</div><div class="pp-note">${off ? 'Taken out of your timing from now on.' : 'Nothing to correct.'}</div></div>`;
        }
        paintHud();
        G.timer = setTimeout(() => { if (G) G.el.querySelector('#pp-overlay').innerHTML = ''; }, 2200);
    }, 1500 + count * beat + 500);
}
