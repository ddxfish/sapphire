// Variations tab: one slider, a dry run you can read and hear, the held-out slice, and the rows under a fold.
// Nothing is written to disk; both trainers draw a fresh variation per clip per pass from this recipe.
import { api, audioUrl, esc } from '../api.js';
import { qbtn, bindHelp } from '../help.js';
import { showConfirm } from '/static/shared/modal.js';

const ROWS = [['room', 'Rooms', 'v_room'], ['background', 'Background sound', 'v_background'], ['far', 'Next room', 'v_far'],
    ['pitch', 'Pitch', 'v_pitch'], ['speed', 'Speed', 'v_speed'], ['eq', 'Tone', 'v_eq'], ['notch', 'Notch', 'v_notch'],
    ['distortion', 'Distortion', 'v_distortion'], ['level', 'Level', 'v_level']];
const STOP_WORDS = { gentle: 'Gentle: a quiet home, close to the mic.', mild: 'Mild: a quiet home with some life in it.', natural: 'Natural: a living room with a TV. Fits most homes.',
    firm: 'Firm: a busy room, the mic across it.', rough: 'Rough: a loud house, far mics, cheap speakers.' };
let saveTimer = null, audio = null;

export function render(el, ctx, compact = false) {
    const p = ctx.state.project;
    if (!p) { el.innerHTML = '<div class="wwm-empty">Create a phrase first.</div>'; return; }
    el.innerHTML = '<div class="wwm-loading">Loading…</div>';
    Promise.all([api('/variations/presets'), api('/datasets'), api(`/projects/${p.slug}/holdout`).catch(() => null)])
        .then(([pr, ds, ho]) => { if (compact) el.dataset.compact = '1'; draw(el, ctx, p, pr, ds, ho); if (compact) fold(el); })
        .catch(e => { el.innerHTML = `<div class="wwm-error">${esc(e.message)}</div>`; });
}

// compact: the slider stays open; the dry run, the ear, the held-out slice and fine tune become one row of toggles
function fold(el) {
    const cards = [...el.querySelectorAll(':scope > .wwm-card')];
    if (cards.length < 5) return;
    const [slider, dry, ear, held, fine] = cards;
    const names = [['dry', 'Does the phrase survive it?'], ['ear', 'Hear it'], ['held', 'Held out for testing'], ['fine', 'Fine tune']];
    const row = document.createElement('div');
    row.className = 'wwm-row wwm-foldrow';
    row.innerHTML = names.map(([k, label]) => `<button class="wwm-btn" data-fold="${k}">▸ ${label}</button>`).join('');
    slider.appendChild(row);
    const map = { dry, ear, held, fine };
    for (const c of [dry, ear, held, fine]) c.style.display = 'none';
    fine.querySelector('#wwm-fine')?.remove();
    const rows = fine.querySelector('#wwm-fine-rows'); if (rows) rows.style.display = '';
    row.querySelectorAll('[data-fold]').forEach(b => b.addEventListener('click', () => {
        const c = map[b.dataset.fold]; const open = c.style.display === 'none';
        c.style.display = open ? '' : 'none';
        b.textContent = (open ? '▾ ' : '▸ ') + b.textContent.slice(2);
    }));
}

let RENAMED = {};      // old stop names -> today's (harsh -> rough), from the presets door
function merged(presets, saved) {
    const name = RENAMED[saved?.preset] || saved?.preset;
    const base = presets[name] || presets.natural;
    const out = {};
    for (const [k, v] of Object.entries(base)) out[k] = (v && typeof v === 'object') ? { ...v, ...(saved?.[k] || {}) } : (saved?.[k] ?? v);
    if (saved?.preset) out.preset = name;
    return out;
}

function draw(el, ctx, p, pr, ds, ho) {
    const presets = pr.presets, stops = pr.stops || ['gentle', 'mild', 'natural', 'firm', 'rough'];
    RENAMED = pr.renamed || {};
    let recipe = merged(presets, p.settings?.augment);
    const envReady = ['ready', 'stale'].includes(ctx.state.status?.env?.state);
    const have = Object.fromEntries((ds.datasets || []).map(d => [d.id, d.state === 'ready']));
    const ambientN = (p.counts?.['ambient/recorded'] || 0) + (p.counts?.['ambient/uploaded'] || 0);
    const stopIdx = Math.max(0, stops.indexOf(recipe.preset));
    const h = ho?.holdout;
    const pct = (k) => Math.round((recipe[k].p ?? 1) * 100);
    const row = (k, label, help, controls, note = '') => `
      <div class="wwm-vrow ${recipe[k].on ? '' : 'off'}" data-k="${k}">
        <label class="wwm-vtoggle"><input type="checkbox" data-on ${recipe[k].on ? 'checked' : ''}> <b>${label}</b></label>${qbtn(help)}
        <div class="wwm-vctl">${controls}</div>
        <span class="wwm-muted wwm-vnote">${note}</span>
        <button class="play" data-hear="${k}" title="hear just this one" ${envReady ? '' : 'disabled'}>▶</button>
      </div>`;
    const chance = (k) => `<label>chance <input type="range" data-p min="0" max="100" value="${pct(k)}"><span data-pv>${pct(k)}%</span></label>`;
    const num = (k, field, label, min, max, step, idx) => {
        const v = idx == null ? recipe[k][field] : recipe[k][field][idx];
        return `<label>${label} <input type="number" data-f="${field}" ${idx != null ? `data-i="${idx}"` : ''} min="${min}" max="${max}" step="${step}" value="${v}"></label>`;
    };
    el.innerHTML = `
      <div class="wwm-card">
        <h2>How much the clips are varied for training ${qbtn('variations')}</h2>
        <div class="wwm-slider">
          <input type="range" id="wwm-intensity" min="0" max="${stops.length - 1}" step="1" value="${recipe.preset === 'custom' ? stopIdx : stopIdx}">
          <div class="wwm-stops">${stops.map((s, i) => `<span class="${i === stopIdx && recipe.preset !== 'custom' ? 'on' : ''}" data-i="${i}">${s[0].toUpperCase() + s.slice(1)}</span>`).join('')}</div>
        </div>
        <div class="wwm-muted" id="wwm-stopword">${recipe.preset === 'custom' ? 'Custom: a row under Fine tune has been changed.' : STOP_WORDS[recipe.preset] || ''}</div>
      </div>
      <div class="wwm-card">
        <div class="wwm-row wwm-between">
          <h2 style="margin:0">Does the phrase survive it? ${qbtn('dryrun')}</h2>
          <button class="wwm-btn primary" id="wwm-dryrun" ${envReady ? '' : 'disabled'}>Dry run: 300 draws</button>
        </div>
        <div id="wwm-dry-out" class="wwm-muted">${envReady ? 'Thirty seconds. Whisper listens to the varied clips and the clean ones; you get a hit rate and the five harshest survivors to hear.' : 'build the environment in Settings first'}</div>
      </div>
      <div class="wwm-card">
        <div class="wwm-row wwm-between">
          <h2 style="margin:0">Hear it</h2>
          <div class="wwm-row">
            <select id="wwm-vsrc"><option value="random">a random synthetic clip</option><option value="recorded">my newest recording</option></select>
            <button class="wwm-btn" id="wwm-vorig" ${envReady ? '' : 'disabled'}>▶ Original</button>
            <button class="wwm-btn primary" id="wwm-vdraw" ${envReady ? '' : 'disabled'}>▶ A variation</button>
          </div>
        </div>
        <div class="wwm-muted" id="wwm-vnote">${envReady ? 'Every press is a new draw of the recipe above.' : ''}</div>
      </div>
      <div class="wwm-card">
        <div class="wwm-row wwm-between">
          <h2 style="margin:0">Held out for testing ${qbtn('holdout')}</h2>
          <button class="wwm-btn ghost" id="wwm-reshuffle">Reshuffle</button> <label class="wwm-muted">holding out <input type="number" id="wwm-hofrac" min="5" max="40" step="5" value="${Math.round(100 * ((ho?.holdout?.fraction ?? ho?.fraction ?? 0.15)))}" style="width:56px">% of your recordings</label>
        </div>
        <div class="wwm-muted" id="wwm-holdout">${h ? `${h.counts.positive[0]} of ${h.counts.positive[1]} of your positives · ${h.counts.near[0]} of ${h.counts.near[1]} near misses · ${h.counts.negative[0]} of ${h.counts.negative[1]} negatives · ${h.counts.ambient_minutes[0]} of ${h.counts.ambient_minutes[1]} min of room (${h.counts.ambient[0]} takes). Never trained on, never varied: Test grades on these.` : 'made when the first run starts'}</div>
      </div>
      <div class="wwm-card">
        <button class="wwm-btn" id="wwm-fine">▸ Fine tune</button>
        <div id="wwm-fine-rows" style="display:${recipe.preset === 'custom' ? '' : 'none'}">
          <div class="wwm-vrows" style="margin-top:10px">
            ${row('room', 'Rooms', 'v_room', chance('room'), have.rirs ? '270 real rooms' : 'synthetic rooms until the Room impulse responses set is downloaded (Settings)')}
            ${row('background', 'Background sound', 'v_background', chance('background') + num('background', 'snr', 'from', -20, 30, 1, 0) + num('background', 'snr', 'to dB', -20, 30, 1, 1)
                + `<label><input type="checkbox" data-f="ambient" ${recipe.background.ambient ? 'checked' : ''}> my room (${ambientN}, drawn twice as often)</label><label><input type="checkbox" data-f="music" ${recipe.background.music ? 'checked' : ''}> music${have.music ? '' : ' (not downloaded)'}</label><label><input type="checkbox" data-f="noise" ${recipe.background.noise ? 'checked' : ''}> static</label>`)}
            ${row('far', 'Next room', 'v_far', chance('far'))}
            ${row('pitch', 'Pitch', 'v_pitch', chance('pitch') + num('pitch', 'semitones', '± semitones', 0, 8, 0.5))}
            ${row('speed', 'Speed', 'v_speed', chance('speed') + num('speed', 'range', 'from', 0.5, 1.5, 0.05, 0) + num('speed', 'range', 'to ×', 0.5, 1.5, 0.05, 1))}
            ${row('eq', 'Tone', 'v_eq', chance('eq') + num('eq', 'db', '± dB', 0, 15, 1))}
            ${row('notch', 'Notch', 'v_notch', chance('notch'))}
            ${row('distortion', 'Distortion', 'v_distortion', chance('distortion') + num('distortion', 'max', 'up to', 0.01, 0.5, 0.01))}
            ${row('level', 'Level', 'v_level', num('level', 'range', 'from', -60, 0, 1, 0) + num('level', 'range', 'to dB', -60, 12, 1, 1) + `<label><input type="checkbox" data-f="norm" ${recipe.level.norm !== false ? 'checked' : ''}> start every clip at the same level</label>`)}
          </div>
        </div>
      </div>`;
    bindHelp(el);

    const save = async () => {
        clearTimeout(saveTimer);
        try { const r = await api(`/projects/${p.slug}`, { method: 'PUT', body: { settings: { augment: recipe } } }); Object.assign(p, r.project); }
        catch (e) { ctx.toast(e.message, 'error'); }
    };
    const slider = el.querySelector('#wwm-intensity');
    const setStop = (i) => {
        recipe = merged(presets, { preset: stops[i] });
        recipe.preset = stops[i];
        el.querySelectorAll('.wwm-stops span').forEach((sp, j) => sp.classList.toggle('on', j === i));
        el.querySelector('#wwm-stopword').textContent = STOP_WORDS[stops[i]] || '';
        save().then(() => { draw(el, ctx, p, pr, ds, ho); if (el.dataset.compact) fold(el); });
    };
    slider.addEventListener('change', () => setStop(+slider.value));
    el.querySelectorAll('.wwm-stops span').forEach(sp => sp.addEventListener('click', () => { slider.value = sp.dataset.i; setStop(+sp.dataset.i); }));
    el.querySelector('#wwm-fine').addEventListener('click', (e) => {
        const box = el.querySelector('#wwm-fine-rows');
        const open = box.style.display === 'none';
        box.style.display = open ? '' : 'none';
        e.target.textContent = open ? '▾ Fine tune' : '▸ Fine tune';
    });
    const touched = () => {
        recipe.preset = 'custom';
        el.querySelectorAll('.wwm-stops span').forEach(sp => sp.classList.remove('on'));
        el.querySelector('#wwm-stopword').textContent = 'Custom: a row under Fine tune has been changed.';
        clearTimeout(saveTimer); saveTimer = setTimeout(save, 400);
    };
    el.querySelectorAll('.wwm-vrow').forEach(r => {
        const k = r.dataset.k;
        r.querySelector('[data-on]').addEventListener('change', e => { recipe[k].on = e.target.checked; r.classList.toggle('off', !e.target.checked); touched(); });
        r.querySelector('[data-p]')?.addEventListener('input', e => { recipe[k].p = e.target.value / 100; r.querySelector('[data-pv]').textContent = e.target.value + '%'; touched(); });
        r.querySelectorAll('[data-f]').forEach(inp => inp.addEventListener('change', () => {
            const f = inp.dataset.f;
            if (inp.type === 'checkbox') recipe[k][f] = inp.checked;
            else if (inp.dataset.i != null) { const arr = [...recipe[k][f]]; arr[+inp.dataset.i] = parseFloat(inp.value); recipe[k][f] = arr; }
            else recipe[k][f] = parseFloat(inp.value);
            touched();
        }));
    });
    el.querySelector('#wwm-reshuffle').addEventListener('click', () => showConfirm('Pick a new held-out slice? Earlier Test results stop being comparable with later ones.', async () => {
        const fraction = Math.max(5, Math.min(40, parseInt(el.querySelector('#wwm-hofrac').value) || 15)) / 100;
        const r = await api(`/projects/${p.slug}/holdout`, { method: 'POST', body: { fraction } });
        ho = r; draw(el, ctx, p, pr, ds, ho); if (el.dataset.compact) fold(el);
    }, { title: 'Reshuffle the held-out slice', saveLabel: 'Reshuffle' }));

    // the ear
    const note = el.querySelector('#wwm-vnote');
    const play = async (url) => {
        const r = await fetch(url, { headers: { 'X-CSRF-Token': document.querySelector('meta[name="csrf-token"]')?.content || '' } });
        if (!r.ok) { let m = `HTTP ${r.status}`; try { m = (await r.json()).error || m; } catch { /* fine */ } throw new Error(m); }
        const tag = JSON.parse(r.headers.get('X-Sample') || '{}');
        const blob = await r.blob();
        if (audio) { audio.pause(); URL.revokeObjectURL(audio.src); }
        audio = new Audio(URL.createObjectURL(blob));
        audio.play();
        return tag;
    };
    const src = async () => {
        const v = el.querySelector('#wwm-vsrc').value;
        if (v === 'random') return 'random';
        const r = await api(`/projects/${p.slug}/clips?collection=positive/recorded&limit=1`);
        if (!r.clips.length) throw new Error('no recording yet');
        return `positive/recorded/${r.clips[0].id}`;
    };
    let last = 'random';
    el.querySelector('#wwm-vdraw').addEventListener('click', async () => {
        try {
            const clip = await src();
            const tag = await play(`/api/plugin/wakeword-maker/projects/${p.slug}/variation?clip=${encodeURIComponent(clip)}`);
            last = tag.clip || clip;
            note.textContent = `Applied: ${(tag.applied || []).join(', ') || 'nothing this time'}` + (tag.missing?.length ? ` · missing sets: ${tag.missing.join(', ')}` : '');
        } catch (e) { note.textContent = e.message; }
    });
    el.querySelector('#wwm-vorig').addEventListener('click', async () => {
        try {
            const clip = last === 'random' ? await src() : last;
            if (clip === 'random') { note.textContent = 'press A variation first; Original then plays the same clip dry'; return; }
            const [kind, s, cid] = clip.split('/');
            await play(audioUrl(p.slug, `${kind}/${s}`, cid));
            note.textContent = 'Original.';
        } catch (e) { note.textContent = e.message; }
    });
    el.querySelectorAll('[data-hear]').forEach(b => b.addEventListener('click', async () => {
        try {
            const clip = last === 'random' ? await src() : last;
            const tag = await play(`/api/plugin/wakeword-maker/projects/${p.slug}/variation?clip=${encodeURIComponent(clip)}&want=${b.dataset.hear}`);
            last = tag.clip || clip;
            note.textContent = `Just ${b.dataset.hear}.`;
        } catch (e) { note.textContent = e.message; }
    }));

    // the dry run
    el.querySelector('#wwm-dryrun').addEventListener('click', async (e) => {
        const out = el.querySelector('#wwm-dry-out');
        e.target.disabled = true; out.textContent = 'Drawing and listening… about thirty seconds.';
        try {
            const d = await api(`/projects/${p.slug}/dryrun?n=300`);
            const hv = d.heard_varied, hc = d.heard_clean;
            const bar = (vals, lo, hi, unit) => { const m = Math.max(1, ...vals); return `<div class="wwm-mini">${vals.map((v, i) => `<i style="height:${Math.round(100 * v / m)}%" title="${(lo + (hi - lo) * i / vals.length).toFixed(0)}${unit}: ${v}"></i>`).join('')}</div><div class="wwm-muted" style="font-size:var(--font-xs)">${lo}${unit} … ${hi}${unit}</div>`; };
            out.innerHTML = `
              <div class="wwm-dryline"><b>${esc(d.verdict)}</b></div>
              <div class="wwm-thirds">
                <div class="wwm-stat small"><b>${hv.own ?? hv.synth ?? '–'}%</b><span>Whisper still hears the phrase</span><small>varied: yours ${hv.own ?? '–'}% · synthetic ${hv.synth ?? '–'}%<br>clean: yours ${hc.own ?? '–'}% · synthetic ${hc.synth ?? '–'}%</small></div>
                <div class="wwm-stat small"><b>${Math.round(100 * d.rejected / Math.max(1, d.n + d.rejected))}%</b><span>draws redrawn</span><small>the voice sank under the noise${d.tamed ? ` · tamed ${d.tamed}× (floor now ${d.snr_floor_now[0]} dB)` : ''}</small></div>
                <div class="wwm-stat small"><span>voice over background</span>${bar(d.snr_hist, -15, 25, ' dB')}</div>
              </div>
              <div class="wwm-muted" style="margin-top:8px">The five harshest survivors (lowest voice over background). If you can still hear the word, the recipe is honest.</div>
              <div class="wwm-clips">${d.harshest.map(hs => `<div class="wwm-clip"><button class="play" data-clip="${esc(hs.clip)}" data-seed="${hs.seed}">▶</button><span class="tags">${hs.snr.toFixed(1)} dB over background · ${esc(hs.applied.join(', '))} · ${hs.kind === 'own' ? 'your voice' : 'synthetic'}</span></div>`).join('')}</div>`;
            out.querySelectorAll('[data-seed]').forEach(b => b.addEventListener('click', async () => {
                try { await play(`/api/plugin/wakeword-maker/projects/${p.slug}/variation?clip=${encodeURIComponent(b.dataset.clip)}&seed=${b.dataset.seed}`); } catch (err) { ctx.toast(err.message, 'error'); }
            }));
        } catch (err) { out.textContent = err.message; }
        e.target.disabled = false;
    });
}
