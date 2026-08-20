// Game Settings — large modal for ONE game; tabs come from the engine's
// SETTINGS schema ('tab' per field — e.g. poker: Rules / Start). All tabs
// render at once (hidden/shown), one Save writes everything, Reset restores
// schema defaults into the form. A new game ships settings by exporting
// SETTINGS — zero UI code here.
//
// Open-mansion v1 (2026-08-20, plan tmp/open-mansion-plan.md): ONE modal
// family for every story-editing verb (Krem's ruling — no second kind of
// modal). Stories compose tabs from three sources:
//   Setup (this run)  — Mad-Libs slot values, per-playthrough (story/slots)
//   This story / GM   — the existing settings schema (storycfg)
//   Objects           — custom pane: placed objects + room-text overrides
// Fresh launch uses openStorySetup(): ONLY the Setup tab, Start Story button.

import { setupModalClose } from '/static/shared/modal.js';
import * as ui from '/static/ui.js';

const PLUGIN_API = '/api/plugin/game-room/';

function csrf() {
    return document.querySelector('meta[name="csrf-token"]')?.content || '';
}

async function api(path, method, body) {
    const res = await fetch(PLUGIN_API + path, {
        method: method || 'GET',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf() },
        body: body ? JSON.stringify(body) : undefined,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || ('HTTP ' + res.status));
    return data;
}

function esc(s) {
    return String(s == null ? '' : s)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function fieldHtml(f, val) {
    const v = val ?? f.default ?? '';
    switch (f.type) {
        case 'checkbox':
            return `<div class="sb-field">
                <label class="st-tools-check" style="margin:0">
                    <input type="checkbox" class="grs-field" data-key="${esc(f.key)}"${(v === true || v === 'true') ? ' checked' : ''}>
                    ${esc(f.label)}
                </label>
            </div>`;
        case 'text':
            return `<div class="sb-field sb-field-stack">
                <label>${esc(f.label)}</label>
                <textarea class="grs-field" data-key="${esc(f.key)}" rows="${f.rows || 4}">${esc(v)}</textarea>
            </div>`;
        case 'range':
            return `<div class="sb-field sb-field-stack">
                <label>${esc(f.label)}: <span class="grs-val">${esc(v)}</span></label>
                <input type="range" class="grs-field" data-key="${esc(f.key)}"
                       min="${f.min ?? 0}" max="${f.max ?? 1}" step="${f.step ?? 0.05}" value="${esc(v)}">
            </div>`;
        case 'number':
            return `<div class="sb-field">
                <label>${esc(f.label)}</label>
                <input type="number" class="grs-field" data-key="${esc(f.key)}"
                       min="${f.min ?? ''}" max="${f.max ?? ''}" step="${f.step ?? 1}" value="${esc(v)}">
            </div>`;
        case 'select': {
            const options = f.options || [];
            const isCustom = !!f.allow_custom && v !== '' && !options.includes(v);
            return `<div class="sb-field sb-field-stack">
                <label>${esc(f.label)}</label>
                <select class="grs-field grs-select" data-key="${esc(f.key)}">
                    ${options.map(o => `<option value="${esc(o)}"${String(v) === String(o) ? ' selected' : ''}>${esc(o)}</option>`).join('')}
                    ${f.allow_custom ? `<option value="__custom"${isCustom ? ' selected' : ''}>Other…</option>` : ''}
                </select>
                ${f.allow_custom ? `<input type="text" class="grs-custom" data-for="${esc(f.key)}"
                    placeholder="your own…" value="${isCustom ? esc(v) : ''}"
                    style="display:${isCustom ? '' : 'none'};margin-top:4px">` : ''}
            </div>`;
        }
        default:
            return `<div class="sb-field">
                <label>${esc(f.label)}</label>
                <input type="text" class="grs-field" data-key="${esc(f.key)}" value="${esc(v)}">
            </div>`;
    }
}

// Wire select-with-Other toggling inside `scope`.
function wireSelects(scope) {
    scope.querySelectorAll('select.grs-select').forEach(sel => {
        sel.addEventListener('change', () => {
            const custom = scope.querySelector(`.grs-custom[data-for="${sel.dataset.key}"]`);
            if (!custom) return;
            custom.style.display = sel.value === '__custom' ? '' : 'none';
            if (sel.value === '__custom') custom.focus();
        });
    });
}

function readField(scope, key) {
    const el = scope.querySelector(`.grs-field[data-key="${CSS.escape(key)}"]`);
    if (!el) return undefined;
    if (el.type === 'checkbox') return el.checked;
    if (el.tagName === 'SELECT' && el.value === '__custom') {
        const custom = scope.querySelector(`.grs-custom[data-for="${CSS.escape(key)}"]`);
        return (custom?.value || '').trim();
    }
    return el.value;
}

function writeField(scope, key, value) {
    const el = scope.querySelector(`.grs-field[data-key="${CSS.escape(key)}"]`);
    if (!el) return;
    if (el.type === 'checkbox') { el.checked = !!value; return; }
    if (el.tagName === 'SELECT') {
        const has = [...el.options].some(o => o.value === String(value));
        const custom = scope.querySelector(`.grs-custom[data-for="${CSS.escape(key)}"]`);
        if (has) {
            el.value = String(value);
            if (custom) custom.style.display = 'none';
        } else if (custom) {
            el.value = '__custom';
            custom.value = String(value ?? '');
            custom.style.display = '';
        }
        return;
    }
    el.value = value ?? '';
    el.dispatchEvent(new Event('input'));
}

// Slot declaration → a schema-ish field for fieldHtml.
function slotField(s, includeSealed) {
    if (s.sealed) {
        if (!includeSealed) return null;
        return { key: s.key, label: `\u{1F512} ${s.label} — sealed: she won't see this until it's found in play`,
                 type: 'text', rows: 2, default: '', _sealed: true, _seal_key: s.seal_key };
    }
    if ((s.options || []).length) {
        return { key: s.key, label: s.label, type: 'select', options: s.options,
                 allow_custom: s.allow_custom !== false, default: s.default };
    }
    return { key: s.key, label: s.label, type: undefined, default: s.default };
}

// ── shared modal chrome ─────────────────────────────────────────────────────
// tabs: [{title, html, init(pane, overlay)}]; actionsHtml renders in the
// footer; returns {overlay, close}.
function buildModal(title, tabs, actionsHtml) {
    const overlay = document.createElement('div');
    overlay.className = 'pr-modal-overlay';
    overlay.innerHTML = `
        <div class="pr-modal grs-modal">
            <div class="pr-modal-header">
                <h3>${title}</h3>
                <button type="button" class="sb-icon-btn grs-close" title="Close">✕</button>
            </div>
            ${tabs.length > 1 ? `<div class="sb-mode-tabs grs-tabs">
                ${tabs.map((t, i) => `<button class="sb-mode-tab grs-tab${i === 0 ? ' active' : ''}" data-tab="${esc(t.title)}">${esc(t.title)}</button>`).join('')}
            </div>` : ''}
            <div class="pr-modal-body grs-body">
                ${tabs.map((t, i) => `<div class="grs-pane" data-tab="${esc(t.title)}" style="display:${i === 0 ? 'block' : 'none'}">${t.html}</div>`).join('')}
                <div class="grs-actions">${actionsHtml}</div>
            </div>
        </div>`;
    document.body.appendChild(overlay);
    const close = () => overlay.remove();
    setupModalClose(overlay, close);
    overlay.querySelector('.grs-close').onclick = close;
    overlay.querySelectorAll('.grs-tab').forEach(t => {
        t.onclick = () => {
            overlay.querySelectorAll('.grs-tab').forEach(x => x.classList.toggle('active', x === t));
            overlay.querySelectorAll('.grs-pane').forEach(p =>
                p.style.display = p.dataset.tab === t.dataset.tab ? 'block' : 'none');
        };
    });
    overlay.querySelectorAll('input[type="range"].grs-field').forEach(r => {
        r.addEventListener('input', () => {
            const lbl = r.closest('.sb-field')?.querySelector('.grs-val');
            if (lbl) lbl.textContent = r.value;
        });
    });
    for (const t of tabs) {
        const pane = overlay.querySelector(`.grs-pane[data-tab="${CSS.escape(t.title)}"]`);
        wireSelects(pane);
        if (t.init) t.init(pane, overlay);
    }
    return { overlay, close };
}

export async function openGameSettings(gameId) {
    if (!gameId) return;
    return openFromPath(`play/${gameId}/settings`, gameId);
}

// ── Story settings (the ⚙ gear) ─────────────────────────────────────────────
// opts.session + opts.active → the in-game gear: Setup-this-run + Objects
// tabs join the schema tabs. Library gear (no session) = schema tabs only,
// exactly as before.
export async function openStorySettings(slug, opts = {}) {
    if (!slug) return;
    let data, setup = null, objData = null;
    try {
        data = await api(`story/${encodeURIComponent(slug)}/settings`);
        if (opts.session && opts.active) {
            setup = await api(`story/${encodeURIComponent(slug)}/setup`);
            objData = await api(`story/objects?session=${encodeURIComponent(opts.session)}`);
            if (!objData.active) objData = null;
        }
    } catch (e) {
        ui.showToast('Could not load settings: ' + e.message, 'error');
        return;
    }
    const schema = data.schema || [];
    const tabs = [];

    // Setup (this run) — editable slot values, presets. Sealed blanks are
    // edited from the scene strip's ✍ chips, not here.
    const slots = (setup?.slots || []).filter(s => !s.sealed);
    let setupPane = null;
    if (setup && slots.length) {
        const presetNames = Object.keys(setup.presets || {});
        tabs.push({
            title: 'Setup (this run)',
            html: `
                <div class="sb-field">
                    <label>Preset</label>
                    <select class="grs-setup-preset">
                        <option value="">— current values —</option>
                        ${presetNames.map(n => `<option value="${esc(n)}">${esc(n)}</option>`).join('')}
                    </select>
                    <button type="button" class="pk-btn grs-preset-save" style="margin-top:4px">Save as preset…</button>
                </div>
                <hr style="opacity:.25">
                ${slots.map(s => fieldHtml(slotField(s, false), (opts.slots || {})[s.key])).join('')}
                <div style="opacity:.7;font-size:.85em">Changes land on her next turn after Save. Sealed blanks are edited from the ✍ chips in the scene panel.</div>`,
            init(pane) {
                setupPane = pane;
                pane.querySelector('.grs-setup-preset').onchange = (e) => {
                    const p = (setup.presets || {})[e.target.value];
                    if (!p) return;
                    for (const s of slots) writeField(pane, s.key, (p.slots || {})[s.key] ?? s.default);
                };
                pane.querySelector('.grs-preset-save').onclick = async () => {
                    const name = prompt('Preset name:');
                    if (!name) return;
                    const vals = {};
                    for (const s of slots) vals[s.key] = readField(pane, s.key) ?? '';
                    try {
                        await api(`story/${encodeURIComponent(slug)}/presets`, 'POST', { name, slots: vals });
                        ui.showToast(`Preset '${name}' saved`, 'success', 2000);
                    } catch (e2) { ui.showToast(e2.message, 'error'); }
                };
            }
        });
    }

    // Schema tabs (This story / GM Style) — unchanged behavior.
    const byTab = {};
    const schemaTabs = [];
    for (const f of schema) {
        const tab = f.tab || 'Settings';
        if (!byTab[tab]) { byTab[tab] = []; schemaTabs.push(tab); }
        byTab[tab].push(f);
    }
    for (const t of schemaTabs) {
        tabs.push({ title: t,
                    html: byTab[t].map(f => fieldHtml(f, data.settings?.[f.key])).join('') });
    }

    // Objects — the open-world pane (active playthrough only).
    if (objData) tabs.push(objectsTab(slug, opts.session, objData));

    const { overlay, close } = buildModal(
        `&#x2699;&#xFE0E; ${esc(data.title || slug)} settings`, tabs,
        `<button type="button" class="pk-btn pk-btn-primary grs-save">Save</button>
         <button type="button" class="pk-btn grs-reset" title="Restore defaults into the form (Save to apply)">Reset to defaults</button>`);

    overlay.querySelector('.grs-save').onclick = async () => {
        const out = {};
        for (const f of schema) {
            const v = readField(overlay, f.key);
            if (v !== undefined) out[f.key] = v;
        }
        try {
            await api(`story/${encodeURIComponent(slug)}/settings`, 'POST', { settings: out });
            if (setupPane) {
                const vals = {};
                for (const s of slots) vals[s.key] = readField(setupPane, s.key) ?? '';
                await api('story/slots', 'POST', { session: opts.session, slots: vals });
            }
            ui.showToast('Settings saved — live on the next turn', 'success', 2500);
            close();
        } catch (e) { ui.showToast(e.message, 'error'); }
    };
    overlay.querySelector('.grs-reset').onclick = () => {
        for (const f of schema) writeField(overlay, f.key, f.default ?? (f.type === 'checkbox' ? false : ''));
        if (setupPane) for (const s of slots) writeField(setupPane, s.key, s.default);
    };
}

// ── Fresh-launch setup (Mad-Libs) ───────────────────────────────────────────
// ONLY the Setup tab + Start Story. Resolves {slots, sealed, objset} on
// Start, null on cancel. Returns undefined-equivalent {} start when the
// story declares nothing (callers should check needsSetup first).
export async function storyNeedsSetup(slug) {
    try {
        const s = await api(`story/${encodeURIComponent(slug)}/setup`);
        return (s.slots || []).length || Object.keys(s.presets || {}).length
            || (s.objsets || []).length ? s : null;
    } catch { return null; }
}

export function openStorySetup(slug, setup) {
    return new Promise((resolve) => {
        const slots = setup.slots || [];
        const open = slots.filter(s => !s.sealed);
        const sealed = slots.filter(s => s.sealed && s.seal_key);
        const presetNames = Object.keys(setup.presets || {});
        let done = false;
        const finish = (v) => { if (!done) { done = true; resolve(v); } };

        const { overlay, close } = buildModal(
            `\u{1F4D6} ${esc(setup.title || slug)} — set the stage`,
            [{
                title: 'Setup',
                html: `
                    ${presetNames.length ? `<div class="sb-field">
                        <label>Start from</label>
                        <select class="grs-setup-preset">
                            <option value="">— the default story —</option>
                            ${presetNames.map(n => `<option value="${esc(n)}">${esc(n)}</option>`).join('')}
                        </select>
                    </div>` : ''}
                    ${open.map(s => fieldHtml(slotField(s, false), undefined)).join('')}
                    ${sealed.map(s => fieldHtml(slotField(s, true), undefined)).join('')}
                    ${(setup.objsets || []).length ? `<div class="sb-field">
                        <label>Stock the house</label>
                        <select class="grs-setup-objset">
                            <option value="">— nothing extra —</option>
                            ${setup.objsets.map(n => `<option value="${esc(n)}">${esc(n)}</option>`).join('')}
                        </select>
                    </div>` : ''}
                    <div style="opacity:.7;font-size:.85em">Your words become the story. 🔒 fields are sealed — she discovers them in play, never up front.</div>`,
                init(pane) {
                    const presetSel = pane.querySelector('.grs-setup-preset');
                    if (presetSel) presetSel.onchange = (e) => {
                        const p = (setup.presets || {})[e.target.value];
                        for (const s of open) writeField(pane, s.key, p ? ((p.slots || {})[s.key] ?? s.default) : s.default);
                    };
                }
            }],
            `<button type="button" class="pk-btn pk-btn-primary grs-start">▶ Start Story</button>
             <button type="button" class="pk-btn grs-cancel">Cancel</button>`);

        overlay.addEventListener('click', (e) => {
            if (e.target === overlay) finish(null);
        });
        overlay.querySelector('.grs-close').addEventListener('click', () => finish(null));
        overlay.querySelector('.grs-cancel').onclick = () => { close(); finish(null); };
        overlay.querySelector('.grs-start').onclick = () => {
            const vals = {};
            for (const s of open) {
                const v = readField(overlay, s.key);
                if (v !== undefined && String(v).trim()) vals[s.key] = String(v).trim();
            }
            const sealedFills = sealed
                .map(s => ({ key: s.seal_key, text: String(readField(overlay, s.key) || '').trim() }))
                .filter(f => f.text);
            const objset = overlay.querySelector('.grs-setup-objset')?.value || '';
            close();
            finish({ slots: vals, sealed: sealedFills, objset });
        };
    });
}

// ── Objects tab (the open-world editor) ─────────────────────────────────────
function objectsTab(slug, session, data) {
    const rooms = data.rooms || [];
    const openFlag = data.open_flag || '';
    const authorOf = (spec) => spec?._author === 'ai' ? ' \u{1F916}' : '';
    const html = `
        <div class="sb-field">
            <label>Room</label>
            <select class="grs-obj-room">
                ${rooms.map(r => `<option value="${r.id}">${esc(r.title)} (${r.id})</option>`).join('')}
            </select>
        </div>
        <div class="sb-field sb-field-stack">
            <label>What she reads — the room's reality (<span class="grs-obj-count">0</span>/900)</label>
            <textarea class="grs-obj-template" rows="4"></textarea>
            <label>What you see — scene strip</label>
            <textarea class="grs-obj-pdesc" rows="2"></textarea>
            <button type="button" class="pk-btn grs-obj-savetext" style="align-self:flex-start">Save room text</button>
            ${openFlag ? `<div style="opacity:.7;font-size:.85em">Room text changes appear when the house opens (<code>${esc(openFlag)}</code>).</div>` : ''}
        </div>
        <hr style="opacity:.25">
        <div class="sb-field sb-field-stack">
            <label>Placed objects in this room</label>
            <div class="grs-obj-list" style="display:flex;flex-direction:column;gap:4px"></div>
        </div>
        <div class="sb-field sb-field-stack" style="border:1px solid rgba(128,128,128,.3);border-radius:6px;padding:8px">
            <label>+ Add object</label>
            <input type="text" class="grs-obj-name" placeholder="name, e.g. rare_pepes">
            <input type="text" class="grs-obj-desc" placeholder="what one sees looking at it">
            <input type="text" class="grs-obj-verb" placeholder="custom verb (optional), e.g. look_inside">
            <textarea class="grs-obj-resp" rows="2" placeholder="what that verb returns, e.g. You see a paper..."></textarea>
            <label class="st-tools-check" style="margin:0"><input type="checkbox" class="grs-obj-hidden"> Hidden — found only by searching</label>
            ${openFlag ? `<label class="st-tools-check" style="margin:0"><input type="checkbox" class="grs-obj-atopen"> Appears when the house opens</label>` : ''}
            <button type="button" class="pk-btn pk-btn-primary grs-obj-place" style="align-self:flex-start">Place</button>
        </div>
        <hr style="opacity:.25">
        <div class="sb-field" style="display:flex;gap:6px;flex-wrap:wrap;align-items:center">
            <button type="button" class="pk-btn grs-set-save">Save objects as set…</button>
            <select class="grs-set-pick"><option value="">— load a set —</option></select>
            <button type="button" class="pk-btn grs-set-load">Load</button>
        </div>`;

    return { title: 'Objects', html, init(pane) {
        let world = data;
        const roomSel = pane.querySelector('.grs-obj-room');
        const tArea = pane.querySelector('.grs-obj-template');
        const pArea = pane.querySelector('.grs-obj-pdesc');
        const count = pane.querySelector('.grs-obj-count');
        const curRoom = () => world.rooms.find(r => String(r.id) === roomSel.value) || world.rooms[0];

        const paintRoom = () => {
            const r = curRoom();
            if (!r) return;
            tArea.value = r.template || r.shipped_template || '';
            pArea.value = r.player_desc || r.shipped_player_desc || '';
            count.textContent = tArea.value.length;
            const objs = (world.objects || {})[String(r.id)] || {};
            const list = pane.querySelector('.grs-obj-list');
            list.innerHTML = Object.keys(objs).length
                ? Object.entries(objs).map(([n, spec]) =>
                    `<div style="display:flex;gap:6px;align-items:center">
                        <span>${esc(n)}${authorOf(spec)} — <span style="opacity:.7">${esc(spec.desc || '')}</span></span>
                        <button type="button" class="sb-icon-btn grs-obj-del" data-name="${esc(n)}" title="Remove">✕</button>
                    </div>`).join('')
                : '<div style="opacity:.6">nothing placed here yet</div>';
            list.querySelectorAll('.grs-obj-del').forEach(b => b.onclick = async () => {
                try {
                    await api('story/objects/delete', 'POST',
                              { session, room_id: r.id, name: b.dataset.name });
                    await refresh();
                } catch (e) { ui.showToast(e.message, 'error'); }
            });
        };

        const refresh = async () => {
            try {
                const d = await api(`story/objects?session=${encodeURIComponent(session)}`);
                if (d.active) { world = d; paintRoom(); }
            } catch { /* pane keeps last state */ }
        };

        tArea.addEventListener('input', () => { count.textContent = tArea.value.length; });
        roomSel.onchange = paintRoom;
        paintRoom();

        pane.querySelector('.grs-obj-savetext').onclick = async () => {
            const r = curRoom();
            try {
                await api('story/room-text', 'POST', {
                    session, room_id: r.id,
                    template: tArea.value, player_desc: pArea.value });
                ui.showToast('Room text saved', 'success', 2000);
                await refresh();
            } catch (e) { ui.showToast(e.message, 'error'); }
        };

        pane.querySelector('.grs-obj-place').onclick = async () => {
            const r = curRoom();
            const name = pane.querySelector('.grs-obj-name').value.trim();
            if (!name) { ui.showToast('Object needs a name', 'error'); return; }
            const spec = { desc: pane.querySelector('.grs-obj-desc').value.trim() };
            if (pane.querySelector('.grs-obj-hidden').checked) spec.hidden = true;
            if (pane.querySelector('.grs-obj-atopen')?.checked && openFlag)
                spec.condition = { flag: openFlag };
            const verb = pane.querySelector('.grs-obj-verb').value.trim();
            const resp = pane.querySelector('.grs-obj-resp').value.trim();
            if (verb) spec.interactions = { [verb]: { message: resp || `You ${verb} the ${name}.` } };
            try {
                const res = await api('story/objects', 'POST',
                                      { session, room_id: r.id, name, spec });
                if (!res.success) { ui.showToast(res.detail || 'refused', 'error'); return; }
                ['name', 'desc', 'verb'].forEach(k => pane.querySelector(`.grs-obj-${k}`).value = '');
                pane.querySelector('.grs-obj-resp').value = '';
                ui.showToast(`'${name}' placed`, 'success', 2000);
                await refresh();
            } catch (e) { ui.showToast(e.message, 'error'); }
        };

        const setPick = pane.querySelector('.grs-set-pick');
        const loadSets = async () => {
            try {
                const d = await api(`story/${encodeURIComponent(slug)}/objsets`);
                const names = Object.keys(d.objsets || {});
                setPick.innerHTML = '<option value="">— load a set —</option>'
                    + names.map(n => `<option value="${esc(n)}">${esc(n)}</option>`).join('');
            } catch { /* leave as-is */ }
        };
        loadSets();
        pane.querySelector('.grs-set-save').onclick = async () => {
            const name = prompt('Object set name:');
            if (!name) return;
            try {
                const res = await api(`story/${encodeURIComponent(slug)}/objsets`, 'POST',
                                      { name, session });
                if (!res.success) { ui.showToast(res.detail || 'refused', 'error'); return; }
                ui.showToast(`Set '${name}' saved (your objects only)`, 'success', 2500);
                await loadSets();
            } catch (e) { ui.showToast(e.message, 'error'); }
        };
        pane.querySelector('.grs-set-load').onclick = async () => {
            if (!setPick.value) return;
            try {
                const res = await api('story/objsets/load', 'POST',
                                      { session, name: setPick.value });
                ui.showToast(res.detail || (res.success ? 'loaded' : 'refused'),
                             res.success ? 'success' : 'error', 2500);
                await refresh();
            } catch (e) { ui.showToast(e.message, 'error'); }
        };
    } };
}

// ── generic schema modal (games) ────────────────────────────────────────────
async function openFromPath(path, fallbackTitle) {
    let data;
    try {
        data = await api(path);
    } catch (e) {
        ui.showToast('Could not load settings: ' + e.message, 'error');
        return;
    }
    const schema = data.schema || [];
    if (!schema.length) { ui.showToast('No settings here.', 'error'); return; }
    const savePath = path;

    const byTab = {};
    const tabNames = [];
    for (const f of schema) {
        const tab = f.tab || 'Settings';
        if (!byTab[tab]) { byTab[tab] = []; tabNames.push(tab); }
        byTab[tab].push(f);
    }
    const tabs = tabNames.map(t => ({
        title: t, html: byTab[t].map(f => fieldHtml(f, data.settings?.[f.key])).join('') }));

    const { overlay, close } = buildModal(
        `&#x2699;&#xFE0E; ${esc(data.title || fallbackTitle)} settings`, tabs,
        `<button type="button" class="pk-btn pk-btn-primary grs-save">Save</button>
         <button type="button" class="pk-btn grs-reset" title="Restore defaults into the form (Save to apply)">Reset to defaults</button>`);

    overlay.querySelector('.grs-save').onclick = async () => {
        const out = {};
        for (const f of schema) {
            const v = readField(overlay, f.key);
            if (v !== undefined) out[f.key] = v;
        }
        try {
            await api(savePath, 'POST', { settings: out });
            ui.showToast('Settings saved — live on the next turn', 'success', 2500);
            close();
        } catch (e) { ui.showToast(e.message, 'error'); }
    };
    overlay.querySelector('.grs-reset').onclick = () => {
        for (const f of schema) writeField(overlay, f.key, f.default ?? (f.type === 'checkbox' ? false : ''));
    };
}
