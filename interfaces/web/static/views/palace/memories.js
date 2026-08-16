// views/palace/memories.js - Mind › Memories, palace edition (L1 events +
// L0 self). Server-side search/pagination (palace scales to 50k rows/layer —
// no warm client cache). Loaded by mind-dispatch when mindpalace is active.
import { renderSectionHeader, bindSectionHeader } from '../../shared/section-header.js';
import { helpPills } from '../../features/video-link.js';
import { renderScopeSidebar, bindScopeSidebar } from '../../shared/scope-sidebar.js';
import { listScopes } from '../../shared/scope-api.js';
import { escHtml, escAttr, scopeForChatTab, subscribeMindDomain } from '../../shared/mind-common.js';
import { setupModalClose } from '../../shared/modal.js';
import * as ui from '../../ui.js';
import { PALACE_TABS, refreshPalaceTabs, SCOPE_ENDPOINT, palaceGet, palaceSend, chunkCard, bindChunkCards, describeScopeForDelete, transferButtons, bindTransfer, rememberMindScope, recallMindScope } from './common.js';

const SCOPE_KEY = 'memory_scope';
const DOMAIN = 'memory';
const PAGE = 50;

let container = null;
let scope = 'default';
let scopes = [];
let unsub = null;

let _search = '';
let _offset = 0;
let _searchTimer = null;
let _keyed = false;        // 🔒 only memories carrying a private key

function resetFilters() { _search = ''; _offset = 0; _keyed = false; }

export default {
    init(el) { container = el; },
    async show() {
        await refreshPalaceTabs();
        if (!unsub) unsub = subscribeMindDomain(DOMAIN, () => scope, () => container?.offsetParent !== null, renderList);
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

function content() { return container?.querySelector('#pal-mem-content'); }

function render() {
    if (!container) return;
    container.innerHTML = `
        ${renderSectionHeader({ tabs: PALACE_TABS, active: 'memories', help: helpPills('Memories', { doc: 'MEMORY.md', inline: true }), status: '\u{1F3DB}️ Mind Palace — her diary, one stream. Deliberate self notes ride along (chip says which); the self SHEET lives on the Self tab. Expand "meta" on any card to see how a memory was made.' })}
        <div class="two-panel">
            ${renderScopeSidebar(scopes, scope)}
            <div class="panel-right">
                <div class="view-body view-scroll" id="pal-mem-content"></div>
            </div>
        </div>`;
    bindSectionHeader(container);
    bindScopeSidebar(container, {
        describeScope: describeScopeForDelete,
        onScopeChange: (s) => { scope = s; rememberMindScope(s); resetFilters(); render(); },
        onChanged: async (s) => { scope = s || 'default'; rememberMindScope(scope); resetFilters(); scopes = await listScopes(SCOPE_ENDPOINT); render(); },
    });
    renderList();
}

async function renderList() {
    const el = content();
    if (!el) return;
    // ONE stream, for real now (self layer retired 2026-07-26 — free self
    // notes migrated into events; the sheet's home is the Self page).
    const params = new URLSearchParams({ scope, limit: PAGE, offset: _offset,
                                         layer: 'events',
                                         exclude_sheet: 1 });
    if (_search) params.set('q', _search);
    if (_keyed) params.set('keyed', 1);
    let data;
    try {
        data = await palaceGet(`chunks?${params}`);
    } catch (e) {
        el.innerHTML = `<div class="ui-empty">Failed to load: ${escHtml(e.message)}</div>`;
        return;
    }
    const chunks = data.chunks || [];

    el.innerHTML = `
        <div class="ui-rows">
            <div class="ui-row">
                <input type="search" id="pal-mem-search" class="palace-search" placeholder="Search memories…" value="${escAttr(_search)}">
                <span class="palace-count">${data.total} in scope</span>
            </div>
            <div class="ui-row">
                <button class="ui-pill ${_keyed ? 'ui-pill-on' : ''}" id="pal-mem-keyed" title="Only memories saved with a private key">🔒 Keyed</button>
            </div>
            <div class="ui-row">
                <button class="mind-btn" id="pal-mem-add">+ Add Memory</button>
                ${transferButtons()}
            </div>
        </div>
        ${chunks.length
            ? `<div class="palace-chunk-list">${chunks.map(c => chunkCard(c)).join('')}</div>`
            : `<div class="ui-empty">${_search ? 'No matches' : 'No memories yet'}</div>`}
        ${(!_search && _offset + PAGE < data.total)
            ? `<div class="palace-more-wrap"><button class="mind-btn" id="pal-mem-more">Load more (${data.total - _offset - PAGE} older)</button></div>`
            : ''}
    `;

    el.querySelector('#pal-mem-keyed')?.addEventListener('click', () => {
        _keyed = !_keyed;
        _offset = 0;
        renderList();
    });
    const searchBox = el.querySelector('#pal-mem-search');
    searchBox?.addEventListener('input', () => {
        clearTimeout(_searchTimer);
        _searchTimer = setTimeout(() => {
            _search = searchBox.value.trim();
            _offset = 0;
            renderList();
        }, 300);
    });
    // Keep focus through the re-render when typing
    if (_search && document.activeElement === document.body) {
        searchBox?.focus();
        searchBox?.setSelectionRange(searchBox.value.length, searchBox.value.length);
    }
    el.querySelector('#pal-mem-add')?.addEventListener('click', showAddModal);
    bindTransfer(el, 'events', () => scope, ui, renderList);
    el.querySelector('#pal-mem-more')?.addEventListener('click', () => {
        _offset += PAGE; renderList();
    });
    bindChunkCards(el, renderList, ui);
}

function showAddModal() {
    document.querySelector('.mind-modal-overlay')?.remove();
    const overlay = document.createElement('div');
    overlay.className = 'pr-modal-overlay mind-modal-overlay';
    overlay.innerHTML = `
        <div class="pr-modal">
            <div class="pr-modal-header">
                <h3>Add Memory</h3>
                <button class="mind-btn-sm mind-modal-close">✕</button>
            </div>
            <div class="pr-modal-body">
                <div class="mind-form">
                    <textarea id="pal-add-content" placeholder="The memory (max 512 chars) *" rows="4" maxlength="512"></textarea>
                    <input type="text" id="pal-add-label" placeholder="Label (optional)">
                    <label style="display:flex;align-items:center;gap:8px;font-size:0.8125rem;color:var(--text-muted);cursor:pointer">
                        <input type="checkbox" id="pal-add-fav"> Favorite (never fades)
                    </label>
                    <button class="mind-btn" id="pal-add-save">Save</button>
                </div>
            </div>
        </div>`;
    document.body.appendChild(overlay);
    overlay.querySelector('.mind-modal-close').addEventListener('click', () => overlay.remove());
    setupModalClose(overlay, () => overlay.remove());
    overlay.querySelector('#pal-add-save').addEventListener('click', async () => {
        const content = overlay.querySelector('#pal-add-content').value.trim();
        if (!content) { ui.showToast('Content is required', 'error'); return; }
        try {
            await palaceSend('chunks', 'POST', {
                content, scope,
                layer: 'events',
                label: overlay.querySelector('#pal-add-label').value.trim() || null,
                favorite: overlay.querySelector('#pal-add-fav').checked,
            });
            overlay.remove();
            ui.showToast('Saved', 'success');
            await renderList();
        } catch (e) { ui.showToast(`Save failed: ${e.message}`, 'error'); }
    });
}
