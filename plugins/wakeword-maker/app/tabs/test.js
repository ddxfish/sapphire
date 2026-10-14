// Test tab: pick runs, read how each hears you by microphone and way of speaking, choose a threshold, and try
// a live utterance against several models at once (your mic or a satellite's).
import { api, esc } from '../api.js';
import { qbtn, bindHelp } from '../help.js';
import * as rec from '../recorder.js';
import { encodeWAV } from '../recorder.js';

let keyHandler = null;

export function render(el, ctx) {
    const p = ctx.state.project;
    if (!p) { el.innerHTML = '<div class="wwm-empty">Create a phrase first.</div>'; return; }
    el.innerHTML = '<div class="wwm-loading">Loading…</div>';
    api(`/projects/${p.slug}/runs`).then(r => draw(el, ctx, p, r)).catch(e => { el.innerHTML = `<div class="wwm-error">${esc(e.message)}</div>`; });
    return () => { if (keyHandler) document.removeEventListener('keydown', keyHandler); keyHandler = null; if (watchPoll) { clearInterval(watchPoll); watchPoll = null; } if (watchRec) { clearTimeout(watchRec.timer); watchRec = null; } if (rec.isRecording()) rec.stop(); };
}

const best = (rows) => {
    if (!rows?.length) return null;
    const ok = rows.filter(r => r.recall != null && (r.fp_per_hour || 0) <= 0.5 && (r.own_fa_per_hour || 0) <= 1);
    return (ok.length ? ok : rows).slice().sort((a, b) => (b.recall || 0) - (a.recall || 0))[0];
};
const pct = (v) => v == null ? '–' : Math.round(100 * v) + '%';

function draw(el, ctx, p, data) {
    const runs = (data.runs || []).filter(r => r.state === 'done' && r.has_model);
    let sel = new Set();
    try { sel = new Set(JSON.parse(localStorage.getItem('wwm.testsel') || '[]')); } catch { /* fine */ }
    for (const id of [...sel]) if (!runs.find(r => r.id === id)) sel.delete(id);
    if (!sel.size) runs.filter(r => r.chosen).forEach(r => sel.add(r.id));
    if (!sel.size && runs.length) sel.add(runs[0].id);
    const thrOf = (r) => r.threshold ?? r.cutoff ?? r.heldout?.threshold ?? 0.5;
    el.innerHTML = `
      <div class="wwm-card">
        <div class="wwm-row wwm-between"><h2 style="margin:0">Testing ${qbtn('test')}</h2><button class="wwm-btn ghost" id="wwm-test-pick">change on Train</button></div>
        ${runs.length ? `<div class="wwm-row wwm-wrap">${[...sel].map(id => { const r = runs.find(x => x.id === id); return `<span class="wwm-chip ${r.chosen ? 'wwm-chosen' : ''}">${r.trainer === 'mww' ? 'ESP32' : 'desktop/Pi'} · ${esc(r.label || r.id)} · thr ${thrOf(r)}${r.heldout && r.heldout.recall != null ? ` · hears ${Math.round(100 * r.heldout.recall)}% · ${r.heldout.near_fires ?? 0} sound-alike${r.heldout.near_fires === 1 ? '' : 's'}` : ''}</span>`; }).join('')}</div>
          <div class="wwm-muted">The ticked models from the Train table. Try it and Room watch run all of them side by side.</div>`
        : '<div class="wwm-muted">No finished models yet. Train one first.</div>'}
      </div>
      <div class="wwm-card">
        <div class="wwm-row wwm-between"><h2 style="margin:0">How it hears you, by microphone and way of speaking ${qbtn('breakdown')}</h2><select id="wwm-bd-run">${runs.slice().sort((a, b) => (sel.has(b.id) - sel.has(a.id))).map(r => `<option value="${esc(r.id)}">${esc(r.label || r.id)}</option>`).join('')}</select></div>
        <div id="wwm-bd" class="wwm-muted">Pick a run above.</div>
      </div>
      <div class="wwm-card">
        <div class="wwm-row wwm-between"><h2 style="margin:0">Try it ${qbtn('try')}</h2>
          <div class="wwm-row"><select id="wwm-try-mic"><option value="">this browser's default mic</option></select><button class="wwm-x" id="wwm-try-refresh" title="look again">↻</button></div></div>
        <div class="wwm-rec">
          <div class="wwm-phrase">${esc(p.phrase)}</div>
          <button class="wwm-btn big primary" id="wwm-try-btn" ${runs.length ? '' : 'disabled'}>● Say it</button>
          <div class="wwm-level"><i id="wwm-try-level"></i></div>
          <div class="wwm-muted" id="wwm-try-note">Press, say it, press again. Each ticked model says what it heard. Space bar works too. Try a sound-alike and the TV too.</div>
        </div>
        <div id="wwm-try-out"></div>
      </div>
      <div class="wwm-card" id="wwm-roomday-card">
        <h2>Room day ${qbtn('roomday')}</h2>
        <div class="wwm-row"><label>for <input type="number" id="wwm-rd-min" min="1" max="1440" value="360" style="width:70px"> minutes</label><span id="wwm-rd-mics" class="wwm-row wwm-wrap"><span class="wwm-muted">finding microphones…</span></span></div>
        <div class="wwm-row"><button class="wwm-btn primary" id="wwm-rd-go">Record the room on every ticked mic</button><button class="wwm-btn" id="wwm-rd-stop">Stop them all</button><span class="wwm-muted" id="wwm-rd-note">Every piece goes to the shared room library, tagged by mic; every wake word trains on it. PC mics record from this tab: keep it open.</span></div>
        <div id="wwm-rd-lib" class="wwm-muted"></div>
      </div>
      <div class="wwm-card" id="wwm-watch-card">
        <h2>Room watch ${qbtn('watch')}</h2>
        <div class="wwm-row">
          <label>Listen through <select id="wwm-watch-mic"><option value="browser">this browser's mic</option></select></label>
          <label>for <input type="number" id="wwm-watch-min" min="1" max="1440" value="60" style="width:70px"> minutes</label>
          <label title="every piece of what it hears becomes a room take: hours of this room through this microphone, for training and for judging false alarms"><input type="checkbox" id="wwm-watch-keep"> keep it all as room sound</label>
          <label id="wwm-watch-test-l" style="display:none" title="the board counts its own fires and answers nothing: a candidate model judged on a day of the room without her talking to the TV"><input type="checkbox" id="wwm-watch-test"> board test mode</label>
          <button class="wwm-btn primary" id="wwm-watch-go">Start watching</button>
        </div>
        <div class="wwm-muted" id="wwm-watch-note">The ticked models listen to your room: typing, TV, music, people. Every time one would have answered, the moment is kept for you to sort. One watch per microphone: start one on each satellite and on each PC mic (a PC mic is one browser tab each; keep that tab open). A Pi won't answer its wake word while it watches; an ESP32 that pushes its room keeps answering unless board test mode is on, and its own fires are counted beside the models'.</div>
        <div id="wwm-watch-out"></div>
      </div>`;
    bindHelp(el);
    el.querySelector('#wwm-test-pick').addEventListener('click', () => ctx.go('train'));

    // breakdown
    const bdSel = el.querySelector('#wwm-bd-run');
    const breakdown = async () => {
        const rid = bdSel.value; const box = el.querySelector('#wwm-bd');
        if (!rid) return;
        const r = runs.find(x => x.id === rid);
        const thr = thrOf(r);
        box.innerHTML = 'Scoring the held-out clips with this model… (a minute the first time)';
        try {
            const j = await api(`/projects/${p.slug}/runs/${rid}/judge`);
            const pos = j.rows.filter(x => x.kind === 'positive'), neg = j.rows.filter(x => x.kind === 'negative');
            const group = (rows, key) => { const g = {}; rows.forEach(x => { const k = x[key] || '?'; (g[k] = g[k] || []).push(x); }); return Object.entries(g).map(([k, xs]) => ({ k, n: xs.length, hit: xs.filter(x => x.score >= thr).length, min: Math.min(...xs.map(x => x.score)) })).sort((a, b) => a.hit / a.n - b.hit / b.n); };
            const tbl = (title, rows) => `<div><h3>${title}</h3><table class="wwm-table"><tbody>${rows.map(x => `<tr><td>${esc(x.k)}</td><td class="num">${x.hit}/${x.n}</td><td><div class="wwm-bar" style="width:120px"><i style="width:${100 * x.hit / x.n}%;background:${x.hit / x.n >= 0.9 ? 'var(--success, #6c6)' : x.hit / x.n >= 0.7 ? '#e9a33a' : 'var(--error, #e66)'}"></i></div></td></tr>`).join('')}</tbody></table></div>`;
            const misses = pos.filter(x => x.score < thr).sort((a, b) => a.score - b.score);
            const fires = neg.filter(x => x.score >= thr).sort((a, b) => b.score - a.score);
            box.innerHTML = `<div class="wwm-muted">${pos.length} held-out phrase clips, ${neg.length} held-out negatives, at threshold ${thr}.</div>
              <div class="wwm-grid2">${tbl('By microphone', group(pos, 'device'))}${tbl('By way of speaking', group(pos, 'style'))}</div>
              ${misses.length ? `<h3>Missed (${misses.length})</h3><div class="wwm-muted">${misses.map(x => `${esc(x.device)} · ${esc(x.style)} · score ${x.score}`).join('<br>')}</div>` : '<h3>Nothing missed</h3>'}
              ${fires.length ? `<h3>Negatives that fire (${fires.length})</h3><div class="wwm-muted">${fires.map(x => `${x.near ? '"' + esc(x.text || '') + '"' : 'other words'} · ${esc(x.device)} · score ${x.score}`).join('<br>')}</div>` : '<h3>No negative fires</h3>'}`;
        } catch (e) { box.innerHTML = `<span class="wwm-error">${esc(e.message)}</span>`; }
    };
    bdSel.addEventListener('change', breakdown);
    if (runs.length) breakdown();

    // try it
    const micSel = el.querySelector('#wwm-try-mic');
    let sats = [];
    const fillMics = async (ask) => {
        const keep = micSel.value;
        const list = await rec.inputs(ask);
        micSel.innerHTML = `<option value="">this browser's default mic</option>` + list.filter(d => d.label).map(d => `<option value="in:${esc(d.id)}">${esc(d.label)}</option>`).join('')
            + sats.map(m => `<option value="sat:${esc(m.id)}" ${m.online ? '' : 'disabled'}>${esc(m.id)}${m.online ? '' : ' (offline)'}</option>`).join('');
        if ([...micSel.options].some(o => o.value === keep)) micSel.value = keep; else { try { micSel.value = localStorage.getItem('wwm.mic') || ''; } catch { /* fine */ } }
    };
    api('/mics').then(r => { sats = r.mics || []; fillMics(false); }).catch(() => fillMics(false));
    el.querySelector('#wwm-try-refresh').addEventListener('click', () => fillMics(true));
    const btn = el.querySelector('#wwm-try-btn'), note = el.querySelector('#wwm-try-note'), out = el.querySelector('#wwm-try-out');
    let meter = null, busy = false, startedBy = 'mouse';
    const show = (rep) => {
        const ids = Object.keys(rep.runs || {});
        out.innerHTML = `<div class="wwm-muted" style="margin:6px 0">${rep.seconds}s · mic floor ${rep.mic?.floor_db} dB, peak ${rep.mic?.peak_db} dB</div>
          <table class="wwm-table"><thead><tr><th>Model</th><th class="num">Score</th><th class="num">Threshold</th><th>Heard it?</th><th>Along the clip</th></tr></thead><tbody>
          ${ids.map(id => { const x = rep.runs[id]; const r = runs.find(q => q.id === id); if (x.error) return `<tr><td>${esc(r?.label || id)}</td><td colspan="4" class="wwm-error">${esc(x.error)}</td></tr>`;
            const spark = (x.trace || []).map(v => `<i style="height:${Math.round(100 * v)}%"></i>`).join('');
            return `<tr><td>${esc(r?.label || id)} <span class="wwm-muted">${r?.trainer === 'mww' ? 'ESP32' : 'desktop/Pi'}</span></td><td class="num">${x.score.toFixed(3)}</td><td class="num">${x.threshold}</td><td>${x.fires ? '<span class="wwm-pill ok">yes</span>' : '<span class="wwm-pill bad">no</span>'}</td><td><div class="wwm-spark">${spark}</div></td></tr>`; }).join('')}</tbody></table>`;
    };
    const send = async (blob, key) => {
        if (!sel.size) { note.textContent = 'Tick at least one model above.'; return; }
        const form = new FormData();
        form.append('audio', blob, 'try.wav'); form.append('runs', [...sel].join(',')); if (key) form.append('key', '1');
        note.textContent = 'Scoring…';
        try { show(await api(`/projects/${p.slug}/try`, { method: 'POST', form })); note.textContent = 'Again?'; }
        catch (e) { note.textContent = e.message; }
    };
    const toggle = async (how = 'mouse') => {
        if (busy) return;
        const mic = micSel.value;
        if (mic.startsWith('sat:')) {
            busy = true; btn.disabled = true; note.textContent = `${mic.slice(4)}: say it when its ring turns yellow.`;
            if (!sel.size) { note.textContent = 'Tick at least one model above.'; busy = false; btn.disabled = false; return; }
            try { show(await api(`/projects/${p.slug}/try`, { method: 'POST', body: { device: mic.slice(4), runs: [...sel].join(',') } })); note.textContent = 'Again?'; }
            catch (e) { note.textContent = e.message; }
            busy = false; btn.disabled = false; return;
        }
        if (!rec.isRecording()) {
            try { await rec.start(mic.startsWith('in:') ? mic.slice(3) : undefined); } catch (e) { note.textContent = `Microphone: ${e.message}`; return; }
            startedBy = how; btn.textContent = '■ Stop'; btn.classList.add('rec'); note.textContent = 'Say it now…';
            meter = setInterval(() => { el.querySelector('#wwm-try-level').style.width = Math.min(100, rec.peak() * 140) + '%'; }, 80);
            return;
        }
        busy = true; clearInterval(meter); btn.textContent = '● Say it'; btn.classList.remove('rec'); el.querySelector('#wwm-try-level').style.width = '0';
        const take = await rec.stop(); busy = false;
        if (take) await send(take.blob, startedBy === 'key' || how === 'key');
    };
    btn.addEventListener('click', () => toggle('mouse'));
    watchCard(el, ctx, p, runs, sel, () => sats);
    roomDay(el, ctx, p, () => sats);
    keyHandler = (e) => { if (e.code === 'Space' && !e.repeat && !['INPUT', 'TEXTAREA', 'SELECT', 'BUTTON', 'A'].includes(document.activeElement?.tagName) && !document.querySelector('.modal-overlay, .modal, dialog[open]')) { e.preventDefault(); toggle('key'); } };
    document.addEventListener('keydown', keyHandler);
}


// ---- room watch ----
let watchPoll = null, watchRec = null;

function watchCard(el, ctx, p, runs, sel, satsFn) {
    const micSel = el.querySelector('#wwm-watch-mic'), go = el.querySelector('#wwm-watch-go'), out = el.querySelector('#wwm-watch-out'), note = el.querySelector('#wwm-watch-note');
    let inputs = [];
    const testRow = () => { const m = satsFn().find(x => x.id === micSel.value); el.querySelector('#wwm-watch-test-l').style.display = m && m.capture ? '' : 'none'; };
    // one watch per microphone: the satellites, and every PC mic as its own browser watch (one tab each)
    const fillMics = async () => {
        const keep = micSel.value;
        try { inputs = (await rec.inputs(false)).filter(d => d.label); } catch { inputs = []; }
        micSel.innerHTML = `<option value="browser">this browser's default mic</option>` + inputs.map(d => `<option value="browser:${esc(d.label.slice(0, 40))}" data-id="${esc(d.id)}">${esc(d.label)} (this browser)</option>`).join('')
            + satsFn().map(m => `<option value="${esc(m.id)}" ${m.online ? '' : 'disabled'}>${esc(m.id)}${m.online ? '' : ' (offline)'}${m.capture ? ' · pushes its room' : ''}</option>`).join('');
        micSel.value = [...micSel.options].some(o => o.value === keep) ? keep : 'browser'; testRow();
    };
    setTimeout(fillMics, 800);
    micSel.addEventListener('change', testRow);
    const fmt = (s) => s >= 3600 ? `${(s / 3600).toFixed(1)} h` : s >= 60 ? `${Math.round(s / 60)} min` : `${Math.round(s)} s`;
    const label = (rid) => { if (rid.startsWith('board:')) return `the board itself · ${rid.slice(6)}`; const r = runs.find(x => x.id === rid); return r ? (r.label || rid) + (r.trainer === 'mww' ? ' (ESP32)' : '') : rid; };
    const block = (w) => {
        const live = w.running && !w.ended;
        const hours = w.seconds / 3600;
        const per = {};
        for (const f of w.fires) for (const [rid, x] of Object.entries(f.runs)) if (x.fires) per[rid] = (per[rid] || 0) + 1;
        const open = w.fires.filter(f => !f.fate);
        return `<div class="wwm-watch" data-watch="${esc(w.id)}" style="margin:8px 0;padding:6px 0;border-top:1px solid var(--border, #333)">
          <div class="wwm-row wwm-between"><span class="wwm-muted">${live ? '● watching' : w.paused ? '⏸ paused' : 'watched'} through <b>${esc(w.device)}</b>${w.pushed ? ' (pushed by the board' + (w.test ? ', test mode' : '') + ')' : ''}: ${fmt(w.seconds)} heard, ${w.fires.length} fire${w.fires.length === 1 ? '' : 's'}${w.kept ? ` · ${w.kept} piece${w.kept === 1 ? '' : 's'} kept as room sound (${fmt(w.kept_seconds || 0)})` : ''}${w.rearmed ? ` · re-armed ${w.rearmed}×` : ''}${w.errors?.length ? ` · <span class="wwm-error">${esc(w.errors[w.errors.length - 1])}</span>` : ''}</span>${live ? `<button class="wwm-btn" data-stop="${esc(w.device)}">Stop</button>` : ''}</div>
          ${Object.keys(per).length || w.seconds > 60 ? `<table class="wwm-table"><thead><tr><th>Model</th><th class="num">Fires</th><th class="num">Per hour</th></tr></thead><tbody>
            ${(w.runs || '').split(',').filter(Boolean).concat(w.board ? [`board:${w.board}`] : []).map(rid => `<tr><td>${esc(label(rid))}</td><td class="num">${per[rid] || 0}</td><td class="num">${hours > 0 ? ((per[rid] || 0) / hours).toFixed(1) : '–'}</td></tr>`).join('')}</tbody></table>` : ''}
          ${open.length ? `<h3>To sort (${open.length})</h3>` + open.slice(-30).reverse().map(f => `<div class="wwm-fire" data-fire="${esc(f.id)}" data-wid="${esc(w.id)}">
              <audio controls preload="none" src="/api/plugin/wakeword-maker/projects/${p.slug}/watch/${w.id}/${f.id}/audio"></audio>
              <span>at ${fmt(f.at)} · ${Object.entries(f.runs).filter(([, x]) => x.fires).map(([rid, x]) => `${esc(label(rid))} ${x.score.toFixed(2)}`).join(', ')}</span>
              <button class="wwm-btn" data-fate="negative" title="keep as a negative sample">False alarm</button>
              <button class="wwm-btn" data-fate="positive" title="keep as a phrase sample">That was me</button>
              <button class="wwm-x" data-fate="discard" title="drop it">✕</button></div>`).join('') : (w.fires.length ? '<div class="wwm-muted">All sorted.</div>' : '')}</div>`;
    };
    const draw = (list) => {
        list = (list || []).filter(Boolean);
        out.innerHTML = list.map(block).join('');
        out.querySelectorAll('[data-fate]').forEach(b => b.addEventListener('click', async () => {
            const row = b.closest('.wwm-fire');
            try { await api(`/projects/${p.slug}/watch/${row.dataset.wid}/${row.dataset.fire}`, { method: 'POST', body: { as: b.dataset.fate } }); row.remove(); if (b.dataset.fate !== 'discard') ctx.toast(`Kept as a ${b.dataset.fate}`, 'success'); }
            catch (e) { ctx.toast(e.message, 'error'); }
        }));
        out.querySelectorAll('[data-stop]').forEach(b => b.addEventListener('click', () => stopOne(b.dataset.stop)));
        const anyLive = list.some(w => w.running && !w.ended);
        if (anyLive && !watchPoll) watchPoll = setInterval(refresh, 15000);
        if (!anyLive && watchPoll && !watchRec) { clearInterval(watchPoll); watchPoll = null; }
    };
    const refresh = () => api(`/projects/${p.slug}/watch`).then(r => draw(r.watches || (r.watch ? [r.watch] : []))).catch(() => {});
    refresh();
    // a PC mic: this tab records 20 s chunks and posts them with the mic's name; one tab per mic
    const browserLoop = async (device, deviceId, minutes) => {
        const end = Date.now() + minutes * 60000;
        const next = async () => {
            if (!watchRec || Date.now() > end) { await stopOne(device); return; }
            try { await rec.start(deviceId); } catch (e) { note.textContent = `Microphone: ${e.message}`; await stopOne(device); return; }
            watchRec.timer = setTimeout(async () => {
                if (!watchRec) return;
                const take = await rec.stop();
                next();                                             // the mic reopens at once; the chunk is scored while the next one records
                if (take && watchRec) {
                    const form = new FormData(); form.append('audio', take.blob, 'chunk.wav'); form.append('device', device);
                    try { await api(`/projects/${p.slug}/watch/chunk`, { method: 'POST', form }); refresh(); }
                    catch (e) { note.textContent = e.message; if (/no browser watch/i.test(e.message)) { watchRec = null; if (rec.isRecording()) await rec.stop(); refresh(); } }
                }
            }, 20000);
        };
        next();
    };
    const stopOne = async (device) => {
        if (watchRec && watchRec.device === device) { clearTimeout(watchRec.timer); watchRec = null; if (rec.isRecording()) await rec.stop(); }
        try { const r = await api(`/projects/${p.slug}/watch?device=${encodeURIComponent(device)}`, { method: 'DELETE' }); draw(r.watches); } catch (e) { ctx.toast(e.message, 'error'); }
    };
    go.addEventListener('click', async () => {
        const device = micSel.value, minutes = parseInt(el.querySelector('#wwm-watch-min').value) || 60;
        const keep = el.querySelector('#wwm-watch-keep').checked, test = el.querySelector('#wwm-watch-test').checked && el.querySelector('#wwm-watch-test-l').style.display !== 'none';
        if (!sel.size && !keep) return ctx.toast('Tick at least one model above, or tick keep it all as room sound.', 'warning');
        if (device.startsWith('browser') && watchRec) return ctx.toast('This tab already records a mic. Open the page in another tab for a second PC mic.', 'warning');
        try {
            const r = await api(`/projects/${p.slug}/watch`, { method: 'POST', body: { device, minutes, runs: [...sel].join(','), keep, test } });
            if (device.startsWith('browser')) { const opt = [...micSel.options].find(o => o.value === device); watchRec = { device, deviceId: opt?.dataset.id || undefined, timer: null }; browserLoop(device, watchRec.deviceId, minutes); }
            refresh();
        } catch (e) { ctx.toast(e.message, 'error'); }
    });
}


// ---- room day: every microphone at once, into the shared room library ----
// Satellites start through one door. PC mics are this tab's: one stream each (their own AudioContext), 20 s pieces,
// posted with the mic's name. Keep the tab open; closing it ends only the PC mics.
const pcMics = {};          // device -> {stop}

async function pcStream(slug, device, deviceId, minutes, onNote) {
    const audio = { channelCount: 1, echoCancellation: false, noiseSuppression: false, autoGainControl: false };
    if (deviceId) audio.deviceId = { exact: deviceId };
    const stream = await navigator.mediaDevices.getUserMedia({ audio });
    const ctx = new AudioContext(); const source = ctx.createMediaStreamSource(stream); const proc = ctx.createScriptProcessor(4096, 1, 1);
    let chunks = [], alive = true;
    const end = Date.now() + minutes * 60000;
    proc.onaudioprocess = (e) => { if (alive) chunks.push(new Float32Array(e.inputBuffer.getChannelData(0))); };
    source.connect(proc); proc.connect(ctx.destination);
    const flush = async () => {
        const mine = chunks; chunks = [];
        const n = mine.reduce((a, c) => a + c.length, 0);
        if (n < ctx.sampleRate) return;
        const all = new Float32Array(n); let o = 0; for (const c of mine) { all.set(c, o); o += c.length; }
        const form = new FormData(); form.append('audio', encodeWAV(all, ctx.sampleRate), 'piece.wav'); form.append('device', device);
        try { await api(`/projects/${slug}/watch/chunk`, { method: 'POST', form }); }
        catch (e) { onNote(`${device}: ${e.message}`); if (/no browser watch/i.test(e.message)) stop(); }
    };
    const timer = setInterval(() => { if (Date.now() > end) { stop(); return; } flush(); }, 20000);
    const stop = async () => { if (!alive) return; alive = false; clearInterval(timer); try { proc.disconnect(); source.disconnect(); } catch { /* fine */ } stream.getTracks().forEach(t => t.stop()); try { await ctx.close(); } catch { /* fine */ } delete pcMics[device]; };
    pcMics[device] = { stop };
    return stop;
}

function roomDay(el, ctx, p, satsFn) {
    const box = el.querySelector('#wwm-rd-mics'), note = el.querySelector('#wwm-rd-note'), lib = el.querySelector('#wwm-rd-lib');
    let inputs = [];
    const fill = async () => {
        try { inputs = (await rec.inputs(true)).filter(d => d.label); } catch { inputs = []; }
        box.innerHTML = satsFn().map(m => `<label><input type="checkbox" data-rd="sat" value="${esc(m.id)}" ${m.online ? 'checked' : 'disabled'}> ${esc(m.id)}${m.online ? '' : ' (offline)'}</label>`).join('')
            + inputs.map(d => `<label><input type="checkbox" data-rd="pc" value="browser:${esc(d.label.slice(0, 40))}" data-id="${esc(d.id)}" checked> ${esc(d.label)} (this PC)</label>`).join('')
            || '<span class="wwm-muted">no microphones found</span>';
    };
    setTimeout(fill, 900);
    const library = async () => {
        try {
            const r = await api(`/room?project=${encodeURIComponent(p.slug)}`);
            lib.innerHTML = r.devices?.length ? `Room library: <b>${(r.minutes / 60).toFixed(1)} h</b> over ${r.devices.length} mic${r.devices.length === 1 ? '' : 's'}: ${r.devices.map(d => `${esc(d.device)} ${(d.minutes / 60).toFixed(1)} h`).join(', ')}${r.dropped_for ? ` · ${r.dropped_for} piece${r.dropped_for === 1 ? '' : 's'} dropped for this phrase` : ''}. Pieces and deletion live on Record, under Ambient.` : 'Room library: empty so far.';
        } catch { /* fine */ }
    };
    library();
    el.querySelector('#wwm-rd-go').addEventListener('click', async () => {
        const minutes = parseInt(el.querySelector('#wwm-rd-min').value) || 360;
        const sats = [...box.querySelectorAll('[data-rd="sat"]:checked')].map(i => i.value);
        const pcs = [...box.querySelectorAll('[data-rd="pc"]:checked')].map(i => ({ device: i.value, id: i.dataset.id }));
        if (!sats.length && !pcs.length) return ctx.toast('Tick at least one microphone.', 'warning');
        let started = [], refused = [];
        if (sats.length) {
            try { const r = await api(`/projects/${p.slug}/room-day`, { method: 'POST', body: { minutes, devices: sats } }); started = r.started; refused = r.refused; }
            catch (e) { ctx.toast(e.message, 'error'); }
        }
        for (const m of pcs) {
            if (pcMics[m.device]) { refused.push({ device: m.device, error: 'already recording from this tab' }); continue; }
            try {
                await api(`/projects/${p.slug}/watch`, { method: 'POST', body: { device: m.device, minutes, runs: '', keep: true } });
                await pcStream(p.slug, m.device, m.id, minutes, (t) => { note.textContent = t; });
                started.push({ device: m.device });
            } catch (e) { refused.push({ device: m.device, error: e.message }); }
        }
        note.textContent = `${started.length} microphone${started.length === 1 ? '' : 's'} recording for ${minutes} min${refused.length ? ` · refused: ${refused.map(r => `${r.device} (${r.error})`).join('; ')}` : ''}`;
        setTimeout(() => el.querySelector('#wwm-watch-card')?.scrollIntoView({ behavior: 'smooth' }), 300);
        setTimeout(() => api(`/projects/${p.slug}/watch`).then(() => {}).catch(() => {}), 1000);
    });
    el.querySelector('#wwm-rd-stop').addEventListener('click', async () => {
        for (const d of Object.keys(pcMics)) await pcMics[d].stop();
        try { await api(`/projects/${p.slug}/room-day`, { method: 'DELETE' }); } catch (e) { ctx.toast(e.message, 'error'); }
        note.textContent = 'Stopped every watch of this wake word.';
        library();
    });
}
