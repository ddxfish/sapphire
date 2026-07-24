// views/palace/self.js - Mind › Self, palace edition (L0: the self layer).
// The self-sheet as section cards: autosave textareas for the typed sections,
// a key-value editor for handles, custom boxes with +add, version history for
// the versioned sections (identity/values/projects), and the live dashboard —
// computed from mind.db at read time, read-only by construction.
import { renderSectionHeader, bindSectionHeader } from '../../shared/section-header.js';
import { helpPills } from '../../features/video-link.js';
import { renderScopeSidebar, bindScopeSidebar } from '../../shared/scope-sidebar.js';
import { listScopes } from '../../shared/scope-api.js';
import { escHtml, escAttr, timeAgo, scopeForChatTab, subscribeMindDomain } from '../../shared/mind-common.js';
import { setupModalClose } from '../../shared/modal.js';
import * as ui from '../../ui.js';
import { PALACE_TABS, refreshPalaceTabs, SCOPE_ENDPOINT, palaceGet, palaceSend, describeScopeForDelete, transferButtons, bindTransfer, rememberMindScope, recallMindScope } from './common.js';

const SCOPE_KEY = 'memory_scope';
const MAX_CHARS = 2000;
const MODE_CHIPS = {
    'hand': { icon: '✍️', tip: 'Hand-authored — you and Sapphire edit this' },
    'librarian-regen': { icon: '\u{1F319}', tip: 'The librarian will rewrite this nightly (v1: hand-authored)' },
    'computed': { icon: '⚙️', tip: 'Computed — read-only' },
};

let container = null;
let scope = 'default';
let scopes = [];
let unsub = null;
let _saveTimers = {};
let _localBoxes = [];   // client-side boxes not yet persisted

export default {
    init(el) { container = el; },
    async show() {
        await refreshPalaceTabs();
        // Editor semantics: skip SSE refresh while ANY self card holds focus
        // (buttons included — a re-render mid-edit wipes unsaved input) or a
        // save is still pending (the 'conse' truncation class, 2026-07-12).
        if (!unsub) unsub = subscribeMindDomain('memory', () => scope,
            () => container?.offsetParent !== null
                && !container.querySelector('.palace-self-card:focus-within')
                && !Object.keys(_saveTimers).length,
            renderSheet);
        if (window._mindScope) { scope = window._mindScope; delete window._mindScope; }
        else { const s = recallMindScope() || await scopeForChatTab(SCOPE_KEY); if (s) scope = s; }
        delete window._mindTab;
        scopes = await listScopes(SCOPE_ENDPOINT);
        if (scope !== 'default' && !scopes.some(x => x.name === scope)) scope = 'default';
        rememberMindScope(scope);
        render();
    },
    hide() { if (unsub) { unsub(); unsub = null; } }
};

function content() { return container?.querySelector('#pal-self-content'); }

function render() {
    if (!container) return;
    container.innerHTML = `
        ${renderSectionHeader({ tabs: PALACE_TABS, active: 'self', help: helpPills('Self', { doc: 'MEMORY.md', inline: true }), status: '\u{1F48D} Mind Palace — the self sheet (L0). Who she is, in her own words. Sections autosave; prior identity/values/growing versions are archived, never lost.' })}
        <div class="two-panel">
            ${renderScopeSidebar(scopes, scope)}
            <div class="panel-right">
                <div class="view-body view-scroll" id="pal-self-content"></div>
            </div>
        </div>`;
    bindSectionHeader(container);
    bindScopeSidebar(container, {
        describeScope: describeScopeForDelete,
        onScopeChange: (s) => { scope = s; rememberMindScope(s); _localBoxes = []; render(); },
        onChanged: async (s) => { scope = s || 'default'; rememberMindScope(scope); _localBoxes = []; scopes = await listScopes(SCOPE_ENDPOINT); render(); },
    });
    renderSheet();
}

async function renderSheet() {
    const el = content();
    if (!el) return;
    // Pending saves fire from the still-attached DOM and land BEFORE we read
    // server state — a stale timer surviving into a re-render used to save
    // from a detached card and truncate mid-word ('consent' → 'conse').
    await flushAllSaves();
    let data;
    try {
        data = await palaceGet(`self?scope=${encodeURIComponent(scope)}`);
    } catch (e) {
        el.innerHTML = `<div class="mind-empty">Failed to load: ${escHtml(e.message)}</div>`;
        return;
    }
    const persisted = new Set(data.custom.map(c => c.section));
    _localBoxes = _localBoxes.filter(b => !persisted.has(b.section));

    const customCard = (c) => {
        const base = { ...c, title: `[${c.section}]`, mode: 'hand', versioned: false, custom: true,
                       hint: c.fields ? 'custom list' : 'custom box' };
        return c.fields ? structCard(base) : sectionCard(base);
    };
    const localCard = (b) => b.fields
        ? structCard({ section: b.section, title: `[${b.section}]`, hint: 'custom list — saves when you add rows',
                       mode: 'hand', custom: true, fields: b.fields, rows: [], width: 'half', history_count: 0 })
        : sectionCard({ section: b.section, title: `[${b.section}]`, hint: 'custom box — saves when you write',
                        mode: 'hand', custom: true, content: '', width: 'third', history_count: 0 });

    el.innerHTML = `
        ${dashboardCard(data.dashboard)}
        <div class="palace-self-grid">
            ${data.sections.map(s => s.fields ? structCard(s) : sectionCard(s)).join('')}
            ${data.custom.map(customCard).join('')}
            ${_localBoxes.map(localCard).join('')}
            <div id="pal-wake-tools" class="mind-mem-card palace-self-card pal-w-half"></div>
        </div>
        <div class="palace-more-wrap">
            <button class="mind-btn" id="pal-self-addbox">+ Add box</button>
            ${transferButtons()}
        </div>
        <div style="display:flex;gap:16px;align-items:flex-start;flex-wrap:wrap">
            <!-- flex-basis 0 = columns size by RATIO, not content — long ledger
                 lines wrap (overflow-wrap inherits) instead of stealing width -->
            <div id="pal-ledger" style="flex:3 1 0;min-width:340px;overflow-wrap:anywhere"></div>
            <div id="pal-upcoming" style="flex:2 1 0;min-width:280px;overflow-wrap:anywhere">${upcomingCard(data.dashboard)}</div>
        </div>
    `;
    bindCards(el);
    bindLibrarian(el);
    bindTransfer(el, 'self', () => scope, ui, renderSheet);
    renderLedger(el);
    renderWakeTools(el);
    renderResident(el);
}

// ─── Resident rows — who tends this scope (2026-07-19) ──────────────────────
// Lives INSIDE the Dashboard card (replacing the old Librarian status line;
// the Admin button stays). Row 1: prompt + provider/model, chat-sidebar
// style. Row 2: one labeled slide toggle per librarian pass — per-scope
// nightly opt-ins, DEFAULT OFF, gated on top by the global Admin toggles.
// Blank prompt/provider = the classic fallbacks (librarian chat persona /
// auto provider / global librarian model).

const RESIDENT_PASSES = [
    { key: 'dates', label: 'Dates', title: 'Resolve date mentions into calendar dates' },
    { key: 'link', label: 'Link', title: 'Connect memories to people/places/things' },
    { key: 'dedup', label: 'Dedup', title: 'Fold measured near-duplicates' },
    { key: 'sort', label: 'Sort', title: 'Review: keep, split, promote, retire' },
    { key: 'self', label: 'Self', title: 'Tend the self sheet, then verify' },
];

async function renderResident(el) {
    const row = el.querySelector('#pal-resident');
    const passRow = el.querySelector('#pal-res-passes');
    if (!row || !passRow) return;
    let res = {}, prompts = [], providers = [], metadata = {};
    try {
        const [{ getPrompts }, { fetchLLMProviders }] = await Promise.all([
            import('../../shared/init-data.js'),
            import('../../shared/continuity-api.js'),
        ]);
        const [r, p, llm] = await Promise.all([
            palaceGet(`resident?scope=${encodeURIComponent(scope)}`),
            getPrompts().catch(() => null),
            fetchLLMProviders().catch(() => ({})),
        ]);
        res = r.resident || {};
        prompts = ((p && p.list) || [])
            .map(x => typeof x === 'string' ? x : (x.name || ''))
            .filter(Boolean);
        providers = (llm.providers || []).filter(x => x.enabled);
        metadata = llm.metadata || {};
    } catch {
        row.querySelector('.palace-lib-status').textContent = 'unavailable';
        return;
    }
    // Per-select option lists: a since-deleted current value stays selectable
    // in ITS dropdown only, never leaking into the sibling one.
    const promptOpts = [...prompts];
    if (res.prompt && !promptOpts.includes(res.prompt)) promptOpts.push(res.prompt);
    const watchOpts = [...prompts];
    if (res.watched_prompt && !watchOpts.includes(res.watched_prompt)) watchOpts.push(res.watched_prompt);

    const save = async (patch) => {
        try {
            await palaceSend('resident', 'PUT', { scope, ...patch });
        } catch (e) { ui.showToast(`Resident save failed: ${e.message}`, 'error'); }
    };

    row.querySelector('.palace-lib-status').outerHTML = `
        <select id="pal-res-prompt" class="palace-select" title="Prompt the librarian speaks as when tending this scope. Blank = the librarian chat's persona.">
            <option value="">prompt: default</option>
            ${promptOpts.map(p => `<option value="${escAttr(p)}" ${p === res.prompt ? 'selected' : ''}>${escHtml(p)}</option>`).join('')}
        </select>
        <select id="pal-res-provider" class="palace-select" title="Provider for this scope's librarian passes">
            <option value="">provider: auto</option>
            ${providers.map(pr => `<option value="${escAttr(pr.key)}" ${pr.key === res.provider ? 'selected' : ''}>${escHtml(pr.name || pr.key)}</option>`).join('')}
        </select>
        <select id="pal-res-model" class="palace-select" title="Model for this scope's librarian passes"></select>`;

    const provSel = row.querySelector('#pal-res-provider');
    const modelSel = row.querySelector('#pal-res-model');
    const updateModels = () => {
        // Chat-sidebar pattern: core providers list model_options; custom
        // providers have the model baked in; no provider = global default.
        const key = provSel.value;
        const pConfig = providers.find(x => x.key === key);
        const opts = (metadata[key] || {}).model_options || {};
        if (!key) {
            modelSel.innerHTML = `<option value="">model: global default</option>`;
            modelSel.disabled = true;
            return;
        }
        if (pConfig && pConfig.is_core === false) {
            modelSel.innerHTML = `<option value="">${escHtml(pConfig.model || '(provider model)')}</option>`;
            modelSel.disabled = true;
            return;
        }
        modelSel.disabled = false;
        modelSel.innerHTML = `<option value="">provider default</option>`
            + Object.entries(opts).map(([k, v]) =>
                `<option value="${escAttr(k)}" ${k === res.model ? 'selected' : ''}>${escHtml(v)}</option>`).join('');
        if (res.model && !opts[res.model]) {
            modelSel.innerHTML += `<option value="${escAttr(res.model)}" selected>${escHtml(res.model)}</option>`;
        }
    };
    updateModels();

    row.querySelector('#pal-res-prompt').addEventListener('change',
        (e) => save({ prompt: e.target.value }));
    provSel.addEventListener('change', () => {
        res.model = '';
        updateModels();
        save({ provider: provSel.value, model: '' });
    });
    modelSel.addEventListener('change', () => save({ model: modelSel.value }));

    const passes = res.passes || {};
    passRow.innerHTML = `
        <span class="palace-lib-title" title="Nightly librarian passes THIS scope opts into. Default off — the global Admin toggles gate these on top. Manual ▶ Run in Admin always works.">\u{1F9F9} Librarian</span>
        ${RESIDENT_PASSES.map(p => `
            <span style="display:inline-flex;align-items:center;gap:6px" title="${escAttr(p.title)} — nightly opt-in for this scope">
                <span class="ui-meta-text">${p.label}</span>
                <label class="ui-toggle">
                    <input type="checkbox" data-res-pass="${p.key}" ${passes[p.key] ? 'checked' : ''}>
                    <span class="ui-toggle-slider"></span>
                </label>
            </span>`).join('')}
    `;
    passRow.querySelectorAll('[data-res-pass]').forEach(cb =>
        cb.addEventListener('change', () => {
            const next = {};
            passRow.querySelectorAll('[data-res-pass]').forEach(x =>
                next[x.dataset.resPass] = x.checked);
            save({ passes: next });
        }));

    // Ledger row (2026-07-23): the prompt-ledger opt-out + its watch target.
    // The toggle gates RECORDING for this scope; the flip itself lands in the
    // ledger, so a blind window always starts with a visible row.
    const ledgerRow = el.querySelector('#pal-res-ledger');
    if (!ledgerRow) return;
    const ledgerOn = res.prompt_ledger !== false;
    ledgerRow.innerHTML = `
        <span class="palace-lib-title" title="Tamper watch: changes to the watched persona's prompt land in this scope's ledger. Off = prompt changes go unrecorded for this scope (the flip itself is recorded).">\u{1F4DC} Ledger</span>
        <span style="display:inline-flex;align-items:center;gap:6px">
            <span class="ui-meta-text">Prompt changes</span>
            <label class="ui-toggle">
                <input type="checkbox" id="pal-res-ledger-on" ${ledgerOn ? 'checked' : ''}>
                <span class="ui-toggle-slider"></span>
            </label>
        </span>
        <select id="pal-res-watched" class="palace-select" ${ledgerOn ? '' : 'disabled'} title="Which persona's prompt this scope's ledger tracks. Blank = the resident prompt.">
            <option value="">watch: resident prompt</option>
            ${watchOpts.map(p => `<option value="${escAttr(p)}" ${p === res.watched_prompt ? 'selected' : ''}>watch: ${escHtml(p)}</option>`).join('')}
        </select>`;
    const watchedSel = ledgerRow.querySelector('#pal-res-watched');
    watchedSel.addEventListener('change',
        (e) => save({ watched_prompt: e.target.value }));
    ledgerRow.querySelector('#pal-res-ledger-on').addEventListener('change', (e) => {
        watchedSel.disabled = !e.target.checked;
        save({ prompt_ledger: e.target.checked });
    });
}

// ─── Wake tools card — user-armed live checks at wake (2026-07-16) ──────────
// +Add Tool arms a tool call that read_self(depth>=1) executes and appends.
// HUMAN-ARMED ONLY: this card is the sole write surface (update_self refuses
// the section) — the consent boundary that makes autorun safe.

async function renderWakeTools(el) {
    const box = el.querySelector('#pal-wake-tools');
    if (!box) return;
    let data, opts;
    try {
        [data, opts] = await Promise.all([
            palaceGet(`wake-tools?scope=${encodeURIComponent(scope)}`),
            palaceGet('wake-tools/options'),
        ]);
    } catch { box.innerHTML = ''; return; }
    const byName = Object.fromEntries((opts.tools || []).map(t => [t.name, t]));
    const rows = data.tools || [];
    const paramSummary = p => Object.entries(p || {})
        .filter(([, v]) => v !== '' && v != null)
        .map(([k, v]) => `${escHtml(k)}=${escHtml(String(v))}`).join(' · ');
    box.innerHTML = `
        <div class="palace-self-card-head">
            <span class="palace-self-title">🔧 Wake tools</span>
            <span class="palace-self-hint">${rows.length}/${data.max_rows}</span>
        </div>
        <div class="palace-self-hint">Run automatically at wake (read_self depth ≥ 1); results land at the end of her sheet. Armed here only — she can't edit this list herself.</div>
        <div class="pal-wake-list" style="display:flex;flex-direction:column;gap:6px;margin-top:6px">
            ${rows.map(r => `
            <div class="pal-wake-row" data-id="${r.id}" style="border:1px solid var(--border,#333);border-radius:6px;padding:6px 8px;${r.enabled ? '' : 'opacity:.55'}">
                <div style="display:flex;align-items:center;gap:8px">
                    <input type="checkbox" class="pal-wake-en" ${r.enabled ? 'checked' : ''} title="Run at wake">
                    <b style="flex:1;overflow-wrap:anywhere">${escHtml(r.tool)}</b>
                    <input type="number" class="pal-wake-chars" value="${r.max_chars ?? data.default_chars}" min="128" max="4096" title="Max result characters shown at wake" style="width:72px">
                    <button class="mind-btn-sm pal-wake-del" title="Remove">✕</button>
                </div>
                ${paramSummary(r.params) ? `<div class="palace-self-hint" style="margin:2px 0 0 24px">${paramSummary(r.params)}</div>` : ''}
            </div>`).join('') || '<div class="palace-self-hint">No wake tools yet — she wakes with her sheet alone.</div>'}
        </div>
        <div class="pal-wake-add" style="margin-top:8px">
            ${rows.length < data.max_rows ? `
            <select id="pal-wake-pick" class="mind-btn-sm" style="max-width:100%">
                <option value="">+ Add tool…</option>
                ${(opts.tools || []).map(t => `<option value="${escAttr(t.name)}">${escHtml(t.name)}</option>`).join('')}
            </select>
            <div id="pal-wake-form" hidden style="margin-top:6px"></div>` : ''}
        </div>`;
    bindWakeTools(el, box, byName, data.default_chars);
}

function bindWakeTools(el, box, byName, defChars) {
    box.querySelectorAll('.pal-wake-row').forEach(row => {
        const id = row.dataset.id;
        row.querySelector('.pal-wake-en')?.addEventListener('change', async e => {
            try {
                await palaceSend(`wake-tools/${id}`, 'PUT', { enabled: e.target.checked });
                renderWakeTools(el);
            } catch (err) { ui.showToast(err.message, 'error'); }
        });
        row.querySelector('.pal-wake-chars')?.addEventListener('change', async e => {
            try { await palaceSend(`wake-tools/${id}`, 'PUT', { max_chars: Number(e.target.value) }); }
            catch (err) { ui.showToast(err.message, 'error'); }
        });
        row.querySelector('.pal-wake-del')?.addEventListener('click', async () => {
            if (!confirm('Remove this wake tool?')) return;
            try {
                await palaceSend(`wake-tools/${id}`, 'DELETE');
                renderWakeTools(el);
            } catch (err) { ui.showToast(err.message, 'error'); }
        });
    });
    const pick = box.querySelector('#pal-wake-pick');
    pick?.addEventListener('change', () => {
        const spec = byName[pick.value];
        const form = box.querySelector('#pal-wake-form');
        if (!spec || !form) { if (form) form.hidden = true; return; }
        form.hidden = false;
        form.innerHTML = `
            ${spec.description ? `<div class="palace-self-hint">${escHtml(spec.description)}</div>` : ''}
            ${spec.params.map(p => `
                <input type="text" class="pal-wake-param" data-key="${escAttr(p.key)}" data-type="${escAttr(p.type)}"
                    placeholder="${escAttr(p.key)}${p.required ? ' *' : ''} — ${escAttr(p.description || p.type)}"
                    style="width:100%;margin-top:4px">`).join('')}
            <div style="display:flex;gap:6px;margin-top:6px;align-items:center">
                <label class="palace-self-hint">max chars <input type="number" id="pal-wake-new-chars" value="${defChars}" min="128" max="4096" style="width:72px"></label>
                <button class="mind-btn-sm" id="pal-wake-save">Arm</button>
                <button class="mind-btn-sm" id="pal-wake-cancel">Cancel</button>
            </div>`;
        form.querySelector('#pal-wake-cancel').addEventListener('click', () => {
            pick.value = '';
            form.hidden = true;
        });
        form.querySelector('#pal-wake-save').addEventListener('click', async () => {
            const params = {};
            form.querySelectorAll('.pal-wake-param').forEach(inp => {
                const v = inp.value.trim();
                if (!v) return;
                const t = inp.dataset.type;
                params[inp.dataset.key] =
                    t === 'integer' ? parseInt(v, 10) :
                    t === 'number' ? Number(v) :
                    t === 'boolean' ? ['true', '1', 'yes', 'on'].includes(v.toLowerCase()) : v;
            });
            try {
                await palaceSend('wake-tools', 'POST', {
                    scope, tool: pick.value, params,
                    max_chars: Number(form.querySelector('#pal-wake-new-chars').value) || undefined,
                });
                ui.showToast(`Armed ${pick.value} at wake`, 'success');
                renderWakeTools(el);
            } catch (err) { ui.showToast(err.message, 'error'); }
        });
    });
}

// ─── Upcoming Events — bottom-right, beside the Ledger ──────────────────────
// A read-only view over meta.event_dates: the regex floor stamps at save
// time, so a dated memory lands here the moment it's created (SSE re-renders
// the sheet). Rides the sheet fetch (dashboard.upcoming) — no extra request.

function fmtEventDate(d) {
    const dt = new Date(d.length > 10 ? d : d + 'T12:00');   // noon guards TZ backslide
    if (isNaN(dt)) return d;
    const opts = { weekday: 'short', month: 'short', day: 'numeric' };
    if (dt.getFullYear() !== new Date().getFullYear()) opts.year = 'numeric';
    const day = dt.toLocaleDateString(undefined, opts);
    return d.length > 10 ? `${day} · ${d.slice(11)}` : day;
}

function upcomingCard(dash) {
    const items = dash?.upcoming || [];
    const rows = items.map(u => `
        <div class="palace-ledger-row" title="${escAttr(`[${u.id}] ${u.preview}`)}">
            <span class="palace-ledger-ts">${u.recurring ? '↻ ' : ''}${escHtml(fmtEventDate(u.date))}</span>
            <span class="palace-ledger-sum">${escHtml(u.preview)}</span>
        </div>`).join('');
    return `
        <div class="mind-mem-card palace-ledger">
            <div class="palace-self-card-head">
                <span class="palace-self-title">\u{1F5D3} Upcoming Events</span>
                <span class="palace-self-hint">${items.length ? `next ${items.length}` : ''}</span>
            </div>
            <div class="palace-self-hint">Dates her memories point to — stamped the moment a memory is saved.</div>
            ${rows || '<div class="mind-empty">Nothing on the calendar — mention a date in chat and it lands here.</div>'}
        </div>`;
}


// ─── The Ledger (v1) — append-only change stream, bottom of the sheet ───────
// Collapsed by default: header counts + the last 3 lines. Expanded: the full
// stream, unread divider at her last read, pass rows unfold their children.
// Read-only surface — every row is written by the backend seams.

const LEDGER_ICONS = { user: '\u{1F464}', ai: '\u{1F916}', librarian: '\u{1F9F9}', import: '\u{1F4E6}', system: '⚙️' };
let _ledgerOpen = false;
let _ledgerLimit = 30;
let _expandedKids = new Set();   // parent ids whose children stay open across re-renders

function ledgerLine(r) {
    return `<div class="palace-ledger-row">
        <span class="palace-ledger-ts">${escHtml((r.ts || '').slice(0, 10))}</span>
        <span class="palace-ledger-actor" title="${escAttr(r.actor)}">${LEDGER_ICONS[r.actor] || '·'}</span>
        <span class="palace-ledger-sum">${escHtml(r.summary)}</span>
        ${r.children ? `<button class="mind-btn-sm palace-ledger-kids" data-id="${r.id}" data-n="${r.children}">▸ ${r.children}</button>` : ''}
        ${r.actor !== 'ai' ? `<button class="mind-btn-sm palace-ledger-why" data-id="${r.id}" data-reason="${escAttr((r.detail || {}).reason || '')}" title="Add or edit the why — it lands next to this change in her ledger">✏</button>` : ''}
    </div>`;
}

async function renderLedger(el) {
    const box = el.querySelector('#pal-ledger');
    if (!box) return;
    let data;
    try {
        data = await palaceGet(`ledger?scope=${encodeURIComponent(scope)}&limit=${_ledgerOpen ? _ledgerLimit : 3}`);
    } catch { box.innerHTML = ''; return; }
    const rows = data.rows || [];
    const lines = [];
    let divided = false;
    for (const r of rows) {
        if (_ledgerOpen && !divided && data.last_read_ts && r.ts <= data.last_read_ts && lines.length) {
            lines.push('<div class="palace-ledger-divider">— she has read to here —</div>');
            divided = true;
        }
        lines.push(ledgerLine(r));
    }
    box.innerHTML = `
        <div class="mind-mem-card palace-ledger">
            <div class="palace-self-card-head">
                <span class="palace-self-title">\u{1F4D2} Ledger</span>
                <span class="palace-self-hint">${data.week} this week · ${data.unread} since her last read</span>
                <button class="mind-btn-sm" id="pal-ledger-toggle">${_ledgerOpen ? '▾ collapse' : '▸ expand'}</button>
            </div>
            <div class="palace-self-hint">Every change to her memory, by whoever made it — append-only, read-only.</div>
            ${rows.length ? lines.join('') : '<div class="mind-empty">Nothing recorded yet — changes land here from now on.</div>'}
            ${(_ledgerOpen && rows.length < data.total)
                ? `<div class="palace-more-wrap"><button class="mind-btn-sm" id="pal-ledger-more">Load more (${data.total - rows.length} older)</button></div>`
                : ''}
        </div>`;
    box.querySelector('#pal-ledger-toggle')?.addEventListener('click', () => {
        _ledgerOpen = !_ledgerOpen;
        renderLedger(el);
    });
    box.querySelector('#pal-ledger-more')?.addEventListener('click', () => {
        _ledgerLimit += 30;
        renderLedger(el);
    });
    box.querySelectorAll('.palace-ledger-why').forEach(btn => {
        btn.addEventListener('click', async () => {
            const cur = btn.dataset.reason || '';
            const reason = prompt('Why? (what she’ll see next to this change — empty clears it)', cur);
            if (reason === null || reason === cur) return;
            try {
                await palaceSend(`ledger/${btn.dataset.id}/reason`, 'PUT', { scope, reason });
                renderLedger(el);
            } catch (e) { ui.showToast(`Reason save failed: ${e.message}`, 'error'); }
        });
    });
    box.querySelectorAll('.palace-ledger-kids').forEach(btn => {
        btn.addEventListener('click', async () => {
            const row = btn.closest('.palace-ledger-row');
            const open = row.nextElementSibling?.classList.contains('palace-ledger-children');
            if (open) {
                row.nextElementSibling.remove();
                btn.textContent = `▸ ${btn.dataset.n}`;
                _expandedKids.delete(btn.dataset.id);
                return;
            }
            try {
                const d = await palaceGet(`ledger?scope=${encodeURIComponent(scope)}&parent_id=${btn.dataset.id}`);
                row.insertAdjacentHTML('afterend',
                    `<div class="palace-ledger-children">${(d.rows || []).map(ledgerLine).join('')}</div>`);
                btn.textContent = `▾ ${btn.dataset.n}`;
                _expandedKids.add(btn.dataset.id);
            } catch (e) { ui.showToast(`Ledger children failed: ${e.message}`, 'error'); }
        });
    });
    // Re-open the children that were open before this re-render (a ✏ reason
    // save re-fetches the panel — expansion state must survive it)
    box.querySelectorAll('.palace-ledger-kids').forEach(btn => {
        if (_expandedKids.has(btn.dataset.id)) btn.click();
    });
}

function dashboardCard(d) {
    const stat = (n, label) => `<div class="palace-dash-stat"><div class="palace-dash-n">${n}</div><div class="palace-dash-l">${label}</div></div>`;
    return `
        <div class="palace-self-dash">
            <div class="palace-self-card-head">
                <span class="palace-self-title">⚙️ Dashboard</span>
                <span class="palace-self-hint">computed live — she reads, nothing writes</span>
            </div>
            <div class="palace-dash-grid">
                ${stat(d.events, `memories <span class="palace-dash-sub">+${d.events_7d} wk · +${d.events_30d} mo</span>`)}
                ${stat(d.per_day_30d, 'per day (30d)')}
                ${stat(d.entities, 'entities')}
                ${stat(d.knowledge, 'knowledge')}
                ${stat(d.edges, 'connections')}
                ${stat(d.favorites, 'favorites')}
            </div>
            ${d.most_woven.length ? `<div class="palace-dash-woven">Most woven: ${d.most_woven.map(w => `<span class="palace-pill">${escHtml(w.name)} <b>${w.count}</b></span>`).join('')}</div>` : ''}
            ${d.since ? `<div class="palace-dash-since">Mind since ${escHtml(d.since)}</div>` : ''}
            <div class="palace-librarian-row" id="pal-resident">
                <span class="palace-lib-title">\u{1FAAA} Resident</span>
                <span class="palace-lib-status">loading…</span>
                <button class="mind-btn-sm" id="pal-lib-admin" title="Run passes, migration, and rescue tools — the operator console">\u{1F6E0}️ Admin</button>
            </div>
            <div class="palace-librarian-row" id="pal-res-passes"></div>
            <div class="palace-librarian-row" id="pal-res-ledger"></div>
        </div>`;
}

let _libTimer = null;

async function refreshLibStatus(el, { poll = false } = {}) {
    const box = el.querySelector('#pal-lib-status');
    if (!box) return;
    let st;
    try {
        st = await palaceGet(`librarian/status?scope=${encodeURIComponent(scope)}`);
    } catch (e) { box.textContent = 'status unavailable'; return; }
    clearTimeout(_libTimer);
    if (st.enabled === false) {
        box.textContent = 'disabled (alpha — enable in Settings → Plugins → Mind Palace)';
        return;
    }
    if (st.running) {
        const c = st.current;
        box.textContent = `running (${c.scope}): message ${c.messages_done}/${c.messages_total || '?'}`;
        _libTimer = setTimeout(() => refreshLibStatus(el, { poll: true }), 3000);
    } else if (poll) {
        ui.showToast(st.current?.last_message || 'Librarian pass finished', 'success');
        renderSheet();
    } else {
        // Resident's summary line — the most recent of the per-pass rows.
        // Operating the passes lives in Mind → Admin.
        const mine = (st.scopes || [])
            .filter(s => s.scope === scope && s.last_pass)
            .sort((a, b) => (b.last_pass || '').localeCompare(a.last_pass || ''))[0];
        box.textContent = mine
            ? `last ${mine.pass} pass ${timeAgo(mine.last_pass)}`
            : 'never run in this scope';
    }
}

function bindLibrarian(el) {
    el.querySelector('#pal-lib-admin')?.addEventListener('click', async () => {
        const { switchView } = await import('../../core/router.js');
        window._mindScope = scope;   // Admin opens on the scope being viewed
        switchView('admin');
    });
    refreshLibStatus(el);
}

function modeChip(mode) {
    const m = MODE_CHIPS[mode] || MODE_CHIPS.hand;
    return `<span class="palace-mode-chip" title="${escAttr(m.tip)}">${m.icon}</span>`;
}

function cardHead(s) {
    return `
            <div class="palace-self-card-head">
                <span class="palace-self-title">${escHtml(s.title)}</span>
                ${modeChip(s.mode)}
                <span class="palace-self-saved" hidden>✓ saved</span>
                ${s.history_count ? `<button class="mind-btn-sm palace-self-hist" data-section="${escAttr(s.section)}" title="Archived versions">\u{1F4DC} ${s.history_count}</button>` : ''}
                ${s.custom ? `<button class="mind-btn-sm palace-self-delbox" data-section="${escAttr(s.section)}" title="Remove box">✕</button>` : ''}
            </div>
            <div class="palace-self-hint">${escHtml(s.hint)}${s.updated ? ` · ${escHtml(timeAgo(s.updated))}` : ''}</div>`;
}

function sectionCard(s) {
    return `
        <div class="mind-mem-card palace-self-card pal-w-${escAttr(s.width || 'third')}" data-section="${escAttr(s.section)}">
            ${cardHead(s)}
            <textarea class="palace-self-text" maxlength="${MAX_CHARS}" placeholder="${escAttr(s.hint)}">${escHtml(s.content)}</textarea>
        </div>`;
}

// The structured-list editor — the Handles "+add" pattern, generalized to any
// section/box with a fields spec (1–3 columns). Rows live in meta.rows server-
// side; the canonical text stays the spider/embedding surface.
function rowGridStyle(n, linky = false) {
    const cols = n === 1 ? '1fr' : n === 2 ? 'minmax(90px, 34%) 1fr' : 'minmax(80px, 26%) 1fr 1fr';
    return `grid-template-columns: ${cols} ${linky ? 'auto ' : ''}auto;`;
}

function structRow(fields, row = {}, linky = false) {
    // The ★ checkbox mirrors the '(important)' text mark — one substrate:
    // the server renders the mark into canonical text, this just toggles it.
    return `<div class="palace-row" style="${rowGridStyle(fields.length, linky)}">
        ${fields.map(f => `<input type="text" data-k="${escAttr(f.key)}" placeholder="${escAttr(f.label)}" value="${escAttr(row[f.key] || '')}">`).join('')}
        ${linky ? `<label class="palace-row-imp" title="important — this row spiders at wake and pulls its memories">
            <input type="checkbox" data-imp ${row.important ? 'checked' : ''}>★</label>` : ''}
        <button class="mind-btn-sm palace-row-del" title="Remove">✕</button>
    </div>`;
}

function structCard(s) {
    const fields = s.fields || [];
    const rows = s.rows?.length ? s.rows : [];
    const atMax = s.max_rows && rows.length >= s.max_rows;
    const linky = !!(s.link_fields && s.link_fields.length);
    return `
        <div class="mind-mem-card palace-self-card palace-self-struct pal-w-${escAttr(s.width || 'half')}"
             data-section="${escAttr(s.section)}" data-spec="${escAttr(JSON.stringify(fields))}"
             ${s.max_rows ? `data-max="${s.max_rows}"` : ''} ${s.custom ? 'data-custom="1"' : ''}
             ${linky ? 'data-linky="1"' : ''}>
            ${cardHead(s)}
            ${fields.length > 1 ? `<div class="palace-row palace-row-head" style="${rowGridStyle(fields.length, linky)}">${fields.map(f => `<span>${escHtml(f.label)}</span>`).join('')}${linky ? '<span title="spiders at wake">★</span>' : ''}<span></span></div>` : ''}
            <div class="palace-row-list">
                ${rows.map(r => structRow(fields, r, linky)).join('')}
            </div>
            <button class="mind-btn-sm palace-row-add" ${atMax ? 'hidden' : ''}>+ add</button>
        </div>`;
}

function flashSaved(card) {
    const chip = card.querySelector('.palace-self-saved');
    if (!chip) return;
    chip.hidden = false;
    clearTimeout(chip._t);
    chip._t = setTimeout(() => { chip.hidden = true; }, 1500);
}

async function saveSection(card, section, body, scopeAt = scope) {
    try {
        await palaceSend(`self/${encodeURIComponent(section)}`, 'PUT', { ...body, scope: scopeAt });
        flashSaved(card);
    } catch (e) { ui.showToast(`Save failed: ${e.message}`, 'error'); }
}

function queueSave(card, section, body, delay = 1000) {
    clearTimeout(_saveTimers[section]?.t);
    const scopeAt = scope;   // a flush after a scope switch must not cross-write
    const fire = () => { delete _saveTimers[section]; return saveSection(card, section, body(), scopeAt); };
    _saveTimers[section] = { t: setTimeout(fire, delay), fire };
}

// Fire one section's pending save NOW, reading the live DOM. No-op when idle.
function flushSave(section) {
    const p = _saveTimers[section];
    if (!p) return null;
    clearTimeout(p.t);
    return p.fire();
}

async function flushAllSaves() {
    const waits = Object.keys(_saveTimers).map(flushSave).filter(Boolean);
    if (waits.length) await Promise.all(waits);
}

function collectRows(card) {
    return [...card.querySelectorAll('.palace-row:not(.palace-row-head)')].map(r => {
        const row = {};
        r.querySelectorAll('input[data-k]').forEach(inp => { row[inp.dataset.k] = inp.value.trim(); });
        if (!Object.values(row).some(v => v)) return null;   // text decides life
        if (r.querySelector('input[data-imp]')?.checked) row.important = true;
        return row;
    }).filter(Boolean);
}

function bindCards(el) {
    // Text sections + custom boxes: autosave on idle, flush on blur.
    el.querySelectorAll('.palace-self-card:not(.palace-self-struct)').forEach(card => {
        const section = card.dataset.section;
        const ta = card.querySelector('textarea');
        if (!ta) return;
        ta.addEventListener('input', () => queueSave(card, section, () => ({ content: ta.value })));
        ta.addEventListener('blur', () => flushSave(section));
    });

    // Structured lists: the generic row editor (handles, relationships,
    // values, projects, custom lists). fields_spec rides along for custom
    // boxes so a not-yet-persisted list creates itself on first save.
    el.querySelectorAll('.palace-self-struct').forEach(card => {
        const section = card.dataset.section;
        const spec = JSON.parse(card.dataset.spec || '[]');
        const body = () => ({ rows: collectRows(card),
                              ...(card.dataset.custom ? { fields_spec: spec } : {}) });
        const save = () => queueSave(card, section, body);
        // Struct inputs had no blur flush (textareas did) — leaving the card
        // mid-debounce stranded the half-typed word as the final save.
        card.addEventListener('focusout', e => {
            if (!card.contains(e.relatedTarget)) flushSave(section);
        });
        const syncAdd = () => {
            const max = parseInt(card.dataset.max || '0', 10);
            const n = card.querySelectorAll('.palace-row:not(.palace-row-head)').length;
            card.querySelector('.palace-row-add').hidden = !!max && n >= max;
        };
        card.addEventListener('input', e => { if (e.target.matches('.palace-row input')) save(); });
        card.addEventListener('click', e => {
            if (e.target.matches('.palace-row-add')) {
                card.querySelector('.palace-row-list').insertAdjacentHTML(
                    'beforeend', structRow(spec, {}, !!card.dataset.linky));
                card.querySelector('.palace-row-list .palace-row:last-child input')?.focus();
                syncAdd();
            } else if (e.target.matches('.palace-row-del')) {
                e.target.closest('.palace-row').remove();
                syncAdd();
                save();
            }
        });
    });

    // History modals.
    el.querySelectorAll('.palace-self-hist').forEach(btn => {
        btn.addEventListener('click', () => showHistory(btn.dataset.section));
    });

    // Custom box removal (PUT empty = remove — user_bio semantics).
    el.querySelectorAll('.palace-self-delbox').forEach(btn => {
        btn.addEventListener('click', async () => {
            const section = btn.dataset.section;
            if (!confirm(`Remove box [${section}]?`)) return;
            _localBoxes = _localBoxes.filter(b => b.section !== section);
            try {
                await palaceSend(`self/${encodeURIComponent(section)}`, 'PUT', { content: '', scope });
                renderSheet();
            } catch (e) { ui.showToast(`Remove failed: ${e.message}`, 'error'); }
        });
    });

    // + Add box.
    el.querySelector('#pal-self-addbox')?.addEventListener('click', addBoxModal);
}

function _slug(s) {
    return s.trim().toLowerCase().replace(/\s+/g, '-').replace(/[^a-z0-9_-]/g, '').slice(0, 32);
}

function addBoxModal() {
    document.querySelector('.mind-modal-overlay')?.remove();
    const overlay = document.createElement('div');
    overlay.className = 'pr-modal-overlay mind-modal-overlay';
    overlay.innerHTML = `
        <div class="pr-modal palace-ent-modal">
            <div class="pr-modal-header">
                <h3>+ Add box</h3>
                <button class="mind-btn-sm mind-modal-close">✕</button>
            </div>
            <div class="pr-modal-body">
                <label class="palace-addbox-label">Name
                    <input type="text" id="pal-box-name" placeholder="e.g. quirks, games-beaten"></label>
                <label class="palace-addbox-label">Type
                    <select id="pal-box-type">
                        <option value="text">Text box (free writing)</option>
                        <option value="list">Structured list (columns, + add rows)</option>
                    </select></label>
                <div id="pal-box-cols" hidden>
                    <div class="palace-self-hint">Columns (1–3) — e.g. "Game" and "Score"</div>
                    <input type="text" class="pal-box-col palace-addbox-col" placeholder="Column 1">
                    <input type="text" class="pal-box-col palace-addbox-col" placeholder="Column 2 (optional)">
                    <input type="text" class="pal-box-col palace-addbox-col" placeholder="Column 3 (optional)">
                </div>
                <div class="palace-more-wrap"><button class="mind-btn" id="pal-box-create">Create</button></div>
            </div>
        </div>`;
    document.body.appendChild(overlay);
    const close = () => overlay.remove();
    overlay.querySelector('.mind-modal-close').addEventListener('click', close);
    setupModalClose(overlay, close);
    overlay.querySelector('#pal-box-type').addEventListener('change', e => {
        overlay.querySelector('#pal-box-cols').hidden = e.target.value !== 'list';
    });
    overlay.querySelector('#pal-box-create').addEventListener('click', () => {
        const slug = _slug(overlay.querySelector('#pal-box-name').value || '');
        if (!slug) { ui.showToast('Box needs a name', 'error'); return; }
        let fields = null;
        if (overlay.querySelector('#pal-box-type').value === 'list') {
            fields = [...overlay.querySelectorAll('.pal-box-col')]
                .map(i => i.value.trim()).filter(Boolean)
                .map(label => ({ key: _slug(label), label }));
            if (!fields.length) { ui.showToast('A list needs at least one column', 'error'); return; }
        }
        if (!_localBoxes.some(b => b.section === slug)) _localBoxes.push({ section: slug, fields });
        close();
        renderSheet().then(() => {
            content()?.querySelector(`.palace-self-card[data-section="${slug}"] textarea, ` +
                `.palace-self-card[data-section="${slug}"] .palace-row-add`)?.focus();
        });
    });
    overlay.querySelector('#pal-box-name').focus();
}

async function showHistory(section) {
    let data;
    try {
        data = await palaceGet(`self/${encodeURIComponent(section)}/history?scope=${encodeURIComponent(scope)}`);
    } catch (e) { ui.showToast(`History failed: ${e.message}`, 'error'); return; }

    document.querySelector('.mind-modal-overlay')?.remove();
    const overlay = document.createElement('div');
    overlay.className = 'pr-modal-overlay mind-modal-overlay';
    overlay.innerHTML = `
        <div class="pr-modal palace-ent-modal">
            <div class="pr-modal-header">
                <h3>\u{1F4DC} ${escHtml(section)} — the becoming trail</h3>
                <button class="mind-btn-sm mind-modal-close">✕</button>
            </div>
            <div class="pr-modal-body view-scroll">
                ${data.versions.length ? data.versions.map(v => `
                    <div class="mind-mem-card">
                        <div class="mind-mem-header">
                            <span class="mind-mem-time">written ${escHtml(timeAgo(v.created))} · archived ${escHtml(timeAgo(v.superseded_at))}</span>
                        </div>
                        <div class="mind-mem-content">${escHtml(v.content)}</div>
                    </div>`).join('') : '<div class="mind-empty">No archived versions yet</div>'}
            </div>
        </div>`;
    document.body.appendChild(overlay);
    overlay.querySelector('.mind-modal-close').addEventListener('click', () => overlay.remove());
    setupModalClose(overlay, () => overlay.remove());
}
