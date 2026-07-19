// views/palace/admin.js - Mind › Admin: the Mind Palace operator console.
// Librarian pass cards (one per pipeline step, each with a nightly toggle +
// an Actions dropdown in the Settings›Dashboard widget style), then the
// Maintenance & Migration / Rescue / Danger grid boxes. Operator surfaces
// live HERE — Self keeps only the resident's surfaces (status line,
// Upcoming card). Scope-aware like every palace tab; the per-pass toggles
// and the ⚙ settings modal write install-wide plugin settings (hidden from
// Settings → Plugins — this page is their only home, saves are per-key
// merges so the two surfaces can't clobber each other).
import { renderSectionHeader, bindSectionHeader } from '../../shared/section-header.js';
import { helpPills } from '../../features/video-link.js';
import { renderScopeSidebar, bindScopeSidebar } from '../../shared/scope-sidebar.js';
import { listScopes } from '../../shared/scope-api.js';
import { csrfHeaders, escHtml, escAttr, timeAgo, scopeForChatTab } from '../../shared/mind-common.js';
import { setupModalClose, showConfirm } from '../../shared/modal.js';
import * as ui from '../../ui.js';
import { PALACE_TABS, refreshPalaceTabs, SCOPE_ENDPOINT, palaceGet, palaceSend, describeScopeForDelete, rememberMindScope, recallMindScope } from './common.js';

const SCOPE_KEY = 'memory_scope';

// The librarian pipeline, in nightly order. Toggle = "part of the nightly
// recipe"; a Run click is explicit human intent and always works (Krem
// ruling, 2026-07-16).
// Each pass carries its help card (the ? modal): a plain paragraph plus
// Input → Processing → Output and the exact tools she holds. Written at
// grade 10-12 reading level on purpose — same facts, easier read (Krem's
// ruling 2026-07-19; the librarian is novel AND complex).
const PASSES = [
    { key: 'dates', icon: '\u{1F5D3}', title: 'Dates',
      blurb: 'Resolve date mentions into real calendar dates — feeds Upcoming Events.',
      help: {
          about: 'Finds memories that mention a date or time, and turns those mentions into real calendar dates. These dates feed the Upcoming and Just-happened lists on her Self page.',
          input: 'A batch of memories that mention time. Each one is shown with the date it was saved, plus worked examples.',
          processing: 'She works out what date the text points to, using the saved date as the anchor. "Tomorrow at noon" saved July 15 becomes July 16, 12:00. Code rejects any date that is not real.',
          output: 'Each memory gets its dates filed. "No real date here" counts as an answer too. Once filed, a memory never comes back into this queue.',
          tools: ['set_event_dates — one call files every memory in the message'],
      },
      actions: [
          { label: '▶ Run now', run: { what: 'all', pass: 'dates' } },
          { label: '⚠ Run ALL (drain queue)…', drain: { what: 'all', pass: 'dates' } },
          { label: 'Redate all (built-in rules)', maint: 'redate_regex' },
          { label: 'Redate all (librarian model)', maint: 'redate_model' },
      ] },
    { key: 'link', icon: '\u{1F517}', title: 'Link',
      blurb: 'Connect memories to the existing people, places, and things they name.',
      help: {
          about: 'Connects memories to the people, places, and things they mention. These links are the paths her recall follows — one memory pulls in the ones connected to it.',
          input: 'Memories that name something the system could not match on its own, plus a list of the people and things she already knows.',
          processing: 'She decides which person or thing each memory is really about. Names must match something that already exists — linking never creates a new entry. Unknown names get a note instead of a guess.',
          output: 'Links between memories and what they mention. "No link needed" is a recorded answer too.',
          tools: ['set_links — one call covers every memory in the message'],
      },
      actions: [
          { label: '▶ Run now', run: { what: 'all', pass: 'link' } },
          { label: '⚠ Run ALL (drain queue)…', drain: { what: 'all', pass: 'link' } },
      ] },
    { key: 'dedup', icon: '\u{1F46F}', title: 'Dedup',
      blurb: 'Fold measured near-duplicates — similarity-gated in code, reversible.',
      help: {
          about: 'Folds memories that record the same thing twice. Code finds the lookalikes first, by measuring how similar the stored memories are. She only answers one question per group: same thing, or actually different?',
          input: 'Groups of near-identical memories. Every pair in a group already passed the similarity bar before she sees it.',
          processing: 'For each group she either merges it or leaves it alone. Code re-checks the math before any merge goes through. Favorites and core memories refuse to merge. By default her rewording is ignored — the longest original text survives word for word.',
          output: 'Each merged group becomes one memory that keeps the earliest date. The originals are hidden, not deleted, and can be brought back. Everything scanned gets marked as checked, so it never recycles.',
          tools: ['merge_memories(ids) — one call per group',
                  'Dry-run scan (button below) — the same finder, no AI, no marks'],
      },
      actions: [
          { label: '▶ Run now', run: { what: 'all', pass: 'dedup' } },
          { label: '⚠ Run ALL (drain queue)…', drain: { what: 'all', pass: 'dedup' } },
          { label: '🔍 Dry-run scan (no model)', preview: 'dedup' },
      ] },
    { key: 'sort', icon: '\u{1F9F9}', title: 'Sort',
      blurb: 'The review charter: mark, split, promote, retire — oldest first.',
      help: {
          about: 'The review pass. She reads her oldest unreviewed memories and picks one action for each: keep it, split it, promote it, or retire it.',
          input: 'A batch of raw memories, oldest first, full text, plus her review charter ("be gentle with the early ones").',
          processing: 'One action per memory. Code blocks her from retiring favorites and core memories. Importance ratings only count when the Importance feature is turned on.',
          output: 'Each memory gets marked as reviewed. Splits, promotions, and retirements are all soft and reversible, and every action lands in the Ledger.',
          tools: ['mark_processed(id, importance?, favorite?) — fine as it is (the default)',
                  'atomize_memory(id, parts) — split a tangled entry',
                  'promote_memory(id, layer) — copy up to her self sheet or onto a person/thing',
                  'prune_memory(id, reason) — soft retire, reversible'],
      },
      actions: [
          { label: '▶ Run now (whole scope)', run: { what: 'all', pass: 'sort' } },
          { label: '⚠ Run ALL (drain queue)…', drain: { what: 'all', pass: 'sort' } },
          { label: '▶ Run now (self layer only)', run: { what: 'self', pass: 'sort' } },
      ] },
    { key: 'self', icon: '\u{1FA9E}', title: 'Self',
      blurb: 'She tends her own self sheet, then takes one final look to verify.',
      help: {
          about: 'Her self-sheet hour, last in the night so the sort pass\'s promotions have already landed. She reads her own sheet, updates what has drifted, then gets the updated sheet back for one final check before the night ends.',
          input: 'Her live self sheet — identity, values, projects, relationships, voice, handles, origin — shown in full.',
          processing: 'Two rounds. First she tends: update the sections that no longer read true; a good section needs no call. Then she verifies: the updated sheet comes back, and she can still fix it. Code folds duplicate lines, and the wake-tools section can never be written.',
          output: 'An updated sheet. Every edit saves the old version into the sheet\'s history, so nothing is ever lost.',
          tools: ['update_self(section, content) — rewrite one section whole'],
      },
      actions: [
          { label: '▶ Run now', run: { what: 'all', pass: 'self' } },
      ] },
];

// Non-librarian grid boxes. Every action is an UPDATE on metadata — memory
// content is never touched; only the Danger box deletes, behind a typed
// confirm that the server re-checks.
const OPS = [
    { key: 'migration', icon: '\u{1F4E6}', title: 'Import & Migration',
      blurb: 'Copy from Memory v1 (opened read-only, never modified) and rebuild derived metadata.',
      actions: [
          { label: 'Import from Memory v1 (all scopes)', maint: 'import_v1' },
          { label: 'Copy knowledge into the Library (v1 + v2)', library: 'migrate' },
          { label: 'Generate missing metadata', maint: 'generate_metadata' },
      ] },
    { key: 'rescue', icon: '\u{1F6DF}', title: 'Rescue',
      blurb: 'Undo what the alpha features wrote. Content untouched, reversible by design.',
      actions: [
          { label: 'Reset importance ratings', maint: 'reset_importance' },
          { label: 'Restore retired memories', maint: 'restore_retired' },
      ] },
    { key: 'danger', icon: '☠️', title: 'Danger zone', danger: true,
      blurb: 'Typed confirmation required, double-checked server-side. Memory v1 is a separate system — never touched.',
      actions: [
          { label: '⚠ Delete ALL memories in scope', maint: 'wipe_scope', danger: true },
          { label: '⚠ Clear ONE tab: Memories', maint: 'clear_layer', layer: 'events', danger: true },
          { label: '⚠ Clear ONE tab: Self', maint: 'clear_layer', layer: 'self', danger: true },
          { label: '⚠ Clear ONE tab: Entities', maint: 'clear_layer', layer: 'entities', danger: true },
          { label: '⚠ Clear ONE tab: Goals', maint: 'clear_layer', layer: 'goals', danger: true },
          { label: '⚠ Clear ONE tab: Knowledge (incl. Library files)', maint: 'clear_layer', layer: 'knowledge', danger: true },
      ] },
];

// Per-action gates: `confirm` = ok/cancel dialog; `prompt` = the user must
// TYPE the scope name (destructive tier). Actions with neither run directly.
const MAINT = {
    reset_importance: {
        confirm: s => `Reset importance ratings in scope '${s}'?\n\n` +
            'All ratings return to unrated; favorites and permanent goals return to 0.95. ' +
            'No memory content is touched.',
    },
    restore_retired: {
        confirm: s => `Restore all retired memories in scope '${s}'?\n\n` +
            'Every librarian-pruned memory returns to her recall. Atomize/merge ' +
            'retirements are kept — their content lives on in derived entries.',
    },
    import_v1: {
        confirm: () => 'Import everything from the classic Memory v1 system?\n\n' +
            'Additive and idempotent — re-running copies zero duplicates. All scopes ' +
            'are imported. The v1 databases are opened read-only and are never modified.',
    },
    generate_metadata: {},
    clear_layer: {
        prompt: s => `⚠ PERMANENTLY DELETE one tab's data in scope '${s}' — ` +
            'just that tab, the rest of the scope stays.\n\n' +
            'Knowledge also deletes the scope\'s Library documents and their ' +
            'files. Other scopes and Memory v1 are never touched.\n\n' +
            `Type the scope name ('${s}') to confirm:`,
    },
    redate_regex: {
        confirm: s => `Re-run the built-in date rules over scope '${s}'?\n\n` +
            'Every memory is re-dated against its own saved date. Dates the ' +
            'librarian resolved are kept. Memory content is never touched.',
    },
    redate_model: {
        confirm: s => `Re-date scope '${s}' with the librarian model?\n\n` +
            'All date verdicts reopen and a dates pass starts now (batch and ' +
            'daily caps apply — re-run or let the nightly drain the rest). ' +
            'Requires the Librarian alpha toggle. Memory content is never touched.',
    },
    wipe_scope: {
        prompt: s => `⚠ PERMANENTLY DELETE all Mind Palace data in scope '${s}' — ` +
            'memories, entities, connections, goals, self sheet, AND the ' +
            'scope\'s Library (documents + their files)?\n\n' +
            'Memory v1 is a separate system and is never touched.\n\n' +
            'Type the scope name to confirm:',
    },
};

let container = null;
let scope = 'default';
let scopes = [];
let settings = {};
let _libTimer = null;

export default {
    init(el) { container = el; },
    async show() {
        await refreshPalaceTabs();
        if (window._mindScope) { scope = window._mindScope; delete window._mindScope; }
        else { const s = recallMindScope() || await scopeForChatTab(SCOPE_KEY); if (s) scope = s; }
        delete window._mindTab;
        scopes = await listScopes(SCOPE_ENDPOINT);
        if (scope !== 'default' && !scopes.some(x => x.name === scope)) scope = 'default';
        rememberMindScope(scope);
        await loadSettings();
        render();
    },
    hide() { clearTimeout(_libTimer); }
};

function content() { return container?.querySelector('#pal-admin-content'); }

async function loadSettings() {
    try {
        const r = await fetch('/api/webui/plugins/mindpalace/settings', { credentials: 'same-origin' });
        settings = (await r.json()).settings || {};
    } catch { settings = {}; }
}

async function savePluginSettings(patch) {
    // PUT is a per-key merge server-side — a partial patch can't clobber
    // keys owned by other surfaces.
    const r = await fetch('/api/webui/plugins/mindpalace/settings', {
        method: 'PUT',
        credentials: 'same-origin',
        headers: csrfHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({ settings: patch }),
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.detail || data.error || `HTTP ${r.status}`);
    settings = data.settings || { ...settings, ...patch };
}

function render() {
    if (!container) return;
    container.innerHTML = `
        ${renderSectionHeader({ tabs: PALACE_TABS, active: 'admin', help: helpPills('Admin', { doc: 'MEMORY.md', inline: true }), status: '\u{1F6E0}️ Mind Palace — the operator console. Librarian pipeline, migration, rescue. Nothing here edits memory content; only the Danger box deletes.' })}
        <div class="two-panel">
            ${renderScopeSidebar(scopes, scope)}
            <div class="panel-right">
                <div class="view-body view-scroll" id="pal-admin-content"></div>
            </div>
        </div>`;
    bindSectionHeader(container);
    bindScopeSidebar(container, {
        describeScope: describeScopeForDelete,
        onScopeChange: (s) => { scope = s; rememberMindScope(s); render(); },
        onChanged: async (s) => { scope = s || 'default'; rememberMindScope(scope); scopes = await listScopes(SCOPE_ENDPOINT); render(); },
    });
    renderConsole();
}

function passEnabled(key) {
    return settings[`librarian_pass_${key}`] !== false;
}

function nightlySummary() {
    if (!settings.librarian_nightly_enabled) return 'Nightly round: off — passes run only when you click Run.';
    const t = settings.librarian_nightly_time || '03:30';
    const skipped = PASSES.filter(p => !passEnabled(p.key)).map(p => p.title.toLowerCase());
    return `Nightly round: on · ${t} · scopes opt in on their Self pages (Resident strip)`
        + (skipped.length ? ` · skipping ${skipped.join(', ')}` : '');
}

function actionsDropdown(spec, libDisabled) {
    return `
        <details class="dash-action-dropdown ui-card-bottom">
            <summary><span>Actions</span><span class="chev">▾</span></summary>
            <div class="dash-action-dropdown-menu">
                ${spec.actions.map((a, i) => `
                    <button data-card="${escAttr(spec.key)}" data-idx="${i}"
                        ${a.run && libDisabled ? 'disabled title="Enable the Librarian alpha in Settings → Plugins → Mind Palace"' : ''}
                        ${a.danger ? 'style="color:var(--error,#f44)"' : ''}>${escHtml(a.label)}</button>`).join('')}
            </div>
        </details>`;
}

function capsLine(key) {
    // The caps that govern this pass, inline — glanceable, click to adjust.
    const s = settings || {};
    const bits = key === 'self'
        ? [`${s.librarian_max_passes_per_day ?? 3}/day`]   // batch/msg N/A: the sheet is the batch
        : [
            `batch ${s.librarian_batch_size ?? 20}`,
            `${s.librarian_items_per_message ?? 10}/msg`,
            `${s.librarian_max_passes_per_day ?? 3}/day`,
        ];
    if (key === 'dedup') {
        bits.push(`≥${s.librarian_merge_threshold ?? 0.9}`);
        bits.push(`rewrite ${s.librarian_merge_rewrite ? 'on' : 'off'}`);
    }
    if (s.librarian_model) bits.push(String(s.librarian_model));
    return bits.join(' · ');
}

function helpModal(p) {
    const h = p.help;
    if (!h) return;
    document.querySelector('.mind-modal-overlay')?.remove();
    const overlay = document.createElement('div');
    overlay.className = 'pr-modal-overlay mind-modal-overlay';
    overlay.innerHTML = `
        <div class="pr-modal palace-ent-modal">
            <div class="pr-modal-header">
                <h3>${p.icon} ${escHtml(p.title)} — what this pass does</h3>
                <button class="mind-btn-sm mind-modal-close">✕</button>
            </div>
            <div class="pr-modal-body view-scroll">
                <p style="margin-top:0">${escHtml(h.about)}</p>
                <p><b>Input</b> — ${escHtml(h.input)}</p>
                <p><b>Processing</b> — ${escHtml(h.processing)}</p>
                <p><b>Output</b> — ${escHtml(h.output)}</p>
                <p style="margin-bottom:4px"><b>Tools she holds</b></p>
                <ul style="margin-top:0">
                    ${(h.tools || []).map(t => `<li>${escHtml(t)}</li>`).join('')}
                </ul>
                <p class="ui-meta-text" style="white-space:normal">Every pass runs as her. A session starts with her self sheet loaded, all of one night shares one chat, and every action is logged in the Ledger.</p>
            </div>
        </div>`;
    document.body.appendChild(overlay);
    const close = () => overlay.remove();
    overlay.querySelector('.mind-modal-close').addEventListener('click', close);
    setupModalClose(overlay, close);
}

function passCard(p, st, libEnabled) {
    const on = passEnabled(p.key);
    const mine = (st.scopes || []).find(s => s.scope === scope && s.pass === p.key);
    const statusLine = mine
        ? `last ${timeAgo(mine.last_pass)} · ${mine.passes_today} today`
        : 'never run in this scope';
    return `
        <div class="ui-card" data-card="${escAttr(p.key)}" style="${on ? '' : 'opacity:.6'}">
            <div class="ui-row">
                <span class="ui-card-title" style="padding-right:0">${p.icon} ${escHtml(p.title)}</span>
                <button class="mind-btn-sm" data-pass-help="${escAttr(p.key)}" title="What this pass does" style="margin-left:auto">?</button>
                <button class="mind-btn-sm" data-pass-gear="${escAttr(p.key)}" title="Librarian settings">⚙</button>
                <label class="ui-toggle" title="Include this pass in the nightly round. Off = the nightly skips it; ▶ Run always works.">
                    <input type="checkbox" data-pass-toggle="${escAttr(p.key)}" ${on ? 'checked' : ''}>
                    <span class="ui-toggle-slider"></span>
                </label>
            </div>
            <div class="ui-card-body">${escHtml(p.blurb)}</div>
            <div class="ui-card-meta">
                <span class="ui-chip" data-pass-status="${escAttr(p.key)}">${escHtml(libEnabled ? statusLine : 'alpha off')}</span>
                <span class="ui-meta-text" data-caps="${escAttr(p.key)}" style="cursor:pointer;white-space:normal" title="The caps governing this pass — click to adjust">${escHtml(capsLine(p.key))}</span>
            </div>
            ${actionsDropdown(p, !libEnabled)}
            <div class="ui-card-body" data-result="${escAttr(p.key)}"></div>
        </div>`;
}

function opsCard(o) {
    return `
        <div class="ui-card" data-card="${escAttr(o.key)}"
             ${o.danger ? 'style="border-color:var(--error-border)"' : ''}>
            <div class="ui-card-title" style="padding-right:0">${o.icon} ${escHtml(o.title)}</div>
            <div class="ui-card-body">${escHtml(o.blurb)}</div>
            ${actionsDropdown(o, false)}
            <div class="ui-card-body" data-result="${escAttr(o.key)}"></div>
        </div>`;
}

async function renderConsole() {
    const el = content();
    if (!el) return;
    let st;
    try {
        st = await palaceGet(`librarian/status?scope=${encodeURIComponent(scope)}`);
    } catch (e) {
        el.innerHTML = `<div class="ui-empty">Failed to load: ${escHtml(e.message)}</div>`;
        return;
    }
    const libEnabled = st.enabled !== false;
    el.innerHTML = `
        <div class="ui-box">
            <div class="ui-box-head">
                <span class="ui-dot ${libEnabled ? 'ui-dot-ok' : ''}" title="${libEnabled ? 'Librarian alpha: on' : 'Librarian alpha: off'}"></span>
                <span class="ui-box-title">\u{1F9F9} Librarian</span>
                <span class="ui-box-desc" id="pal-adm-nightly">${escHtml(nightlySummary())}</span>
                ${libEnabled ? '' : '<span class="ui-chip ui-chip-warn" title="Enable in Settings → Plugins → Mind Palace">alpha off</span>'}
                <div class="ui-box-actions">
                    <button class="mind-btn-sm" id="pal-adm-settings">⚙ Settings</button>
                </div>
            </div>
            <div class="ui-box-body">
                <div class="ui-card-body" id="pal-adm-running" style="margin:0 0 8px"></div>
                <div class="ui-grid">
                    ${PASSES.map(p => passCard(p, st, libEnabled)).join('')}
                </div>
            </div>
        </div>
        <div class="ui-box">
            <div class="ui-box-head">
                <span class="ui-box-title">\u{1F6E0} Maintenance & Migration</span>
                <span class="ui-box-desc">per-scope · every run lands in the Ledger</span>
            </div>
            <div class="ui-box-body">
                <div class="ui-grid">
                    ${OPS.map(opsCard).join('')}
                </div>
            </div>
        </div>
        <div class="ui-box">
            <div class="ui-box-head">
                <span class="ui-box-title">\u{1F52C} Tool console</span>
                <span class="ui-box-desc">her READ-ONLY tools, raw output, zero fingerprints — no recall boosts, no ledger stamp, no wake tools</span>
            </div>
            <div class="ui-box-body">
                ${Object.entries(PEEK_TOOLS).map(([k, t]) => `
                <div class="ui-row" data-peek-row="${escAttr(k)}" style="width:100%;margin-bottom:8px">
                    <span class="ui-meta-text" style="min-width:180px;color:var(--text-secondary)">${t.label}</span>
                    ${(t.fields || []).map(f =>
                        `<input class="palace-search" style="flex:1 1 140px;max-width:240px" data-peek="${escAttr(f.id)}"
                            type="${f.type || 'text'}" placeholder="${escAttr(f.ph)}"
                            value="${escAttr(f.val || '')}">`).join('')}
                    <button class="mind-btn-sm" data-peek-run="${escAttr(k)}" style="margin-left:auto">▶ Run</button>
                </div>`).join('')}
                <pre id="pal-peek-out" class="pal-peek-out" hidden></pre>
            </div>
        </div>`;
    bindConsole(el);
    bindPeek(el);
    refreshRunning(el, st);
}

// ─── Tool console (read-only peek — she sees this exact text) ────────────────

const PEEK_TOOLS = {
    search_memory: { label: '\u{1F50D} search_memory', fields: [
        { id: 'query', ph: 'query *' },
        { id: 'layer', ph: 'layer (blank = all)' },
        { id: 'depth', ph: 'depth 0-2', type: 'number' },
    ] },
    read_self: { label: '\u{1F4A0} read_self', fields: [
        { id: 'depth', ph: 'depth 0-2', type: 'number', val: '1' },
    ] },
    get_recent_memories: { label: '\u{1F9E0} get_recent_memories', fields: [
        { id: 'count', ph: 'count', type: 'number', val: '10' },
    ] },
    library: { label: '\u{1F3DB} library', fields: [] },
    read_document: { label: '\u{1F4D6} read_document', fields: [
        { id: 'document_id', ph: 'doc id *', type: 'number' },
        { id: 'page', ph: 'page', type: 'number' },
        { id: 'around', ph: 'around §', type: 'number' },
    ] },
    list_goals: { label: '\u{1F3AF} list_goals', fields: [
        { id: 'goal_id', ph: 'goal id (deep view)', type: 'number' },
        { id: 'status', ph: 'status (active | all…)' },
    ] },
};

function bindPeek(el) {
    // One full-width row per tool: name · params · Run, wrapping as the
    // panel narrows. One shared output pane below the rows.
    const out = el.querySelector('#pal-peek-out');
    el.querySelectorAll('[data-peek-run]').forEach(btn =>
        btn.addEventListener('click', async () => {
            const tool = btn.dataset.peekRun;
            const row = el.querySelector(`[data-peek-row="${tool}"]`);
            const args = {};
            row?.querySelectorAll('[data-peek]').forEach(inp => {
                if (inp.value.trim() !== '') args[inp.dataset.peek] = inp.value.trim();
            });
            out.hidden = false;
            out.textContent = '…';
            try {
                const r = await palaceSend('console/peek', 'POST',
                    { scope, tool, args });
                out.textContent = r.output || '(empty)';
                out.classList.toggle('pal-peek-warn', r.ok === false);
            } catch (e) {
                out.textContent = `✗ ${e.message}`;
                out.classList.add('pal-peek-warn');
            }
        }));
}

function bindConsole(el) {
    // Per-pass nightly toggles → install-wide plugin setting (partial merge).
    el.querySelectorAll('[data-pass-toggle]').forEach(input => {
        input.addEventListener('change', async () => {
            const key = input.dataset.passToggle;
            try {
                await savePluginSettings({ [`librarian_pass_${key}`]: input.checked });
                const card = el.querySelector(`.ui-card[data-card="${key}"]`);
                if (card) card.style.opacity = input.checked ? '' : '.6';
                const nl = el.querySelector('#pal-adm-nightly');
                if (nl) nl.textContent = nightlySummary();
                ui.showToast(`Nightly ${key} pass ${input.checked ? 'on' : 'off'}`, 'success');
            } catch (e) {
                input.checked = !input.checked;
                ui.showToast(`Save failed: ${e.message}`, 'error');
            }
        });
    });

    // Card action menus.
    el.querySelectorAll('.dash-action-dropdown-menu button').forEach(btn => {
        btn.addEventListener('click', () => {
            btn.closest('details')?.removeAttribute('open');
            const spec = [...PASSES, ...OPS].find(s => s.key === btn.dataset.card);
            const action = spec?.actions[Number(btn.dataset.idx)];
            if (action) runAction(el, spec, action);
        });
    });

    el.querySelector('#pal-adm-settings')?.addEventListener('click', () => settingsModal('general'));

    // Per-card gears + the caps line itself: open settings on that pass's tab.
    el.querySelectorAll('[data-pass-gear],[data-caps]').forEach(b =>
        b.addEventListener('click', () => {
            const key = b.dataset.passGear || b.dataset.caps;
            settingsModal(key === 'dedup' ? 'dedup' : 'general');
        }));

    // ? per pass — the IPO help modal.
    el.querySelectorAll('[data-pass-help]').forEach(b =>
        b.addEventListener('click', () => {
            const p = PASSES.find(x => x.key === b.dataset.passHelp);
            if (p) helpModal(p);
        }));
}

function refreshCapsLines(el) {
    el?.querySelectorAll('[data-caps]').forEach(s => {
        s.textContent = capsLine(s.dataset.caps);
    });
}

function setResult(el, key, text) {
    const box = el.querySelector(`[data-result="${key}"]`);
    if (box) box.textContent = text;
}

async function runAction(el, spec, action) {
    if (action.library === 'migrate') {
        showConfirm('Copy knowledge from Memory v1 AND old palace knowledge chunks into the Library? '
            + 'Additive and idempotent — already-copied groups skip. v1 stays read-only and keeps '
            + 'working; nothing is deleted anywhere. Embedding continues in the background.',
            async () => {
                try {
                    const r = await palaceSend('library/migrate', 'POST', {});
                    const rep = r.report || {};
                    const f = s => s ? `${s.imported} in · ${s.skipped} skipped · ${s.failed} failed` : '?';
                    const msg = `v2 chunks: ${f(rep.v2)} — v1: ${rep.v1?.absent ? 'not present' : f(rep.v1)}`;
                    setResult(el, spec.key, msg);
                    ui.showToast('Library migration done — embedding continues in background', 'success');
                } catch (e) { ui.showToast(e.message, 'error'); }
            }, { title: 'Library migration', saveLabel: 'Copy' });
        return;
    }
    if (action.drain) {
        // Run ALL — fetch the LIVE queue depth, then confirm with real
        // numbers + a rough token range before draining the whole thing.
        let d;
        try {
            d = await palaceGet(`librarian/queue-depth?scope=${encodeURIComponent(scope)}&kind=${action.drain.pass}`);
        } catch (e) { ui.showToast(e.message, 'error'); return; }
        if (!d.depth) {
            ui.showToast(`Nothing queued for the ${action.drain.pass} pass in '${scope}'.`, 'success');
            return;
        }
        const tk = n => n >= 1000 ? `${Math.round(n / 1000)}k` : `${n}`;
        showConfirm(
            `Drain the ENTIRE ${action.drain.pass} queue for scope '${scope}'?\n\n`
            + `${d.depth} memories waiting · ~${d.batches} batches of ${d.batch_size} · `
            + `roughly ${tk(d.est_tokens_low)}–${tk(d.est_tokens_high)} tokens on your librarian model.\n\n`
            + `Each batch runs in its own chat (flat context). This bypasses the daily cap `
            + `and may take a while — you can Stop it between batches.`,
            async () => {
                try {
                    const r = await palaceSend('librarian/drain', 'POST', { scope, ...action.drain });
                    ui.showToast(r.message || 'Draining…', 'success');
                    setResult(el, spec.key, r.message || 'draining…');
                    refreshRunning(el);
                } catch (e) { ui.showToast(e.message, 'error'); }
            }, { title: `Run ALL — ${action.drain.pass}`, saveLabel: `Drain ${d.depth}` });
        return;
    }
    if (action.preview) {
        // Mechanical similarity scan only — no LLM, no stamps, no cap spend.
        try {
            const r = await palaceGet(`librarian/dedup-preview?scope=${encodeURIComponent(scope)}`);
            const cls = r.clusters || [];
            const groups = cls.map(c => c.ids.join('+')).slice(0, 8).join(' · ');
            const msg = `${r.scanned} scanned @ ≥${r.threshold} → ${cls.length} cluster(s)`
                + (cls.length ? `: ${groups}${cls.length > 8 ? ' …' : ''}` : ' — shelf looks clean');
            setResult(el, spec.key, msg);
            ui.showToast(`Dedup dry-run: ${cls.length} duplicate cluster(s)`, cls.length ? 'warning' : 'success');
        } catch (e) { ui.showToast(e.message, 'error'); }
        return;
    }
    if (action.run) {
        try {
            const r = await palaceSend('librarian/run', 'POST', { scope, ...action.run });
            ui.showToast(r.message || 'Pass started', 'success');
            setResult(el, spec.key, r.message || 'started');
            refreshRunning(el);
        } catch (e) { ui.showToast(e.message, 'error'); }
        return;
    }
    const gate = MAINT[action.maint] || {};
    const body = { action: action.maint, scope };
    if (action.layer) body.layer = action.layer;
    if (gate.prompt) {
        const typed = prompt(gate.prompt(scope));
        if (typed === null) return;
        if (typed !== scope) { ui.showToast('Scope name did not match — nothing deleted', 'warning'); return; }
        body.confirm = typed;
    } else if (gate.confirm && !confirm(gate.confirm(scope))) return;
    try {
        const r = await palaceSend('maintenance', 'POST', body);
        const bits = [];
        if ('cleared' in r) bits.push(`${r.cleared} cleared`);
        if ('restored' in r) bits.push(`${r.restored} restored`);
        if (r.skipped) bits.push(`${r.skipped} kept (atomize/merge)`);
        if ('stamped' in r) bits.push(`${r.stamped} stamped` + ('edges' in r ? ` · ${r.edges} links seeded` : ''));
        if ('kept' in r) bits.push(`${r.kept} librarian verdicts kept`);
        if ('requeued' in r) bits.push(`${r.requeued} verdicts reopened`);
        if (r.message) bits.push(r.message);
        if ('deleted_chunks' in r) bits.push(`${r.deleted_chunks} memories · ${r.deleted_entities} entities · ${r.deleted_edges} connections deleted`);
        if (r.library_docs_deleted) bits.push(`${r.library_docs_deleted} library docs deleted`);
        const msg = r.summary || bits.join(' · ') || 'done';
        setResult(el, spec.key, msg);
        ui.showToast(r.summary ? 'Import finished' : `Maintenance: ${msg}`, 'success');
        if (action.maint === 'wipe_scope' || action.maint === 'import_v1'
            || action.maint === 'redate_model') refreshRunning(el);
    } catch (e) { ui.showToast(e.message, 'error'); }
}

// Live pass indicator: poll while a pass runs, refresh the per-pass status
// lines when it finishes. Pull-only — Admin has no SSE surface of its own.
async function refreshRunning(el, preloaded) {
    const line = el.querySelector('#pal-adm-running');
    if (!line) return;
    let st = preloaded;
    if (!st) {
        try { st = await palaceGet(`librarian/status?scope=${encodeURIComponent(scope)}`); }
        catch { line.textContent = ''; return; }
    }
    clearTimeout(_libTimer);
    const dr = st.current?.drain;
    if (dr) {
        const done = dr.handled || 0, left = dr.remaining || 0;
        line.innerHTML = '';
        const txt = document.createElement('span');
        txt.textContent = `⏳ Draining ${dr.kind} (${st.current.scope}): batch ${dr.batch} · `
            + `${done} done · ${left} left${dr.stopping ? ' · stopping…' : ''} `;
        line.appendChild(txt);
        if (!dr.stopping) {
            const stop = document.createElement('button');
            stop.className = 'mind-btn-sm';
            stop.textContent = '■ Stop';
            stop.title = 'Stop after the current batch — finished batches stay done';
            stop.addEventListener('click', async () => {
                stop.disabled = true;
                try { const r = await palaceSend('librarian/drain-stop', 'POST', {}); ui.showToast(r.message, 'success'); }
                catch (e) { ui.showToast(e.message, 'error'); }
            });
            line.appendChild(stop);
        }
        _libTimer = setTimeout(() => refreshRunning(el), 3000);
        return;
    }
    if (st.running) {
        const c = st.current;
        line.textContent = `⏳ ${c.kind || ''} pass running (${c.scope}): message ${c.messages_done}/${c.messages_total || '?'}`;
        _libTimer = setTimeout(() => refreshRunning(el), 3000);
        return;
    }
    if (line.textContent) {
        ui.showToast(st.current?.last_message || 'Librarian pass finished', 'success');
        line.textContent = '';
    }
    for (const p of PASSES) {
        const box = el.querySelector(`[data-pass-status="${p.key}"]`);
        if (!box) continue;
        const mine = (st.scopes || []).find(s => s.scope === scope && s.pass === p.key);
        if (st.enabled === false) box.textContent = 'alpha off';
        else box.textContent = mine
            ? `last ${timeAgo(mine.last_pass)} · ${mine.passes_today} today`
            : 'never run in this scope';
    }
}

// ⚙ Librarian settings — the hidden manifest fields, rendered by the SAME
// shared renderer the Settings page uses (schema lives in plugin.json only).
// The 4 pass toggles are excluded — the cards are their home.
// Tab → the settings keys that belong to it. Passes without unique settings
// share General; future per-pass settings slot into new tabs here.
const SETTINGS_TABS = [
    { key: 'general', label: 'General', match: () => true },
    { key: 'dedup', label: '\u{1F46F} Dedup',
      keys: ['librarian_merge_threshold', 'librarian_merge_rewrite'] },
];

async function settingsModal(initialTab = 'general') {
    let schema = [];
    try {
        const r = await fetch('/api/webui/plugins', { credentials: 'same-origin' });
        schema = ((await r.json()).plugins || [])
            .find(p => p.name === 'mindpalace')?.settings_schema || [];
    } catch {}
    const fields = schema
        .filter(f => (f.hidden || f.key === 'librarian_enabled')
            && !f.key.startsWith('librarian_pass_'))
        .map(f => ({ ...f, hidden: false }));
    if (!fields.length) { ui.showToast('Settings schema unavailable', 'error'); return; }
    const claimed = new Set(SETTINGS_TABS.flatMap(t => t.keys || []));
    const groups = {};
    for (const t of SETTINGS_TABS) {
        groups[t.key] = t.keys
            ? fields.filter(f => t.keys.includes(f.key))
            : fields.filter(f => !claimed.has(f.key));
    }
    const { renderSettingsForm, readSettingsForm } =
        await import('../../shared/plugin-settings-renderer.js');

    document.querySelector('.mind-modal-overlay')?.remove();
    const overlay = document.createElement('div');
    overlay.className = 'pr-modal-overlay mind-modal-overlay';
    overlay.innerHTML = `
        <div class="pr-modal palace-ent-modal">
            <div class="pr-modal-header">
                <h3>⚙ Librarian settings</h3>
                <span style="display:flex;gap:8px;align-items:center">
                    <button class="mind-btn" id="pal-adm-settings-save"
                        style="background:var(--trim);border-color:var(--trim);color:var(--bg)">Save</button>
                    <button class="mind-btn-sm mind-modal-close">✕</button>
                </span>
            </div>
            <div class="pr-modal-body view-scroll">
                <div class="ui-row" style="margin-bottom:10px">
                    ${SETTINGS_TABS.map(t => `<button class="ui-pill" data-tab="${t.key}">${t.label}</button>`).join('')}
                </div>
                ${SETTINGS_TABS.map(t => `<div data-pane="${t.key}" style="display:none"></div>`).join('')}
            </div>
        </div>`;
    document.body.appendChild(overlay);
    const close = () => overlay.remove();
    overlay.querySelector('.mind-modal-close').addEventListener('click', close);
    setupModalClose(overlay, close);
    // Both panes render once and stay live — tab switches only toggle
    // display, so half-edited values survive tab hops and Save reads both.
    for (const t of SETTINGS_TABS) {
        renderSettingsForm(overlay.querySelector(`[data-pane="${t.key}"]`),
                           groups[t.key], settings);
    }
    const selectTab = (key) => {
        overlay.querySelectorAll('[data-tab]').forEach(b =>
            b.classList.toggle('ui-pill-on', b.dataset.tab === key));
        overlay.querySelectorAll('[data-pane]').forEach(p =>
            p.style.display = p.dataset.pane === key ? '' : 'none');
    };
    overlay.querySelectorAll('[data-tab]').forEach(b =>
        b.addEventListener('click', () => selectTab(b.dataset.tab)));
    selectTab(SETTINGS_TABS.some(t => t.key === initialTab) ? initialTab : 'general');
    overlay.querySelector('#pal-adm-settings-save').addEventListener('click', async () => {
        try {
            const patch = {};
            for (const t of SETTINGS_TABS) {
                Object.assign(patch, readSettingsForm(
                    overlay.querySelector(`[data-pane="${t.key}"]`), groups[t.key]));
            }
            await savePluginSettings(patch);
            ui.showToast('Librarian settings saved', 'success');
            close();
            const nl = content()?.querySelector('#pal-adm-nightly');
            if (nl) nl.textContent = nightlySummary();
            refreshCapsLines(content());
        } catch (e) { ui.showToast(`Save failed: ${e.message}`, 'error'); }
    });
}
