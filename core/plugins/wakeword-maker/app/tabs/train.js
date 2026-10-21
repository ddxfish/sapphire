// Train tab. Single: one recipe, how hard, with or without the held-out slice, the knobs under Advanced. Search: a
// slider over four stops (Look, Standard, Thorough, Deep), each a grid of runs with seeds; Deep adds the Optuna
// refine around the best cells. Then the grids, the runs, each with its curves and its threshold table.
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
        .then(([opt, runs, ds]) => { if (el.isConnected) draw(el, ctx, p, opt, runs, ds); }).catch(e => { el.innerHTML = `<div class="wwm-error">${esc(e.message)}</div>`; });   // left already: no poll, no listeners
    return { cleanup: () => { if (poll) clearInterval(poll); poll = null; } };     // a finished job redraws the tab from the shell
}

// the four search stops: each a grid over the ticked models (the current Variations stop when `recipes` is null),
// with seeds; Deep adds the refine. Cells × this wake word's own run times make the estimate.
const STOPS = ['look', 'standard', 'thorough', 'deep'];
const SEARCH = {
    look: { word: 'Look: one quick run per model. Minutes. Does the phrase train at all, is the data any good?', recipes: null, sources: ['all'], presets: ['quick'], knobs: { oww: ['default'], mww: ['default'] }, seeds: 1, refine: false },
    standard: { word: 'Standard: the two desktop nets and the ESP32 net at Standard, three seeds each. The middle seed is what each recipe really does.', recipes: null, sources: ['all'], presets: ['standard'], knobs: { oww: ['default', 'rnn'], mww: ['default'] }, seeds: 3, refine: false },
    thorough: { word: 'Thorough: three variation stops, two source blocks, two nets per family, three seeds. The Grids card says which data and which stop help.', recipes: ['gentle', 'natural', 'rough'], sources: ['all', 'own x16'], presets: ['standard'], knobs: { oww: ['default', 'rnn'], mww: ['default', 'more room'] }, seeds: 3, refine: false },
    deep: { word: "Deep: Thorough's space at two sizes, then Optuna around the three best desktop cells, scored by the judge. The grid finds the candidates, the refine masters them.", recipes: ['gentle', 'natural', 'rough'], sources: ['all', 'own x16'], presets: ['standard', 'thorough'], knobs: { oww: ['default', 'rnn'], mww: ['default', 'more room'] }, seeds: 3, refine: true },
};
const GUESS = { oww: { quick: 1, standard: 2, thorough: 4 }, mww: { quick: 3, standard: 7, thorough: 14 } };   // minutes per run on a GPU until the wake word has runs of its own; a CPU is ~8× slower
const OWW_KNOBS = ['model_type', 'layer_size', 'n_blocks', 'steps', 'lr', 'max_negative_weight', 'target_fp_per_hour', 'seed'];
const MWW_KNOBS = ['steps', 'lr', 'negative_class_weight', 'pointwise_filters', 'first_conv_filters', 'sampling_room', 'sampling_adversarial', 'penalty_adversarial'];
const cap = (s) => s[0].toUpperCase() + s.slice(1);

function draw(el, ctx, p, opt, runsData, ds) {
    const envReady = ['ready', 'stale'].includes(ctx.state.status?.env?.state);
    const have = Object.fromEntries((ds.datasets || []).map(d => [d.id, d.state === 'ready']));
    const owwReady = have.oww_models && have.oww_negatives && have.oww_validation;
    const mwwReady = !!have.mww_negatives;
    const saved = p.settings?.train || {};
    const want = saved.trainers || { oww: true, mww: mwwReady };
    const cfg = { ...opt.defaults, ...(saved.cfg || {}) };
    const cfgM = { ...(opt.mww?.defaults || {}), ...(saved.cfg_mww || {}) };
    const preset = saved.preset || 'standard';
    const useHold = saved.holdout !== false;
    const rounds = saved.rounds || 2;
    const synth = saved.synth || 'all';
    const human = saved.sources?.human || 'all';
    const ownRepeat = saved.sources?.own_repeat || 8;
    const mode = saved.mode === 'search' ? 'search' : 'single';
    const level = STOPS.includes(saved.search) ? saved.search : 'standard';
    const running = runsData.running || [];
    const gpu = !!ctx.state.status?.gpu;
    const canTrain = envReady && (owwReady || mwwReady);
    const curRecipe = ({ harsh: 'rough' })[p.settings?.augment?.preset] || p.settings?.augment?.preset || 'natural';
    const num = (id, label, v, min, max, step) => `<label>${label} <input type="number" id="${id}" min="${min}" max="${max}" step="${step}" value="${v}"></label>`;
    el.innerHTML = `
      <div id="wwm-var-host"></div>
      <div class="wwm-card">
        <div class="wwm-row wwm-between"><h2 style="margin:0">Train ${qbtn('train')}</h2><div class="wwm-seg small" id="wwm-mode"><button data-v="single">Single</button><button data-v="search">Search</button></div></div>
        <div class="wwm-row wwm-trainers">
          <label><input type="checkbox" id="wwm-t-oww" ${want.oww !== false ? 'checked' : ''} ${owwReady ? '' : 'disabled'}> Desktop and Pi <span class="wwm-muted">(openWakeWord)</span></label>
          <label><input type="checkbox" id="wwm-t-mww" ${want.mww && mwwReady ? 'checked' : ''} ${mwwReady ? '' : 'disabled'}> ESP32 <span class="wwm-muted">(microWakeWord)</span></label>
          ${running.length ? `<span class="wwm-muted">running: ${esc(running[0].msg || running[0].kind)}</span>` : envReady ? '' : '<span class="wwm-muted">build the environment on the Settings tab first</span>'}
        </div>
        <ul class="wwm-checks">${owwReady ? '' : `<li class="warn">⚠ Desktop and Pi needs the three openWakeWord sets from Settings: feature models, negative features (17 GB), validation features.</li>`}${mwwReady ? '' : `<li class="warn">⚠ ESP32 needs the microWakeWord negative spectrograms from Settings (5.7 GB).</li>`}</ul>
        <div id="wwm-single">
          <div class="wwm-row" style="margin-top:8px">
            <div class="wwm-seg" id="wwm-tpreset">
              <button data-v="quick">Quick<small>10k steps · ~${gpu ? '5' : '40'} min</small></button>
              <button data-v="standard">Standard<small>50k steps · ~${gpu ? '25' : '200'} min</small></button>
              <button data-v="thorough">Thorough<small>100k steps, bigger net</small></button>
            </div>
          </div>
          <div class="wwm-row wwm-wrap" style="margin-top:8px">
            <label><input type="checkbox" id="wwm-holdout" ${useHold ? 'checked' : ''}> validate on the held-out slice</label>${qbtn('validate')}
            <label>Variations per clip <input type="number" id="wwm-rounds" min="1" max="6" value="${rounds}" style="width:60px"></label>${qbtn('rounds')}
            <label>Synthetic voices <select id="wwm-synth">${[['all', 'all'], ['piper', 'Piper only'], ['kokoro', 'Kokoro only'], ['none', 'none: your recordings only']].map(([v, t]) => `<option value="${v}" ${synth === v ? 'selected' : ''}>${t}</option>`).join('')}</select></label>${qbtn('synth')}
            <label>Your takes <select id="wwm-human"><option value="all" ${human !== 'none' ? 'selected' : ''}>all</option><option value="none" ${human === 'none' ? 'selected' : ''}>none: synthetic only</option></select></label>
            <label title="how many extra times each of your takes is varied into training; 8 is the usual weight against thousands of synthetic clips">Own weight <input type="number" id="wwm-ownrep" min="1" max="64" value="${ownRepeat}" style="width:56px"></label>${qbtn('sources')}
            <label>Label <input type="text" id="wwm-tlabel" placeholder="e.g. natural, standard" style="min-width:160px"></label>
            <label>Repeats <input type="number" id="wwm-repeats" min="1" max="5" value="1" style="width:56px"></label>${qbtn('repeats')}
          </div>
          <div class="wwm-row" style="margin-top:8px">
            <button class="wwm-btn primary big" id="wwm-train-go" ${canTrain ? '' : 'disabled'}>${running.length || (runsData.queued || []).length ? 'Add to queue' : 'Train'}</button>
            <button class="wwm-btn ghost" id="wwm-adv-toggle">▸ Advanced</button>
          </div>
          <div id="wwm-adv" style="display:none">
            <div class="wwm-advgrid" id="wwm-adv-oww">
              <div class="wwm-muted" style="grid-column:1/-1">Desktop and Pi</div>
              <label>Model <select id="wwm-c-model_type"><option value="dnn">dnn (dense)</option><option value="rnn">rnn (LSTM)</option></select></label>
              ${num('wwm-c-layer_size', 'Layer size', cfg.layer_size, 8, 512, 8)}${num('wwm-c-n_blocks', 'Blocks', cfg.n_blocks, 1, 4, 1)}${num('wwm-c-steps', 'Steps', cfg.steps, 1000, 300000, 1000)}
              ${num('wwm-c-lr', 'Learning rate', cfg.lr, 0.00001, 0.01, 0.00001)}${num('wwm-c-max_negative_weight', 'Max negative weight', cfg.max_negative_weight, 1, 10000, 50)}
              ${num('wwm-c-target_fp_per_hour', 'Target false alarms/h', cfg.target_fp_per_hour, 0.01, 5, 0.05)}${num('wwm-c-seed', 'Seed', cfg.seed, 0, 99999, 1)}
            </div>
            <div class="wwm-advgrid" id="wwm-adv-mww">
              <div class="wwm-muted" style="grid-column:1/-1">ESP32</div>
              ${num('wwm-m-steps', 'Steps', cfgM.steps, 1000, 200000, 1000)}${num('wwm-m-lr', 'Learning rate', cfgM.lr, 0.00001, 0.01, 0.0001)}${num('wwm-m-negative_class_weight', 'Negative class weight', cfgM.negative_class_weight, 1, 200, 1)}
              <label>Filters per block <input type="text" id="wwm-m-pointwise_filters" value="${esc(cfgM.pointwise_filters)}" title="four numbers: the mixednet's width"></label>
              ${num('wwm-m-first_conv_filters', 'First conv filters', cfgM.first_conv_filters, 8, 128, 8)}${num('wwm-m-sampling_room', 'Room weight', cfgM.sampling_room, 0, 20, 0.5)}
              ${num('wwm-m-sampling_adversarial', 'Sound-alike weight', cfgM.sampling_adversarial, 0, 20, 0.5)}${num('wwm-m-penalty_adversarial', 'Sound-alike penalty', cfgM.penalty_adversarial, 0, 10, 0.5)}
            </div>
          </div>
        </div>
        <div id="wwm-search">
          <div class="wwm-slider" style="margin-top:8px">
            <input type="range" id="wwm-level" min="0" max="${STOPS.length - 1}" step="1" value="${STOPS.indexOf(level)}">
            <div class="wwm-stops">${STOPS.map((s, i) => `<span data-i="${i}" class="${s === level ? 'on' : ''}">${cap(s)}</span>`).join('')}</div>
          </div>
          <div class="wwm-muted" id="wwm-level-word">…</div>
          <div class="wwm-row wwm-wrap" style="margin-top:8px">
            <button class="wwm-btn primary big" id="wwm-search-go" ${canTrain ? '' : 'disabled'}>Search</button>
            <label>Label <input type="text" id="wwm-g-label" placeholder="e.g. ${level}" style="min-width:140px"></label>
            <button class="wwm-btn ghost" id="wwm-space-toggle">▸ Choose the space</button>${qbtn('search')}
            <button class="wwm-btn ghost" id="wwm-ask-toggle" ${canTrain ? '' : 'disabled'} title="train with and without one mic or tag, several seeds each, and read the difference on that tag's own held-out takes">Does this data help?…</button>
          </div>
          <div id="wwm-grid" style="display:none" class="wwm-sweep">
            <div class="wwm-row wwm-wrap"><b>Variations</b> ${(opt.grid?.recipes || []).map(r => `<label><input type="checkbox" data-gax="recipes" value="${r}"> ${r}</label>`).join('')}${curRecipe === 'custom' ? '<label title="your Fine tune rows, as Variations stands now"><input type="checkbox" data-gax="recipes" value="custom"> custom (yours)</label>' : ''}</div>
            <div class="wwm-row wwm-wrap" id="wwm-grid-sources"><b>Sources</b> <span class="wwm-muted">loading…</span></div>
            <div class="wwm-row"><b>How hard</b> ${(opt.grid?.presets || []).map(r => `<label><input type="checkbox" data-gax="presets" value="${r}"> ${r}</label>`).join('')}</div>
            <div class="wwm-row"><b>Desktop knobs</b> <label><input type="checkbox" data-gknob="oww" value="default"> as the preset</label>${(opt.grid?.knobs?.oww || []).map(k => `<label><input type="checkbox" data-gknob="oww" value="${k}"> ${k}</label>`).join('')}</div>
            <div class="wwm-row"><b>ESP32 knobs</b> <label><input type="checkbox" data-gknob="mww" value="default"> as the preset</label>${(opt.grid?.knobs?.mww || []).map(k => `<label><input type="checkbox" data-gknob="mww" value="${k}"> ${k}</label>`).join('')}</div>
            <div class="wwm-row wwm-wrap"><label>Seeds each <input type="number" id="wwm-g-seeds" min="1" max="5" value="3" style="width:56px"></label>${qbtn('repeats')}
              <label title="desktop and Pi only: Optuna walks the knobs around the best judged cells, each trial a short run scored by the judge, then the winner trains in full"><input type="checkbox" id="wwm-refine"> then refine the best desktop cells with Optuna</label>${qbtn('refine')}</div>
            <div class="wwm-row wwm-wrap" id="wwm-refine-row" style="display:none">
              <label>Trials <input type="number" id="wwm-r-trials" min="2" max="60" value="16" style="width:60px"></label>
              <label>Steps per trial <input type="number" id="wwm-r-budget" min="1000" max="50000" step="1000" value="8000" style="width:84px"></label>
              <label>At a time <input type="number" id="wwm-r-parallel" min="1" max="4" value="1" style="width:52px" title="two at a time share the GPU and the ONNX exporter; one is the safe pace"></label>
              <label>Start from the best <input type="number" id="wwm-r-top" min="1" max="10" value="3" style="width:52px"> cells</label>
            </div>
          </div>
          <div id="wwm-ask" style="display:none" class="wwm-sweep">
            <h3>Does this data help? ${qbtn('ablate')}</h3>
            <div class="wwm-row wwm-wrap"><label>Leave out <select id="wwm-ask-what"><option value="">loading…</option></select></label>
              <label>Seeds each <input type="number" id="wwm-ask-seeds" min="1" max="5" value="3" style="width:56px"></label>
              <button class="wwm-btn primary" id="wwm-ask-go">Queue with and without</button><span class="wwm-muted" id="wwm-ask-note"></span></div>
          </div>
        </div>
      </div>
      <div class="wwm-card" id="wwm-grids-card" style="display:none">
        <h2>Grids ${qbtn('grid')}</h2>
        <div id="wwm-grids"></div>
      </div>
      <div class="wwm-card">
        <h2>Runs</h2>
        <div id="wwm-runs"></div>
      </div>
      <div id="wwm-run-detail"></div>`;
    bindHelp(el);
    variationsTab.render(el.querySelector('#wwm-var-host'), ctx, true);

    // the preset fills the knobs it owns (the rest keep what you typed); Advanced shows the ticked families
    const seg = el.querySelector('#wwm-tpreset');
    const owned = (presets) => new Set(Object.values(presets || {}).flatMap(x => Object.keys(x)));
    const ownedO = owned(opt.presets), ownedM = owned(opt.mww?.presets);
    const setPreset = (v) => {
        seg.querySelectorAll('button').forEach(b => b.classList.toggle('on', b.dataset.v === v));
        const po = { ...opt.defaults, ...(opt.presets[v] || {}) }, pm = { ...(opt.mww?.defaults || {}), ...(opt.mww?.presets?.[v] || {}) };
        for (const k of OWW_KNOBS) if (ownedO.has(k)) { const i = el.querySelector(`#wwm-c-${k}`); if (i) i.value = po[k]; }
        for (const k of MWW_KNOBS) if (ownedM.has(k)) { const i = el.querySelector(`#wwm-m-${k}`); if (i) i.value = pm[k]; }
    };
    setPreset(preset);
    seg.querySelectorAll('button').forEach(b => b.addEventListener('click', () => { setPreset(b.dataset.v); save(); }));
    const mt = el.querySelector('#wwm-c-model_type');
    mt.value = cfg.model_type;
    const sizeKnobs = () => { for (const k of ['layer_size', 'n_blocks']) el.querySelector(`#wwm-c-${k}`).closest('label').style.display = mt.value === 'rnn' ? 'none' : ''; };   // the LSTM is a fixed net: the size knobs do nothing for it
    sizeKnobs();
    mt.addEventListener('change', sizeKnobs);
    const advShow = () => { el.querySelector('#wwm-adv-oww').style.display = el.querySelector('#wwm-t-oww').checked ? '' : 'none'; el.querySelector('#wwm-adv-mww').style.display = el.querySelector('#wwm-t-mww').checked ? '' : 'none'; };
    advShow();
    el.querySelectorAll('#wwm-t-oww, #wwm-t-mww').forEach(i => i.addEventListener('change', advShow));
    el.querySelector('#wwm-adv-toggle').addEventListener('click', (e) => { const a = el.querySelector('#wwm-adv'); const open = a.style.display === 'none'; a.style.display = open ? '' : 'none'; e.target.textContent = open ? '▾ Advanced' : '▸ Advanced'; });
    const readCfg = () => {
        const c = {};
        for (const k of OWW_KNOBS) { const i = el.querySelector(`#wwm-c-${k}`); c[k] = i.type === 'number' ? parseFloat(i.value) : i.value; }
        return c;
    };
    const readCfgM = () => {
        const c = {};
        for (const k of MWW_KNOBS) { const i = el.querySelector(`#wwm-m-${k}`); c[k] = i.type === 'number' ? parseFloat(i.value) : i.value.trim(); }
        c.seed = parseFloat(el.querySelector('#wwm-c-seed').value);
        return c;
    };
    const modeSeg = el.querySelector('#wwm-mode');
    const setMode = (v) => {
        modeSeg.querySelectorAll('button').forEach(b => b.classList.toggle('on', b.dataset.v === v));
        el.querySelector('#wwm-single').style.display = v === 'single' ? '' : 'none';
        el.querySelector('#wwm-search').style.display = v === 'search' ? '' : 'none';
    };
    setMode(mode);
    modeSeg.querySelectorAll('button').forEach(b => b.addEventListener('click', () => { setMode(b.dataset.v); save(); }));
    const read = () => ({ mode: modeSeg.querySelector('button.on')?.dataset.v || 'single', search: STOPS[+el.querySelector('#wwm-level').value] || 'standard',
        preset: seg.querySelector('button.on')?.dataset.v || 'standard', holdout: el.querySelector('#wwm-holdout').checked, rounds: parseInt(el.querySelector('#wwm-rounds').value) || 2, cfg: readCfg(), cfg_mww: readCfgM(), synth: el.querySelector('#wwm-synth').value,
        sources: { synth: el.querySelector('#wwm-synth').value, human: el.querySelector('#wwm-human').value, own_repeat: parseInt(el.querySelector('#wwm-ownrep').value) || 8 },
        trainers: { oww: el.querySelector('#wwm-t-oww').checked, mww: el.querySelector('#wwm-t-mww').checked },
        recipe: ({ harsh: 'rough' })[p.settings?.augment?.preset] || p.settings?.augment?.preset || 'natural' });
    const save = async () => { try { const r = await api(`/projects/${p.slug}`, { method: 'PUT', body: { settings: { train: read() } } }); Object.assign(p, r.project); } catch { /* fine */ } };
    el.querySelectorAll('#wwm-adv input, #wwm-adv select, #wwm-holdout, #wwm-rounds, #wwm-synth, #wwm-human, #wwm-ownrep, #wwm-t-oww, #wwm-t-mww').forEach(i => i.addEventListener('change', save));

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
                    if (t.trainers.mww) await ctx.startJob('train_mww', { ...a, cfg: { ...t.cfg_mww, seed: a.cfg.seed } });
                }
                ctx.toast(`${[t.trainers.oww && 'desktop/Pi', t.trainers.mww && 'ESP32'].filter(Boolean).join(' and ')}${n > 1 ? ` × ${n}` : ''} queued; each runs when the one before it is done.`, 'info');
                setTimeout(() => render(el, ctx), 800);
            } catch (e) { ctx.toast(e.message, 'error'); }
        };
        if (!t.holdout) showConfirm('Without the held-out slice, every recording trains and the recall number comes from synthetic clips only. Test will be less honest about your own voice. Continue?', go, { title: 'No held-out slice', saveLabel: 'Train anyway' });
        else go();
    });
    searchPanel(el, ctx, p, opt, runsData, read, save, gpu, level);
    askPanel(el, ctx, p, read);

    drawRuns(el, ctx, p, runsData, opt);
    drawGrids(el, ctx, p);
    if (poll) clearInterval(poll);
    if (running.length || (runsData.queued || []).length) poll = setInterval(() => { if (!document.hidden) { api(`/projects/${p.slug}/runs`).then(r => drawRuns(el, ctx, p, r, opt)).catch(() => {}); drawGrids(el, ctx, p); } }, 10000);
}

// --- search: the slider picks a stop, the fold shows the space it stands for; touch the fold and it is Custom ----
function searchPanel(el, ctx, p, opt, runsData, read, save, gpu, level0) {
    const grid = el.querySelector('#wwm-grid'), slider = el.querySelector('#wwm-level'), word = el.querySelector('#wwm-level-word'), go = el.querySelector('#wwm-search-go');
    let level = level0, custom = false;
    const picked = (ax) => [...grid.querySelectorAll(`[data-gax="${ax}"]:checked`)].map(i => i.value);
    const knobs = (t) => [...grid.querySelectorAll(`[data-gknob="${t}"]:checked`)].map(i => i.value);
    const trainers = () => ['oww', 'mww'].filter(t => el.querySelector(`#wwm-t-${t}`).checked);
    const refineOn = () => grid.querySelector('#wwm-refine').checked && trainers().includes('oww');
    const refineRow = () => { grid.querySelector('#wwm-refine-row').style.display = grid.querySelector('#wwm-refine').checked ? '' : 'none'; };
    // minutes per run from this wake word's own finished runs, by model and preset; a guess until it has some
    const med = {};
    for (const r of runsData.runs || []) if (r.state === 'done' && r.seconds && r.preset && r.trainer && r.kind !== 'tune') (med[`${r.trainer}/${r.preset}`] = med[`${r.trainer}/${r.preset}`] || []).push(r.seconds / 60);
    const minutes = (t, pr) => { const v = (med[`${t}/${pr}`] || []).slice().sort((a, b) => a - b); return v.length ? v[Math.floor(v.length / 2)] : (GUESS[t]?.[pr] || 5) * (gpu ? 1 : 8); };
    const plan = () => {
        const seeds = Math.max(1, Math.min(5, parseInt(grid.querySelector('#wwm-g-seeds').value) || 1));
        let n = 0, mins = 0;
        for (const t of trainers()) for (const pr of picked('presets')) { const c = Math.max(1, knobs(t).length) * picked('recipes').length * picked('sources').length * seeds; n += c; mins += c * minutes(t, pr); }
        let refine = null;
        if (refineOn()) {
            refine = { trials: parseInt(grid.querySelector('#wwm-r-trials').value) || 16, budget_steps: parseInt(grid.querySelector('#wwm-r-budget').value) || 8000, parallel: parseInt(grid.querySelector('#wwm-r-parallel').value) || 1, top: parseInt(grid.querySelector('#wwm-r-top').value) || 3 };
            mins += refine.trials * ((refine.budget_steps / 50000) * minutes('oww', 'standard') / refine.parallel + 0.5) + minutes('oww', picked('presets').includes('thorough') ? 'thorough' : 'standard');
        }
        return { n, mins, refine, seeds };
    };
    const fmtTime = (m) => m < 90 ? `${Math.round(m)} min` : m < 36 * 60 ? `${(m / 60).toFixed(1)} h` : `${(m / 60 / 24).toFixed(1)} days`;
    const words = () => {
        const { n, mins, refine } = plan(); const max = opt.grid?.max || 200;
        const have = Object.values(med).reduce((a, v) => a + v.length, 0);
        word.innerHTML = `${custom ? 'Custom: the space as you ticked it.' : esc(SEARCH[level].word)} <b>${n} run${n === 1 ? '' : 's'}${refine ? ` + a refine of ${refine.trials} trials` : ''}</b>, about <b>${fmtTime(mins)}</b> on this PC${have ? ` (from the ${have} runs so far)` : ' (a guess until something has run here)'}${n > max ? ` <span class="wwm-error">· too many: ${max} at most</span>` : ''}${!n ? ' · tick a model, and one of each in the space' : ''}`;
        go.disabled = !n || n > max || go.dataset.locked === '1';
        go.textContent = refine ? 'Search, then refine' : 'Search';
    };
    if (go.disabled) go.dataset.locked = '1';
    const apply = (lv) => {   // tick the space the stop stands for
        level = lv; custom = false;
        const s = SEARCH[lv], R = read().recipe;
        const tick = (sel, on) => grid.querySelectorAll(sel).forEach(i => { if (!i.disabled) i.checked = on(i.value); });
        tick('[data-gax="recipes"]', v => (s.recipes || [R]).includes(v));
        tick('[data-gax="sources"]', v => s.sources.includes(v));
        tick('[data-gax="presets"]', v => s.presets.includes(v));
        for (const t of ['oww', 'mww']) tick(`[data-gknob="${t}"]`, v => s.knobs[t].includes(v));
        grid.querySelector('#wwm-g-seeds').value = s.seeds;
        grid.querySelector('#wwm-refine').checked = s.refine;
        refineRow();
        el.querySelectorAll('.wwm-stops span').forEach(x => x.classList.toggle('on', STOPS[+x.dataset.i] === lv));
        slider.value = STOPS.indexOf(lv);
        el.querySelector('#wwm-g-label').placeholder = `e.g. ${lv}`;
        words();
    };
    const touched = () => { custom = true; el.querySelectorAll('.wwm-stops span').forEach(x => x.classList.remove('on')); refineRow(); words(); };
    slider.addEventListener('input', () => { apply(STOPS[+slider.value]); save(); });
    el.querySelectorAll('.wwm-stops span').forEach(x => x.addEventListener('click', () => { apply(STOPS[+x.dataset.i]); save(); }));
    grid.querySelectorAll('input').forEach(i => i.addEventListener('change', touched));
    el.querySelectorAll('#wwm-t-oww, #wwm-t-mww').forEach(i => i.addEventListener('change', words));
    el.querySelector('#wwm-space-toggle').addEventListener('click', (e) => { const open = grid.style.display === 'none'; grid.style.display = open ? '' : 'none'; e.target.textContent = (open ? '▾' : '▸') + ' Choose the space'; });
    api(`/projects/${p.slug}/sources`).then(s => {
        el.querySelector('#wwm-grid-sources').innerHTML = `<b>Sources</b> ${(s.presets || []).map(x => `<label title="${esc(x.label)}"><input type="checkbox" data-gax="sources" value="${esc(x.name)}"> ${esc(x.name)}</label>`).join('')}`;
        el.querySelectorAll('#wwm-grid-sources input').forEach(i => i.addEventListener('change', touched));
        apply(level);
    }).catch(() => { el.querySelector('#wwm-grid-sources').innerHTML = '<b>Sources</b> <span class="wwm-error">could not read the sources</span>'; apply(level); });
    apply(level);
    go.addEventListener('click', async () => {
        const { n, refine, seeds } = plan();
        if (!n) return;
        const label = el.querySelector('#wwm-g-label').value.trim() || (custom ? 'custom' : level);
        const body = { trainers: trainers(), recipes: picked('recipes'), sources: picked('sources'), presets: picked('presets'), knobs: { oww: knobs('oww'), mww: knobs('mww') },
            seeds, label, holdout: el.querySelector('#wwm-holdout').checked, rounds: parseInt(el.querySelector('#wwm-rounds').value) || 2, ...(refine ? { refine } : {}) };
        try { const r = await api(`/projects/${p.slug}/grid`, { method: 'POST', body }); ctx.toast(`${r.grid.of} runs queued${refine ? ', then the refine' : ''}; they take turns, one at a time.`, 'info'); setTimeout(() => render(el, ctx), 800); }
        catch (e) { ctx.toast(e.message, 'error'); }
    });
}

// "Does this data help?": one mic or one tag value left out, against everything, several seeds each. A grid of
// two source blocks with a focus, so the pivot reads recall on that tag's own held-out takes beside the overall.
function askPanel(el, ctx, p, read) {
    const panel = el.querySelector('#wwm-ask'), sel = panel.querySelector('#wwm-ask-what'), note = panel.querySelector('#wwm-ask-note');
    el.querySelector('#wwm-ask-toggle').addEventListener('click', () => { panel.style.display = panel.style.display === 'none' ? '' : 'none'; });
    const words = { device: 'mic', how: 'how', where: 'from', noise: 'noise', who: 'who' };
    api(`/projects/${p.slug}/sources`).then(s => {
        const have = new Set((s.presets || []).map(x => x.name));
        const opts = [];
        for (const [ax, vals] of Object.entries({ device: s.devices || [], ...(s.tags || {}) })) for (const v of vals) if (have.has(`without ${v}`)) opts.push({ ax, v });
        sel.innerHTML = opts.length ? opts.map(o => `<option value="${esc(JSON.stringify(o))}">${esc(words[o.ax] || o.ax)}: ${esc(o.v)}</option>`).join('') : '<option value="">nothing to leave out yet: two or more mics, or two values of a tag</option>';
        count();
    }).catch(() => { sel.innerHTML = '<option value="">could not read the sources</option>'; });
    const count = () => {
        const t = read(); const n = 2 * ((t.trainers.oww ? 1 : 0) + (t.trainers.mww ? 1 : 0)) * (parseInt(panel.querySelector('#wwm-ask-seeds').value) || 1);
        note.textContent = sel.value ? `${n} runs: with and without, ${t.preset}, the ticked models` : '';
        panel.querySelector('#wwm-ask-go').disabled = !sel.value || !n;
    };
    panel.querySelectorAll('input, select').forEach(i => i.addEventListener('change', count));
    el.querySelectorAll('#wwm-t-oww, #wwm-t-mww').forEach(i => i.addEventListener('change', count));
    panel.querySelector('#wwm-ask-go').addEventListener('click', async () => {
        let o; try { o = JSON.parse(sel.value); } catch { return; }
        const t = read();
        const body = { trainers: ['oww', 'mww'].filter(k => t.trainers[k]), recipes: [t.recipe], sources: ['all', `without ${o.v}`], presets: [t.preset], knobs: {},
            seeds: parseInt(panel.querySelector('#wwm-ask-seeds').value) || 3, label: `does ${o.v} help`, focus: { axis: o.ax, value: o.v }, holdout: t.holdout, rounds: t.rounds };
        try { const r = await api(`/projects/${p.slug}/grid`, { method: 'POST', body }); ctx.toast(`${r.grid.of} runs queued: with and without ${o.v}. The Grids card reads the answer under "sources".`, 'info'); panel.style.display = 'none'; setTimeout(() => render(el, ctx), 800); }
        catch (e) { ctx.toast(e.message, 'error'); }
    });
}

const pct0 = (v) => v == null ? '–' : Math.round(100 * v) + '%';
const clean = (c) => ((c.near ?? 0) <= 1 && (c.fp == null || c.fp <= 0.5)) ? 1 : 0;
// the best judged cell of a model family: clean first, then stressed recall, recall, gap
const bestCell = (cells, tr) => cells.filter(c => c.trainer === tr && c.recall != null).sort((a, b) => (clean(b) - clean(a)) || ((b.stress ?? -1) - (a.stress ?? -1)) || (b.recall - a.recall) || ((b.gap ?? -9) - (a.gap ?? -9)))[0];

async function drawGrids(el, ctx, p) {
    const card = el.querySelector('#wwm-grids-card'), box = el.querySelector('#wwm-grids');
    if (!card || !box) return;
    let grids = [];
    try { grids = (await api(`/projects/${p.slug}/grids`)).grids || []; } catch { return; }
    card.style.display = grids.length ? '' : 'none';
    if (!grids.length) return;
    const open = box.dataset.open || grids[0].id;
    box.dataset.open = open;
    box.innerHTML = grids.map(g => `<div class="wwm-row wwm-between" style="margin:4px 0"><span><button class="wwm-btn ghost" data-grid="${esc(g.id)}">${g.id === open ? '▾' : '▸'} ${esc(g.label || g.id)}</button> <span class="wwm-muted">${g.done} of ${g.of} done${g.counts?.running ? `, ${g.counts.running} running` : ''}${g.counts?.queued ? `, ${g.counts.queued} queued` : ''}${g.counts?.failed ? `, <span class="wwm-error">${g.counts.failed} failed</span>` : ''} · ${g.seeds} seed${g.seeds === 1 ? '' : 's'}${g.refine ? ` · refine ${esc(g.refine.state)}` : ''}</span></span><button class="wwm-x" data-grid-del="${esc(g.id)}" title="forget this grid; queued cells (and the refine) are cancelled, finished runs stay">✕</button></div>${g.id === open ? `<div id="wwm-grid-open" class="wwm-muted">loading…</div>` : ''}`).join('');
    box.querySelectorAll('[data-grid]').forEach(b => b.addEventListener('click', () => { box.dataset.open = b.dataset.grid === box.dataset.open ? '-' : b.dataset.grid; drawGrids(el, ctx, p); }));
    box.querySelectorAll('[data-grid-del]').forEach(b => b.addEventListener('click', () => showConfirm(`Forget grid ${b.dataset.gridDel}? Queued runs and its refine are cancelled; finished ones stay on the scoreboard.`, async () => {
        try { await api(`/projects/${p.slug}/grids/${b.dataset.gridDel}`, { method: 'DELETE' }); render(el, ctx); } catch (e) { ctx.toast(e.message, 'error'); }
    }, { title: 'Forget grid', saveLabel: 'Forget' })));
    const slot = box.querySelector('#wwm-grid-open');
    if (!slot) return;
    try {
        const g = (await api(`/projects/${p.slug}/grids/${open}`)).grid;
        const axes = Object.entries(g.pivot || {});
        const names = { trainer: 'model', recipe: 'variations', sources: 'sources', preset: 'how hard', knob: 'knobs', seed: 'seed' };
        const f = g.focus;      // the tag value this grid asks about: two more columns, recall and stress on its own held-out takes
        const fn = (g.cells.find(c => c.focus_n != null) || {}).focus_n;
        const fHead = f ? `<th class="num" title="of the held-out takes tagged ${esc(f.value)}${fn != null ? ` (${fn})` : ''}, how many it heard">${esc(f.value)}</th><th class="num" title="the same takes under the rough draws">${esc(f.value)} stressed</th>` : '';
        const fCols = (s) => f ? `<td class="num">${pct0(s.focus)}</td><td class="num">${pct0(s.focus_stress)}</td>` : '';
        const star = (run, chosen) => run ? `<button class="wwm-x ${chosen ? 'on' : ''}" data-star="${esc(run)}" title="${chosen ? 'Install uses this one' : 'use this one for Install'}">${chosen ? '★' : '☆'}</button>` : '';
        const winners = ['oww', 'mww'].map(tr => { const b = bestCell(g.cells, tr); return b ? `<span class="wwm-chip ${b.chosen ? 'wwm-chosen' : ''}">${tr === 'oww' ? 'desktop/Pi' : 'ESP32'} · ${esc(b.label || b.run)} · hears ${pct0(b.recall)} · stressed ${pct0(b.stress)} · ${b.near ?? 0} sound-alike${b.near === 1 ? '' : 's'} ${star(b.run, b.chosen)}</span>` : ''; }).filter(Boolean);
        const rf = g.refine;
        slot.innerHTML = `<div class="wwm-row wwm-wrap" style="margin:2px 0 6px"><span class="wwm-muted">Best so far:</span>${winners.join('') || '<span class="wwm-muted">nothing judged yet</span>'}</div>`
            + (rf ? `<div class="wwm-row wwm-wrap" style="margin:0 0 6px"><span class="wwm-muted">Refine (${rf.trials} trials around the best ${rf.top}):</span><span class="wwm-chip ${rf.chosen ? 'wwm-chosen' : ''}">${esc(rf.state)}${rf.state === 'running' && rf.pct != null ? ` ${Math.round(rf.pct)}%` : ''}${rf.state === 'running' && rf.msg ? ` · ${esc(rf.msg)}` : ''}${rf.run ? ` · ${esc(rf.run)} · hears ${pct0(rf.recall)} · stressed ${pct0(rf.stress)} · ${rf.near ?? 0} sound-alike${rf.near === 1 ? '' : 's'} ${star(rf.run, rf.chosen)}` : ''}</span></div>` : '')
            + (f ? `<div class="wwm-muted" style="margin-bottom:4px">Does <b>${esc(f.value)}</b> help? Read the <i>sources</i> table: "all" against "without ${esc(f.value)}", on the held-out takes tagged ${esc(f.value)}${fn != null ? ` (${fn} of them)` : ''} and overall. With ${g.seeds} seed${g.seeds === 1 ? '' : 's'}, a difference under the seed-to-seed spread is noise.</div>` : '')
            + (axes.length ? `<div class="wwm-grid2">${axes.map(([ax, table]) => `<div><h3>By ${names[ax] || ax}</h3><table class="wwm-table"><thead><tr><th>${esc(names[ax] || ax)}</th><th class="num" title="judged cells of this value">n</th><th class="num">hears you</th><th class="num" title="the worst cell">min</th>${fHead}<th class="num">stressed</th><th class="num">sound-alikes</th><th class="num">gap</th></tr></thead><tbody>${Object.entries(table).map(([v, s]) => `<tr><td>${esc(v === 'oww' ? 'desktop/Pi' : v === 'mww' ? 'ESP32' : v)}</td><td class="num">${s.n}/${s.of}</td><td class="num">${pct0(s.recall)}</td><td class="num">${pct0(s.recall_min)}</td>${fCols(s)}<td class="num">${pct0(s.stress)}</td><td class="num">${s.near == null ? '–' : s.near.toFixed(1)}</td><td class="num">${s.gap == null ? '–' : s.gap.toFixed(2)}</td></tr>`).join('')}</tbody></table></div>`).join('')}</div>` : '<div class="wwm-muted">Nothing judged yet; the pivot fills in as cells finish.</div>')
            + `<details style="margin-top:6px"><summary class="wwm-muted">Every cell (${g.of})</summary><table class="wwm-table"><thead><tr><th>cell</th><th>state</th><th class="num">hears you</th>${fHead}<th class="num">stressed</th><th class="num">sound-alikes</th><th class="num">gap</th><th>run</th></tr></thead><tbody>${g.cells.map(c => `<tr><td>${esc(c.label || '')}</td><td><span class="wwm-pill ${c.state === 'done' ? 'ok' : c.state === 'failed' ? 'bad' : 'warn'}">${esc(c.state)}</span>${c.error ? ` <span class="wwm-error" title="${esc(c.error)}">!</span>` : ''}</td><td class="num">${pct0(c.recall)}</td>${fCols(c)}<td class="num">${pct0(c.stress)}</td><td class="num">${c.near ?? '–'}</td><td class="num">${c.gap == null ? '–' : c.gap.toFixed(2)}</td><td class="wwm-muted">${esc(c.run || '')}${c.chosen ? ' ★' : ''}</td></tr>`).join('')}</tbody></table></details>`;
        slot.querySelectorAll('[data-star]').forEach(b => b.addEventListener('click', async () => {
            const rid = b.dataset.star;
            try {
                const runs = (await api(`/projects/${p.slug}/runs`)).runs || [];
                const r = runs.find(x => x.id === rid);
                if (!r) throw new Error('that run is gone');
                for (const o of runs) if (o.trainer === r.trainer && o.chosen && o.id !== rid) await api(`/projects/${p.slug}/runs/${o.id}`, { method: 'PUT', body: { chosen: false } });
                await api(`/projects/${p.slug}/runs/${rid}`, { method: 'PUT', body: { chosen: !r.chosen } });
                ctx.toast(r.chosen ? 'Unstarred.' : `★ ${r.label || rid}: Install uses it now.`, 'success');
                render(el, ctx);
            } catch (e) { ctx.toast(e.message, 'error'); }
        }));
    } catch (e) { slot.innerHTML = `<span class="wwm-error">${esc(e.message)}</span>`; }
}

const fmt = (v, d = 2) => v == null ? '–' : (typeof v === 'number' ? v.toFixed(d) : v);

function weakest(h) {   // the tag value the model hears worst, among those with two or more held-out takes
    let w = null;
    for (const [ax, table] of Object.entries(h?.by_tag || {})) for (const [v, s] of Object.entries(table)) {
        if (s.n < 2 || s.recall == null || (ax === 'who' && !v)) continue;
        if (!w || s.recall < w.recall) w = { ax, v, ...s };
    }
    return w && w.recall < (h.recall ?? 1) ? w : null;
}

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
    box.innerHTML = `${q.length ? `<div class="wwm-queue"><b>Queued:</b> ${q.map(j => `<span class="wwm-chip">${esc({ train: 'desktop/Pi', train_mww: 'ESP32', tune: 'refine' }[j.kind] || j.kind)} · ${esc(j.args?.preset || '')}${j.args?.recipe ? ' · ' + esc(j.args.recipe) : ''}${j.args?.synth && j.args.synth !== 'all' ? ' · ' + esc(String(j.args.synth)) : ''}${j.args?.label ? ' · ' + esc(j.args.label) : ''}<button data-unqueue="${esc(j.id)}" title="remove from the queue">✕</button></span>`).join('')}</div>` : ''}
      ${f ? `<div class="wwm-muted" style="margin-bottom:6px">Feature set on disk: ${(f.counts?.positive || 0).toLocaleString()} positive rows, ${(f.counts?.adversarial || 0).toLocaleString()} sound-alike rows, ${(f.counts?.ambient || 0).toLocaleString()} room windows · ${f.rounds} variation(s) per clip · ${f.recipe}${f.synth && f.synth !== 'all' ? ' · voices: ' + esc(String(f.synth)) : ''} · ${f.holdout ? 'held-out slice kept apart' : 'no held-out slice'} · built in ${f.seconds}s</div>` : ''}
      ${(() => { const stale = runs.filter(r => r.heldout?.stale).length, unjudged = runs.filter(r => r.state === 'done' && r.has_model && !r.heldout && !r.id.startsWith('builtin-')).length; return (stale || unjudged) ? `<div class="wwm-row" style="margin-bottom:6px"><button class="wwm-btn" id="wwm-rejudge">Re-judge ${stale + unjudged} model${stale + unjudged === 1 ? '' : 's'}</button><span class="wwm-muted">${stale ? `${stale} verdict${stale === 1 ? '' : 's'} came from an older held-out slice (a reshuffle or a dropped clip since). ` : ''}${unjudged ? `${unjudged} finished model${unjudged === 1 ? '' : 's'} never judged. ` : ''}A few seconds per model.</span></div>` : ''; })()}
      ${runs.length ? `<table class="wwm-table wwm-score"><thead><tr><th title="tick to test it live on Test">test</th><th></th><th>Run</th><th>Model</th><th>Label</th><th>What</th><th>Recipe</th><th>State</th><th class="num" title="held-out clips of you it heard, streaming judge">Hears you</th><th class="num" title="held-out sound-alikes it fired on (your near misses)">Sound-alikes</th><th class="num" title="the same held-out takes under four rough draws each (room, noise, pitch, speed, distance), backgrounds from the held-out room: the stress test">Stressed</th><th class="num" title="false alarms per hour on the standard negative set">FA/h</th><th class="num" title="lowest score of your held-out takes minus the highest score of any held-out negative; under 0 they overlap, no threshold separates them">Gap</th><th class="num" title="the judge's pick; type to override">Threshold</th><th class="num">min</th><th title="the one Install puts on the devices, one per model type">use</th><th></th></tr></thead><tbody>
      ${runs.map(r => { const h = r.heldout; const b = bestRow(r.rows); const thr = r.threshold ?? r.cutoff ?? h?.threshold ?? b?.threshold ?? 0.5; const near = h ? h.near_fires : null; const testable = r.state === 'done' && r.has_model;
        return `<tr data-run="${esc(r.id)}" class="wwm-runrow ${r.chosen ? 'wwm-chosen' : ''}" title="click for the curves and the threshold tables">
        <td>${testable ? `<input type="checkbox" data-sel="${esc(r.id)}" ${sel.has(r.id) ? 'checked' : ''}>` : ''}</td><td><span class="wwm-info">i</span></td><td>${esc(r.id)}</td><td>${r.trainer === 'mww' ? 'ESP32' : 'desktop/Pi'}</td><td>${esc(r.label || '')}</td><td>${r.kind === 'tune' ? `${r.refine_of ? 'refine' : 'sweep'} ${r.trials}×${(r.budget_steps / 1000).toFixed(0)}k` : esc(r.preset || '')}${r.cfg && r.cfg.model_type ? ` · ${esc(r.cfg.model_type)}${r.cfg.model_type === 'rnn' ? '' : ` ${r.cfg.layer_size}×${r.cfg.n_blocks}`}` : (r.cfg && r.cfg.pointwise_filters ? ` · mixednet ${esc(r.cfg.pointwise_filters)}` : '')}</td>
        <td>${esc(r.recipe || '')}${r.sources_label && r.sources_label !== 'all' ? ` · ${esc(r.sources_label)}` : (r.synth && r.synth !== 'all' ? ` · ${esc(Array.isArray(r.synth) ? 'voice set' : r.synth)}` : '')}${r.holdout === false ? ' · no holdout' : ''}</td><td><span class="wwm-pill ${r.state === 'done' ? 'ok' : r.state === 'failed' ? 'bad' : 'warn'}">${esc(r.state)}</span></td>
        <td class="num ${h?.stale ? 'wwm-stale' : ''}" ${h?.stale ? 'title="judged on an older held-out slice"' : h && h.recall == null ? 'title="no held-out recordings of you to judge on"' : ''}>${h ? (h.recall == null ? '–' : (100 * h.recall).toFixed(0) + '%') + (h.stale ? ' ⟳' : '') : (b ? `<span class="wwm-muted" title="trainer's own number; not judged yet">${(100 * b.recall).toFixed(0)}%</span>` : '–')}${(() => { const w = weakest(h); return w ? `<br><small class="wwm-muted" title="the tag it hears worst (${esc(w.ax)}): ${w.hit} of ${w.n} held-out takes">${esc(w.v)} ${w.hit}/${w.n}</small>` : ''; })()}</td>
        <td class="num ${near == null ? '' : near === 0 ? 'wwm-good' : near === 1 ? 'wwm-meh' : 'wwm-bad'}">${h && near != null ? `${near}/${h.n_near}` : '–'}</td>
        <td class="num ${h?.stress_recall == null ? '' : h.stress_recall >= 0.9 ? 'wwm-good' : h.stress_recall >= 0.7 ? 'wwm-meh' : 'wwm-bad'}" ${h && h.stress_recall == null ? 'title="not stress-judged yet: Re-judge"' : ''}>${h && h.stress_recall != null ? (100 * h.stress_recall).toFixed(0) + '%' : '–'}</td>
        <td class="num" ${h && h.fp_per_hour == null ? 'title="the trainer did not measure false alarms at this threshold"' : ''}>${h ? (h.fp_per_hour != null ? fmt(h.fp_per_hour) : '–') : (b ? `<span class="wwm-muted" title="trainer's own number at its best threshold; not judged yet">${fmt(b.fp_per_hour)}</span>` : '–')}</td>
        <td class="num ${h?.margin?.gap == null ? '' : h.margin.gap >= 0.3 ? 'wwm-good' : h.margin.gap > 0 ? 'wwm-meh' : 'wwm-bad'}" ${h?.margin ? `title="lowest take ${fmt(h.margin.min_pos)}, highest negative ${fmt(Math.max(h.margin.max_near ?? -1, h.margin.max_neg ?? -1))}, lowest stressed take ${fmt(h.margin.stress_min)}"` : ''}>${h?.margin?.gap != null ? fmt(h.margin.gap) : '–'}</td>
        <td class="num">${testable ? `<input type="number" data-thr="${esc(r.id)}" min="0.05" max="0.999" step="0.01" value="${thr}" style="width:64px">${h && h.clean === false ? '<span class="wwm-warn" title="no threshold met the rule (no other-word fires, at most one sound-alike, under 0.5 false alarms an hour); this is the best of a flawed set">⚠</span>' : ''}${h && h.neg_fires ? `<span class="wwm-warn" title="${h.neg_fires} other-word recording(s) fire at this threshold">!</span>` : ''}` : '–'}</td>
        <td class="num">${r.seconds ? Math.round(r.seconds / 60) : ''}</td><td>${testable ? `<button class="wwm-x ${r.chosen ? 'on' : ''}" data-choose="${esc(r.id)}" title="${r.chosen ? 'Install uses this one' : 'use this one for Install'}">${r.chosen ? '★' : '☆'}</button>` : ''}</td><td class="num"><button class="wwm-x" data-del-run="${esc(r.id)}" title="delete this run">✕</button></td></tr>`; }).join('')}
      </tbody></table><div class="wwm-muted">Hears you, Sound-alikes, Stressed and Gap come from the streaming judge on your held-out clips, the same one Test uses; every finished run is judged on its own. Stressed is the same takes under four rough draws each, backgrounds from the held-out room. Gap under 0 means a take and a negative overlap. Tick models to try them live on Test. ★ marks the one Install puts on the devices.</div>` : '<div class="wwm-muted">No runs yet.</div>'}`;
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
        ${r.heldout?.table ? `<h3>The judge, by threshold <span class="wwm-muted">streaming, your ${r.heldout.n_pos} held-out clips, ${r.heldout.n_near} sound-alikes, ${r.heldout.n_neg} other negatives</span></h3><table class="wwm-table"><thead><tr><th>threshold</th><th class="num">hears you</th><th class="num">stressed</th><th class="num">sound-alikes fired</th><th class="num">other negatives fired</th><th class="num">FA/h standard</th></tr></thead><tbody>
          ${r.heldout.table.map(x => `<tr class="${x.threshold === r.heldout.threshold ? 'wwm-pick' : ''}"><td>${x.threshold}${x.threshold === r.heldout.threshold ? ' ◂' : ''}</td><td class="num">${x.recall == null ? '–' : (100 * x.recall).toFixed(0) + '%'}</td><td class="num">${x.stress_recall == null ? '–' : (100 * x.stress_recall).toFixed(0) + '%'}</td><td class="num ${x.near_fires === 0 ? 'wwm-good' : x.near_fires === 1 ? 'wwm-meh' : 'wwm-bad'}">${x.near_fires}</td><td class="num ${x.neg_fires ? 'wwm-bad' : 'wwm-good'}">${x.neg_fires}</td><td class="num">${fmt(x.fp_per_hour)}</td></tr>`).join('')}</tbody></table>
          <div class="wwm-muted">◂ the pick: no other negative fires, at most one sound-alike, under 0.5 false alarms an hour where the trainer measured it, then the most of you heard, then the most heard under stress, then the threshold nearest the usual one (0.5 desktop, 0.97 ESP32). Type another number in the table above to override.${r.heldout.margin ? ` Margins: lowest take ${fmt(r.heldout.margin.min_pos)} (tenth percentile ${fmt(r.heldout.margin.p10_pos)}), highest sound-alike ${fmt(r.heldout.margin.max_near)}, highest other negative ${fmt(r.heldout.margin.max_neg)}, lowest stressed take ${fmt(r.heldout.margin.stress_min)}, gap ${fmt(r.heldout.margin.gap)}.` : ''}</div>` : ''}
        ${rows.length ? `<details style="margin-top:8px"><summary class="wwm-muted">The trainer's own eval by threshold (feature windows, not streaming)</summary><table class="wwm-table"><thead><tr><th>threshold</th><th class="num">recall (yours)</th><th class="num">negatives fired</th><th class="num">FA/h standard</th><th class="num">FA/h your room</th></tr></thead><tbody>
          ${rows.map(x => { const ok = (x.fp_per_hour || 0) <= 0.5 && (x.own_fa_per_hour || 0) <= 1; return `<tr class="${ok ? '' : 'wwm-dim'}"><td>${x.threshold}</td><td class="num">${x.recall == null ? '–' : (100 * x.recall).toFixed(0) + '%'}</td><td class="num">${x.neg_fp_rate == null ? '–' : (100 * x.neg_fp_rate).toFixed(0) + '%'}</td><td class="num">${fmt(x.fp_per_hour)}</td><td class="num">${fmt(x.own_fa_per_hour)}</td></tr>`; }).join('')}</tbody></table></details>` : ''}
      </div>`;
}

function trialsChart(trials) {
    const w = 520, h = 120;
    const max = Math.max(0.01, ...trials.map(t => t.score || 0));
    const bw = Math.max(6, Math.min(30, (w - 40) / Math.max(1, trials.length)));
    return `<svg viewBox="0 0 ${w} ${h}" class="wwm-chart">${trials.map((t, i) => { const bh = Math.round((h - 24) * (t.score || 0) / max); const x = 30 + i * bw; return `<rect x="${x}" y="${h - 14 - bh}" width="${bw - 2}" height="${bh}" fill="var(--accent, var(--accent-blue))" opacity=".85"><title>trial ${t.trial}: score ${(t.score || 0).toFixed(3)}${t.heldout?.recall != null ? ` · hears ${Math.round(100 * t.heldout.recall)}%, stressed ${Math.round(100 * (t.heldout.stress_recall ?? 0))}%, ${t.heldout.near_fires ?? 0} sound-alikes` : ''} · ${Object.entries(t.params || {}).map(([k, v]) => `${k}=${typeof v === 'number' && v < 0.01 ? v.toExponential(1) : v}`).join(' ')} · ${t.seconds}s</title></rect><text x="${x + bw / 2 - 1}" y="${h - 3}" font-size="8" fill="var(--text-muted)" text-anchor="middle">${t.trial}</text>`; }).join('')}<text x="4" y="10" font-size="9" fill="var(--text-muted)">score = the judge: 0.3 × hears you + 0.7 × stressed, a tenth off per sound-alike (the trainer's own number when a trial could not be judged)</text></svg>`;
}
