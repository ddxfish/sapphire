// Settings tab: the data folder, the environment, the datasets. Short on the page, long behind "?".
import { api, putSettings, envStatus, buildEnv, fmtBytes, esc } from '../api.js';
import { qbtn, bindHelp } from '../help.js';
import { pickFolder } from '../folder.js';
import { showConfirm } from '/static/shared/modal.js';

let timer = null;

export function render(el, ctx) {
    const s = ctx.state.status || {};
    el.innerHTML = `
      <div class="wwm-card" id="wwm-dir">
        <div class="wwm-row wwm-between">
          <div><h2 style="margin:0">Data folder ${qbtn('folder')}</h2><div class="wwm-pathline">${esc(s.data_dir)}${s.root_ok ? '' : ` <span class="wwm-error">${esc(s.root_error || 'not usable')}</span>`}</div>
            <div class="wwm-muted">${s.root_ok ? `${fmtBytes(s.disk?.free)} free${s.default_dir ? ' · the default, in the Sapphire folder: not in git, not in the nightly backup' : ''}` : ''}</div></div>
          <button class="wwm-btn" id="wwm-dir-change">${s.root_ok ? 'Change' : 'Choose folder'}</button>
        </div>
      </div>
      <div class="wwm-card" id="wwm-env">
        <h2>Environment ${qbtn('env')}</h2>
        <div class="wwm-row"><span id="wwm-env-state" class="wwm-pill">…</span>
          <button class="wwm-btn" id="wwm-env-build">Build</button>
          <span class="wwm-muted">${s.gpu ? `${esc(s.gpu.name)} · ${fmtBytes(s.gpu.total_mb * 1e6)}` : 'no NVIDIA GPU seen: CPU only'}</span>
        </div>
        <pre class="wwm-log" id="wwm-env-log" style="display:none"></pre>
      </div>
      <div class="wwm-card" id="wwm-data">
        <h2>Datasets ${qbtn('datasets')}</h2>
        <div id="wwm-data-table">…</div>
      </div>`;
    bindHelp(el);

    el.querySelector('#wwm-dir-change').addEventListener('click', () => pickFolder(ctx, s.data_dir || ''));
    el.querySelector('#wwm-env-build').addEventListener('click', () => {
        showConfirm('Build the environment? About 7 GB, ten minutes or more. Sapphire stays usable meanwhile.', async () => {
            try { await buildEnv(); ctx.toast('Building. The log appears below.', 'info'); }
            catch (e) { ctx.toast(e.message, 'error'); }
            pollEnv(el);
        }, { title: 'Build environment', saveLabel: 'Build' });
    });
    pollEnv(el);
    drawDatasets(el, ctx);
    return () => { if (timer) clearTimeout(timer); timer = null; };
}

async function pollEnv(el) {
    if (timer) clearTimeout(timer);
    const pill = el.querySelector('#wwm-env-state'), log = el.querySelector('#wwm-env-log'), btn = el.querySelector('#wwm-env-build');
    if (!pill) return;
    try {
        const st = await envStatus();
        const state = st.state || 'unknown';
        const step = st.build?.step || '';
        pill.textContent = { ready: 'ready', missing: 'not built', stale: 'works · pins changed, rebuild when convenient', building: `building: ${step || '…'}`, error: 'build failed', 'no-conda': 'conda not found: install Miniconda, then restart Sapphire' }[state] || state;
        pill.className = 'wwm-pill ' + (state === 'ready' ? 'ok' : state === 'building' || state === 'stale' ? 'warn' : 'bad');
        btn.textContent = state === 'ready' || state === 'stale' ? 'Rebuild' : (state === 'building' ? 'Building…' : 'Build');
        btn.disabled = state === 'building' || state === 'no-conda';
        if (state === 'building' || state === 'error') {
            log.style.display = '';
            log.textContent = (st.log_tail || []).join('\n') || (st.build?.error || '');
            log.scrollTop = log.scrollHeight;
        } else { log.style.display = 'none'; }
        if (state === 'building') timer = setTimeout(() => pollEnv(el), 3000);
    } catch (e) { pill.textContent = e.message; pill.className = 'wwm-pill bad'; }
}

const FOR = { oww: 'desktop + Pi', mww: 'ESP32', both: 'both' };

async function drawDatasets(el, ctx) {
    const box = el.querySelector('#wwm-data-table');
    if (!box) return;
    let data;
    try { data = await api('/datasets'); } catch (e) { box.innerHTML = `<div class="wwm-error">${esc(e.message)}</div>`; return; }
    const running = new Set(ctx.state.jobs.filter(j => j.state === 'running' && j.args?.dataset).map(j => j.args.dataset));
    box.innerHTML = `<table class="wwm-table"><thead><tr><th>Set</th><th>For</th><th class="num">Size</th><th>Licence</th><th></th></tr></thead><tbody>
      ${data.datasets.map(d => `<tr>
        <td><b>${esc(d.label)}</b></td>
        <td>${FOR[d.for] || d.for}</td>
        <td class="num">${fmtBytes(d.bytes)}</td>
        <td><a href="${esc(d.licence_url)}" target="_blank" rel="noopener">${esc(d.licence.split(' (')[0])}</a></td>
        <td class="num">${running.has(d.id) ? '<span class="wwm-pill warn">downloading</span>' : d.state === 'ready'
            ? `<span class="wwm-pill ok">ready</span> <button class="wwm-btn ghost danger" data-del="${d.id}">Delete</button>`
            : `<button class="wwm-btn primary" data-get="${d.id}" ${ctx.state.status?.root_ok ? '' : 'disabled'}>${d.state === 'partial' ? 'Resume' : 'Download'}</button>`}</td>
      </tr>`).join('')}
      </tbody></table>
      <div class="wwm-muted" style="margin-top:8px">${fmtBytes(data.disk?.free)} free on the data drive</div>`;
    box.querySelectorAll('[data-get]').forEach(b => b.addEventListener('click', () => {
        const d = data.datasets.find(x => x.id === b.dataset.get);
        showConfirm(`${d.label}: ${fmtBytes(d.bytes)}. ${d.what} Licence: ${d.licence}.`, async () => {
            try { await api(`/datasets/${d.id}/download`, { method: 'POST' }); await ctx.refreshJobs(); drawDatasets(el, ctx); }
            catch (e) { ctx.toast(e.message, 'error'); }
        }, { title: 'Download', saveLabel: 'Download' });
    }));
    box.querySelectorAll('[data-del]').forEach(b => b.addEventListener('click', () => {
        const d = data.datasets.find(x => x.id === b.dataset.del);
        showConfirm(`Delete ${d.label} from disk (${fmtBytes(d.on_disk)})?`, async () => {
            try { await api(`/datasets/${d.id}`, { method: 'DELETE' }); await ctx.refreshJobs(); setTimeout(() => drawDatasets(el, ctx), 1500); }
            catch (e) { ctx.toast(e.message, 'error'); }
        }, { title: 'Delete dataset', saveLabel: 'Delete' });
    }));
}
