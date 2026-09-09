// Game Settings — large modal for ONE game; tabs come from the engine's
// SETTINGS schema ('tab' per field — e.g. poker: Rules / Start). All tabs
// render at once (hidden/shown). GAME modals keep a Save/Reset pair; the
// STORY gear has neither (Krem 2026-08-21): schema + slots autosave as you
// edit, rooms/objects/exits save through their own lanes, and the scenario
// 💾 is the one deliberate save. A new game ships settings by exporting
// SETTINGS — zero UI code here.
//
// Open-mansion v1 (2026-08-20, plan tmp/open-mansion-plan.md): ONE modal
// family for every story-editing verb (Krem's ruling — no second kind of
// modal). Stories compose tabs from three sources:
//   Setup (this run)  — Mad-Libs slot values, per-playthrough (story/slots)
//   This story / GM   — the existing settings schema (storycfg)
//   Objects           — custom pane: placed objects + room-text overrides
// Fresh launch uses openStorySetup(): ONLY the Setup tab, Start Story button.

import * as ui from '/static/ui.js';
import { accordionHtml } from '/static/shared/accordion.js';

const PLUGIN_API = '/api/plugin/game-room/';
const bootV = () => document.querySelector('meta[name="boot-version"]')?.content || '';

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
            // options: plain values, or {value, label} (the room spine's
            // dynamic lists — providers, prompts, toolsets)
            const options = (f.options || []).map(o => (o && typeof o === 'object') ? o : { value: o, label: o });
            const isCustom = !!f.allow_custom && v !== '' && !options.some(o => String(o.value) === String(v));
            return `<div class="sb-field sb-field-stack">
                ${labelHtml(f)}
                <select class="grs-field grs-select" data-key="${esc(f.key)}">
                    ${options.map(o => `<option value="${esc(o.value)}"${String(v) === String(o.value) ? ' selected' : ''}>${esc(o.label ?? o.value)}</option>`).join('')}
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

// Open slots → ONE TAB PER SECTION (declaration order; sectionless slots
// land on 'Story') — Krem 2026-08-20: tabs are Story / Characters / Rooms.
// The tab title carries the section, so in-pane headers are suppressed.
// Sealed slots ride the first tab.
function slotTabs(open, sealed, valueOf) {
    const groups = [], byName = {};
    for (const s of open) {
        const f = slotField(s, false);
        const title = f._section || 'Story';
        if (!byName[title]) { byName[title] = { title, fields: [] }; groups.push(byName[title]); }
        f._section = '';
        byName[title].fields.push(f);
    }
    return groups.map((g, i) => ({
        title: g.title,
        html: slotsHtml(g.fields, valueOf)
            + (i === 0 ? (sealed || []).map(s => fieldHtml(slotField(s, true), undefined)).join('') : '')
    }));
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

// ── The Scenario bar (Krem 2026-08-20: ONE dropdown for the whole
// authored world — slots + environment — under the modal header). Picking
// STAGES: slots fill the form, the env half is remembered and applied on
// Save/▶ Start alongside pending room edits. 💾 Save snapshots form slots
// + the playthrough's environment under a name (namer = save-as).
function scenarioBarHtml(names) {
    return `<div class="grs-preset-row grs-scenario-bar">
        <label class="grs-section-title" style="margin:0">Scenario:</label>
        <select class="grs-scn-pick" title="Picking swaps the house to that scenario, right then — blank = the shipped story">
            <option value="">— the default story —</option>
            ${names.map(n => `<option value="${esc(n)}">${esc(n)}</option>`).join('')}
        </select>
        <span class="grs-scn-unsaved" style="display:none" title="Your setup/house differs from this scenario — 💾 Save to keep the changes">●</span>
        <button type="button" class="pk-btn grs-scn-save" title="Save the current setup + environment as a named scenario">\u{1F4BE} Save</button>
        <button type="button" class="pk-btn grs-scn-del" title="Delete the selected scenario">\u{1F5D1}\u{FE0E} Delete</button>
        <span class="grs-preset-namer" style="display:none">
            <input type="text" class="grs-preset-name" placeholder="scenario name" maxlength="60">
            <button type="button" class="pk-btn pk-btn-primary grs-preset-ok">✓ Save</button>
            <button type="button" class="pk-btn grs-preset-no">✕</button>
        </span>
    </div>`;
}

// Wire the bar. Returns state {staged} — the scenario name whose ENV half
// still needs story/scenarios/load at flush time. baseVals = what the
// blank option restores into the slot form; envFlush = the Environment
// tab's pending flush (so a snapshot includes textarea/exit edits).
function wireScenarioBar(overlay, slug, setup, slotList, baseVals, opts = {}) {
    const bar = overlay.querySelector('.grs-scenario-bar');
    if (!bar) return null;
    const sel = bar.querySelector('.grs-scn-pick');
    const state = {};
    let last = '';   // revert target when a swap is refused or declined
    // Reopen on the scenario the playthrough is ON (persisted on the user
    // layer — Krem 2026-08-20: the gear opened showing 'default' amnesia).
    if (opts.current && (setup.scenarios || {})[opts.current]) {
        sel.value = opts.current;
        last = opts.current;
    }
    // Slot fields diverged from the LOADED scenario's values (unsaved
    // form edits a swap would silently overwrite — draft restores included).
    const slotsDiverged = () => {
        const sc = (setup.scenarios || {})[last];
        const base = sc ? (sc.slots || {}) : (baseVals || {});
        return slotList.some(s =>
            String(readField(overlay, s.key) ?? '') !== String(base[s.key] ?? s.default ?? ''));
    };
    // ● unsaved dot: visible whenever the canvas/form has drifted from the
    // loaded scenario — the visible cue that a 💾 re-save is due (clown_key
    // fossil lesson, 2026-08-20).
    const updateDot = () => {
        bar.querySelector('.grs-scn-unsaved').style.display =
            (slotsDiverged() || (opts.envDiverged && opts.envDiverged())) ? '' : 'none';
    };
    state.updateDot = updateDot;
    overlay.addEventListener('input', updateDot);
    overlay.addEventListener('change', updateDot);
    setTimeout(updateDot, 0);   // after callers finish wiring/prefill

    // PURE SWAP (Krem's ruling 2026-08-20): picking a scenario makes the
    // house BECOME it, right then — blank = back to the shipped story.
    // Guard rule (Krem): prompt ONLY when current values have DIVERGED from
    // the loaded scenario — matching content swaps silently.
    sel.onchange = async () => {
        const name = sel.value;
        const sc = (setup.scenarios || {})[name];
        if (name && !sc) { sel.value = last; return; }
        if ((slotsDiverged() || (opts.envDiverged && opts.envDiverged()))
            && !confirm((name
                ? `Swap to '${name}'?`
                : 'Reset to the shipped story?')
                + ' You have unsaved changes here — they will be replaced.')) {
            sel.value = last;
            return;
        }
        try {
            const r = await api('story/scenarios/load', 'POST',
                                { session: opts.session, slug, name });
            if (!r.success) { ui.showToast(r.detail || 'refused', 'error'); sel.value = last; return; }
        } catch (e) { ui.showToast(e.message, 'error'); sel.value = last; return; }
        const base = sc ? (sc.slots || {}) : (baseVals || {});
        for (const s of slotList) writeField(overlay, s.key, base[s.key] ?? s.default);
        last = name;
        if (opts.onSwap) await opts.onSwap();   // repaint the Rooms tab NOW
        updateDot();
        ui.showToast(name ? `'${name}' loaded` : 'Back to the default story', 'success', 2000);
    };
    wireNamer(bar, bar.querySelector('.grs-scn-save'), () => sel.value, async (name) => {
        try {
            if (opts.envFlush) await opts.envFlush();   // snapshot includes pendings
            const vals = {};
            for (const s of slotList) vals[s.key] = readField(overlay, s.key) ?? '';
            const r = await api(`story/${encodeURIComponent(slug)}/scenarios`, 'POST',
                                { name, slots: vals, session: opts.session, slug });
            if (!r.success) { ui.showToast(r.detail || 'refused', 'error'); return false; }
            if (![...sel.options].some(o => o.value === name)) {
                const opt = document.createElement('option');
                opt.value = opt.textContent = name;
                sel.appendChild(opt);
            }
            setup.scenarios = setup.scenarios || {};
            setup.scenarios[name] = { slots: vals };
            sel.value = name;
            last = name;   // saved = the canvas IS this scenario now
            if (opts.onSaved) opts.onSaved();
            updateDot();
            ui.showToast(`Scenario '${name}' saved`, 'success', 2000);
        } catch (e) { ui.showToast(e.message, 'error'); return false; }
    });
    armDelete(bar.querySelector('.grs-scn-del'), '\u{1F5D1}\u{FE0E} Delete',
        () => {
            if (!sel.value) { ui.showToast('Pick a scenario to delete first', 'error', 2000); return false; }
            return true;
        },
        async () => {
            const name = sel.value;
            try {
                const r = await api(`story/${encodeURIComponent(slug)}/scenarios`, 'POST',
                                    { name, delete: true });
                if (!r.success) { ui.showToast(r.detail || 'refused', 'error'); return; }
                delete (setup.scenarios || {})[name];
                [...sel.options].find(o => o.value === name)?.remove();
                sel.value = '';
                last = '';   // the canvas keeps its content — only the save died
                ui.showToast(`Scenario '${name}' deleted`, 'success', 2000);
            } catch (e) { ui.showToast(e.message, 'error'); }
        });
    return state;
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

// ── shared modal chrome ─────────────────────────────────────────────────────
// tabs: [{title, html, init(pane, overlay)}]; actionsHtml renders in the
// header (left of ✕); barHtml (optional) renders as a modal-level bar
// between header and tabs — the Scenario line. Returns {overlay, close}.
function buildModal(title, tabs, actionsHtml, barHtml, opts = {}) {
    const overlay = document.createElement('div');
    overlay.className = 'pr-modal-overlay';
    overlay.innerHTML = `
        <div class="pr-modal grs-modal">
            <div class="pr-modal-header">
                <h3>${title}</h3>
                <div class="grs-actions">${actionsHtml}</div>
                <button type="button" class="sb-icon-btn grs-close" title="Close">✕</button>
            </div>
            ${barHtml ? `<div class="grs-bar">${barHtml}</div>` : ''}
            ${tabs.length > 1 ? `<div class="sb-mode-tabs grs-tabs">
                ${tabs.map((t, i) => `<button class="sb-mode-tab grs-tab${i === 0 ? ' active' : ''}" data-tab="${esc(t.title)}">${esc(t.title)}</button>`).join('')}
            </div>` : ''}
            <div class="pr-modal-body grs-body">
                ${tabs.map((t, i) => `<div class="grs-pane" data-tab="${esc(t.title)}" style="display:${i === 0 ? 'block' : 'none'}">${t.html}</div>`).join('')}
            </div>
        </div>`;
    document.body.appendChild(overlay);
    // CLICK GUARD (Krem 2026-08-20, after three lost setups): clicking the
    // backdrop NEVER closes these modals — a mis-click outside a form
    // holding 30 minutes of writing is not a close request. ✕/Escape route
    // through a guard; the default guard confirms when any field changed.
    // Excluded from the fingerprint: room-browse fields (pendings track
    // real edits) and the SCENARIO PICKER — picks are applied instantly
    // (pure swap) and both the preselect and post-save select-set happen
    // after the baseline stamp, which made every open→close prompt
    // "discard changes?" with nothing changed (Krem 2026-08-21).
    const fingerprint = () => JSON.stringify(
        [...overlay.querySelectorAll(
            'input, select:not(.grs-obj-room):not(.grs-scn-pick), '
            + 'textarea:not(.grs-obj-template):not(.grs-obj-pdesc):not(.grs-piece-text)')]
            .map(el => el.type === 'checkbox' ? !!el.checked : el.value));
    let baseline = null;   // stamped after inits run (below)
    const dirty = () => fingerprint() !== baseline;
    const escHandler = (e) => { if (e.key === 'Escape') tryClose(); };
    const close = () => {
        document.removeEventListener('keydown', escHandler);
        overlay.remove();
    };
    const tryClose = () => {
        const guard = opts.guard || (() => !dirty() || confirm('Discard your changes?'));
        if (!guard()) return;
        close();
        if (opts.onClose) opts.onClose();
    };
    document.addEventListener('keydown', escHandler);
    overlay.querySelector('.grs-close').onclick = tryClose;
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
    baseline = fingerprint();   // post-init = the form's clean state
    // rebaseline: autosave lanes call this at commit time — what's saved
    // needs no discard-guard (no-Save-button model, Krem 2026-08-21)
    return { overlay, close, tryClose, dirty, rebaseline: () => { baseline = fingerprint(); } };
}

export async function openGameSettings(gameId) {
    if (!gameId) return;
    return openFromPath(`play/${gameId}/settings`, gameId);
}

// ── the settings spine (2026-09-09) ─────────────────────────────────────────
// The layer kit (Krem's ruling, same day: NO override switches). A layer's
// fields come pre-filled with what they inherit; editing one makes it this
// layer's own value — marked with a dot, with ↺ to inherit again. Typing
// the inherited value back is not an override. One kit, two sidebars: the
// library (room defaults over the shipped ones) and a game room (the game's
// overrides over the room's).

function optionLabel(f, v) {
    if (f.type === 'checkbox') return v ? 'on' : 'off';
    if (f.type === 'select') {
        const o = (f.options || []).map(o => (o && typeof o === 'object') ? o : { value: o, label: o })
            .find(o => String(o.value) === String(v));
        return o ? String(o.label ?? o.value) : String(v ?? '');
    }
    return String(v ?? '');
}

function sameValue(f, a, b) {
    if (f.type === 'checkbox') return !!a === !!b;
    if (f.type === 'number' || f.type === 'range') return Number(a) === Number(b);
    return String(a ?? '').trim() === String(b ?? '').trim();
}

// values = this layer's OWN values ({key: value} — absent = inheriting).
// Sidebar shape (Krem 2026-09-09, the Mind accordion's line): one short
// label + one field per line, the field right-aligned; a ? opens the long
// words. Text fields stack. `reveal_if: key` rows show only while that
// checkbox is on.
function inlineFieldHtml(f, v) {
    const key = esc(f.key);
    const help = f.help ? `<button type="button" class="grs-help-btn" data-help="${key}" title="${esc(f.help)}">?</button>` : '';
    const label = `<label>${esc(f.label)}${help}</label>`;
    switch (f.type) {
        case 'checkbox':
            return `<div class="sb-field grs-inline">${label}<input type="checkbox" class="grs-field" data-key="${key}"${(v === true || v === 'true') ? ' checked' : ''}></div>`;
        case 'number':
        case 'range':
            return `<div class="sb-field grs-inline">${label}<input type="number" class="grs-field grs-num" data-key="${key}"
                min="${f.min ?? ''}" max="${f.max ?? ''}" step="${f.step ?? 1}" value="${esc(v)}"></div>`;
        case 'select': {
            const options = (f.options || []).map(o => (o && typeof o === 'object') ? o : { value: o, label: o });
            return `<div class="sb-field grs-inline">${label}<select class="grs-field grs-select" data-key="${key}">
                ${options.map(o => `<option value="${esc(o.value)}"${String(v) === String(o.value) ? ' selected' : ''}>${esc(o.label ?? o.value)}</option>`).join('')}
            </select></div>`;
        }
        case 'text':
            return fieldHtml(f, v) + (f.help ? `<div class="grs-inline-help"><button type="button" class="grs-help-btn" data-help="${key}" title="${esc(f.help)}">?</button></div>` : '');
        default:
            return `<div class="sb-field grs-inline">${label}<input type="text" class="grs-field" data-key="${key}" value="${esc(v)}"></div>`;
    }
}

export function layerRowsHtml(schema, inherited, values) {
    const shown = (f) => {
        const inh = inherited?.[f.key] ?? f.default;
        const own = values && values[f.key] !== undefined && values[f.key] !== null ? values[f.key] : undefined;
        return own !== undefined && !sameValue(f, own, inh) ? own : inh;
    };
    return schema.map(f => {
        const inh = inherited?.[f.key] ?? f.default;
        const v = shown(f);
        const over = !sameValue(f, v, inh);
        let hidden = false;
        const rv = revealSpec(f);
        if (rv) {
            const ctl = schema.find(x => x.key === rv.key);
            hidden = !!ctl && !revealOk(rv, shown(ctl));
        }
        return `<div class="grs-layer-row${over ? ' overridden' : ''}${hidden ? ' grs-hidden' : ''}" data-key="${esc(f.key)}"${rv ? ` data-reveal-if="${esc(rv.key)}"` : ''}>
            <div class="grs-layer-field">${inlineFieldHtml(f, v)}</div>
            <button type="button" class="grs-reset-key sb-icon-btn" title="Inherit again (${esc(optionLabel(f, inh))})">&#x21BA;</button>
        </div>`;
    }).join('');
}

// reveal_if: 'key' (a checkbox — shown while on) or {key, is: [...]} /
// {key, not: [...]} against another field's current value.
function revealSpec(f) {
    if (!f.reveal_if) return null;
    return typeof f.reveal_if === 'string' ? { key: f.reveal_if } : f.reveal_if;
}
function revealOk(rv, value) {
    if (rv.is) return rv.is.map(String).includes(String(value));
    if (rv.not) return !rv.not.map(String).includes(String(value));
    return !!value && value !== 'false';
}

// The ? card: the field's long words, one small card, click anywhere closes.
function helpCard(f) {
    document.getElementById('grs-help-card')?.remove();
    const wrap = document.createElement('div');
    wrap.id = 'grs-help-card';
    wrap.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.45);display:flex;align-items:center;justify-content:center;z-index:10000';
    wrap.innerHTML = `<div class="st-card"><div class="st-card-title">${esc(f.label)}</div><div class="st-card-desc" style="margin-bottom:0">${esc(f.help || '')}</div></div>`;
    wrap.onclick = () => wrap.remove();
    document.body.appendChild(wrap);
}

export function groupByTab(schema) {
    const byTab = {}, order = [];
    for (const f of schema) {
        const t = f.tab || 'Settings';
        if (!byTab[t]) { byTab[t] = []; order.push(t); }
        byTab[t].push(f);
    }
    return order.map(t => [t, byTab[t]]);
}

// onChange(key, value | null) — null = inherit again. The dot follows the
// comparison, not the click: typing the inherited value clears it.
export function wireLayer(scope, schema, inherited, onChange) {
    const byKey = Object.fromEntries(schema.map(f => [f.key, f]));
    const sync = (row) => {
        const f = byKey[row.dataset.key];
        const inh = inherited?.[f.key] ?? f.default;
        const v = readField(row, f.key);
        const over = v !== undefined && !sameValue(f, v, inh);
        row.classList.toggle('overridden', over);
        return over ? v : null;
    };
    // reveal: a checkbox controls the rows/groups tagged with its key
    const reveal = () => {
        scope.querySelectorAll('[data-reveal-if]').forEach(el => {
            const f = byKey[el.dataset.key];
            const rv = f && revealSpec(f);
            const ctl = rv && scope.querySelector(`.grs-layer-row[data-key="${CSS.escape(rv.key)}"]`);
            if (!ctl) return;
            el.classList.toggle('grs-hidden', !revealOk(rv, readField(ctl, rv.key)));
        });
    };
    scope.querySelectorAll('.grs-help-btn').forEach(b => {
        b.addEventListener('click', (e) => { e.preventDefault(); const f = byKey[b.dataset.help]; if (f) helpCard(f); });
    });
    scope.querySelectorAll('.grs-layer-row').forEach(row => {
        const key = row.dataset.key;
        const f = byKey[key];
        if (!f) return;
        sync(row);
        row.querySelectorAll('.grs-field, .grs-custom').forEach(el => {
            el.addEventListener('change', () => { if (onChange) onChange(key, sync(row)); reveal(); });
        });
        row.querySelector('.grs-reset-key')?.addEventListener('click', () => {
            writeField(row, key, inherited?.[key] ?? f.default);
            const lbl = row.querySelector('.grs-val');
            if (lbl) lbl.textContent = String(readField(row, key));
            sync(row);
            if (onChange) onChange(key, null);
            reveal();
        });
    });
    wireSelects(scope);
    reveal();
}

// Accordions per `tab` for one layer — the same shape in both sidebars.
export function layerAccordionsHtml(schema, inherited, values, nsPrefix, icons = {}, notes = {}) {
    return groupByTab(schema).map(([t, fields]) => accordionHtml({
        id: `${nsPrefix}:${t.toLowerCase()}`, title: t, icon: icons[t] || '',
        content: (notes[t] ? `<div class="gr-seat-note grs-tab-note">${esc(notes[t])}</div>` : '')
            + layerRowsHtml(fields, inherited, values),
    })).join('');
}

export const LAYER_ICONS = { Room: '\u{1F6CB}', Cadence: '⏱', Perception: '\u{1F441}', Voice: '\u{1F50A}', Memory: '\u{1F9E0}', Identity: '\u{1F3AD}' };

// ── Story settings (the ⚙ gear) ─────────────────────────────────────────────
// opts.session + opts.active → the in-game gear: Setup-this-run + Objects
// tabs join the schema tabs. Library gear (no session) = schema tabs only,
// exactly as before.
// ── Prompt Pieces panel (Krem 2026-08-21, one factory, two homes: the
// in-game gear's Characters tab and the setup modal's — the PLAY
// surfaces; the library wheel is conduct-only and never carries it).
// One user concept: pool pieces AND engine built-ins in one list, all
// editable — editing a built-in writes a pool OVERRIDE (tier-one key,
// wins in both lanes); ↩ reverts to shipped text; autosave debounced
// when non-empty (a cleared textarea never deletes); ✓ receipt on the
// landed response. Fields are OUTSIDE the modal fingerprint (own lane).
function piecesPanel(slug, session) {
    const html = `
        <div class="grs-section-title">Prompt Pieces</div>
        <div style="opacity:.7;font-size:.85em;margin-bottom:6px">Text she wears while a piece is active — toggled by Effects (add/remove prompt piece) on objects, exits and dice. Edits apply live. This is their one home; they don't show on the Prompts page.</div>
        <div class="grs-piece-rows"></div>
        <button type="button" class="pk-btn grs-piece-add">+ Add Prompt Piece</button>`;
    const wire = async (root) => {
        let data;
        try {
            data = await api(`story/${encodeURIComponent(slug)}/pieces`);
        } catch { return; }
        const rowsHost = root.querySelector('.grs-piece-rows');
        const addBtn = root.querySelector('.grs-piece-add');
        if (!rowsHost || !addBtn) return;
        const dl = root.querySelector('#grs-piece-list');
        let pool = data.pieces || {};
        const builtin = data.builtin || {};
        const syncDl = () => { if (dl) dl.innerHTML =
            Object.keys({ ...builtin, ...pool }).map(n => `<option value="${esc(n)}">`).join(''); };
        const pieceTimers = {};
        const savePiece = async (name, text) => {
            try {
                const r = await api(`story/${encodeURIComponent(slug)}/pieces`, 'POST',
                                    { session, name, text });
                if (!r.success) { ui.showToast(r.detail || 'refused', 'error'); return false; }
                pool = r.pieces || {};
                syncDl();
                return true;
            } catch (e) { ui.showToast(e.message, 'error'); return false; }
        };
        const pieceRow = (name, engineText) => {
            const isEngine = engineText != null;
            const div = document.createElement('div');
            div.className = 'grs-piece-row';
            div.innerHTML = `
                <div class="grs-piece-head"><code>${esc(name)}</code>
                    ${isEngine ? '<span class="grs-piece-src">engine</span>' : ''}
                    <span class="grs-piece-saved">✓ saved</span>
                    <button type="button" class="sb-icon-btn grs-piece-revert" title="Revert to the engine text" style="display:none">↩</button>
                    <button type="button" class="sb-icon-btn grs-piece-del" title="Delete piece"${isEngine ? ' style="display:none"' : ''}>✕</button></div>
                <textarea rows="2" class="grs-piece-text" placeholder="prompt text while this piece is active"></textarea>`;
            const ta = div.querySelector('.grs-piece-text');
            const savedMark = div.querySelector('.grs-piece-saved');
            const revBtn = div.querySelector('.grs-piece-revert');
            const flash = () => {
                savedMark.classList.add('on');
                clearTimeout(savedMark._t);
                savedMark._t = setTimeout(() => savedMark.classList.remove('on'), 1600);
            };
            const paintRev = () => {
                revBtn.style.display = (isEngine && name in pool) ? '' : 'none';
            };
            ta.value = (name in pool) ? pool[name] : (engineText || '');
            ta.oninput = () => {
                clearTimeout(pieceTimers[name]);
                const v = ta.value.trim();
                if (!v) return;
                // typed back to the shipped text = a revert, not an override
                const asShipped = isEngine && v === String(engineText).trim();
                if (asShipped && !(name in pool)) return;
                pieceTimers[name] = setTimeout(async () => {
                    if (await savePiece(name, asShipped ? '' : v)) { paintRev(); flash(); }
                }, 700);
            };
            revBtn.onclick = async () => {
                if (!confirm(`Revert '${name}' to the engine text?`)) return;
                if (await savePiece(name, '')) {
                    ta.value = engineText || '';
                    paintRev();
                    flash();
                }
            };
            div.querySelector('.grs-piece-del').onclick = async () => {
                if ((name in pool) && !confirm(`Delete piece '${name}'?`)) return;
                if (name in pool) await savePiece(name, '');
                div.remove();
            };
            paintRev();
            return div;
        };
        const names = [...new Set([...Object.keys(builtin), ...Object.keys(pool)])].sort();
        for (const n of names)
            rowsHost.appendChild(pieceRow(n, n in builtin ? builtin[n] : null));
        addBtn.onclick = () => {
            let name = prompt('Piece name (letters/numbers/underscores):');
            if (!name) return;
            name = name.toLowerCase().replace(/[^a-z0-9_]+/g, '_')
                       .replace(/^_+|_+$/g, '').slice(0, 40);
            if (!name) { ui.showToast('Name needs letters or numbers', 'error'); return; }
            if (name in pool || name in builtin) { ui.showToast('That piece already exists', 'error'); return; }
            const div = pieceRow(name, null);
            rowsHost.appendChild(div);
            div.querySelector('.grs-piece-text').focus();
        };
    };
    return { html, wire };
}

// ── Starting items (Krem 2026-08-22, ruling B): the player's kit — story-
// level objects living in NO room, in the inventory from turn 0, and live
// on the very next turn when edited (membership is DERIVED server-side,
// never journaled — no rename orphans; delete = it never was). Cards
// mirror the Rooms pane's objects (📦 shipped, ✏ shadowed, ghost
// tombstoned); same fidelity gate, but items speak a SUBSET of the object
// grammar — no take/hidden/visible-when/riddle (a kit item is simply with
// you) — so richer shipped specs open read-only, text edits shadowing.
const itemFits = (name, spec) => objFits(name, spec)
    && !spec.takeable && !spec.hidden && !spec.condition && !spec.puzzle;

// ── ONE owner for the Characters tab's shared content (2026-08-22, Krem's
// dual-surface finding: the in-game gear and the Play/setup modal each
// hand-assembled Characters, so a section added to one silently missed
// the other — the items panel did exactly that on day one; pieces only
// avoided it by luck of being added to both). Every shared section
// registers HERE and rides BOTH surfaces by construction — the same
// pattern that keeps the Rooms tab drift-free (objectsTab, one builder).
// Gating is per-section, not per-surface: items need world data (objData
// resolves pre-start via the explicit-slug layer lane), pieces need only
// a session.
function charactersPanels(slug, session, objData) {
    const panels = [];
    if (objData) panels.push(itemsPanel(slug, session, objData));
    if (session) panels.push(piecesPanel(slug, session));
    return {
        html: panels.map(p => p.html).join(''),
        wire: (root) => { for (const p of panels) p.wire(root); },
    };
}

// Merge shared content into a tab list — appends to an existing tab of
// that title (slot sections may already own one) or creates it.
const mergeTab = (tabs, title, html) => {
    if (!html) return;
    const t = tabs.find(x => x.title === title);
    if (t) t.html += html;
    else tabs.push({ title, html });
};

// ── The WORLD sections (2026-08-22, DRY'd ahead of the scenario builder —
// rule of three: items drifted, pieces survived by luck, the builder is
// next). Everything a story surface shows of the world — Characters
// content (items + pieces) and the Rooms tab, plus the envTab hooks the
// scenario bar needs — assembled ONCE. A surface splices these into its
// own frame; the frames themselves (setup: slots/draft-net/▶ Start; gear:
// GM/State/autosave) are legitimately different and stay surface-owned.
// A new shared tab or section registers HERE and reaches every surface by
// construction.
function worldSections(slug, session, objData) {
    const chars = charactersPanels(slug, session, objData);
    const envTab = objData ? objectsTab(slug, session, objData) : null;
    return {
        envTab,
        splice: (tabs) => {
            mergeTab(tabs, 'Characters', chars.html);
            if (envTab) tabs.push(envTab);
        },
        wire: (overlay) => { chars.wire(overlay); },
        // envTab hooks every surface hands to wireScenarioBar — spread
        // these into its opts alongside surface-specific ones.
        scnOpts: {
            envFlush: () => envTab?.flush?.(),
            envDiverged: () => !!(envTab && envTab.diverged()),
            onSwap: () => envTab?.swapped?.(),
            onSaved: () => envTab?.markClean?.(),
        },
        bindBar: (scnState) => {
            if (envTab && scnState) envTab.onCanvasPaint = scnState.updateDot;
        },
    };
}

function itemsPanel(slug, session, initialData) {
    const html = `
        <div class="grs-section-title">\u{1F392} Player starts with</div>
        <div style="opacity:.7;font-size:.85em;margin-bottom:6px">Items in the player's pocket from turn 0 — no room, always carried, live on her next turn. Actions fire anywhere; a look action with a show-image effect makes a poppable keepsake.</div>
        <div class="grs-items-list grs-obj-grid"></div>
        <div class="grs-item-form grs-obj-add-form" style="display:none">
            <input type="text" class="grs-item-name" placeholder="item name, e.g. picture_of_joey">
            <div class="grs-item-mech-note" style="display:none;color:var(--text-secondary,#8a8fa3);font-size:var(--font-sm,0.85em)"></div>
            <input type="text" class="grs-item-desc" placeholder="what looking at it shows her">
            <input type="text" class="grs-item-wears" list="grs-wear-slots" placeholder="wearable? slot name e.g. body (blank = not wearable)">
            <datalist id="grs-wear-slots"><option>hat</option><option>outer</option><option>shirt</option><option>pants</option><option>shoes</option><option>underwear</option><option>in_hand</option><option>bra</option></datalist>
            <div class="grs-item-act-rows"></div>
            <button type="button" class="pk-btn grs-item-act-add">+ Add action</button>
            <div style="display:flex;gap:6px">
                <button type="button" class="pk-btn pk-btn-primary grs-item-save">Add</button>
                <button type="button" class="pk-btn grs-item-cancel">Cancel</button>
                <button type="button" class="pk-btn grs-item-reset" style="display:none" title="Drop your edits — back to the pack's version">↩ Reset to shipped</button>
            </div>
        </div>`;
    const wire = (root) => {
        let shipped = initialData.shipped_items || {};
        let userItems = initialData.user_items || {};
        const list = root.querySelector('.grs-items-list');
        const form = root.querySelector('.grs-item-form');
        if (!list || !form) return;
        const nameIn = form.querySelector('.grs-item-name');
        const descIn = form.querySelector('.grs-item-desc');
        const wearsIn = form.querySelector('.grs-item-wears');
        const actRows = form.querySelector('.grs-item-act-rows');
        const mechNote = form.querySelector('.grs-item-mech-note');
        const resetBtn = form.querySelector('.grs-item-reset');
        let editing = null;
        let authoring = true;
        const addCard = (a) => makeActCard(actRows, a,
            { authoring: () => authoring, pieceList: 'grs-piece-list' });
        form.querySelector('.grs-item-act-add').onclick = () =>
            addCard(null).querySelector('.grs-act-verb').focus();

        const refresh = async () => {
            try {
                const d = await api(`story/objects?session=${encodeURIComponent(session)}&slug=${encodeURIComponent(slug)}`);
                if (d.active !== false) {
                    shipped = d.shipped_items || {};
                    userItems = d.user_items || {};
                }
            } catch { /* keep last state */ }
            paint();
        };
        const closeForm = () => {
            form.style.display = 'none';
            editing = null; authoring = true;
            nameIn.value = ''; nameIn.disabled = false;
            descIn.value = ''; wearsIn.value = ''; actRows.innerHTML = '';
            mechNote.style.display = 'none';
        };
        const readDescriptor = () => ({
            desc: descIn.value.trim(), wears: wearsIn.value.trim(), take: false, hidden: false,
            visCond: null, puzzle: null, solveFx: null,
            acts: [...actRows.querySelectorAll('.grs-act-card')].map(card => {
                const lk = card._lw.readLocks() || {};
                return { verb: card.querySelector('.grs-act-verb').value.trim(),
                         resp: card.querySelector('.grs-act-resp').value.trim(),
                         cond: lk.cond || null, dice: lk.dice || null, sealed: lk.sealed || null,
                         fx: lk.fx || null, msg: lk.msg || '' };
            }).filter(a => a.verb),
        });
        const openForm = (name) => {
            editing = name || null;
            authoring = true;
            actRows.innerHTML = '';
            nameIn.value = editing || '';
            nameIn.disabled = !!editing;
            descIn.value = '';
            wearsIn.value = '';
            mechNote.style.display = 'none';
            resetBtn.style.display = 'none';
            const so = editing ? shipped[editing] : null;
            const ov = editing ? userItems[editing] : null;
            if (so) {
                const merged = objMerged(so.spec || {}, ov);
                if (itemFits(editing, merged)) {
                    const d = objToDescriptor(merged);
                    descIn.value = d.desc;
                    wearsIn.value = d.wears || '';
                    for (const a of d.acts) addCard(a);
                } else {
                    // Text-shadow lane: machinery rides pack-side, read-only.
                    authoring = false;
                    descIn.value = (ov && ov.desc != null) ? ov.desc : (so.desc || '');
                    wearsIn.value = merged.wears || '';
                    for (const [v, m] of Object.entries(so.verbs || {})) {
                        const ovMsg = ov?.interactions?.[v]?.message;
                        addCard({ verb: v, resp: ovMsg != null ? ovMsg : m });
                    }
                    mechNote.textContent = '⚙︎ Story machinery richer than this '
                        + 'editor — shown read-only, your text edits reword it: '
                        + (objMechWords(merged) || 'unnamed machinery');
                    mechNote.style.display = '';
                }
                resetBtn.style.display = ov ? '' : 'none';
            } else if (editing && ov) {
                const d = objToDescriptor(ov);
                descIn.value = d.desc;
                wearsIn.value = d.wears || '';
                for (const a of d.acts) addCard(a);
            }
            form.querySelector('.grs-item-save').textContent = editing ? 'Save' : 'Add';
            form.style.display = '';
            (editing ? descIn : nameIn).focus();
        };

        form.querySelector('.grs-item-cancel').onclick = closeForm;
        resetBtn.onclick = async () => {
            if (!editing) return;
            try {
                await api('story/items/delete', 'POST',
                          { session, slug, name: editing, restore: true });
                closeForm();
                await refresh();
            } catch (e) { ui.showToast(e.message, 'error'); }
        };
        form.querySelector('.grs-item-save').onclick = async () => {
            const name = editing || nameIn.value.trim();
            if (!name) { ui.showToast('Item needs a name', 'error'); return; }
            const so = shipped[name];
            const ovNow = userItems[name];
            const d = readDescriptor();
            const restoreAndClose = async () => {
                try {
                    if (ovNow)
                        await api('story/items/delete', 'POST',
                                  { session, slug, name, restore: true });
                    closeForm();
                    await refresh();
                } catch (e) { ui.showToast(e.message, 'error'); }
            };
            let body;
            if (so && itemFits(name, objMerged(so.spec || {}, ovNow))) {
                const spec = compileObj(name, d, objMerged(so.spec || {}, ovNow));
                if (deepEq(spec, so.spec || {})) { await restoreAndClose(); return; }
                body = { session, slug, name, spec, replace: true };
            } else if (so) {
                const spec = {};
                const desc = descIn.value.trim();
                if (desc !== (so.desc || '')) spec.desc = desc;
                const wv = wearsIn.value.trim();
                if (wv !== (objMerged(so.spec || {}, ovNow).wears || '')) spec.wears = wv;
                const ints = {};
                for (const a of d.acts) {
                    if (a.verb in (so.verbs || {})) {
                        if (a.resp !== so.verbs[a.verb]) ints[a.verb] = { message: a.resp };
                    } else {
                        ints[a.verb] = { message: a.resp || `You ${a.verb} the ${name}.` };
                    }
                }
                if (Object.keys(ints).length) spec.interactions = ints;
                if (!Object.keys(spec).length) { await restoreAndClose(); return; }
                body = { session, slug, name, spec };
            } else {
                body = { session, slug, name, spec: compileObj(name, d, null) };
            }
            try {
                const res = await api('story/items', 'POST', body);
                if (!res.success) { ui.showToast(res.detail || 'refused', 'error'); return; }
                closeForm();
                ui.showToast(`'${name}' is in the kit`, 'success', 2000);
                await refresh();
            } catch (e) { ui.showToast(e.message, 'error'); }
        };

        const paint = () => {
            const cards = [];
            for (const [n, so] of Object.entries(shipped)) {
                const ov = userItems[n];
                if (ov && ov._removed) {
                    cards.push(`<div class="grs-obj-card grs-obj-ghost">
                        <button type="button" class="sb-icon-btn grs-item-restore" data-name="${esc(n)}" title="Bring it back">↩</button>
                        <div class="grs-obj-card-title">${esc(n)} \u{1F4E6}</div>
                        <div class="grs-obj-card-desc">removed from the kit</div>
                    </div>`);
                    continue;
                }
                const merged = objMerged(so.spec || {}, ov);
                const verbs = Object.keys(merged.interactions || {}).join(', ');
                cards.push(`<div class="grs-obj-card grs-obj-editable" data-item="${esc(n)}">
                    <button type="button" class="sb-icon-btn grs-item-del" data-name="${esc(n)}" title="Remove from the kit (restorable)">✕</button>
                    <div class="grs-obj-card-title">${esc(n)} \u{1F4E6}${ov ? ' ✏' : ''}${objMarks(merged)}</div>
                    <div class="grs-obj-card-desc">${esc(merged.desc || '')}</div>
                    ${verbs ? `<div class="grs-obj-card-verbs">${esc(verbs)}</div>` : ''}
                </div>`);
            }
            for (const [n, spec] of Object.entries(userItems)) {
                if (shipped[n]) continue;
                const verbs = Object.keys(spec.interactions || {}).join(', ');
                cards.push(`<div class="grs-obj-card grs-obj-editable" data-item="${esc(n)}">
                    <button type="button" class="sb-icon-btn grs-item-del" data-name="${esc(n)}" title="Remove">✕</button>
                    <div class="grs-obj-card-title">${esc(n)}${spec?._author === 'ai' ? ' \u{1F916}' : ''}${objMarks(spec)}</div>
                    <div class="grs-obj-card-desc">${esc(spec.desc || '')}</div>
                    ${verbs ? `<div class="grs-obj-card-verbs">${esc(verbs)}</div>` : ''}
                </div>`);
            }
            list.innerHTML = cards.join('')
                + '<div class="grs-obj-card grs-obj-addtile grs-item-addtile" role="button" tabindex="0">+ Add item</div>';
            const tile = list.querySelector('.grs-item-addtile');
            tile.onclick = () => openForm();
            tile.onkeydown = (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openForm(); } };
            list.querySelectorAll('[data-item]').forEach(c => c.onclick = (e) => {
                if (e.target.closest('.grs-item-del')) return;
                openForm(c.dataset.item);
            });
            list.querySelectorAll('.grs-item-del').forEach(b => b.onclick = async (e) => {
                e.stopPropagation();
                try {
                    const res = await api('story/items/delete', 'POST',
                                          { session, slug, name: b.dataset.name });
                    if (res.detail) ui.showToast(res.detail, res.success ? 'success' : 'error', 2000);
                    await refresh();
                } catch (e2) { ui.showToast(e2.message, 'error'); }
            });
            list.querySelectorAll('.grs-item-restore').forEach(b => b.onclick = async (e) => {
                e.stopPropagation();
                try {
                    await api('story/items/delete', 'POST',
                              { session, slug, name: b.dataset.name, restore: true });
                    await refresh();
                } catch (e2) { ui.showToast(e2.message, 'error'); }
            });
        };
        paint();
    };
    return { html, wire };
}

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

    // Setup (this run) — editable slot values, one tab per slot section.
    // Sealed blanks are edited from the scene strip's ✍ chips, not here.
    // Scenarios live in the modal-level bar, not a tab.
    const slots = (setup?.slots || []).filter(s => !s.sealed);
    if (setup && slots.length) {
        const st = slotTabs(slots, [], f => (opts.slots || {})[f.key]);
        if (st.length) st[0].html +=
            '<div style="opacity:.7;font-size:.85em">Changes save as you type — live on her next turn. Sealed blanks are edited from the ✍ chips in the scene panel.</div>';
        // AI tools fence (Krem 2026-08-24): per-playthrough, rides the
        // scenario 💾 like slots. Unchecked = the tool leaves her schema
        // entirely (core tools_filter hook — not a "please don't" note).
        if (st.length && opts.active) {
            const fenced = new Set(opts.fence || []);
            const FENCE = [
                ['story_place', 'story_place — author new objects into rooms'],
                ['story_status', 'story_status — read story state on demand'],
                ['story_end', 'story_end — end the story from inside']];
            st[0].html += `<div class="grs-fence-block" style="margin-top:12px">
                <div><b>AI tools</b> — what she may use in this scenario:</div>
                ${FENCE.map(([k, label]) => `<label style="display:block;margin:2px 0">
                    <input type="checkbox" class="grs-fence" data-tool="${k}"${fenced.has(k) ? '' : ' checked'}> ${label}</label>`).join('')}
                <div style="opacity:.7;font-size:.85em">Unchecked = fenced off her toolset this run; saved into the scenario with 💾. story_act (the referee) always stays.</div>
            </div>`;
        }
        tabs.push(...st);
    }

    // World sections — Characters (items + pieces) + Rooms, the ONE
    // assembly shared with the setup modal (2026-08-22). Order ruling
    // (Krem 2026-08-21): Story, Characters, Rooms first; GM + State last.
    const world = worldSections(slug, opts.session, objData);
    world.splice(tabs);
    const envTab = world.envTab;

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

    // State — the old 🔍 inspector as a read-only last tab (Krem 2026-08-21).
    if (opts.state && opts.active) tabs.push(stateTab(opts.state));

    // No header Save/Reset (Krem 2026-08-21: two save buttons = one too
    // many). The gear applies as you edit: schema + this-run slots autosave
    // debounced; rooms/objects/exits already save through their own lanes;
    // the scenario 💾 stays the one DELIBERATE save (a named snapshot).
    let modal = null;
    let saveTimer = null;
    let saveAll = async () => {};        // bound below, needs the overlay
    modal = buildModal(
        `&#x2699;&#xFE0E; ${esc(data.title || slug)} settings`, tabs,
        '',
        setup ? scenarioBarHtml(Object.keys(setup.scenarios || {})) : '',
        { guard: () => {
            // closing COMMITS: in-flight autosave fires now, pending room
            // text flushes (fire-and-forget) — nothing here is discardable.
            if (saveTimer) { clearTimeout(saveTimer); saveTimer = null; saveAll(); }
            if (envTab?.flush) envTab.flush();
            // what's left dirty is a sub-form (typed, unplaced object/exit)
            return !modal.dirty() || confirm('Discard your unsaved changes?');
        } });
    const { overlay, close } = modal;
    const scnState = setup
        ? wireScenarioBar(overlay, slug, setup, slots, opts.slots || {},
                          { session: opts.session,
                            current: objData?.scenario || '',
                            ...world.scnOpts })
        : null;
    world.bindBar(scnState);

    // Deep link (the 🏠 button lands on the Objects tab directly)
    if (opts.tab) {
        const btn = [...overlay.querySelectorAll('.grs-tab')].find(t => t.dataset.tab === opts.tab);
        if (btn) btn.click();
    }

    world.wire(overlay);

    // AI tools fence — applies immediately (own lane, not the autosave
    // debounce: a fence is a rule change, not prose in flight)
    overlay.querySelectorAll('.grs-fence').forEach(cb => cb.onchange = async () => {
        const tools = [...overlay.querySelectorAll('.grs-fence')]
            .filter(c => !c.checked).map(c => c.dataset.tool);
        try {
            const r = await api('story/fence', 'POST', { session: opts.session, tools });
            if (!r.success) throw new Error(r.detail || 'fence failed');
        } catch (e) { ui.showToast(e.message, 'error'); }
    });

    // 👁 full prompt — core's assembly, on demand (Krem 2026-08-23)
    const loadBtn = overlay.querySelector('.grs-prompt-load');
    if (loadBtn) loadBtn.onclick = async () => {
        const view = overlay.querySelector('.grs-prompt-view');
        loadBtn.disabled = true;
        try {
            const pp = await import(`./prompt-preview.js?v=${bootV()}`);
            view.innerHTML = pp.promptPreviewHtml(await pp.fetchPromptPreview(opts.session));
            view.style.display = '';
            loadBtn.textContent = 'Reload full prompt';
        } catch (e) { ui.showToast(e.message, 'error'); }
        loadBtn.disabled = false;
    };

    // ⏹ End story — moved from the toolbar into State (Krem 2026-08-21)
    const endBtn = overlay.querySelector('.grs-end-story');
    if (endBtn && opts.onEndStory) endBtn.onclick = async () => {
        if (!confirm(`End "${opts.storyTitle || slug}"?\n\nThe journal is kept. `
                     + 'The chat returns per your return settings.')) return;
        close();
        await opts.onEndStory();
    };

    // Autosave (debounced): the exact writes the old Save button made —
    // schema settings + this-run slots — minus the close. rebaseline runs
    // at commit time so the close-guard never prompts over saved work.
    // Quiet by design: the ● dot tracks scenario divergence, the scenario
    // 💾 toast is the deliberate-save receipt (Krem's call, 2026-08-21).
    saveAll = async () => {
        saveTimer = null;
        modal.rebaseline();
        const out = {};
        for (const f of schema) {
            const v = readField(overlay, f.key);
            if (v !== undefined) out[f.key] = v;
        }
        try {
            // session rides so the server refreshes THIS chat's costume,
            // not whichever chat is globally active (2026-08-21 hunt, R6)
            await api(`story/${encodeURIComponent(slug)}/settings`, 'POST',
                      { settings: out, session: opts.session });
            if (setup && slots.length) {
                const vals = {};
                for (const s of slots) vals[s.key] = readField(overlay, s.key) ?? '';
                await api('story/slots', 'POST', { session: opts.session, slots: vals });
            }
        } catch (e) { ui.showToast(e.message, 'error'); }
    };
    const queueSave = (e) => {
        // .grs-field = schema + slot inputs only; the rooms pane, the
        // object/exit sub-forms and the scenario bar run their own lanes
        if (!e.target.closest('.grs-field, .grs-custom')) return;
        clearTimeout(saveTimer);
        saveTimer = setTimeout(saveAll, 700);
    };
    overlay.addEventListener('input', queueSave);
    overlay.addEventListener('change', queueSave);
}

// ── Fresh-launch setup (Mad-Libs) ───────────────────────────────────────────
// Story + Environment tabs under the Scenario bar. Resolves {slots, sealed}
// on Start, null on cancel. Callers check needsSetup first.
export async function storyNeedsSetup(slug) {
    try {
        const s = await api(`story/${encodeURIComponent(slug)}/setup`);
        return (s.slots || []).length
            || Object.keys(s.scenarios || {}).length ? s : null;
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
        let done = false;
        const finish = (v) => { if (!done) { done = true; resolve(v); } };

        // ONE source of truth (Prime repro 2026-08-23: dropdown said test2,
        // fields showed defaults): the layer's scenario tag — which the bar
        // preselects from — also seeds the Story-tab fields. Pre-start has
        // no running entry, so the saved scenario's slots ARE the canvas.
        const tag = objData?.scenario || '';
        const tagged = tag ? (setup.scenarios || {})[tag] : null;
        const tabs = slotTabs(open, sealed, f => tagged ? (tagged.slots || {})[f.key] : undefined);
        // World sections — the ONE assembly shared with the in-game gear
        // (2026-08-22): Characters (items + pieces) + Rooms.
        const world = worldSections(slug, session, objData);
        world.splice(tabs);
        const envTab = world.envTab;

        // Draft net (Krem 2026-08-20, three lost setups): every edit lands
        // in sessionStorage; reopening the form — same tab, even after a
        // refresh — restores it. Cleared on ▶ Start or a CONFIRMED discard.
        const draftKey = 'grs-setup-draft:' + slug;
        const clearDraft = () => { try { sessionStorage.removeItem(draftKey); } catch { /* blocked */ } };

        let modal = null;
        modal = buildModal(
            `\u{1F4D6} ${esc(setup.title || slug)} — set the stage`, tabs,
            `<button type="button" class="pk-btn pk-btn-primary grs-start" title="Start the story with this setup">▶ Start</button>
             <button type="button" class="pk-btn grs-cancel">Cancel</button>`,
            scenarioBarHtml(Object.keys(setup.scenarios || {})),
            { guard: () => {
                const d = modal.dirty() || envTab?.dirty?.();
                if (!d) { clearDraft(); return true; }
                if (confirm('Discard this setup? Your entries will be lost.')) {
                    clearDraft();
                    return true;
                }
                return false;
            },
              onClose: () => finish(null) });
        const { overlay, close } = modal;
        world.wire(overlay);
        const scnState = wireScenarioBar(overlay, slug, setup, open, {},
                              { session,
                                current: objData?.scenario || '',
                                ...world.scnOpts });
        world.bindBar(scnState);

        try {
            const d = JSON.parse(sessionStorage.getItem(draftKey) || 'null');
            if (d && Object.keys(d).length) {
                for (const [k, v] of Object.entries(d)) writeField(overlay, k, v);
                ui.showToast('Restored your unsaved setup draft', 'success', 3000);
            }
        } catch { /* corrupt draft — start clean */ }
        const saveDraft = () => {
            const vals = {};
            for (const s of slots) {
                const v = readField(overlay, s.key);
                if (v !== undefined) vals[s.key] = v;
            }
            try { sessionStorage.setItem(draftKey, JSON.stringify(vals)); } catch { /* full */ }
        };
        overlay.addEventListener('input', saveDraft);
        overlay.addEventListener('change', saveDraft);

        overlay.querySelector('.grs-cancel').onclick = modal.tryClose;
        overlay.querySelector('.grs-start').onclick = async () => {
            // Scenario swaps already applied on pick — Start just flushes
            // pending room edits on top.
            try {
                if (envTab?.flush) await envTab.flush();
            } catch (e) {
                ui.showToast('Environment failed to save: ' + e.message, 'error');
                return;
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
            clearDraft();
            finish({ slots: vals, sealed: sealedFills });
        };
    });
}

// ── State tab — the old 🔍 inspector, read-only (Krem 2026-08-21) ──────────
function stateTab(a) {
    const flat = {
        story: a.story, paused: a.paused, ended: a.ended, room: a.room,
        room_id: a.room_id, turn: a.turn, turns_in_room: a.turns_in_room,
        inventory: (a.inventory || []).join(', ') || 'NULL',
        emotions: (a.emotions || []).join(', ') || 'NULL',
        solved: (a.solved || []).join(', ') || 'NULL',
        found: (a.found || []).join(', ') || 'NULL',
        ...(a.flags || {}),
    };
    const rows = Object.entries(flat).map(([k, v]) =>
        `<tr><td style="padding:3px 14px 3px 0;color:var(--text-muted,#8a8fa3)">${esc(k)}</td><td style="padding:3px 0">${esc(String(v))}</td></tr>`).join('');
    return { title: 'State', html: `
        <div class="grs-section-title" style="margin-top:0">Behind the scenes — read-only snapshot at open</div>
        <table style="border-collapse:collapse;font-size:var(--font-sm,13px)">${rows}</table>
        <div class="grs-section-title">\u{1F441} What she gets — verbatim</div>
        <div style="opacity:.7;font-size:.85em;margin-bottom:6px">Core's own assembly for the next turn: persona, custom context, spice, every plugin injection (avatar, anything on this surface), then the per-turn envelope and the tools offered.</div>
        <button type="button" class="pk-btn grs-prompt-load">Load full prompt</button>
        <div class="grs-prompt-view" style="display:none;margin-top:8px"></div>
        <div class="grs-section-title">End of the tale</div>
        <button type="button" class="pk-btn danger grs-end-story" title="The journal is kept; the chat returns per your return settings">⏹ End story</button>` };
}

// ── locks grammar (Krem 2026-08-20; DRYed for the exits editor 2026-08-21):
// (type, value) rows compiling into the referee's SHIPPED grammar. The
// widget is grammar-NEUTRAL: readVis/readLocks return {hidden, cond} /
// {cond, puzzle, dice, fx, msg} descriptors and prefill() takes one back;
// each host form (objects: per-verb interactions; exits: top-level exit
// fields) does its own compile. First row of each kind wins.
// Visible-when = EXISTENCE (she doesn't know it's there — surprise);
// Requirements = USABLE-when (she sees it, it refuses — tension).
const VIS_TYPES = [
    ['searched', 'when searched for', '', ''],
    ['has', 'when carrying item', 'item name, e.g. uv_lamp', ''],
    ['did', 'after another object is used', 'object name, e.g. door1', ''],
    ['flag', 'when a flag is set', 'flag name, e.g. house_open', ''],
    // numbers + time (2026-08-23): the third leg of stats (initial_flags
    // seeds, adjust moves, this checks) and hints-as-objects
    ['gte', 'when a number is at least', 'name, e.g. trust', 'amount, e.g. 3'],
    ['turns', 'after turns in this room', 'turns, e.g. 3', ''],
];
const REQ_TYPES = [
    ['has', 'needs item', 'item name, e.g. bronze_key', ''],
    ['did', 'needs opened/used', 'object name, e.g. door1', ''],
    ['flag', 'flag is set', 'flag name, e.g. ballroom_unlocked', ''],
    ['solved', 'riddle solved', 'object with the riddle — usually this one', ''],
    ['gte', 'number at least', 'name, e.g. trust', 'amount, e.g. 3'],
    ['turns', 'after turns in this room', 'turns, e.g. 3', ''],
    // dice: second slot = the miss line; a "one try" box rides the row
    ['d20', 'd20 chance', 'roll needed, e.g. 11', 'on a miss (optional)'],
    ['d100', 'd100 chance', 'roll needed, e.g. 51', 'on a miss (optional)'],
];
const FX_TYPES = [
    ['set', 'set flag', 'flag name, e.g. ballroom_unlocked', ''],
    ['clear', 'clear flag', 'flag name, e.g. ballroom_unlocked', ''],
    ['give', 'give item', 'item name, e.g. bronze_key', ''],
    ['adjust', 'adjust number', 'name, e.g. love', 'amount, e.g. 10 or -5'],
    ['xadd', 'add prompt piece', 'piece name, e.g. hostile', ''],
    ['xrem', 'remove prompt piece', 'piece name, e.g. hostile', ''],
    ['goto', 'move player to room', 'room number, e.g. 3', ''],
    ['show', 'show image (lightbox)', 'image — 📷 uploads', 'caption (optional)'],
    // Sealed blank (2026-08-23): the player writes the reveal in a popup
    // when she performs the verb. Compiles to the verb's `sealed` block,
    // NOT an fx key — verbs only (exits/solve-fx use FX_BASE). While an
    // ask row exists the action's response box is the hold message.
    ['ask', 'ask the player (popup)', 'the question the popup asks', 'fallback if they skip (optional)'],
];
const FX_BASE = FX_TYPES.filter(t => t[0] !== 'ask');
// Exits: no riddle-solved (a riddle door = a door OBJECT with a riddle;
// the exit then Requires "needs opened/used" on it) and no searched
// (search finds objects; a found lever's flag makes the passage appear).
const EXIT_VIS = VIS_TYPES.filter(t => t[0] !== 'searched');
const EXIT_REQ = REQ_TYPES.filter(t => t[0] !== 'solved');
const EXIT_FAIL_MSG = 'Not this time — the way defeats the attempt.';
const EXIT_MECH = ['condition', 'roll', 'effects', 'visible_when'];

// ── Fidelity gate (2026-08-21): shipped door machinery prefills as
// EDITABLE only when the widget speaks it losslessly — a lossy save would
// silently strip pack grammar (flags dicts, flag_gte, custom roll
// branches, generation). Richer doors show a read-only plain-words
// summary instead; text edits still shadow.
const _condFits = (c) => !c || Object.entries(c).every(([k, v]) =>
    (['has', 'did', 'flag'].includes(k) && typeof v === 'string')
    || (k === 'after_turns' && Number.isFinite(v))
    || (k === 'flag_gte' && !!v && typeof v === 'object'
        && Object.values(v).every(n => typeof n === 'number')));
// show speaks two shapes: bare name, or {image, caption} — nothing richer.
const _showFits = (v) => typeof v === 'string'
    || (!!v && typeof v === 'object' && typeof v.image === 'string'
        && Object.keys(v).every(k => k === 'image' || k === 'caption')
        && (v.caption == null || typeof v.caption === 'string'));
const _fxFits = (f) => !f || Object.entries(f).every(([k, v]) =>
    (k === 'set' && Object.values(v).every(x => x === true || x === false))
    || (k === 'gives' && typeof v === 'string')
    || (k === 'adjust' && Object.values(v).every(x => typeof x === 'number'))
    || ((k === 'extras' || k === 'extras_remove') && Array.isArray(v)
        && v.every(x => typeof x === 'string'))
    || (k === 'goto' && typeof v === 'number')
    || (k === 'show' && _showFits(v)));
// failure = a miss LINE only (failure effects are richer than the row);
// `once` rides as the row's "one try" box
const _rollFits = (r) => !r || (
    (r.sides === 20 || r.sides === 100) && Number.isFinite(r.beat)
    && Object.keys(r).every(k => ['sides', 'beat', 'success', 'failure', 'once'].includes(k))
    && (r.once == null || typeof r.once === 'boolean')
    && _fxFits(r.success)
    && (!r.failure || (typeof r.failure.message === 'string'
                       && Object.keys(r.failure).every(k => k === 'message'))));
const exitMechFits = (se, m) => !se.generate && se.to != null
    && !(m.roll && m.effects)
    && _condFits(m.condition) && _condFits(m.visible_when)
    && _rollFits(m.roll) && _fxFits(m.effects);
// Effective machinery for a shipped row: the shadow's mechanics unit
// wholesale if present, else the pack's own fields.
const exitMech = (se, sh) => (sh && sh.mechanics)
    || Object.fromEntries(EXIT_MECH.filter(k => se[k] != null).map(k => [k, se[k]]));
const exitMarks = (m) => {
    const fxOnRoll = m.roll && m.roll.success
        && Object.keys(m.roll.success).some(k => k !== 'message');
    return (m.visible_when ? ' \u{1F32B}\u{FE0F}' : '') + (m.condition ? ' \u{1F512}' : '')
        + (m.roll ? ' \u{1F3B2}' : '') + (m.effects || fxOnRoll ? ' ⚡' : '');
};
// Plain-words rendering for the read-only case — the old "story mechanics
// ride along" mystery note dies either way.
const _condWords = (c) => Object.entries(c || {}).map(([k, v]) =>
    k === 'has' ? `carrying ${v}` : k === 'did' ? `used ${v}`
    : k === 'flag' ? `flag ${v}` : k === 'solved' ? `solved ${v}`
    : k === 'after_turns' ? `after ${v} turns here`
    : k === 'flag_gte' ? Object.entries(v).map(([f, n]) => `${f} ≥ ${n}`).join(', ')
    : `${k} ${JSON.stringify(v)}`).join(', ');
const exitMechWords = (se, m) => {
    const bits = [];
    if (se.generate || se.to == null) bits.push('leads somewhere unwritten (generation)');
    if (m.visible_when) bits.push(`appears when ${_condWords(m.visible_when)}`);
    if (m.condition) bits.push(`needs ${_condWords(m.condition)}`);
    if (m.roll) bits.push(`\u{1F3B2} d${m.roll.sides || '?'} beat ${m.roll.beat ?? '?'}`);
    const fx = m.effects || (m.roll && m.roll.success) || null;
    if (fx && Object.keys(fx).length) bits.push('⚡ ' + [
        ...Object.keys(fx.set || {}).map(k => `sets ${k}`),
        ...(fx.gives ? [`gives ${fx.gives}`] : []),
        ...Object.entries(fx.adjust || {}).map(([k, n]) => `${k} ${n > 0 ? '+' : ''}${n}`),
        ...(fx.show ? ['\u{1F4F7} shows image'] : [])].join(', '));
    return bits.join(' · ');
};

// ── Objects fidelity gate (2026-08-21, same law as exits — but object
// grammar is BRAIDED: messages live inside the same verb specs as rolls
// and conditions, so instead of shape rules the gate is the round trip
// itself: a shipped object edits live iff compile(descriptor(spec), spec)
// equals spec verbatim. The compile IS the fits check — nothing to drift.
// PASSENGERS: fields the editor neither shows nor edits (per-verb aliases
// + emotions, the object's search rewards) reattach verbatim from the
// source on compile, so they survive a gated save and ride their carrier
// (delete the verb, its passengers go too). `sealed` is NOT a passenger —
// sealed blanks never edit through this lane.
const OBJ_FAIL_MSG = 'Not this time — the attempt fails.';
const OBJ_RIDDLE_DEFAULT = 'It waits for the right answer — solve it.';
const deepEq = (a, b) => {
    if (a === b) return true;
    if (typeof a !== 'object' || typeof b !== 'object' || !a || !b) return false;
    if (Array.isArray(a) !== Array.isArray(b)) return false;
    const ka = Object.keys(a);
    return ka.length === Object.keys(b).length && ka.every(k => deepEq(a[k], b[k]));
};
// shipped spec + user override → the effective object (mirror of the
// server merge law: _replace swaps wholesale; else desc/hidden replace
// and per-verb message overlay)
const objMerged = (sspec, ov) => {
    if (!ov) return sspec || {};
    if (ov._replace) {
        const m = { ...ov };
        delete m._replace;
        delete m._author;
        return m;
    }
    const m = { ...(sspec || {}) };
    if ('desc' in ov) m.desc = ov.desc;
    if ('hidden' in ov) m.hidden = !!ov.hidden;
    if ('wears' in ov) m.wears = ov.wears;
    const ints = { ...(m.interactions || {}) };
    for (const [v, s] of Object.entries(ov.interactions || {})) {
        if (!s || typeof s !== 'object') continue;
        ints[v] = (v in ints)
            ? { ...ints[v], ...('message' in s ? { message: s.message } : {}) }
            : s;
    }
    if (Object.keys(ints).length) m.interactions = ints;
    return m;
};
// spec → the editor's neutral descriptor (what the form fields hold).
// THE FLIP (Krem 2026-08-21): the ACTION is the unit — each verb carries
// its own cond/dice/fx/msg, matching the engine's real per-verb grammar.
// The object owns desc/take/visibility/riddle; solve effects live on the
// riddle (on_solve — the engine fires it at solve time regardless of
// interactions, referee solve path); `solved` is a plain condition key
// (the 'riddle solved' req row), so cross-object solves express too.
const FX_KEYS = ['set', 'gives', 'adjust', 'extras', 'extras_remove', 'goto', 'show'];
const _fxOf = (src) => {
    const fx = {};
    for (const k of FX_KEYS) if (src[k] != null) fx[k] = src[k];
    return Object.keys(fx).length ? fx : null;
};
const _fxWords = (f) => [
    ...Object.entries(f.set || {}).map(([k, v]) => `${v === false ? 'clears' : 'sets'} ${k}`),
    ...(f.gives ? [`gives ${f.gives}`] : []),
    ...Object.entries(f.adjust || {}).map(([k, n]) => `${k} ${n > 0 ? '+' : ''}${n}`),
    ...(f.extras || []).map(e => `+${e}`),
    ...(f.extras_remove || []).map(e => `−${e}`),
    ...(f.goto != null ? [`→ room ${f.goto}`] : []),
    ...(f.show ? [`\u{1F4F7} ${(typeof f.show === 'string' ? '' : f.show.caption) || 'shows image'}`] : [])].join(', ');
const objToDescriptor = (spec) => {
    const puz = spec.puzzle;
    return {
        desc: spec.desc || '', wears: spec.wears || '', take: !!spec.takeable,
        hidden: !!spec.hidden, visCond: spec.condition || null,
        // answers as CSV — one req row, OR inside the field (Krem 2026-08-23)
        puzzle: puz ? { riddle: puz.riddle || OBJ_RIDDLE_DEFAULT,
                        solution: (Array.isArray(puz.solutions) ? puz.solutions : [puz.solution])
                            .filter(x => x != null && x !== '').join(', ') } : null,
        // solve effects only make sense with a riddle to solve; an
        // on_solve on a puzzle-less object is unexpressible → gated out
        solveFx: puz ? _fxOf(spec.on_solve || {}) : null,
        acts: Object.entries(spec.interactions || {})
            .filter(([, s]) => s && typeof s === 'object')
            .map(([verb, s]) => {
                // sealed verb: the player's text IS the response, so the
                // response box carries the hold message instead
                const sl = s.sealed && typeof s.sealed === 'object' ? s.sealed : null;
                return {
                    // verb field = "canonical, alias, alias" (Krem 2026-08-23)
                    verb: [verb, ...(Array.isArray(s.aliases) ? s.aliases : [])].join(', '),
                    resp: sl ? (typeof sl.hold_message === 'string' ? sl.hold_message : '')
                        : (s.message ?? (s.roll && s.roll.success && s.roll.success.message) ?? ''),
                    cond: Object.keys(s.condition || {}).length ? { ...s.condition } : null,
                    dice: s.roll ? { sides: s.roll.sides, beat: s.roll.beat, once: !!s.roll.once,
                                     miss: (s.roll.failure && typeof s.roll.failure.message === 'string')
                                         ? s.roll.failure.message : '' } : null,
                    fx: _fxOf(s.roll ? (s.roll.success || {}) : s),
                    msg: s.blocked_message || '',
                    sealed: sl ? { ask: sl.ask ?? '', fallback: sl.fallback ?? '' } : null,
                };
            }),
    };
};
// descriptor (+ passenger source) → spec. ONE compile for the user-object
// lane (src null), the gated shipped lane, and the fits check.
const compileObj = (name, d, src) => {
    const spec = { desc: d.desc };
    if (d.wears) spec.wears = d.wears;
    if (d.take) spec.takeable = true;
    if (d.hidden) spec.hidden = true;
    if (d.visCond) spec.condition = d.visCond;
    if (d.puzzle) {
        const answers = String(d.puzzle.solution || '').split(',').map(x => x.trim()).filter(Boolean);
        spec.puzzle = { riddle: d.puzzle.riddle,
                        ...(answers.length > 1 ? { solutions: answers } : { solution: answers[0] ?? '' }) };
    }
    if (d.solveFx) spec.on_solve = { ...d.solveFx };
    const acts = {};
    for (const a of d.acts) {
        const names = String(a.verb || '').split(',').map(x => x.trim()).filter(Boolean);
        if (!names.length) continue;
        const verb = names[0];
        const v = {};
        if (names.length > 1) v.aliases = names.slice(1);
        const resp = a.resp || `You ${verb} the ${name}.`;
        if (a.cond && Object.keys(a.cond).length) {
            v.condition = { ...a.cond };
            if (a.msg) v.blocked_message = a.msg;
        }
        if (a.sealed) {
            // the engine answers with the player's words — no message;
            // effects fire at reveal time. A roll never runs on a sealed
            // verb (referee returns at the seal), so none is emitted: a
            // shipped sealed+roll fails the round trip → read-only.
            v.sealed = { ask: a.sealed.ask };
            if (a.resp) v.sealed.hold_message = a.resp;
            if (a.sealed.fallback) v.sealed.fallback = a.sealed.fallback;
            if (a.fx) Object.assign(v, a.fx);
        } else if (a.dice) {
            // chance replaces the flat outcome: response + effects
            // ride the success branch
            v.roll = { sides: a.dice.sides, beat: a.dice.beat,
                       ...(a.dice.once ? { once: true } : {}),
                       success: { message: resp, ...(a.fx || {}) },
                       failure: { message: a.dice.miss || OBJ_FAIL_MSG } };
        } else {
            v.message = resp;
            if (a.fx) Object.assign(v, a.fx);
        }
        acts[verb] = v;
    }
    if (src) {                             // passengers ride their carrier
        for (const k of ['found_by', 'gives'])
            if (src[k] != null) spec[k] = src[k];
        if (src.on_solve) {
            // message/emotions are passengers even when the fx half was
            // edited away — or was never there (message-only on_solve)
            const os = spec.on_solve || {};
            for (const k of ['message', 'emotions', 'emotions_remove'])
                if (src.on_solve[k] != null) os[k] = src.on_solve[k];
            if (Object.keys(os).length) spec.on_solve = os;
        }
        const sints = src.interactions || {};
        for (const [verb, v] of Object.entries(acts)) {
            const sv = sints[verb];
            if (!sv || typeof sv !== 'object') continue;
            // seal countdown is a tuning knob, not authoring — rides verbatim
            if (v.sealed && sv.sealed && typeof sv.sealed === 'object' && sv.sealed.wait != null)
                v.sealed.wait = sv.sealed.wait;
            if (v.roll && sv.roll && typeof sv.roll === 'object') {
                // once + miss line are authored now (2026-08-23); the
                // "spent" line still rides as a passenger
                if (sv.roll.retry_message != null) v.roll.retry_message = sv.roll.retry_message;
                for (const k of ['emotions', 'emotions_remove'])
                    if (sv.roll.success && sv.roll.success[k] != null)
                        v.roll.success[k] = sv.roll.success[k];
            } else {
                for (const k of ['emotions', 'emotions_remove'])
                    if (sv[k] != null) v[k] = sv[k];
            }
        }
    }
    if (Object.keys(acts).length) spec.interactions = acts;
    return spec;
};
// The round trip catches STRUCTURAL extras, but verbatim-passthrough
// leaves (visCond, per-act cond, fx values, dice sides) survive it as
// identity even when the WIDGET can't speak them — the gate passed, the
// user edited, and readLocks silently stripped the rich shape on save
// (2026-08-21 hunt, HIGH-3). These shape rules are the widget's actual
// vocabulary: vis rows has/did/flag, req rows + solved, fx per FX_TYPES,
// dice d20/d100 only. Anything richer → read-only plain-words summary.
const _condFitsO = (c, keys) => !c || Object.entries(c).every(([k, v]) =>
    (keys.includes(k) && typeof v === 'string')
    || (k === 'after_turns' && Number.isFinite(v))
    || (k === 'flag_gte' && !!v && typeof v === 'object'
        && Object.values(v).every(n => typeof n === 'number')));
const _fxFitsO = (f) => !f || Object.entries(f).every(([k, v]) =>
    (k === 'set' && v && typeof v === 'object'
        && Object.values(v).every(x => x === true || x === false))
    || (k === 'gives' && typeof v === 'string')
    || (k === 'adjust' && v && typeof v === 'object'
        && Object.values(v).every(x => typeof x === 'number'))
    || ((k === 'extras' || k === 'extras_remove') && Array.isArray(v)
        && v.every(x => typeof x === 'string'))
    || (k === 'goto' && typeof v === 'number')
    || (k === 'show' && _showFits(v)));
const _diceFitsO = (d) => !d
    || ((d.sides === 20 || d.sides === 100) && Number.isFinite(d.beat));
// ask row = one non-empty question + optional string fallback (the
// round trip already rejects extra seal keys and a dead roll beside it)
const _sealFitsO = (s) => !s
    || (typeof s.ask === 'string' && s.ask.trim() !== '' && typeof s.fallback === 'string');
const objFits = (name, spec) => {
    const d = objToDescriptor(spec);
    return deepEq(compileObj(name, d, spec), spec)
        && _condFitsO(d.visCond, ['has', 'did', 'flag'])
        && _fxFitsO(d.solveFx)
        && d.acts.every(a => _condFitsO(a.cond, ['has', 'did', 'flag', 'solved'])
                             && _fxFitsO(a.fx) && _diceFitsO(a.dice) && _sealFitsO(a.sealed));
};
const objMarks = (spec) => {
    const ints = Object.values(spec.interactions || {}).filter(s => s && typeof s === 'object');
    return (spec.hidden || spec.condition ? ' \u{1F32B}\u{FE0F}' : '')
        + (spec.takeable ? ' \u{1F392}' : '')
        + (spec.puzzle || ints.some(v => v.condition) ? ' \u{1F512}' : '')
        + (ints.some(v => v.roll) ? ' \u{1F3B2}' : '')
        + (ints.some(v => v.sealed) ? ' \u{270D}' : '');
};
const objMechWords = (spec) => {
    const bits = [];
    if (spec.hidden) bits.push('hidden until found');
    if (spec.condition) bits.push(`appears when ${_condWords(spec.condition)}`);
    if (spec.gives) bits.push(`finding gives ${spec.gives}`);
    if (spec.wears) bits.push(`wearable (${spec.wears})`);
    if (spec.puzzle) bits.push('\u{1F9E9} riddle'
        + (spec.puzzle.solutions ? ` (${spec.puzzle.solutions.length} answers)` : ''));
    for (const [v, s] of Object.entries(spec.interactions || {})) {
        if (!s || typeof s !== 'object') continue;
        const b = [];
        if (s.sealed) b.push('holds a sealed reveal');
        if (s.condition) b.push(`needs ${_condWords(s.condition)}`);
        if (s.roll) b.push(`\u{1F3B2} d${s.roll.sides || '?'} beat ${s.roll.beat ?? '?'}`);
        const fx = [...Object.keys(s.set || {}).map(k => `sets ${k}`),
                    ...(s.gives ? [`gives ${s.gives}`] : []),
                    ...Object.entries(s.adjust || {}).map(([k, n]) => `${k} ${n > 0 ? '+' : ''}${n}`),
                    ...((s.emotions || []).length || (s.emotions_remove || []).length
                        ? ['emotions shift'] : [])];
        if (fx.length) b.push('⚡ ' + fx.join(', '));
        if (b.length) bits.push(`${v}: ${b.join('; ')}`);
    }
    return bits.join(' · ');
};

// Shared action-card builder (factored 2026-08-22): one command — verb +
// response — hosting its OWN requirements/dice/effects via locksWidget.
// Used by the Rooms-pane object form and the Characters-tab items panel.
// opts.authoring() gates the ⚙ gear (read-only shipped machinery).
function makeActCard(host, a, opts) {
    const card = document.createElement('div');
    card.className = 'grs-act-card';
    card.innerHTML = `
        <div class="grs-act-row grs-act-main">
            <input type="text" class="grs-act-verb" placeholder="verb(s), e.g. open, unlock, force" title="First word is the command; the rest are synonyms she can also use">
            <input type="text" class="grs-act-resp" placeholder="what the world says back — returned to her as story truth">
            <button type="button" class="sb-icon-btn grs-act-gear" title="Requirements & effects — this action's own locks, dice and changes">\u{2699}\u{FE0E}</button>
            <button type="button" class="sb-icon-btn grs-act-del" title="Remove action">✕</button>
        </div>
        <div class="grs-act-sum" style="display:none"></div>
        <div class="grs-lock-body grs-act-mech" style="display:none">
            <label class="st-tools-check grs-req-label" style="margin:0"><input type="checkbox" class="grs-req-toggle"> Requirements — what it takes to do this</label>
            <div class="grs-lock-body grs-req-body" style="display:none">
                <div class="grs-req-rows"></div>
                <button type="button" class="pk-btn grs-req-add">+ Add requirement</button>
                <input type="text" class="grs-lock-msg" placeholder="blocked message (optional) — what she sees while it refuses">
            </div>
            <label class="st-tools-check grs-fx-label" style="margin:0"><input type="checkbox" class="grs-fx-toggle"> Effects — what doing it changes</label>
            <div class="grs-lock-body grs-fx-body" style="display:none">
                <div class="grs-fx-rows"></div>
                <button type="button" class="pk-btn grs-fx-add">+ Add effect</button>
            </div>
        </div>`;
    const gear = card.querySelector('.grs-act-gear');
    const sum = card.querySelector('.grs-act-sum');
    const mech = card.querySelector('.grs-act-mech');
    const resp = card.querySelector('.grs-act-resp');
    const RESP_PH = resp.placeholder;
    // ask row present → the player's words are the response; the box
    // becomes the hold line she gets while the popup waits
    const relabel = () => {
        const sealed = !!(card._lw && (card._lw.readLocks() || {}).sealed);
        resp.placeholder = sealed
            ? 'hold message (optional) — what she gets while the popup waits for the player'
            : RESP_PH;
        resp.classList.toggle('grs-act-hold', sealed);
    };
    card._lw = locksWidget(card, { types: { req: REQ_TYPES, fx: FX_TYPES },
                                   pieceList: opts.pieceList, onChange: relabel,
                                   selfName: opts.selfName });
    if (a) {
        card.querySelector('.grs-act-verb').value = a.verb || '';
        resp.value = a.resp || '';
        card._lw.prefill({ cond: a.cond, roll: a.dice, fx: a.fx || {}, msg: a.msg,
                           sealed: a.sealed || null });
    }
    // collapsed = a plain-words summary of what's inside
    const paintSum = () => {
        if (mech.style.display !== 'none') { sum.style.display = 'none'; return; }
        const lk = card._lw.readLocks() || {};
        const bits = [];
        if (lk.cond) bits.push('\u{1F512} ' + _condWords(lk.cond));
        if (lk.dice) bits.push(`\u{1F3B2} d${lk.dice.sides} beat ${lk.dice.beat}${lk.dice.once ? ' (one try)' : ''}`);
        if (lk.sealed) bits.push(`\u{270D} player writes: ${lk.sealed.ask}`);
        if (lk.fx) bits.push('⚡ ' + _fxWords(lk.fx));
        sum.textContent = bits.join(' · ');
        sum.style.display = bits.length ? '' : 'none';
    };
    gear.onclick = () => {
        const open = mech.style.display === 'none';
        mech.style.display = open ? '' : 'none';
        gear.classList.toggle('on', open);
        paintSum();
    };
    if (!opts.authoring()) gear.style.display = 'none';
    paintSum();
    card.querySelector('.grs-act-del').onclick = () => card.remove();
    host.appendChild(card);
    return card;
}

function locksWidget(form, opts) {
    const types = opts.types;
    const q = (sel) => form.querySelector(sel);
    // Sections are OPTIONAL (the flip, 2026-08-21): the widget wires
    // whichever of the vis/req/fx blocks exist inside `form` — the object
    // section hosts vis alone, each action card hosts req+fx, the riddle
    // body hosts fx alone, the exit form hosts all three.
    const visToggle = q('.grs-vis-toggle'), reqToggle = q('.grs-req-toggle'), fxToggle = q('.grs-fx-toggle');
    const visLabel = q('.grs-vis-label'), reqLabel = q('.grs-req-label'), fxLabel = q('.grs-fx-label');
    const visBody = q('.grs-vis-body'), reqBody = q('.grs-req-body'), fxBody = q('.grs-fx-body');
    const visRows = q('.grs-vis-rows'), reqRows = q('.grs-req-rows'), fxRows = q('.grs-fx-rows');
    const lockMsg = q('.grs-lock-msg');
    for (const [t, b] of [[visToggle, visBody], [reqToggle, reqBody], [fxToggle, fxBody]])
        if (t) t.onchange = () => { b.style.display = t.checked ? '' : 'none'; sync(); };
    const pickRow = (host, tlist, kind, val, extra, once) => {
        const row = document.createElement('div');
        row.className = 'grs-act-row';
        row.innerHTML = `
            <select class="grs-pick-kind">${tlist.map(t =>
                `<option value="${t[0]}"${t[0] === kind ? ' selected' : ''}>${t[1]}</option>`).join('')}</select>
            <input type="text" class="grs-pick-val">
            <input type="text" class="grs-pick-extra">
            <label class="st-tools-check grs-pick-once-wrap" style="display:none;margin:0;white-space:nowrap" title="The chance is spent after one roll — a miss stays missed"><input type="checkbox" class="grs-pick-once"> one try</label>
            <button type="button" class="sb-icon-btn grs-pick-art" title="Upload image" style="display:none">&#x1F4F7;</button>
            <input type="file" class="grs-pick-file" accept="image/*" style="display:none">
            <button type="button" class="sb-icon-btn grs-act-del" title="Remove">✕</button>`;
        const sel = row.querySelector('.grs-pick-kind');
        const vIn = row.querySelector('.grs-pick-val');
        const xIn = row.querySelector('.grs-pick-extra');
        const artBtn = row.querySelector('.grs-pick-art');
        const fileIn = row.querySelector('.grs-pick-file');
        const onceWrap = row.querySelector('.grs-pick-once-wrap');
        const paint = () => {
            const t = tlist.find(x => x[0] === sel.value) || tlist[0];
            vIn.placeholder = t[2];
            xIn.placeholder = t[3];
            vIn.style.display = t[2] ? '' : 'none';   // value-less kinds (searched)
            xIn.style.display = t[3] ? '' : 'none';
            artBtn.style.display = t[0] === 'show' ? '' : 'none';
            onceWrap.style.display = (t[0] === 'd20' || t[0] === 'd100') ? '' : 'none';
            // Prompt-piece kinds offer the story's pool as suggestions
            if (opts.pieceList && (t[0] === 'xadd' || t[0] === 'xrem'))
                vIn.setAttribute('list', opts.pieceList);
            else vIn.removeAttribute('list');
        };
        // Show-image rows upload straight into the content-hash store (the
        // backdrop lane's twin) — the returned name fills the value field;
        // 🔒 Save is still what applies it.
        artBtn.onclick = () => fileIn.click();
        fileIn.onchange = async () => {
            const f = fileIn.files && fileIn.files[0];
            fileIn.value = '';
            if (!f) return;
            const fd = new FormData();
            fd.append('file', f);
            try {
                const up = await fetch('/api/plugin/game-room/story/art',
                                       { method: 'POST', body: fd });
                const j = await up.json();
                if (!j.success) { ui.showToast(j.detail || 'upload refused', 'error'); return; }
                vIn.value = j.name;
                ui.showToast('Image stored — save the action to apply', 'success', 2200);
            } catch (e) { ui.showToast(e.message, 'error'); }
        };
        sel.onchange = () => {
            paint();
            // picking "riddle solved" on an empty row offers this object's
            // own name — the padlock-on-itself case (Krem 2026-08-23)
            if (sel.value === 'solved' && !vIn.value.trim() && opts.selfName) {
                const self = opts.selfName();
                if (self) vIn.value = self;
            }
            sync();
        };
        paint();
        vIn.value = val || '';
        xIn.value = extra || '';
        row.querySelector('.grs-pick-once').checked = !!once;
        row.querySelector('.grs-act-del').onclick = () => { row.remove(); sync(); };
        host.appendChild(row);
        sync();
        return row;
    };
    // One outcome per verb: a sealed reveal and a dice roll can't share an
    // action (the engine answers at the seal and never rolls), so each
    // kind's option greys out in the dropdowns while the other is picked.
    // Also the hosts' change hook (the action card relabels its response
    // box as the hold message while an ask row stands).
    const sync = () => {
        if (reqRows && fxRows) {
            const hasAsk = rawRows(fxRows).some(x => x.kind === 'ask');
            const hasDice = rawRows(reqRows).some(x => x.kind === 'd20' || x.kind === 'd100');
            for (const o of reqRows.querySelectorAll('option[value="d20"],option[value="d100"]'))
                o.disabled = hasAsk && !o.selected;
            for (const o of fxRows.querySelectorAll('option[value="ask"]'))
                o.disabled = hasDice && !o.selected;
        }
        if (opts.onChange) opts.onChange();
    };
    for (const [sel, host, tlist, focus] of [
        ['.grs-vis-add', visRows, types.vis, '.grs-pick-kind'],
        ['.grs-req-add', reqRows, types.req, '.grs-pick-val'],
        ['.grs-fx-add', fxRows, types.fx, '.grs-pick-val']]) {
        const btn = q(sel);
        if (btn) btn.onclick = () => pickRow(host, tlist).querySelector(focus).focus();
    }

    const rawRows = (host) => [...host.querySelectorAll('.grs-act-row')].map(row => ({
        kind: row.querySelector('.grs-pick-kind').value,
        val: row.querySelector('.grs-pick-val').value.trim(),
        extra: row.querySelector('.grs-pick-extra').value.trim(),
        once: !!row.querySelector('.grs-pick-once')?.checked,
    }));
    const rowsOf = (host) => rawRows(host).filter(x => x.val);

    const clear = () => {
        for (const t of [visToggle, reqToggle, fxToggle].filter(Boolean)) t.checked = false;
        for (const b of [visBody, reqBody, fxBody].filter(Boolean)) b.style.display = 'none';
        for (const l of [visLabel, reqLabel, fxLabel].filter(Boolean)) l.style.display = '';
        for (const h of [visRows, reqRows, fxRows].filter(Boolean)) h.innerHTML = '';
        if (lockMsg) lockMsg.value = '';
        sync();
    };
    const showAuthoring = (on) => {
        for (const l of [visLabel, reqLabel, fxLabel].filter(Boolean)) l.style.display = on ? '' : 'none';
        if (!on) for (const b of [visBody, reqBody, fxBody].filter(Boolean)) b.style.display = 'none';
    };

    // Visible-when rows → the existence gate: `hidden` (search reveal)
    // and/or a condition {has, did, flag}.
    const readVis = () => {
        if (!visToggle || !visToggle.checked) return null;
        const out = { hidden: false, cond: {} };
        for (const x of rawRows(visRows)) {
            if (x.kind === 'searched') out.hidden = true;
            else if (!x.val) continue;
            else if (x.kind === 'has' && !out.cond.has) out.cond.has = x.val;
            else if (x.kind === 'did' && !out.cond.did) out.cond.did = x.val;
            else if (x.kind === 'flag' && !out.cond.flag) out.cond.flag = x.val;
            else if (x.kind === 'gte') {
                const n = parseFloat(x.extra);
                if (Number.isFinite(n)) (out.cond.flag_gte = out.cond.flag_gte || {})[x.val] = n;
            } else if (x.kind === 'turns' && out.cond.after_turns == null) {
                const n = parseInt(x.val, 10);
                if (Number.isFinite(n)) out.cond.after_turns = Math.max(0, n);
            }
        }
        if (!Object.keys(out.cond).length) out.cond = null;
        return (out.hidden || out.cond) ? out : null;
    };

    const readLocks = () => {
        const reqOn = reqToggle && reqToggle.checked;
        const fxOn = fxToggle && fxToggle.checked;
        if (!reqOn && !fxOn) return null;
        const cond = {};
        let dice = null;
        if (reqOn) for (const x of rowsOf(reqRows)) {
            if (x.kind === 'has' && !cond.has) cond.has = x.val;
            else if (x.kind === 'did' && !cond.did) cond.did = x.val;
            else if (x.kind === 'flag' && !cond.flag) cond.flag = x.val;
            else if (x.kind === 'solved' && !cond.solved) cond.solved = x.val;
            else if (x.kind === 'gte') {
                const n = parseFloat(x.extra);
                if (Number.isFinite(n)) (cond.flag_gte = cond.flag_gte || {})[x.val] = n;
            } else if (x.kind === 'turns' && cond.after_turns == null) {
                const n = parseInt(x.val, 10);
                if (Number.isFinite(n)) cond.after_turns = Math.max(0, n);
            } else if ((x.kind === 'd20' || x.kind === 'd100') && !dice) {
                const sides = x.kind === 'd20' ? 20 : 100;
                const beat = parseInt(x.val, 10);
                dice = { sides, beat: Number.isFinite(beat)
                         ? Math.max(1, Math.min(sides, beat)) : sides / 2 + 1,
                         once: !!x.once, miss: x.extra };
            }
        }
        const fx = {};
        let sealed = null;
        if (fxOn) for (const x of rowsOf(fxRows)) {
            if (x.kind === 'ask') { if (!sealed) sealed = { ask: x.val, fallback: x.extra }; }
            else if (x.kind === 'set') (fx.set = fx.set || {})[x.val] = true;
            else if (x.kind === 'clear') (fx.set = fx.set || {})[x.val] = false;
            else if (x.kind === 'give' && !fx.gives) fx.gives = x.val;
            else if (x.kind === 'adjust') {
                const n = parseFloat(x.extra);
                if (Number.isFinite(n)) (fx.adjust = fx.adjust || {})[x.val] = n;
            } else if (x.kind === 'xadd') {
                if (!(fx.extras || []).includes(x.val)) (fx.extras = fx.extras || []).push(x.val);
            } else if (x.kind === 'xrem') {
                if (!(fx.extras_remove || []).includes(x.val)) (fx.extras_remove = fx.extras_remove || []).push(x.val);
            } else if (x.kind === 'goto' && fx.goto == null) {
                const n = parseInt(x.val, 10);
                if (Number.isFinite(n)) fx.goto = n;
            } else if (x.kind === 'show' && fx.show == null) {
                fx.show = x.extra ? { image: x.val, caption: x.extra } : x.val;
            }
        }
        const out = { cond: Object.keys(cond).length ? cond : null,
                      dice: sealed ? null : dice,     // one outcome per verb
                      sealed,
                      fx: Object.keys(fx).length ? fx : null,
                      msg: lockMsg ? lockMsg.value.trim() : '' };
        return (out.cond || out.dice || out.fx || out.sealed) ? out : null;
    };

    // descriptor → rows (edit round-trip)
    const prefill = (d) => {
        let anyReq = false, anyFx = false, anyVis = false;
        if (visRows) {
            if (d.hidden) { pickRow(visRows, types.vis, 'searched'); anyVis = true; }
            const tc = d.visCond || {};
            if (tc.has) { pickRow(visRows, types.vis, 'has', tc.has); anyVis = true; }
            if (tc.did) { pickRow(visRows, types.vis, 'did', tc.did); anyVis = true; }
            if (tc.flag) { pickRow(visRows, types.vis, 'flag', tc.flag); anyVis = true; }
            for (const [f, n] of Object.entries(tc.flag_gte || {})) { pickRow(visRows, types.vis, 'gte', f, String(n)); anyVis = true; }
            if (tc.after_turns != null) { pickRow(visRows, types.vis, 'turns', String(tc.after_turns)); anyVis = true; }
        }
        if (reqRows) {
            const cond = d.cond || {};
            if (cond.has) { pickRow(reqRows, types.req, 'has', cond.has); anyReq = true; }
            if (cond.did) { pickRow(reqRows, types.req, 'did', cond.did); anyReq = true; }
            if (cond.flag) { pickRow(reqRows, types.req, 'flag', cond.flag); anyReq = true; }
            if (cond.solved) { pickRow(reqRows, types.req, 'solved', cond.solved); anyReq = true; }
            for (const [f, n] of Object.entries(cond.flag_gte || {})) { pickRow(reqRows, types.req, 'gte', f, String(n)); anyReq = true; }
            if (cond.after_turns != null) { pickRow(reqRows, types.req, 'turns', String(cond.after_turns)); anyReq = true; }
            if (d.roll) {
                pickRow(reqRows, types.req, d.roll.sides === 20 ? 'd20' : 'd100',
                        String(d.roll.beat ?? ''),
                        d.roll.miss ?? (d.roll.failure && d.roll.failure.message) ?? '',
                        !!d.roll.once);
                anyReq = true;
            }
        }
        if (fxRows) {
            const src = d.fx || {};
            for (const [k, v] of Object.entries(src.set || {})) {
                pickRow(fxRows, types.fx, v === false ? 'clear' : 'set', k);
                anyFx = true;
            }
            if (src.gives) { pickRow(fxRows, types.fx, 'give', src.gives); anyFx = true; }
            for (const [k, n] of Object.entries(src.adjust || {})) {
                pickRow(fxRows, types.fx, 'adjust', k, String(n));
                anyFx = true;
            }
            for (const e of (src.extras || [])) { pickRow(fxRows, types.fx, 'xadd', e); anyFx = true; }
            for (const e of (src.extras_remove || [])) { pickRow(fxRows, types.fx, 'xrem', e); anyFx = true; }
            if (src.goto != null) { pickRow(fxRows, types.fx, 'goto', String(src.goto)); anyFx = true; }
            if (src.show != null) {
                const sh = typeof src.show === 'string' ? { image: src.show } : src.show;
                pickRow(fxRows, types.fx, 'show', sh.image || '', sh.caption || '');
                anyFx = true;
            }
            // ask row first so the seal leads the list the player reads
            if (d.sealed) {
                fxRows.prepend(pickRow(fxRows, types.fx, 'ask', d.sealed.ask, d.sealed.fallback || ''));
                anyFx = true;
            }
        }
        // A refusal message alone is dead data — it only shows when a
        // lock fails. Stage it in the field (recoverable if a lock is
        // re-added) but let real locks own the checkbox; counting it made
        // Requirements re-check on stripped doors (Krem 2026-08-21).
        if (lockMsg && d.msg) lockMsg.value = d.msg;
        for (const [t, b, on] of [[visToggle, visBody, anyVis],
                                  [reqToggle, reqBody, anyReq],
                                  [fxToggle, fxBody, anyFx]]) {
            if (!t) continue;
            t.checked = on;
            b.style.display = on ? '' : 'none';
        }
        sync();                            // toggles just changed → hosts relabel
    };

    return { readVis, readLocks, prefill, clear, showAuthoring };
}

// ── Environment tab (the open-world editor) ─────────────────────────────────
function objectsTab(slug, session, data) {
    const rooms = data.rooms || [];
    const authorOf = (spec) => spec?._author === 'ai' ? ' \u{1F916}' : '';
    const curId = data.current_room;
    const html = `
        <div class="grs-section-title grs-room-head" style="margin-top:0">Room
            <span class="grs-find-chip" role="button" tabindex="0" title="Find an object, exit or room anywhere in this story">&#x1F50E; Search</span>
            <span class="grs-find-wrap" style="display:none">
                <input type="text" class="grs-find-input" placeholder="object, exit or room…">
                <div class="grs-find-results" style="display:none"></div>
            </span>
        </div>
        <div class="grs-room-row">
            <select class="grs-obj-room">
                ${rooms.map(r => `<option value="${r.id}"${r.id === curId ? ' selected' : ''}>${r.id === curId ? '\u{1F4CD} ' : ''}${esc(r.title)}${r.id === curId ? ' — you are here' : ''}</option>`).join('')}
            </select>
            <button type="button" class="sb-icon-btn grs-room-del" style="display:none" title="Remove this room — its doors and objects go with it">✕</button>
            <div class="grs-room-stats">
                <div class="grs-room-stats-head">In this room</div>
                <div class="grs-room-stats-line"></div>
                <div class="grs-room-stats-line grs-room-stats-from"></div>
            </div>
        </div>
        <div class="grs-room-desc-row">
            <div class="sb-field sb-field-stack grs-room-desc">
                <label>Full description (<span class="grs-obj-count">0</span>/900)</label>
                <textarea class="grs-obj-template" rows="6" title="what she reads — the room's reality"></textarea>
            </div>
            <div class="grs-bd-col">
                <img class="grs-room-thumb" style="display:none" alt="room image" title="the room's art">
                <div class="grs-bd-controls">
                    <select class="grs-bd-pick" title="pick from this story's art"></select>
                    <button type="button" class="sb-icon-btn grs-bd-upload" title="Upload an image — recompressed to webp, stored once no matter how many rooms use it">\u{2B06}</button>
                    <button type="button" class="sb-icon-btn grs-bd-reset" title="Back to the story's shipped art" style="display:none">\u{21A9}</button>
                    <input type="file" class="grs-bd-file" accept="image/*" style="display:none">
                </div>
            </div>
        </div>
        <div class="sb-field sb-field-stack">
            <label>Short description</label>
            <textarea class="grs-obj-pdesc" rows="2" title="the one-liner in your scene strip"></textarea>
        </div>
        <div class="grs-enter sb-field sb-field-stack">
            <label class="st-tools-check grs-fx-label" style="margin:0"><input type="checkbox" class="grs-fx-toggle"> On entering — what arriving here changes (flags, prompt pieces, an image…)</label>
            <div class="grs-enter-note" style="display:none;color:var(--text-secondary,#8a8fa3);font-size:var(--font-sm,0.85em)"></div>
            <div class="grs-lock-body grs-fx-body" style="display:none">
                <div class="grs-fx-rows"></div>
                <div style="display:flex;gap:6px;flex-wrap:wrap">
                    <button type="button" class="pk-btn grs-fx-add">+ Add effect</button>
                    <button type="button" class="pk-btn pk-btn-primary grs-enter-save">Save on-entry</button>
                    <button type="button" class="pk-btn grs-enter-reset" style="display:none" title="Drop your edits — back to the pack's on-entry effects">↩ Reset to shipped</button>
                </div>
            </div>
        </div>
        <div class="sb-field sb-field-stack">
            <div class="grs-exit-head">
                <label>Exits</label>
                <span class="grs-exit-badge grs-exit-addchip" role="button" tabindex="0">+ Add exit</span>
            </div>
            <div class="grs-exit-badges"></div>
        </div>
        <datalist id="grs-piece-list"></datalist>
        <div class="grs-exit-form grs-obj-add-form" style="display:none">
            <div style="display:flex;gap:6px">
                <select class="grs-ex-to" title="where this way leads"></select>
                <input type="text" class="grs-ex-label" placeholder="what the player calls it, e.g. oak door">
            </div>
            <div class="grs-ex-mech-note" style="display:none;color:var(--text-secondary,#8a8fa3);font-size:var(--font-sm,0.85em)">\u{2699}\u{FE0E} This door has story mechanics — your edits reword it; the machinery stays.</div>
            <input type="text" class="grs-ex-desc" placeholder="short description — what the way looks like">
            <label class="st-tools-check grs-vis-label" style="margin:0"><input type="checkbox" class="grs-vis-toggle"> Visible when — until then the way doesn't exist</label>
            <div class="grs-lock-body grs-vis-body" style="display:none">
                <div class="grs-vis-rows"></div>
                <button type="button" class="pk-btn grs-vis-add">+ Add condition</button>
            </div>
            <label class="st-tools-check grs-req-label" style="margin:0"><input type="checkbox" class="grs-req-toggle"> Requirements — what it takes to pass</label>
            <div class="grs-lock-body grs-req-body" style="display:none">
                <div class="grs-req-rows"></div>
                <button type="button" class="pk-btn grs-req-add">+ Add requirement</button>
                <input type="text" class="grs-lock-msg" placeholder="blocked message (optional) — what she sees while the way refuses">
            </div>
            <label class="st-tools-check grs-fx-label" style="margin:0"><input type="checkbox" class="grs-fx-toggle"> Effects — what passing through changes</label>
            <div class="grs-lock-body grs-fx-body" style="display:none">
                <div class="grs-fx-rows"></div>
                <button type="button" class="pk-btn grs-fx-add">+ Add effect</button>
            </div>
            <label class="st-tools-check grs-ex-return-label" style="margin:0"><input type="checkbox" class="grs-ex-return"> Also add the return exit (one-time — the two sides stay independent after)</label>
            <div style="display:flex;gap:6px">
                <button type="button" class="pk-btn pk-btn-primary grs-ex-save">Add</button>
                <button type="button" class="pk-btn grs-ex-cancel">Cancel</button>
                <button type="button" class="pk-btn grs-ex-reset" style="display:none" title="Drop your edits — back to the pack's door">↩ Reset to shipped</button>
            </div>
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
            <button type="button" class="pk-btn grs-act-add" title="One more command it answers to — 'eat' → 'You shrink to very small.'">+ Add action</button>
            <label class="st-tools-check grs-take-label" style="margin:0"><input type="checkbox" class="grs-obj-take"> Can be picked up — goes into her inventory and leaves the room</label>
            <div class="grs-obj-vis">
                <label class="st-tools-check grs-vis-label" style="margin:0"><input type="checkbox" class="grs-vis-toggle"> Visible when — until then it doesn't exist for her</label>
                <div class="grs-lock-body grs-vis-body" style="display:none">
                    <div class="grs-vis-rows"></div>
                    <button type="button" class="pk-btn grs-vis-add">+ Add condition</button>
                </div>
            </div>
            <label class="st-tools-check grs-rid-label" style="margin:0"><input type="checkbox" class="grs-rid-toggle"> Riddle — a puzzle she can solve by answering</label>
            <div class="grs-lock-body grs-rid-body" style="display:none">
                <input type="text" class="grs-rid-text" placeholder="the riddle / prompt she sees">
                <input type="text" class="grs-rid-answer" placeholder="answer(s), e.g. 1234 — comma-separated if more than one">
                <div class="grs-obj-ridfx">
                    <label class="st-tools-check grs-fx-label" style="margin:0"><input type="checkbox" class="grs-fx-toggle"> Solve effects — what solving it changes</label>
                    <div class="grs-lock-body grs-fx-body" style="display:none">
                        <div class="grs-fx-rows"></div>
                        <button type="button" class="pk-btn grs-fx-add">+ Add effect</button>
                    </div>
                </div>
            </div>
            <div style="display:flex;gap:6px">
                <button type="button" class="pk-btn pk-btn-primary grs-obj-place">Place</button>
                <button type="button" class="pk-btn grs-obj-cancel">Cancel</button>
                <button type="button" class="pk-btn grs-obj-reset" style="display:none" title="Drop your edits — back to the pack's version">↩ Reset to shipped</button>
            </div>
        </div>`;

    const tab = { title: 'Rooms', html };
    tab.init = (pane) => {
        let world = data;
        // Room-text edits are PENDING until the modal's Save/▶ Start (or a
        // preset save) flushes them — no third save button (Krem 2026-08-20).
        // Pending survives room switches; closing the modal discards it.
        // Exits save IMMEDIATELY through their own form (2026-08-21), same
        // as objects — no pending lane.
        const pending = {};
        const roomSel = pane.querySelector('.grs-obj-room');
        const tArea = pane.querySelector('.grs-obj-template');
        const pArea = pane.querySelector('.grs-obj-pdesc');
        const count = pane.querySelector('.grs-obj-count');
        const curRoom = () => world.rooms.find(r => String(r.id) === roomSel.value) || world.rooms[0];

        const stash = () => {
            const r = curRoom();
            if (!r) return;
            const savedT = r.template || r.shipped_template || '';
            const savedP = r.player_desc || r.shipped_player_desc || '';
            if (tArea.value === savedT && pArea.value === savedP) delete pending[r.id];
            else pending[r.id] = { template: tArea.value, player_desc: pArea.value };
        };

        tab.flush = async () => {
            for (const rid of Object.keys(pending)) {
                await api('story/room-text', 'POST',
                          { session, slug, room_id: Number(rid),
                            template: pending[rid].template,
                            player_desc: pending[rid].player_desc });
            }
            Object.keys(pending).forEach(k => delete pending[k]);
        };
        tab.dirty = () => !!Object.keys(pending).length;
        tab.hasContent = () =>
            Object.values(world.objects || {}).some(o => Object.keys(o || {}).length)
            || (world.rooms || []).some(r => r.template || r.player_desc
                                             || r.backdrop_override
                                             || (r.add_exits || []).length
                                             || Object.keys(r.exit_shadows || {}).length);
        // Divergence, not existence (Krem 2026-08-20: "prompt only when the
        // current values don't match the loaded scenario"). markClean stamps
        // the canvas right after a swap/save; diverged() compares against it.
        // Before any stamp the baseline is unknown — protect if content.
        const canvasFp = () => JSON.stringify([
            world.objects || {},
            (world.rooms || []).map(r => [r.template || '', r.player_desc || '',
                                          r.backdrop_override || '',
                                          r.add_exits || [], r.exit_shadows || {}]),
            pending]);
        let cleanFp = null;
        tab.markClean = () => { cleanFp = canvasFp(); };
        tab.diverged = () => cleanFp === null
            ? (tab.hasContent() || tab.dirty())
            : canvasFp() !== cleanFp;
        // A scenario swap replaced the canvas: pendings are stale, repaint,
        // and the fresh canvas IS the scenario — stamp it clean.
        tab.swapped = async () => {
            Object.keys(pending).forEach(k => delete pending[k]);
            await refresh();
            tab.markClean();
        };

        // Effective object view — the card/form face of objMerged (2026-
        // 08-21: rows now carry the full shipped spec, replace shadows and
        // roll-success messages render truthfully).
        const effective = (so, ov) => {
            const m = objMerged(so?.spec || {}, ov);
            const verbs = {};
            for (const [v, s] of Object.entries(m.interactions || {}))
                if (s && typeof s === 'object')
                    verbs[v] = String(s.message
                        ?? (s.roll && s.roll.success && s.roll.success.message) ?? '');
            return { desc: m.desc ?? '', verbs, merged: m };
        };
        // W2: options rebuild every paint — user rooms appear/disappear
        // live; selection survives; "+ New room…" rides at the bottom.
        // ── On-entry effects (2026-08-23) — the room's own fx widget; the
        // layer stores the block WHOLESALE ({_clear} strips a shipped one,
        // verbatim-equals-shipped drops the override). Immediate save.
        const enterForm = pane.querySelector('.grs-enter');
        const enterNote = enterForm.querySelector('.grs-enter-note');
        const enterSave = enterForm.querySelector('.grs-enter-save');
        const enterReset = enterForm.querySelector('.grs-enter-reset');
        const ew = locksWidget(enterForm, { types: { fx: FX_BASE }, pieceList: 'grs-piece-list' });
        const effEnter = (r) => r.on_enter
            ? (r.on_enter._clear ? null : r.on_enter)
            : (r.shipped_on_enter || null);
        const paintEnter = (r) => {
            ew.clear();
            const eff = effEnter(r);
            enterReset.style.display = r.on_enter ? '' : 'none';
            if (eff && !_fxFitsO(eff)) {
                ew.showAuthoring(false);
                enterNote.textContent = '\u{2699}\u{FE0E} Shipped on-entry machinery richer than this editor — '
                    + 'read-only: ' + (_fxWords(eff) || 'unnamed effects');
                enterNote.style.display = '';
                return;
            }
            ew.showAuthoring(true);
            enterNote.style.display = 'none';
            if (eff) ew.prefill({ fx: eff });
        };
        enterSave.onclick = async () => {
            const r = curRoom();
            if (!r) return;
            const fx = (ew.readLocks() || {}).fx || null;
            const shipped = r.shipped_on_enter || null;
            let body;
            if (fx && shipped && deepEq(fx, shipped)) body = null;          // verbatim = reset
            else if (!fx) body = shipped ? { _clear: true } : null;        // nothing = strip / reset
            else body = fx;
            try {
                const res = await api('story/room-enter', 'POST',
                                      { session, slug, room_id: r.id, on_enter: body });
                if (!res.success) { ui.showToast(res.detail || 'save failed', 'error'); return; }
                r.on_enter = body;
                paintEnter(r);
                ui.showToast(res.detail || 'Saved', 'success', 1800);
            } catch (e) { ui.showToast(e.message, 'error'); }
        };
        enterReset.onclick = async () => {
            const r = curRoom();
            if (!r) return;
            try {
                const res = await api('story/room-enter', 'POST',
                                      { session, slug, room_id: r.id, on_enter: null });
                if (!res.success) { ui.showToast(res.detail || 'reset failed', 'error'); return; }
                r.on_enter = null;
                paintEnter(r);
            } catch (e) { ui.showToast(e.message, 'error'); }
        };

        const paintRoomOptions = () => {
            const cur = roomSel.value;
            const curId = world.current_room;
            roomSel.innerHTML = (world.rooms || []).map(r =>
                `<option value="${r.id}">${r.id === curId ? '\u{1F4CD} ' : ''}${r.user_room ? '\u{1F3D7}\u{FE0F} ' : ''}${esc(r.title)}${r.id === curId ? ' — you are here' : ''}</option>`).join('')
                + '<option value="__new__">\u{2795} New room\u{2026}</option>';
            if (cur && [...roomSel.options].some(o => o.value === cur))
                roomSel.value = cur;
            else if (roomSel.options.length > 1)
                roomSel.selectedIndex = 0;
        };
        const paintRoom = () => {
            paintRoomOptions();
            const r = curRoom();
            if (!r) return;
            roomSel.dataset.prev = String(r.id);
            pane.querySelector('.grs-room-del').style.display =
                r.user_room ? '' : 'none';
            const px = pending[r.id];
            tArea.value = px ? px.template : (r.template || r.shipped_template || '');
            pArea.value = px ? px.player_desc : (r.player_desc || r.shipped_player_desc || '');
            count.textContent = tArea.value.length;
            const thumb = pane.querySelector('.grs-room-thumb');
            if (r.backdrop) { thumb.src = r.backdrop; thumb.style.display = ''; }
            else { thumb.style.display = 'none'; }
            // Backdrop controls (W1): dropdown = this story's art palette
            // (pack files + this room's upload); ↩ only when overridden.
            const bdPick = pane.querySelector('.grs-bd-pick');
            const eff = r.backdrop_file || '';
            const isStore = /^[0-9a-f]{16}\.webp$/.test(eff);
            const opts = [];
            if (!eff) opts.push('<option value="" selected>no art</option>');
            for (const f of (world.pack_backdrops || []))
                opts.push(`<option value="${esc(f)}"${f === eff ? ' selected' : ''}>${esc(f)}</option>`);
            if (isStore)
                opts.push(`<option value="${esc(eff)}" selected>uploaded ${esc(eff.slice(0, 6))}…</option>`);
            bdPick.innerHTML = opts.join('');
            pane.querySelector('.grs-bd-reset').style.display =
                r.backdrop_override ? '' : 'none';
            pane.querySelector('#grs-piece-list').innerHTML =
                Object.keys(world.pieces || {}).map(n => `<option value="${esc(n)}">`).join('');
            paintEnter(r);
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
                    <div class="grs-obj-card-title">${esc(n)} \u{1F4E6}${ov ? ' ✏' : ''}${objMarks(eff.merged)}</div>
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
                    <div class="grs-obj-card-title">${esc(n)}${authorOf(spec)}${objMarks(spec)}</div>
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

            // Exits (exits editor 2026-08-21): shipped badges are editable
            // (✏ shadow, ✕ tombstone, ghost ↩ restore) and user-added ones
            // carry the full grammar — same laws as the object cards.
            const added = r.add_exits || [];
            const shadows = r.exit_shadows || {};
            const badges = pane.querySelector('.grs-exit-badges');
            // ONE ground truth for names (Krem 2026-08-20): badges show the
            // DESTINATION ROOM'S TITLE — same names as the Room dropdown.
            // The author's flavor label ("the parlor door") lives in the
            // tooltip; in play she still moves by that label.
            const roomTitle = (id) => (world.rooms.find(x => x.id === id) || {}).title;
            let exitCount = 0;
            const chips = [];
            for (const e of (r.shipped_exits || [])) {
                // Unwritten doors (generation) — inert badge, nothing to
                // edit or tombstone by destination yet.
                if (e.to == null || e.generate) {
                    exitCount += 1;
                    chips.push(`<span class="grs-exit-badge" title="unwritten — room generation arrives in a future build">${esc(e.label || '???')} \u{1F6A7}</span>`);
                    continue;
                }
                const sh = shadows[String(e.to)];
                if (sh && sh._removed) {
                    chips.push(`<span class="grs-exit-badge grs-exit-ghost" title="walled off">${esc(roomTitle(e.to) || e.label || e.to)}
                        <button type="button" class="grs-exit-x grs-exit-restore" data-to="${e.to}" title="Bring the way back">↩</button></span>`);
                    continue;
                }
                exitCount += 1;
                const label = (sh && sh.label) || e.label || '';
                chips.push(`<span class="grs-exit-badge grs-exit-editable" data-to="${e.to}" title="${esc(label)}">${esc(roomTitle(e.to) || label || e.to)}${sh ? ' ✏' : ''}${exitMarks(exitMech(e, sh))}
                    <button type="button" class="grs-exit-x" data-to="${e.to}" title="Wall this way off (restorable)">✕</button></span>`);
            }
            for (const e of added) {
                exitCount += 1;
                chips.push(`<span class="grs-exit-badge grs-exit-user grs-exit-editable" data-to="${e.to}" title="${esc(e.label || '')}">${esc(roomTitle(e.to) || e.label)}${exitMarks(e)}
                    <button type="button" class="grs-exit-x" data-to="${e.to}" title="Remove this exit">✕</button></span>`);
            }
            badges.innerHTML = chips.join('');
            badges.querySelectorAll('.grs-exit-editable').forEach(c => c.onclick = (e) => {
                if (e.target.closest('.grs-exit-x')) return;
                openExit(Number(c.dataset.to));
            });
            badges.querySelectorAll('.grs-exit-x:not(.grs-exit-restore)').forEach(b => b.onclick = async () => {
                try {
                    const res = await api('story/exits/delete', 'POST',
                        { session, slug, room_id: r.id, to: Number(b.dataset.to) });
                    if (res.detail) ui.showToast(res.detail, res.success ? 'success' : 'error', 2000);
                    await refresh();
                } catch (e2) { ui.showToast(e2.message, 'error'); }
            });
            badges.querySelectorAll('.grs-exit-restore').forEach(b => b.onclick = async () => {
                try {
                    await api('story/exits/delete', 'POST',
                        { session, slug, room_id: r.id, to: Number(b.dataset.to), restore: true });
                    await refresh();
                } catch (e2) { ui.showToast(e2.message, 'error'); }
            });

            // "In this room" blurb — the dropdown reads as THE room
            // selector, not another item picker (Krem 2026-08-20)
            pane.querySelector('.grs-room-stats-line').textContent =
                `Exits: ${exitCount} · Items: ${items} · Actions: ${actions}`;
            // Reverse map (Krem 2026-08-21): which rooms' EFFECTIVE exits
            // point at this one — tombstoned shipped doors don't count.
            const inbound = [];
            for (const o of world.rooms) {
                if (o.id === r.id) continue;
                const osh = o.exit_shadows || {};
                if ((o.shipped_exits || []).some(e => e.to === r.id
                        && !((osh[String(e.to)] || {})._removed))
                    || (o.add_exits || []).some(e => e.to === r.id))
                    inbound.push(o.title);
            }
            const fromLine = pane.querySelector('.grs-room-stats-from');
            fromLine.textContent = `Rooms that lead here: ${inbound.join(', ') || 'none'}`;
            fromLine.title = inbound.join(', ');
            applyFindGlow();                  // standing search-match rings
            if (tab.onCanvasPaint) tab.onCanvasPaint();   // scenario bar's ● dot
        };

        const refresh = async () => {
            try {
                const d = await api(`story/objects?session=${encodeURIComponent(session)}&slug=${encodeURIComponent(slug)}`);
                if (d.active !== false) { world = d; paintRoom(); }
            } catch { /* pane keeps last state */ }
        };

        // ── Find (Krem 2026-08-22): the 🔎 chip expands to a jump-list
        // search over the WHOLE world — pure client-side, `world` already
        // holds every room/object/exit. Click a hit → room switches, the
        // card flashes; while text stands, matches in any viewed room keep
        // a glow ring. The room dropdown never mutates (no mystery-missing-
        // rooms state). Forgiving keys like the referee: 'mirror' finds
        // 'the_mirror'. Function declaration on purpose — hoisted, so
        // paintRoom (defined above) can call the glow safely.
        const findNorm = (s) => String(s || '').toLowerCase().replace(/[^a-z0-9]/g, '');
        const findChip = pane.querySelector('.grs-find-chip');
        const findWrap = pane.querySelector('.grs-find-wrap');
        const findInput = pane.querySelector('.grs-find-input');
        const findRes = pane.querySelector('.grs-find-results');
        function applyFindGlow() {
            const k = findNorm(findInput.value);
            pane.querySelectorAll('.grs-obj-card[data-name]').forEach(c =>
                c.classList.toggle('grs-find-glow', !!k && findNorm(c.textContent).includes(k)));
            pane.querySelectorAll('.grs-exit-badge[data-to]').forEach(c =>
                c.classList.toggle('grs-find-glow', !!k && findNorm((c.title || '') + c.textContent).includes(k)));
        }
        const findHits = (q) => {
            const k = findNorm(q);
            if (!k) return [];
            const prim = [], sec = [];
            for (const r of (world.rooms || [])) {
                if (findNorm(r.title).includes(k) || String(r.id) === q.trim())
                    prim.push({ kind: 'room', name: r.title, room: r });
                const layer = (world.objects || {})[String(r.id)] || {};
                const names = new Set([...Object.keys(r.shipped_objs || {}), ...Object.keys(layer)]);
                for (const n of names) {
                    const so = (r.shipped_objs || {})[n] || {};
                    const ov = layer[n] || {};
                    const desc = ov.desc != null ? ov.desc : so.desc || '';
                    if (findNorm(n).includes(k)) prim.push({ kind: 'object', name: n, room: r });
                    else if (findNorm(desc).includes(k)) sec.push({ kind: 'object', name: n, room: r });
                }
                for (const e of (r.shipped_exits || []).concat(r.add_exits || []))
                    if (findNorm(e.label).includes(k))
                        sec.push({ kind: 'exit', name: e.label, room: r });
            }
            return prim.concat(sec).slice(0, 12);
        };
        const findJump = (h) => {
            roomSel.value = String(h.room.id);
            paintRoom();
            if (h.kind === 'object') {
                const card = pane.querySelector(`.grs-obj-card[data-name="${CSS.escape(h.name)}"]`);
                if (card) {
                    card.scrollIntoView({ block: 'center', behavior: 'smooth' });
                    card.classList.remove('grs-find-flash');
                    void card.offsetWidth;               // restart the pulse
                    card.classList.add('grs-find-flash');
                }
            } else if (h.kind === 'exit') {
                const badges = pane.querySelector('.grs-exit-badges');
                badges.scrollIntoView({ block: 'center', behavior: 'smooth' });
            }
        };
        const paintFindResults = () => {
            const q = findInput.value;
            const hits = findHits(q);
            const icon = { room: '\u{1F5FA}', object: '\u{1F4E6}', exit: '\u{1F6AA}' };
            findRes.innerHTML = hits.map((h, i) => `
                <button type="button" class="grs-find-hit" data-i="${i}">
                    <span>${icon[h.kind]}</span>
                    <span class="grs-find-name">${esc(h.name || '')}</span>
                    <span class="grs-find-where">${h.kind} · ${h.room.id} ${esc(h.room.title || '')}</span>
                </button>`).join('')
                || `<div class="grs-find-empty">no match in ${world.rooms?.length || 0} rooms</div>`;
            findRes.style.display = q.trim() ? '' : 'none';
            findRes.querySelectorAll('.grs-find-hit').forEach(b =>
                b.onclick = () => { findJump(hits[+b.dataset.i]); findRes.style.display = 'none'; });
        };
        const findCollapse = () => {
            findWrap.style.display = 'none';
            findChip.style.display = '';
            findInput.value = '';
            findRes.style.display = 'none';
            applyFindGlow();
        };
        const findExpand = () => {
            findChip.style.display = 'none';
            findWrap.style.display = '';
            findInput.focus();
        };
        findChip.onclick = findExpand;
        findChip.onkeydown = (e) => {
            if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); findExpand(); }
        };
        findInput.oninput = () => { paintFindResults(); applyFindGlow(); };
        findInput.onkeydown = (e) => {
            if (e.key === 'Escape') findCollapse();
            else if (e.key === 'Enter') {
                if (findRes.style.display === 'none') paintFindResults();
                findRes.querySelector('.grs-find-hit')?.click();
            }
        };
        // Empty box loses focus → fold back to the chip (clicking a hit is
        // safe: the box still holds text). Delay lets the click land first.
        findInput.onblur = () => setTimeout(() => {
            if (findWrap.style.display !== 'none' && !findInput.value.trim()
                && document.activeElement !== findInput) findCollapse();
        }, 250);

        tArea.addEventListener('input', () => { count.textContent = tArea.value.length; stash(); });
        pArea.addEventListener('input', stash);

        // ── Exit form (2026-08-21): the + chip adds, clicking a badge
        // edits. Shipped doors save as SHADOWS — text diff-only, and
        // through the fidelity gate their MECHANICS edit live too (the
        // shadow's `mechanics` unit replaces the pack's wholesale); user
        // doors carry the full grammar via the shared widget.
        const exitForm = pane.querySelector('.grs-exit-form');
        const exTo = exitForm.querySelector('.grs-ex-to');
        const exLabel = exitForm.querySelector('.grs-ex-label');
        const exDesc = exitForm.querySelector('.grs-ex-desc');
        const exMech = exitForm.querySelector('.grs-ex-mech-note');
        const exReturn = exitForm.querySelector('.grs-ex-return');
        const exReturnLabel = exitForm.querySelector('.grs-ex-return-label');
        const exSave = exitForm.querySelector('.grs-ex-save');
        const exReset = exitForm.querySelector('.grs-ex-reset');
        const xw = locksWidget(exitForm, { types: { vis: EXIT_VIS, req: EXIT_REQ, fx: FX_BASE },
                                           pieceList: 'grs-piece-list' });
        let exEditing = null;   // destination id while editing, else null

        const closeExit = () => {
            exitForm.style.display = 'none';
            exEditing = null;
            exLabel.value = ''; exDesc.value = '';
            exReturn.checked = false;
            // empty the select too — leftover options would trip the
            // modal's dirty fingerprint (the spurious-confirm class)
            exTo.innerHTML = ''; exTo.disabled = false;
            xw.clear();
        };
        const openExit = (to) => {
            const r = curRoom();
            closeAdd();                      // one form at a time
            closeExit();
            exEditing = to ?? null;
            exTo.innerHTML = world.rooms.filter(x => x.id !== r.id)
                .map(x => `<option value="${x.id}">${esc(x.title)}</option>`).join('');
            exTo.disabled = to != null;
            if (to != null) exTo.value = String(to);
            const se = to != null ? (r.shipped_exits || []).find(e => e.to === to) : null;
            const sh = to != null ? (r.exit_shadows || {})[String(to)] : null;
            const ue = to != null && !se ? (r.add_exits || []).find(e => e.to === to) : null;
            exReturnLabel.style.display = to == null ? '' : 'none';
            if (se) {
                exLabel.value = (sh && sh.label) || se.label || '';
                exDesc.value = (sh && sh.desc) || se.desc || '';
                const mech = exitMech(se, sh);
                if (exitMechFits(se, mech)) {
                    // Gate passed: the door's machinery IS the widget
                    // grammar — prefill it live (2026-08-21).
                    xw.showAuthoring(true);
                    xw.prefill({ visCond: mech.visible_when || null,
                                 cond: mech.condition || null,
                                 roll: mech.roll || null,
                                 fx: mech.roll ? (mech.roll.success || {}) : (mech.effects || {}),
                                 msg: (sh && sh.blocked_message) || se.blocked_message || '' });
                    exMech.style.display = 'none';
                } else {
                    xw.showAuthoring(false);
                    exMech.textContent = '⚙\u{FE0E} Story machinery richer than this editor — '
                        + 'shown read-only, your text edits reword the door: '
                        + (exitMechWords(se, mech) || 'unnamed machinery');
                    exMech.style.display = '';
                }
                exReset.style.display = (sh && !sh._removed) ? '' : 'none';
                exSave.textContent = 'Save';
            } else if (ue) {
                exLabel.value = ue.label || '';
                exDesc.value = ue.desc || '';
                xw.prefill({ visCond: ue.visible_when || null, cond: ue.condition || null,
                             roll: ue.roll || null,
                             fx: ue.roll ? (ue.roll.success || {}) : (ue.effects || {}),
                             msg: ue.blocked_message || '' });
                exMech.style.display = 'none';
                exReset.style.display = 'none';
                exSave.textContent = 'Save';
            } else {
                exMech.style.display = 'none';
                exReset.style.display = 'none';
                exSave.textContent = 'Add';
            }
            exitForm.style.display = '';
            (to == null ? exTo : exLabel).focus();
        };
        // static chip on the label line (Krem 2026-08-21: trim-colored,
        // above the list) — bound once; openExit reads curRoom() at click
        const addChip = pane.querySelector('.grs-exit-addchip');
        addChip.onclick = () => openExit();
        addChip.onkeydown = (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openExit(); } };
        exitForm.querySelector('.grs-ex-cancel').onclick = closeExit;
        exReset.onclick = async () => {
            const r = curRoom();
            if (exEditing == null) return;
            try {
                await api('story/exits/delete', 'POST',
                          { session, slug, room_id: r.id, to: exEditing, restore: true });
                closeExit();
                await refresh();
            } catch (e) { ui.showToast(e.message, 'error'); }
        };
        exSave.onclick = async () => {
            const r = curRoom();
            const to = Number(exTo.value);
            if (!Number.isFinite(to)) { ui.showToast('Pick a destination room', 'error'); return; }
            const body = { session, slug, room_id: r.id, to,
                           label: exLabel.value.trim(), desc: exDesc.value.trim() };
            const se = (r.shipped_exits || []).find(e => e.to === to);
            // User exits always author; shipped ones only through the
            // fidelity gate — edit_mechanics marks the compile as
            // authoritative (replaces the pack's machinery as a unit).
            if (!se || exitMechFits(se, exitMech(se, (r.exit_shadows || {})[String(to)]))) {
                const vis = xw.readVis();
                if (vis && vis.cond) body.visible_when = vis.cond;
                const lk = xw.readLocks();
                if (lk) {
                    if (lk.cond) body.condition = lk.cond;
                    if (lk.msg) body.blocked_message = lk.msg;
                    if (lk.dice) {
                        const { miss, once, ...d } = lk.dice;
                        body.roll = { ...d, ...(once ? { once: true } : {}),
                                      success: { ...(lk.fx || {}) },
                                      failure: { message: miss || EXIT_FAIL_MSG } };
                    }
                    else if (lk.fx) body.effects = lk.fx;
                }
                if (se) body.edit_mechanics = true;
            }
            try {
                const res = await api('story/exits', 'POST', body);
                if (!res.success) { ui.showToast(res.detail || 'refused', 'error'); return; }
                // One-shot return door (F4): mirrored once at creation,
                // skipped if the far side already leads back. Never linked
                // after — each side stays its own exit.
                if (exEditing == null && exReturn.checked) {
                    const dest = world.rooms.find(x => x.id === to);
                    const destHas = dest && [...(dest.shipped_exits || []), ...(dest.add_exits || [])]
                        .some(e => e.to === r.id);
                    if (!destHas)
                        await api('story/exits', 'POST',
                                  { session, slug, room_id: to, to: r.id, label: r.title || '' });
                }
                closeExit();
                ui.showToast('Exit saved', 'success', 1500);
                await refresh();
            } catch (e) { ui.showToast(e.message, 'error'); }
        };

        // ── Add/edit-object form (the + tile adds; clicking a card edits;
        // shipped objects save as SHADOWS — verbatim-equals-shipped clears,
        // same house rule as room text) ─────────────────────────────────
        // :not — the exit form shares the styling class and sits earlier
        const addForm = pane.querySelector('.grs-obj-add-form:not(.grs-exit-form)');
        const objName = addForm.querySelector('.grs-obj-name');
        const objDesc = addForm.querySelector('.grs-obj-desc');
        const actRows = addForm.querySelector('.grs-act-rows');
        const resetBtn = addForm.querySelector('.grs-obj-reset');
        const mechNote = addForm.querySelector('.grs-obj-mech-note');
        let editing = null;   // object name while editing, else null

        // Per-ACTION machinery (the flip, Krem 2026-08-21): each card is
        // one command — verb + response — hosting its OWN requirements/
        // dice/effects via the shared widget. Matches the engine's real
        // per-verb grammar. Builder factored to makeActCard (2026-08-22)
        // so the starting-items panel shares it.
        const addActCard = (a) => makeActCard(actRows, a,
            { authoring: () => authoring, pieceList: 'grs-piece-list',
              selfName: () => (objName.value || '').trim() });

        const openAdd = (name) => {
            const r = curRoom();
            closeExit();                     // one form at a time
            editing = name || null;
            actRows.innerHTML = '';
            clearLocks();
            setAuthoring(true);
            objName.value = editing || '';
            objName.disabled = !!editing;
            const so = editing ? (r.shipped_objs || {})[editing] : null;
            const ov = editing ? ((world.objects || {})[String(r.id)] || {})[editing] : null;
            if (so) {
                const eff = effective(so, ov);
                if (objFits(editing, eff.merged)) {
                    // Fidelity gate passed (2026-08-21): the machinery IS
                    // the widget grammar — prefill it live.
                    prefillFromSpec(eff.merged);
                    mechNote.style.display = 'none';
                } else {
                    setAuthoring(false);
                    objDesc.value = eff.desc;
                    for (const [v, m] of Object.entries(eff.verbs))
                        addActCard({ verb: v, resp: m });
                    mechNote.textContent = '\u{2699}\u{FE0E} Story machinery richer than this '
                        + 'editor — shown read-only, your text edits reword it: '
                        + (objMechWords(eff.merged) || 'unnamed machinery');
                    mechNote.style.display = '';
                }
                resetBtn.style.display = ov ? '' : 'none';
            } else if (editing && ov) {
                prefillFromSpec(ov);
                mechNote.style.display = 'none';
                resetBtn.style.display = 'none';
            } else {
                objDesc.value = '';
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
            objDesc.value = '';
            actRows.innerHTML = '';
            clearLocks();
        };
        addForm.querySelector('.grs-act-add').onclick = () => addActCard(null).querySelector('.grs-act-verb').focus();

        // ── Object-level sections (the flip): visibility, and the riddle
        // (text + answer + solve effects). Actions carry their own
        // machinery per card — the conjured open/use row hack died with
        // the object-level lock sections.
        const objTake = addForm.querySelector('.grs-obj-take');
        const takeLabel = addForm.querySelector('.grs-take-label');
        const visW = locksWidget(addForm.querySelector('.grs-obj-vis'),
                                 { types: { vis: VIS_TYPES } });
        const ridFxW = locksWidget(addForm.querySelector('.grs-obj-ridfx'),
                                   { types: { fx: FX_BASE }, pieceList: 'grs-piece-list' });
        const ridToggle = addForm.querySelector('.grs-rid-toggle');
        const ridLabel = addForm.querySelector('.grs-rid-label');
        const ridBody = addForm.querySelector('.grs-rid-body');
        const ridText = addForm.querySelector('.grs-rid-text');
        const ridAnswer = addForm.querySelector('.grs-rid-answer');
        ridToggle.onchange = () => { ridBody.style.display = ridToggle.checked ? '' : 'none'; };
        let authoring = true;   // false = machinery rides pack-side, text edits only
        const setAuthoring = (on) => {
            authoring = on;
            visW.showAuthoring(on);
            for (const l of [takeLabel, ridLabel]) l.style.display = on ? '' : 'none';
            if (!on) ridBody.style.display = 'none';
            actRows.querySelectorAll('.grs-act-gear').forEach(g => { g.style.display = on ? '' : 'none'; });
        };
        const clearLocks = () => {
            visW.clear();
            ridFxW.clear();
            ridToggle.checked = false;
            ridBody.style.display = 'none';
            ridText.value = ''; ridAnswer.value = '';
            objTake.checked = false;
        };
        // spec → form (one prefill for user objects AND gated shipped
        // ones — objToDescriptor is the single spec reader)
        const prefillFromSpec = (spec) => {
            const d = objToDescriptor(spec);
            objDesc.value = d.desc;
            for (const a of d.acts) addActCard(a);
            objTake.checked = d.take;
            visW.prefill({ hidden: d.hidden, visCond: d.visCond });
            if (d.puzzle) {
                ridToggle.checked = true;
                ridBody.style.display = '';
                ridText.value = d.puzzle.riddle || '';
                ridAnswer.value = d.puzzle.solution || '';
                if (d.solveFx) ridFxW.prefill({ fx: d.solveFx });
            }
        };
        // form → descriptor (compileObj's input); a riddle needs an
        // answer to exist, and solve effects only exist with the riddle
        const readObjDescriptor = () => {
            const acts = [...actRows.querySelectorAll('.grs-act-card')].map(card => {
                const lk = card._lw.readLocks() || {};
                return { verb: card.querySelector('.grs-act-verb').value.trim(),
                         resp: card.querySelector('.grs-act-resp').value.trim(),
                         cond: lk.cond || null, dice: lk.dice || null, sealed: lk.sealed || null,
                         fx: lk.fx || null, msg: lk.msg || '' };
            }).filter(a => a.verb);
            const vis = visW.readVis() || {};
            const rid = ridToggle.checked && ridAnswer.value.trim()
                ? { riddle: ridText.value.trim() || OBJ_RIDDLE_DEFAULT,
                    solution: ridAnswer.value.trim() } : null;
            return { desc: objDesc.value.trim(), take: objTake.checked,
                     hidden: !!vis.hidden, visCond: vis.cond || null,
                     puzzle: rid,
                     solveFx: rid ? ((ridFxW.readLocks() || {}).fx || null) : null,
                     acts };
        };

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
            const ovNow = ((world.objects || {})[String(r.id)] || {})[name];
            const d = readObjDescriptor();
            const restoreAndClose = async () => {
                try {
                    if (ovNow)
                        await api('story/objects/delete', 'POST',
                                  { session, slug, room_id: r.id, name, restore: true });
                    closeAdd();
                    await refresh();
                } catch (e) { ui.showToast(e.message, 'error'); }
            };
            // Hand-placed objects appear immediately; gating is the
            // author's explicit choice via Visible-when (scenario loads are
            // verbatim — no implicit zork-line stamp since 2026-08-20).
            let body;
            if (so && objFits(name, objMerged(so.spec || {}, ovNow))) {
                // Fidelity-gate lane: the compile replaces the shipped
                // object wholesale; verbatim-equals-shipped clears.
                const spec = compileObj(name, d, objMerged(so.spec || {}, ovNow));
                if (deepEq(spec, so.spec || {})) { await restoreAndClose(); return; }
                body = { session, slug, room_id: r.id, name, spec, replace: true };
            } else if (so) {
                // Text-shadow lane (machinery rides pack-side): store only
                // the DIFF vs shipped; nothing changed → clear entirely.
                const spec = {};
                const desc = objDesc.value.trim();
                if (desc !== (so.desc || '')) spec.desc = desc;
                const ints = {};
                for (const a of d.acts) {
                    if (a.verb in (so.verbs || {})) {
                        if (a.resp !== so.verbs[a.verb]) ints[a.verb] = { message: a.resp };
                    } else {
                        ints[a.verb] = { message: a.resp || `You ${a.verb} the ${name}.` };
                    }
                }
                if (Object.keys(ints).length) spec.interactions = ints;
                if (!Object.keys(spec).length) { await restoreAndClose(); return; }
                body = { session, slug, room_id: r.id, name, spec };
            } else {
                body = { session, slug, room_id: r.id, name,
                         spec: compileObj(name, d, null) };
            }
            try {
                const res = await api('story/objects', 'POST', body);
                if (!res.success) { ui.showToast(res.detail || 'refused', 'error'); return; }
                closeAdd();
                ui.showToast(`'${name}' ${so ? 'saved' : 'placed'}`, 'success', 2000);
                await refresh();
            } catch (e) { ui.showToast(e.message, 'error'); }
        };

        // ── Backdrop controls (W1, 2026-08-21): pick applies immediately
        // (same lane as objects/exits); upload → content-hash store → set.
        const bdPick = pane.querySelector('.grs-bd-pick');
        const bdFile = pane.querySelector('.grs-bd-file');
        const setBackdrop = async (name) => {
            const r = curRoom();
            try {
                const res = await api('story/backdrop', 'POST',
                                      { session, slug, room_id: r.id, name });
                if (!res.success) { ui.showToast(res.detail || 'refused', 'error'); return; }
                await refresh();
            } catch (e) { ui.showToast(e.message, 'error'); }
        };
        bdPick.onchange = () => { if (bdPick.value) setBackdrop(bdPick.value); };
        pane.querySelector('.grs-bd-reset').onclick = () => setBackdrop('');
        pane.querySelector('.grs-bd-upload').onclick = () => bdFile.click();
        bdFile.onchange = async () => {
            const f = bdFile.files && bdFile.files[0];
            bdFile.value = '';
            if (!f) return;
            const fd = new FormData();
            fd.append('file', f);
            try {
                const up = await fetch('/api/plugin/game-room/story/art',
                                       { method: 'POST', body: fd });
                const j = await up.json();
                if (!j.success) { ui.showToast(j.detail || 'upload refused', 'error'); return; }
                await setBackdrop(j.name);
                ui.showToast('Image stored & applied', 'success', 1800);
            } catch (e) { ui.showToast(e.message, 'error'); }
        };

        // ── W2: room create/delete (playthrough rooms — id ≥ 100) ──────
        roomSel.onchange = async () => {
            if (roomSel.value !== '__new__') { paintRoom(); return; }
            const title = prompt('New room name:');
            roomSel.value = roomSel.dataset.prev || '';   // cancel-safe
            if (!title || !title.trim()) { paintRoom(); return; }
            try {
                const res = await api('story/rooms', 'POST',
                                      { session, slug, title: title.trim() });
                if (!res.success) { ui.showToast(res.detail || 'refused', 'error'); paintRoom(); return; }
                await refresh();
                roomSel.value = String(res.id);
                paintRoom();
                ui.showToast(res.detail, 'success', 2000);
            } catch (e) { ui.showToast(e.message, 'error'); }
        };
        pane.querySelector('.grs-room-del').onclick = async () => {
            const r = curRoom();
            if (!r || !r.user_room) return;
            if (!confirm(`Remove '${r.title}'? Its doors and objects go with it.`)) return;
            try {
                const res = await api('story/rooms/delete', 'POST',
                                      { session, slug, room_id: r.id });
                ui.showToast(res.detail || (res.success ? 'Removed' : 'refused'),
                             res.success ? 'success' : 'error', 2500);
                if (!res.success) return;
                await refresh();
                roomSel.value = String(world.current_room
                    ?? ((world.rooms || [])[0] || {}).id ?? '');
                paintRoom();
            } catch (e) { ui.showToast(e.message, 'error'); }
        };
        paintRoom();
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
