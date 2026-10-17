// Install tab: the ★ models onto the things that listen. This computer swaps live; a satellite takes the model
// through its door when its body has one, else the files are here to copy by hand.
import { api, esc } from '../api.js';
import { qbtn, bindHelp } from '../help.js';
import { showConfirm } from '/static/shared/modal.js';

export function render(el, ctx) {
    const p = ctx.state.project;
    if (!p) { el.innerHTML = '<div class="wwm-empty">Create a phrase first.</div>'; return; }
    el.innerHTML = '<div class="wwm-loading">Looking at the devices…</div>';
    load(el, ctx, p);
    return { onJob: () => {} };
}

const pct = (v) => v == null ? '–' : Math.round(100 * v) + '%';
const brief = (r) => r ? `${esc(r.label || r.id)} · ${esc(r.recipe || '')} · threshold ${r.threshold}${r.recall != null ? ` · hears you ${pct(r.recall)} · ${r.near_fires} of ${r.n_near} sound-alikes${r.stale ? ' (older slice)' : ''}` : ''}${r.chosen ? '' : ' <span class="wwm-muted">(best by the judge; ★ one on Train to pick yourself)</span>'}` : '<span class="wwm-muted">none yet: train one</span>';

async function load(el, ctx, p) {
    let d;
    try { d = await api(`/projects/${p.slug}/install`); } catch (e) { el.innerHTML = `<div class="wwm-error">${esc(e.message)}</div>`; return; }
    const oww = d.picks.oww, mww = d.picks.mww, dk = d.desktop;
    const fileLink = (r, name, text) => r ? `<a class="wwm-btn ghost" href="/api/plugin/wakeword-maker/projects/${encodeURIComponent(p.slug)}/runs/${encodeURIComponent(r.id)}/file/${name}" download>${text}</a>` : '';
    el.innerHTML = `
      <div class="wwm-card">
        <h2>Install "${esc(d.phrase)}" ${qbtn('install')}</h2>
        <div class="wwm-muted">Desktop and Pi take the openWakeWord model; the ESP32 takes the microWakeWord one. Each is the ★ run from Train, or the judge's best when nothing is starred.</div>
        <table class="wwm-table" style="margin-top:8px"><tbody>
          <tr><td>desktop / Pi</td><td>${brief(oww)}</td></tr>
          <tr><td>ESP32</td><td>${brief(mww)}</td></tr>
        </tbody></table>
      </div>
      <div class="wwm-card">
        <h2>This computer ${qbtn('install_desktop')}</h2>
        <div>Listening for <b>${esc(dk.model || 'nothing')}</b>${dk.threshold != null ? ` at ${dk.threshold}` : ''}${dk.enabled ? '' : ' <span class="wwm-muted">(the wake word is switched off in Settings; the model applies when it is switched on)</span>'}${dk.ours ? ' <span class="wwm-pill ok">this one</span>' : ''}</div>
        <div class="wwm-row" style="margin-top:8px">
          <button class="wwm-btn primary" id="wwm-inst-desktop" ${oww ? '' : 'disabled'}>${dk.ours ? 'Install again (newer ★)' : `Listen for "${esc(d.phrase)}" here`}</button>
          ${dk.previous?.model ? `<button class="wwm-btn" id="wwm-inst-back">Put ${esc(dk.previous.model)} back</button>` : ''}
          <span class="wwm-muted">No restart. Say it once after, and watch the log line.</span>
        </div>
      </div>
      <div class="wwm-card">
        <h2>Satellites ${qbtn('install_satellite')}</h2>
        ${d.satellites.length ? `<table class="wwm-table"><thead><tr><th>Device</th><th>Where</th><th>Has</th><th></th></tr></thead><tbody>
          ${d.satellites.map(s => `<tr><td>${esc(s.id)}${s.online ? '' : ' <span class="wwm-muted">(offline)</span>'}</td><td>${esc(s.location || '')}</td><td>${s.installed ? `${esc(s.installed.run)} at ${s.installed.threshold}` : '<span class="wwm-muted">not sent yet</span>'}</td>
            <td><button class="wwm-btn" data-send="${esc(s.id)}" ${s.online && (s.format === 'tflite' ? mww : oww) ? '' : 'disabled'}>Send</button> <span class="wwm-muted" data-told="${esc(s.id)}"></span></td></tr>`).join('')}
          </tbody></table>` : '<div class="wwm-muted">No satellite with a wake word is set up in Devices.</div>'}
      </div>
      <div class="wwm-card">
        <h2>Files, to copy by hand</h2>
        <div class="wwm-row wwm-wrap">${fileLink(oww, 'model.onnx', `${esc(p.slug)}.onnx`)}${fileLink(oww, 'manifest', `${esc(p.slug)}.json (threshold)`)}${fileLink(mww, 'model.tflite', `${esc(p.slug)}.tflite`)}${fileLink(mww, 'model.json', `${esc(p.slug)}_manifest.json`)}</div>
        <div class="wwm-muted" style="margin-top:6px">A Pi body: the .onnx and its .json into its wakeword models folder, then tell it the name. An ESP32 without the model door: the .tflite and its manifest go in with the next flash.</div>
      </div>`;
    bindHelp(el);
    el.querySelector('#wwm-inst-desktop')?.addEventListener('click', () => showConfirm(`Swap this computer's wake word to "${d.phrase}" now? It keeps listening; the old model stays on disk.`, async () => {
        try { const r = await api(`/projects/${p.slug}/install/desktop`, { method: 'POST', body: {} }); ctx.toast(r.swapped ? `Listening for "${d.phrase}" at ${r.threshold}` : 'Installed; it applies when the wake word is switched on', 'success'); }
        catch (e) { ctx.toast(e.message, 'error'); }
        ctx.refresh();
    }, { title: 'Install on this computer', saveLabel: 'Install' }));
    el.querySelector('#wwm-inst-back')?.addEventListener('click', async () => {
        try { await api(`/projects/${p.slug}/install/desktop`, { method: 'DELETE' }); ctx.toast('Put back', 'success'); } catch (e) { ctx.toast(e.message, 'error'); }
        ctx.refresh();
    });
    el.querySelectorAll('[data-send]').forEach(b => b.addEventListener('click', async () => {
        const told = el.querySelector(`[data-told="${CSS.escape(b.dataset.send)}"]`);
        b.disabled = true; told.textContent = 'sending…';
        try { const r = await api(`/projects/${p.slug}/install/satellite`, { method: 'POST', body: { device: b.dataset.send } }); told.textContent = `sent ${r.sent} at ${r.threshold}`; ctx.toast(`${b.dataset.send} has it`, 'success'); setTimeout(() => load(el, ctx, p), 1200); }
        catch (e) { told.textContent = e.message; b.disabled = false; }
    }));
}
