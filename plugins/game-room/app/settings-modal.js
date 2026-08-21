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

// Empty label = deliberate (the section header carries it) — render a
// spacer so side-by-side fields in a slot row stay top-aligned.
function labelHtml(f) {
    return f.label ? `<label>${esc(f.label)}</label>` : '<label>&nbsp;</label>';
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
                ${labelHtml(f)}
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
                ${labelHtml(f)}
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
            return `<div class="sb-field sb-field-stack">
                ${labelHtml(f)}
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

// Slot declaration → a schema-ish field for fieldHtml. `_section`/`_width`
// are layout hints slotsHtml() consumes (see below).
function slotField(s, includeSealed) {
    const lay = { _section: s.section || '', _width: s.width || 100 };
    if (s.sealed) {
        if (!includeSealed) return null;
        return { key: s.key, label: `\u{1F512} ${s.label || s.key} — sealed: she won't see this until it's found in play`,
                 type: 'text', rows: 2, default: '', _sealed: true, _seal_key: s.seal_key, ...lay, _width: 100 };
    }
    if ((s.options || []).length) {
        return { key: s.key, label: s.label, type: 'select', options: s.options,
                 allow_custom: s.allow_custom !== false, default: s.default, ...lay };
    }
    if (s.rows) {   // long-text slot (scenario, goals) → textarea
        return { key: s.key, label: s.label, type: 'text', rows: s.rows, default: s.default, ...lay };
    }
    return { key: s.key, label: s.label, type: undefined, default: s.default, ...lay };
}

// Fields → sectioned, responsive rows. Consecutive fields sharing _section
// render under one header; _width (% of the row) lets a 20% name sit beside
// its 80% backstory. Rows flex-wrap, so narrow screens stack naturally.
function slotsHtml(fields, valueOf) {
    let html = '', open = false, cur = null;
    for (const f of fields) {
        const sec = f._section || '';
        if (sec !== cur) {
            if (open) html += '</div>';
            cur = sec; open = true;
            html += (sec ? `<div class="grs-section-title">${esc(sec)}</div>` : '')
                  + '<div class="grs-slot-row">';
        }
        html += `<div class="grs-slot" style="--w:${f._width || 100}%">${fieldHtml(f, valueOf(f))}</div>`;
    }
    return html + (open ? '</div>' : '');
}

// Preset section: "Preset" heading, then ONE line — [dropdown][💾 Save]
// [🗑 Delete] (Krem 2026-08-20). Save swaps in an inline name input
// (prefilled with the selection — keep the name to overwrite, change it to
// fork a new one). Delete is two-click armed. Shared by the fresh-launch
// setup form and the in-game gear's Story tab.
function presetRowHtml(names, placeholder) {
    return `<div class="grs-section-title" style="margin-top:0">Preset</div>
    <div class="grs-preset-row">
        <select class="grs-setup-preset">
            <option value="">${esc(placeholder)}</option>
            ${names.map(n => `<option value="${esc(n)}">${esc(n)}</option>`).join('')}
        </select>
        <button type="button" class="pk-btn grs-preset-save" title="Save the form below as a named preset for this story">\u{1F4BE} Save</button>
        <button type="button" class="pk-btn grs-preset-del" title="Delete the selected preset">\u{1F5D1}\u{FE0E} Delete</button>
        <span class="grs-preset-namer" style="display:none">
            <input type="text" class="grs-preset-name" placeholder="preset name" maxlength="60">
            <button type="button" class="pk-btn pk-btn-primary grs-preset-ok">✓ Save</button>
            <button type="button" class="pk-btn grs-preset-no">✕</button>
        </span>
    </div>`;
}

// The namer span (shared by preset + object-set rows): 💾 swaps in an
// inline name input prefilled from the dropdown; ✓/Enter commits via
// onSave(name) — return false to keep it open (refusal); ✕ cancels.
function wireNamer(row, saveBtn, getPrefill, onSave) {
    const namer = row.querySelector('.grs-preset-namer');
    const nameIn = row.querySelector('.grs-preset-name');
    saveBtn.onclick = () => {
        nameIn.value = getPrefill() || '';
        namer.style.display = '';
        nameIn.focus();
    };
    row.querySelector('.grs-preset-no').onclick = () => { namer.style.display = 'none'; };
    const commit = async () => {
        const name = nameIn.value.trim();
        if (!name) return;
        if (await onSave(name) !== false) namer.style.display = 'none';
    };
    row.querySelector('.grs-preset-ok').onclick = commit;
    nameIn.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') { e.preventDefault(); commit(); }
    });
}

// Two-click armed delete: first click asks "Sure?" for 3s. guard() can
// refuse (nothing selected) before the button ever arms.
function armDelete(btn, label, guard, onDelete) {
    let armed = 0;
    btn.onclick = async () => {
        if (guard && !guard()) return;
        if (Date.now() - armed > 3000) {
            armed = Date.now();
            btn.textContent = 'Sure?';
            setTimeout(() => { btn.textContent = label; }, 3000);
            return;
        }
        armed = 0;
        btn.textContent = label;
        await onDelete();
    };
}

// baseVals = what the blank option restores (defaults on the setup form,
// the run's current values on the gear).
function wirePresetRow(pane, slug, setup, slotList, baseVals) {
    const row = pane.querySelector('.grs-preset-row');
    const sel = pane.querySelector('.grs-setup-preset');
    sel.onchange = () => {
        const p = (setup.presets || {})[sel.value];
        const base = p ? (p.slots || {}) : (baseVals || {});
        for (const s of slotList) writeField(pane, s.key, base[s.key] ?? s.default);
    };
    wireNamer(row, pane.querySelector('.grs-preset-save'), () => sel.value, async (name) => {
        const vals = {};
        for (const s of slotList) vals[s.key] = readField(pane, s.key) ?? '';
        try {
            const r = await api(`story/${encodeURIComponent(slug)}/presets`, 'POST', { name, slots: vals });
            if (!r.success) { ui.showToast(r.detail || 'refused', 'error'); return false; }
            if (![...sel.options].some(o => o.value === name)) {
                const opt = document.createElement('option');
                opt.value = opt.textContent = name;
                sel.appendChild(opt);
            }
            setup.presets = setup.presets || {};
            setup.presets[name] = { slots: vals };
            sel.value = name;
            ui.showToast(`Preset '${name}' saved`, 'success', 2000);
        } catch (e) { ui.showToast(e.message, 'error'); return false; }
    });
    armDelete(pane.querySelector('.grs-preset-del'), '\u{1F5D1}\u{FE0E} Delete',
        () => {
            if (!sel.value) { ui.showToast('Pick a preset to delete first', 'error', 2000); return false; }
            return true;
        },
        async () => {
            const name = sel.value;
            try {
                const r = await api(`story/${encodeURIComponent(slug)}/presets`, 'POST', { name, delete: true });
                if (!r.success) { ui.showToast(r.detail || 'refused', 'error'); return; }
                delete (setup.presets || {})[name];
                [...sel.options].find(o => o.value === name)?.remove();
                sel.value = '';   // form keeps its values — only the saved copy dies
                ui.showToast(`Preset '${name}' deleted`, 'success', 2000);
            } catch (e) { ui.showToast(e.message, 'error'); }
        });
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
                <div class="grs-actions">${actionsHtml}</div>
                <button type="button" class="sb-icon-btn grs-close" title="Close">✕</button>
            </div>
            ${tabs.length > 1 ? `<div class="sb-mode-tabs grs-tabs">
                ${tabs.map((t, i) => `<button class="sb-mode-tab grs-tab${i === 0 ? ' active' : ''}" data-tab="${esc(t.title)}">${esc(t.title)}</button>`).join('')}
            </div>` : ''}
            <div class="pr-modal-body grs-body">
                ${tabs.map((t, i) => `<div class="grs-pane" data-tab="${esc(t.title)}" style="display:${i === 0 ? 'block' : 'none'}">${t.html}</div>`).join('')}
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
            title: 'Story',
            html: `
                ${presetRowHtml(presetNames, '— current values —')}
                ${slotsHtml(slots.map(s => slotField(s, false)), f => (opts.slots || {})[f.key])}
                <div style="opacity:.7;font-size:.85em">Changes land on her next turn after Save. Sealed blanks are edited from the ✍ chips in the scene panel.</div>`,
            init(pane) {
                setupPane = pane;
                wirePresetRow(pane, slug, setup, slots, opts.slots || {});
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
    const envTab = objData ? objectsTab(slug, opts.session, objData) : null;
    if (envTab) tabs.push(envTab);

    const { overlay, close } = buildModal(
        `&#x2699;&#xFE0E; ${esc(data.title || slug)} settings`, tabs,
        `<button type="button" class="pk-btn pk-btn-primary grs-save">Save</button>
         <button type="button" class="pk-btn grs-reset" title="Restore defaults into the form (Save to apply)">Reset</button>`);

    // Deep link (the 🏠 button lands on the Objects tab directly)
    if (opts.tab) {
        const btn = [...overlay.querySelectorAll('.grs-tab')].find(t => t.dataset.tab === opts.tab);
        if (btn) btn.click();
    }

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
            if (envTab?.flush) await envTab.flush();   // pending room-text edits
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

export async function openStorySetup(slug, setup, session) {
    // Environment rides the start modal too (Krem 2026-08-20): stock the
    // house / load a whole saved story BEFORE turn 1. The routes resolve a
    // pre-start `slug` onto this chat's user layer; story/start merges it.
    let objData = null;
    if (session) {
        try {
            const d = await api(`story/objects?session=${encodeURIComponent(session)}&slug=${encodeURIComponent(slug)}`);
            if (d.active !== false) objData = d;
        } catch { /* Story tab alone */ }
    }
    return new Promise((resolve) => {
        const slots = setup.slots || [];
        const open = slots.filter(s => !s.sealed);
        const sealed = slots.filter(s => s.sealed && s.seal_key);
        const presetNames = Object.keys(setup.presets || {});
        let done = false;
        const finish = (v) => { if (!done) { done = true; resolve(v); } };

        const tabs = [{
            title: 'Story',
            html: `
                ${presetRowHtml(presetNames, '— the default story —')}
                ${slotsHtml(open.map(s => slotField(s, false)), () => undefined)}
                ${sealed.map(s => fieldHtml(slotField(s, true), undefined)).join('')}`,
            init(pane) {
                wirePresetRow(pane, slug, setup, open, {});
            }
        }];
        const envTab = objData ? objectsTab(slug, session, objData) : null;
        if (envTab) tabs.push(envTab);

        const { overlay, close } = buildModal(
            `\u{1F4D6} ${esc(setup.title || slug)} — set the stage`, tabs,
            `<button type="button" class="pk-btn pk-btn-primary grs-start" title="Start the story with this setup">▶ Start</button>
             <button type="button" class="pk-btn grs-cancel">Cancel</button>`);

        overlay.addEventListener('click', (e) => {
            if (e.target === overlay) finish(null);
        });
        overlay.querySelector('.grs-close').addEventListener('click', () => finish(null));
        overlay.querySelector('.grs-cancel').onclick = () => { close(); finish(null); };
        overlay.querySelector('.grs-start').onclick = async () => {
            // Pending room-text edits save on Start — the only save verb
            // here besides a preset save (Krem 2026-08-20).
            if (envTab?.flush) {
                try { await envTab.flush(); }
                catch (e) { ui.showToast('Room edits failed to save: ' + e.message, 'error'); return; }
            }
            const vals = {};
            for (const s of open) {
                const v = readField(overlay, s.key);
                if (v !== undefined && String(v).trim()) vals[s.key] = String(v).trim();
            }
            const sealedFills = sealed
                .map(s => ({ key: s.seal_key, text: String(readField(overlay, s.key) || '').trim() }))
                .filter(f => f.text);
            close();
            finish({ slots: vals, sealed: sealedFills });
        };
    });
}

// ── Environment tab (the open-world editor) ─────────────────────────────────
function objectsTab(slug, session, data) {
    const rooms = data.rooms || [];
    const authorOf = (spec) => spec?._author === 'ai' ? ' \u{1F916}' : '';
    const curId = data.current_room;
    const html = `
        <div class="grs-section-title" style="margin-top:0">Preset</div>
        <div class="grs-preset-row">
            <select class="grs-set-pick" title="Picking a preset loads it into this playthrough (materializes at the zork-line)"><option value="">— the default environment —</option></select>
            <button type="button" class="pk-btn grs-set-save" title="Save your placed objects + room text as a named preset for this story">\u{1F4BE} Save</button>
            <button type="button" class="pk-btn grs-set-del" title="Delete the selected preset">\u{1F5D1}\u{FE0E} Delete</button>
            <span class="grs-preset-namer" style="display:none">
                <input type="text" class="grs-preset-name" placeholder="preset name" maxlength="60">
                <button type="button" class="pk-btn pk-btn-primary grs-preset-ok">✓ Save</button>
                <button type="button" class="pk-btn grs-preset-no">✕</button>
            </span>
        </div>
        <div class="grs-section-title">Room</div>
        <div class="grs-room-row">
            <select class="grs-obj-room">
                ${rooms.map(r => `<option value="${r.id}"${r.id === curId ? ' selected' : ''}>${r.id === curId ? '\u{1F4CD} ' : ''}${esc(r.title)}${r.id === curId ? ' — you are here' : ''}</option>`).join('')}
            </select>
            <div class="grs-room-stats">
                <div class="grs-room-stats-head">In this room</div>
                <div class="grs-room-stats-line"></div>
            </div>
        </div>
        <div class="sb-field sb-field-stack">
            <label>Full description (<span class="grs-obj-count">0</span>/900)</label>
            <textarea class="grs-obj-template" rows="6" title="what she reads — the room's reality"></textarea>
        </div>
        <div class="sb-field sb-field-stack">
            <label>Short description</label>
            <textarea class="grs-obj-pdesc" rows="2" title="the one-liner in your scene strip"></textarea>
        </div>
        <div class="sb-field sb-field-stack">
            <label>Exits</label>
            <input type="text" class="grs-exit-add" list="grs-exit-rooms"
                   placeholder="type a room name to add an exit…"
                   title="Additive only — shipped doors, their locks and conditions are never touched. New exits open with the house (zork-line).">
            <datalist id="grs-exit-rooms">
                ${rooms.map(r => `<option value="${esc(r.title)}"></option>`).join('')}
            </datalist>
            <div class="grs-exit-badges"></div>
        </div>
        <div class="grs-section-title">Placed objects</div>
        <div class="sb-field sb-field-stack">
            <div class="grs-obj-list grs-obj-grid"></div>
        </div>
        <div class="grs-obj-add-form" style="display:none">
            <input type="text" class="grs-obj-name" placeholder="object name, e.g. rare_pepes">
            <div class="grs-obj-mech-note" style="display:none;color:var(--text-secondary,#8a8fa3);font-size:var(--font-sm,0.85em)">\u{2699}\u{FE0E} This object has story mechanics — your edits reword it; the machinery stays.</div>
            <input type="text" class="grs-obj-desc" placeholder="what looking at it shows her">
            <div class="grs-act-rows"></div>
            <button type="button" class="pk-btn grs-act-add" title="One more verb it responds to — 'eat' → 'You shrink to very small.'">+ Add action</button>
            <label class="st-tools-check" style="margin:0"><input type="checkbox" class="grs-obj-hidden"> Hidden — she can't see it until it's found by searching the room</label>
            <div style="display:flex;gap:6px">
                <button type="button" class="pk-btn pk-btn-primary grs-obj-place">Place</button>
                <button type="button" class="pk-btn grs-obj-cancel">Cancel</button>
                <button type="button" class="pk-btn grs-obj-reset" style="display:none" title="Drop your edits — back to the pack's version">↩ Reset to shipped</button>
            </div>
        </div>`;

    const tab = { title: 'Environment', html };
    tab.init = (pane) => {
        let world = data;
        // Room-text edits are PENDING until the modal's Save/▶ Start (or a
        // preset save) flushes them — no third save button (Krem 2026-08-20).
        // Pending survives room switches; closing the modal discards it.
        const pending = {};
        const pendingExits = {};   // rid → add_exits list (same save verbs)
        const roomSel = pane.querySelector('.grs-obj-room');
        const tArea = pane.querySelector('.grs-obj-template');
        const pArea = pane.querySelector('.grs-obj-pdesc');
        const count = pane.querySelector('.grs-obj-count');
        const curRoom = () => world.rooms.find(r => String(r.id) === roomSel.value) || world.rooms[0];
        const dirty = () => Object.keys(pending).length || Object.keys(pendingExits).length;

        const stash = () => {
            const r = curRoom();
            if (!r) return;
            const savedT = r.template || r.shipped_template || '';
            const savedP = r.player_desc || r.shipped_player_desc || '';
            if (tArea.value === savedT && pArea.value === savedP) delete pending[r.id];
            else pending[r.id] = { template: tArea.value, player_desc: pArea.value };
        };

        tab.flush = async () => {
            const rids = new Set([...Object.keys(pending), ...Object.keys(pendingExits)]);
            for (const rid of rids) {
                const body = { session, slug, room_id: Number(rid) };
                if (pending[rid]) {
                    body.template = pending[rid].template;
                    body.player_desc = pending[rid].player_desc;
                }
                if (pendingExits[rid]) body.add_exits = pendingExits[rid];
                await api('story/room-text', 'POST', body);
            }
            [pending, pendingExits].forEach(m => Object.keys(m).forEach(k => delete m[k]));
        };

        // Effective object view: shipped spec + user shadow (desc/hidden
        // replace, verb messages overlay) — mirrors the server merge law.
        const effective = (so, ov) => {
            const verbs = { ...(so?.verbs || {}) };
            for (const [v, s] of Object.entries(ov?.interactions || {}))
                verbs[v] = String((s || {}).message ?? verbs[v] ?? '');
            return { desc: ov?.desc ?? so?.desc ?? '',
                     hidden: ov?.hidden ?? so?.hidden ?? false,
                     verbs, has_mechanics: !!so?.has_mechanics };
        };
        const roomExits = (r) => pendingExits[r.id] ?? r.add_exits ?? [];

        const paintRoom = () => {
            const r = curRoom();
            if (!r) return;
            const px = pending[r.id];
            tArea.value = px ? px.template : (r.template || r.shipped_template || '');
            pArea.value = px ? px.player_desc : (r.player_desc || r.shipped_player_desc || '');
            count.textContent = tArea.value.length;
            const layerObjs = (world.objects || {})[String(r.id)] || {};
            const shippedObjs = r.shipped_objs || {};

            // Cards: shipped (📦, ✏ when shadowed, ghost when tombstoned),
            // then user/AI-placed. The + tile is last AND the empty state.
            const cards = [];
            let items = 0, actions = 0;
            for (const [n, so] of Object.entries(shippedObjs)) {
                const ov = layerObjs[n];
                if (ov && ov._removed) {
                    cards.push(`<div class="grs-obj-card grs-obj-ghost">
                        <button type="button" class="sb-icon-btn grs-obj-restore" data-name="${esc(n)}" title="Bring it back">↩</button>
                        <div class="grs-obj-card-title">${esc(n)} \u{1F4E6}</div>
                        <div class="grs-obj-card-desc">removed from this room</div>
                    </div>`);
                    continue;
                }
                const eff = effective(so, ov);
                const verbs = Object.keys(eff.verbs).join(', ');
                items += 1; actions += Object.keys(eff.verbs).length;
                cards.push(`<div class="grs-obj-card grs-obj-editable" data-name="${esc(n)}" data-shipped="1">
                    <button type="button" class="sb-icon-btn grs-obj-del" data-name="${esc(n)}" title="Remove from this room (restorable)">✕</button>
                    <div class="grs-obj-card-title">${esc(n)} \u{1F4E6}${ov ? ' ✏' : ''}</div>
                    <div class="grs-obj-card-desc">${esc(eff.desc)}</div>
                    ${verbs ? `<div class="grs-obj-card-verbs">${esc(verbs)}</div>` : ''}
                </div>`);
            }
            for (const [n, spec] of Object.entries(layerObjs)) {
                if (shippedObjs[n]) continue;   // shadows/tombstones decorate above
                const verbs = Object.keys(spec.interactions || {}).join(', ');
                items += 1; actions += Object.keys(spec.interactions || {}).length;
                cards.push(`<div class="grs-obj-card grs-obj-editable" data-name="${esc(n)}">
                    <button type="button" class="sb-icon-btn grs-obj-del" data-name="${esc(n)}" title="Remove">✕</button>
                    <div class="grs-obj-card-title">${esc(n)}${authorOf(spec)}</div>
                    <div class="grs-obj-card-desc">${esc(spec.desc || '')}</div>
                    ${verbs ? `<div class="grs-obj-card-verbs">${esc(verbs)}</div>` : ''}
                </div>`);
            }
            const list = pane.querySelector('.grs-obj-list');
            list.innerHTML = cards.join('')
                + '<div class="grs-obj-card grs-obj-addtile" role="button" tabindex="0">+ Add object</div>';
            const tile = list.querySelector('.grs-obj-addtile');
            tile.onclick = () => openAdd();
            tile.onkeydown = (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openAdd(); } };
            list.querySelectorAll('.grs-obj-editable').forEach(c => c.onclick = (e) => {
                if (e.target.closest('.grs-obj-del')) return;
                openAdd(c.dataset.name);
            });
            list.querySelectorAll('.grs-obj-del').forEach(b => b.onclick = async () => {
                try {
                    const res = await api('story/objects/delete', 'POST',
                              { session, slug, room_id: r.id, name: b.dataset.name });
                    if (res.detail) ui.showToast(res.detail, res.success ? 'success' : 'error', 2000);
                    await refresh();
                } catch (e) { ui.showToast(e.message, 'error'); }
            });
            list.querySelectorAll('.grs-obj-restore').forEach(b => b.onclick = async () => {
                try {
                    await api('story/objects/delete', 'POST',
                              { session, slug, room_id: r.id, name: b.dataset.name, restore: true });
                    await refresh();
                } catch (e) { ui.showToast(e.message, 'error'); }
            });

            // Exits: shipped = fixed badges; user-added = removable, pending
            // until Save/Start. New ones open with the house (zork-line).
            const added = roomExits(r);
            const badges = pane.querySelector('.grs-exit-badges');
            badges.innerHTML = (r.shipped_exits || []).map(e =>
                `<span class="grs-exit-badge">${esc(e.label || e.to)}</span>`).join('')
                + added.map((e, i) =>
                `<span class="grs-exit-badge grs-exit-user">${esc(e.label)}
                    <button type="button" class="grs-exit-x" data-i="${i}" title="Remove this exit">✕</button></span>`).join('');
            badges.querySelectorAll('.grs-exit-x').forEach(b => b.onclick = () => {
                const next = added.filter((_, i) => i !== Number(b.dataset.i));
                const saved = JSON.stringify(r.add_exits || []);
                if (JSON.stringify(next) === saved) delete pendingExits[r.id];
                else pendingExits[r.id] = next;
                paintRoom();
            });

            // "In this room" blurb — the dropdown reads as THE room
            // selector, not another item picker (Krem 2026-08-20)
            pane.querySelector('.grs-room-stats-line').textContent =
                `Exits: ${(r.exits || 0) + added.length} · Items: ${items} · Actions: ${actions}`;
        };

        const refresh = async () => {
            try {
                const d = await api(`story/objects?session=${encodeURIComponent(session)}&slug=${encodeURIComponent(slug)}`);
                if (d.active !== false) { world = d; paintRoom(); }
            } catch { /* pane keeps last state */ }
        };

        tArea.addEventListener('input', () => { count.textContent = tArea.value.length; stash(); });
        pArea.addEventListener('input', stash);

        // Exits: type a room name (datalist autocompletes) → badge appends.
        const exitAdd = pane.querySelector('.grs-exit-add');
        const tryAddExit = () => {
            const r = curRoom();
            const val = exitAdd.value.trim().toLowerCase();
            if (!val || !r) return;
            const target = world.rooms.find(x => (x.title || '').trim().toLowerCase() === val);
            if (!target) return;   // not a room (yet) — keep typing
            exitAdd.value = '';
            if (target.id === r.id) return;
            const added = roomExits(r);
            if ((r.shipped_exits || []).some(e => e.to === target.id)
                || added.some(e => e.to === target.id)) {
                ui.showToast('That exit already exists', 'error', 1500);
                return;
            }
            pendingExits[r.id] = [...added, { label: target.title, to: target.id }];
            paintRoom();
        };
        exitAdd.addEventListener('change', tryAddExit);
        exitAdd.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') { e.preventDefault(); tryAddExit(); }
        });

        // ── Add/edit-object form (the + tile adds; clicking a card edits;
        // shipped objects save as SHADOWS — verbatim-equals-shipped clears,
        // same house rule as room text) ─────────────────────────────────
        const addForm = pane.querySelector('.grs-obj-add-form');
        const objName = addForm.querySelector('.grs-obj-name');
        const objDesc = addForm.querySelector('.grs-obj-desc');
        const objHidden = addForm.querySelector('.grs-obj-hidden');
        const actRows = addForm.querySelector('.grs-act-rows');
        const resetBtn = addForm.querySelector('.grs-obj-reset');
        const mechNote = addForm.querySelector('.grs-obj-mech-note');
        let editing = null;   // object name while editing, else null

        const addActRow = (verb, resp) => {
            const row = document.createElement('div');
            row.className = 'grs-act-row';
            row.innerHTML = `
                <input type="text" class="grs-act-verb" placeholder="verb, e.g. eat">
                <input type="text" class="grs-act-resp" placeholder="what the world says back — returned to her as story truth">
                <button type="button" class="sb-icon-btn grs-act-del" title="Remove action">✕</button>`;
            row.querySelector('.grs-act-verb').value = verb || '';
            row.querySelector('.grs-act-resp').value = resp || '';
            row.querySelector('.grs-act-del').onclick = () => row.remove();
            actRows.appendChild(row);
            return row;
        };

        const openAdd = (name) => {
            const r = curRoom();
            editing = name || null;
            actRows.innerHTML = '';
            objName.value = editing || '';
            objName.disabled = !!editing;
            const so = editing ? (r.shipped_objs || {})[editing] : null;
            const ov = editing ? ((world.objects || {})[String(r.id)] || {})[editing] : null;
            if (so) {
                const eff = effective(so, ov);
                objDesc.value = eff.desc;
                objHidden.checked = !!eff.hidden;
                for (const [v, m] of Object.entries(eff.verbs)) addActRow(v, m);
                mechNote.style.display = so.has_mechanics ? '' : 'none';
                resetBtn.style.display = ov ? '' : 'none';
            } else if (editing && ov) {
                objDesc.value = ov.desc || '';
                objHidden.checked = !!ov.hidden;
                for (const [v, s] of Object.entries(ov.interactions || {}))
                    addActRow(v, (s || {}).message || '');
                mechNote.style.display = 'none';
                resetBtn.style.display = 'none';
            } else {
                objDesc.value = '';
                objHidden.checked = false;
                mechNote.style.display = 'none';
                resetBtn.style.display = 'none';
            }
            addForm.querySelector('.grs-obj-place').textContent = editing ? 'Save' : 'Place';
            addForm.style.display = '';
            (editing ? objDesc : objName).focus();
        };
        const closeAdd = () => {
            addForm.style.display = 'none';
            editing = null;
            objName.value = ''; objName.disabled = false;
            objDesc.value = ''; objHidden.checked = false;
            actRows.innerHTML = '';
        };
        addForm.querySelector('.grs-act-add').onclick = () => addActRow().querySelector('.grs-act-verb').focus();
        addForm.querySelector('.grs-obj-cancel').onclick = closeAdd;
        resetBtn.onclick = async () => {
            const r = curRoom();
            if (!editing || !r) return;
            try {
                await api('story/objects/delete', 'POST',
                          { session, slug, room_id: r.id, name: editing, restore: true });
                closeAdd();
                await refresh();
            } catch (e) { ui.showToast(e.message, 'error'); }
        };
        addForm.querySelector('.grs-obj-place').onclick = async () => {
            const r = curRoom();
            const name = editing || objName.value.trim();
            if (!name) { ui.showToast('Object needs a name', 'error'); return; }
            const so = (r.shipped_objs || {})[name];
            // Hand-placed objects appear immediately (the at-open toggle was
            // noise — post-line placement IS the normal case; presets still
            // materialize at the zork-line on import).
            let spec;
            if (so) {
                // Shadow: store only the DIFF vs shipped. Nothing changed →
                // clear the override entirely (verbatim-equals-shipped).
                spec = {};
                const desc = objDesc.value.trim();
                if (desc !== (so.desc || '')) spec.desc = desc;
                if (objHidden.checked !== !!so.hidden) spec.hidden = objHidden.checked;
                const ints = {};
                actRows.querySelectorAll('.grs-act-row').forEach(row => {
                    const verb = row.querySelector('.grs-act-verb').value.trim();
                    if (!verb) return;
                    const resp = row.querySelector('.grs-act-resp').value.trim();
                    if (verb in (so.verbs || {})) {
                        if (resp !== so.verbs[verb]) ints[verb] = { message: resp };
                    } else {
                        ints[verb] = { message: resp || `You ${verb} the ${name}.` };
                    }
                });
                if (Object.keys(ints).length) spec.interactions = ints;
                if (!Object.keys(spec).length) {
                    try {
                        if (((world.objects || {})[String(r.id)] || {})[name])
                            await api('story/objects/delete', 'POST',
                                      { session, slug, room_id: r.id, name, restore: true });
                        closeAdd();
                        await refresh();
                    } catch (e) { ui.showToast(e.message, 'error'); }
                    return;
                }
            } else {
                spec = { desc: objDesc.value.trim(), hidden: objHidden.checked };
                const acts = {};
                actRows.querySelectorAll('.grs-act-row').forEach(row => {
                    const verb = row.querySelector('.grs-act-verb').value.trim();
                    if (!verb) return;
                    const resp = row.querySelector('.grs-act-resp').value.trim();
                    acts[verb] = { message: resp || `You ${verb} the ${name}.` };
                });
                if (Object.keys(acts).length) spec.interactions = acts;
            }
            try {
                const res = await api('story/objects', 'POST',
                                      { session, slug, room_id: r.id, name, spec });
                if (!res.success) { ui.showToast(res.detail || 'refused', 'error'); return; }
                closeAdd();
                ui.showToast(`'${name}' ${so ? 'saved' : 'placed'}`, 'success', 2000);
                await refresh();
            } catch (e) { ui.showToast(e.message, 'error'); }
        };

        roomSel.onchange = paintRoom;
        paintRoom();

        const setPick = pane.querySelector('.grs-set-pick');
        let lastSet = '';   // revert target when a load is refused/declined
        const loadSets = async () => {
            try {
                const d = await api(`story/${encodeURIComponent(slug)}/objsets`);
                const names = Object.keys(d.objsets || {});
                setPick.innerHTML = '<option value="">— the default environment —</option>'
                    + names.map(n => `<option value="${esc(n)}">${esc(n)}</option>`).join('');
                setPick.value = lastSet;
            } catch { /* leave as-is */ }
        };
        loadSets();
        // No Load button — picking a preset loads it right then (Krem
        // 2026-08-20); unsaved room edits get a yes/no before they're lost.
        setPick.onchange = async () => {
            const name = setPick.value;
            if (!name) { lastSet = ''; return; }
            if (dirty()
                && !confirm('You have unsaved room edits — loading a preset discards them.\n\nContinue?')) {
                setPick.value = lastSet;
                return;
            }
            try {
                const res = await api('story/objsets/load', 'POST', { session, slug, name });
                if (!res.success) {
                    ui.showToast(res.detail || 'refused', 'error');
                    setPick.value = lastSet;
                    return;
                }
                [pending, pendingExits].forEach(m => Object.keys(m).forEach(k => delete m[k]));
                lastSet = name;
                ui.showToast(res.detail || `'${name}' loaded`, 'success', 2500);
                await refresh();
            } catch (e) { ui.showToast(e.message, 'error'); setPick.value = lastSet; }
        };
        const setRow = setPick.closest('.grs-preset-row');
        wireNamer(setRow, pane.querySelector('.grs-set-save'), () => setPick.value, async (name) => {
            try {
                await tab.flush();   // the preset snapshot must include textarea edits
                const res = await api(`story/${encodeURIComponent(slug)}/objsets`, 'POST',
                                      { name, session, slug });
                if (!res.success) { ui.showToast(res.detail || 'refused', 'error'); return false; }
                if (![...setPick.options].some(o => o.value === name)) {
                    const opt = document.createElement('option');
                    opt.value = opt.textContent = name;
                    setPick.appendChild(opt);
                }
                setPick.value = name;
                lastSet = name;
                ui.showToast(`Preset '${name}' saved (your objects only)`, 'success', 2500);
            } catch (e) { ui.showToast(e.message, 'error'); return false; }
        });
        armDelete(pane.querySelector('.grs-set-del'), '\u{1F5D1}\u{FE0E} Delete',
            () => {
                if (!setPick.value) { ui.showToast('Pick a preset to delete first', 'error', 2000); return false; }
                return true;
            },
            async () => {
                const name = setPick.value;
                try {
                    const res = await api(`story/${encodeURIComponent(slug)}/objsets`, 'POST',
                                          { name, delete: true });
                    if (!res.success) { ui.showToast(res.detail || 'refused', 'error'); return; }
                    [...setPick.options].find(o => o.value === name)?.remove();
                    setPick.value = '';   // placed objects stay — only the saved preset dies
                    lastSet = '';
                    ui.showToast(`Preset '${name}' deleted`, 'success', 2000);
                } catch (e) { ui.showToast(e.message, 'error'); }
            });
    };
    return tab;
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
         <button type="button" class="pk-btn grs-reset" title="Restore defaults into the form (Save to apply)">Reset</button>`);

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
