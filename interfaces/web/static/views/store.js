// views/store.js — In-app Plugin Store.
//
// Fullscreen view modeled on help.js. No nav-rail item — reached from
// Settings Dashboard "Recommended Plugins" widget and the Plugins tab
// "Browse Store" button (wired in Stage 6).
//
// Hash routes:
//   #store                       → Plugins tab landing
//   #store/plugins               → explicit Plugins tab
//   #store/plugins/<slug>        → detail page
//   #store/personas              → disabled placeholder
//
// Card install states (driven by server-side annotation):
//   none             → [ Install ]
//   current          → [ Installed ] (muted, no action)
//   update_available → [ Update ]   (accent)

import {
    getStoreStatus, listStorePlugins, getStorePlugin,
    getStoreCategories, installFromStore,
    listStorePersonas, getStorePersona, getStorePersonaCategories,
    installPersonaFromStore,
} from '../shared/store-api.js';
import { renderMarkdown } from '../shared/markdown.js';
import { snapFocus } from '../shared/dom-guard.js';
import { isSafeHref } from '../shared/url-safety.js';
import { refreshInitData } from '../shared/init-data.js';
import { confirmPersonaImport } from '../shared/persona-import-confirm.js';
import * as ui from '../ui.js';

const SUBMIT_URL = 'https://sapphireblue.dev/plugins/submit-your-plugin/';
const HELP_PLUGINS_HASH = '#help/plugin-author/README';

// Per-store config. The two tabs share all DOM/render scaffolding; only the
// data source, labels, and the get-it action differ.
const STORES = {
    plugins: {
        list: listStorePlugins,
        detail: getStorePlugin,
        categories: getStoreCategories,
        allLabel: 'All Plugins',
        searchPlaceholder: 'Search plugins...',
        featuredBlurb: 'Community plugins backed by the Sapphire team — these authors have earned a spot.',
        emptyNoun: 'plugins',
    },
    personas: {
        list: listStorePersonas,
        detail: getStorePersona,
        categories: getStorePersonaCategories,
        allLabel: 'All Personas',
        searchPlaceholder: 'Search personas...',
        featuredBlurb: 'Personas featured by the Sapphire team.',
        emptyNoun: 'personas',
    },
};

let container = null;
let state = {
    tab: 'plugins',           // 'plugins' | 'personas'
    category: null,           // category slug or null = all
    sort: 'newest',           // 'newest' | 'votes' | 'name' | 'updated'
    q: '',                    // search query
    page: 1,
    detailSlug: null,         // when set, render detail page
    storeStatus: null,        // cached /status response
};

const store = () => STORES[state.tab] || STORES.plugins;

// Categories cached per tab — plugin and persona namespaces are distinct.
let categoriesCache = { plugins: null, personas: null };
let listInflight = null;     // dedupe rapid clicks
let searchTimeout = null;


/* ── Utilities ───────────────────────────────────────────────────────────── */

function _esc(s) {
    return String(s ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}


function _categoryIcon(catSlug) {
    const cache = categoriesCache[state.tab];
    if (!cache) return '';
    const cat = cache.find(c => c.slug === catSlug);
    return cat?.icon || '';
}


function _trustBadge(level) {
    const cls = `store-trust-${level || 'community'}`;
    const label = (level || 'community').replace(/^./, c => c.toUpperCase());
    return `<span class="store-trust-badge ${cls}">${_esc(label)}</span>`;
}


function _installButton(item) {
    const slug = _esc(item.slug);
    const stateAttr = _esc(item.installed_state || 'none');
    if (item.installed_state === 'current') {
        return `<button class="store-btn store-btn-installed" data-slug="${slug}" data-state="${stateAttr}" disabled>Installed</button>`;
    }
    if (item.installed_state === 'update_available') {
        return `<button class="store-btn store-btn-update" data-slug="${slug}" data-state="${stateAttr}" data-action="install">Update</button>`;
    }
    return `<button class="store-btn store-btn-install" data-slug="${slug}" data-state="${stateAttr}" data-action="install">Install</button>`;
}


function _readHashParts() {
    // #store/plugins/peg-and-pint → ['plugins', 'peg-and-pint']
    const raw = location.hash.replace(/^#store\/?/, '');
    return raw ? raw.split('/').filter(Boolean) : [];
}


function _writeHash(parts) {
    const path = parts.length ? `/${parts.join('/')}` : '';
    history.replaceState(null, '', `#store${path}`);
}


/* ── Rendering ───────────────────────────────────────────────────────────── */

function renderShell() {
    container.innerHTML = `
    <div class="store-view">
        <div class="store-header">
            <div class="store-tabs">
                <button class="store-tab ${state.tab === 'plugins' ? 'active' : ''}" data-tab="plugins">Plugins</button>
                <button class="store-tab ${state.tab === 'personas' ? 'active' : ''}" data-tab="personas">Personas</button>
            </div>
            <div class="store-header-actions">
                <a href="${SUBMIT_URL}" target="_blank" rel="noopener noreferrer" class="store-btn store-btn-secondary">Submit your plugin</a>
                <a href="${HELP_PLUGINS_HASH}" class="store-btn store-btn-secondary">How to write a plugin</a>
            </div>
        </div>
        <div class="store-body">
            <div class="store-sidebar"></div>
            <div class="store-main"></div>
        </div>
    </div>`;
}


function renderSidebar() {
    const sb = container.querySelector('.store-sidebar');
    if (!sb) return;
    const cache = categoriesCache[state.tab];
    if (!cache) {
        sb.innerHTML = '<div class="store-loading">Loading categories...</div>';
        return;
    }
    const all = `<button class="store-cat ${state.category === null ? 'active' : ''}" data-cat="">${_esc(store().allLabel)}</button>`;
    const items = cache
        .filter(c => (c.count || 0) > 0)
        .map(c => `
            <button class="store-cat ${state.category === c.slug ? 'active' : ''}" data-cat="${_esc(c.slug)}">
                <span class="store-cat-icon">${_esc(c.icon || '📁')}</span>
                <span class="store-cat-label">${_esc(c.label || c.slug)}</span>
                <span class="store-cat-count">${c.count}</span>
            </button>
        `).join('');
    sb.innerHTML = all + items;
}


function _githubUsernameFromUrl(url) {
    // Extract `<user>` from `https://github.com/<user>` (strict — bare profile
    // URL only, not repo paths). Returns null on non-match. Avoids constructing
    // avatar URLs from arbitrary author_url values that didn't match GitHub.
    if (!url || typeof url !== 'string') return null;
    const m = url.match(/^https:\/\/github\.com\/([A-Za-z0-9][A-Za-z0-9-]{0,38})\/?$/);
    return m ? m[1] : null;
}

function _renderAuthorAvatar(item) {
    // Author avatar for featured cards. GitHub profile URL → real avatar via
    // github.com/<user>.png redirector. Otherwise fall back to a gradient
    // circle with the author's first letter — keeps the layout consistent
    // even for community authors who set a non-GitHub author_url (or none).
    // On GitHub-load failure: img removes itself rather than trying to
    // synthesize a fallback inline (cleaner than embedding JS in onerror).
    const username = _githubUsernameFromUrl(item.author_url);
    if (username) {
        return `<img class="store-card-avatar" loading="lazy" referrerpolicy="no-referrer"
            src="https://github.com/${_esc(username)}.png?size=64"
            alt="${_esc(item.author || username)}"
            onerror="this.remove()">`;
    }
    const letter = (item.author || '?').charAt(0).toUpperCase();
    return `<div class="store-card-avatar store-card-avatar-fallback">${_esc(letter)}</div>`;
}

function _personaThumb(item) {
    // Letter rendered behind; the avatar img covers it. If the img fails to
    // load it removes itself, revealing the trim-colored letter block.
    const url = item.avatar_url;
    const color = item.trim_color || 'var(--trim)';
    const letter = _esc((item.sapphire_name || item.name || '?').charAt(0).toUpperCase());
    const img = (url && isSafeHref(url))
        ? `<img src="${_esc(url)}" alt="" loading="lazy" onerror="this.remove()">`
        : '';
    return `<div class="store-persona-thumb" style="background:${_esc(color)}">
        <span class="store-persona-thumb-letter">${letter}</span>${img}
    </div>`;
}


function _personaMastheadImg(item) {
    // Bigger version of the card thumb for the detail masthead; trim color
    // doubles as the ring + fallback background.
    const url = item.avatar_url;
    const color = item.trim_color || 'var(--trim)';
    const letter = _esc((item.sapphire_name || item.name || '?').charAt(0).toUpperCase());
    const img = (url && isSafeHref(url))
        ? `<img src="${_esc(url)}" alt="" loading="lazy" onerror="this.remove()">`
        : '';
    return `<div class="store-persona-masthead-img" style="background:${_esc(color)};box-shadow:0 0 0 3px ${_esc(color)}">
        <span class="store-persona-thumb-letter">${letter}</span>${img}
    </div>`;
}


function renderPersonaCard(item, showcase = false) {
    const isFeatured = !!item.featured;
    const featured = isFeatured ? '<span class="store-featured-tag">★ Featured</span>' : '';
    const name = item.sapphire_name || item.name || 'Unnamed';
    const author = (item.author_url && isSafeHref(item.author_url))
        ? `<a href="${_esc(item.author_url)}" target="_blank" rel="noopener noreferrer">${_esc(item.author)}</a>`
        : _esc(item.author || 'Unknown');
    const catIcon = _categoryIcon(item.category);
    const catLabel = _findCategoryLabel(item.category);
    const color = item.trim_color || 'var(--trim)';
    let cardClass = 'store-card store-persona-card';
    if (showcase && isFeatured) cardClass += ' store-card-featured';
    else if (isFeatured) cardClass += ' store-card-tagged';
    return `
    <article class="${cardClass}" data-slug="${_esc(item.slug)}">
        ${_personaThumb(item)}
        <div class="store-persona-body" style="border-left:3px solid ${_esc(color)}">
            <h3 class="store-card-name">${_esc(name)} ${featured}</h3>
            <span class="store-persona-by">by ${author}</span>
            <p class="store-persona-motto">${_esc(item.tagline || item.description || '')}</p>
            <span class="store-persona-cat">${_esc(catIcon)} ${_esc(catLabel)}</span>
            <footer class="store-card-actions">
                <button class="store-btn store-btn-install" data-action="install" data-slug="${_esc(item.slug)}">Get</button>
                <button class="store-btn store-btn-secondary" data-action="details" data-slug="${_esc(item.slug)}">Details</button>
            </footer>
        </div>
    </article>`;
}


function renderCard(item, showcase = false) {
    if (state.tab === 'personas') return renderPersonaCard(item, showcase);
    // `showcase=true` is passed only by the top featured strip — that strip
    // gets the premium tile treatment (gradient bg, trim border, glow).
    // The same item appearing in the bottom "All Plugins" grid keeps its
    // small ★ Featured tag but drops the showcase styling, so the bottom
    // grid stays calm and the top strip retains visual hierarchy. 2026-05-08.
    const isFeatured = !!item.featured;
    const featured = isFeatured ? '<span class="store-featured-tag">★ Featured</span>' : '';
    const author = (item.author_url && isSafeHref(item.author_url))
        ? `<a href="${_esc(item.author_url)}" target="_blank" rel="noopener noreferrer">${_esc(item.author)}</a>`
        : _esc(item.author || 'Unknown');
    const version = item.version ? `<span class="store-card-version">v${_esc(item.version)}</span>` : '';
    const localVer = item.installed_state === 'update_available' && item.local_version
        ? `<span class="store-card-localver" title="You have v${_esc(item.local_version)}">→ v${_esc(item.version)}</span>`
        : '';
    // Avatar policy:
    //   - Showcase strip: always show (humanizes featured plugins)
    //   - Bottom grid: only for verified/official trust (skip community)
    //     — visual hierarchy says higher trust = more presence on the page.
    // 2026-05-08.
    const showAvatar = showcase || item.trust_level === 'verified' || item.trust_level === 'official';
    const avatar = showAvatar ? _renderAuthorAvatar(item) : '';
    // Three-tier card treatment:
    //   - showcase + featured  → `store-card-featured` (full premium: bg+border+glow+scaled tag)
    //   - bottom + featured    → `store-card-tagged`   (just a subtle trim border)
    //   - everything else      → `store-card`          (default)
    // 2026-05-08.
    let cardClass = 'store-card';
    if (showcase && isFeatured) cardClass += ' store-card-featured';
    else if (isFeatured) cardClass += ' store-card-tagged';
    return `
    <article class="${cardClass}" data-slug="${_esc(item.slug)}">
        <header class="store-card-header">
            <span class="store-card-icon" title="${_esc(item.category)}">${_esc(_categoryIcon(item.category) || '🧩')}</span>
            <h3 class="store-card-name">${_esc(item.name)}</h3>
            ${featured}
        </header>
        <div class="store-card-meta">
            ${avatar}
            <span class="store-card-author">by ${author}</span>
            ${version}
            ${localVer}
            ${_trustBadge(item.trust_level)}
        </div>
        <p class="store-card-desc">${_esc(item.description || '')}</p>
        <footer class="store-card-actions">
            ${_installButton(item)}
            <button class="store-btn store-btn-secondary" data-action="details" data-slug="${_esc(item.slug)}">Details</button>
        </footer>
    </article>`;
}


// Focus + caret snapshot, taken by renderList BEFORE its 'Loading…' wipe. The
// old inline capture lived in renderMain and ran AFTER that wipe, so it never
// saw the search box — every 800 ms typing pause destroyed the field and the
// next keystrokes went nowhere (DOM-refresh hunt 2026-09-08).
let _focusStash = null;

function renderMain(html) {
    const m = container.querySelector('.store-main');
    if (!m) return;
    const restore = _focusStash || snapFocus(m);
    _focusStash = null;
    m.innerHTML = html;
    restore();
}


function renderEmpty(msg, withRetry = false) {
    renderMain(`
        <div class="store-empty">
            <p>${_esc(msg)}</p>
            ${withRetry ? '<button class="store-btn store-btn-secondary" data-action="retry">Retry</button>' : ''}
        </div>
    `);
}


function renderUnreachable() {
    renderMain(`
        <div class="store-empty">
            <h3>The store is unreachable.</h3>
            <p>Check your network or try again in a moment.</p>
            <button class="store-btn store-btn-secondary" data-action="retry">Retry</button>
        </div>
    `);
}


async function renderList() {
    state.detailSlug = null;
    const main = container.querySelector('.store-main');
    if (!main) return;
    _focusStash = snapFocus(main);   // before the wipe; renderMain restores
    main.innerHTML = '<div class="store-loading">Loading...</div>';

    // Two parallel fetches when on landing tab + first page: featured strip + grid.
    // For category/search/page>1 pages, just the grid.
    const showFeaturedStrip = !state.category && !state.q && state.page === 1;
    let featuredItems = [];
    let listResult = null;

    try {
        const tasks = [
            store().list({
                q: state.q || null,
                category: state.category,
                sort: state.sort,
                page: state.page,
                perPage: 20,
            }).then(r => { listResult = r; }),
        ];
        if (showFeaturedStrip) {
            tasks.push(
                store().list({ featured: true, perPage: 6 })
                    .then(r => { featuredItems = r?.items || []; })
                    .catch(() => { featuredItems = []; })
            );
        }
        await Promise.all(tasks);
    } catch (e) {
        console.warn('[Store] list failed:', e);
        renderUnreachable();
        return;
    }

    if (!listResult || listResult.unreachable) {
        renderUnreachable();
        return;
    }

    const items = listResult.items || [];
    const total = listResult.total || 0;
    const pages = listResult.pages || 1;

    let html = '';

    if (showFeaturedStrip && featuredItems.length > 0) {
        html += `
        <section class="store-featured">
            <h2 class="store-section-title">★ Featured</h2>
            <p class="store-featured-blurb">${_esc(store().featuredBlurb)}</p>
            <div class="store-grid store-grid-featured">
                ${featuredItems.map(it => renderCard(it, true)).join('')}
            </div>
        </section>`;
    }

    const heading = state.q
        ? `Search: "${_esc(state.q)}" — ${total} result${total === 1 ? '' : 's'}`
        : state.category
            ? `${_esc(_findCategoryLabel(state.category))} (${total})`
            : `${_esc(store().allLabel)} (${total})`;

    html += `
    <section class="store-list">
        <div class="store-list-header">
            <h2 class="store-section-title">${heading}</h2>
            <div class="store-list-controls">
                <input type="search" class="store-search" placeholder="${_esc(store().searchPlaceholder)}" value="${_esc(state.q)}">
                <select class="store-sort" ${state.q ? 'disabled' : ''}>
                    <option value="newest" ${state.sort === 'newest' ? 'selected' : ''}>Newest</option>
                    <option value="updated" ${state.sort === 'updated' ? 'selected' : ''}>Recently updated</option>
                    <option value="votes" ${state.sort === 'votes' ? 'selected' : ''}>Most voted</option>
                    <option value="name" ${state.sort === 'name' ? 'selected' : ''}>Name</option>
                </select>
            </div>
        </div>`;

    if (items.length === 0) {
        html += `<div class="store-empty"><p>No ${_esc(store().emptyNoun)} ${state.q ? 'match this search' : 'in this category'} yet.</p></div>`;
    } else {
        html += `<div class="store-grid">${items.map(renderCard).join('')}</div>`;
        if (pages > 1) {
            html += renderPagination(state.page, pages);
        }
    }

    html += `</section>`;
    renderMain(html);
}


function renderPagination(page, pages) {
    const prev = page > 1
        ? `<button class="store-btn store-btn-secondary" data-action="page" data-page="${page - 1}">‹ Prev</button>`
        : `<button class="store-btn store-btn-secondary" disabled>‹ Prev</button>`;
    const next = page < pages
        ? `<button class="store-btn store-btn-secondary" data-action="page" data-page="${page + 1}">Next ›</button>`
        : `<button class="store-btn store-btn-secondary" disabled>Next ›</button>`;
    return `
    <div class="store-pagination">
        ${prev}
        <span class="store-pagination-info">Page ${page} of ${pages}</span>
        ${next}
    </div>`;
}


function _findCategoryLabel(slug) {
    const c = (categoriesCache[state.tab] || []).find(x => x.slug === slug);
    return c?.label || slug;
}


function _renderPromptAccordions(item) {
    // The full prompt is already in `export_content` (the bundle). No extra
    // fetch. Monolith → one section; assembled → one accordion per component.
    let bundle;
    try { bundle = item.export_content ? JSON.parse(item.export_content) : null; }
    catch (_) { bundle = null; }
    if (!bundle) return '<p class="store-empty">No prompt details available.</p>';

    const pdata = (bundle.prompt || {}).data || {};
    const sections = [];
    if (pdata.type === 'monolith' && pdata.content) {
        sections.push({ title: 'Prompt', body: pdata.content });
    } else {
        const comps = bundle.components;
        if (comps && typeof comps === 'object') {
            for (const [type, defs] of Object.entries(comps)) {
                if (!defs || typeof defs !== 'object') continue;
                for (const [key, value] of Object.entries(defs)) {
                    const text = (typeof value === 'string') ? value : (value?.content || '');
                    if (!text || !text.trim()) continue;   // skip empty components
                    sections.push({ title: `${type} · ${key}`, body: text });
                }
            }
        }
    }
    if (!sections.length) return '<p class="store-empty">No prompt details available.</p>';

    return `<h3 class="store-prompt-heading">Prompt</h3>` + sections.map(s => `
        <details class="store-prompt-acc" open>
            <summary>${_esc(s.title)}</summary>
            <pre class="store-prompt-text">${_esc(s.body)}</pre>
        </details>`).join('');
}


async function renderPersonaDetail(slug) {
    state.detailSlug = slug;
    const main = container.querySelector('.store-main');
    if (!main) return;
    main.innerHTML = '<div class="store-loading">Loading...</div>';

    let item;
    try {
        item = await getStorePersona(slug);
    } catch (e) {
        console.warn('[Store] persona detail fetch failed:', e);
        renderUnreachable();
        return;
    }
    if (!item || item.unreachable) { renderUnreachable(); return; }

    const name = item.sapphire_name || item.name || 'Unnamed';
    const author = (item.author_url && isSafeHref(item.author_url))
        ? `<a href="${_esc(item.author_url)}" target="_blank" rel="noopener noreferrer">${_esc(item.author)}</a>`
        : _esc(item.author || 'Unknown');
    let voiceName = '';
    try {
        const vc = item.voice_config ? JSON.parse(item.voice_config) : null;
        if (vc?.voice) voiceName = vc.voice;
    } catch (_) { /* voice_config is a string; ignore parse errors */ }
    const catIcon = _categoryIcon(item.category);
    const catLabel = _findCategoryLabel(item.category);
    const screenshot = (item.screenshot_url && isSafeHref(item.screenshot_url))
        ? `<img class="store-detail-screenshot" src="${_esc(item.screenshot_url)}" alt="">`
        : '';
    const featured = item.featured ? '<span class="store-featured-tag">★ Featured</span>' : '';

    const rows = [`<div class="store-persona-info-row"><span class="lbl">By</span>${author}</div>`];
    if (voiceName) rows.push(`<div class="store-persona-info-row"><span class="lbl">Voice</span>${_esc(voiceName)}</div>`);
    if (item.prompt_type) rows.push(`<div class="store-persona-info-row"><span class="lbl">Type</span>${_esc(item.prompt_type)}</div>`);
    rows.push(`<div class="store-persona-info-row"><span class="lbl">Category</span>${_esc(catIcon)} ${_esc(catLabel)}</div>`);
    if (item.privacy_required) rows.push(`<div class="store-persona-info-row"><span class="lbl">Privacy</span>🔒 cloud disabled</div>`);

    renderMain(`
    <div class="store-detail">
        <button class="store-back-btn" data-action="back">‹ Back</button>
        <article>
            <header class="store-persona-masthead">
                ${_personaMastheadImg(item)}
                <div class="store-persona-masthead-info">
                    <h1 class="store-detail-name">${_esc(name)} ${featured}</h1>
                    ${rows.join('')}
                    ${item.tagline ? `<p class="store-persona-masthead-tagline">${_esc(item.tagline)}</p>` : ''}
                    <div class="store-persona-masthead-actions">
                        <button class="store-btn store-btn-install" data-action="install" data-slug="${_esc(item.slug)}">Get</button>
                    </div>
                </div>
            </header>
            ${screenshot}
            <div class="store-detail-body">
                ${_renderPromptAccordions(item)}
            </div>
        </article>
    </div>
    `);
}


async function renderDetail(slug) {
    if (state.tab === 'personas') return renderPersonaDetail(slug);
    state.detailSlug = slug;
    const main = container.querySelector('.store-main');
    if (!main) return;
    main.innerHTML = '<div class="store-loading">Loading...</div>';

    let item;
    try {
        item = await getStorePlugin(slug);
    } catch (e) {
        console.warn('[Store] detail fetch failed:', e);
        renderUnreachable();
        return;
    }

    if (!item || item.unreachable) { renderUnreachable(); return; }

    const author = (item.author_url && isSafeHref(item.author_url))
        ? `<a href="${_esc(item.author_url)}" target="_blank" rel="noopener noreferrer">${_esc(item.author)}</a>`
        : _esc(item.author || 'Unknown');
    const longHtml = item.long_description ? renderMarkdown(item.long_description) : '';
    const screenshot = (item.screenshot_url && isSafeHref(item.screenshot_url))
        ? `<img class="store-detail-screenshot" src="${_esc(item.screenshot_url)}" alt="">`
        : '';
    const featured = item.featured ? '<span class="store-featured-tag">★ Featured</span>' : '';
    const localVerNote = item.installed_state === 'update_available' && item.local_version
        ? `<p class="store-detail-update-note">You have v${_esc(item.local_version)} installed.</p>`
        : item.installed_state === 'current' && item.local_version
            ? `<p class="store-detail-update-note">Installed (v${_esc(item.local_version)}).</p>`
            : '';

    renderMain(`
    <div class="store-detail">
        <button class="store-back-btn" data-action="back">‹ Back</button>
        <article>
            <header class="store-detail-header">
                <span class="store-detail-icon">${_esc(_categoryIcon(item.category) || '🧩')}</span>
                <div>
                    <h1 class="store-detail-name">${_esc(item.name)}</h1>
                    <div class="store-detail-meta">
                        <span class="store-detail-author">by ${author}</span>
                        ${item.version ? `<span class="store-detail-version">v${_esc(item.version)}</span>` : ''}
                        ${_trustBadge(item.trust_level)}
                        ${featured}
                    </div>
                </div>
                <div class="store-detail-install">
                    ${_installButton(item)}
                </div>
            </header>
            ${localVerNote}
            ${screenshot}
            <p class="store-detail-summary">${_esc(item.description || '')}</p>
            <div class="store-detail-body">
                ${longHtml || '<p class="store-empty">No long description provided.</p>'}
            </div>
            <footer class="store-detail-footer">
                ${isSafeHref(item.github_url)
                    ? `<a href="${_esc(item.github_url)}" target="_blank" rel="noopener noreferrer">View source on ${_esc(item.source_type || 'GitHub')} →</a>`
                    : ''}
                <span class="store-detail-cat">${_esc(_categoryIcon(item.category))} ${_esc(_findCategoryLabel(item.category))}</span>
            </footer>
        </article>
    </div>
    `);
}


/* ── Install flow ────────────────────────────────────────────────────────── */

async function confirmAndInstall(slug, btn) {
    let item;
    try {
        item = await getStorePlugin(slug);
    } catch (e) {
        ui.showToast(`Couldn't load plugin: ${e.message}`, 'error');
        return;
    }
    if (!item || item.unreachable) {
        ui.showToast('Store unreachable. Try again.', 'error');
        return;
    }

    const overlay = document.createElement('div');
    overlay.className = 'modal-overlay store-install-modal';
    const verLine = item.version ? `v${_esc(item.version)}` : '';
    const trust = _esc(item.trust_level || 'community');
    const isUpdate = item.installed_state === 'update_available';
    const verb = isUpdate ? 'Update' : 'Install';
    overlay.innerHTML = `
    <div class="modal store-install-dialog">
        <h2>${verb} ${_esc(item.name)}?</h2>
        <p class="store-install-byline">by ${_esc(item.author || 'Unknown')} · ${verLine} · ${trust}</p>
        <p class="store-install-desc">${_esc(item.description || '')}</p>
        ${isUpdate && item.local_version ? `<p class="store-install-note">You have v${_esc(item.local_version)} installed.</p>` : ''}
        <div class="modal-actions">
            <button class="store-btn store-btn-secondary" data-modal-action="cancel">Cancel</button>
            <button class="store-btn store-btn-install" data-modal-action="confirm">${verb}</button>
        </div>
    </div>`;
    document.body.appendChild(overlay);

    const close = () => overlay.remove();
    overlay.addEventListener('click', e => {
        if (e.target === overlay) close();
    });
    overlay.querySelector('[data-modal-action="cancel"]').addEventListener('click', close);
    overlay.querySelector('[data-modal-action="confirm"]').addEventListener('click', async () => {
        const confirmBtn = overlay.querySelector('[data-modal-action="confirm"]');
        confirmBtn.disabled = true;
        confirmBtn.textContent = isUpdate ? 'Updating...' : 'Installing...';
        try {
            await installFromStore({
                githubUrl: item.github_url,
                storeSlug: item.slug,
            });
            ui.showToast(`${item.name} ${isUpdate ? 'updated' : 'installed'}.`, 'success');
            // Trigger frontend main.js load for the new plugin. Without this,
            // a default_enabled:true plugin lands on disk + backend registers
            // its tools, but the frontend script never loads until the user
            // does a full page reload. 2026-05-14.
            document.dispatchEvent(new CustomEvent('sapphire:plugin_toggled'));
            close();
            // Refresh whichever surface is showing.
            if (state.detailSlug) renderDetail(state.detailSlug);
            else renderList();
        } catch (e) {
            ui.showToast(`Install failed: ${e.message}`, 'error');
            confirmBtn.disabled = false;
            confirmBtn.textContent = verb;
        }
    });
}


async function confirmAndImportPersona(slug) {
    let item;
    try {
        item = await getStorePersona(slug);
    } catch (e) {
        ui.showToast(`Couldn't load persona: ${e.message}`, 'error');
        return;
    }
    if (!item || item.unreachable) {
        ui.showToast('Store unreachable. Try again.', 'error');
        return;
    }

    // The store's bundle is in export_content — use it for the collision +
    // piece-preview; the actual import still pulls the PNG card server-side
    // (export_content carries no avatar). Same shared confirm as the local import.
    let bundle = null;
    try { bundle = item.export_content ? JSON.parse(item.export_content) : null; } catch (_) {}
    if (!bundle) {
        const nm = item.sapphire_name || item.name;
        bundle = { name: nm, prompt: { name: nm } };
    }
    await confirmPersonaImport({
        bundle,
        doImport: (flags) => installPersonaFromStore(slug, flags),
        onDone: () => { refreshInitData(); },
    });
}


/* ── Routing + state-update helpers ──────────────────────────────────────── */

async function ensureCategories() {
    if (categoriesCache[state.tab]) return;
    try {
        categoriesCache[state.tab] = await store().categories();
    } catch (e) {
        categoriesCache[state.tab] = [];
    }
}


async function navigateFromHash() {
    const parts = _readHashParts();
    const tab = parts[0] === 'personas' ? 'personas'
        : (parts.length === 0 || parts[0] === 'plugins') ? 'plugins'
            : null;
    if (tab === null) {
        // Unknown sub-route — redirect to plugins.
        _writeHash(['plugins']);
        return navigateFromHash();
    }
    state.tab = tab;
    container.querySelectorAll('.store-tab').forEach(b =>
        b.classList.toggle('active', b.dataset.tab === tab));
    await ensureCategories();
    renderSidebar();
    if (parts.length >= 2) renderDetail(parts[1]);
    else renderList();
}


function switchTab(which) {
    if (!STORES[which]) return;
    state.category = null;
    state.q = '';
    state.page = 1;
    state.detailSlug = null;
    _writeHash([which]);
    navigateFromHash();
}


function setCategory(slug) {
    state.category = slug || null;
    state.q = '';
    state.page = 1;
    renderSidebar();
    _writeHash([state.tab]);
    renderList();
}


function setSort(sort) {
    state.sort = sort;
    state.page = 1;
    renderList();
}


function setSearch(q) {
    state.q = q.trim();
    state.page = 1;
    state.category = null;
    renderSidebar();
    renderList();
}


function setPage(p) {
    state.page = Math.max(1, parseInt(p, 10) || 1);
    renderList();
    container.querySelector('.store-main')?.scrollTo({ top: 0, behavior: 'smooth' });
}


function openDetail(slug) {
    _writeHash([state.tab, slug]);
    renderDetail(slug);
}


function backToList() {
    state.detailSlug = null;
    _writeHash([state.tab]);
    renderList();
}


/* ── Event delegation ────────────────────────────────────────────────────── */

function bindEvents() {
    container.addEventListener('click', e => {
        // Tabs
        const tab = e.target.closest('.store-tab');
        if (tab && !tab.classList.contains('disabled')) {
            switchTab(tab.dataset.tab);
            return;
        }

        // Categories
        const cat = e.target.closest('.store-cat');
        if (cat) {
            setCategory(cat.dataset.cat || null);
            return;
        }

        // Card details / install / retry / page / back
        const action = e.target.closest('[data-action]');
        if (action) {
            const a = action.dataset.action;
            if (a === 'details') {
                openDetail(action.dataset.slug);
                return;
            }
            if (a === 'install') {
                if (state.tab === 'personas') confirmAndImportPersona(action.dataset.slug);
                else confirmAndInstall(action.dataset.slug, action);
                return;
            }
            if (a === 'retry') {
                if (state.detailSlug) renderDetail(state.detailSlug);
                else renderList();
                return;
            }
            if (a === 'page') {
                setPage(action.dataset.page);
                return;
            }
            if (a === 'back') {
                backToList();
                return;
            }
        }

        // Card body click (excluding buttons + links) → details
        const card = e.target.closest('.store-card');
        if (card && !e.target.closest('button, a')) {
            openDetail(card.dataset.slug);
        }
    });

    container.addEventListener('input', e => {
        if (e.target.classList.contains('store-search')) {
            clearTimeout(searchTimeout);
            const q = e.target.value;
            searchTimeout = setTimeout(() => setSearch(q), 800);
        }
    });

    container.addEventListener('change', e => {
        if (e.target.classList.contains('store-sort')) {
            setSort(e.target.value);
        }
    });

    // Hash changes (from anywhere — back/forward, deep links, etc.)
    window.addEventListener('hashchange', () => {
        if (location.hash.startsWith('#store')) navigateFromHash();
    });
}


/* ── View Module ─────────────────────────────────────────────────────────── */

export default {
    async init(el) {
        container = el;
        renderShell();
        bindEvents();
    },

    async show() {
        // Status check first — if disabled, render disabled state.
        if (!state.storeStatus) {
            try {
                state.storeStatus = await getStoreStatus();
            } catch (e) {
                state.storeStatus = { enabled: false };
            }
        }
        if (!state.storeStatus?.enabled) {
            renderMain(`
                <div class="store-empty">
                    <h2>The store is disabled.</h2>
                    <p>Enable it in Settings to browse community plugins.</p>
                </div>
            `);
            return;
        }

        // navigateFromHash loads the active tab's categories + renders sidebar.
        navigateFromHash();
    },

    hide() {
        // Nothing to teardown — DOM is rebuilt on each show().
    },
};
