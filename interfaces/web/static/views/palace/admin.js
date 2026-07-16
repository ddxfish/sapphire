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
import { setupModalClose } from '../../shared/modal.js';
import * as ui from '../../ui.js';
import { PALACE_TABS, refreshPalaceTabs, SCOPE_ENDPOINT, palaceGet, palaceSend, describeScopeForDelete } from './common.js';

const SCOPE_KEY = 'memory_scope';

// The librarian pipeline, in nightly order. Toggle = "part of the nightly
// recipe"; a Run click is explicit human intent and always works (Krem
// ruling, 2026-07-16).
const PASSES = [
    { key: 'dates', icon: '\u{1F5D3}', title: 'Dates',
      blurb: 'Resolve date mentions into real calendar dates — feeds Upcoming Events.',
      actions: [
          { label: '▶ Run now', run: { what: 'all', pass: 'dates' } },
          { label: 'Redate all (built-in rules)', maint: 'redate_regex' },
          { label: 'Redate all (librarian model)', maint: 'redate_model' },
      ] },
    { key: 'link', icon: '\u{1F517}', title: 'Link',
      blurb: 'Connect memories to the existing people, places, and things they name.',
      actions: [
          { label: '▶ Run now', run: { what: 'all', pass: 'link' } },
      ] },
    { key: 'dedup', icon: '\u{1F46F}', title: 'Dedup',
      blurb: 'Fold measured near-duplicates — similarity-gated in code, reversible.',
      actions: [
          { label: '▶ Run now', run: { what: 'all', pass: 'dedup' } },
      ] },
    { key: 'sort', icon: '\u{1F9F9}', title: 'Sort',
      blurb: 'The review charter: mark, split, promote, retire — oldest first.',
      actions: [
          { label: '▶ Run now (whole scope)', run: { what: 'all', pass: 'sort' } },
          { label: '▶ Run now (self layer only)', run: { what: 'self', pass: 'sort' } },
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
            'memories, entities, connections, goals, and self sheet?\n\n' +
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
        else { const s = await scopeForChatTab(SCOPE_KEY); if (s) scope = s; }
        delete window._mindTab;
        scopes = await listScopes(SCOPE_ENDPOINT);
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
        onScopeChange: (s) => { scope = s; render(); },
        onChanged: async (s) => { scope = s || 'default'; scopes = await listScopes(SCOPE_ENDPOINT); render(); },
    });
    renderConsole();
}

function passEnabled(key) {
    return settings[`librarian_pass_${key}`] !== false;
}

function nightlySummary() {
    if (!settings.librarian_nightly_enabled) return 'Nightly round: off — passes run only when you click Run.';
    const t = settings.librarian_nightly_time || '03:30';
    const sc = (settings.librarian_nightly_scopes || ['default']).join(', ');
    const skipped = PASSES.filter(p => !passEnabled(p.key)).map(p => p.title.toLowerCase());
    return `Nightly round: on · ${t} · scopes: ${sc}`
        + (skipped.length ? ` · skipping ${skipped.join(', ')}` : '');
}

function actionsDropdown(spec, libDisabled) {
    return `
        <details class="dash-action-dropdown">
            <summary><span>Actions</span><span class="chev">▾</span></summary>
            <div class="dash-action-dropdown-menu">
                ${spec.actions.map((a, i) => `
                    <button data-card="${escAttr(spec.key)}" data-idx="${i}"
                        ${a.run && libDisabled ? 'disabled title="Enable the Librarian alpha in Settings → Plugins → Mind Palace"' : ''}
                        ${a.danger ? 'style="color:var(--error,#f44)"' : ''}>${escHtml(a.label)}</button>`).join('')}
            </div>
        </details>`;
}

function passCard(p, st, libEnabled) {
    const on = passEnabled(p.key);
    const mine = (st.scopes || []).find(s => s.scope === scope && s.pass === p.key);
    const statusLine = mine
        ? `last ${timeAgo(mine.last_pass)} · ${mine.passes_today} today`
        : 'never run in this scope';
    return `
        <div class="mind-mem-card pal-admin-card" data-card="${escAttr(p.key)}"
             style="flex:1 1 230px;min-width:215px${on ? '' : ';opacity:.6'}">
            <div class="palace-self-card-head">
                <span class="palace-self-title">${p.icon} ${escHtml(p.title)}</span>
                <label class="setting-toggle" style="margin-left:auto" title="Include this pass in the nightly round. Off = the nightly skips it; Run always works.">
                    <input type="checkbox" data-pass-toggle="${escAttr(p.key)}" ${on ? 'checked' : ''}>
                    <span>nightly</span>
                </label>
            </div>
            <div class="palace-self-hint">${escHtml(p.blurb)}</div>
            <div class="palace-self-hint" data-pass-status="${escAttr(p.key)}">${escHtml(libEnabled ? statusLine : 'librarian disabled (alpha)')}</div>
            ${actionsDropdown(p, !libEnabled)}
            <div class="palace-self-hint pal-admin-result" data-result="${escAttr(p.key)}"></div>
        </div>`;
}

function opsCard(o) {
    return `
        <div class="mind-mem-card pal-admin-card" data-card="${escAttr(o.key)}"
             style="flex:1 1 230px;min-width:215px${o.danger ? ';border-color:var(--error,#f44)' : ''}">
            <div class="palace-self-card-head">
                <span class="palace-self-title">${o.icon} ${escHtml(o.title)}</span>
            </div>
            <div class="palace-self-hint">${escHtml(o.blurb)}</div>
            ${actionsDropdown(o, false)}
            <div class="palace-self-hint pal-admin-result" data-result="${escAttr(o.key)}"></div>
        </div>`;
}

async function renderConsole() {
    const el = content();
    if (!el) return;
    let st;
    try {
        st = await palaceGet(`librarian/status?scope=${encodeURIComponent(scope)}`);
    } catch (e) {
        el.innerHTML = `<div class="mind-empty">Failed to load: ${escHtml(e.message)}</div>`;
        return;
    }
    const libEnabled = st.enabled !== false;
    const master = libEnabled
        ? 'alpha toggle on'
        : 'disabled — enable in Settings → Plugins → Mind Palace (alpha)';
    el.innerHTML = `
        <div class="palace-self-card-head">
            <span class="palace-self-title">\u{1F9F9} Librarian</span>
            <span class="palace-self-hint" id="pal-adm-master">${escHtml(master)}</span>
            <button class="mind-btn-sm" id="pal-adm-settings" style="margin-left:auto">⚙ Settings</button>
        </div>
        <div class="palace-self-hint" id="pal-adm-nightly">${escHtml(nightlySummary())}</div>
        <div class="palace-self-hint" id="pal-adm-running"></div>
        <div style="display:flex;gap:12px;flex-wrap:wrap;align-items:stretch;margin:8px 0 18px">
            ${PASSES.map(p => passCard(p, st, libEnabled)).join('')}
        </div>
        <div class="palace-self-card-head">
            <span class="palace-self-title">\u{1F6E0} Maintenance & Migration</span>
            <span class="palace-self-hint">per-scope · every run lands in the Ledger</span>
        </div>
        <div style="display:flex;gap:12px;flex-wrap:wrap;align-items:stretch;margin:8px 0">
            ${OPS.map(opsCard).join('')}
        </div>`;
    bindConsole(el);
    refreshRunning(el, st);
}

function bindConsole(el) {
    // Per-pass nightly toggles → install-wide plugin setting (partial merge).
    el.querySelectorAll('[data-pass-toggle]').forEach(input => {
        input.addEventListener('change', async () => {
            const key = input.dataset.passToggle;
            try {
                await savePluginSettings({ [`librarian_pass_${key}`]: input.checked });
                const card = el.querySelector(`.pal-admin-card[data-card="${key}"]`);
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

    el.querySelector('#pal-adm-settings')?.addEventListener('click', settingsModal);
}

function setResult(el, key, text) {
    const box = el.querySelector(`[data-result="${key}"]`);
    if (box) box.textContent = text;
}

async function runAction(el, spec, action) {
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
        if (st.enabled === false) box.textContent = 'librarian disabled (alpha)';
        else box.textContent = mine
            ? `last ${timeAgo(mine.last_pass)} · ${mine.passes_today} today`
            : 'never run in this scope';
    }
}

// ⚙ Librarian settings — the hidden manifest fields, rendered by the SAME
// shared renderer the Settings page uses (schema lives in plugin.json only).
// The 4 pass toggles are excluded — the cards are their home.
async function settingsModal() {
    let schema = [];
    try {
        const r = await fetch('/api/webui/plugins', { credentials: 'same-origin' });
        schema = ((await r.json()).plugins || [])
            .find(p => p.name === 'mindpalace')?.settings_schema || [];
    } catch {}
    const fields = schema
        .filter(f => f.hidden && !f.key.startsWith('librarian_pass_'))
        .map(f => ({ ...f, hidden: false }));
    if (!fields.length) { ui.showToast('Settings schema unavailable', 'error'); return; }
    const { renderSettingsForm, readSettingsForm } =
        await import('../../shared/plugin-settings-renderer.js');

    document.querySelector('.mind-modal-overlay')?.remove();
    const overlay = document.createElement('div');
    overlay.className = 'pr-modal-overlay mind-modal-overlay';
    overlay.innerHTML = `
        <div class="pr-modal palace-ent-modal">
            <div class="pr-modal-header">
                <h3>⚙ Librarian settings</h3>
                <button class="mind-btn-sm mind-modal-close">✕</button>
            </div>
            <div class="pr-modal-body view-scroll">
                <div id="pal-adm-settings-form"></div>
                <div class="palace-more-wrap"><button class="mind-btn" id="pal-adm-settings-save">Save</button></div>
            </div>
        </div>`;
    document.body.appendChild(overlay);
    const close = () => overlay.remove();
    overlay.querySelector('.mind-modal-close').addEventListener('click', close);
    setupModalClose(overlay, close);
    const form = overlay.querySelector('#pal-adm-settings-form');
    renderSettingsForm(form, fields, settings);
    overlay.querySelector('#pal-adm-settings-save').addEventListener('click', async () => {
        try {
            await savePluginSettings(readSettingsForm(form, fields));
            ui.showToast('Librarian settings saved', 'success');
            close();
            const nl = content()?.querySelector('#pal-adm-nightly');
            if (nl) nl.textContent = nightlySummary();
        } catch (e) { ui.showToast(`Save failed: ${e.message}`, 'error'); }
    });
}
