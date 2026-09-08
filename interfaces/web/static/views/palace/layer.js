// views/palace/layer.js - Generic plugin-layer tab (Plugin Layers v1.1).
// One module, many instances: ?layer=<key> on the import URL picks the layer
// (mind-dispatch.js pattern — distinct URLs = distinct module instances).
// Layer providers ship ZERO frontend: this view is their tab. Browse, search,
// favorite, delete, export/import — the mirror interacts with the palace only
// through normal channels (Krem's ruling, 2026-07-12).
import { renderSectionHeader, bindSectionHeader } from '../../shared/section-header.js';
import { renderScopeSidebar, bindScopeSidebar } from '../../shared/scope-sidebar.js';
import { listScopes } from '../../shared/scope-api.js';
import { escHtml, escAttr, scopeForChatTab, subscribeMindDomain } from '../../shared/mind-common.js';
import * as ui from '../../ui.js';
import { PALACE_TABS, PLUGIN_LAYERS, refreshPalaceTabs, SCOPE_ENDPOINT, palaceGet, chunkCard, bindChunkCards, describeScopeForDelete, transferButtons, bindTransfer, rememberMindScope, recallMindScope } from './common.js';
import { snapFocus, snapScroll } from '../../shared/dom-guard.js';

const params = new URL(import.meta.url).searchParams;
const LAYER = params.get('layer') || '';
const SCOPE_KEY = 'memory_scope';
const DOMAIN = 'memory';   // plugin layers publish on the memory SSE domain
const PAGE = 50;

let container = null;
let scope = 'default';
let scopes = [];
let unsub = null;
let spec = null;           // this layer's registration (null = gone dark)

let _search = '';
let _offset = 0;
let _searchTimer = null;

export default {
    init(el) { container = el; },
    async show() {
        if (!unsub) unsub = subscribeMindDomain(DOMAIN, () => scope, () => container?.offsetParent !== null, renderList);
        await refreshPalaceTabs();
        spec = PLUGIN_LAYERS.find(l => l.key === LAYER) || null;
        const s = recallMindScope() || await scopeForChatTab(SCOPE_KEY);
        if (s) scope = s;
        scopes = await listScopes(SCOPE_ENDPOINT);
        if (scope !== 'default' && !scopes.some(x => x.name === scope)) scope = 'default';
        rememberMindScope(scope);
        render();
    },
    hide() { if (unsub) { unsub(); unsub = null; } }
};

function content() { return container?.querySelector('#pal-layer-content'); }

function render() {
    if (!container) return;
    if (!spec) {
        // Provider disabled since this view registered — the layer is dark.
        container.innerHTML = `<div class="view-placeholder">
            <h2>\u{1F9E9} ${escHtml(LAYER)}</h2>
            <p style="color:var(--text-muted);font-size:var(--font-sm)">This layer's plugin is disabled. Its memories are preserved and return when the plugin is re-enabled.</p>
        </div>`;
        return;
    }
    const hints = [];
    if (!spec.writable) hints.push('content is managed by the plugin');
    if (spec.librarian) hints.push('librarian tends this layer');
    container.innerHTML = `
        ${renderSectionHeader({ tabs: PALACE_TABS, active: `layer-${LAYER}`, status: `${spec.icon ? escHtml(spec.icon) + ' ' : '\u{1F9E9} '}${escHtml(spec.label)} — plugin layer from '${escHtml(spec.plugin)}'.${spec.description ? ' ' + escHtml(spec.description) : ''}${hints.length ? ' (' + hints.join('; ') + ')' : ''}` })}
        <div class="two-panel">
            ${renderScopeSidebar(scopes, scope)}
            <div class="panel-right">
                <div class="view-body view-scroll" id="pal-layer-content"></div>
            </div>
        </div>`;
    bindSectionHeader(container);
    bindScopeSidebar(container, {
        describeScope: describeScopeForDelete,
        onScopeChange: (s) => { scope = s; rememberMindScope(s); _search = ''; _offset = 0; render(); },
        onChanged: async (s) => { scope = s || 'default'; rememberMindScope(scope); _search = ''; _offset = 0; scopes = await listScopes(SCOPE_ENDPOINT); render(); },
    });
    renderList();
}

async function renderList() {
    const el = content();
    if (!el) return;
    const qp = new URLSearchParams({ scope, limit: PAGE, offset: _offset, layer: LAYER });
    if (_search) qp.set('q', _search);
    let data;
    try {
        data = await palaceGet(`chunks?${qp}`);
    } catch (e) {
        el.innerHTML = `<div class="mind-empty">Failed to load: ${escHtml(e.message)}</div>`;
        return;
    }
    const chunks = data.chunks || [];
    // Carry focus + caret + scroll across the rebuild (DOM-refresh hunt 2026-09-08).
    const restoreFocus = snapFocus(el), restoreScroll = snapScroll(el);
    el.innerHTML = `
        <div class="mind-toolbar">
            <input type="search" id="pal-layer-search" class="palace-search" placeholder="Search ${escAttr(spec.label)}…" value="${escAttr(_search)}">
            ${transferButtons()}
            <span class="palace-count">${data.total} in scope</span>
        </div>
        ${chunks.length
            ? `<div class="palace-chunk-list">${chunks.map(c => chunkCard(c, { showLayer: false })).join('')}</div>`
            : `<div class="mind-empty">${_search ? 'No matches' : `Nothing here yet — '${escHtml(spec.plugin)}' fills this layer.`}</div>`}
        ${(!_search && _offset + PAGE < data.total)
            ? `<div class="palace-more-wrap"><button class="mind-btn" id="pal-layer-more">Load more (${data.total - _offset - PAGE} older)</button></div>`
            : ''}
    `;
    const searchBox = el.querySelector('#pal-layer-search');
    searchBox?.addEventListener('input', () => {
        clearTimeout(_searchTimer);
        _searchTimer = setTimeout(() => {
            _search = searchBox.value.trim();
            _offset = 0;
            renderList();
        }, 300);
    });
    restoreScroll();
    restoreFocus();
    bindTransfer(el, LAYER, () => scope, ui, renderList);
    el.querySelector('#pal-layer-more')?.addEventListener('click', () => {
        _offset += PAGE; renderList();
    });
    bindChunkCards(el, renderList, ui);
}
