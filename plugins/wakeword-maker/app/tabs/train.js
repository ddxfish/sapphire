// Train tab: which model, how hard, with or without the held-out slice; a sweep when you want the knobs walked for
// you; and the runs, each with its curves and its threshold table.
import { api, esc } from '../api.js';
import { qbtn, bindHelp } from '../help.js';
import { showConfirm } from '/static/shared/modal.js';
import * as variationsTab from './variations.js';

let poll = null;

export function render(el, ctx) {
    const p = ctx.state.project;
    if (!p) { el.innerHTML = '<div class="wwm-empty">Create a phrase first.</div>'; return; }
    el.innerHTML = '<div class="wwm-loading">Loading…</div>';
    Promise.all([api('/train/options'), api(`/projects/${p.slug}/runs`), api('/datasets')])
        .then(([opt, runs, ds]) => draw(el, ctx, p, opt, runs, ds)).catch(e => { el.innerHTML = `<div class="wwm-error">${esc(e.message)}</div>`; });
    return { cleanup: () => { if (poll) clearInterval(poll); poll = null; } };     // a finished job redraws the tab from the shell
}

function draw(el, ctx, p, opt, runsData, ds) {
    const envReady = ['ready', 'stale'].includes(ctx.state.status?.env?.state);
    const have = Object.fromEntries((ds.datasets || []).map(d => [d.id, d.state === 'ready']));
    const owwReady = have.oww_models && have.oww_negatives && have.oww_validation;
    const mwwReady = !!have.mww_negatives;
    const saved = p.settings?.train || {};
    const want = saved.trainers || { oww: true, mww: mwwReady };
    const cfg = { ...opt.defaults, ...(saved.cfg || {}) };
    const preset = saved.preset || 'standard';
    const useHold = saved.holdout !== false;
    const rounds = saved.rounds || 2;
    const synth = saved.synth || 'all';
    const running = runsData.running || [];
    const gpu = !!ctx.state.status?.gpu;
    el.innerHTML = `
      <div id="wwm-var-host"></div>
      <div class="wwm-card">
        <h2>Train ${qbtn('train')}</h2>
        <div class="wwm-row wwm-trainers">
          <label><input type="checkbox" id="wwm-t-oww" ${want.oww !== false ? 'checked' : ''} ${owwReady ? '' : 'disabled'}> Desktop and Pi <span class="wwm-muted">(openWakeWord)</span></label>
          <label><input type="checkbox" id="wwm-t-mww" ${want.mww && mwwReady ? 'checked' : ''} ${mwwReady ? '' : 'disabled'}> ESP32 <span class="wwm-muted">(microWakeWord)</span></label>
        </div>
        <ul class="wwm-checks">${owwReady ? '' : `<li class="warn">⚠ Desktop and Pi needs the three openWakeWord datasets from Settings: feature models, negative features (17 GB), validation features.</li>`}${mwwReady ? '' : `<li class="warn">⚠ ESP32 needs the microWakeWord negative spectrograms from Settings (5.7 GB).</li>`}</ul>
        <div class="wwm-row" style="margin-top:8px">
          <div class="wwm-seg" id="wwm-tpreset">
            <button data-v="quick">Quick<small>10k steps · ~${gpu ? '5' : '40'} min</small></button>
            <button data-v="standard">Standard<small>50k steps · ~${gpu ? '25' : '200'} min</small></button>
            <button data-v="thorough">Thorough<small>100k steps, bigger net</small></button>
          </div>
        </div>
        <div class="wwm-row" style="margin-top:8px">
          <label><input type="checkbox" id="wwm-holdout" ${useHold ? 'checked' : ''}> validate on the held-out slice</label>${qbtn('validate')}
          <label>Variations per clip <input type="number" id="wwm-rounds" min="1" max="6" value="${rounds}" style="width:60px"></label>${qbtn('rounds')}
          <label>Synthetic voices <select id="wwm-synth">${[['all', 'all'], ['piper', 'Piper only'], ['kokoro', 'Kokoro only'], ['none', 'none: your recordings only']].map(([v, t]) => `<option value="${v}" ${synth === v ? 'selected' : ''}>${t}</option>`).join('')}</select></label>${qbtn('synth')}
          <label>Label <input type="text" id="wwm-tlabel" placeholder="e.g. natural, standard" style="min-width:160px"></label>
          <label>Repeats <input type="number" id="wwm-repeats" min="1" max="5" value="1" style="width:56px"></label>${qbtn('repeats')}
        </div>
        <div class="wwm-row" style="margin-top:8px">
          <button class="wwm-btn primary big" id="wwm-train-go" ${envReady && (owwReady || mwwReady) ? '' : 'disabled'}>${running.length || (runsData.queued || []).length ? 'Add to queue' : 'Train'}</button>
          <button class="wwm-btn" id="wwm-sweep-toggle" ${envReady && owwReady ? '' : 'disabled'} title="desktop and Pi model only, for now">Sweep the knobs…</button>
          <button class="wwm-btn ghost" id="wwm-adv-toggle">▸ Advanced</button>
          ${running.length ? `<span class="wwm-muted">running: ${esc(running[0].msg || running[0].kind)}</span>` : envReady ? '' : '<span class="wwm-muted">build the environment on the Settings tab first</span>'}
        </div>
        <div id="wwm-adv" style="display:none" class="wwm-advgrid">
          <label>Model <select id="wwm-c-model_type"><option value="dnn">dnn (dense)</option><option value="rnn">rnn (LSTM)</option></select></label>
          <label>Layer size <input type="number" id="wwm-c-layer_size" min="8" max="512" step="8" value="${cfg.layer_size}"></label>
          <label>Blocks <input type="number" id="wwm-c-n_blocks" min="1" max="4" value="${cfg.n_blocks}"></label>
          <label>Steps <input type="number" id="wwm-c-steps" min="1000" max="300000" step="1000" value="${cfg.steps}"></label>
          <label>Learning rate <input type="number" id="wwm-c-lr" min="0.00001" max="0.01" step="0.00001" value="${cfg.lr}"></label>
          <label>Max negative weight <input type="number" id="wwm-c-max_negative_weight" min="1" max="10000" step="50" value="${cfg.max_negative_weight}"></label>
          <label>Target false alarms/h <input type="number" id="wwm-c-target_fp_per_hour" min="0.01" max="5" step="0.05" value="${cfg.target_fp_per_hour}"></label>
          <label>Seed <input type="number" id="wwm-c-seed" min="0" max="99999" value="${cfg.seed}"></label>
        </div>
        <div id="wwm-sweep" style="display:none" class="wwm-sweep">
          <h3>Sweep ${qbtn('sweep')}</h3>
          <div class="wwm-row">
            <label>Trials <input type="number" id="wwm-s-trials" min="2" max="60" value="12" style="width:64px"></label>
            <label>Steps per trial <input type="number" id="wwm-s-budget" min="1000" max="50000" step="1000" value="8000" style="width:90px"></label>
            <label>At a time <input type="number" id="wwm-s-parallel" min="1" max="4" value="${gpu ? 2 : 1}" style="width:56px"></label>
          </div>
          <div class="wwm-row">${Object.keys(opt.space).map(k => `<label><input type="checkbox" data-space="${k}" checked> ${k.replace(/_/g, ' ')}</label>`).join('')}</div>
          <div class="wwm-row"><button class="wwm-btn primary" id="wwm-sweep-go">Run the sweep</button><span class="wwm-muted">the best trial then trains in full with the preset above</span></div>
        </div>
      </div>
      <div class="wwm-card">
        <h2>Runs</h2>
        <div id="wwm-runs"></div>
      </div>
      <div id="wwm-run-detail"></div>`;
    bindHelp(el);
    variationsTab.render(el.querySelector('#wwm-var-host'), ctx, true);

    const seg = el.querySelector('#wwm-tpreset');
    const setPreset = (v) => { seg.querySelectorAll('button').forEach(b => b.classList.toggle('on', b.dataset.v === v)); const pr = opt.presets[v]; if (pr) for (const [k, val] of Object.entries(pr)) { const i = el.querySelector(`#wwm-c-${k}`); if (i) i.value = val; } };
    setPreset(preset);
    seg.querySelectorAll('button').forEach(b => b.addEventListener('click', () => { setPreset(b.dataset.v); save(); }));
    const mt = el.querySelector('#wwm-c-model_type');
    mt.value = cfg.model_type;
    const sizeKnobs = () => { for (const k of ['layer_size', 'n_blocks']) el.querySelector(`#wwm-c-${k}`).closest('label').style.display = mt.value === 'rnn' ? 'none' : ''; };   // the LSTM is a fixed net: the size knobs do nothing for it
    sizeKnobs();
    mt.addEventListener('change', sizeKnobs);
    el.querySelector('#wwm-adv-toggle').addEventListener('click', (e) => { const a = el.querySelector('#wwm-adv'); const open = a.style.display === 'none'; a.style.display = open ? '' : 'none'; e.target.textContent = open ? '▾ Advanced' : '▸ Advanced'; });
    el.querySelector('#wwm-sweep-toggle').addEventListener('click', () => { const a = el.querySelector('#wwm-sweep'); a.style.display = a.style.display === 'none' ? '' : 'none'; });
    const readCfg = () => {
        const c = {};
        for (const k of ['model_type', 'layer_size', 'n_blocks', 'steps', 'lr', 'max_negative_weight', 'target_fp_per_hour', 'seed']) {
            const i = el.querySelector(`#wwm-c-${k}`); c[k] = i.type === 'number' ? parseFloat(i.value) : i.value;
        }
        return c;
    };
    const read = () => ({ preset: seg.querySelector('button.on')?.dataset.v || 'standard', holdout: el.querySelector('#wwm-holdout').checked, rounds: parseInt(el.querySelector('#wwm-rounds').value) || 2, cfg: readCfg(), synth: el.querySelector('#wwm-synth').value,
        trainers: { oww: el.querySelector('#wwm-t-oww').checked, mww: el.querySelector('#wwm-t-mww').checked },
        recipe: ({ harsh: 'rough' })[p.settings?.augment?.preset] || p.settings?.augment?.preset || 'natural' });
    const save = async () => { try { const r = await api(`/projects/${p.slug}`, { method: 'PUT', body: { settings: { train: read() } } }); Object.assign(p, r.project); } catch { /* fine */ } };
    el.querySelectorAll('#wwm-adv input, #wwm-adv select, #wwm-holdout, #wwm-rounds, #wwm-synth, #wwm-t-oww, #wwm-t-mww').forEach(i => i.addEventListener('change', save));

    el.querySelector('#wwm-train-go').addEventListener('click', async () => {
        const t = read();
        const go = async () => {
            const label = el.querySelector('#wwm-tlabel').value.trim();
            const n = Math.max(1, Math.min(5, parseInt(el.querySelector('#wwm-repeats').value) || 1));
            if (!t.trainers.oww && !t.trainers.mww) return ctx.toast('Tick at least one model', 'error');
            try {
                for (let s = 1; s <= n; s++) {   // repeats differ only by seed: same cell three times tells noise from signal
                    const a = n > 1 ? { ...t, cfg: { ...t.cfg, seed: s }, label: label ? `${label} · s${s}` : `s${s}` } : { ...t, label };
                    if (t.trainers.oww) await ctx.startJob('train', a);
                    if (t.trainers.mww) await ctx.startJob('train_mww', a);
                }
                ctx.toast(`${[t.trainers.oww && 'desktop/Pi', t.trainers.mww && 'ESP32'].filter(Boolean).join(' and ')}${n > 1 ? ` × ${n}` : ''} queued; each runs when the one before it is done.`, 'info');
                setTimeout(() => render(el, ctx), 800);
            } catch (e) { ctx.toast(e.message, 'error'); }
        };
        if (!t.holdout) showConfirm('Without the held-out slice, every recording trains and the recall number comes from synthetic clips only. Test will be less honest about your own voice. Continue?', go, { title: 'No held-out slice', saveLabel: 'Train anyway' });
        else go();
    });
    el.querySelector('#wwm-sweep-go').addEventListener('click', async () => {
        const t = read();
        const space = [...el.querySelectorAll('[data-space]:checked')].map(i => i.dataset.space);
        const args = { ...t, trials: parseInt(el.querySelector('#wwm-s-trials').value) || 12, budget_steps: parseInt(el.querySelector('#wwm-s-budget').value) || 8000,
            parallel: parseInt(el.querySelector('#wwm-s-parallel').value) || 1, space, label: el.querySelector('#wwm-tlabel').value.trim() };
        try { await ctx.startJob('tune', args); ctx.toast(`Sweep started: ${args.trials} trials.`, 'info'); setTimeout(() => render(el, ctx), 800); }
        catch (e) { ctx.toast(e.message, 'error'); }
    });

    drawRuns(el, ctx, p, runsData, opt);
    if (poll) clearInterval(poll);
    if (running.length || (runsData.queued || []).length) poll = setInterval(() => { if (!document.hidden) api(`/projects/${p.slug}/runs`).then(r => drawRuns(el, ctx, p, r, opt)).catch(() => {}); }, 10000);
}

const fmt = (v, d = 2) => v == null ? '–' : (typeof v === 'number' ? v.toFixed(d) : v);

function bestRow(rows) {
    if (!rows?.length) return null;
    const ok = rows.filter(r => r.recall != null && (r.fp_per_hour || 0) <= 0.5 && (r.own_fa_per_hour || 0) <= 1);
    return (ok.length ? ok : rows).slice().sort((a, b) => (b.recall || 0) - (a.recall || 0))[0];
}

function drawRuns(el, ctx, p, data, opt) {
    const box = el.querySelector('#wwm-runs');
    if (!box) return;
    const runs = data.runs || [];
    const f = data.features;
    const q = data.queued || [];
    let sel = new Set();
    try { sel = new Set(JSON.parse(localStorage.getItem('wwm.testsel') || '[]')); } catch { /* fine */ }
    box.innerHTML = `${q.length ? `<div class="wwm-queue"><b>Queued:</b> ${q.map(j => `<span class="wwm-chip">${esc({ train: 'desktop/Pi', train_mww: 'ESP32', tune: 'sweep' }[j.kind] || j.kind)} · ${esc(j.args?.preset || '')}${j.args?.recipe ? ' · ' + esc(j.args.recipe) : ''}${j.args?.synth && j.args.synth !== 'all' ? ' · ' + esc(String(j.args.synth)) : ''}${j.args?.label ? ' · ' + esc(j.args.label) : ''}<button data-unqueue="${esc(j.id)}" title="remove from the queue">✕</button></span>`).join('')}</div>` : ''}
      ${f ? `<div class="wwm-muted" style="margin-bottom:6px">Feature set on disk: ${(f.counts?.positive || 0).toLocaleString()} positive rows, ${(f.counts?.adversarial || 0).toLocaleString()} sound-alike rows, ${(f.counts?.ambient || 0).toLocaleString()} room windows · ${f.rounds} variation(s) per clip · ${f.recipe}${f.synth && f.synth !== 'all' ? ' · voices: ' + esc(String(f.synth)) : ''} · ${f.holdout ? 'held-out slice kept apart' : 'no held-out slice'} · built in ${f.seconds}s</div>` : ''}
      ${(() => { const stale = runs.filter(r => r.heldout?.stale).length, unjudged = runs.filter(r => r.state === 'done' && r.has_model && !r.heldout && !r.id.startsWith('builtin-')).length; return (stale || unjudged) ? `<div class="wwm-row" style="margin-bottom:6px"><button class="wwm-btn" id="wwm-rejudge">Re-judge ${stale + unjudged} model${stale + unjudged === 1 ? '' : 's'}</button><span class="wwm-muted">${stale ? `${stale} verdict${stale === 1 ? '' : 's'} came from an older held-out slice (a reshuffle or a dropped clip since). ` : ''}${unjudged ? `${unjudged} finished model${unjudged === 1 ? '' : 's'} never judged. ` : ''}A few seconds per model.</span></div>` : ''; })()}
      ${runs.length ? `<table class="wwm-table wwm-score"><thead><tr><th title="tick to test it live on Test">test</th><th></th><th>Run</th><th>Model</th><th>Label</th><th>What</th><th>Recipe</th><th>State</th><th class="num" title="held-out clips of you it heard, streaming judge">Hears you</th><th class="num" title="held-out sound-alikes it fired on (your near misses)">Sound-alikes</th><th class="num" title="false alarms per hour on the standard negative set">FA/h</th><th class="num" title="the judge's pick; type to override">Threshold</th><th class="num">min</th><th title="the one Install puts on the devices, one per model type">use</th><th></th></tr></thead><tbody>
      ${runs.map(r => { const h = r.heldout; const b = bestRow(r.rows); const thr = r.threshold ?? r.cutoff ?? h?.threshold ?? b?.threshold ?? 0.5; const near = h ? h.near_fires : null; const testable = r.state === 'done' && r.has_model;
        return `<tr data-run="${esc(r.id)}" class="wwm-runrow ${r.chosen ? 'wwm-chosen' : ''}" title="click for the curves and the threshold tables">
        <td>${testable ? `<input type="checkbox" data-sel="${esc(r.id)}" ${sel.has(r.id) ? 'checked' : ''}>` : ''}</td><td><span class="wwm-info">i</span></td><td>${esc(r.id)}</td><td>${r.trainer === 'mww' ? 'ESP32' : 'desktop/Pi'}</td><td>${esc(r.label || '')}</td><td>${r.kind === 'tune' ? `sweep ${r.trials}×${(r.budget_steps / 1000).toFixed(0)}k` : esc(r.preset || '')}${r.cfg && r.cfg.model_type ? ` · ${esc(r.cfg.model_type)}${r.cfg.model_type === 'rnn' ? '' : ` ${r.cfg.layer_size}×${r.cfg.n_blocks}`}` : (r.cfg && r.cfg.pointwise_filters ? ` · mixednet ${esc(r.cfg.pointwise_filters)}` : '')}</td>
        <td>${esc(r.recipe || '')}${r.synth && r.synth !== 'all' ? ` · ${esc(Array.isArray(r.synth) ? 'voice set' : r.synth)}` : ''}${r.holdout === false ? ' · no holdout' : ''}</td><td><span class="wwm-pill ${r.state === 'done' ? 'ok' : r.state === 'failed' ? 'bad' : 'warn'}">${esc(r.state)}</span></td>
        <td class="num ${h?.stale ? 'wwm-stale' : ''}" ${h?.stale ? 'title="judged on an older held-out slice"' : h && h.recall == null ? 'title="no held-out recordings of you to judge on"' : ''}>${h ? (h.recall == null ? '–' : (100 * h.recall).toFixed(0) + '%') + (h.stale ? ' ⟳' : '') : (b ? `<span class="wwm-muted" title="trainer's own number; not judged yet">${(100 * b.recall).toFixed(0)}%</span>` : '–')}</td>
        <td class="num ${near == null ? '' : near === 0 ? 'wwm-good' : near === 1 ? 'wwm-meh' : 'wwm-bad'}">${h && near != null ? `${near}/${h.n_near}` : '–'}</td>
        <td class="num" ${h && h.fp_per_hour == null ? 'title="the trainer did not measure false alarms at this threshold"' : ''}>${h ? (h.fp_per_hour != null ? fmt(h.fp_per_hour) : '–') : (b ? `<span class="wwm-muted" title="trainer's own number at its best threshold; not judged yet">${fmt(b.fp_per_hour)}</span>` : '–')}</td>
        <td class="num">${testable ? `<input type="number" data-thr="${esc(r.id)}" min="0.05" max="0.999" step="0.01" value="${thr}" style="width:64px">${h && h.clean === false ? '<span class="wwm-warn" title="no threshold met the rule (no other-word fires, at most one sound-alike, under 0.5 false alarms an hour); this is the best of a flawed set">⚠</span>' : ''}${h && h.neg_fires ? `<span class="wwm-warn" title="${h.neg_fires} other-word recording(s) fire at this threshold">!</span>` : ''}` : '–'}</td>
        <td class="num">${r.seconds ? Math.round(r.seconds / 60) : ''}</td><td>${testable ? `<button class="wwm-x ${r.chosen ? 'on' : ''}" data-choose="${esc(r.id)}" title="${r.chosen ? 'Install uses this one' : 'use this one for Install'}">${r.chosen ? '★' : '☆'}</button>` : ''}</td><td class="num"><button class="wwm-x" data-del-run="${esc(r.id)}" title="delete this run">✕</button></td></tr>`; }).join('')}
      </tbody></table><div class="wwm-muted">Hears you and Sound-alikes come from the streaming judge on your held-out clips, the same one Test uses; every finished run is judged on its own. Tick models to try them live on Test. ★ marks the one Install puts on the devices.</div>` : '<div class="wwm-muted">No runs yet.</div>'}`;
    box.querySelector('#wwm-rejudge')?.addEventListener('click', async () => {
        try { await ctx.startJob('judge', {}); ctx.toast('Judging every finished model on the held-out slice as it stands…', 'info'); } catch (e) { ctx.toast(e.message, 'error'); }
    });
    box.querySelectorAll('[data-sel]').forEach(i => i.addEventListener('change', () => { i.checked ? sel.add(i.dataset.sel) : sel.delete(i.dataset.sel); try { localStorage.setItem('wwm.testsel', JSON.stringify([...sel])); } catch { /* fine */ } }));
    box.querySelectorAll('[data-thr]').forEach(i => i.addEventListener('change', async () => {
        try { await api(`/projects/${p.slug}/runs/${i.dataset.thr}`, { method: 'PUT', body: { threshold: parseFloat(i.value) } }); ctx.toast(`Threshold ${i.value} saved`, 'success'); } catch (e) { ctx.toast(e.message, 'error'); }
    }));
    box.querySelectorAll('[data-choose]').forEach(b => b.addEventListener('click', async () => {
        const rid = b.dataset.choose; const r = runs.find(x => x.id === rid);
        try {
            for (const o of runs) if (o.trainer === r.trainer && o.chosen && o.id !== rid) await api(`/projects/${p.slug}/runs/${o.id}`, { method: 'PUT', body: { chosen: false } });
            await api(`/projects/${p.slug}/runs/${rid}`, { method: 'PUT', body: { chosen: !r.chosen } });
            api(`/projects/${p.slug}/runs`).then(d => drawRuns(el, ctx, p, d, opt));
        } catch (e) { ctx.toast(e.message, 'error'); }
    }));
    box.querySelectorAll('.wwm-runrow').forEach(tr => tr.addEventListener('click', (e) => { if (e.target.closest('button, input')) return; box.querySelectorAll('.wwm-runrow').forEach(x => x.classList.toggle('on', x === tr)); showRun(el, ctx, p, tr.dataset.run, opt); }));
    box.querySelectorAll('[data-unqueue]').forEach(b => b.addEventListener('click', async () => { try { await api(`/jobs/${b.dataset.unqueue}`, { method: 'DELETE' }); render(el, ctx); } catch (e) { ctx.toast(e.message, 'error'); } }));
    box.querySelectorAll('[data-del-run]').forEach(b => b.addEventListener('click', () => showConfirm(`Delete run ${b.dataset.delRun} and its model?`, async () => {
        try { await api(`/projects/${p.slug}/runs/${b.dataset.delRun}`, { method: 'DELETE' }); render(el, ctx); } catch (e) { ctx.toast(e.message, 'error'); }
    }, { title: 'Delete run', saveLabel: 'Delete' })));
    const open = el.querySelector('#wwm-run-detail')?.dataset.run;
    const live = runs.find(r => r.state === 'running');
    if (live && (!open || open === live.id)) showRun(el, ctx, p, live.id, opt);
}

// --- a run: curves, thresholds, trials -----------------------------------------------------------------
const W = 520, H = 150, L = 36, B = 18;
function line(series, { x = 'step', ylabel = '', ymax = null, logy = false } = {}) {
    const pts = series.flatMap(s => s.data.filter(d => d[s.key] != null && d[x] != null));
    if (!pts.length) return '<div class="wwm-muted">no points yet</div>';
    const xs = pts.map(d => d[x]); const xmin = Math.min(...xs), xmax = Math.max(...xs);
    const vals = series.flatMap(s => s.data.map(d => d[s.key]).filter(v => v != null));
    let ylo = logy ? Math.min(...vals.filter(v => v > 0)) : 0, yhi = ymax ?? Math.max(...vals);
    if (logy) { ylo = Math.log10(ylo || 1e-6); yhi = Math.log10(yhi || 1); }
    const X = v => L + (W - L - 6) * (xmax > xmin ? (v - xmin) / (xmax - xmin) : 0.5);
    const Y = v => { const t = logy ? Math.log10(v || 1e-6) : v; return (H - B) - (H - B - 8) * (yhi > ylo ? (t - ylo) / (yhi - ylo) : 0.5); };
    const paths = series.map(s => { const d = s.data.filter(d => d[s.key] != null && d[x] != null); return `<path d="${d.map((d, i) => `${i ? 'L' : 'M'}${X(d[x]).toFixed(1)},${Y(d[s.key]).toFixed(1)}`).join(' ')}" fill="none" stroke="${s.color}" stroke-width="1.6"/>`; }).join('');
    const ticks = [0, 0.5, 1].map(f => `<text x="${L + (W - L - 6) * f}" y="${H - 3}" font-size="9" fill="var(--text-muted)" text-anchor="middle">${Math.round(xmin + (xmax - xmin) * f).toLocaleString()}</text>`).join('');
    const yt = [0, 0.5, 1].map(f => { const v = logy ? Math.pow(10, ylo + (yhi - ylo) * f) : ylo + (yhi - ylo) * f; return `<text x="${L - 4}" y="${(H - B) - (H - B - 8) * f + 3}" font-size="9" fill="var(--text-muted)" text-anchor="end">${v >= 100 ? v.toFixed(0) : v >= 1 ? v.toFixed(1) : v.toFixed(3)}</text>`; }).join('');
    const legend = series.map((s, i) => `<rect x="${L + i * 120}" y="2" width="8" height="8" fill="${s.color}"/><text x="${L + 12 + i * 120}" y="10" font-size="9" fill="var(--text-muted)">${s.name}</text>`).join('');
    return `<svg viewBox="0 0 ${W} ${H}" class="wwm-chart">${paths}${ticks}${yt}${legend}<text x="${W - 4}" y="${H - 3}" font-size="9" fill="var(--text-muted)" text-anchor="end">${ylabel}</text></svg>`;
}

async function showRun(el, ctx, p, rid, opt) {
    const box = el.querySelector('#wwm-run-detail');
    if (!box) return;
    box.dataset.run = rid;
    let d;
    try { d = await api(`/projects/${p.slug}/runs/${rid}`); } catch (e) { box.innerHTML = `<div class="wwm-error">${esc(e.message)}</div>`; return; }
    const r = d.run, m = d.final_metrics?.length ? d.final_metrics : d.metrics;
    const rows = r.rows || [];
    const trials = d.trials || [];
    box.innerHTML = `
      <div class="wwm-card">
        <div class="wwm-row wwm-between"><h2 style="margin:0">${esc(r.id)} ${r.label ? '· ' + esc(r.label) : ''} <span class="wwm-pill ${r.state === 'done' ? 'ok' : r.state === 'failed' ? 'bad' : 'warn'}">${esc(r.state)}</span></h2>
          <span class="wwm-muted">${r.cfg && r.cfg.model_type ? `${esc(r.cfg.model_type)}${r.cfg.model_type === 'rnn' ? ' (fixed 2×BiLSTM 64)' : ` ${r.cfg.layer_size}×${r.cfg.n_blocks}`} · ${(r.cfg.steps || 0).toLocaleString()} steps · lr ${r.cfg.lr} · neg weight ${r.cfg.max_negative_weight}` : r.cfg ? `ESP32 mixednet ${esc(r.cfg.pointwise_filters)} · ${(r.cfg.steps || 0).toLocaleString()} steps · lr ${r.cfg.lr} · neg weight ${r.cfg.negative_class_weight}${r.cutoff ? ` · cutoff ${r.cutoff}` : ''}` : ''}${r.params ? ` · ${r.params.toLocaleString()} ${r.trainer === 'mww' ? 'bytes' : 'parameters'}` : ''}${r.error ? ` · <span class="wwm-error">${esc(r.error)}</span>` : ''}</span></div>
        ${trials.length ? `<h3>Trials</h3>${trialsChart(trials)}<div class="wwm-muted">Best trial ${r.best_trial ?? '–'}: ${r.best_params ? esc(Object.entries(r.best_params).map(([k, v]) => `${k} ${typeof v === 'number' && v < 0.01 ? v.toExponential(1) : v}`).join(', ')) : ''}</div>` : ''}
        <div class="wwm-grid2" style="margin-top:8px">
          <div><h3>Loss</h3>${line([{ name: 'loss', key: 'loss', data: m, color: 'var(--accent, var(--accent-blue))' }], { logy: true })}</div>
          <div><h3>Recall on your held-out clips (threshold 0.5)</h3>${line([{ name: 'held-out recall', key: 'val_recall', data: m, color: 'var(--success, #6c6)' }, { name: 'train recall', key: 'train_recall', data: m, color: 'var(--text-muted)' }], { ymax: 1 })}</div>
          <div><h3>False alarms per hour, standard set</h3>${line([{ name: 'std FA/h', key: 'fp_per_hour', data: m, color: '#e9a33a' }])}</div>
          <div><h3>False alarms per hour, your room</h3>${line([{ name: 'room FA/h', key: 'own_fa_per_hour', data: m, color: 'var(--error, #e66)' }])}</div>
        </div>
        ${r.heldout?.table ? `<h3>The judge, by threshold <span class="wwm-muted">streaming, your ${r.heldout.n_pos} held-out clips, ${r.heldout.n_near} sound-alikes, ${r.heldout.n_neg} other negatives</span></h3><table class="wwm-table"><thead><tr><th>threshold</th><th class="num">hears you</th><th class="num">sound-alikes fired</th><th class="num">other negatives fired</th><th class="num">FA/h standard</th></tr></thead><tbody>
          ${r.heldout.table.map(x => `<tr class="${x.threshold === r.heldout.threshold ? 'wwm-pick' : ''}"><td>${x.threshold}${x.threshold === r.heldout.threshold ? ' ◂' : ''}</td><td class="num">${x.recall == null ? '–' : (100 * x.recall).toFixed(0) + '%'}</td><td class="num ${x.near_fires === 0 ? 'wwm-good' : x.near_fires === 1 ? 'wwm-meh' : 'wwm-bad'}">${x.near_fires}</td><td class="num ${x.neg_fires ? 'wwm-bad' : 'wwm-good'}">${x.neg_fires}</td><td class="num">${fmt(x.fp_per_hour)}</td></tr>`).join('')}</tbody></table>
          <div class="wwm-muted">◂ the pick: no other negative fires, at most one sound-alike, under 0.5 false alarms an hour where the trainer measured it, then the most of you heard, then the threshold nearest the usual one (0.5 desktop, 0.97 ESP32). Type another number in the table above to override.</div>` : ''}
        ${rows.length ? `<details style="margin-top:8px"><summary class="wwm-muted">The trainer's own eval by threshold (feature windows, not streaming)</summary><table class="wwm-table"><thead><tr><th>threshold</th><th class="num">recall (yours)</th><th class="num">negatives fired</th><th class="num">FA/h standard</th><th class="num">FA/h your room</th></tr></thead><tbody>
          ${rows.map(x => { const ok = (x.fp_per_hour || 0) <= 0.5 && (x.own_fa_per_hour || 0) <= 1; return `<tr class="${ok ? '' : 'wwm-dim'}"><td>${x.threshold}</td><td class="num">${x.recall == null ? '–' : (100 * x.recall).toFixed(0) + '%'}</td><td class="num">${x.neg_fp_rate == null ? '–' : (100 * x.neg_fp_rate).toFixed(0) + '%'}</td><td class="num">${fmt(x.fp_per_hour)}</td><td class="num">${fmt(x.own_fa_per_hour)}</td></tr>`; }).join('')}</tbody></table></details>` : ''}
      </div>`;
}

function trialsChart(trials) {
    const w = 520, h = 120;
    const max = Math.max(0.01, ...trials.map(t => t.score || 0));
    const bw = Math.max(6, Math.min(30, (w - 40) / Math.max(1, trials.length)));
    return `<svg viewBox="0 0 ${w} ${h}" class="wwm-chart">${trials.map((t, i) => { const bh = Math.round((h - 24) * (t.score || 0) / max); const x = 30 + i * bw; return `<rect x="${x}" y="${h - 14 - bh}" width="${bw - 2}" height="${bh}" fill="var(--accent, var(--accent-blue))" opacity=".85"><title>trial ${t.trial}: score ${(t.score || 0).toFixed(3)} · ${Object.entries(t.params || {}).map(([k, v]) => `${k}=${typeof v === 'number' && v < 0.01 ? v.toExponential(1) : v}`).join(' ')} · ${t.seconds}s</title></rect><text x="${x + bw / 2 - 1}" y="${h - 3}" font-size="8" fill="var(--text-muted)" text-anchor="middle">${t.trial}</text>`; }).join('')}<text x="4" y="10" font-size="9" fill="var(--text-muted)">score = best recall under the false-alarm targets</text></svg>`;
}
