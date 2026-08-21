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
        tabs.push(...st);
    }

    // Prompt Pieces (Krem 2026-08-21, surfaces ruling: content/conduct/
    // run) — the panel rides the PLAY surfaces only: this gear (running
    // world) and the setup modal (pre-run). The library wheel is CONDUCT
    // (GM tab) and carries no story content.
    const pp = objData ? piecesPanel(slug, opts.session) : null;
    if (pp) {
        const chars = tabs.find(t => t.title === 'Characters');
        if (chars) chars.html += pp.html;
        else tabs.push({ title: 'Characters', html: pp.html });
    }

    // Rooms — the open-world pane (active playthrough only). Order ruling
    // (Krem 2026-08-21): Story, Characters, Rooms first; GM + State last.
    const envTab = objData ? objectsTab(slug, opts.session, objData) : null;
    if (envTab) tabs.push(envTab);

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
                            envFlush: () => envTab?.flush?.(),
                            envDiverged: () => !!(envTab && envTab.diverged()),
                            onSwap: () => envTab?.swapped?.(),
                            onSaved: () => envTab?.markClean?.() })
        : null;
    if (envTab && scnState) envTab.onCanvasPaint = scnState.updateDot;

    // Deep link (the 🏠 button lands on the Objects tab directly)
    if (opts.tab) {
        const btn = [...overlay.querySelectorAll('.grs-tab')].find(t => t.dataset.tab === opts.tab);
        if (btn) btn.click();
    }

    if (pp) pp.wire(overlay);

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
            await api(`story/${encodeURIComponent(slug)}/settings`, 'POST', { settings: out });
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

        const tabs = slotTabs(open, sealed, () => undefined);
        // Prompt Pieces ride the setup modal too (Krem 2026-08-21: author
        // the moods before pressing ▶) — under Characters, same panel as
        // the in-game gear.
        const pp = piecesPanel(slug, session);
        const charsTab = tabs.find(t => t.title === 'Characters');
        if (charsTab) charsTab.html += pp.html;
        else tabs.push({ title: 'Characters', html: pp.html });
        const envTab = objData ? objectsTab(slug, session, objData) : null;
        if (envTab) tabs.push(envTab);

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
        pp.wire(overlay);
        const scnState = wireScenarioBar(overlay, slug, setup, open, {},
                              { session,
                                current: objData?.scenario || '',
                                envFlush: () => envTab?.flush?.(),
                                envDiverged: () => !!(envTab && envTab.diverged()),
                                onSwap: () => envTab?.swapped?.(),
                                onSaved: () => envTab?.markClean?.() });
        if (envTab && scnState) envTab.onCanvasPaint = scnState.updateDot;

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
];
const REQ_TYPES = [
    ['has', 'needs item', 'item name, e.g. bronze_key', ''],
    ['did', 'needs opened/used', 'object name, e.g. door1', ''],
    ['flag', 'flag is set', 'flag name, e.g. ballroom_unlocked', ''],
    ['password', 'password / riddle', 'the answer, e.g. 1234', 'riddle / prompt she sees (optional)'],
    ['d20', 'd20 chance', 'roll needed, e.g. 11', ''],
    ['d100', 'd100 chance', 'roll needed, e.g. 51', ''],
];
const FX_TYPES = [
    ['set', 'set flag', 'flag name, e.g. ballroom_unlocked', ''],
    ['clear', 'clear flag', 'flag name, e.g. ballroom_unlocked', ''],
    ['give', 'give item', 'item name, e.g. bronze_key', ''],
    ['adjust', 'adjust number', 'name, e.g. love', 'amount, e.g. 10 or -5'],
    ['xadd', 'add prompt piece', 'piece name, e.g. hostile', ''],
    ['xrem', 'remove prompt piece', 'piece name, e.g. hostile', ''],
    ['goto', 'move player to room', 'room number, e.g. 3', ''],
];
// Exits: no password (a riddle door = a door OBJECT with a password; the
// exit then Requires "needs opened/used" on it) and no searched (search
// finds objects; a found lever's flag makes the passage appear).
const EXIT_VIS = VIS_TYPES.filter(t => t[0] !== 'searched');
const EXIT_REQ = REQ_TYPES.filter(t => t[0] !== 'password');
const EXIT_FAIL_MSG = 'Not this time — the way defeats the attempt.';
const EXIT_MECH = ['condition', 'roll', 'effects', 'visible_when'];

// ── Fidelity gate (2026-08-21): shipped door machinery prefills as
// EDITABLE only when the widget speaks it losslessly — a lossy save would
// silently strip pack grammar (flags dicts, flag_gte, custom roll
// branches, generation). Richer doors show a read-only plain-words
// summary instead; text edits still shadow.
const _condFits = (c) => !c || Object.entries(c).every(([k, v]) =>
    ['has', 'did', 'flag'].includes(k) && typeof v === 'string');
const _fxFits = (f) => !f || Object.entries(f).every(([k, v]) =>
    (k === 'set' && Object.values(v).every(x => x === true || x === false))
    || (k === 'gives' && typeof v === 'string')
    || (k === 'adjust' && Object.values(v).every(x => typeof x === 'number'))
    || ((k === 'extras' || k === 'extras_remove') && Array.isArray(v)
        && v.every(x => typeof x === 'string'))
    || (k === 'goto' && typeof v === 'number'));
const _rollFits = (r) => !r || (
    (r.sides === 20 || r.sides === 100) && Number.isFinite(r.beat)
    && Object.keys(r).every(k => ['sides', 'beat', 'success', 'failure'].includes(k))
    && _fxFits(r.success)
    && (!r.failure || JSON.stringify(r.failure) === JSON.stringify({ message: EXIT_FAIL_MSG })));
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
        ...Object.entries(fx.adjust || {}).map(([k, n]) => `${k} ${n > 0 ? '+' : ''}${n}`)].join(', '));
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
// spec → the editor's neutral descriptor (what the form fields hold);
// mirrors what the DOM prefill can express — v0's locks speak for all
// verbs, puzzle collapses to one solution, solved is the password row's
const objToDescriptor = (spec) => {
    const ints = spec.interactions || {};
    const vspecs = Object.values(ints).filter(s => s && typeof s === 'object');
    const v0 = vspecs[0] || {};
    const fxSrc = v0.roll ? (v0.roll.success || {})
        : (vspecs.length ? v0 : (spec.on_solve || {}));
    const fx = {};
    for (const k of ['set', 'gives', 'adjust', 'extras', 'extras_remove', 'goto'])
        if (fxSrc[k] != null) fx[k] = fxSrc[k];
    const cond = { ...(v0.condition || {}) };
    delete cond.solved;
    const puz = spec.puzzle;
    return {
        desc: spec.desc || '', take: !!spec.takeable,
        hidden: !!spec.hidden, visCond: spec.condition || null,
        cond: Object.keys(cond).length ? cond : null,
        puzzle: puz ? { riddle: puz.riddle || OBJ_RIDDLE_DEFAULT,
                        solution: (puz.solutions || [])[0] ?? puz.solution ?? '' } : null,
        dice: v0.roll ? { sides: v0.roll.sides, beat: v0.roll.beat } : null,
        fx: Object.keys(fx).length ? fx : null,
        msg: v0.blocked_message || '',
        acts: Object.entries(ints).filter(([, s]) => s && typeof s === 'object')
            .map(([v, s]) => [v, s.message
                ?? (s.roll && s.roll.success && s.roll.success.message) ?? '']),
    };
};
// descriptor (+ passenger source) → spec. ONE compile for the user-object
// lane (src null), the gated shipped lane, and the fits check.
const compileObj = (name, d, src) => {
    const spec = { desc: d.desc };
    const acts = {};
    for (const [verb, resp] of d.acts) {
        if (!verb) continue;
        acts[verb] = { message: resp || `You ${verb} the ${name}.` };
    }
    if (d.take) spec.takeable = true;
    if (d.hidden) spec.hidden = true;
    if (d.visCond) spec.condition = d.visCond;
    const cond = { ...(d.cond || {}), ...(d.puzzle ? { solved: name } : {}) };
    const fxHas = d.fx && Object.keys(d.fx).length;
    if (Object.keys(cond).length || d.puzzle || d.dice || fxHas) {
        if (d.puzzle) spec.puzzle = { ...d.puzzle };
        for (const v of Object.values(acts)) {
            if (Object.keys(cond).length) {
                v.condition = cond;
                if (d.msg) v.blocked_message = d.msg;
            }
            if (d.dice) {
                // chance replaces the flat outcome: response + effects
                // ride the success branch
                v.roll = { sides: d.dice.sides, beat: d.dice.beat,
                           success: { message: v.message, ...(d.fx || {}) },
                           failure: { message: OBJ_FAIL_MSG } };
                delete v.message;
            } else if (fxHas) {
                Object.assign(v, d.fx);
            }
        }
        if (!Object.keys(acts).length && d.puzzle && fxHas)
            spec.on_solve = { ...d.fx };   // pure riddle: effects on solve
    }
    if (src) {                             // passengers ride their carrier
        for (const k of ['found_by', 'gives'])
            if (src[k] != null) spec[k] = src[k];
        if (spec.on_solve && src.on_solve)
            for (const k of ['message', 'emotions', 'emotions_remove'])
                if (src.on_solve[k] != null) spec.on_solve[k] = src.on_solve[k];
        const sints = src.interactions || {};
        for (const [verb, v] of Object.entries(acts)) {
            const sv = sints[verb];
            if (!sv || typeof sv !== 'object') continue;
            if (sv.aliases != null) v.aliases = sv.aliases;
            if (v.roll && sv.roll && typeof sv.roll === 'object') {
                if (sv.roll.failure) v.roll.failure = { ...sv.roll.failure };
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
const objFits = (name, spec) =>
    deepEq(compileObj(name, objToDescriptor(spec), spec), spec);
const objMarks = (spec) => {
    const ints = Object.values(spec.interactions || {}).filter(s => s && typeof s === 'object');
    return (spec.hidden || spec.condition ? ' \u{1F32B}\u{FE0F}' : '')
        + (spec.takeable ? ' \u{1F392}' : '')
        + (spec.puzzle || ints.some(v => v.condition) ? ' \u{1F512}' : '')
        + (ints.some(v => v.roll) ? ' \u{1F3B2}' : '');
};
const objMechWords = (spec) => {
    const bits = [];
    if (spec.hidden) bits.push('hidden until found');
    if (spec.condition) bits.push(`appears when ${_condWords(spec.condition)}`);
    if (spec.gives) bits.push(`finding gives ${spec.gives}`);
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

function locksWidget(form, opts) {
    const types = opts.types;
    const q = (sel) => form.querySelector(sel);
    const visToggle = q('.grs-vis-toggle'), reqToggle = q('.grs-req-toggle'), fxToggle = q('.grs-fx-toggle');
    const visLabel = q('.grs-vis-label'), reqLabel = q('.grs-req-label'), fxLabel = q('.grs-fx-label');
    const visBody = q('.grs-vis-body'), reqBody = q('.grs-req-body'), fxBody = q('.grs-fx-body');
    const visRows = q('.grs-vis-rows'), reqRows = q('.grs-req-rows'), fxRows = q('.grs-fx-rows');
    const lockMsg = q('.grs-lock-msg');
    visToggle.onchange = () => { visBody.style.display = visToggle.checked ? '' : 'none'; };
    reqToggle.onchange = () => {
        reqBody.style.display = reqToggle.checked ? '' : 'none';
        if (reqToggle.checked && opts.onReqOpen) opts.onReqOpen();
    };
    fxToggle.onchange = () => {
        fxBody.style.display = fxToggle.checked ? '' : 'none';
        if (fxToggle.checked && opts.onFxOpen) opts.onFxOpen();
    };
    const pickRow = (host, tlist, kind, val, extra) => {
        const row = document.createElement('div');
        row.className = 'grs-act-row';
        row.innerHTML = `
            <select class="grs-pick-kind">${tlist.map(t =>
                `<option value="${t[0]}"${t[0] === kind ? ' selected' : ''}>${t[1]}</option>`).join('')}</select>
            <input type="text" class="grs-pick-val">
            <input type="text" class="grs-pick-extra">
            <button type="button" class="sb-icon-btn grs-act-del" title="Remove">✕</button>`;
        const sel = row.querySelector('.grs-pick-kind');
        const vIn = row.querySelector('.grs-pick-val');
        const xIn = row.querySelector('.grs-pick-extra');
        const paint = () => {
            const t = tlist.find(x => x[0] === sel.value) || tlist[0];
            vIn.placeholder = t[2];
            xIn.placeholder = t[3];
            vIn.style.display = t[2] ? '' : 'none';   // value-less kinds (searched)
            xIn.style.display = t[3] ? '' : 'none';
            // Prompt-piece kinds offer the story's pool as suggestions
            if (opts.pieceList && (t[0] === 'xadd' || t[0] === 'xrem'))
                vIn.setAttribute('list', opts.pieceList);
            else vIn.removeAttribute('list');
        };
        sel.onchange = paint;
        paint();
        vIn.value = val || '';
        xIn.value = extra || '';
        row.querySelector('.grs-act-del').onclick = () => row.remove();
        host.appendChild(row);
        return row;
    };
    q('.grs-vis-add').onclick = () =>
        pickRow(visRows, types.vis).querySelector('.grs-pick-kind').focus();
    q('.grs-req-add').onclick = () =>
        pickRow(reqRows, types.req).querySelector('.grs-pick-val').focus();
    q('.grs-fx-add').onclick = () =>
        pickRow(fxRows, types.fx).querySelector('.grs-pick-val').focus();

    const rawRows = (host) => [...host.querySelectorAll('.grs-act-row')].map(row => ({
        kind: row.querySelector('.grs-pick-kind').value,
        val: row.querySelector('.grs-pick-val').value.trim(),
        extra: row.querySelector('.grs-pick-extra').value.trim(),
    }));
    const rowsOf = (host) => rawRows(host).filter(x => x.val);

    const clear = () => {
        for (const t of [visToggle, reqToggle, fxToggle]) t.checked = false;
        for (const b of [visBody, reqBody, fxBody]) b.style.display = 'none';
        for (const l of [visLabel, reqLabel, fxLabel]) l.style.display = '';
        for (const h of [visRows, reqRows, fxRows]) h.innerHTML = '';
        lockMsg.value = '';
    };
    const showAuthoring = (on) => {
        for (const l of [visLabel, reqLabel, fxLabel]) l.style.display = on ? '' : 'none';
        if (!on) for (const b of [visBody, reqBody, fxBody]) b.style.display = 'none';
    };

    // Visible-when rows → the existence gate: `hidden` (search reveal)
    // and/or a condition {has, did, flag}.
    const readVis = () => {
        if (!visToggle.checked) return null;
        const out = { hidden: false, cond: {} };
        for (const x of rawRows(visRows)) {
            if (x.kind === 'searched') out.hidden = true;
            else if (!x.val) continue;
            else if (x.kind === 'has' && !out.cond.has) out.cond.has = x.val;
            else if (x.kind === 'did' && !out.cond.did) out.cond.did = x.val;
            else if (x.kind === 'flag' && !out.cond.flag) out.cond.flag = x.val;
        }
        if (!Object.keys(out.cond).length) out.cond = null;
        return (out.hidden || out.cond) ? out : null;
    };

    const readLocks = (name) => {
        if (!reqToggle.checked && !fxToggle.checked) return null;
        const cond = {};
        let puzzle = null, dice = null;
        if (reqToggle.checked) for (const x of rowsOf(reqRows)) {
            if (x.kind === 'has' && !cond.has) cond.has = x.val;
            else if (x.kind === 'did' && !cond.did) cond.did = x.val;
            else if (x.kind === 'flag' && !cond.flag) cond.flag = x.val;
            else if (x.kind === 'password' && !puzzle) {
                puzzle = { riddle: x.extra || OBJ_RIDDLE_DEFAULT,
                           solution: x.val };
                cond.solved = name;
            } else if ((x.kind === 'd20' || x.kind === 'd100') && !dice) {
                const sides = x.kind === 'd20' ? 20 : 100;
                const beat = parseInt(x.val, 10);
                dice = { sides, beat: Number.isFinite(beat)
                         ? Math.max(1, Math.min(sides, beat)) : sides / 2 + 1 };
            }
        }
        const fx = {};
        if (fxToggle.checked) for (const x of rowsOf(fxRows)) {
            if (x.kind === 'set') (fx.set = fx.set || {})[x.val] = true;
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
            }
        }
        const out = { cond: Object.keys(cond).length ? cond : null,
                      puzzle, dice,
                      fx: Object.keys(fx).length ? fx : null,
                      msg: lockMsg.value.trim() };
        return (out.cond || out.puzzle || out.dice || out.fx) ? out : null;
    };

    // descriptor → rows (edit round-trip)
    const prefill = (d) => {
        let anyReq = false, anyFx = false, anyVis = false;
        if (d.hidden) { pickRow(visRows, types.vis, 'searched'); anyVis = true; }
        const tc = d.visCond || {};
        if (tc.has) { pickRow(visRows, types.vis, 'has', tc.has); anyVis = true; }
        if (tc.did) { pickRow(visRows, types.vis, 'did', tc.did); anyVis = true; }
        if (tc.flag) { pickRow(visRows, types.vis, 'flag', tc.flag); anyVis = true; }
        const cond = d.cond || {};
        if (cond.has) { pickRow(reqRows, types.req, 'has', cond.has); anyReq = true; }
        if (cond.did) { pickRow(reqRows, types.req, 'did', cond.did); anyReq = true; }
        if (cond.flag) { pickRow(reqRows, types.req, 'flag', cond.flag); anyReq = true; }
        if (d.puzzle) {
            pickRow(reqRows, types.req, 'password',
                    (d.puzzle.solutions || [])[0] || d.puzzle.solution || '',
                    d.puzzle.riddle || '');
            anyReq = true;
        }
        if (d.roll) {
            pickRow(reqRows, types.req, d.roll.sides === 20 ? 'd20' : 'd100',
                    String(d.roll.beat ?? ''));
            anyReq = true;
        }
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
        // A refusal message alone is dead data — it only shows when a
        // lock fails. Stage it in the field (recoverable if a lock is
        // re-added) but let real locks own the checkbox; counting it made
        // Requirements re-check on stripped doors (Krem 2026-08-21).
        if (d.msg) lockMsg.value = d.msg;
        visToggle.checked = anyVis;
        visBody.style.display = anyVis ? '' : 'none';
        reqToggle.checked = anyReq;
        reqBody.style.display = anyReq ? '' : 'none';
        fxToggle.checked = anyFx;
        fxBody.style.display = anyFx ? '' : 'none';
    };

    return { readVis, readLocks, prefill, clear, showAuthoring };
}

// ── Environment tab (the open-world editor) ─────────────────────────────────
function objectsTab(slug, session, data) {
    const rooms = data.rooms || [];
    const authorOf = (spec) => spec?._author === 'ai' ? ' \u{1F916}' : '';
    const curId = data.current_room;
    const html = `
        <div class="grs-section-title" style="margin-top:0">Room</div>
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
            <button type="button" class="pk-btn grs-act-add" title="One more verb it responds to — 'eat' → 'You shrink to very small.'">+ Add action</button>
            <label class="st-tools-check grs-take-label" style="margin:0"><input type="checkbox" class="grs-obj-take"> Can be picked up — goes into her inventory and leaves the room</label>
            <label class="st-tools-check grs-vis-label" style="margin:0"><input type="checkbox" class="grs-vis-toggle"> Visible when — until then it doesn't exist for her</label>
            <div class="grs-lock-body grs-vis-body" style="display:none">
                <div class="grs-vis-rows"></div>
                <button type="button" class="pk-btn grs-vis-add">+ Add condition</button>
            </div>
            <label class="st-tools-check grs-req-label" style="margin:0"><input type="checkbox" class="grs-req-toggle"> Requirements — what it takes to use this</label>
            <div class="grs-lock-body grs-req-body" style="display:none">
                <div class="grs-req-rows"></div>
                <button type="button" class="pk-btn grs-req-add">+ Add requirement</button>
                <input type="text" class="grs-lock-msg" placeholder="locked message (optional) — what she sees while it refuses">
            </div>
            <label class="st-tools-check grs-fx-label" style="margin:0"><input type="checkbox" class="grs-fx-toggle"> Effects — what using it changes</label>
            <div class="grs-lock-body grs-fx-body" style="display:none">
                <div class="grs-fx-rows"></div>
                <button type="button" class="pk-btn grs-fx-add">+ Add effect</button>
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
            if (tab.onCanvasPaint) tab.onCanvasPaint();   // scenario bar's ● dot
        };

        const refresh = async () => {
            try {
                const d = await api(`story/objects?session=${encodeURIComponent(session)}&slug=${encodeURIComponent(slug)}`);
                if (d.active !== false) { world = d; paintRoom(); }
            } catch { /* pane keeps last state */ }
        };

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
        const xw = locksWidget(exitForm, { types: { vis: EXIT_VIS, req: EXIT_REQ, fx: FX_TYPES },
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
                const lk = xw.readLocks(body.label || String(to));
                if (lk) {
                    if (lk.cond) body.condition = lk.cond;
                    if (lk.msg) body.blocked_message = lk.msg;
                    if (lk.dice) body.roll = {
                        ...lk.dice,
                        success: { ...(lk.fx || {}) },
                        failure: { message: EXIT_FAIL_MSG } };
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
            closeExit();                     // one form at a time
            editing = name || null;
            actRows.innerHTML = '';
            clearLocks();
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
                    objDesc.value = eff.desc;
                    for (const [v, m] of Object.entries(eff.verbs)) addActRow(v, m);
                    mechNote.textContent = '\u{2699}\u{FE0E} Story machinery richer than this '
                        + 'editor — shown read-only, your text edits reword it: '
                        + (objMechWords(eff.merged) || 'unnamed machinery');
                    mechNote.style.display = '';
                    lw.showAuthoring(false);
                    takeLabel.style.display = 'none';
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
        addForm.querySelector('.grs-act-add').onclick = () => addActRow().querySelector('.grs-act-verb').focus();

        // ── Locks & effects — the shared widget carries the machinery
        // (exits editor DRY, 2026-08-21); this form maps its object grammar
        // in and out around it.
        const objTake = addForm.querySelector('.grs-obj-take');
        const takeLabel = addForm.querySelector('.grs-take-label');
        const lw = locksWidget(addForm, {
            types: { vis: VIS_TYPES, req: REQ_TYPES, fx: FX_TYPES },
            pieceList: 'grs-piece-list',
            // A lock/effect wants a carrier verb — offer 'open'/'use' as
            // visible, editable action rows (Krem's clown_chest 2026-08-20:
            // hand-authoring the obvious verb was friction, not law).
            onReqOpen: () => { if (!actRows.children.length) addActRow('open', ''); },
            onFxOpen: () => {
                if (!actRows.children.length)
                    addActRow('use', '').querySelector('.grs-act-resp').focus();
            },
        });

        const clearLocks = () => {
            lw.clear();
            objTake.checked = false;
            takeLabel.style.display = '';
        };
        const readVis = lw.readVis;
        const readLocks = lw.readLocks;
        // spec → form (one prefill for user objects AND gated shipped
        // ones — objToDescriptor is the single spec reader)
        const prefillFromSpec = (spec) => {
            const d = objToDescriptor(spec);
            objDesc.value = d.desc;
            for (const [v, m] of d.acts) addActRow(v, m);
            objTake.checked = d.take;
            lw.prefill({ hidden: d.hidden, visCond: d.visCond, cond: d.cond,
                         puzzle: d.puzzle, roll: d.dice, fx: d.fx || {}, msg: d.msg });
        };
        // form → descriptor (compileObj's input; solved is stripped — the
        // compile re-stamps it from the puzzle, matching the password row)
        const readObjDescriptor = (name) => {
            const acts = [...actRows.querySelectorAll('.grs-act-row')].map(row => [
                row.querySelector('.grs-act-verb').value.trim(),
                row.querySelector('.grs-act-resp').value.trim()]).filter(a => a[0]);
            const vis = readVis() || {};
            const lk = readLocks(name) || {};
            const cond = { ...(lk.cond || {}) };
            delete cond.solved;
            return { desc: objDesc.value.trim(), take: objTake.checked,
                     hidden: !!vis.hidden, visCond: vis.cond || null,
                     cond: Object.keys(cond).length ? cond : null,
                     puzzle: lk.puzzle || null, dice: lk.dice || null,
                     fx: lk.fx || null, msg: lk.msg || '', acts };
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
            const d = readObjDescriptor(name);
            const fxHas = d.fx && Object.keys(d.fx).length;
            // Locks want a carrier verb (widget hidden = fields empty, so
            // the read-only shipped lane never trips this).
            if ((d.cond || d.dice || fxHas) && !d.acts.length && !d.puzzle) {
                ui.showToast('Requirements & effects need at least one action — or a password to solve.',
                             'error', 3500);
                return;
            }
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
                for (const [verb, resp] of d.acts) {
                    if (verb in (so.verbs || {})) {
                        if (resp !== so.verbs[verb]) ints[verb] = { message: resp };
                    } else {
                        ints[verb] = { message: resp || `You ${verb} the ${name}.` };
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
