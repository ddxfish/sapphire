// datasets.js - the download table, drawn on Settings (the trainer sets) and on Background (rooms and music).
import { api, fmtBytes, esc } from './api.js';
import { showConfirm } from '/static/shared/modal.js';

const FOR = { oww: 'desktop + Pi', mww: 'ESP32', both: 'both' };

/** Draws the sets that pass `keep` into `box`; download and delete are jobs, the status strip shows them. */
export async function drawDatasets(box, ctx, keep = () => true) {
    if (!box) return;
    let data;
    try { data = await api('/datasets'); } catch (e) { box.innerHTML = `<div class="wwm-error">${esc(e.message)}</div>`; return; }
    const sets = data.datasets.filter(keep);
    const running = new Set(ctx.state.jobs.filter(j => j.state === 'running' && j.args?.dataset).map(j => j.args.dataset));
    box.innerHTML = `<table class="wwm-table"><thead><tr><th>Set</th><th>For</th><th class="num">Size</th><th>Licence</th><th></th></tr></thead><tbody>
      ${sets.map(d => `<tr>
        <td><b>${esc(d.label)}</b><div class="wwm-muted">${esc(d.what)}</div></td>
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
        const d = sets.find(x => x.id === b.dataset.get);
        showConfirm(`${d.label}: ${fmtBytes(d.bytes)}. ${d.what} Licence: ${d.licence}.`, async () => {
            try { await api(`/datasets/${d.id}/download`, { method: 'POST' }); await ctx.refreshJobs(); drawDatasets(box, ctx, keep); }
            catch (e) { ctx.toast(e.message, 'error'); }
        }, { title: 'Download', saveLabel: 'Download' });
    }));
    box.querySelectorAll('[data-del]').forEach(b => b.addEventListener('click', () => {
        const d = sets.find(x => x.id === b.dataset.del);
        showConfirm(`Delete ${d.label} from disk (${fmtBytes(d.on_disk)})?`, async () => {
            try { await api(`/datasets/${d.id}`, { method: 'DELETE' }); await ctx.refreshJobs(); setTimeout(() => drawDatasets(box, ctx, keep), 1500); }
            catch (e) { ctx.toast(e.message, 'error'); }
        }, { title: 'Delete dataset', saveLabel: 'Delete' });
    }));
}
