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
    // Slot fields diverged from the LOADED scenario's values (unsaved
    // form edits a swap would silently overwrite — draft restores included).
    const slotsDiverged = () => {
        const sc = (setup.scenarios || {})[last];
        const base = sc ? (sc.slots || {}) : (baseVals || {});
        return slotList.some(s =>
            String(readField(overlay, s.key) ?? '') !== String(base[s.key] ?? s.default ?? ''));
    };
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
    const fingerprint = () => JSON.stringify(
        [...overlay.querySelectorAll(
            'input, select:not(.grs-obj-room), textarea:not(.grs-obj-template):not(.grs-obj-pdesc)')]
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
    return { overlay, close, tryClose, dirty };
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

    // Setup (this run) — editable slot values, one tab per slot section.
    // Sealed blanks are edited from the scene strip's ✍ chips, not here.
    // Scenarios live in the modal-level bar, not a tab.
    const slots = (setup?.slots || []).filter(s => !s.sealed);
    if (setup && slots.length) {
        const st = slotTabs(slots, [], f => (opts.slots || {})[f.key]);
        if (st.length) st[0].html +=
            '<div style="opacity:.7;font-size:.85em">Changes land on her next turn after Save. Sealed blanks are edited from the ✍ chips in the scene panel.</div>';
        tabs.push(...st);
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

    let modal = null;
    modal = buildModal(
        `&#x2699;&#xFE0E; ${esc(data.title || slug)} settings`, tabs,
        `<button type="button" class="pk-btn pk-btn-primary grs-save">Save</button>
         <button type="button" class="pk-btn grs-reset" title="Restore defaults into the form (Save to apply)">Reset</button>`,
        setup ? scenarioBarHtml(Object.keys(setup.scenarios || {})) : '',
        { guard: () => {
            const d = modal.dirty() || envTab?.dirty?.();
            return !d || confirm('Discard your unsaved changes?');
        } });
    const { overlay, close } = modal;
    if (setup)
        wireScenarioBar(overlay, slug, setup, slots, opts.slots || {},
                          { session: opts.session,
                            envFlush: () => envTab?.flush?.(),
                            envDiverged: () => !!(envTab && envTab.diverged()),
                            onSwap: () => envTab?.swapped?.(),
                            onSaved: () => envTab?.markClean?.() });

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
            if (setup && slots.length) {
                const vals = {};
                for (const s of slots) vals[s.key] = readField(overlay, s.key) ?? '';
                await api('story/slots', 'POST', { session: opts.session, slots: vals });
            }
            if (envTab?.flush) await envTab.flush();   // pending room edits
            ui.showToast('Settings saved — live on the next turn', 'success', 2500);
            close();
        } catch (e) { ui.showToast(e.message, 'error'); }
    };
    overlay.querySelector('.grs-reset').onclick = () => {
        for (const f of schema) writeField(overlay, f.key, f.default ?? (f.type === 'checkbox' ? false : ''));
        for (const s of slots) writeField(overlay, s.key, s.default);
    };
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
        wireScenarioBar(overlay, slug, setup, open, {},
                              { session,
                                envFlush: () => envTab?.flush?.(),
                                envDiverged: () => !!(envTab && envTab.diverged()),
                                onSwap: () => envTab?.swapped?.(),
                                onSaved: () => envTab?.markClean?.() });

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
            <div class="grs-room-stats">
                <div class="grs-room-stats-head">In this room</div>
                <div class="grs-room-stats-line"></div>
            </div>
        </div>
        <div class="grs-room-desc-row">
            <div class="sb-field sb-field-stack grs-room-desc">
                <label>Full description (<span class="grs-obj-count">0</span>/900)</label>
                <textarea class="grs-obj-template" rows="6" title="what she reads — the room's reality"></textarea>
            </div>
            <img class="grs-room-thumb" style="display:none" alt="room image" title="the room's baked-in art (view only)">
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
        const pending = {};
        const pendingExits = {};   // rid → add_exits list (same save verbs)
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
        tab.dirty = () => !!(Object.keys(pending).length || Object.keys(pendingExits).length);
        tab.hasContent = () =>
            Object.values(world.objects || {}).some(o => Object.keys(o || {}).length)
            || (world.rooms || []).some(r => r.template || r.player_desc
                                             || (r.add_exits || []).length);
        // Divergence, not existence (Krem 2026-08-20: "prompt only when the
        // current values don't match the loaded scenario"). markClean stamps
        // the canvas right after a swap/save; diverged() compares against it.
        // Before any stamp the baseline is unknown — protect if content.
        const canvasFp = () => JSON.stringify([
            world.objects || {},
            (world.rooms || []).map(r => [r.template || '', r.player_desc || '',
                                          r.add_exits || []]),
            pending, pendingExits]);
        let cleanFp = null;
        tab.markClean = () => { cleanFp = canvasFp(); };
        tab.diverged = () => cleanFp === null
            ? (tab.hasContent() || tab.dirty())
            : canvasFp() !== cleanFp;
        // A scenario swap replaced the canvas: pendings are stale, repaint,
        // and the fresh canvas IS the scenario — stamp it clean.
        tab.swapped = async () => {
            [pending, pendingExits].forEach(m => Object.keys(m).forEach(k => delete m[k]));
            await refresh();
            tab.markClean();
        };

        // Effective object view: shipped spec + user shadow (desc/hidden
        // replace, verb messages overlay) — mirrors the server merge law.
        const effective = (so, ov) => {
            const verbs = { ...(so?.verbs || {}) };
            for (const [v, s] of Object.entries(ov?.interactions || {}))
                verbs[v] = String((s || {}).message ?? verbs[v] ?? '');
            return { desc: ov?.desc ?? so?.desc ?? '',
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
            const thumb = pane.querySelector('.grs-room-thumb');
            if (r.backdrop) { thumb.src = r.backdrop; thumb.style.display = ''; }
            else { thumb.style.display = 'none'; }
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
                const ints = Object.values(spec.interactions || {});
                const marks = (spec.hidden || spec.condition ? ' \u{1F32B}\u{FE0F}' : '')
                    + (spec.takeable ? ' \u{1F392}' : '')
                    + (spec.puzzle || ints.some(v => v && v.condition) ? ' \u{1F512}' : '')
                    + (ints.some(v => v && v.roll) ? ' \u{1F3B2}' : '');
                cards.push(`<div class="grs-obj-card grs-obj-editable" data-name="${esc(n)}">
                    <button type="button" class="sb-icon-btn grs-obj-del" data-name="${esc(n)}" title="Remove">✕</button>
                    <div class="grs-obj-card-title">${esc(n)}${authorOf(spec)}${marks}</div>
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
            // ONE ground truth for names (Krem 2026-08-20): badges show the
            // DESTINATION ROOM'S TITLE — same names as the Room dropdown.
            // The author's flavor label ("the parlor door") lives in the
            // tooltip; in play she still moves by that label.
            const roomTitle = (id) => (world.rooms.find(x => x.id === id) || {}).title;
            badges.innerHTML = (r.shipped_exits || []).map(e =>
                `<span class="grs-exit-badge" title="${esc(e.label || '')}">${esc(roomTitle(e.to) || e.label || e.to)}</span>`).join('')
                + added.map((e, i) =>
                `<span class="grs-exit-badge grs-exit-user" title="${esc(e.label)}">${esc(roomTitle(e.to) || e.label)}
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
            clearLocks();
            objName.value = editing || '';
            objName.disabled = !!editing;
            const so = editing ? (r.shipped_objs || {})[editing] : null;
            const ov = editing ? ((world.objects || {})[String(r.id)] || {})[editing] : null;
            if (so) {
                const eff = effective(so, ov);
                objDesc.value = eff.desc;
                for (const [v, m] of Object.entries(eff.verbs)) addActRow(v, m);
                mechNote.style.display = so.has_mechanics ? '' : 'none';
                resetBtn.style.display = ov ? '' : 'none';
                // pack mechanics stay pack-side — no lock authoring on shipped
                visLabel.style.display = 'none';
                reqLabel.style.display = 'none';
                fxLabel.style.display = 'none';
                takeLabel.style.display = 'none';
            } else if (editing && ov) {
                objDesc.value = ov.desc || '';
                for (const [v, s] of Object.entries(ov.interactions || {}))
                    addActRow(v, (s || {}).message
                        || ((s || {}).roll && s.roll.success && s.roll.success.message) || '');
                prefillLocks(ov);
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

        // ── Locks & effects (Krem 2026-08-20): (type, value) rows compiling
        // into the referee's SHIPPED grammar — condition {has,did}, puzzle +
        // {solved}, roll, set/adjust/gives. First row of each kind wins.
        const visToggle = addForm.querySelector('.grs-vis-toggle');
        const reqToggle = addForm.querySelector('.grs-req-toggle');
        const fxToggle = addForm.querySelector('.grs-fx-toggle');
        const visLabel = addForm.querySelector('.grs-vis-label');
        const reqLabel = addForm.querySelector('.grs-req-label');
        const fxLabel = addForm.querySelector('.grs-fx-label');
        const visBody = addForm.querySelector('.grs-vis-body');
        const reqBody = addForm.querySelector('.grs-req-body');
        const fxBody = addForm.querySelector('.grs-fx-body');
        const visRows = addForm.querySelector('.grs-vis-rows');
        const reqRows = addForm.querySelector('.grs-req-rows');
        const fxRows = addForm.querySelector('.grs-fx-rows');
        const lockMsg = addForm.querySelector('.grs-lock-msg');
        const objTake = addForm.querySelector('.grs-obj-take');
        const takeLabel = addForm.querySelector('.grs-take-label');
        visToggle.onchange = () => { visBody.style.display = visToggle.checked ? '' : 'none'; };
        reqToggle.onchange = () => {
            reqBody.style.display = reqToggle.checked ? '' : 'none';
            // A lock wants a verb — offer 'open' as a visible, editable
            // action row (Krem's clown_chest, 2026-08-20: requiring the
            // user to hand-author the obvious verb was friction, not law).
            if (reqToggle.checked && !actRows.children.length)
                addActRow('open', '');
        };
        fxToggle.onchange = () => {
            fxBody.style.display = fxToggle.checked ? '' : 'none';
            // Effects need a carrier verb — offer 'use' as a visible,
            // editable action row rather than implying one silently
            // (Krem 2026-08-20: the response field IS the return message).
            if (fxToggle.checked && !actRows.children.length)
                addActRow('use', '').querySelector('.grs-act-resp').focus();
        };

        // Visible-when = EXISTENCE (she doesn't know it's there — surprise);
        // Requirements = USABLE-when (she sees it, it refuses — tension).
        // Both compile to the referee's shipped condition grammar.
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
            ['give', 'give item', 'item name, e.g. bronze_key', ''],
            ['adjust', 'adjust number', 'name, e.g. love', 'amount, e.g. 10 or -5'],
        ];
        const pickRow = (host, types, kind, val, extra) => {
            const row = document.createElement('div');
            row.className = 'grs-act-row';
            row.innerHTML = `
                <select class="grs-pick-kind">${types.map(t =>
                    `<option value="${t[0]}"${t[0] === kind ? ' selected' : ''}>${t[1]}</option>`).join('')}</select>
                <input type="text" class="grs-pick-val">
                <input type="text" class="grs-pick-extra">
                <button type="button" class="sb-icon-btn grs-act-del" title="Remove">✕</button>`;
            const sel = row.querySelector('.grs-pick-kind');
            const vIn = row.querySelector('.grs-pick-val');
            const xIn = row.querySelector('.grs-pick-extra');
            const paint = () => {
                const t = types.find(x => x[0] === sel.value) || types[0];
                vIn.placeholder = t[2];
                xIn.placeholder = t[3];
                vIn.style.display = t[2] ? '' : 'none';   // value-less kinds (searched)
                xIn.style.display = t[3] ? '' : 'none';
            };
            sel.onchange = paint;
            paint();
            vIn.value = val || '';
            xIn.value = extra || '';
            row.querySelector('.grs-act-del').onclick = () => row.remove();
            host.appendChild(row);
            return row;
        };
        addForm.querySelector('.grs-vis-add').onclick = () =>
            pickRow(visRows, VIS_TYPES).querySelector('.grs-pick-kind').focus();
        addForm.querySelector('.grs-req-add').onclick = () =>
            pickRow(reqRows, REQ_TYPES).querySelector('.grs-pick-val').focus();
        addForm.querySelector('.grs-fx-add').onclick = () =>
            pickRow(fxRows, FX_TYPES).querySelector('.grs-pick-val').focus();

        const rawRows = (host) => [...host.querySelectorAll('.grs-act-row')].map(row => ({
            kind: row.querySelector('.grs-pick-kind').value,
            val: row.querySelector('.grs-pick-val').value.trim(),
            extra: row.querySelector('.grs-pick-extra').value.trim(),
        }));
        const rowsOf = (host) => rawRows(host).filter(x => x.val);

        const clearLocks = () => {
            for (const t of [visToggle, reqToggle, fxToggle, objTake]) t.checked = false;
            for (const b of [visBody, reqBody, fxBody]) b.style.display = 'none';
            for (const l of [visLabel, reqLabel, fxLabel, takeLabel]) l.style.display = '';
            for (const h of [visRows, reqRows, fxRows]) h.innerHTML = '';
            lockMsg.value = '';
        };

        // Visible-when rows → the object's top-level existence gate:
        // `hidden` (search reveal) and/or `condition` {has, did, flag}.
        const readVis = () => {
            if (!visToggle.checked) return null;
            const out = { hidden: false, cond: {} };
            for (const q of rawRows(visRows)) {
                if (q.kind === 'searched') out.hidden = true;
                else if (!q.val) continue;
                else if (q.kind === 'has' && !out.cond.has) out.cond.has = q.val;
                else if (q.kind === 'did' && !out.cond.did) out.cond.did = q.val;
                else if (q.kind === 'flag' && !out.cond.flag) out.cond.flag = q.val;
            }
            if (!Object.keys(out.cond).length) out.cond = null;
            return (out.hidden || out.cond) ? out : null;
        };

        const readLocks = (name) => {
            if (!reqToggle.checked && !fxToggle.checked) return null;
            const cond = {};
            let puzzle = null, dice = null;
            if (reqToggle.checked) for (const q of rowsOf(reqRows)) {
                if (q.kind === 'has' && !cond.has) cond.has = q.val;
                else if (q.kind === 'did' && !cond.did) cond.did = q.val;
                else if (q.kind === 'flag' && !cond.flag) cond.flag = q.val;
                else if (q.kind === 'password' && !puzzle) {
                    puzzle = { riddle: q.extra || 'It waits for the right answer — solve it.',
                               solution: q.val };
                    cond.solved = name;
                } else if ((q.kind === 'd20' || q.kind === 'd100') && !dice) {
                    const sides = q.kind === 'd20' ? 20 : 100;
                    const beat = parseInt(q.val, 10);
                    dice = { sides, beat: Number.isFinite(beat)
                             ? Math.max(1, Math.min(sides, beat)) : sides / 2 + 1 };
                }
            }
            const fx = {};
            if (fxToggle.checked) for (const q of rowsOf(fxRows)) {
                if (q.kind === 'set') (fx.set = fx.set || {})[q.val] = true;
                else if (q.kind === 'give' && !fx.gives) fx.gives = q.val;
                else if (q.kind === 'adjust') {
                    const n = parseFloat(q.extra);
                    if (Number.isFinite(n)) (fx.adjust = fx.adjust || {})[q.val] = n;
                }
            }
            const out = { cond: Object.keys(cond).length ? cond : null,
                          puzzle, dice,
                          fx: Object.keys(fx).length ? fx : null,
                          msg: lockMsg.value.trim() };
            return (out.cond || out.puzzle || out.dice || out.fx) ? out : null;
        };

        // spec → rows (edit round-trip for user-placed objects)
        const prefillLocks = (spec) => {
            const v0 = Object.values(spec.interactions || {})[0] || {};
            const cond = v0.condition || {};
            const src = v0.roll ? (v0.roll.success || {})
                : (spec.interactions ? v0 : (spec.on_solve || {}));
            let anyReq = false, anyFx = false, anyVis = false;
            objTake.checked = !!spec.takeable;
            if (spec.hidden) { pickRow(visRows, VIS_TYPES, 'searched'); anyVis = true; }
            const tc = spec.condition || {};   // top-level = existence gate
            if (tc.has) { pickRow(visRows, VIS_TYPES, 'has', tc.has); anyVis = true; }
            if (tc.did) { pickRow(visRows, VIS_TYPES, 'did', tc.did); anyVis = true; }
            if (tc.flag) { pickRow(visRows, VIS_TYPES, 'flag', tc.flag); anyVis = true; }
            if (cond.has) { pickRow(reqRows, REQ_TYPES, 'has', cond.has); anyReq = true; }
            if (cond.did) { pickRow(reqRows, REQ_TYPES, 'did', cond.did); anyReq = true; }
            if (cond.flag) { pickRow(reqRows, REQ_TYPES, 'flag', cond.flag); anyReq = true; }
            if (spec.puzzle) {
                pickRow(reqRows, REQ_TYPES, 'password',
                        (spec.puzzle.solutions || [])[0] || spec.puzzle.solution || '',
                        spec.puzzle.riddle || '');
                anyReq = true;
            }
            if (v0.roll) {
                pickRow(reqRows, REQ_TYPES, v0.roll.sides === 20 ? 'd20' : 'd100',
                        String(v0.roll.beat ?? ''));
                anyReq = true;
            }
            for (const k of Object.keys(src.set || {})) { pickRow(fxRows, FX_TYPES, 'set', k); anyFx = true; }
            if (src.gives) { pickRow(fxRows, FX_TYPES, 'give', src.gives); anyFx = true; }
            for (const [k, n] of Object.entries(src.adjust || {})) {
                pickRow(fxRows, FX_TYPES, 'adjust', k, String(n));
                anyFx = true;
            }
            if (v0.blocked_message) { lockMsg.value = v0.blocked_message; anyReq = true; }
            visToggle.checked = anyVis;
            visBody.style.display = anyVis ? '' : 'none';
            reqToggle.checked = anyReq;
            reqBody.style.display = anyReq ? '' : 'none';
            fxToggle.checked = anyFx;
            fxBody.style.display = anyFx ? '' : 'none';
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
                spec = { desc: objDesc.value.trim() };
                const acts = {};
                actRows.querySelectorAll('.grs-act-row').forEach(row => {
                    const verb = row.querySelector('.grs-act-verb').value.trim();
                    if (!verb) return;
                    const resp = row.querySelector('.grs-act-resp').value.trim();
                    acts[verb] = { message: resp || `You ${verb} the ${name}.` };
                });
                if (objTake.checked) spec.takeable = true;
                const vis = readVis();
                if (vis) {
                    if (vis.hidden) spec.hidden = true;
                    if (vis.cond) spec.condition = vis.cond;
                }
                const lk = readLocks(name);
                if (lk) {
                    if (!Object.keys(acts).length && !lk.puzzle) {
                        ui.showToast('Requirements & effects need at least one action — or a password to solve.',
                                     'error', 3500);
                        return;
                    }
                    if (lk.puzzle) spec.puzzle = lk.puzzle;
                    for (const v of Object.values(acts)) {
                        if (lk.cond) {
                            v.condition = lk.cond;
                            if (lk.msg) v.blocked_message = lk.msg;
                        }
                        if (lk.dice) {
                            // chance replaces the flat outcome: response +
                            // effects ride the success branch
                            v.roll = { ...lk.dice,
                                       success: { message: v.message, ...(lk.fx || {}) },
                                       failure: { message: 'Not this time — the attempt fails.' } };
                            delete v.message;
                        } else if (lk.fx) {
                            Object.assign(v, lk.fx);
                        }
                    }
                    if (!Object.keys(acts).length && lk.puzzle && lk.fx)
                        spec.on_solve = { ...lk.fx };   // pure riddle: effects on solve
                }
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
