// Check tab: rate the clips, then read the set: what you have, what is red, what would make it better, and
// a few charts to look at while you read. The lowest-scoring clips wait at the bottom with keep / drop.
import { api, audioUrl, esc } from '../api.js';
import { qbtn, bindHelp } from '../help.js';
import { showConfirm } from '/static/shared/modal.js';

export function render(el, ctx) {
    const p = ctx.state.project;
    if (!p) { el.innerHTML = '<div class="wwm-empty">Create a phrase first.</div>'; return; }
    const envReady = ['ready', 'stale'].includes(ctx.state.status?.env?.state);
    el.innerHTML = `
      <div class="wwm-card">
        <div class="wwm-row wwm-between">
          <h2 style="margin:0">Check the clips ${qbtn('check')}</h2>
          <div class="wwm-row">
            <label>Bar <input type="number" id="wwm-bar" min="0" max="100" value="${p.settings?.qa_bar ?? 60}" style="width:64px"></label>
            <select id="wwm-rescope" title="what to rate"><option value="new">new clips</option><option value="own">my recordings again</option><option value="all">everything again</option></select>
            <button class="wwm-btn primary" id="wwm-qa-go" ${envReady ? '' : 'disabled'}>Rate</button>
          </div>
        </div>
        ${envReady ? '' : '<div class="wwm-muted">build the environment in Settings first</div>'}
      </div>
      <div id="wwm-check-out"><div class="wwm-loading">Reading the set…</div></div>`;
    bindHelp(el);
    el.querySelector('#wwm-qa-go').addEventListener('click', async () => {
        const v = parseInt(el.querySelector('#wwm-bar').value); const bar = Number.isFinite(v) ? v : 60;
        try {
            await api(`/projects/${p.slug}`, { method: 'PUT', body: { settings: { qa_bar: bar } } });
            const scope = el.querySelector('#wwm-rescope').value;
            await ctx.startJob('qa', { bar, rerate: scope !== 'new', scope: scope === 'own' ? 'own' : 'all' });
            ctx.toast('Rating…', 'info');
        } catch (e) { ctx.toast(e.message, 'error'); }
    });
    load(el, ctx, p);
    return {};     // a finished rating redraws the tab from the shell
}

// --- tiny svg charts ---------------------------------------------------------------------------------
const W = 420, H = 110, PAD = 22;
function hist(series, { lo, hi, unit = '', mark = null, labels = [] }) {
    const bins = series[0].data.length;
    const max = Math.max(1, ...series.flatMap(s => s.data));
    const bw = (W - PAD) / bins;
    const bars = series.map((s, si) => s.data.map((v, i) => {
        const h = Math.round((H - 18) * v / max);
        const x = PAD + i * bw + si * (bw / series.length);
        return `<rect x="${x.toFixed(1)}" y="${H - 14 - h}" width="${(bw / series.length - 1).toFixed(1)}" height="${h}" fill="${s.color}" opacity=".85"><title>${s.name}: ${v} at ${(lo + (hi - lo) * i / bins).toFixed(unit === 's' ? 1 : 0)}${unit}</title></rect>`;
    }).join('')).join('');
    const ticks = [0, 0.5, 1].map(f => `<text x="${PAD + (W - PAD) * f}" y="${H - 2}" font-size="9" fill="var(--text-muted)" text-anchor="${f === 0 ? 'start' : f === 1 ? 'end' : 'middle'}">${(lo + (hi - lo) * f).toFixed(unit === 's' ? 1 : 0)}${unit}</text>`).join('');
    const markLine = mark != null ? `<line x1="${PAD + (W - PAD) * (mark - lo) / (hi - lo)}" x2="${PAD + (W - PAD) * (mark - lo) / (hi - lo)}" y1="0" y2="${H - 14}" stroke="var(--error, #e66)" stroke-dasharray="3 3"/>` : '';
    const legend = series.map((s, i) => `<rect x="${PAD + i * 110}" y="2" width="8" height="8" fill="${s.color}"/><text x="${PAD + 12 + i * 110}" y="10" font-size="9" fill="var(--text-muted)">${s.name}</text>`).join('');
    return `<svg viewBox="0 0 ${W} ${H}" class="wwm-chart">${bars}${markLine}${ticks}${legend}</svg>`;
}
function hbars(rows, key, { max = null, fmt = (v) => v, color = 'var(--accent, var(--accent-blue))' } = {}) {
    const m = max ?? Math.max(1, ...rows.map(r => r[key] || 0));
    return `<div class="wwm-hbars">${rows.map(r => `<div class="wwm-hbar"><span class="l" title="${esc(r.name)}">${esc(r.name)}</span><i style="width:${Math.min(100, 100 * (r[key] || 0) / m)}%;background:${typeof color === 'function' ? color(r) : color}"></i><b>${fmt(r[key])}</b></div>`).join('')}</div>`;
}

async function load(el, ctx, p) {
    const out = el.querySelector('#wwm-check-out');
    let r, q;
    try { [r, q] = await Promise.all([api(`/projects/${p.slug}/check`), api(`/projects/${p.slug}/qa`)]); }
    catch (e) { out.innerHTML = `<div class="wwm-error">${esc(e.message)}</div>`; return; }
    const c = r.counts, ch = r.charts;
    const mark = { ok: '✓', info: 'ℹ', warn: '⚠', bad: '✗' }, order = { bad: 0, warn: 1, info: 2, ok: 3 };
    const stat = (n, label, sub) => `<div class="wwm-stat small"><b>${typeof n === 'number' ? n.toLocaleString() : n}</b><span>${label}</span><small>${sub || ''}</small></div>`;
    const scoreColor = (row) => row.score == null ? 'var(--text-muted)' : row.score < 60 ? 'var(--error, #e66)' : row.score < 80 ? '#e9a33a' : 'var(--success, #6c6)';
    out.innerHTML = `
      <div class="wwm-thirds">
        ${stat(c.positive.synth + c.positive.recorded + c.positive.uploaded, 'Says it', `${c.positive.synth.toLocaleString()} made · ${c.positive.recorded} recorded${c.positive.uploaded ? ' · ' + c.positive.uploaded + ' uploaded' : ''}`)}
        ${stat(c.near.synth + c.near.recorded + c.near.uploaded, 'Near misses', `${c.near.synth.toLocaleString()} made · ${c.near.recorded} recorded`)}
        ${stat(c.negative.recorded + c.negative.mined, "Doesn't say it", `${c.negative.recorded} recorded${c.negative.mined ? ' · ' + c.negative.mined + ' mined' : ''}`)}
      </div>
      <div class="wwm-thirds">
        ${stat(c.ambient.minutes + ' min', 'Room sound', `${c.ambient.takes} take${c.ambient.takes === 1 ? '' : 's'}`)}
        ${stat(c.rated ? Math.round(100 * c.rated / Math.max(1, c.total)) + '%' : '0%', 'Rated', c.rated ? `${c.rated.toLocaleString()} of ${c.total.toLocaleString()}` : 'press Rate')}
        ${stat(c.dropped, 'Dropped', c.rated ? `${Math.round(100 * c.dropped / Math.max(1, c.rated))}% of rated · still on disk, skipped by training` : '')}
      </div>
      ${c.dropped ? `<div class="wwm-card" id="wwm-dropped-card"><div class="wwm-row wwm-between"><h2 style="margin:0">Dropped: ${c.dropped} <span class="wwm-muted">still on disk, skipped by training</span></h2>
          <span class="wwm-row"><button class="wwm-btn" id="wwm-rerate-dropped" title="rate only the dropped clips again, with the current rules">Re-rate dropped</button><button class="wwm-btn danger" id="wwm-del-dropped">Delete all ${c.dropped}</button></span></div>
          <table class="wwm-table" id="wwm-dropped-reasons"><tbody><tr><td class="wwm-muted">reading…</td></tr></tbody></table></div>` : ''}
      <div class="wwm-card">
        <h2>What to do about it</h2>
        <ul class="wwm-checks">${r.advice.slice().sort((a, b) => order[a.level] - order[b.level]).map(a => `<li class="${a.level}">${mark[a.level]} ${esc(a.text)}</li>`).join('') || '<li class="wwm-muted">Nothing to say yet.</li>'}</ul>
        ${Object.keys(r.flags).length ? `<div class="wwm-muted" style="margin-top:6px">Flags across the set: ${Object.entries(r.flags).map(([f, n]) => `${esc(f)} ${n}`).join(' · ')}</div>` : ''}
      </div>
      <div class="wwm-grid2">
        <div class="wwm-card"><h3>Scores ${c.rated ? '' : '<span class="wwm-muted">(after rating)</span>'}</h3>
          ${hist([{ name: 'says it', data: ch.score_pos, color: 'var(--accent, var(--accent-blue))' }, { name: "doesn't", data: ch.score_neg, color: '#e9a33a' }], { lo: 0, hi: 100, mark: ch.bar })}
          <div class="wwm-muted">Left of the dashed line is dropped. A clean set bunches at the right.</div></div>
        <div class="wwm-card"><h3>Clip length</h3>
          ${hist([{ name: 'yours', data: ch.length_own, color: 'var(--accent, var(--accent-blue))' }, { name: 'made', data: ch.length_synth, color: 'var(--text-muted)' }], { lo: 0, hi: 3, unit: 's' })}
          <div class="wwm-muted">Yours should sit where the made ones do; a tail far right is room kept in, far left is a cut word.</div></div>
        <div class="wwm-card"><h3>Loudness (peak)</h3>
          ${hist([{ name: 'yours', data: ch.level_own, color: 'var(--accent, var(--accent-blue))' }, { name: 'made', data: ch.level_synth, color: 'var(--text-muted)' }], { lo: -40, hi: 0, unit: ' dB' })}
          <div class="wwm-muted">Training varies level anyway; a pile at the far left is a mic too quiet, at 0 is clipping.</div></div>
        <div class="wwm-card"><h3>Ways of speaking (yours)</h3>
          ${hbars(Object.entries(r.styles).map(([name, n]) => ({ name, n })), 'n')}</div>
      </div>
      <div class="wwm-grid2">
        <div class="wwm-card"><h3>Your microphones</h3>
          <table class="wwm-table"><thead><tr><th>Mic</th><th class="num">Clips</th><th class="num">Peak</th><th class="num">Flagged</th><th class="num">Score</th></tr></thead><tbody>
          ${r.mics.map(m => `<tr><td>${esc(m.name)}</td><td class="num">${m.n}</td><td class="num">${m.level} dB</td><td class="num">${m.flagged}</td><td class="num" style="color:${scoreColor(m)}">${m.score ?? '–'}</td></tr>`).join('') || '<tr><td colspan="5" class="wwm-muted">no recordings yet</td></tr>'}</tbody></table></div>
        <div class="wwm-card"><h3>Synthetic voices, weakest first</h3>
          ${r.voices.length ? hbars(r.voices.slice(0, 14).map(v => ({ ...v, name: `${v.name.replace(/^en_(US|GB)-/, '').replace(/-(low|medium|high)$/, '')} (${v.n})`, val: v.score ?? 0 })), 'val', { max: 100, fmt: v => v || '–', color: scoreColor }) : '<div class="wwm-muted">no synthetic clips yet</div>'}
          <div class="wwm-muted">Under 70 after rating: the voice is mispronouncing the phrase. Untick it under Voices and make the set again.</div></div>
      </div>
      <div class="wwm-card">
        <h2>Lowest scores ${q.summary ? `<span class="wwm-muted">rated ${new Date((q.summary.when || 0) * 1000).toLocaleString()}</span>` : ''}</h2>
        <div class="wwm-clips">${(q.worst || []).map(cl => `<div class="wwm-clip ${cl.verdict === 'drop' ? 'bad' : ''}" data-c="${esc(cl.collection)}" data-id="${esc(cl.id)}">
            <button class="play" data-play="${audioUrl(p.slug, cl.collection, cl.id)}">▶</button>
            <span class="tags"><b>${cl.qa?.score ?? ''}</b> · heard "${esc(cl.qa?.transcript || '')}" · ${(cl.qa?.reasons || []).map(esc).join(', ')} · ${esc(cl.voice || cl.device || '')}</span>
            <button class="wwm-btn" data-keep>keep</button><button class="wwm-btn danger" data-drop>drop</button></div>`).join('') || '<div class="wwm-muted">appears after a rating</div>'}</div>
      </div>`;
    const act = (body, title, label) => showConfirm(title, async () => {
        const r = await api(`/projects/${p.slug}/delete-dropped`, { method: 'POST', body });
        ctx.toast(r.kept != null ? `Kept back ${r.kept}` : `Deleted ${r.deleted}`, 'success');
        p.counts = r.counts;
        load(el, ctx, p);
    }, { title: label, saveLabel: label.split(' ')[0] });
    out.querySelector('#wwm-del-dropped')?.addEventListener('click', () => act({}, `Delete all ${c.dropped} dropped clips from disk? Kept clips and their scores stay.`, 'Delete all'));
    out.querySelector('#wwm-rerate-dropped')?.addEventListener('click', async () => {
        try { await ctx.startJob('qa', { bar: p.settings?.qa_bar ?? 60, only_dropped: true }); ctx.toast('Re-rating the dropped clips…', 'info'); }
        catch (e) { ctx.toast(e.message, 'error'); }
    });
    if (c.dropped) {
        api(`/projects/${p.slug}/dropped`).then(d => {
            const tb = out.querySelector('#wwm-dropped-reasons tbody');
            if (!tb) return;
            tb.innerHTML = d.reasons.map(x => `<tr><td>${esc(x.reason)}</td><td class="num">${x.n}</td>
                <td class="num"><button class="wwm-btn ghost" data-keep-reason="${esc(x.reason)}">Keep these</button> <button class="wwm-btn ghost danger" data-del-reason="${esc(x.reason)}">Delete these</button></td></tr>`).join('');
            tb.querySelectorAll('[data-del-reason]').forEach(b => b.addEventListener('click', () => act({ reason: b.dataset.delReason }, `Delete the clips dropped for "${b.dataset.delReason}" from disk?`, 'Delete these')));
            tb.querySelectorAll('[data-keep-reason]').forEach(b => b.addEventListener('click', () => act({ reason: b.dataset.keepReason, keep: true }, `Keep back the clips dropped for "${b.dataset.keepReason}"? They go into training.`, 'Keep these')));
        }).catch(() => {});
    }
    let playing = null;
    out.querySelectorAll('.wwm-clip').forEach(row => {
        const set = async (verdict) => {
            await api(`/projects/${p.slug}/clips/${row.dataset.c}/${row.dataset.id}`, { method: 'PUT', body: { verdict } });
            row.classList.toggle('bad', verdict === 'drop');
        };
        row.querySelector('[data-keep]').addEventListener('click', () => set('keep'));
        row.querySelector('[data-drop]').addEventListener('click', () => set('drop'));
        row.querySelector('[data-play]').addEventListener('click', (e) => {
            const b = e.currentTarget;
            if (playing) { playing.audio.pause(); playing.btn.textContent = '▶'; if (playing.btn === b) { playing = null; return; } }
            const audio = new Audio(b.dataset.play); playing = { audio, btn: b }; b.textContent = '■';
            audio.addEventListener('ended', () => { if (playing?.btn === b) { playing = null; b.textContent = '▶'; } });
            audio.play().catch(() => { b.textContent = '▶'; playing = null; });
        });
    });
}
