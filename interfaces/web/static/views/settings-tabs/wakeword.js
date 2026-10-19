// settings-tabs/wakeword.js - Wake word detection settings, and the library: every phrase this Sapphire can
// listen for, as one row with a PC model (openWakeWord, .onnx) and a satellite model (microWakeWord, .tflite),
// who listens for it, uploads, and removal. Switching is not here: this computer picks from the dropdown above,
// a board picks on its Wake word tab in Devices. The doors are /api/wakeword/* (core/routes/wakeword.py,
// core/wakeword/library.py).
import { fetchWithTimeout } from '../../shared/fetch.js';
import { showToast } from '../../shared/toast.js';
import { escapeHtml as esc } from '../../shared/modal.js';

const API = '/api/wakeword';
const FAMILY = { onnx: 'openWakeWord', tflite: 'microWakeWord' };
const COLUMN = { onnx: 'PC', tflite: 'Satellite' };
const SOURCE = { user: '', core: 'ships with Sapphire', builtin: "openWakeWord's own" };

export default {
    id: 'wakeword',
    name: 'Wakeword',
    icon: '🎵',
    description: 'Wake word detection model and threshold',
    essentialKeys: ['WAKE_WORD_ENABLED', 'WAKEWORD_MODEL', 'WAKEWORD_THRESHOLD'],
    advancedKeys: ['WAKEWORD_FRAMEWORK', 'CHUNK_SIZE', 'BUFFER_DURATION', 'WAKE_TONE_DURATION', 'WAKE_TONE_FREQUENCY'],

    render(ctx) {
        return ctx.renderFields(this.essentialKeys) + `
            <div class="net-section" id="ww-library">
                <h4>Library</h4>
                <div class="setting-help" style="margin-bottom:8px">Every phrase Sapphire can listen for. A PC model is openWakeWord (.onnx), for this computer and a Pi;
                a satellite model is microWakeWord (.tflite), for an ESP32. This computer picks from the dropdown above; a board picks on its Wake word tab in Devices.</div>
                <div id="ww-lib-body" style="font-size:var(--font-sm)">Loading…</div>
                ${section('onnx', 'Add a PC wake word', 'openWakeWord: this computer, a Pi', '.onnx', 'Manifest (.json, optional)')}
                ${section('tflite', 'Add a satellite wake word', 'microWakeWord: an ESP32', '.tflite', 'Manifest (.json)')}
            </div>` + ctx.renderAccordion('wakeword-adv', this.advancedKeys);
    },

    async attachListeners(ctx, el) {
        const body = el.querySelector('#ww-lib-body');
        try { paint(body, await fetchWithTimeout(`${API}/library`), ctx); }
        catch (e) { body.innerHTML = `<span style="color:var(--error)">The library could not be read: ${esc(e.message)}</span>`; }
        for (const fam of Object.keys(FAMILY))
            el.querySelector(`#ww-add-${fam}`)?.addEventListener('click', () => add(ctx, el, fam));
    }
};

// One section per family: a heading, the model picker, the manifest picker, Add. The library name is the
// model file's name (hey_marcus.tflite -> hey_marcus); the spoken phrase comes from the manifest.
function section(fam, head, sub, modelExt, manifestLabel) {
    const pick = (id, label, accept) => `
        <label style="display:flex;flex-direction:column;gap:3px;font-size:var(--font-sm)">
            <span>${label}</span><input type="file" id="${id}" accept="${accept}" style="max-width:260px">
        </label>`;
    return `
        <div style="margin-top:14px">
            <div style="font-weight:600">${head} <span style="font-weight:normal;color:var(--text-muted)">${sub}</span></div>
            <div style="display:flex;gap:12px;flex-wrap:wrap;align-items:flex-end;margin-top:6px">
                ${pick(`ww-model-${fam}`, `Model (${modelExt})`, modelExt)}
                ${pick(`ww-manifest-${fam}`, manifestLabel, '.json')}
                <button class="btn-sm" id="ww-add-${fam}">Add</button>
            </div>
        </div>`;
}

async function add(ctx, el, fam) {
    const model = el.querySelector(`#ww-model-${fam}`).files[0];
    const manifest = el.querySelector(`#ww-manifest-${fam}`).files[0];
    if (!model) { showToast(`Pick the .${fam} file first`, 'error'); return; }
    if (fam === 'tflite' && !manifest) { showToast('A satellite model comes with its .json manifest: pick it too', 'error'); return; }
    const btn = el.querySelector(`#ww-add-${fam}`);
    btn.disabled = true;
    const send = async overwrite => {
        const fd = new FormData();
        fd.append('file', model);
        if (manifest) fd.append('manifest', manifest);
        fd.append('overwrite', overwrite ? 'true' : 'false');
        return fetchWithTimeout(`${API}/library`, { method: 'POST', body: fd });
    };
    try {
        let r;
        try { r = await send(false); }
        catch (e) {
            if (!/already has/.test(e.message)) throw e;
            if (!confirm(`${e.message} Replace it?`)) { btn.disabled = false; return; }
            r = await send(true);
        }
        showToast(`"${r.model.phrase}" (${COLUMN[fam]}) is in the library as ${r.model.name}`, 'success');
        await ctx.refreshTab();              // the dropdown above lists a new PC model too
        return;
    } catch (e) { showToast(e.message, 'error'); }
    btn.disabled = false;
}

function paint(body, d, ctx) {
    const who = d.listeners || [];
    const desktop = who.find(l => l.kind === 'desktop');
    const boards = who.filter(l => l.kind === 'device');
    // one row per phrase: its models by family
    const rows = new Map();
    for (const m of d.models || []) {
        if (!rows.has(m.name)) rows.set(m.name, { name: m.name, phrase: m.phrase || m.name.replace(/_/g, ' '), onnx: null, tflite: null });
        rows.get(m.name)[m.family] = m;
    }
    const cell = (m, fam, name) => {
        if (!m) return '<td style="text-align:center;color:var(--text-muted)">—</td>';
        if (m.ok === false)       // a file dropped into the folder by hand that fails the library's gates: listed, never offered
            return `<td style="text-align:center;white-space:nowrap" title="${esc(m.why || '')}"><span style="color:var(--error,#e53935)">unusable</span></td>`;
        // who hears it now: this computer (only while its detector really runs) and the boards of this family;
        // a board that has not said yet counts by what it was set to
        const hears = b => b.model === name || (b.model == null && b.wish === name);
        const notes = [];
        if (fam === 'onnx' && desktop && desktop.model === name && desktop.enabled && desktop.running !== false) notes.push('listening');
        const on = boards.filter(b => b.family === fam && hears(b)).map(b => b.label || b.id);
        if (on.length) notes.push(`on ${on.join(', ')}`);
        const title = [FAMILY[fam], SOURCE[m.source] || m.made_by || '', `threshold ${m.threshold}`].filter(Boolean).join(' · ');
        return `<td style="text-align:center;white-space:nowrap" title="${esc(title)}"><span style="color:var(--success,#22c55e)">✓</span>${
            notes.length ? ` <span style="color:var(--text-muted)">${esc(notes.join(' · '))}</span>` : ''}</td>`;
    };
    const order = [...rows.values()].sort((a, b) => {
        const own = r => (r.onnx?.source === 'user' || r.tflite?.source === 'user') ? 0 : (r.onnx?.source === 'core' || r.tflite?.source === 'core') ? 1 : 2;
        return own(a) - own(b) || a.phrase.localeCompare(b.phrase);
    });
    body.innerHTML = order.length ? `<div style="overflow-x:auto"><table style="border-collapse:collapse;width:100%">
        <thead><tr style="text-align:left"><th>Phrase</th><th style="text-align:center" title="openWakeWord (.onnx): this computer, and a Pi body">PC</th><th style="text-align:center" title="microWakeWord (.tflite): an ESP32">Satellite</th><th></th></tr></thead>
        <tbody>${order.map(r => `<tr>
            <td><b>${esc(r.phrase)}</b> <span style="color:var(--text-muted)">${esc(r.name)}</span></td>
            ${cell(r.onnx, 'onnx', r.name)}${cell(r.tflite, 'tflite', r.name)}
            <td>${(r.onnx?.source === 'user' || r.tflite?.source === 'user') ? `<button class="btn-sm danger" data-del="${esc(r.name)}" title="out of the library">×</button>` : ''}</td>
        </tr>`).join('')}</tbody></table></div>` : '<span class="setting-help">The library is empty.</span>';
    body.querySelectorAll('[data-del]').forEach(b => b.addEventListener('click', async () => {
        const r = rows.get(b.dataset.del);
        const fams = ['onnx', 'tflite'].filter(f => r[f]?.source === 'user');
        const users = who.filter(l => fams.includes(l.family) && l.model === r.name).map(l => l.label || l.id);
        const warn = users.length ? ` ${users.join(', ')} ${users.length === 1 ? 'is' : 'are'} listening for it now and keep${users.length === 1 ? 's' : ''} a copy until switched.` : '';
        if (!confirm(`Take "${r.phrase}" (${fams.map(f => COLUMN[f]).join(' and ')}) out of the library?${warn}`)) return;
        try {
            for (const f of fams) await fetchWithTimeout(`${API}/library/${f}/${encodeURIComponent(r.name)}`, { method: 'DELETE' });
            showToast(`"${r.phrase}" is out of the library`, 'success');
            await ctx.refreshTab();
        } catch (e) { showToast(e.message, 'error'); }
    }));
}
