// views/palace/knowledge.js - Mind › Knowledge, palace edition (L3: reference
// data in sub-chunked groups — label = old tab name, source + chunk_index
// preserve group identity for future neighbor-stitching). ONE tab for human
// AND AI knowledge (2026-07-11): same layer, meta.added_by tells them apart —
// filter with the author pills, add your own with + Add.
import { renderSectionHeader, bindSectionHeader } from '../../shared/section-header.js';
import { helpPills } from '../../features/video-link.js';
import { renderScopeSidebar, bindScopeSidebar } from '../../shared/scope-sidebar.js';
import { listScopes } from '../../shared/scope-api.js';
import { escHtml, escAttr, scopeForChatTab, subscribeMindDomain } from '../../shared/mind-common.js';
import { setupModalClose } from '../../shared/modal.js';
import * as ui from '../../ui.js';
import { PALACE_TABS, refreshPalaceTabs, SCOPE_ENDPOINT, palaceGet, palaceSend, chunkCard, bindChunkCards, describeScopeForDelete, transferButtons, bindTransfer } from './common.js';

const SCOPE_KEY = 'memory_scope';
const DOMAIN = 'knowledge';
const PAGE = 50;

let container = null;
let scope = 'default';
let scopes = [];
let unsub = null;
let _search = '';
let _offset = 0;
let _searchTimer = null;
let _author = '';   // '' = all, 'user', 'ai'

export default {
    init(el) { container = el; },
    async show() {
        await refreshPalaceTabs();
        if (!unsub) unsub = subscribeMindDomain(DOMAIN, () => scope, () => container?.offsetParent !== null, renderList);
        if (window._mindScope) { scope = window._mindScope; delete window._mindScope; }
        else { const s = await scopeForChatTab(SCOPE_KEY); if (s) scope = s; }
        delete window._mindTab;
        scopes = await listScopes(SCOPE_ENDPOINT);
        render();
    },
    hide() { if (unsub) { unsub(); unsub = null; } }
};

function content() { return container?.querySelector('#pal-kn-content'); }

function render() {
    if (!container) return;
    container.innerHTML = `
        ${renderSectionHeader({ tabs: PALACE_TABS, active: 'knowledge', help: helpPills('Knowledge', { doc: 'MEMORY.md', inline: true }), status: '\u{1F3DB}️ Mind Palace — reference knowledge (L3). One shelf, two authors: \u{1F464} added by you, \u{1F916} written by Sapphire.' })}
        <div class="two-panel">
            ${renderScopeSidebar(scopes, scope)}
            <div class="panel-right">
                <div class="view-body view-scroll" id="pal-kn-content"></div>
            </div>
        </div>`;
    bindSectionHeader(container);
    bindScopeSidebar(container, {
        describeScope: describeScopeForDelete,
        onScopeChange: (s) => { scope = s; _search = ''; _offset = 0; render(); },
        onChanged: async (s) => { scope = s || 'default'; _search = ''; _offset = 0; scopes = await listScopes(SCOPE_ENDPOINT); render(); },
    });
    renderList();
}

async function renderList() {
    const el = content();
    if (!el) return;
    const params = new URLSearchParams({ scope, layer: 'knowledge', limit: PAGE, offset: _offset });
    if (_search) params.set('q', _search);
    if (_author) params.set('added_by', _author);
    let data;
    try {
        data = await palaceGet(`chunks?${params}`);
    } catch (e) {
        el.innerHTML = `<div class="mind-empty">Failed to load: ${escHtml(e.message)}</div>`;
        return;
    }
    const chunks = data.chunks || [];
    const apill = (val, label) =>
        `<button class="palace-kpill ${_author === val ? 'active' : ''}" data-author="${val}">${label}</button>`;
    el.innerHTML = `
        <div class="mind-toolbar">
            <input type="search" id="pal-kn-search" class="palace-search" placeholder="Search knowledge…" value="${escAttr(_search)}">
            ${apill('', 'All')}${apill('user', '\u{1F464} Added by you')}${apill('ai', '\u{1F916} Hers')}
            <button class="mind-btn" id="pal-kn-add">+ Add</button>
            ${transferButtons()}
            <span class="palace-count">${data.total} chunks</span>
        </div>
        ${chunks.length
            ? `<div class="palace-chunk-list">${chunks.map(c => chunkCard(c, { showLayer: false })).join('')}</div>`
            : `<div class="mind-empty">${_search ? 'No matches' : 'No knowledge chunks in this scope'}</div>`}
        ${(!_search && _offset + PAGE < data.total)
            ? `<div class="palace-more-wrap"><button class="mind-btn" id="pal-kn-more">Load more (${data.total - _offset - PAGE} older)</button></div>`
            : ''}
    `;
    const searchBox = el.querySelector('#pal-kn-search');
    searchBox?.addEventListener('input', () => {
        clearTimeout(_searchTimer);
        _searchTimer = setTimeout(() => { _search = searchBox.value.trim(); _offset = 0; renderList(); }, 300);
    });
    el.querySelectorAll('.palace-kpill').forEach(btn => {
        btn.addEventListener('click', () => { _author = btn.dataset.author; _offset = 0; renderList(); });
    });
    el.querySelector('#pal-kn-add')?.addEventListener('click', showAddModal);
    bindTransfer(el, 'knowledge', () => scope, ui, renderList);
    el.querySelector('#pal-kn-more')?.addEventListener('click', () => { _offset += PAGE; renderList(); });
    bindChunkCards(el, renderList, ui);
}

function showAddModal() {
    document.querySelector('.mind-modal-overlay')?.remove();
    const overlay = document.createElement('div');
    overlay.className = 'pr-modal-overlay mind-modal-overlay';
    overlay.innerHTML = `
        <div class="pr-modal">
            <div class="pr-modal-header">
                <h3>Add Knowledge</h3>
                <button class="mind-btn-sm mind-modal-close">✕</button>
            </div>
            <div class="pr-modal-body">
                <div class="mind-form">
                    <textarea id="pal-kn-content" placeholder="Reference knowledge — facts, procedures, background *" rows="6"></textarea>
                    <input type="text" id="pal-kn-label" placeholder="Label / topic (optional, groups entries)">
                    <button class="mind-btn" id="pal-kn-save">Save</button>
                </div>
            </div>
        </div>`;
    document.body.appendChild(overlay);
    overlay.querySelector('.mind-modal-close').addEventListener('click', () => overlay.remove());
    setupModalClose(overlay, () => overlay.remove());
    overlay.querySelector('#pal-kn-save').addEventListener('click', async () => {
        const content = overlay.querySelector('#pal-kn-content').value.trim();
        if (!content) { ui.showToast('Content is required', 'error'); return; }
        try {
            await palaceSend('chunks', 'POST', {
                content, scope, layer: 'knowledge',
                label: overlay.querySelector('#pal-kn-label').value.trim() || null,
            });
            overlay.remove();
            ui.showToast('Saved', 'success');
            await renderList();
        } catch (e) { ui.showToast(`Save failed: ${e.message}`, 'error'); }
    });
}
