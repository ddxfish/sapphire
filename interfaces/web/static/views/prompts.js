// views/prompts.js - Prompt editor view (accordion-based inline editing)
import { listPrompts, getPrompt, getComponents, savePrompt, deletePrompt,
         saveComponent, deleteComponent, loadPrompt } from '../shared/prompt-api.js';
import { renderPersonaTabs, bindPersonaTabs } from '../shared/persona-tabs.js';
import * as ui from '../ui.js';
import { updateScene } from '../features/scene.js';

// ── State ──
let container = null;
let prompts = [];
let components = {};
let promptDetails = {};     // { name: { char_count, components, type, ... } }
let selected = null;
let selectedData = null;
let activePromptName = null;
let openAccordion = null;
let editTarget = {};        // { type: key } per-type editing target
let saveTimer = null;
let compSaveTimers = {};

const SINGLE_TYPES = ['character', 'location', 'goals', 'relationship', 'format', 'scenario'];
const MULTI_TYPES  = ['extras', 'emotions'];
const ALL_TYPES    = [...SINGLE_TYPES, ...MULTI_TYPES];
const ICONS = {
    character: '\u{1F464}', location: '\u{1F3E0}', goals: '\u{1F3AF}', relationship: '\u{1F49C}',
    format: '\u{1F4DD}', scenario: '\u{1F30D}', extras: '\u{1F9E9}', emotions: '\u{2728}'
};

export default {
    init(el) { container = el; },
    async show() {
        if (window._viewSelect) { selected = window._viewSelect; delete window._viewSelect; }
        await loadAll(); render();
    },
    hide() {}
};

// ── Data ──
async function loadAll() {
    try {
        const [pList, comps] = await Promise.all([listPrompts(), getComponents()]);
        prompts = pList || [];
        components = comps || {};

        const active = prompts.find(p => p.active);
        activePromptName = active?.name || null;

        if (!selected && activePromptName) selected = activePromptName;
        else if (!selected && prompts.length > 0) selected = prompts[0].name;

        // Fetch details for all prompts in parallel (for sidebar meta)
        const results = await Promise.allSettled(prompts.map(p => getPrompt(p.name)));
        results.forEach((r, i) => {
            if (r.status === 'fulfilled' && r.value) {
                promptDetails[prompts[i].name] = r.value;
            }
        });

        // Use already-fetched data for selected prompt
        if (selected && promptDetails[selected]) {
            selectedData = promptDetails[selected];
        } else if (selected) {
            try { selectedData = await getPrompt(selected); } catch { selectedData = null; }
        }
    } catch (e) {
        console.warn('Prompts load failed:', e);
    }
}

// ── Main Render ──
function render() {
    if (!container) return;

    container.innerHTML = `
        ${renderPersonaTabs('prompts')}
        <div class="prompts-layout">
            <div class="pr-content">
                <div class="pr-editor">
                    ${selected ? renderEditor() : '<div class="view-placeholder"><p>Select a prompt</p></div>'}
                </div>
                <div class="pr-preview">
                    ${selected ? renderPreview() : ''}
                </div>
            </div>
            <div class="pr-roster">
                ${renderRoster()}
            </div>
        </div>
    `;
    bindEvents();
}

function renderRoster() {
    return `
        <div class="panel-list-header">
            <span class="panel-list-title">Prompts</span>
            <button class="btn-sm" id="pr-new" title="New prompt">+</button>
        </div>
        <div class="panel-list-items" id="pr-list">
            ${prompts.map(p => {
                const d = promptDetails[p.name];
                const tokens = d?.token_count || p.token_count;
                const tokenStr = tokens ? formatCount(tokens) + ' tokens' : '';
                const typeName = p.type === 'monolith' ? 'Monolith' : 'Assembled';
                const character = d?.components?.character;
                const meta = [typeName, character ? '\u{1F464} ' + character : ''].filter(Boolean).join(' \u00B7 ');
                const isActive = p.name === activePromptName;
                return `
                    <button class="panel-list-item${p.name === selected ? ' selected' : ''}${isActive ? ' active-prompt' : ''}" data-name="${p.name}">
                        <div class="pr-item-info">
                            <span class="pr-item-name">${p.privacy_required ? '\u{1F512} ' : ''}${p.name}${isActive ? ' (Active)' : ''}</span>
                            ${tokenStr ? `<span class="pr-item-tokens">${tokenStr}</span>` : ''}
                            <span class="pr-item-meta">${meta}</span>
                        </div>
                    </button>
                `;
            }).join('')}
        </div>
    `;
}

function renderEditor() {
    if (!selectedData) return '<div class="view-placeholder"><p>Loading...</p></div>';
    const p = selectedData;
    const isActive = selected === activePromptName;
    const isMonolith = p.type === 'monolith';

    return `
        <div class="pr-header">
            <div class="pr-header-left">
                <h2>${p.privacy_required ? '\u{1F512} ' : ''}${selected}</h2>
                <span class="view-subtitle">${isMonolith ? 'Monolith' : 'Assembled'}${p.char_count ? ' \u00B7 ' + formatCount(p.char_count) + ' chars' : ''}</span>
            </div>
            <div class="pr-header-actions">
                ${!isActive ? '<button class="btn-primary" id="pr-activate">Activate</button>' : '<span class="badge badge-active">Active</span>'}
                <button class="btn-sm" id="pr-io" title="Import / Export">\u21C4</button>
                <button class="btn-sm danger" id="pr-delete" title="Delete prompt">\u2715</button>
            </div>
        </div>
        <div class="pr-body">
            ${isMonolith ? renderMonolith(p) : renderAssembled(p)}
            <div class="pr-privacy">
                <label><input type="checkbox" id="pr-privacy" ${p.privacy_required ? 'checked' : ''}> Private only (requires Privacy Mode)</label>
            </div>
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
                <span class="pr-acc-icon">${ICONS[type]}</span>
                <div class="pr-acc-text">
                    <span class="pr-acc-label">${cap(type)}</span>
                    <span class="pr-acc-value">${current || 'none'}</span>
                </div>
                <span class="pr-acc-arrow">${isOpen ? '\u25BE' : '\u25B8'}</span>
            </div>
            ${isOpen ? `
                <div class="pr-accordion-body">
                    <div class="pr-piece-row">
                        <select class="pr-piece-select" data-type="${type}">
                            <option value="">None</option>
                            ${keys.map(k => `<option value="${k}"${k === current ? ' selected' : ''}>${k}</option>`).join('')}
                        </select>
                        ${current ? `<button class="btn-icon pr-rename-btn" data-type="${type}" data-key="${current}" title="Rename">\u270F</button>` : ''}
                    </div>
                    ${current ? `
                        <textarea class="pr-def-text" data-type="${type}" data-key="${current}" rows="4" placeholder="Definition text...">${esc(currentText)}</textarea>
                        <div class="pr-def-actions">
                            <button class="btn-sm" data-action="new-def" data-type="${type}">+ New</button>
                            <button class="btn-sm" data-action="dup-def" data-type="${type}" data-key="${current}">Duplicate</button>
                            <button class="btn-sm danger" data-action="del-def" data-type="${type}" data-key="${current}">Delete</button>
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
    const headerValue = current.length ? current.slice().sort().join(', ') : 'none';

    return `
        <div class="pr-accordion${isOpen ? ' open' : ''}" data-type="${type}">
            <div class="pr-accordion-header" data-type="${type}">
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
                            <label class="pr-chip${current.includes(k) ? ' active' : ''}" title="${escAttr(defs[k] || '')}">
                                <input type="checkbox" data-type="${type}" data-key="${k}" ${current.includes(k) ? 'checked' : ''}>
                                <span>${k}</span>
                            </label>
                        `).join('')}
                    </div>
                    ${keys.length ? `
                        <div class="pr-piece-row">
                            <select class="pr-piece-select" data-type="${type}">
                                ${keys.map(k => `<option value="${k}"${k === target ? ' selected' : ''}>${k}</option>`).join('')}
                            </select>
                            <button class="btn-icon pr-rename-btn" data-type="${type}" data-key="${target}" title="Rename">\u270F</button>
                        </div>
                        <textarea class="pr-def-text" data-type="${type}" data-key="${target}" rows="3" placeholder="Definition text...">${esc(targetText)}</textarea>
                        <div class="pr-def-actions">
                            <button class="btn-sm" data-action="new-def" data-type="${type}">+ New</button>
                            <button class="btn-sm" data-action="dup-def" data-type="${type}" data-key="${target}">Duplicate</button>
                            <button class="btn-sm danger" data-action="del-def" data-type="${type}" data-key="${target}">Delete</button>
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
        <div class="pr-preview-header">
            <h3>Preview: ${esc(selected)}</h3>
            <span class="view-subtitle">${formatCount(text.length)} chars</span>
        </div>
        <div class="pr-preview-body">
            <pre class="pr-preview-text">${esc(text)}</pre>
        </div>
    `;
}

// ── Events ──
function bindEvents() {
    if (!container) return;
    bindPersonaTabs(container);
    const layout = container.querySelector('.prompts-layout');
    if (!layout) return;

    // --- Roster ---
    layout.querySelector('#pr-list')?.addEventListener('click', async e => {
        const item = e.target.closest('.panel-list-item');
        if (!item) return;
        selected = item.dataset.name;
        openAccordion = null;
        editTarget = {};
        try { selectedData = await getPrompt(selected); } catch { selectedData = null; }
        render();
    });

    // New prompt
    layout.querySelector('#pr-new')?.addEventListener('click', createPrompt);

    // --- Header actions ---
    layout.querySelector('#pr-activate')?.addEventListener('click', activateCurrentPrompt);
    layout.querySelector('#pr-delete')?.addEventListener('click', deleteCurrentPrompt);
    layout.querySelector('#pr-io')?.addEventListener('click', () => openImportExport());

    // Privacy
    layout.querySelector('#pr-privacy')?.addEventListener('change', e => {
        if (selectedData) {
            selectedData.privacy_required = e.target.checked;
            debouncedSavePrompt();
        }
    });

    // Monolith content
    layout.querySelector('#pr-content')?.addEventListener('input', e => {
        if (selectedData) {
            selectedData.content = e.target.value;
            debouncedSavePrompt();
        }
    });

    // --- Accordion headers ---
    layout.querySelectorAll('.pr-accordion-header').forEach(hdr => {
        hdr.addEventListener('click', () => {
            const type = hdr.dataset.type;
            openAccordion = openAccordion === type ? null : type;
            render();
        });
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
            selectedData.components[type] = current;
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

    // Definition text (debounced save)
    body.querySelector('.pr-def-text')?.addEventListener('input', e => {
        const key = e.target.dataset.key;
        debouncedSaveComponent(type, key, e.target.value);
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
    newAcc.querySelector('.pr-accordion-header')?.addEventListener('click', () => {
        openAccordion = openAccordion === type ? null : type;
        render();
    });
    const body = newAcc.querySelector('.pr-accordion-body');
    if (body) bindAccordionBodyEvents(body, type);
}

// ── Prompt CRUD ──
async function createPrompt() {
    const name = prompt('New prompt name:');
    if (!name?.trim()) return;
    const type = confirm('Create as Assembled prompt?\n\nOK = Assembled (components)\nCancel = Monolith (free text)') ? 'assembled' : 'monolith';

    const data = type === 'monolith'
        ? { type: 'monolith', content: '', privacy_required: false }
        : { type: 'assembled', components: { character: 'sapphire', location: 'default', goals: 'default', relationship: 'default', format: 'default', scenario: 'default', extras: [], emotions: [] }, privacy_required: false };

    try {
        await savePrompt(name.trim(), data);
        selected = name.trim();
        openAccordion = null;
        editTarget = {};
        await loadAll();
        render();
        ui.showToast(`Created: ${name.trim()}`, 'success');
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
        ui.showToast(e.privacyRequired ? 'Privacy Mode required' : (e.message || 'Failed'), 'error');
    }
}

async function deleteCurrentPrompt() {
    if (!confirm(`Delete "${selected}"?`)) return;
    try {
        await deletePrompt(selected);
        selected = null;
        selectedData = null;
        openAccordion = null;
        editTarget = {};
        await loadAll();
        render();
        updateScene();
        ui.showToast('Deleted', 'success');
    } catch (e) { ui.showToast(e.message || 'Failed', 'error'); }
}

// ── Definition CRUD ──
async function newDefinition(type) {
    const name = prompt(`New ${type} name:`);
    if (!name?.trim()) return;
    try {
        await saveComponent(type, name.trim(), '');
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
        await saveComponent(type, newName.trim(), text);
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

async function renameDefinition(type, oldKey, newKey) {
    const defs = components[type] || {};
    if (defs[newKey]) {
        ui.showToast(`"${newKey}" already exists`, 'error');
        return;
    }
    try {
        const text = defs[oldKey] || '';
        await saveComponent(type, newKey, text);
        await deleteComponent(type, oldKey);

        // Update local state
        components[type][newKey] = text;
        delete components[type][oldKey];

        // Update prompt reference
        if (selectedData?.components) {
            if (MULTI_TYPES.includes(type)) {
                const arr = selectedData.components[type] || [];
                const idx = arr.indexOf(oldKey);
                if (idx >= 0) { arr[idx] = newKey; await savePrompt(selected, selectedData); }
            } else {
                if (selectedData.components[type] === oldKey) {
                    selectedData.components[type] = newKey;
                    await savePrompt(selected, selectedData);
                }
            }
        }

        editTarget[type] = newKey;
        renderAccordionBody(type);
        refreshPreview();
        ui.showToast(`Renamed to: ${newKey}`, 'success');
    } catch (e) { ui.showToast('Rename failed', 'error'); }
}

// ── Auto-save ──
function debouncedSavePrompt() {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(async () => {
        if (!selected || !selectedData) return;
        try {
            await savePrompt(selected, selectedData);
            if (selected === activePromptName) await loadPrompt(selected);
            updateScene();
            refreshPreview();
        } catch (e) {
            ui.showToast('Save failed', 'error');
        }
    }, 600);
}

function debouncedSaveComponent(type, key, value) {
    const timerId = `${type}:${key}`;
    clearTimeout(compSaveTimers[timerId]);
    compSaveTimers[timerId] = setTimeout(async () => {
        try {
            await saveComponent(type, key, value);
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
        const fresh = await getPrompt(selected);
        if (fresh) {
            selectedData.compiled = fresh.compiled;
            selectedData.char_count = fresh.char_count;
        }
    } catch { /* ignore */ }

    const previewEl = container?.querySelector('.pr-preview');
    if (previewEl) previewEl.innerHTML = renderPreview();

    // Update char count in header subtitle
    const subtitle = container?.querySelector('.pr-header .view-subtitle');
    if (subtitle && selectedData) {
        const isMonolith = selectedData.type === 'monolith';
        subtitle.textContent = `${isMonolith ? 'Monolith' : 'Assembled'}${selectedData.char_count ? ' \u00B7 ' + formatCount(selectedData.char_count) + ' chars' : ''}`;
    }
}

// ── Import / Export (modal) ──
function openImportExport() {
    const modal = document.createElement('div');
    modal.className = 'pr-modal-overlay';
    modal.innerHTML = `
        <div class="pr-modal">
            <div class="pr-modal-header">
                <h3>Import / Export: ${selected}</h3>
                <button class="btn-icon" id="pr-io-close">\u2715</button>
            </div>
            <div class="pr-modal-body">
                <div class="pr-io-section">
                    <h4>Export</h4>
                    <label class="pr-io-option">
                        <input type="checkbox" id="io-export-pieces" checked> Include prompt pieces (component definitions)
                    </label>
                    <div class="pr-io-buttons">
                        <button class="btn-sm" id="io-export-clip">Copy to Clipboard</button>
                        <button class="btn-sm" id="io-export-file">Download File</button>
                    </div>
                </div>
                <hr class="pr-io-divider">
                <div class="pr-io-section">
                    <h4>Import</h4>
                    <label class="pr-io-option">
                        <input type="checkbox" id="io-import-pieces"> Overwrite prompt pieces with imported definitions
                    </label>
                    <div class="pr-io-buttons">
                        <button class="btn-sm" id="io-import-clip">Paste from Clipboard</button>
                        <button class="btn-sm" id="io-import-file">Upload File</button>
                        <input type="file" id="io-file-input" accept=".json" style="display:none">
                    </div>
                    <div id="io-status" class="pr-io-status"></div>
                </div>
            </div>
        </div>
    `;

    document.body.appendChild(modal);
    const close = () => modal.remove();
    modal.addEventListener('click', e => { if (e.target === modal) close(); });
    modal.querySelector('#pr-io-close').addEventListener('click', close);

    // Export
    async function buildExport() {
        const bundle = { name: selected, prompt: selectedData };
        if (modal.querySelector('#io-export-pieces').checked) bundle.components = components;
        return bundle;
    }

    modal.querySelector('#io-export-clip').addEventListener('click', async () => {
        try {
            const data = await buildExport();
            await navigator.clipboard.writeText(JSON.stringify(data, null, 2));
            ui.showToast('Copied to clipboard', 'success');
        } catch { ui.showToast('Copy failed', 'error'); }
    });

    modal.querySelector('#io-export-file').addEventListener('click', async () => {
        const data = await buildExport();
        const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url; a.download = `${selected}.prompt.json`; a.click();
        URL.revokeObjectURL(url);
        ui.showToast('Downloaded', 'success');
    });

    // Import
    async function doImport(json) {
        const status = modal.querySelector('#io-status');
        try {
            const data = JSON.parse(json);
            if (!data.prompt) { status.textContent = 'Invalid format: missing prompt data'; return; }
            const name = data.name || selected;
            status.textContent = `Importing "${name}"...`;
            await savePrompt(name, data.prompt);
            const importPieces = data.components || data.pieces;
            if (modal.querySelector('#io-import-pieces').checked && importPieces) {
                for (const [type, defs] of Object.entries(importPieces)) {
                    for (const [key, value] of Object.entries(defs)) {
                        await saveComponent(type, key, value);
                    }
                }
            }
            if (name === activePromptName) await loadPrompt(name);
            selected = name;
            await loadAll();
            render();
            updateScene();
            close();
            ui.showToast(`Imported: ${name}`, 'success');
        } catch (e) { status.textContent = `Error: ${e.message}`; }
    }

    modal.querySelector('#io-import-clip').addEventListener('click', async () => {
        try {
            const text = await navigator.clipboard.readText();
            await doImport(text);
        } catch { modal.querySelector('#io-status').textContent = 'Clipboard read failed (check permissions)'; }
    });

    modal.querySelector('#io-import-file').addEventListener('click', () => modal.querySelector('#io-file-input').click());
    modal.querySelector('#io-file-input').addEventListener('change', e => {
        const file = e.target.files[0];
        if (!file) return;
        const reader = new FileReader();
        reader.onload = () => doImport(reader.result);
        reader.readAsText(file);
    });
}

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
