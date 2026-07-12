// views/palace/entities.js - Mind › Entities, palace edition (L2: people /
// places / things / events). Entity cards → detail modal with tiered chunks,
// the mention graph ("woven into N memories"), human-editable kind, and
// per-kind template fields (GET templates — defaults + user-defined kinds).
import { renderSectionHeader, bindSectionHeader } from '../../shared/section-header.js';
import { helpPills } from '../../features/video-link.js';
import { renderScopeSidebar, bindScopeSidebar } from '../../shared/scope-sidebar.js';
import { listScopes } from '../../shared/scope-api.js';
import { escHtml, escAttr, timeAgo, scopeForChatTab, subscribeMindDomain } from '../../shared/mind-common.js';
import { setupModalClose } from '../../shared/modal.js';
import * as ui from '../../ui.js';
import { PALACE_TABS, refreshPalaceTabs, SCOPE_ENDPOINT, palaceGet, palaceSend, labelChip, keyPill, metaPanel, describeScopeForDelete, transferButtons, bindTransfer } from './common.js';

const SCOPE_KEY = 'memory_scope';
const DOMAIN = 'people';
const TIER_NAMES = { 1: 'Headline', 2: 'Facts', 3: 'Trivia' };

let container = null;
let scope = 'default';
let scopes = [];
let unsub = null;
let _kindFilter = '';   // '' = all
let TPL = {};           // kind -> template
let TPL_LIST = [];      // ordered templates

export default {
    init(el) { container = el; },
    async show() {
        await refreshPalaceTabs();
        if (!unsub) unsub = subscribeMindDomain(DOMAIN, () => scope, () => container?.offsetParent !== null, renderEntities);
        if (window._mindScope) { scope = window._mindScope; delete window._mindScope; }
        else { const s = await scopeForChatTab(SCOPE_KEY); if (s) scope = s; }
        delete window._mindTab;
        scopes = await listScopes(SCOPE_ENDPOINT);
        try {
            const td = await palaceGet('templates');
            TPL_LIST = td.templates || [];
            TPL = Object.fromEntries(TPL_LIST.map(t => [t.kind, t]));
        } catch (e) { TPL_LIST = []; TPL = {}; }
        render();
    },
    hide() { if (unsub) { unsub(); unsub = null; } }
};

function content() { return container?.querySelector('#pal-ent-content'); }

function render() {
    if (!container) return;
    container.innerHTML = `
        ${renderSectionHeader({ tabs: PALACE_TABS, active: 'people', help: helpPills('Entities', { doc: 'MEMORY.md', inline: true }), status: '\u{1F3DB}️ Mind Palace — the people, places, and things she knows. Mentions in new memories weave entities into the graph automatically.' })}
        <div class="two-panel">
            ${renderScopeSidebar(scopes, scope)}
            <div class="panel-right">
                <div class="view-body view-scroll" id="pal-ent-content"></div>
            </div>
        </div>`;
    bindSectionHeader(container);
    bindScopeSidebar(container, {
        describeScope: describeScopeForDelete,
        onScopeChange: (s) => { scope = s; _kindFilter = ''; render(); },
        onChanged: async (s) => { scope = s || 'default'; _kindFilter = ''; scopes = await listScopes(SCOPE_ENDPOINT); render(); },
    });
    renderEntities();
}

function kindChip(kind) {
    if (!kind) return '<span class="palace-kind palace-kind-none">unsorted</span>';
    const t = TPL[kind];
    return `<span class="palace-kind palace-kind-${escHtml(kind)}">${t?.icon || ''} ${escHtml(t?.label || kind)}</span>`;
}

async function renderEntities() {
    const el = content();
    if (!el) return;
    let data;
    try {
        data = await palaceGet(`entities?scope=${encodeURIComponent(scope)}`);
    } catch (e) {
        el.innerHTML = `<div class="mind-empty">Failed to load: ${escHtml(e.message)}</div>`;
        return;
    }
    const all = data.entities || [];
    const counts = { '': all.length, none: all.filter(x => !x.kind).length };
    for (const t of TPL_LIST) counts[t.kind] = all.filter(x => x.kind === t.kind).length;
    const list = _kindFilter === ''
        ? all
        : all.filter(x => (_kindFilter === 'none' ? !x.kind : x.kind === _kindFilter));

    const pill = (val, label, n) => n || val === '' ?
        `<button class="palace-kpill ${_kindFilter === val ? 'active' : ''}" data-kind="${val}">${label} <span>${n}</span></button>` : '';

    el.innerHTML = `
        <div class="mind-toolbar">
            <button class="mind-btn" id="pal-ent-new">+ New entity</button>
            ${transferButtons()}
        </div>
        <div class="mind-toolbar palace-kind-pills">
            ${pill('', 'All', all.length)}
            ${TPL_LIST.map(t => pill(t.kind, `${t.icon} ${escHtml(t.label)}`, counts[t.kind])).join('')}
            ${pill('none', 'Unsorted', counts.none)}
        </div>
        ${list.length ? `<div class="mind-people-grid">
            ${list.map(e => `
                <div class="mind-person-card palace-ent-card" data-id="${e.id}" role="button" tabindex="0">
                    <button class="palace-ent-x" data-id="${e.id}" data-name="${escAttr(e.name)}"
                            data-chunks="${e.chunk_count}" title="Delete entity">✕</button>
                    <div class="mind-person-name">${escHtml(e.name)}</div>
                    <div class="palace-ent-meta">${kindChip(e.kind)}</div>
                    ${e.headline ? `<div class="palace-ent-headline">${escHtml(e.headline.length > 90 ? e.headline.slice(0, 90) + '…' : e.headline)}</div>` : ''}
                    <div class="mind-person-details palace-ent-counts">
                        <div>\u{1F4C4} ${e.chunk_count} ${e.chunk_count === 1 ? 'entry' : 'entries'}</div>
                        <div>\u{1F578}️ woven into ${e.edge_count} ${e.edge_count === 1 ? 'memory' : 'memories'}</div>
                        ${e.mentions ? `<div title="Mentions since the librarian's last pass">\u{1F514} ${e.mentions} unprocessed</div>` : ''}
                    </div>
                </div>`).join('')}
        </div>` : '<div class="mind-empty">No entities in this scope yet — save a memory to the entities layer, or mention someone new.</div>'}
    `;

    el.querySelectorAll('.palace-kpill').forEach(btn => {
        btn.addEventListener('click', () => { _kindFilter = btn.dataset.kind; renderEntities(); });
    });
    el.querySelector('#pal-ent-new')?.addEventListener('click', showNewEntityModal);
    bindTransfer(el, 'entities', () => scope, ui, renderEntities);
    el.querySelectorAll('.palace-ent-card').forEach(card => {
        card.addEventListener('click', () => showEntityModal(parseInt(card.dataset.id)));
    });
    el.querySelectorAll('.palace-ent-x').forEach(btn => {
        btn.addEventListener('click', async (ev) => {
            ev.stopPropagation();
            if (!confirm(deleteEntityWarning(btn.dataset.name, parseInt(btn.dataset.chunks) || 0))) return;
            try {
                const r = await palaceSend(`entities/${btn.dataset.id}`, 'DELETE');
                ui.showToast(`Deleted ${r.name}`, 'success');
                renderEntities();
            } catch (e2) { ui.showToast(`Delete failed: ${e2.message}`, 'error'); }
        });
    });
}

function deleteEntityWarning(name, chunkCount) {
    return `Delete "${name}"?\n\nIts ${chunkCount} ${chunkCount === 1 ? 'entry' : 'entries'} `
        + `(headline, facts, trivia) go with it. Memories that mention it stay — `
        + `they just lose the link. This cannot be undone.`;
}

function showNewEntityModal() {
    document.querySelector('.mind-modal-overlay')?.remove();
    const overlay = document.createElement('div');
    overlay.className = 'pr-modal-overlay mind-modal-overlay';
    overlay.innerHTML = `
        <div class="pr-modal">
            <div class="pr-modal-header">
                <h3>New entity</h3>
                <button class="mind-btn-sm mind-modal-close">✕</button>
            </div>
            <div class="pr-modal-body">
                <div class="mind-form">
                    <input type="text" id="pal-ent-name" placeholder="Name *" maxlength="80">
                    <select id="pal-ent-kind" class="palace-select">
                        <option value="">Unsorted</option>
                        ${TPL_LIST.map(t => `<option value="${escHtml(t.kind)}">${t.icon} ${escHtml(t.label)}</option>`).join('')}
                    </select>
                    <button class="mind-btn" id="pal-ent-create">Create</button>
                </div>
            </div>
        </div>`;
    document.body.appendChild(overlay);
    overlay.querySelector('.mind-modal-close').addEventListener('click', () => overlay.remove());
    setupModalClose(overlay, () => overlay.remove());
    overlay.querySelector('#pal-ent-create').addEventListener('click', async () => {
        const name = overlay.querySelector('#pal-ent-name').value.trim();
        if (!name) { ui.showToast('Name is required', 'error'); return; }
        try {
            const r = await palaceSend('entities', 'POST', {
                name, scope, kind: overlay.querySelector('#pal-ent-kind').value || null,
            });
            overlay.remove();
            ui.showToast(`Created ${r.name}`, 'success');
            await renderEntities();
            showEntityModal(r.id);   // straight into facts/fields editing
        } catch (e) { ui.showToast(`Create failed: ${e.message}`, 'error'); }
    });
    overlay.querySelector('#pal-ent-name').focus();
}

function fieldsForm(kind, fields) {
    const t = TPL[kind];
    if (!t) return '';
    const f = fields || {};
    const extras = Object.keys(f).filter(k => !t.fields.some(fd => fd.key === k));
    const rows = [
        ...t.fields,
        ...extras.map(k => ({ key: k, label: k, type: typeof f[k] === 'boolean' ? 'bool' : 'text' })),
    ];
    if (!rows.length) return '';
    return `<div class="palace-tier-section palace-fields">
        <div class="palace-tier-title">${t.icon} ${escHtml(t.label)} details</div>
        ${rows.map(fd => fd.type === 'bool' ? `
            <label class="palace-field-row palace-field-bool">
                <input type="checkbox" data-fkey="${escHtml(fd.key)}" ${f[fd.key] ? 'checked' : ''}>
                <span>${escHtml(fd.label)}</span>
            </label>` : `
            <label class="palace-field-row">
                <span>${escHtml(fd.label)}</span>
                <input type="text" data-fkey="${escHtml(fd.key)}" value="${escHtml(String(f[fd.key] ?? ''))}">
            </label>`).join('')}
    </div>`;
}

function collectFields(overlay) {
    const fields = {};
    overlay.querySelectorAll('[data-fkey]').forEach(inp => {
        fields[inp.dataset.fkey] = inp.type === 'checkbox' ? inp.checked : inp.value;
    });
    return fields;
}

async function showEntityModal(eid) {
    let data;
    try {
        data = await palaceGet(`entities/${eid}`);
    } catch (e) { ui.showToast(`Failed to load entity: ${e.message}`, 'error'); return; }
    const ent = data.entity;
    const byTier = { 1: [], 2: [], 3: [], other: [] };
    for (const c of data.chunks) (byTier[c.tier] || byTier.other).push(c);
    // Newest tier-1 IS the short description — edited via the input below,
    // so it doesn't also render as a chunk card (older tier-1s still list).
    const headline = byTier[1].shift() || null;

    document.querySelector('.mind-modal-overlay')?.remove();
    const overlay = document.createElement('div');
    overlay.className = 'pr-modal-overlay mind-modal-overlay';
    overlay.innerHTML = `
        <div class="pr-modal palace-ent-modal">
            <div class="pr-modal-header">
                <h3>${escHtml(ent.name)}</h3>
                <select id="pal-ent-kind" class="palace-select" title="What kind of entity is this?">
                    <option value="" ${!ent.kind ? 'selected' : ''}>unsorted</option>
                    ${TPL_LIST.map(t => `<option value="${t.kind}" ${ent.kind === t.kind ? 'selected' : ''}>${t.icon} ${escHtml(t.label)}</option>`).join('')}
                </select>
                <button class="mind-btn-sm mind-modal-close">✕</button>
            </div>
            <div class="pr-modal-body view-scroll">
                <div class="palace-headline-row">
                    <input type="text" id="pal-ent-headline" maxlength="512"
                           placeholder="Short description — one line on who/what this is *"
                           value="${escHtml(headline?.content || '')}">
                </div>
                <div id="pal-ent-fields-holder">${fieldsForm(ent.kind, ent.meta?.fields)}</div>
                ${[1, 2, 3].filter(t => byTier[t].length).map(t => `
                    <div class="palace-tier-section">
                        <div class="palace-tier-title">${TIER_NAMES[t]}</div>
                        ${byTier[t].map(c => `
                            <div class="mind-mem-card palace-chunk" data-id="${c.id}">
                                <div class="mind-mem-header">
                                    ${labelChip(c.label)}${keyPill(c.private_key)}
                                    <span class="mind-mem-time">${escHtml(timeAgo(c.created))}</span>
                                    <span class="mind-mem-id">[${c.id}]</span>
                                </div>
                                <div class="mind-mem-content">${escHtml(c.content)}</div>
                                ${metaPanel(c.meta)}
                                <div class="mind-mem-actions">
                                    <button class="mind-btn-sm palace-del-chunk" data-id="${c.id}" title="Delete">✕</button>
                                </div>
                            </div>`).join('')}
                    </div>`).join('') || '<div class="mind-empty">No entries yet</div>'}
                <div class="palace-tier-section">
                    <button class="mind-btn" id="pal-ent-addfact">+ Add fact</button>
                    <div class="palace-addfact-form" hidden>
                        <textarea id="pal-ent-facttext" rows="3" maxlength="512"
                                  placeholder="New fact about ${escHtml(ent.name)} (max 512 chars) — commits with Save below"></textarea>
                        <div class="palace-addfact-actions">
                            <button class="mind-btn-sm" id="pal-ent-factcancel">Cancel</button>
                        </div>
                    </div>
                </div>
                ${data.mentioned_in.length ? `
                    <div class="palace-tier-section">
                        <div class="palace-tier-title">\u{1F578}️ Woven into ${data.mentioned_in.length} ${data.mentioned_in.length === 1 ? 'memory' : 'memories'}</div>
                        ${data.mentioned_in.map(m => `
                            <div class="palace-mention">
                                <span class="palace-mention-layer">${escHtml(m.layer)}</span>
                                <span class="palace-mention-text">${escHtml(m.content.length > 140 ? m.content.slice(0, 140) + '…' : m.content)}</span>
                                <span class="mind-mem-time">${escHtml(timeAgo(m.created))}</span>
                            </div>`).join('')}
                    </div>` : ''}
            </div>
            <div class="pr-modal-footer">
                <button class="mind-btn palace-btn-danger" id="pal-ent-delete">Delete</button>
                <button class="mind-btn" id="pal-ent-cancel">Cancel</button>
                <button class="mind-btn palace-btn-primary" id="pal-ent-save">Save</button>
            </div>
        </div>`;
    document.body.appendChild(overlay);
    overlay.querySelector('.mind-modal-close').addEventListener('click', () => overlay.remove());
    overlay.querySelector('#pal-ent-cancel').addEventListener('click', () => overlay.remove());
    setupModalClose(overlay, () => overlay.remove());

    overlay.querySelector('#pal-ent-delete').addEventListener('click', async () => {
        if (!confirm(deleteEntityWarning(ent.name, data.chunks.length))) return;
        try {
            const r = await palaceSend(`entities/${eid}`, 'DELETE');
            ui.showToast(`Deleted ${r.name}`, 'success');
            overlay.remove();
            renderEntities();
        } catch (err) { ui.showToast(`Delete failed: ${err.message}`, 'error'); }
    });

    // One deferred save for kind + headline + fields (footer Save commits all).
    const kindSel = overlay.querySelector('#pal-ent-kind');
    const initialFields = JSON.stringify(collectFields(overlay));

    // Kind change re-renders the fields form locally — nothing saves until Save.
    kindSel.addEventListener('change', () => {
        const typed = collectFields(overlay);
        overlay.querySelector('#pal-ent-fields-holder').innerHTML =
            fieldsForm(kindSel.value || null, { ...(ent.meta?.fields || {}), ...typed });
    });

    // ONE Save for the whole modal (Krem's ruling, 2026-07-12 — the old
    // per-section "Save fact" re-rendered from the server and wiped unsaved
    // top-section edits): footer Save commits kind + headline + fields AND
    // any pending new fact, in one click.
    overlay.querySelector('#pal-ent-save').addEventListener('click', async () => {
        const body = {};
        if ((ent.kind || '') !== kindSel.value) body.kind = kindSel.value;
        const hl = overlay.querySelector('#pal-ent-headline').value.trim();
        if (hl !== (headline?.content || '').trim()) body.headline = hl;
        // Fields ride along when present and changed (or the kind changed —
        // the form now belongs to the new kind). Absent form = fields untouched.
        if (overlay.querySelector('[data-fkey]')) {
            const fields = collectFields(overlay);
            if ('kind' in body || JSON.stringify(fields) !== initialFields) body.fields = fields;
        }
        const factText = overlay.querySelector('#pal-ent-facttext').value.trim();
        if (!Object.keys(body).length && !factText) { overlay.remove(); return; }
        try {
            if (Object.keys(body).length) await palaceSend(`entities/${eid}`, 'PUT', body);
            if (factText) {
                await palaceSend('chunks', 'POST', {
                    content: factText, scope: ent.scope, layer: 'entities', entity: ent.name,
                });
            }
            ui.showToast('Saved', 'success');
            overlay.remove();
            renderEntities();
        } catch (err) { ui.showToast(`Save failed: ${err.message}`, 'error'); }
    });

    // + Add fact: inline form, not a JS prompt (Krem, 2026-07-11 — big-text
    // entry always gets a real textarea).
    const factForm = overlay.querySelector('.palace-addfact-form');
    const factBtn = overlay.querySelector('#pal-ent-addfact');
    factBtn.addEventListener('click', () => {
        factForm.hidden = false;
        factBtn.hidden = true;
        overlay.querySelector('#pal-ent-facttext').focus();
    });
    overlay.querySelector('#pal-ent-factcancel').addEventListener('click', () => {
        // Clear on cancel — hidden text must not silently ride the footer Save.
        overlay.querySelector('#pal-ent-facttext').value = '';
        factForm.hidden = true;
        factBtn.hidden = false;
    });

    // Fact ✕ deletes IN PLACE (no modal re-render — a re-render from the
    // server would wipe unsaved top-section edits, the same bug class as the
    // old "Save fact" button). The background list refreshes; the modal's
    // section headers go stale only until next open. Cosmetic > data loss.
    overlay.querySelectorAll('.palace-del-chunk').forEach(btn => {
        btn.addEventListener('click', async () => {
            if (!confirm('Delete this memory?')) return;
            try {
                await palaceSend(`chunks/${btn.dataset.id}`, 'DELETE');
                ui.showToast('Deleted', 'success');
                btn.closest('.mind-mem-card')?.remove();
                renderEntities();
            } catch (e) { ui.showToast(`Delete failed: ${e.message}`, 'error'); }
        });
    });
}
