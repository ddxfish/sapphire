// Test tab: the ticked models, how each hears you by microphone and tag, and a live utterance against all of
// them at once (your mic or a satellite's). The room, for hours, is the Background tab's Room day.
import { api, esc } from '../api.js';
import { qbtn, bindHelp } from '../help.js';
import * as rec from '../recorder.js';

let keyHandler = null, meter = null;

export function render(el, ctx) {
    const p = ctx.state.project;
    if (!p) { el.innerHTML = '<div class="wwm-empty">Create a phrase first.</div>'; return; }
    el.innerHTML = '<div class="wwm-loading">Loading…</div>';
    api(`/projects/${p.slug}/runs`).then(r => { if (el.isConnected) draw(el, ctx, p, r); }).catch(e => { el.innerHTML = `<div class="wwm-error">${esc(e.message)}</div>`; });   // left already: no key handler
    return () => { if (keyHandler) document.removeEventListener('keydown', keyHandler); keyHandler = null; if (meter) clearInterval(meter); meter = null; if (rec.isRecording()) rec.stop(); };
}

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
          <div class="wwm-muted">The ticked models from the Train table. Try it runs all of them side by side; a Room day on Background keeps them on watch for hours.</div>`
        : '<div class="wwm-muted">No finished models yet. Train one first.</div>'}
      </div>
      <div class="wwm-card">
        <div class="wwm-row wwm-between"><h2 style="margin:0">How it hears you, by microphone and tag ${qbtn('breakdown')}</h2><select id="wwm-bd-run">${runs.slice().sort((a, b) => (sel.has(b.id) - sel.has(a.id))).map(r => `<option value="${esc(r.id)}">${esc(r.label || r.id)}</option>`).join('')}</select></div>
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
            const group = (rows, key) => { const g = {}; rows.forEach(x => { const k = x[key] || (key === 'who' ? '(nobody tagged)' : '?'); (g[k] = g[k] || []).push(x); }); return Object.entries(g).map(([k, xs]) => ({ k, n: xs.length, hit: xs.filter(x => x.score >= thr).length, min: Math.min(...xs.map(x => x.score)) })).sort((a, b) => a.hit / a.n - b.hit / b.n); };
            const tagLine = (x) => [x.device, x.how !== 'normal' && x.how, x.where !== 'close' && x.where, x.noise !== 'quiet' && x.noise, x.who].filter(Boolean).map(esc).join(' · ') || 'normal';
            const tbl = (title, rows) => `<div><h3>${title}</h3><table class="wwm-table"><tbody>${rows.map(x => `<tr><td>${esc(x.k)}</td><td class="num">${x.hit}/${x.n}</td><td><div class="wwm-bar" style="width:120px"><i style="width:${100 * x.hit / x.n}%;background:${x.hit / x.n >= 0.9 ? 'var(--success, #6c6)' : x.hit / x.n >= 0.7 ? '#e9a33a' : 'var(--error, #e66)'}"></i></div></td></tr>`).join('')}</tbody></table></div>`;
            const misses = pos.filter(x => x.score < thr).sort((a, b) => a.score - b.score);
            const fires = neg.filter(x => x.score >= thr).sort((a, b) => b.score - a.score);
            box.innerHTML = `<div class="wwm-muted">${pos.length} held-out phrase clips, ${neg.length} held-out negatives, at threshold ${thr}.</div>
              <div class="wwm-grid2">${[['device', 'By microphone'], ['how', 'How it was said'], ['where', 'From where'], ['noise', 'What was on'], ['who', 'Who']].map(([k, title]) => { const g = group(pos, k); return (g.length > 1 || k === 'device') ? tbl(title, g) : ''; }).join('')}</div>
              ${misses.length ? `<h3>Missed (${misses.length})</h3><div class="wwm-muted">${misses.map(x => `${tagLine(x)} · score ${x.score}`).join('<br>')}</div>` : '<h3>Nothing missed</h3>'}
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
    let busy = false, startedBy = 'mouse';
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
    keyHandler = (e) => { if (e.code === 'Space' && !e.repeat && !['INPUT', 'TEXTAREA', 'SELECT', 'BUTTON', 'A'].includes(document.activeElement?.tagName) && !document.querySelector('.modal-overlay, .modal, dialog[open]')) { e.preventDefault(); toggle('key'); } };
    document.addEventListener('keydown', keyHandler);
}
