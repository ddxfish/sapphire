// Voices tab: the generate box first (how many, one big button), then the voices to pick and listen to.
// Every change saves at once, so a redraw after a job can never undo a tick.
import { api, audioUrl, esc } from '../api.js';
import { qbtn, bindHelp } from '../help.js';
import { showConfirm } from '/static/shared/modal.js';

export function render(el, ctx) {
    const p = ctx.state.project;
    if (!p) { el.innerHTML = '<div class="wwm-empty">Create a phrase first.</div>'; return; }
    el.innerHTML = '<div class="wwm-loading">Loading voices…</div>';
    api('/voices').then(cat => draw(el, ctx, p, cat)).catch(e => { el.innerHTML = `<div class="wwm-error">${esc(e.message)}</div>`; });
}

const csrf = () => document.querySelector('meta[name="csrf-token"]')?.content || '';

function draw(el, ctx, p, cat) {
    const plan = Object.assign({ preset: 'standard', target: 20000, piper: cat.piper.filter(v => v.default).map(v => v.name), kokoro: cat.kokoro.default || cat.kokoro.english,
        kokoro_other: false, kokoro_other_voices: cat.kokoro.other, blends: true, variation: 'normal', share_kokoro: 0.35 }, p.settings?.synth || {});
    const kokOn = new Set(cat.kokoro.on_disk || []);
    const dot = (on) => `<span class="disk ${on ? 'on' : ''}" title="${on ? 'on disk' : 'not downloaded yet'}">${on ? '●' : '○'}</span>`;
    const speakers = (names) => cat.piper.filter(v => names.includes(v.name)).reduce((a, v) => a + v.speakers, 0);
    const envReady = ['ready', 'stale'].includes(ctx.state.status?.env?.state);
    const gpu = !!ctx.state.status?.gpu;
    const made = p.counts?.['positive/synth'] || 0;
    const minutes = (n) => Math.max(1, Math.round(n / (gpu ? 1800 : 400)));
    const voiceRow = (engine, name, label, checked, onDisk, extra = '') =>
        `<div class="wwm-voice"><button class="play" data-engine="${engine}" data-voice="${esc(name)}" title="hear it say the phrase">▶</button><label><input type="checkbox" value="${esc(name)}" ${checked ? 'checked' : ''}> ${dot(onDisk)}<span class="name">${esc(label)}</span> ${extra}</label></div>`;
    el.innerHTML = `
      <div class="wwm-card wwm-gen">
        <h2>Make the samples ${qbtn('size')}</h2>
        <div class="wwm-row">
          <div class="wwm-seg" id="wwm-preset">
            <button data-v="quick">Quick<small>2,000 clips</small></button>
            <button data-v="standard">Standard<small>20,000 clips</small></button>
            <button data-v="thorough">Thorough<small>60,000 clips</small></button>
            <button data-v="custom">Custom<small><input type="number" id="wwm-target" min="100" max="300000" step="100" value="${plan.target}"></small></button>
          </div>
        </div>
        <div class="wwm-gen-go">
          <button class="wwm-btn primary big" id="wwm-synth" ${envReady ? '' : 'disabled'}>Generate all samples</button>
          <div class="wwm-muted" id="wwm-gen-note">${envReady ? '' : 'The synthesizers live in the plugin\'s own environment: build it on the Settings tab first (one click, a while to download). Recording works meanwhile.'}</div>
        </div>
        <div class="wwm-muted">Synthesizes the phrase in every ticked voice, with speed and delivery varied, plus the sound-alikes.
          ${gpu ? 'Minutes on your GPU' : 'No GPU seen: on a CPU this takes an hour or more for Standard'}; you can record meanwhile.
          Running it again tops the set up to the number above and removes clips from voices you have unticked.</div>
      </div>
      <div class="wwm-card">
        <div class="wwm-row wwm-between"><h2 style="margin:0">Voices ${qbtn('voices')}</h2>
          <span class="wwm-row"><span class="wwm-muted" id="wwm-disk-sum"></span><button class="wwm-btn" id="wwm-fetch" ${envReady ? '' : 'disabled'}>Download checked</button><button class="wwm-btn" id="wwm-change">▸ Choose and listen</button></span></div>
        <div class="wwm-row"><span id="wwm-voice-sum"></span></div>
        <div class="wwm-row">
          <label>Speech variety <select id="wwm-var"><option value="low">low</option><option value="normal">normal</option><option value="high">high</option></select></label>${qbtn('variety')}
          <span class="wwm-vhead"><label><input type="checkbox" id="wwm-blends" ${plan.blends ? 'checked' : ''}> blend pairs into new voices</label>${qbtn('blends')}<button class="play" id="wwm-blend-play" title="hear a random blend" ${envReady ? '' : 'disabled'}>▶</button></span>
          <span class="wwm-vhead"><label><input type="checkbox" id="wwm-kother" ${plan.kokoro_other ? 'checked' : ''}> other-language packs, accented</label>${qbtn('other_packs')}</span>
        </div>
        <div id="wwm-voice-more" style="display:none">
          <div class="wwm-grid2">
            <div><h3>Piper <span class="wwm-muted">(CPU; a voice downloads on first play)</span></h3><div class="wwm-voices" id="wwm-piper">${cat.piper.map(v => voiceRow('piper', v.name, v.name.replace(/^en_(US|GB)-/, '').replace(/-(low|medium|high)$/, ''), plan.piper.includes(v.name), v.on_disk, `<span class="n">${v.speakers > 1 ? v.speakers + ' spk' : v.quality}</span>`)).join('')}</div></div>
            <div><h3>Kokoro <span class="wwm-muted">(GPU)</span></h3>
              <div class="wwm-voices" id="wwm-kokoro">${cat.kokoro.english.map(v => voiceRow('kokoro', v, v, plan.kokoro.includes(v), kokOn.has(v))).join('')}</div>
              <div class="wwm-voices" id="wwm-kokoro-other" style="display:${plan.kokoro_other ? '' : 'none'};margin-top:6px">${cat.kokoro.other.map(v => voiceRow('kokoro', v, v, (plan.kokoro_other_voices || cat.kokoro.other).includes(v), kokOn.has(v))).join('')}</div>
              <div class="wwm-muted" style="margin-top:6px">${cat.kokoro.model_on_disk ? 'Kokoro model on disk' : 'Kokoro model (330 MB) downloads with the first Kokoro voice'}</div></div>
          </div>
          <div class="wwm-row" style="margin-top:10px"><label>Kokoro share <input type="number" id="wwm-share" min="0" max="1" step="0.05" value="${plan.share_kokoro}" style="width:70px" title="Fraction of the clips made by Kokoro; the rest by Piper"></label></div>
        </div>
        <div class="wwm-row" style="margin-top:10px"><button class="wwm-btn" id="wwm-preview" ${envReady ? '' : 'disabled'}>Preview 3 voices</button><span class="wwm-muted">Three random ticked voices say the phrase. Previews are kept apart and never trained on.</span></div>
        <div class="wwm-clips" id="wwm-previews"></div>
      </div>`;
    bindHelp(el);

    let audio = null;
    const seg = el.querySelector('#wwm-preset'), target = el.querySelector('#wwm-target'), varSel = el.querySelector('#wwm-var');
    varSel.value = plan.variation;
    const genNote = () => {
        const n = Math.max(100, parseInt(target.value) || 1000);
        el.querySelector('#wwm-gen-note').textContent = `${n.toLocaleString()} clips, about ${minutes(n)} min` + (made ? ` · ${made.toLocaleString()} made so far` : '');
    };
    const setPreset = (v) => {
        seg.querySelectorAll('button').forEach(b => b.classList.toggle('on', b.dataset.v === v));
        if (cat.presets[v]) target.value = cat.presets[v];
        genNote();
    };
    setPreset(plan.preset);
    seg.querySelectorAll('button').forEach(b => b.addEventListener('click', (e) => { if (e.target.tagName !== 'INPUT') { setPreset(b.dataset.v); autosave(); } }));
    target.addEventListener('input', () => { setPreset('custom'); });
    target.addEventListener('click', () => setPreset('custom'));

    const read = () => ({
        preset: seg.querySelector('button.on')?.dataset.v || 'custom', target: Math.max(100, parseInt(target.value) || 1000), variation: varSel.value,
        share_kokoro: Math.min(1, Math.max(0, parseFloat(el.querySelector('#wwm-share').value) || 0)),
        piper: [...el.querySelectorAll('#wwm-piper input:checked')].map(i => i.value),
        kokoro: [...el.querySelectorAll('#wwm-kokoro input:checked')].map(i => i.value),
        kokoro_other: el.querySelector('#wwm-kother').checked, kokoro_other_voices: [...el.querySelectorAll('#wwm-kokoro-other input:checked')].map(i => i.value),
        blends: el.querySelector('#wwm-blends').checked,
    });
    const save = async () => {
        const synth = read();
        const r = await api(`/projects/${p.slug}`, { method: 'PUT', body: { settings: { synth } } });
        Object.assign(p, r.project);
        return synth;
    };
    let saveTimer = null;
    const autosave = () => { clearTimeout(saveTimer); saveTimer = setTimeout(() => save().catch(e => ctx.toast(e.message, 'error')), 300); };

    const otherChecked = () => el.querySelector('#wwm-kother').checked ? [...el.querySelectorAll('#wwm-kokoro-other input:checked')].map(i => i.value) : [];
    const summary = () => {
        const pn = [...el.querySelectorAll('#wwm-piper input:checked')].map(i => i.value);
        const kv = [...el.querySelectorAll('#wwm-kokoro input:checked')].map(i => i.value).concat(otherChecked());
        el.querySelector('#wwm-voice-sum').textContent = `${pn.length} Piper voices (${speakers(pn).toLocaleString()} speakers) · ${kv.length} Kokoro voices${el.querySelector('#wwm-blends').checked ? ' + blends' : ''}`;
        const have = pn.filter(n => cat.piper.find(v => v.name === n)?.on_disk).length + kv.filter(v => kokOn.has(v)).length;
        const all = pn.length + kv.length;
        el.querySelector('#wwm-disk-sum').textContent = all ? `${have} of ${all} on disk` : '';
        el.querySelector('#wwm-fetch').style.display = have >= all ? 'none' : '';
    };
    el.querySelectorAll('input[type=checkbox]').forEach(i => i.addEventListener('change', () => { summary(); autosave(); }));
    el.querySelectorAll('#wwm-var, #wwm-share, #wwm-target').forEach(i => i.addEventListener('change', autosave));
    el.querySelector('#wwm-kother').addEventListener('change', e => {
        el.querySelector('#wwm-kokoro-other').style.display = e.target.checked ? '' : 'none';
        if (e.target.checked && el.querySelector('#wwm-voice-more').style.display === 'none') el.querySelector('#wwm-change').click();
    });
    summary();

    el.querySelector('#wwm-change').addEventListener('click', (e) => {
        const more = el.querySelector('#wwm-voice-more');
        const open = more.style.display === 'none';
        more.style.display = open ? '' : 'none';
        e.target.textContent = open ? '▾ Done' : '▸ Choose and listen';
    });
    el.querySelector('#wwm-fetch').addEventListener('click', async () => {
        try {
            const synth = await save();
            await ctx.startJob('voices', { piper: synth.piper, kokoro: synth.kokoro.concat(synth.kokoro_other ? synth.kokoro_other_voices : []) });
            ctx.toast('Fetching voices…', 'info');
        } catch (e) { ctx.toast(e.message, 'error'); }
    });

    const playUrl = async (url, btn) => {
        if (btn.classList.contains('busy')) return null;
        btn.classList.add('busy'); const was = btn.textContent; btn.textContent = '…';
        try {
            const r = await fetch(url, { headers: { 'X-CSRF-Token': csrf() } });
            if (!r.ok) { let m = `HTTP ${r.status}`; try { m = (await r.json()).error || m; } catch { /* fine */ } throw new Error(m); }
            const tag = JSON.parse(r.headers.get('X-Sample') || '{}');
            const blob = await r.blob();
            if (audio) { audio.pause(); URL.revokeObjectURL(audio.src); }
            audio = new Audio(URL.createObjectURL(blob));
            audio.play();
            return tag;
        } catch (e) { ctx.toast(`Could not play: ${e.message}`, 'error'); return null; }
        finally { btn.classList.remove('busy'); btn.textContent = was === '…' ? '▶' : was; }
    };
    el.querySelectorAll('.wwm-voice .play').forEach(b => b.addEventListener('click', async () => {
        const tag = await playUrl(`/api/plugin/wakeword-maker/voices/sample?engine=${b.dataset.engine}&voice=${encodeURIComponent(b.dataset.voice)}&text=${encodeURIComponent(p.phrase)}`, b);
        if (tag?.speakers > 1) b.title = `speaker #${tag.speaker} of ${tag.speakers}; press again for another`;
    }));
    el.querySelector('#wwm-blend-play').addEventListener('click', async (e) => {
        const kv = [...el.querySelectorAll('#wwm-kokoro input:checked')].map(i => i.value);
        if (kv.length < 2) return ctx.toast('Tick at least two Kokoro voices to blend', 'error');
        const a = kv[Math.floor(Math.random() * kv.length)];
        let b = kv[Math.floor(Math.random() * kv.length)];
        while (b === a) b = kv[Math.floor(Math.random() * kv.length)];
        const w = (0.3 + Math.random() * 0.4).toFixed(2);
        await playUrl(`/api/plugin/wakeword-maker/voices/sample?engine=kokoro&voice=${a}&blend=${b}@${w}&text=${encodeURIComponent(p.phrase)}`, e.target);
        e.target.title = `${a} + ${b} at ${w}; press again for another`;
    });

    el.querySelector('#wwm-preview').addEventListener('click', async () => {
        try { await save(); await ctx.startJob('synth', { preview: 3 }); ctx.toast('Making three previews…', 'info'); }
        catch (e) { ctx.toast(e.message, 'error'); }
    });
    el.querySelector('#wwm-synth').addEventListener('click', async () => {
        try {
            const synth = await save();
            if (!synth.piper.length && !synth.kokoro.length) return ctx.toast('Tick at least one voice', 'error');
            showConfirm(`Generate about ${synth.target.toLocaleString()} clips of "${p.phrase}" in ${synth.piper.length + synth.kokoro.length + (synth.kokoro_other ? synth.kokoro_other_voices.length : 0)} voices, plus the sound-alikes? About ${minutes(synth.target)} minutes${gpu ? ' on your GPU' : ' on the CPU'}. ${made ? 'Clips from unticked voices are removed first; the rest is topped up.' : ''}`, async () => {
                await ctx.startJob('synth', {});
                ctx.toast('Generating. The status line shows progress.', 'info');
            }, { title: 'Generate all samples', saveLabel: 'Generate' });
        } catch (e) { ctx.toast(e.message, 'error'); }
    });
    loadPreviews(el, p);
    return {};     // a finished job redraws the tab from the shell
}

async function loadPreviews(el, p) {
    const box = el.querySelector('#wwm-previews');
    if (!box) return;
    try {
        const r = await api(`/projects/${p.slug}/clips?collection=previews/synth&limit=6`);
        box.innerHTML = r.clips.map(c => `<div class="wwm-clip"><audio controls preload="metadata" src="${audioUrl(p.slug, 'previews/synth', c.id)}"></audio>
            <span class="tags">${esc(c.engine || '')} · ${esc(c.voice || '')}${c.speaker != null ? ' #' + c.speaker : ''}${c.blend ? ' + ' + esc(c.blend) : ''} · speed ${c.speed ?? ''}</span></div>`).join('');
    } catch { /* fine */ }
}
