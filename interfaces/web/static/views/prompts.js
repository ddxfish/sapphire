// views/prompts.js - Prompt editor view (accordion-based inline editing)
import { listPrompts, getPrompt, getComponentsWithSources, savePrompt, deletePrompt,
         saveComponent, deleteComponent, loadPrompt, renamePiece } from '../shared/prompt-api.js';
import { PERSONA_TABS } from '../shared/persona-tabs.js';
import { renderSectionTabs, bindSectionTabs } from '../shared/section-tabs.js';
import { renderPanelList, bindPanelList } from '../shared/panel-list.js';
import { showExportDialog, showImportDialog } from '../shared/import-export.js';
import { openDeleteModal, openCleanupModal, openBulkVaultModal } from './prompts-cleanup.js';
import { setupModalClose } from '../shared/modal.js';
import * as ui from '../ui.js';
import { updateScene } from '../features/scene.js';
import { helpPills } from '../features/video-link.js';

// ── State ──
let container = null;
let prompts = [];
let components = {};
let componentSources = {};  // {type: {key: pluginName}} — plugin-pack pieces (🧩 badge)
let hiddenPieceKeys = {};   // {type: Set(keys)} — hidden pack scaffolding; refs to
                            // these still render, so the ⚠ badge must not flag them
let vaultNames = new Set(); // prompt names resolving from the vault (unlocked only)
let vaultPieces = {};       // {type: Set(keys)} — vault pieces (unlocked only)
let vaultState = { exists: false, unlocked: false };
let multiCheck = false;          // bulk store-move mode (assembled only)
let checkedSections = new Set(); // 'prompt' + section types picked for the bulk move
let multiCheckFor = null;        // prompt the mode was entered on — exits on switch
let rosterCheck = false;         // roster multi-check (bulk prompt actions)
let checkedPrompts = new Set();  // prompt names picked in roster multi-check
let lastToggledPrompt = null;    // shift-click range anchor (plain clicks move it)
let lastShiftRange = null;       // names the previous shift-click applied — replaced, not stacked
let stockNames = new Set();      // prompts shipped in core/prompt_defaults (roster 'Core' selector)
let stockPieces = {};            // {type: Set(keys)} — pieces shipped in core/prompt_defaults (modals' 'Custom')
let viewVisible = false;
let promptDetails = {};     // { name: { char_count, components, type, ... } }
let selected = null;
let selectedData = null;
let activePromptName = null;
let openAccordion = null;
let editTarget = {};        // { type: key } per-type editing target
let saveTimer = null;
let compSaveTimers = {};
// Editing-session reasons ('prompt' or `${type}:${key}` → {text, at}). Sent
// with each save; the palace ledger folds them into the session's row. A
// why belongs to the editing session it was typed in: entries age out with
// the backend's 30-min window (scout find: a stale why silently mislabeling
// a later edit is the exact failure this feature exists to prevent), and
// ALL entries wipe on prompt switch.
let pendingReasons = {};
const REASON_WINDOW_MS = 30 * 60 * 1000;

function liveReason(id) {
    const r = pendingReasons[id];
    if (!r) return '';
    if (Date.now() - r.at > REASON_WINDOW_MS) { delete pendingReasons[id]; return ''; }
    return r.text;
}

function touchReason(id) {
    const r = pendingReasons[id];
    if (r) r.at = Date.now();   // content keystrokes keep the session's why alive
}

function ensureReasonRow(anchor, id, commitNow) {
    // commitNow: async () => save carrying the current reason — awaited by
    // the ✓ button so "sent" means sent (she can read it right away), not
    // "sitting in the field" (Krem's live find, 2026-07-23).
    const sib = anchor.nextElementSibling;
    if (sib?.classList?.contains('pr-reason-row')) {
        if (sib.dataset.rid === id) return;
        sib.remove();   // row belonged to a different piece — never show a stale why
    }
    const row = document.createElement('div');
    row.className = 'pr-reason-row';
    row.dataset.rid = id;
    const inp = document.createElement('input');
    inp.type = 'text';
    inp.className = 'input pr-reason-input';
    inp.placeholder = 'why? — optional, lands next to this change in her ledger';
    inp.value = liveReason(id);
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'btn-icon pr-reason-commit';
    btn.textContent = '✓';
    btn.title = 'Send this reason now — lands in her ledger immediately';
    row.appendChild(inp);
    row.appendChild(btn);
    anchor.insertAdjacentElement('afterend', row);
    inp.addEventListener('input', () => {
        pendingReasons[id] = { text: inp.value.trim(), at: Date.now() };
    });
    const commit = async () => {
        if (!inp.value.trim()) return;
        btn.disabled = true;
        btn.textContent = '…';
        try {
            await commitNow();
            btn.textContent = 'sent ✓';
        } catch {
            btn.textContent = '✕';
            ui.showToast('Reason send failed', 'error');
        }
        setTimeout(() => { btn.textContent = '✓'; btn.disabled = false; }, 1500);
    };
    btn.addEventListener('click', commit);
    inp.addEventListener('keydown', ev => { if (ev.key === 'Enter') commit(); });
    inp.addEventListener('change', () => { commitNow().catch(() => {}); });
}
let previewOpen = false;

const SINGLE_TYPES = ['character', 'location', 'goals', 'relationship', 'format', 'scenario'];
const MULTI_TYPES  = ['extras', 'emotions'];
const ALL_TYPES    = [...SINGLE_TYPES, ...MULTI_TYPES];
const ICONS = {
    character: '\u{1F464}', location: '\u{1F3E0}', goals: '\u{1F3AF}', relationship: '\u{1F49C}',
    format: '\u{1F4DD}', scenario: '\u{1F30D}', extras: '\u{1F9E9}', emotions: '\u{2728}'
};

export default {
    init(el) { container = el; bindBus(); },
    async show() {
        viewVisible = true;
        if (window._viewSelect) { selected = window._viewSelect; delete window._viewSelect; }
        await loadAll(); render();
    },
    hide() { viewVisible = false; }
};

// ── Origin stamps (finding 6: a save of vault-loaded content must SAY so,
// so the server can refuse it after an idle-lock instead of splashing
// decrypted text into the plaintext store) ──
const promptSaveData = (name, data) =>
    vaultNames.has(name) ? { ...data, origin: 'vault' } : data;
const pieceOrigin = (type, key) =>
    vaultPieces[type]?.has(key) ? 'vault' : undefined;
// 🗝 marker for vault pieces — headers, dropdowns, chips (only ever true
// while unlocked; the 🧩 pack lane keeps its own glyph)
const vKey = (type, key) =>
    vaultPieces[type]?.has(key) ? ' \u{1F5DD}' : '';
// The PUT response says where a piece landed — badge immediately instead of
// waiting for the SSE echo (which the focus guard may defer mid-edit).
function noteVaultRouted(type, key, res) {
    if (!res?.vault) return;
    if (!vaultPieces[type]) vaultPieces[type] = new Set();
    vaultPieces[type].add(key);
}

// ── Event-bus refresh (this view had ZERO listeners — a vault lock in
// another tab, or Sapphire editing pieces, left a stale roster with vault
// names still visible) ──
// FOCUS GUARD: funnel-level publishes carry no origin, so this tab's OWN
// debounced saves echo back over SSE ~700ms after typing stops — a render()
// then yanks the cursor out of the textarea mid-edit (Krem, 2026-08-10).
// While an editable inside this view has focus, defer the refresh; the
// focusout handler catches up as soon as the user leaves the field.
let busBound = false;
let pendingRefresh = false;
let promptSaveInFlight = false;  // debounced prompt save scheduled/awaiting
// This tab's OWN vault moves echo back as vault_changed a beat later; the
// handler already applied them locally, so the echo is skipped while this
// window is open (2026-09-08 — a bulk move's echoes each reloaded the whole
// view, throttling the batch to one item per two seconds).
let echoQuietUntil = 0;
const quietEcho = (ms = 2000) => { echoQuietUntil = Date.now() + ms; };
// Paint-sequence guard: a slower /api/prompts response must never overwrite
// a newer one (the roster painted pre-batch state with no 🗝 and no error).
let _loadSeq = 0;
let _busRefreshInFlight = false, _busRefreshAgain = false;

const _editableFocused = () => {
    const ae = document.activeElement;
    return !!(container?.contains(ae) &&
        (ae.tagName === 'TEXTAREA' || ae.tagName === 'INPUT' || ae.tagName === 'SELECT'));
};

async function doBusRefresh() {
    pendingRefresh = false;
    // Coalesce: one load in flight; events that land meanwhile run ONE more.
    if (_busRefreshInFlight) { _busRefreshAgain = true; return; }
    _busRefreshInFlight = true;
    try {
        do {
            _busRefreshAgain = false;
            const applied = await loadAll();
            if (!applied) continue;   // superseded or failed — nothing new to paint
            if (selected && !prompts.find(p => p.name === selected)) {
                // Selection vanished (vault locked / deleted elsewhere) —
                // drop the editor rather than editing a ghost.
                selected = activePromptName || (prompts[0]?.name ?? null);
                selectedData = selected ? (promptDetails[selected] || null) : null;
                openAccordion = null;
                editTarget = {};
            }
            render();
        } while (_busRefreshAgain);
    } finally {
        _busRefreshInFlight = false;
    }
}

function bindBus() {
    if (busBound) return;
    busBound = true;
    import('../core/event-bus.js').then(eventBus => {
        const refreshIfVisible = (data) => {
            if (!viewVisible) return;
            if (data?.action === 'loaded') return;  // activation side effect
            if (data?.action === 'vault_changed' && Date.now() < echoQuietUntil) return;  // our own move
            // In-flight guard (Bobby bug, 2026-08-18): while a debounced
            // prompt save is pending, a bus refresh would re-fetch the OLD
            // server copy, replace selectedData, and the debounce would then
            // persist the clobbered state — piece +New/switches lost. Defer;
            // the save's completion runs the catch-up.
            if (_editableFocused() || promptSaveInFlight) { pendingRefresh = true; return; }
            doBusRefresh();
        };
        eventBus.on(eventBus.Events.PROMPT_CHANGED, refreshIfVisible);
        eventBus.on(eventBus.Events.COMPONENTS_CHANGED, refreshIfVisible);
        eventBus.on(eventBus.Events.PROMPT_DELETED, refreshIfVisible);
    });
    // Deferred-refresh catch-up: run once focus truly left the editables
    // (150ms lets focus settle — focusout fires before the new target owns it).
    container?.addEventListener('focusout', () => {
        if (!pendingRefresh) return;
        setTimeout(() => {
            if (viewVisible && pendingRefresh && !_editableFocused()) doBusRefresh();
        }, 150);
    });
}

// ── Data ──
// Returns true when this load's data was applied; false when a newer load
// superseded it or a fetch failed (the caller must NOT paint in either case —
// painting stale state showed a finished vault batch as "nothing moved").
async function loadAll() {
    const seq = ++_loadSeq;
    try {
        const [pList, compData] = await Promise.all([listPrompts(), getComponentsWithSources()]);
        if (seq !== _loadSeq) return false;
        prompts = (pList || []).sort((a, b) => a.name.localeCompare(b.name));
        components = compData.components || {};
        componentSources = compData.sources || {};
        hiddenPieceKeys = Object.fromEntries(
            Object.entries(compData.hidden_keys || {}).map(([t, keys]) => [t, new Set(keys)]));
        vaultNames = new Set(prompts.filter(p => p.vault).map(p => p.name));
        vaultPieces = Object.fromEntries(
            Object.entries(compData.vault_pieces || {}).map(([t, keys]) => [t, new Set(keys)]));
        vaultState = pList?.vaultState || { exists: false, unlocked: false };
        stockNames = new Set(pList?.stock || []);
        stockPieces = Object.fromEntries(
            Object.entries(compData.stock_pieces || {}).map(([t, keys]) => [t, new Set(keys)]));

        const active = prompts.find(p => p.active);
        activePromptName = active?.name || null;

        if (!selected && activePromptName) selected = activePromptName;
        else if (!selected && prompts.length > 0) selected = prompts[0].name;

        // Fetch details for all prompts in parallel (for sidebar meta)
        const results = await Promise.allSettled(prompts.map(p => getPrompt(p.name)));
        if (seq !== _loadSeq) return false;
        results.forEach((r, i) => {
            if (r.status === 'fulfilled' && r.value) {
                promptDetails[prompts[i].name] = r.value;
            }
        });

        // Use already-fetched data for selected prompt — UNLESS a debounced
        // prompt save is pending: the local copy is then AHEAD of the server
        // and overwriting it loses the un-persisted change (Bobby bug round
        // 2: an SSE echo arriving inside the await window started this load
        // BEFORE the in-flight flag went up, and this line clobbered the new
        // piece selection with the server's old copy at completion).
        if (promptSaveInFlight) {
            // keep local selectedData — the save persists it in <600ms
        } else if (selected && promptDetails[selected]) {
            selectedData = promptDetails[selected];
        } else if (selected) {
            try { selectedData = await getPrompt(selected); } catch { selectedData = null; }
        }
        return true;
    } catch (e) {
        console.warn('Prompts load failed:', e);
        if (seq === _loadSeq) ui.showToast('Prompts refresh failed — showing the last good list', 'warning');
        return false;
    }
}

// ── Main Render ──
function render() {
    if (!container) return;
    // Full re-render resets every scroll to the top — carry them over. The
    // roster was already saved; the editor pane wasn't (`.pr-content` below
    // 1440px, `.pr-body` above — only one is non-zero at a time), so each
    // piece toggle / vault move yanked the pieces list to the top (2026-09-08).
    const listScroll = container.querySelector('.panel-list-items')?.scrollTop || 0;
    const contentScroll = container.querySelector('.pr-content')?.scrollTop || 0;
    const bodyScroll = container.querySelector('.pr-body')?.scrollTop || 0;

    container.innerHTML = `
        ${renderSectionTabs(PERSONA_TABS, 'prompts', helpPills('Prompts', { video: 'JxgNAk4Y2qI', doc: 'PROMPTS.md', inline: true }))}
        <div class="two-panel">
            ${renderPanelList({
                title: rosterCheck ? `Prompts · ${checkedPrompts.size} selected` : 'Prompts',
                items: prompts,
                selectedId: selected,
                idKey: 'name',
                listClass: 'pr-roster pl-stacked',
                itemClass: p => p.name === activePromptName ? 'active-prompt' : '',
                renderItem: p => {
                    const d = promptDetails[p.name];
                    const tokens = d?.token_count || p.token_count;
                    const tokenStr = tokens ? formatCount(tokens) + ' tokens' : '';
                    const typeName = p.type === 'monolith' ? 'Monolith' : 'Assembled';
                    const character = d?.components?.character;
                    const meta = [typeName, character ? '👤 ' + character : '',
                                  p.source ? '🧩 Plugin: ' + p.source : '',
                                  p.vault ? '\u{1F5DD} Vault' : ''].filter(Boolean).join(' · ');
                    const isActive = p.name === activePromptName;
                    const check = rosterCheck
                        ? `<span style="margin-right:6px">${p.source ? '—' : (checkedPrompts.has(p.name) ? '☑' : '☐')}</span>`
                        : '';
                    // ⚠ dangling-ref badge — suppressed while a vault exists
                    // and is locked (sealed pieces LOOK missing; badging
                    // them would be false alarms).
                    const dangList = [];
                    if (d?.components && !(vaultState.exists && !vaultState.unlocked)) {
                        for (const [t, v] of Object.entries(d.components)) {
                            if (t.startsWith('_')) continue;
                            for (const k of (Array.isArray(v) ? v : (v ? [v] : []))) {
                                // KEY MEMBERSHIP, not text truthiness — pieces
                                // with empty text (shipped 'none', fresh
                                // blanks) EXIST and must not badge as missing.
                                // Hidden scaffolding pieces also exist (the
                                // render merge is intact) — not missing either.
                                if (typeof k === 'string' && k && !(k in (components[t] || {}))
                                        && !hiddenPieceKeys[t]?.has(k)) {
                                    dangList.push(`${t}/${k}`);
                                }
                            }
                        }
                    }
                    return `<div class="pr-item-info">
                        <span class="pr-item-name">${check}${p.privacy_required ? '🔒 ' : ''}${p.name}${isActive ? ' (Active)' : ''}</span>
                        ${tokenStr ? `<span class="pr-item-tokens">${tokenStr}</span>` : ''}
                        <span class="pr-item-meta">${meta}</span>
                        ${dangList.length ? `<span class="pr-item-meta" style="color:#f59e0b" title="Missing: ${escAttr(dangList.join(', '))}">⚠ ${dangList.length} missing ref${dangList.length > 1 ? 's' : ''}</span>` : ''}
                    </div>`;
                },
                addTitle: 'New prompt',
                extraHeader: `<button class="btn-sm" id="pr-roster-check" title="Select prompts for bulk actions"${rosterCheck ? ' style="outline:1px solid var(--accent)"' : ''}>☑</button>`
                    + '<button class="btn-sm" id="pr-cleanup" title="Cleanup tools (orphans, trash)">🧹</button>'
                    + '<button class="btn-sm" id="pr-import" title="Import prompt">⬇</button>',
                headerExtra: rosterCheck && vaultState.unlocked ? `
                    <div style="flex-basis:100%;display:flex;gap:10px;align-items:center;font-size:var(--font-xs);margin-top:6px">
                        <span>Vault:</span>
                        <button class="btn-sm" id="pr-vault-in" title="Move selected prompts (and their pieces) into the vault">Move In</button>
                        <button class="btn-sm" id="pr-vault-out" title="Move selected prompts (and their pieces) out to plaintext">Move Out</button>
                    </div>` : '',
                showDelete: true,
                deletable: rosterCheck ? checkedPrompts.size > 0 : !!selected,
                deleteTitle: rosterCheck ? `Delete ${checkedPrompts.size} selected`
                                         : `Delete "${selected || ''}"`,
                subheader: rosterCheck ? `
                    <div style="display:flex;gap:12px;align-items:center;font-size:var(--font-xs);padding:6px 12px;border-bottom:1px solid var(--border)">
                        <span>Select:</span>
                        <span id="pr-sel-all" style="color:var(--accent);cursor:pointer;text-decoration:underline">All</span>
                        <span id="pr-sel-none" style="color:var(--accent);cursor:pointer;text-decoration:underline">None</span>
                        <span id="pr-sel-core" style="color:var(--accent);cursor:pointer;text-decoration:underline"
                              title="The prompts that ship with Sapphire (stock personas)">Core</span>
                        <span id="pr-sel-custom" style="color:var(--accent);cursor:pointer;text-decoration:underline"
                              title="Your prompts — everything except the stock ones">Custom</span>
                    </div>` : '',
            })}
            <div class="panel-right">
                <div class="pr-content">
                    <div class="pr-editor">
                        ${selected ? renderEditor() : '<div class="view-placeholder"><p>Select a prompt</p></div>'}
                    </div>
                    <div class="pr-preview">
                        ${selected ? renderPreview() : ''}
                    </div>
                </div>
            </div>
        </div>
    `;
    bindEvents();
    const listEl = container.querySelector('.panel-list-items');
    if (listEl) listEl.scrollTop = listScroll;
    const contentEl = container.querySelector('.pr-content');
    if (contentEl && contentScroll) contentEl.scrollTop = contentScroll;
    const bodyEl = container.querySelector('.pr-body');
    if (bodyEl && bodyScroll) bodyEl.scrollTop = bodyScroll;
}


// Sections of the selected prompt that actually use pieces — bulk-move
// targets. Singles count when set, multis when any chip is active.
function usedSections() {
    const c = selectedData?.components || {};
    return [...SINGLE_TYPES.filter(t => c[t]),
            ...MULTI_TYPES.filter(t => (c[t] || []).length)];
}

function renderEditor() {
    if (!selectedData) return '<div class="view-placeholder"><p>Loading...</p></div>';
    const p = selectedData;
    const isActive = selected === activePromptName;
    const isMonolith = p.type === 'monolith';
    // Multi-check is per-prompt — switching prompts (select, rename,
    // duplicate, delete all land here) exits the mode.
    if (multiCheck && multiCheckFor !== selected) {
        multiCheck = false;
        checkedSections = new Set();
    }

    return `
        <div class="pr-header">
            <div class="pr-header-left">
                <div style="display:flex;align-items:center;gap:6px">
                    <h2 id="pr-prompt-name" style="margin:0">${p.privacy_required ? '\u{1F512} ' : ''}${selected}</h2>
                    <button class="btn-icon" id="pr-rename-prompt" title="Rename prompt" style="font-size:0.875rem;opacity:0.5">\u270F</button>
                </div>
                <span class="view-subtitle">${isMonolith ? 'Monolith' : 'Assembled'}${p.char_count ? ' \u00B7 ' + formatCount(p.char_count) + ' chars' : ''}${(prompts.find(x => x.name === selected)?.source) ? ' \u00B7 \u{1F9E9} Plugin: ' + prompts.find(x => x.name === selected).source : ''}${vaultNames.has(selected) ? ' \u00B7 \u{1F5DD} Vault' : ''}</span>
            </div>
            <div class="pr-header-actions">
                ${!isActive ? '<button class="btn-primary" id="pr-activate">Activate</button>' : '<span class="badge badge-active">Active</span>'}
                <button class="btn-sm" id="pr-dup">Duplicate</button>
                <button class="btn-sm" id="pr-export">Export</button>
            </div>
        </div>
        <div class="pr-body">
            ${isMonolith ? renderMonolith(p) : renderAssembled(p)}
            ${vaultState.unlocked && !prompts.find(x => x.name === selected)?.source ? `
            <div class="pr-privacy">
                ${!multiCheck ? `
                <label><input type="checkbox" id="pr-vault-toggle" ${vaultNames.has(selected) ? 'checked' : ''}>
                \u{1F5DD} Keep in vault (encrypted at rest; private by construction)</label>` : ''}
                ${!isMonolith ? (multiCheck ? `
                <div style="display:flex;flex-direction:column;gap:6px">
                    <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;font-size:var(--font-xs)">
                        <label style="display:flex;gap:5px;align-items:center" title="The prompt entry itself (its name + piece list). In the vault it disappears from the prompt list while locked.">
                            <input type="checkbox" id="pr-bulk-prompt" ${checkedSections.has('prompt') ? 'checked' : ''}> This prompt ("${esc(selected)}")</label>
                        <span style="opacity:0.5">·</span>
                        <span id="pr-bulk-all" style="color:var(--accent);cursor:pointer;text-decoration:underline">All</span>
                        <span id="pr-bulk-none" style="color:var(--accent);cursor:pointer;text-decoration:underline">None</span>
                        <span id="pr-bulk-main" style="color:var(--accent);cursor:pointer;text-decoration:underline">Main pieces</span>
                    </div>
                    <div style="display:flex;gap:8px">
                        <button class="btn-sm" id="pr-vault-all-in">\u{1F5DD} Move to vault</button>
                        <button class="btn-sm" id="pr-vault-all-out">Move to plaintext</button>
                        <button class="btn-sm" id="pr-bulk-cancel">Cancel</button>
                    </div>
                </div>` : `
                <div style="margin-top:6px">
                    <button class="btn-sm" id="pr-bulk-enter">☑ Select & move…</button>
                </div>`) : ''}
            </div>` : ''}
            ${p.privacy_required && !vaultNames.has(selected) ? `
            <div class="pr-privacy text-muted" style="font-size:var(--font-xs)">
                \u{1F512} Legacy private flag — refuses cloud providers. New private
                prompts live in the vault instead (privacy is automatic there).
            </div>` : ''}
        </div>
    `;
}

function renderMonolith(p) {
    return `<textarea id="pr-content" class="pr-textarea" placeholder="Enter your prompt...">${esc(p.content || '')}</textarea>`;
}

function renderAssembled(p) {
    const comps = p.components || {};

    // Only multi-select types need a separate edit target
    for (const t of MULTI_TYPES) {
        if (!editTarget[t]) {
            const sel = comps[t] || [];
            editTarget[t] = sel[0] || Object.keys(components[t] || {})[0] || '';
        }
    }

    return `
        <div class="pr-accordions">
            ${SINGLE_TYPES.map(t => renderSingleAccordion(t, comps)).join('')}
            ${MULTI_TYPES.map(t => renderMultiAccordion(t, comps)).join('')}
        </div>
    `;
}

function renderSingleAccordion(type, comps) {
    const current = comps[type] || '';
    const isOpen = openAccordion === type;
    const defs = components[type] || {};
    const keys = Object.keys(defs).sort();
    const currentText = defs[current] || '';

    return `
        <div class="pr-accordion${isOpen ? ' open' : ''}" data-type="${type}">
            <div class="pr-accordion-header" data-type="${type}">
                ${multiCheck ? `<input type="checkbox" class="pr-sec-check" data-type="${type}" ${checkedSections.has(type) ? 'checked' : ''} ${!current ? 'disabled' : ''} style="margin-right:4px">` : ''}
                <span class="pr-acc-icon">${ICONS[type]}</span>
                <div class="pr-acc-text">
                    <span class="pr-acc-label">${cap(type)}</span>
                    <span class="pr-acc-value">${current ? current + vKey(type, current) : 'none'}</span>
                </div>
                <span class="pr-acc-arrow">${isOpen ? '\u25BE' : '\u25B8'}</span>
            </div>
            ${isOpen ? `
                <div class="pr-accordion-body">
                    <div class="pr-piece-row">
                        <select class="pr-piece-select" data-type="${type}">
                            <option value="">None</option>
                            ${keys.map(k => `<option value="${k}"${k === current ? ' selected' : ''}>${k}${componentSources[type]?.[k] ? ' \u{1F9E9}' : ''}${vKey(type, k)}</option>`).join('')}
                        </select>
                        ${current ? `<button class="btn-icon pr-rename-btn" data-type="${type}" data-key="${current}" title="Rename">\u270F</button>` : ''}
                    </div>
                    ${current ? `
                        <textarea class="pr-def-text" data-type="${type}" data-key="${current}" rows="4" placeholder="Definition text...">${esc(currentText)}</textarea>
                        <div class="pr-def-actions">
                            <button class="btn-sm" data-action="new-def" data-type="${type}">+ New</button>
                            <button class="btn-sm" data-action="dup-def" data-type="${type}" data-key="${current}">Duplicate</button>
                            <button class="btn-sm danger" data-action="del-def" data-type="${type}" data-key="${current}">Delete</button>
                            ${vaultState.unlocked && !componentSources[type]?.[current] ? `
                            <label style="font-size:var(--font-xs);display:flex;gap:5px;align-items:center;margin-left:auto">
                                <input type="checkbox" class="pr-piece-vault" data-key="${current}" ${vaultPieces[type]?.has(current) ? 'checked' : ''}> \u{1F5DD} In vault
                            </label>` : ''}
                        </div>
                    ` : `<p class="text-muted" style="font-size:var(--font-sm)">Select a piece above or click + New.</p>
                         <div class="pr-def-actions"><button class="btn-sm" data-action="new-def" data-type="${type}">+ New</button></div>`}
                </div>
            ` : ''}
        </div>
    `;
}

function renderMultiAccordion(type, comps) {
    const current = comps[type] || [];
    const isOpen = openAccordion === type;
    const defs = components[type] || {};
    const keys = Object.keys(defs).sort();
    const target = editTarget[type] || current[0] || keys[0] || '';
    const targetText = defs[target] || '';
    const headerValue = current.length
        ? current.slice().sort().map(k => k + vKey(type, k)).join(', ') : 'none';

    return `
        <div class="pr-accordion${isOpen ? ' open' : ''}" data-type="${type}">
            <div class="pr-accordion-header" data-type="${type}">
                ${multiCheck ? `<input type="checkbox" class="pr-sec-check" data-type="${type}" ${checkedSections.has(type) ? 'checked' : ''} ${!current.length ? 'disabled' : ''} style="margin-right:4px">` : ''}
                <span class="pr-acc-icon">${ICONS[type]}</span>
                <div class="pr-acc-text">
                    <span class="pr-acc-label">${cap(type)}</span>
                    <span class="pr-acc-value">${headerValue}</span>
                </div>
                <span class="pr-acc-arrow">${isOpen ? '\u25BE' : '\u25B8'}</span>
            </div>
            ${isOpen ? `
                <div class="pr-accordion-body">
                    <div class="pr-chips">
                        ${keys.map(k => `
                            <label class="pr-chip${current.includes(k) ? ' active' : ''}" title="${escAttr((componentSources[type]?.[k] ? `[Plugin: ${componentSources[type][k]}] ` : '') + (vaultPieces[type]?.has(k) ? '[Vault] ' : '') + (defs[k] || ''))}">
                                <input type="checkbox" data-type="${type}" data-key="${k}" ${current.includes(k) ? 'checked' : ''}>
                                <span>${k}${componentSources[type]?.[k] ? ' \u{1F9E9}' : ''}${vKey(type, k)}</span>
                            </label>
                        `).join('')}
                    </div>
                    ${keys.length ? `
                        <div class="pr-piece-row">
                            <select class="pr-piece-select" data-type="${type}">
                                ${keys.map(k => `<option value="${k}"${k === target ? ' selected' : ''}>${k}${componentSources[type]?.[k] ? ' \u{1F9E9}' : ''}${vKey(type, k)}</option>`).join('')}
                            </select>
                            <button class="btn-icon pr-rename-btn" data-type="${type}" data-key="${target}" title="Rename">\u270F</button>
                        </div>
                        <textarea class="pr-def-text" data-type="${type}" data-key="${target}" rows="3" placeholder="Definition text...">${esc(targetText)}</textarea>
                        <div class="pr-def-actions">
                            <button class="btn-sm" data-action="new-def" data-type="${type}">+ New</button>
                            <button class="btn-sm" data-action="dup-def" data-type="${type}" data-key="${target}">Duplicate</button>
                            <button class="btn-sm danger" data-action="del-def" data-type="${type}" data-key="${target}">Delete</button>
                            ${vaultState.unlocked && !componentSources[type]?.[target] ? `
                            <label style="font-size:var(--font-xs);display:flex;gap:5px;align-items:center;margin-left:auto">
                                <input type="checkbox" class="pr-piece-vault" data-key="${target}" ${vaultPieces[type]?.has(target) ? 'checked' : ''}> \u{1F5DD} In vault
                            </label>` : ''}
                        </div>
                    ` : `<div class="pr-def-actions"><button class="btn-sm" data-action="new-def" data-type="${type}">+ New</button></div>`}
                </div>
            ` : ''}
        </div>
    `;
}

function renderPreview() {
    const text = selectedData?.compiled || selectedData?.content || '';
    if (!text) return '<div class="pr-preview-empty">No preview available</div>';
    return `
        <div class="pr-preview-accordion">
            <div class="pr-preview-header" id="pr-preview-toggle">
                <span class="accordion-arrow">${previewOpen ? '\u25BC' : '\u25B6'}</span>
                <h3>Preview <span class="text-muted" style="font-weight:normal;font-size:var(--font-sm)">${previewOpen ? '' : '(expand)'}</span></h3>
                <span class="view-subtitle">${formatCount(text.length)} chars</span>
            </div>
            <div class="pr-preview-body" style="${previewOpen ? '' : 'display:none'}">
                <pre class="pr-preview-text">${esc(text)}</pre>
            </div>
        </div>
    `;
}

// ── Events ──
function bindEvents() {
    if (!container) return;
    bindSectionTabs(container);
    const layout = container.querySelector('.two-panel');
    if (!layout) return;

    // --- Roster (shared panel-list) ---
    bindPanelList(container, {
        onSelect: async (name, e) => {
            if (rosterCheck) {
                // Multi-check mode: row clicks toggle membership; shift-click
                // checks the whole range from the last toggled row. Pack
                // prompts never join (they can't be deleted).
                const p = prompts.find(x => x.name === name);
                if (!p || p.source) return;
                const ai = prompts.findIndex(x => x.name === lastToggledPrompt);
                const bi = prompts.findIndex(x => x.name === name);
                if (e?.shiftKey && ai !== -1 && bi !== -1 && ai !== bi) {
                    // File-manager semantics: the anchor stays put, and each
                    // shift-click REPLACES the previous shift range — so
                    // shrinking the range unchecks what fell outside it.
                    if (lastShiftRange) {
                        for (const n of lastShiftRange) checkedPrompts.delete(n);
                    }
                    const [a, b] = ai < bi ? [ai, bi] : [bi, ai];
                    lastShiftRange = new Set();
                    for (let i = a; i <= b; i++) {
                        if (!prompts[i].source) {
                            checkedPrompts.add(prompts[i].name);
                            lastShiftRange.add(prompts[i].name);
                        }
                    }
                } else {
                    checkedPrompts.has(name) ? checkedPrompts.delete(name)
                                             : checkedPrompts.add(name);
                    lastToggledPrompt = name;
                    lastShiftRange = null;
                }
                render();
                return;
            }
            selected = name;
            openAccordion = null;
            editTarget = {};
            pendingReasons = {};   // a why never crosses a prompt switch
            try { selectedData = await getPrompt(selected); } catch { selectedData = null; }
            render();
        },
        onAdd: createPrompt,
        onDelete: () => rosterCheck ? bulkDeletePrompts() : deleteCurrentPrompt(),
    });

    // --- Roster bulk mode + cleanup tools ---
    layout.querySelector('#pr-roster-check')?.addEventListener('click', () => {
        rosterCheck = !rosterCheck;
        if (!rosterCheck) { checkedPrompts = new Set(); lastToggledPrompt = null; lastShiftRange = null; }
        render();
    });
    // Roster bulk selectors (multi-check only). 'Core' = the stock prompts
    // shipped in core/prompt_defaults, per the server's stock list.
    const rosterSelect = filter => {
        checkedPrompts = new Set(prompts.filter(p => !p.source && filter(p))
                                        .map(p => p.name));
        lastShiftRange = null;
        render();
    };
    layout.querySelector('#pr-sel-all')?.addEventListener('click', () => rosterSelect(() => true));
    layout.querySelector('#pr-sel-none')?.addEventListener('click', () => rosterSelect(() => false));
    layout.querySelector('#pr-sel-core')?.addEventListener('click', () => rosterSelect(p => stockNames.has(p.name)));
    layout.querySelector('#pr-sel-custom')?.addEventListener('click', () => rosterSelect(p => !stockNames.has(p.name)));
    const openBulkVault = direction => {
        if (!checkedPrompts.size) { ui.showToast('Nothing selected', 'info'); return; }
        openBulkVaultModal({
            names: [...checkedPrompts], direction,
            componentSources, vaultPieces, vaultNames, stockPieces, stockNames,
            details: promptDetails,
            onStart: () => quietEcho(15 * 60 * 1000),   // the batch's one echo is ours
            onDone: async () => {
                quietEcho();                           // …and so is a straggler
                rosterCheck = false;
                checkedPrompts = new Set();
                lastToggledPrompt = null;
                lastShiftRange = null;
                if (await loadAll()) render();
            },
        });
    };
    layout.querySelector('#pr-vault-in')?.addEventListener('click', () => openBulkVault('in'));
    layout.querySelector('#pr-vault-out')?.addEventListener('click', () => openBulkVault('out'));
    layout.querySelector('#pr-cleanup')?.addEventListener('click', () => {
        openCleanupModal({
            components, componentSources, vaultPieces, stockPieces,
            onDone: async () => { if (await loadAll()) render(); },
        });
    });

    // --- Header actions ---
    layout.querySelector('#pr-activate')?.addEventListener('click', activateCurrentPrompt);
    layout.querySelector('#pr-dup')?.addEventListener('click', duplicatePrompt);

    // Rename prompt
    layout.querySelector('#pr-rename-prompt')?.addEventListener('click', () => {
        if (!selected || !selectedData) return;
        const h2 = layout.querySelector('#pr-prompt-name');
        const pencil = layout.querySelector('#pr-rename-prompt');
        if (!h2 || !pencil) return;

        h2.hidden = true;
        pencil.hidden = true;

        const input = document.createElement('input');
        input.type = 'text';
        input.value = selected;
        input.spellcheck = false;
        input.style.cssText = 'font-size:1.3em;font-weight:600;background:var(--input-bg);border:1px solid var(--accent);border-radius:var(--radius-sm);color:var(--text);padding:2px 8px;width:200px;';
        h2.parentNode.insertBefore(input, h2);
        input.focus();
        input.select();

        let cancelled = false;
        input.addEventListener('keydown', ev => {
            if (ev.key === 'Enter') { ev.preventDefault(); input.blur(); }
            if (ev.key === 'Escape') { cancelled = true; input.blur(); }
        });

        input.addEventListener('blur', async () => {
            const newName = input.value.trim();
            input.remove();
            h2.hidden = false;
            pencil.hidden = false;

            if (cancelled || !newName || newName === selected) return;

            try {
                // Save under new name, delete old. Origin stamped by the OLD
                // name — renamed vault content must stay vault-routed.
                const wasActive = selected === activePromptName;
                await savePrompt(newName, promptSaveData(selected, selectedData));
                await deletePrompt(selected);
                selected = newName;
                if (wasActive) {
                    await loadPrompt(newName);
                    activePromptName = newName;
                }
                await loadAll();
                render();
                ui.showToast(`Renamed to "${newName}"`, 'success');
            } catch (e) {
                ui.showToast(`Rename failed: ${e.message}`, 'error');
            }
        }, { once: true });
    });

    layout.querySelector('#pr-export')?.addEventListener('click', () => {
        if (!selected || !selectedData) return;
        // Vault export gates (finding 8): content never leaves the vault in
        // a shareable bundle — neither the prompt nor any harvested piece.
        if (vaultNames.has(selected)) {
            ui.showToast(`'${selected}' is a vault prompt — export is blocked to keep it encrypted.`, 'error');
            return;
        }
        const vaultUsed = Object.entries(getUsedPieces())
            .flatMap(([t, defs]) => Object.keys(defs)
                .filter(k => vaultPieces[t]?.has(k)).map(k => `${t}/${k}`));
        if (vaultUsed.length) {
            ui.showToast(`Export blocked — this prompt uses vault pieces: ${vaultUsed.join(', ')}`, 'error');
            return;
        }
        showExportDialog({
            type: 'Prompt',
            name: selected,
            filename: `${selected}.prompt.json`,
            checkboxes: [
                { id: 'pieces', label: 'Include pieces used by this prompt', checked: true },
            ],
            buildExport: (states) => {
                const prompt = { ...selectedData };
                if (prompt.type === 'assembled') delete prompt.content;
                delete prompt.compiled;
                delete prompt.char_count;
                delete prompt.token_count;
                const bundle = { sapphire_export: true, type: 'prompt', version: 1, name: selected, prompt };
                if (states.pieces) bundle.components = getUsedPieces();
                return bundle;
            },
        });
    });

    layout.querySelector('#pr-import')?.addEventListener('click', () => {
        showImportDialog({
            type: 'Prompt or Persona',
            overwrites: [
                { key: 'overwrite', label: 'Overwrite existing prompt and pieces' },
            ],
            existingNames: prompts.map(p => p.name),
            validate: (d) => {
                // Standard prompt export
                if (d.prompt) return null;
                // Persona bundle with embedded prompt
                if (d.sapphire_export && d.type === 'persona' && d.prompt) return null;
                return 'Invalid format: missing prompt data';
            },
            getName: (d) => {
                // Persona bundle: prompt name is nested
                if (d.sapphire_export && d.type === 'persona') return d.prompt?.name || d.name || 'imported';
                return d.name || 'imported';
            },
            onImport: async (data, { name, overwrites }) => {
                const overwrite = overwrites.overwrite || false;

                // Extract prompt data — handle persona bundles
                let promptData, importPieces;
                if (data.sapphire_export && data.type === 'persona') {
                    promptData = data.prompt?.data || data.prompt;
                    importPieces = data.components;
                } else {
                    promptData = data.prompt;
                    importPieces = data.components || data.pieces;
                }

                // Import pieces
                let skipped = 0, imported = 0;
                if (importPieces) {
                    for (const [type, defs] of Object.entries(importPieces)) {
                        for (const [key, value] of Object.entries(defs)) {
                            if (!overwrite && components[type]?.[key]) { skipped++; continue; }
                            // Import lane pins the regular store — installing
                            // shared content must not route by lock state.
                            await saveComponent(type, key, value, null, 'regular');
                            imported++;
                        }
                    }
                }

                await savePrompt(name, { ...promptData, origin: 'regular' });
                if (name === activePromptName) await loadPrompt(name);
                selected = name;
                await loadAll();
                render();
                updateScene();

                const parts = [`Imported: ${name}`];
                if (imported) parts.push(`${imported} pieces`);
                if (skipped) parts.push(`${skipped} skipped`);
                if (data.sapphire_export && data.type === 'persona') parts.push('(from persona)');
                ui.showToast(parts.join(' \u2014 '), 'success');
            },
        });
    });

    // Privacy checkbox YEETED 2026-08-12 (Krem's ruling): vault membership
    // IS the privacy bit — want a private prompt, put it in the vault (new
    // saves while unlocked go there automatically). The privacy_required
    // FIELD survives read-only: story costumes inherit it, packs ship it,
    // and legacy-flagged prompts keep refusing cloud (fail-closed) until
    // the v1.1 store-toggle migrates them in. selectedData round-trips the
    // stored value on save, so nothing existing loses its gate.

    // v1.1 store toggle — visible only while the vault is unlocked. IN is
    // the safe direction (no confirm); OUT writes decrypted content to
    // plaintext disk and clears the privacy flag, so it gets a yes/no.
    layout.querySelector('#pr-vault-toggle')?.addEventListener('change', async e => {
        const goingIn = e.target.checked;
        if (!goingIn && !confirm(
            `Move "${selected}" OUT of the vault?\n\n` +
            `Its content will be written to the regular store as plaintext ` +
            `on disk, and it will no longer require privacy.`)) {
            e.target.checked = true;
            return;
        }
        try {
            const { vaultMove } = await import('../shared/vault-api.js');
            const res = await vaultMove({ kind: 'prompt', name: selected,
                                          direction: goingIn ? 'in' : 'out' });
            ui.showToast(res?.message || 'Moved', 'success');
        } catch (err) {
            ui.showToast(err?.message || 'Move failed', 'error');
            e.target.checked = !goingIn;
            return;
        }
        await loadAll();
        render();
    });

    // Bulk store toggle (assembled only), WordPress-bulk-actions style:
    // "Select & move…" opens per-section checkboxes; the move buttons act
    // on checked sections' USED pieces only (the rest of each library is
    // untouched) plus the prompt record when its row is checked. Pieces
    // are global, so the IN confirm warns that vaulting them reaches every
    // prompt using them. Partial failure is safe — each move is
    // individually guarded server-side; clicking again finishes the job.
    const moveAll = async (direction) => {
        const goingIn = direction === 'in';
        if (!checkedSections.size) {
            ui.showToast('Nothing selected', 'info');
            return;
        }
        const pieces = [];
        for (const [type, defs] of Object.entries(getUsedPieces())) {
            if (!checkedSections.has(type)) continue;
            for (const key of Object.keys(defs)) {
                if (componentSources[type]?.[key]) continue;
                if (goingIn === !!vaultPieces[type]?.has(key)) continue;
                pieces.push({ type, key });
            }
        }
        const movePrompt = checkedSections.has('prompt')
            && goingIn !== vaultNames.has(selected);
        if (!movePrompt && !pieces.length) {
            ui.showToast(goingIn ? 'Everything selected is already in the vault'
                                 : 'Nothing selected is in the vault', 'info');
            return;
        }
        const what = [movePrompt ? `"${selected}"` : '',
                      pieces.length ? `${pieces.length} piece(s)` : '']
                     .filter(Boolean).join(' and ');
        if (!confirm(goingIn
            ? `Move ${what} into the vault?\n\nPieces are shared — any other ` +
              `prompt using them will need the vault unlocked.`
            : `Move ${what} OUT of the vault?\n\nAll of it will be written to ` +
              `the regular store as plaintext on disk.`)) return;
        const { vaultMove } = await import('../shared/vault-api.js');
        let moved = 0;
        const failed = [];
        for (const { type, key } of pieces) {
            try {
                await vaultMove({ kind: 'piece', comp_type: type, key, direction });
                moved++;
            } catch (err) { failed.push(`${type}/${key}: ${err?.message || 'failed'}`); }
        }
        if (movePrompt) {
            try {
                await vaultMove({ kind: 'prompt', name: selected, direction });
                moved++;
            } catch (err) { failed.push(`${selected}: ${err?.message || 'failed'}`); }
        }
        const dest = goingIn ? 'the vault' : 'plaintext';
        if (failed.length) {
            ui.showToast(`Moved ${moved} to ${dest} · ${failed.length} failed — ${failed[0]}`, 'error');
        } else {
            ui.showToast(`Moved ${moved} to ${dest}`, 'success');
        }
        multiCheck = false;
        checkedSections = new Set();
        await loadAll();
        render();
    };
    layout.querySelector('#pr-vault-all-in')?.addEventListener('click', () => moveAll('in'));
    layout.querySelector('#pr-vault-all-out')?.addEventListener('click', () => moveAll('out'));

    // Multi-check mode wiring. Presets flip DOM checkboxes in place — a
    // full render() here would reset accordion scroll for a purely local
    // selection change.
    const applyPreset = (sections) => {
        checkedSections = new Set(sections);
        layout.querySelectorAll('.pr-sec-check').forEach(cb => {
            cb.checked = !cb.disabled && checkedSections.has(cb.dataset.type);
        });
        const pb = layout.querySelector('#pr-bulk-prompt');
        if (pb) pb.checked = checkedSections.has('prompt');
    };
    layout.querySelector('#pr-bulk-enter')?.addEventListener('click', () => {
        multiCheck = true;
        multiCheckFor = selected;
        // Default preset = Main: the prompt + its single sections (story-
        // specific), leaving the generic extras/emotions chips alone.
        checkedSections = new Set(['prompt',
            ...usedSections().filter(t => SINGLE_TYPES.includes(t))]);
        render();
    });
    layout.querySelector('#pr-bulk-cancel')?.addEventListener('click', () => {
        multiCheck = false;
        checkedSections = new Set();
        render();
    });
    layout.querySelector('#pr-bulk-all')?.addEventListener('click', () =>
        applyPreset(['prompt', ...usedSections()]));
    layout.querySelector('#pr-bulk-none')?.addEventListener('click', () =>
        applyPreset([]));
    layout.querySelector('#pr-bulk-main')?.addEventListener('click', () =>
        applyPreset(['prompt',
            ...usedSections().filter(t => SINGLE_TYPES.includes(t))]));
    layout.querySelector('#pr-bulk-prompt')?.addEventListener('change', e => {
        e.target.checked ? checkedSections.add('prompt')
                         : checkedSections.delete('prompt');
    });
    layout.querySelectorAll('.pr-sec-check').forEach(cb => {
        // The header's own click handler toggles the accordion — keep a
        // checkbox click from opening/closing the section.
        cb.addEventListener('click', e => e.stopPropagation());
        cb.addEventListener('change', e => {
            e.target.checked ? checkedSections.add(cb.dataset.type)
                             : checkedSections.delete(cb.dataset.type);
        });
    });

    // Monolith content
    const commitPromptReason = async () => {
        if (!selected || !selectedData) return;
        const why = liveReason('prompt');
        await savePrompt(selected, promptSaveData(selected,
            why ? { ...selectedData, reason: why } : selectedData));
    };
    layout.querySelector('#pr-content')?.addEventListener('input', e => {
        if (selectedData) {
            selectedData.content = e.target.value;
            ensureReasonRow(e.target, 'prompt', commitPromptReason);
            touchReason('prompt');
            debouncedSavePrompt();
        }
    });
    // A live reason survives re-renders — put its row back
    const monoText = layout.querySelector('#pr-content');
    if (monoText && liveReason('prompt')) {
        ensureReasonRow(monoText, 'prompt', commitPromptReason);
    }

    // --- Accordion headers ---
    layout.querySelectorAll('.pr-accordion-header').forEach(hdr => {
        hdr.addEventListener('click', () => toggleAccordion(hdr.dataset.type));
    });

    // --- Preview accordion toggle ---
    layout.querySelector('#pr-preview-toggle')?.addEventListener('click', () => {
        previewOpen = !previewOpen;
        const body = layout.querySelector('.pr-preview-body');
        const arrow = layout.querySelector('#pr-preview-toggle .accordion-arrow');
        if (body) body.style.display = previewOpen ? '' : 'none';
        if (arrow) arrow.textContent = previewOpen ? '\u25BC' : '\u25B6';
    });

    // --- Inside accordion bodies ---
    layout.querySelectorAll('.pr-accordion-body').forEach(body => {
        const type = body.closest('.pr-accordion')?.dataset.type;
        if (type) bindAccordionBodyEvents(body, type);
    });
}

// Shared accordion body event binding (used by both initial render and partial re-render)
function bindAccordionBodyEvents(body, type) {
    const isSingle = SINGLE_TYPES.includes(type);

    // Piece dropdown — single-select: saves prompt selection + shows text
    //                  multi-select: switches which definition to edit
    body.querySelector('.pr-piece-select')?.addEventListener('change', e => {
        if (isSingle && selectedData?.components) {
            selectedData.components[type] = e.target.value;
            debouncedSavePrompt();
            renderAccordionBody(type);
        } else {
            editTarget[type] = e.target.value;
            renderAccordionBody(type);
        }
    });

    // Multi-select chip toggles
    body.querySelectorAll('.pr-chip input[type="checkbox"]').forEach(cb => {
        cb.addEventListener('change', () => {
            if (!selectedData?.components) return;
            const key = cb.dataset.key;
            const current = selectedData.components[type] || [];
            if (cb.checked) {
                if (!current.includes(key)) current.push(key);
            } else {
                const idx = current.indexOf(key);
                if (idx >= 0) current.splice(idx, 1);
            }
            selectedData.components[type] = current.sort();
            cb.closest('.pr-chip').classList.toggle('active', cb.checked);
            debouncedSavePrompt();
        });
    });

    // Pencil rename — inline: replaces select with text input
    body.querySelector('.pr-rename-btn')?.addEventListener('click', e => {
        const key = e.currentTarget.dataset.key;
        if (!key) return;
        const row = body.querySelector('.pr-piece-row');
        const select = row.querySelector('.pr-piece-select');
        const pencil = e.currentTarget;

        select.hidden = true;
        pencil.hidden = true;

        const input = document.createElement('input');
        input.type = 'text';
        input.className = 'pr-piece-select';
        input.value = key;
        input.spellcheck = false;
        row.prepend(input);
        input.focus();
        input.select();

        let cancelled = false;

        input.addEventListener('keydown', ev => {
            if (ev.key === 'Enter') { ev.preventDefault(); input.blur(); }
            if (ev.key === 'Escape') { cancelled = true; input.blur(); }
        });

        input.addEventListener('blur', async () => {
            const newKey = input.value.trim();
            if (!cancelled && newKey && newKey !== key) {
                await renameDefinition(type, key, newKey);
            } else {
                input.remove();
                select.hidden = false;
                pencil.hidden = false;
            }
        }, { once: true });
    });

    // Definition text (debounced save) — first edit slides in the reason row
    const defText = body.querySelector('.pr-def-text');
    const commitPieceReason = async () => {
        const k = defText.dataset.key;
        await saveComponent(type, k, defText.value, liveReason(`${type}:${k}`),
                            pieceOrigin(type, k));
    };
    defText?.addEventListener('input', e => {
        const key = e.target.dataset.key;
        ensureReasonRow(e.target, `${type}:${key}`, commitPieceReason);
        touchReason(`${type}:${key}`);
        debouncedSaveComponent(type, key, e.target.value);
    });
    // A live reason survives re-renders — put its row back
    if (defText && liveReason(`${type}:${defText.dataset.key}`)) {
        ensureReasonRow(defText, `${type}:${defText.dataset.key}`, commitPieceReason);
    }

    // v1.1 per-piece store toggle (unlocked only; OUT gets a yes/no)
    body.querySelector('.pr-piece-vault')?.addEventListener('change', async e => {
        const key = e.target.dataset.key;
        const goingIn = e.target.checked;
        if (!goingIn && !confirm(
            `Move piece "${type}/${key}" OUT of the vault?\n\n` +
            `Its text will be written to the regular store as plaintext on disk.`)) {
            e.target.checked = true;
            return;
        }
        try {
            const { vaultMove } = await import('../shared/vault-api.js');
            quietEcho();   // the SSE echo of THIS move is ours — skip it
            const res = await vaultMove({ kind: 'piece', comp_type: type, key,
                                          direction: goingIn ? 'in' : 'out' });
            ui.showToast(res?.message || 'Moved', 'success');
        } catch (err) {
            ui.showToast(err?.message || 'Move failed', 'error');
            e.target.checked = !goingIn;
            return;
        }
        // Surgical: the response is the truth for this one piece — badge it
        // locally and rebuild only its accordion (the 🗝 lives in the header
        // value, the option list, and the chip labels — all inside it).
        // Before: loadAll() + full render(), then the echo did it twice more.
        if (!vaultPieces[type]) vaultPieces[type] = new Set();
        if (goingIn) vaultPieces[type].add(key); else vaultPieces[type].delete(key);
        renderAccordionBody(type);
    });

    // Action buttons
    body.querySelectorAll('[data-action]').forEach(btn => {
        btn.addEventListener('click', () => {
            const action = btn.dataset.action;
            const key = btn.dataset.key;
            if (action === 'new-def') newDefinition(type);
            else if (action === 'dup-def') duplicateDefinition(type, key);
            else if (action === 'del-def') deleteDefinition(type, key);
        });
    });
}

// Open/close without a full render() — rebuilding the whole view for a purely
// local toggle resets the left roster's scroll and DOM state.
function toggleAccordion(type) {
    const prev = openAccordion;
    openAccordion = openAccordion === type ? null : type;
    renderAccordionBody(type);
    if (prev && prev !== type) renderAccordionBody(prev);
}

// Re-render just one accordion without full page re-render
function renderAccordionBody(type) {
    const acc = container.querySelector(`.pr-accordion[data-type="${type}"]`);
    if (!acc) return;
    const comps = selectedData?.components || {};

    const isMulti = MULTI_TYPES.includes(type);
    const html = isMulti ? renderMultiAccordion(type, comps) : renderSingleAccordion(type, comps);

    const temp = document.createElement('div');
    temp.innerHTML = html;
    const newAcc = temp.firstElementChild;
    acc.replaceWith(newAcc);

    // Re-bind events
    newAcc.querySelector('.pr-accordion-header')?.addEventListener('click', () => toggleAccordion(type));
    const body = newAcc.querySelector('.pr-accordion-body');
    if (body) bindAccordionBodyEvents(body, type);
}

// ── Prompt CRUD ──
function createPrompt() {
    const modal = document.createElement('div');
    modal.className = 'pr-modal-overlay';
    modal.innerHTML = `
        <div class="pr-modal" style="max-width:360px">
            <div class="pr-modal-header">
                <h3>New Prompt</h3>
                <button class="btn-icon" id="pr-new-close">\u2715</button>
            </div>
            <div class="pr-modal-body">
                <input type="text" id="pr-new-name" class="input" placeholder="Prompt name" autofocus style="width:100%;margin-bottom:12px">
                <div style="display:flex;gap:8px">
                    <button class="btn-primary" id="pr-new-assembled" style="flex:1">Assembled</button>
                    <button class="btn-primary" id="pr-new-monolith" style="flex:1">Monolith</button>
                </div>
                <p class="text-muted" style="font-size:var(--font-xs);margin-top:8px">Assembled = built from component pieces. Monolith = single free-text block.</p>
                ${vaultState.unlocked ? '<p style="font-size:var(--font-xs);margin-top:6px;color:#f59e0b">\u{1F5DD} Vault is unlocked — this prompt will be saved into the vault.</p>' : ''}
            </div>
        </div>
    `;
    document.body.appendChild(modal);
    const close = () => modal.remove();
    setupModalClose(modal, close);
    modal.querySelector('#pr-new-close').addEventListener('click', close);
    modal.querySelector('#pr-new-name').addEventListener('keydown', e => {
        if (e.key === 'Escape') close();
        if (e.key === 'Enter') create('assembled');
    });

    async function create(type) {
        const name = modal.querySelector('#pr-new-name').value.trim();
        if (!name) { modal.querySelector('#pr-new-name').focus(); return; }
        // A new assembled prompt gets FRESH story-specific pieces named after
        // it (character/location/scenario), so you land editing YOUR prompt —
        // not a body full of 'sapphire' and 'default'. Same-named existing
        // pieces are referenced, never overwritten. Generic sections stay
        // 'default'; extras/emotions start empty.
        const data = type === 'monolith'
            ? { type: 'monolith', content: '', privacy_required: false }
            : { type: 'assembled', components: { character: name, location: name, goals: 'default', relationship: 'default', format: 'default', scenario: name, extras: [], emotions: [] }, privacy_required: false };
        try {
            if (type === 'assembled') {
                for (const t of ['character', 'location', 'scenario']) {
                    if (!components[t]?.[name]) {
                        await saveComponent(t, name, '', `piece for new prompt "${name}"`);
                    }
                }
            }
            await savePrompt(name, data);
            selected = name;
            openAccordion = type === 'assembled' ? 'character' : null;
            editTarget = {};
            await loadAll();
            render();
            ui.showToast(`Created: ${name}`, 'success');
        } catch (e) { ui.showToast(e.message || 'Failed', 'error'); }
        close();
    }

    modal.querySelector('#pr-new-assembled').addEventListener('click', () => create('assembled'));
    modal.querySelector('#pr-new-monolith').addEventListener('click', () => create('monolith'));
}

async function duplicatePrompt() {
    if (!selected || !selectedData) return;
    const name = prompt(`Duplicate "${selected}" as:`, selected + '-copy');
    if (!name?.trim() || name.trim() === selected) return;
    try {
        const data = { ...selectedData };
        delete data.name;
        await savePrompt(name.trim(), data);
        selected = name.trim();
        openAccordion = null;
        editTarget = {};
        await loadAll();
        render();
        ui.showToast(`Duplicated as: ${name.trim()}`, 'success');
    } catch (e) { ui.showToast(e.message || 'Failed', 'error'); }
}

async function activateCurrentPrompt() {
    try {
        await loadPrompt(selected);
        activePromptName = selected;
        ui.showToast(`Activated: ${selected}`, 'success');
        updateScene();
        render();
    } catch (e) {
        ui.showToast(e.message || 'Failed', 'error');
    }
}

async function afterPromptDelete() {
    rosterCheck = false;
    checkedPrompts = new Set();
    lastToggledPrompt = null;
    lastShiftRange = null;
    selected = null;
    selectedData = null;
    openAccordion = null;
    editTarget = {};
    await loadAll();
    render();
    updateScene();
}

// Proper delete (session-1 build): the modal lists the prompt's pieces with
// usage badges — sole-use pieces pre-checked, shared ones not — and checked
// pieces move to the restorable trash alongside the record delete.
async function deleteCurrentPrompt() {
    if (!selected) return;
    openDeleteModal({ names: [selected], componentSources, vaultPieces, stockPieces,
                      details: promptDetails, onDone: afterPromptDelete });
}

function bulkDeletePrompts() {
    if (!checkedPrompts.size) return;
    openDeleteModal({ names: [...checkedPrompts], componentSources, vaultPieces, stockPieces,
                      details: promptDetails, onDone: afterPromptDelete });
}

// ── Definition CRUD ──
async function newDefinition(type) {
    const name = prompt(`New ${type} name:`);
    if (!name?.trim()) return;
    try {
        const res = await saveComponent(type, name.trim(), '');
        noteVaultRouted(type, name.trim(), res);
        if (!components[type]) components[type] = {};
        components[type][name.trim()] = '';

        // Single-select: switch prompt to use the new piece
        if (SINGLE_TYPES.includes(type) && selectedData?.components) {
            selectedData.components[type] = name.trim();
            debouncedSavePrompt();
        } else {
            editTarget[type] = name.trim();
        }

        renderAccordionBody(type);
        ui.showToast(`Created: ${name.trim()}`, 'success');
    } catch (e) { ui.showToast('Failed', 'error'); }
}

async function duplicateDefinition(type, key) {
    const defs = components[type] || {};
    const text = defs[key] || '';
    const newName = prompt(`Duplicate "${key}" as:`, key + '-copy');
    if (!newName?.trim() || newName.trim() === key) return;
    try {
        const res = await saveComponent(type, newName.trim(), text);
        noteVaultRouted(type, newName.trim(), res);
        if (!components[type]) components[type] = {};
        components[type][newName.trim()] = text;

        // Single-select: switch prompt to use the copy
        if (SINGLE_TYPES.includes(type) && selectedData?.components) {
            selectedData.components[type] = newName.trim();
            debouncedSavePrompt();
        } else {
            editTarget[type] = newName.trim();
        }

        renderAccordionBody(type);
        ui.showToast(`Duplicated as: ${newName.trim()}`, 'success');
    } catch (e) { ui.showToast('Failed', 'error'); }
}

async function deleteDefinition(type, key) {
    if (!confirm(`Delete "${key}" from ${type}?`)) return;
    try {
        await deleteComponent(type, key);
        delete components[type][key];
        vaultPieces[type]?.delete(key);

        // If prompt was using this definition, clear it
        if (selectedData?.components) {
            if (MULTI_TYPES.includes(type)) {
                const arr = selectedData.components[type] || [];
                const idx = arr.indexOf(key);
                if (idx >= 0) { arr.splice(idx, 1); await savePrompt(selected, selectedData); }
            } else {
                if (selectedData.components[type] === key) {
                    selectedData.components[type] = '';
                    await savePrompt(selected, selectedData);
                }
            }
        }

        // Move edit target
        const remaining = Object.keys(components[type] || {});
        editTarget[type] = remaining[0] || '';
        renderAccordionBody(type);
        refreshPreview();
        ui.showToast('Deleted', 'success');
    } catch (e) { ui.showToast('Failed', 'error'); }
}

// Safe rename (2026-08-19): ONE server call moves the piece in its own
// store and repoints EVERY reference — user presets, unlocked-vault
// presets, and the assembled state. The old client-side dance (save-new +
// delete-old + fix only the SELECTED prompt) was a dangler factory: every
// other prompt using the piece kept the dead name.
async function renameDefinition(type, oldKey, newKey) {
    const defs = components[type] || {};
    if (defs[newKey]) {
        ui.showToast(`"${newKey}" already exists`, 'error');
        return;
    }
    try {
        const res = await renamePiece(type, oldKey, newKey);
        // Local mirrors stay coherent until the reload lands
        components[type][newKey] = defs[oldKey] || '';
        delete components[type][oldKey];
        if (vaultPieces[type]?.has(oldKey)) {
            vaultPieces[type].delete(oldKey);
            vaultPieces[type].add(newKey);
        }
        editTarget[type] = newKey;
        ui.showToast(res?.message || `Renamed to: ${newKey}`, 'success');
        await loadAll();
        render();
        refreshPreview();
    } catch (e) { ui.showToast(e?.message || 'Rename failed', 'error'); }
}

// ── Auto-save ──
function debouncedSavePrompt() {
    clearTimeout(saveTimer);
    promptSaveInFlight = true;
    saveTimer = setTimeout(async () => {
        if (!selected || !selectedData) { promptSaveInFlight = false; return; }
        try {
            const why = liveReason('prompt');
            await savePrompt(selected, promptSaveData(selected,
                why ? { ...selectedData, reason: why } : selectedData));
            if (selected === activePromptName) await loadPrompt(selected);
            updateScene();
            refreshPreview();
        } catch (e) {
            ui.showToast('Save failed', 'error');
        } finally {
            promptSaveInFlight = false;
            // Catch up the refresh this save deferred (guard above).
            if (pendingRefresh && viewVisible && !_editableFocused()) doBusRefresh();
        }
    }, 600);
}

function debouncedSaveComponent(type, key, value) {
    const timerId = `${type}:${key}`;
    clearTimeout(compSaveTimers[timerId]);
    compSaveTimers[timerId] = setTimeout(async () => {
        try {
            // pieceOrigin: THE stale-editor site (finding 6) — this debounced
            // save fires after an idle-lock too; the stamp lets the server
            // 409 it instead of writing vault text to the plaintext store.
            await saveComponent(type, key, value, liveReason(timerId), pieceOrigin(type, key));
            if (components[type]) components[type][key] = value;
            // If this component is used by the current prompt, refresh preview
            if (selectedData?.components) {
                const sel = selectedData.components[type];
                const isUsed = Array.isArray(sel) ? sel.includes(key) : sel === key;
                if (isUsed && selected === activePromptName) {
                    await loadPrompt(selected);
                }
                if (isUsed) refreshPreview();
            }
        } catch (e) {
            ui.showToast('Save failed', 'error');
        }
    }, 600);
}

async function refreshPreview() {
    if (!selected) return;
    try {
        // Bind the name: a prompt switch within one RTT used to land the OLD
        // prompt's content in the NEW selectedData, and the next debounced
        // save persisted it under the new name (E3#5, 2026-09-08).
        const name = selected;
        const fresh = await getPrompt(name);
        if (fresh && name === selected) {
            // Backend /api/prompts/{name} returns the (re-)assembled text in
            // `.content` — there's no `.compiled` field. Previously this wrote
            // to selectedData.compiled which was always undefined, and
            // renderPreview fell back to the stale selectedData.content from
            // initial load — the preview never updated after a piece edit.
            // TODO L133 — 2026-04-21.
            // ...but never over a live edit (DOM-refresh hunt 2026-09-08): the
            // #pr-content input handler writes selectedData.content on every
            // keystroke, this GET lands one RTT after the save, and chars typed
            // meanwhile were clobbered here, then persisted by the next debounce.
            const mono = container?.querySelector('#pr-content');
            if (!promptSaveInFlight && !(mono && document.activeElement === mono)) {
                selectedData.content = fresh.content;
            }
            selectedData.char_count = fresh.char_count;
        }
    } catch { /* ignore */ }

    const previewEl = container?.querySelector('.pr-preview');
    if (previewEl) {
        previewEl.innerHTML = renderPreview();
        previewEl.querySelector('#pr-preview-toggle')?.addEventListener('click', () => {
            previewOpen = !previewOpen;
            const body = previewEl.querySelector('.pr-preview-body');
            const arrow = previewEl.querySelector('#pr-preview-toggle .accordion-arrow');
            if (body) body.style.display = previewOpen ? '' : 'none';
            if (arrow) arrow.textContent = previewOpen ? '\u25BC' : '\u25B6';
        });
    }

    // Update char count in header subtitle
    const subtitle = container?.querySelector('.pr-header .view-subtitle');
    if (subtitle && selectedData) {
        const isMonolith = selectedData.type === 'monolith';
        subtitle.textContent = `${isMonolith ? 'Monolith' : 'Assembled'}${selectedData.char_count ? ' \u00B7 ' + formatCount(selectedData.char_count) + ' chars' : ''}`;
    }
}

// ── Import / Export (modal) ──
function getUsedPieces() {
    if (!selectedData?.components) return {};
    const used = {};
    // Key membership, not text truthiness (same class as the ⚠ badge fix,
    // 2026-08-19): empty-text pieces are real pieces — excluding them here
    // hid them from the export gates and the delete modal's piece list.
    for (const type of SINGLE_TYPES) {
        const key = selectedData.components[type];
        if (key && (key in (components[type] || {}))) {
            used[type] = { [key]: components[type][key] };
        }
    }
    for (const type of MULTI_TYPES) {
        const keys = selectedData.components[type] || [];
        for (const key of keys) {
            if (key in (components[type] || {})) {
                if (!used[type]) used[type] = {};
                used[type][key] = components[type][key];
            }
        }
    }
    return used;
}

// Old openImportExport() removed — replaced by shared import-export.js module
// Export/Import handlers are now in bindEvents() using showExportDialog/showImportDialog

// ── Helpers ──
function formatCount(n) { return n >= 1000 ? (n / 1000).toFixed(1) + 'k' : n; }
function cap(s) { return s.charAt(0).toUpperCase() + s.slice(1); }

function esc(str) {
    const d = document.createElement('div');
    d.textContent = str;
    return d.innerHTML;
}

function escAttr(str) {
    return str.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}
