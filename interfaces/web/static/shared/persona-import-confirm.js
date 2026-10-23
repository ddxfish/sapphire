// shared/persona-import-confirm.js — unified persona-import confirm + overwrite preview.
// Used by the personas page (JSON + PNG card) and the store. Before overwriting,
// shows which persona / prompt / (global) prompt pieces an import will replace.
import { listPersonas } from './persona-api.js';
import { getInitData } from './init-data.js';
import { getComponents } from './prompt-api.js';
import * as ui from '../ui.js';

const _esc = (s) => String(s ?? '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');

// Mirror persona_manager._sanitize_name for best-effort collision detection.
// The backend 409 is the real gate — this only drives the preview.
function _sanitize(name) {
    return (name || '').split('')
        .filter(c => /[a-z0-9 _-]/i.test(c)).join('')
        .trim().replace(/ /g, '_').toLowerCase();
}

const _pieceVal = (v) =>
    (typeof v === 'string') ? v
    : (v && typeof v === 'object') ? (v.content ?? JSON.stringify(v)) : '';

/** Extract the sapphire_persona bundle from a PNG card client-side (preview only). */
export async function extractBundleFromPng(file) {
    try {
        const buf = new Uint8Array(await file.arrayBuffer());
        const dv = new DataView(buf.buffer);
        let pos = 8; // skip the 8-byte PNG signature
        while (pos + 8 <= buf.length) {
            const len = dv.getUint32(pos); pos += 4;
            const type = String.fromCharCode(buf[pos], buf[pos + 1], buf[pos + 2], buf[pos + 3]); pos += 4;
            if (type === 'tEXt') {
                const seg = buf.subarray(pos, pos + len);
                const nul = seg.indexOf(0);
                if (nul > 0) {
                    let kw = '';
                    for (let i = 0; i < nul; i++) kw += String.fromCharCode(seg[i]);
                    if (kw === 'sapphire_persona') {
                        let b64 = '';
                        for (let i = nul + 1; i < seg.length; i++) b64 += String.fromCharCode(seg[i]);
                        return JSON.parse(atob(b64));
                    }
                }
            }
            if (type === 'IEND') break;
            pos += len + 4; // data + CRC
        }
    } catch (_) { /* not our PNG — preview just won't list pieces */ }
    return null;
}

async function _computeCollisions(bundle) {
    const rawName = bundle?.name || 'imported';
    const safe = _sanitize(rawName);
    const promptName = bundle?.prompt?.name || rawName;
    const [personasResp, init, localComps] = await Promise.all([
        listPersonas().catch(() => ({})),
        getInitData().catch(() => ({})),
        getComponents().catch(() => ({})),
    ]);
    const personaNames = (personasResp?.personas || []).map(p => p.name);
    const personaExists = personaNames.includes(safe);
    const promptExists = (init?.prompts?.list || []).some(p => p.name === promptName);
    const pieces = [];
    const incoming = bundle?.components || {};
    for (const [type, defs] of Object.entries(incoming)) {
        if (!defs || typeof defs !== 'object') continue;
        for (const [key, val] of Object.entries(defs)) {
            const localVal = localComps?.[type]?.[key];
            if (localVal === undefined) continue; // brand-new piece — nothing to clobber
            const oldStr = _pieceVal(localVal), newStr = _pieceVal(val);
            pieces.push({ type, key, changed: oldStr !== newStr, oldStr, newStr });
        }
    }
    return { rawName, promptName, personaExists, promptExists, pieces };
}

/**
 * Confirm + run a persona import. `doImport(flags)` performs the actual import
 * (caller wires it to importPersona / importPersonaCard / store install) and must
 * throw on failure. `bundle` is the parsed export bundle (drives the preview).
 */
export async function confirmPersonaImport({ bundle, doImport, onDone }) {
    let info;
    try { info = await _computeCollisions(bundle); }
    catch (_) {
        info = { rawName: bundle?.name, promptName: bundle?.prompt?.name, personaExists: false, promptExists: false, pieces: [] };
    }

    const collision = info.personaExists || info.promptExists || info.pieces.length;
    if (!collision) {
        // Optimistic import. If the backend finds a collision the preview missed
        // (name-sanitize edge), it 409s → fall back to the overwrite modal.
        try {
            await doImport({ overwrite_persona: false, overwrite_prompt: false, overwrite_avatar: false });
            onDone?.();
            ui.showToast(`Imported "${info.rawName || 'persona'}"`, 'success');
            return;
        } catch (e) {
            if (!/already exists/i.test(e.message || '')) {
                ui.showToast(`Import failed: ${e.message}`, 'error');
                return;
            }
            info.personaExists = true; // 409 → show overwrite modal
        }
    }
    _showOverwriteModal(info, doImport, onDone);
}

function _row(label, value, action) {
    return `<div class="pic-row"><span class="pic-row-label">${_esc(label)}</span>
        <span class="pic-row-value">${_esc(value)}</span>
        <span class="pic-row-action">${_esc(action)}</span></div>`;
}

function _showOverwriteModal(info, doImport, onDone) {
    const name = info.rawName || 'persona';
    const overlay = document.createElement('div');
    // .modal-overlay is display:none until .active (shared.css) — must add it.
    overlay.className = 'modal-overlay active pic-modal';

    const pieceList = (info.pieces || []).map((p, i) => `
        <div class="pic-piece ${p.changed ? 'pic-changed' : 'pic-identical'}">
            <div class="pic-piece-head">
                <input type="checkbox" class="pic-piece-cb" data-pic-key="${_esc(p.type)}/${_esc(p.key)}" checked
                    title="Checked = overwrite this piece. Unchecked = keep your local version.">
                <button class="pic-piece-toggle" data-pic-expand="${i}" ${p.changed ? '' : 'disabled'}>
                    <span class="pic-piece-name">${_esc(p.type)} / ${_esc(p.key)}</span>
                    <span class="pic-piece-badge">${p.changed ? 'changed ▸' : 'identical'}</span>
                </button>
            </div>
            ${p.changed ? `<div class="pic-diff" id="pic-diff-${i}" hidden>
                <div class="pic-diff-col"><div class="pic-diff-h">current (kept if unchecked)</div><pre>${_esc(p.oldStr)}</pre></div>
                <div class="pic-diff-col"><div class="pic-diff-h">incoming</div><pre>${_esc(p.newStr)}</pre></div>
            </div>` : ''}
        </div>`).join('');

    const pieceSection = (info.pieces || []).length
        ? `<div class="pic-section-label">${info.pieces.length} existing prompt piece${info.pieces.length === 1 ? '' : 's'} — pieces are shared (overwriting affects other prompts that use them). Uncheck any to keep your local copy:</div>${pieceList}`
        : '';

    overlay.innerHTML = `
    <div class="modal pic-dialog">
        <h2>Import "${_esc(name)}"</h2>
        <p class="pic-sub">You already have some of this. Overwriting replaces it:</p>
        ${info.personaExists ? _row('Persona', name, 'will replace') : ''}
        ${info.promptExists ? _row('Prompt', info.promptName || name, 'will replace') : ''}
        ${pieceSection}
        <label class="pic-avatar-opt"><input type="checkbox" id="pic-avatar"> Replace avatar too</label>
        <div class="modal-actions">
            <button class="store-btn store-btn-secondary" data-pic="cancel">Cancel</button>
            ${(info.promptExists || (info.pieces || []).length)
                ? `<button class="store-btn store-btn-secondary" data-pic="keep" title="Import the persona but keep your existing prompt + pieces">Import, Don't Overwrite</button>`
                : ''}
            <button class="store-btn store-btn-install" data-pic="overwrite">Overwrite &amp; Import</button>
        </div>
    </div>`;
    document.body.appendChild(overlay);

    const close = () => overlay.remove();
    overlay.addEventListener('click', e => { if (e.target === overlay) close(); });
    overlay.querySelector('[data-pic="cancel"]').addEventListener('click', close);

    overlay.querySelectorAll('[data-pic-expand]').forEach(btn => {
        btn.addEventListener('click', () => {
            const d = overlay.querySelector(`#pic-diff-${btn.dataset.picExpand}`);
            if (d) d.hidden = !d.hidden;
        });
    });

    // Both import buttons share this — they differ only in overwrite_prompt
    // (true = replace prompt + pieces, false = keep the existing local ones).
    const run = async (btn, overwrite_prompt) => {
        const orig = btn.textContent;
        const btns = overlay.querySelectorAll('.modal-actions button');
        btns.forEach(b => b.disabled = true);
        btn.textContent = 'Importing...';
        const overwrite_avatar = overlay.querySelector('#pic-avatar')?.checked || false;
        // Unchecked pieces → keep local (only meaningful when overwriting prompt).
        const keep_components = overwrite_prompt
            ? Array.from(overlay.querySelectorAll('.pic-piece-cb')).filter(cb => !cb.checked).map(cb => cb.dataset.picKey)
            : [];
        try {
            await doImport({ overwrite_persona: true, overwrite_prompt, overwrite_avatar, keep_components });
            close();
            onDone?.();
            ui.showToast(`Imported "${name}"`, 'success');
        } catch (e) {
            ui.showToast(`Import failed: ${e.message}`, 'error');
            btns.forEach(b => b.disabled = false);
            btn.textContent = orig;
        }
    };
    overlay.querySelector('[data-pic="overwrite"]').addEventListener('click', e => run(e.currentTarget, true));
    overlay.querySelector('[data-pic="keep"]')?.addEventListener('click', e => run(e.currentTarget, false));
}
