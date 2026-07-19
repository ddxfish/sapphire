// views/palace/knowledge.js - Mind › Knowledge = the LIBRARY (v3, 2026-07-17).
// Spec: tmp/knowledge-layer.md. Category → Topic drawers with descriptions
// (descriptions are retrieval surface — hers AND yours), documents as
// first-class things: typed add (Note/File/Bulk with automatic importance),
// background import with live progress chips, a reader, byte-exact
// downloads. Spacious like Goals v2 — air over density.
import { renderSectionHeader, bindSectionHeader } from '../../shared/section-header.js';
import { helpPills } from '../../features/video-link.js';
import { renderScopeSidebar, bindScopeSidebar } from '../../shared/scope-sidebar.js';
import { listScopes } from '../../shared/scope-api.js';
import { csrfHeaders, escHtml, escAttr, timeAgo, scopeForChatTab, subscribeMindDomain } from '../../shared/mind-common.js';
import { showModal, showConfirm } from '../../shared/modal.js';
import * as ui from '../../ui.js';
import { API, PALACE_TABS, refreshPalaceTabs, SCOPE_ENDPOINT, palaceGet, palaceSend, describeScopeForDelete } from './common.js';

const SCOPE_KEY = 'memory_scope';
const DOMAIN = 'knowledge';

let container = null;
let scope = 'default';
let scopes = [];
let unsub = null;
let _catalog = null;
let _folders = [];
let _reader = null;        // doc id open in the reader, null = shelf view
let _filter = '';
let _pollTimer = null;

export default {
    init(el) { container = el; },
    async show() {
        await refreshPalaceTabs();
        if (!unsub) unsub = subscribeMindDomain(DOMAIN, () => scope, () => container?.offsetParent !== null, renderShelf);
        if (window._mindScope) { scope = window._mindScope; delete window._mindScope; }
        else { const s = await scopeForChatTab(SCOPE_KEY); if (s) scope = s; }
        delete window._mindTab;
        scopes = await listScopes(SCOPE_ENDPOINT);
        _reader = null;
        render();
    },
    hide() {
        if (unsub) { unsub(); unsub = null; }
        stopPolling();
        closeLightbox();
    }
};

function content() { return container?.querySelector('#pal-lib-content'); }

function render() {
    if (!container) return;
    container.innerHTML = `
        ${renderSectionHeader({ tabs: PALACE_TABS, active: 'knowledge', help: helpPills('Knowledge', { doc: 'MEMORY.md', inline: true }), status: '\u{1F3DB}️ Mind Palace — the Library (L3). Books, notes, and reference docs — hers to search, read, and shelve.' })}
        <div class="two-panel">
            ${renderScopeSidebar(scopes, scope)}
            <div class="panel-right">
                <div class="view-body view-scroll" id="pal-lib-content"></div>
            </div>
        </div>`;
    bindSectionHeader(container);
    bindScopeSidebar(container, {
        describeScope: describeScopeForDelete,
        onScopeChange: (s) => { scope = s; _reader = null; _filter = ''; render(); },
        onChanged: async (s) => { scope = s || 'default'; _reader = null; _filter = ''; scopes = await listScopes(SCOPE_ENDPOINT); render(); },
    });
    renderShelf();
}

// ─── Shelf (catalog) ─────────────────────────────────────────────────────────

const KIND_ICON = { note: '\u{1F4DD}', article: '\u{1F4F0}', book: '\u{1F4DA}', reference: '\u{1F4C4}', image: '\u{1F5BC}\u{FE0F}' };
const IMP_LABEL = { high: '\u{1F53A} high', med: '● med', low: '○ low' };
let _kind = '';            // '' all · 'note' · 'file' (article/book/ref) · 'image'

function jobFor(docId) {
    return (_catalog?.jobs || []).find(j => j.doc_id === docId);
}

function matchesFilter(d) {
    if (_kind === 'image' && d.kind !== 'image') return false;
    if (_kind === 'note' && d.kind !== 'note') return false;
    if (_kind === 'file' && (d.kind === 'note' || d.kind === 'image')) return false;
    if (!_filter) return true;
    const hay = [d.title, d.author, d.description,
                 ...(d.image?.people || []), d.image?.place]
        .filter(Boolean).join(' ').toLowerCase();
    return hay.includes(_filter);
}

function imageTile(d) {
    if (!matchesFilter(d)) return '';
    const job = jobFor(d.id);
    const busy = job && job.state !== 'error' ? '<span class="plib-tile-busy">…</span>' : '';
    return `
        <div class="plib-tile ${d.status === 'missing' ? 'plib-missing' : ''}" data-id="${d.id}" title="${escAttr(d.title)}${d.image?.place ? ' — ' + escAttr(d.image.place) : ''}${d.status === 'missing' ? ' (missing from folder)' : ''}">
            <img src="${API}/library/documents/${d.id}/thumb?scope=${encodeURIComponent(scope)}" loading="lazy" alt="${escAttr(d.title)}">
            <span class="plib-tile-title">${escHtml(d.title)}</span>${busy}
        </div>`;
}

function imageGrid(docs) {
    const tiles = (docs || []).filter(d => d.kind === 'image').map(imageTile).join('');
    return tiles ? `<div class="plib-grid">${tiles}</div>` : '';
}

function docRow(d) {
    if (d.kind === 'image') return '';           // images render as tiles
    if (!matchesFilter(d)) return '';
    const job = jobFor(d.id);
    let status = '';
    if (job && job.state === 'error') {
        status = `<span class="plib-chip plib-chip-err" title="${escAttr(job.error || '')}">import failed</span>`;
    } else if (job) {
        const pct = job.total ? Math.round(100 * (job.cursor || 0) / job.total) : 0;
        status = `<span class="plib-chip plib-chip-busy">importing… ${pct}%</span>`;
    } else if (d.status === 'missing') {
        status = `<span class="plib-chip plib-chip-err" title="File missing from its watched folder — revives if it returns">missing</span>`;
    }
    return `
        <div class="plib-doc ${d.status === 'missing' ? 'plib-missing' : ''}" data-id="${d.id}">
            <span class="plib-doc-icon">${KIND_ICON[d.kind] || '\u{1F4C4}'}</span>
            <div class="plib-doc-main">
                <span class="plib-doc-title" title="Open in the reader">${escHtml(d.title)}</span>
                ${d.author ? `<span class="plib-doc-author">${escHtml(d.author)}</span>` : ''}
                ${d.description ? `<div class="plib-doc-desc">${escHtml(d.description)}</div>` : ''}
            </div>
            <span class="plib-chip">${escHtml(d.kind)}</span>
            <span class="plib-chip">${IMP_LABEL[d.importance] || escHtml(d.importance)}</span>
            ${d.sections ? `<span class="plib-chip">${d.sections}§</span>` : ''}
            ${status}
            <span class="plib-doc-age">${escHtml(timeAgo(d.updated))}</span>
            <button class="mind-btn-sm plib-doc-edit" title="Edit">✎</button>
            <button class="mind-btn-sm plib-doc-dl" title="Download">⬇</button>
            <button class="mind-btn-sm plib-doc-del" title="Delete">✕</button>
        </div>`;
}

function collectionBlock(c, isTopic = false) {
    const docs = (c.documents || []).map(docRow).join('');
    const topics = (c.topics || []).map(t => collectionBlock(t, true)).join('');
    const grid = imageGrid(c.documents);
    // Filtering hides drawers with nothing to show; unfiltered browsing
    // still shows empty drawers (they're organization, not results).
    if ((_kind || _filter) && !docs && !topics && !grid) return '';
    return `
        <div class="plib-coll ${isTopic ? 'plib-topic' : 'plib-cat'}" data-cid="${c.id}">
            <div class="plib-coll-head">
                <span class="plib-coll-name">${isTopic ? '▹' : '▸'} ${escHtml(c.name)}</span>
                ${c.description ? `<span class="plib-coll-desc">— ${escHtml(c.description)}</span>` : ''}
                ${!isTopic ? `<button class="mind-btn-sm plib-add-topic" title="Add topic">+ topic</button>` : ''}
                <button class="mind-btn-sm plib-coll-edit" title="Edit">✎</button>
                <button class="mind-btn-sm plib-coll-del" title="Delete (documents go to Unfiled)">✕</button>
            </div>
            ${topics}
            ${docs ? `<div class="plib-docs">${docs}</div>` : ''}
            ${grid}
        </div>`;
}

async function renderShelf() {
    const el = content();
    if (!el) return;
    if (_reader !== null) return renderReader();
    try {
        [_catalog, _folders] = await Promise.all([
            palaceGet(`library/catalog?scope=${encodeURIComponent(scope)}`),
            palaceGet(`library/folders?scope=${encodeURIComponent(scope)}`).then(r => r.folders || []),
        ]);
    } catch (e) {
        el.innerHTML = `<div class="mind-empty">Failed to load: ${escHtml(e.message)}</div>`;
        return;
    }
    const cats = _catalog.categories || [];
    const unfiled = (_catalog.unfiled || []).map(docRow).join('');
    const unfiledGrid = imageGrid(_catalog.unfiled);
    el.innerHTML = `
        <div class="ui-rows">
            <div class="ui-row">
                <input type="search" id="plib-filter" class="palace-search" placeholder="Filter by title, author, place, or people…" value="${escAttr(_filter)}">
                <span class="palace-count">${_catalog.total} documents</span>
            </div>
            <div class="ui-row" id="plib-kind-pills">
                <button class="ui-pill ${_kind === '' ? 'ui-pill-on' : ''}" data-kind="">All</button>
                <button class="ui-pill ${_kind === 'note' ? 'ui-pill-on' : ''}" data-kind="note">\u{1F4DD} Notes</button>
                <button class="ui-pill ${_kind === 'file' ? 'ui-pill-on' : ''}" data-kind="file">\u{1F4C4} Files</button>
                <button class="ui-pill ${_kind === 'image' ? 'ui-pill-on' : ''}" data-kind="image">\u{1F5BC}\u{FE0F} Images</button>
            </div>
            <div class="ui-row">
                <button class="mind-btn" id="plib-add-note">+ \u{1F4DD} Note</button>
                <button class="mind-btn" id="plib-add-file">+ \u{1F4C4} File</button>
                <button class="mind-btn" id="plib-add-bulk">+ \u{1F4DA} Bulk</button>
                <button class="mind-btn" id="plib-add-images">+ \u{1F5BC}\u{FE0F} Images</button>
                <button class="mind-btn" id="plib-add-folder">+ \u{1F4C2} Folder</button>
                <button class="mind-btn" id="plib-add-cat">+ Category</button>
                <span class="ui-row-end">
                    <button class="mind-btn" id="plib-export" title="Download this scope's whole library as a zip (files + annotations; re-importable into any scope)">⇓ Export</button>
                    <button class="mind-btn" id="plib-import" title="Import a library export zip INTO this scope — re-imports skip what's already here">⇑ Import</button>
                </span>
            </div>
        </div>
        ${renderFolders()}
        <div class="plib-shelf">
            ${cats.map(c => collectionBlock(c)).join('')}
            ${unfiled || unfiledGrid ? `<div class="plib-coll plib-cat"><div class="plib-coll-head"><span class="plib-coll-name">▸ (unfiled)</span></div>${unfiled ? `<div class="plib-docs">${unfiled}</div>` : ''}${unfiledGrid}</div>` : ''}
            ${!_catalog.total && !cats.length ? `<div class="mind-empty">The library is empty — add a note, upload a file, or she shelves things herself with save_memory layer='knowledge'.</div>` : ''}
        </div>
        <input type="file" id="plib-file-input" hidden>
        <input type="file" id="plib-bulk-input" hidden multiple>
        <input type="file" id="plib-images-input" hidden multiple accept="image/*,.heic,.heif">
        <input type="file" id="plib-zip-input" hidden accept=".zip">
    `;
    el.querySelector('#plib-export')?.addEventListener('click', () => {
        location.href = `${API}/library/export?scope=${encodeURIComponent(scope)}`;
    });
    const zipInput = el.querySelector('#plib-zip-input');
    el.querySelector('#plib-import')?.addEventListener('click', () => {
        zipInput.value = '';
        zipInput.click();
    });
    zipInput?.addEventListener('change', async () => {
        const f = zipInput.files[0];
        if (!f) return;
        ui.showToast(`Importing into '${scope}'…`, 'info');
        const fd = new FormData();
        fd.append('file', f);
        fd.append('scope', scope);
        try {
            const r = await fetch(`${API}/library/import-zip`, {
                method: 'POST', credentials: 'same-origin',
                headers: csrfHeaders(), body: fd,
            });
            const data = await r.json().catch(() => ({}));
            if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
            const rep = data.report || {};
            const bits = [`${rep.imported ?? 0} imported`,
                          `${rep.skipped ?? 0} skipped`];
            if (rep.failed) bits.push(`${rep.failed} failed`);
            if (rep.watch_attached) bits.push(`${rep.watch_attached} folder(s) attached`);
            if ((rep.watch_missing || []).length) bits.push(`${rep.watch_missing.length} folder path(s) missing here`);
            ui.showToast(`Library import: ${bits.join(' · ')} — embedding continues in background`, rep.failed ? 'warning' : 'success');
            render();
        } catch (e) { ui.showToast(`Import failed: ${e.message}`, 'error'); }
    });
    el.querySelector('#plib-add-note')?.addEventListener('click', noteModal);
    el.querySelector('#plib-add-cat')?.addEventListener('click', () => collectionModal(null, null));
    el.querySelector('#plib-add-folder')?.addEventListener('click', folderModal);
    bindFolders(el);
    const fileInput = el.querySelector('#plib-file-input');
    const bulkInput = el.querySelector('#plib-bulk-input');
    const imagesInput = el.querySelector('#plib-images-input');
    el.querySelector('#plib-add-file')?.addEventListener('click', () => { fileInput.value = ''; fileInput.click(); });
    el.querySelector('#plib-add-bulk')?.addEventListener('click', () => { bulkInput.value = ''; bulkInput.click(); });
    el.querySelector('#plib-add-images')?.addEventListener('click', () => { imagesInput.value = ''; imagesInput.click(); });
    fileInput?.addEventListener('change', () => { if (fileInput.files[0]) fileModal(fileInput.files[0]); });
    bulkInput?.addEventListener('change', () => { if (bulkInput.files.length) bulkModal([...bulkInput.files]); });
    imagesInput?.addEventListener('change', () => { if (imagesInput.files.length) imagesModal([...imagesInput.files]); });
    const filter = el.querySelector('#plib-filter');
    filter?.addEventListener('input', () => { _filter = filter.value.trim().toLowerCase(); renderShelf(); });
    el.querySelectorAll('#plib-kind-pills [data-kind]').forEach(btn => {
        btn.addEventListener('click', () => { _kind = btn.dataset.kind; renderShelf(); });
    });
    bindShelf(el);
    syncPolling();
}

function bindShelf(el) {
    el.querySelectorAll('.plib-coll').forEach(block => {
        const cid = parseInt(block.dataset.cid);
        if (!cid) return;
        const c = findCollection(cid);
        block.querySelector(':scope > .plib-coll-head .plib-add-topic')?.addEventListener('click', () => collectionModal(null, cid));
        block.querySelector(':scope > .plib-coll-head .plib-coll-edit')?.addEventListener('click', () => collectionModal(c, c?.parent_id));
        block.querySelector(':scope > .plib-coll-head .plib-coll-del')?.addEventListener('click', () => {
            showConfirm(`Delete "${c?.name}"? Its documents move to Unfiled.`, async () => {
                try { await palaceSend(`library/collections/${cid}?scope=${encodeURIComponent(scope)}`, 'DELETE'); renderShelf(); }
                catch (e) { ui.showToast(e.message, 'error'); }
            }, { title: 'Delete drawer', saveLabel: 'Delete' });
        });
    });
    el.querySelectorAll('.plib-tile').forEach(tile => {
        tile.addEventListener('click', () => openLightbox(parseInt(tile.dataset.id)));
    });
    el.querySelectorAll('.plib-doc').forEach(row => {
        const did = parseInt(row.dataset.id);
        const d = findDoc(did);
        row.querySelector('.plib-doc-title')?.addEventListener('click', () => { _reader = did; renderReader(); });
        row.querySelector('.plib-doc-edit')?.addEventListener('click', () => docModal(d));
        row.querySelector('.plib-doc-dl')?.addEventListener('click', () => {
            window.open(`${API}/library/documents/${did}/download?scope=${encodeURIComponent(scope)}&which=original`, '_blank');
        });
        row.querySelector('.plib-doc-del')?.addEventListener('click', () => {
            showConfirm(`Delete "${d?.title}"? The document, its sections, and its files all go.`, async () => {
                try { await palaceSend(`library/documents/${did}?scope=${encodeURIComponent(scope)}`, 'DELETE'); ui.showToast('Deleted', 'success'); renderShelf(); }
                catch (e) { ui.showToast(e.message, 'error'); }
            }, { title: 'Delete document', saveLabel: 'Delete' });
        });
    });
}

function findCollection(cid) {
    for (const c of _catalog?.categories || []) {
        if (c.id === cid) return { ...c, parent_id: null };
        for (const t of c.topics || []) if (t.id === cid) return { ...t, parent_id: c.id };
    }
    return null;
}

function findDoc(did) {
    const all = [...(_catalog?.unfiled || [])];
    for (const c of _catalog?.categories || []) {
        all.push(...(c.documents || []));
        for (const t of c.topics || []) all.push(...(t.documents || []));
    }
    return all.find(d => d.id === did);
}

function docCollectionId(did) {
    for (const c of _catalog?.categories || []) {
        if ((c.documents || []).some(d => d.id === did)) return c.id;
        for (const t of c.topics || []) if ((t.documents || []).some(d => d.id === did)) return t.id;
    }
    return null;
}

// ─── Watched folders — index in place, the folder is canonical ───────────────

function renderFolders() {
    if (!_folders.length) return '';
    const rows = _folders.map(f => `
        <div class="plib-folder" data-fid="${f.id}">
            <span class="plib-folder-path">\u{1F4C2} ${escHtml(f.path)}</span>
            <span class="plib-chip">${f.total} indexed</span>
            ${f.missing ? `<span class="plib-chip plib-chip-err">${f.missing} missing</span>` : ''}
            <span class="plib-doc-age">${f.last_scan ? 'scanned ' + escHtml(timeAgo(f.last_scan)) : 'scanning…'}</span>
            <button class="mind-btn-sm plib-folder-rescan" title="Rescan now">↻</button>
            <button class="mind-btn-sm plib-folder-del" title="Stop watching">✕</button>
        </div>`).join('');
    return `<div class="plib-folders">${rows}</div>`;
}

function bindFolders(el) {
    el.querySelectorAll('.plib-folder').forEach(row => {
        const fid = parseInt(row.dataset.fid);
        const f = _folders.find(x => x.id === fid);
        row.querySelector('.plib-folder-rescan')?.addEventListener('click', async () => {
            try {
                const r = await palaceSend(`library/folders/${fid}/rescan`, 'POST', { scope });
                const rep = r.report || {};
                ui.showToast(`Scan: +${rep.added || 0} new, ${rep.changed || 0} changed, ${rep.missing || 0} missing, ${rep.revived || 0} back`, 'success');
                renderShelf();
            } catch (e) { ui.showToast(e.message, 'error'); }
        });
        row.querySelector('.plib-folder-del')?.addEventListener('click', () => {
            showConfirm(`Stop watching "${f?.path}"? Indexed entries and her annotations go; the actual files in the folder are untouched.`, async () => {
                try { await palaceSend(`library/folders/${fid}?scope=${encodeURIComponent(scope)}`, 'DELETE'); renderShelf(); }
                catch (e) { ui.showToast(e.message, 'error'); }
            }, { title: 'Stop watching folder', saveLabel: 'Stop watching' });
        });
    });
}

function folderModal() {
    showModal('Watch a folder', [
        { type: 'html', value: `<p style="margin:0;color:var(--text-secondary);font-size:13px;">Files are indexed IN PLACE — nothing is copied. New files become findable on each scan; images get thumbnails and vision search. Supported: images, txt, md, pdf, epub.</p>` },
        { id: 'path', label: 'Absolute folder path on this machine *', type: 'text' },
        collectionOptions(null),
    ], async data => {
        if (!data.path?.trim()) { ui.showToast('Path is required', 'error'); return; }
        try {
            await palaceSend('library/folders', 'POST', {
                scope, path: data.path.trim(),
                collection_id: data.collection_id ? parseInt(data.collection_id) : null,
            });
            ui.showToast('Watching — first scan is running', 'success');
            setTimeout(renderShelf, 1200);
        } catch (e) { ui.showToast(e.message, 'error'); }
    }, { saveLabel: 'Watch it' });
}

// ─── Import progress polling ─────────────────────────────────────────────────

function syncPolling() {
    const busy = (_catalog?.jobs || []).some(j => j.state === 'pending');
    if (busy && !_pollTimer) {
        _pollTimer = setInterval(() => {
            if (container?.offsetParent === null || _reader !== null) return;
            renderShelf();
        }, 2500);
    } else if (!busy) stopPolling();
}

function stopPolling() {
    if (_pollTimer) { clearInterval(_pollTimer); _pollTimer = null; }
}

// ─── Reader ──────────────────────────────────────────────────────────────────

async function renderReader() {
    const el = content();
    if (!el || _reader === null) return;
    let data;
    try {
        data = await palaceGet(`library/documents/${_reader}?scope=${encodeURIComponent(scope)}`);
    } catch (e) {
        ui.showToast(e.message, 'error');
        _reader = null;
        return renderShelf();
    }
    const d = findDoc(_reader) || {};
    el.innerHTML = `
        <div class="mind-toolbar plib-actions">
            <button class="mind-btn" id="plib-back">← Library</button>
            <span class="plib-reader-title">${KIND_ICON[d.kind] || '\u{1F4D6}'} ${escHtml(data.title)}</span>
            ${d.author ? `<span class="plib-doc-author">${escHtml(d.author)}</span>` : ''}
            <button class="mind-btn-sm" id="plib-r-dl">⬇ original</button>
            <button class="mind-btn-sm" id="plib-r-dlw">⬇ text</button>
        </div>
        <div class="plib-reader">${escHtml(data.text || '(no text)')}</div>`;
    el.querySelector('#plib-back')?.addEventListener('click', () => { _reader = null; renderShelf(); });
    el.querySelector('#plib-r-dl')?.addEventListener('click', () =>
        window.open(`${API}/library/documents/${_reader}/download?scope=${encodeURIComponent(scope)}&which=original`, '_blank'));
    el.querySelector('#plib-r-dlw')?.addEventListener('click', () =>
        window.open(`${API}/library/documents/${_reader}/download?scope=${encodeURIComponent(scope)}&which=working`, '_blank'));
}

// ─── Modals ──────────────────────────────────────────────────────────────────

function collectionOptions(selected) {
    const opts = [{ v: '', l: '(unfiled)' }];
    for (const c of _catalog?.categories || []) {
        opts.push({ v: String(c.id), l: c.name });
        for (const t of c.topics || []) opts.push({ v: String(t.id), l: `${c.name} › ${t.name}` });
    }
    return {
        id: 'collection_id', label: 'Shelf', type: 'select',
        options: opts.map(o => o.v), labels: opts.map(o => o.l),
        value: selected == null ? '' : String(selected),
    };
}

const IMP_FIELD = (v) => ({
    id: 'importance', label: 'Importance — gates how eagerly this surfaces', type: 'select',
    options: ['high', 'med', 'low'], labels: ['🔺 High', '● Med', '○ Low'], value: v,
});

function noteModal() {
    showModal('Add note', [
        { id: 'title', label: 'Title *', type: 'text' },
        { id: 'content', label: 'Content', type: 'textarea', rows: 8 },
        { id: 'description', label: 'Short description — helps her FIND it', type: 'text' },
        collectionOptions(null),
        IMP_FIELD('high'),
    ], async data => {
        if (!data.title?.trim() || !data.content?.trim()) { ui.showToast('Title and content required', 'error'); return; }
        try {
            await palaceSend('library/notes', 'POST', {
                scope, title: data.title.trim(), content: data.content,
                description: data.description.trim() || null,
                collection_id: data.collection_id ? parseInt(data.collection_id) : null,
                importance: data.importance,
            });
            ui.showToast('Shelved', 'success');
            renderShelf();
        } catch (e) { ui.showToast(`Save failed: ${e.message}`, 'error'); }
    }, { saveLabel: 'Shelve it' });
}

const EXT_KIND = { '.epub': 'book', '.pdf': 'reference' };
const KIND_FIELD = (v) => ({
    id: 'kind', label: 'Kind — sets how it chunks for search', type: 'select',
    options: ['note', 'article', 'book', 'reference'],
    labels: ['📝 Note', '📰 Article', '📚 Book', '📄 Reference'], value: v,
});

function fileModal(file) {
    const ext = (file.name.match(/\.[^.]+$/) || [''])[0].toLowerCase();
    showModal(`Import — ${file.name}`, [
        { id: 'title', label: 'Title (blank = from file metadata)', type: 'text' },
        KIND_FIELD(EXT_KIND[ext] || 'article'),
        { id: 'description', label: 'Short description — helps her FIND it', type: 'text' },
        collectionOptions(null),
        IMP_FIELD('med'),
    ], data => uploadFiles([file], data, data.title.trim() || null),
       { saveLabel: 'Import' });
}

function bulkModal(files) {
    showModal(`Bulk import — ${files.length} files`, [
        { type: 'html', value: `<p style="margin:0;color:var(--text-secondary);font-size:13px;">${files.map(f => escHtml(f.name)).join('<br>')}</p>` },
        collectionOptions(null),
        IMP_FIELD('low'),
    ], data => uploadFiles(files, data, null), { saveLabel: `Import ${files.length}` });
}

function imagesModal(files) {
    showModal(`Import images — ${files.length}`, [
        { type: 'html', value: `<p style="margin:0;color:var(--text-secondary);font-size:13px;">${files.map(f => escHtml(f.name)).join('<br>')}</p>` },
        { id: 'description', label: 'Short description — applies to all, helps her FIND them', type: 'text' },
        collectionOptions(null),
        IMP_FIELD('med'),
    ], data => uploadFiles(files, data, null), { saveLabel: `Import ${files.length}` });
}

// ─── Lightbox ────────────────────────────────────────────────────────────────

function closeLightbox() {
    document.getElementById('plib-lightbox')?.remove();
    document.removeEventListener('keydown', lbEsc);
}

function lbEsc(e) { if (e.key === 'Escape') closeLightbox(); }

function openLightbox(did) {
    const d = findDoc(did);
    if (!d) return;
    closeLightbox();
    const m = d.image || {};
    const facts = [
        m.taken ? `\u{1F4C5} ${escHtml(m.taken)}` : '',
        m.camera ? `\u{1F4F7} ${escHtml(m.camera)}` : '',
        m.place ? `\u{1F4CD} ${escHtml(m.place)}` : (m.lat != null ? `\u{1F4CD} ${m.lat}, ${m.lon}` : ''),
        m.width ? `${m.width}×${m.height}` : '',
    ].filter(Boolean).map(f => `<span class="plib-chip">${f}</span>`).join('');
    const box = document.createElement('div');
    box.className = 'plib-lightbox';
    box.id = 'plib-lightbox';
    box.innerHTML = `
        <div class="plib-lb-img"><img src="${API}/library/documents/${did}/file?scope=${encodeURIComponent(scope)}" alt="${escAttr(d.title)}"></div>
        <div class="plib-lb-side">
            <div class="plib-lb-head">
                <span class="plib-lb-title">${escHtml(d.title)}</span>
                <button class="mind-btn-sm" id="plib-lb-close" title="Close">✕</button>
            </div>
            ${d.description ? `<div class="plib-doc-desc">${escHtml(d.description)}</div>` : ''}
            <div class="plib-lb-facts">${facts}</div>
            <label class="plib-lb-label">People — names that match entities join her graph</label>
            <input type="text" id="plib-lb-people" class="palace-search" placeholder="comma-separated" value="${escAttr((m.people || []).join(', '))}">
            <label class="plib-lb-label">Notes — searchable the moment they save</label>
            <textarea id="plib-lb-notes" class="plib-lb-notes" rows="4">${escHtml(m.notes || '')}</textarea>
            <button class="mind-btn" id="plib-lb-save">Save annotations</button>
            <div class="plib-lb-actions">
                <button class="mind-btn-sm" id="plib-lb-dl" title="Byte-exact upload">⬇ original</button>
                <button class="mind-btn-sm" id="plib-lb-dla" title="Original pixels + your annotations in the metadata">⬇ annotated</button>
                <button class="mind-btn-sm" id="plib-lb-edit">✎ edit</button>
                <button class="mind-btn-sm" id="plib-lb-del">✕ delete</button>
            </div>
        </div>`;
    box.addEventListener('click', e => { if (e.target === box) closeLightbox(); });
    box.querySelector('#plib-lb-close')?.addEventListener('click', closeLightbox);
    box.querySelector('#plib-lb-save')?.addEventListener('click', async () => {
        const people = box.querySelector('#plib-lb-people').value
            .split(',').map(s => s.trim()).filter(Boolean);
        const notes = box.querySelector('#plib-lb-notes').value;
        try {
            await palaceSend(`library/documents/${did}/image-meta`, 'PUT', { scope, people, notes });
            ui.showToast('Annotations saved', 'success');
            closeLightbox();
            renderShelf();
        } catch (e) { ui.showToast(e.message, 'error'); }
    });
    box.querySelector('#plib-lb-dl')?.addEventListener('click', () =>
        window.open(`${API}/library/documents/${did}/download?scope=${encodeURIComponent(scope)}&which=original`, '_blank'));
    box.querySelector('#plib-lb-dla')?.addEventListener('click', () =>
        window.open(`${API}/library/documents/${did}/download?scope=${encodeURIComponent(scope)}&which=annotated`, '_blank'));
    box.querySelector('#plib-lb-edit')?.addEventListener('click', () => { closeLightbox(); docModal(d); });
    box.querySelector('#plib-lb-del')?.addEventListener('click', () => {
        closeLightbox();
        showConfirm(`Delete "${d.title}"? The image and its metadata all go.`, async () => {
            try { await palaceSend(`library/documents/${did}?scope=${encodeURIComponent(scope)}`, 'DELETE'); ui.showToast('Deleted', 'success'); renderShelf(); }
            catch (e) { ui.showToast(e.message, 'error'); }
        }, { title: 'Delete image', saveLabel: 'Delete' });
    });
    document.body.appendChild(box);
    document.addEventListener('keydown', lbEsc);
}

async function uploadFiles(files, data, title) {
    const form = new FormData();
    for (const f of files) form.append('file', f);
    form.append('scope', scope);
    if (title) form.append('title', title);
    if (data.kind) form.append('kind', data.kind);
    if (data.description?.trim()) form.append('description', data.description.trim());
    if (data.collection_id) form.append('collection_id', data.collection_id);
    form.append('importance', data.importance);
    try {
        const r = await fetch(`${API}/library/upload`, {
            method: 'POST', credentials: 'same-origin', headers: csrfHeaders(), body: form,
        });
        const res = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(res.error || res.detail || `HTTP ${r.status}`);
        const n = (res.imported || []).length;
        if (n) ui.showToast(`Importing ${n} file${n !== 1 ? 's' : ''} in the background`, 'success');
        for (const ref of res.refused || []) ui.showToast(`${ref.file}: ${ref.error}`, 'error');
        renderShelf();
    } catch (e) { ui.showToast(`Upload failed: ${e.message}`, 'error'); }
}

function collectionModal(existing, parentId) {
    const isTopic = parentId != null;
    showModal(existing ? `Edit ${isTopic ? 'topic' : 'category'}` : (isTopic ? 'Add topic' : 'Add category'), [
        { id: 'name', label: 'Name *', type: 'text', value: existing?.name || '' },
        { id: 'description', label: 'Short description — she browses by these', type: 'text', value: existing?.description || '' },
    ], async data => {
        if (!data.name?.trim()) { ui.showToast('Name is required', 'error'); return; }
        try {
            if (existing) {
                await palaceSend(`library/collections/${existing.id}`, 'PUT',
                    { scope, name: data.name.trim(), description: data.description.trim() });
            } else {
                await palaceSend('library/collections', 'POST',
                    { scope, name: data.name.trim(), description: data.description.trim() || null, parent_id: parentId });
            }
            renderShelf();
        } catch (e) { ui.showToast(e.message, 'error'); }
    }, { saveLabel: existing ? 'Save' : 'Create' });
}

function docModal(d) {
    if (!d) return;
    const isImage = d.kind === 'image';
    showModal(`Edit — ${d.title}`, [
        { id: 'title', label: 'Title *', type: 'text', value: d.title },
        { id: 'author', label: 'Author', type: 'text', value: d.author || '' },
        { id: 'description', label: 'Short description — helps her FIND it', type: 'text', value: d.description || '' },
        ...(isImage ? [] : [KIND_FIELD(d.kind)]),
        collectionOptions(docCollectionId(d.id)),
        IMP_FIELD(d.importance),
    ], async data => {
        if (!data.title?.trim()) { ui.showToast('Title is required', 'error'); return; }
        try {
            await palaceSend(`library/documents/${d.id}`, 'PUT', {
                scope, title: data.title.trim(), author: data.author.trim(),
                description: data.description.trim(),
                ...(isImage ? {} : { kind: data.kind }),
                importance: data.importance,
                collection_id: data.collection_id ? parseInt(data.collection_id) : null,
            });
            ui.showToast('Updated', 'success');
            renderShelf();
        } catch (e) { ui.showToast(e.message, 'error'); }
    }, { saveLabel: 'Save' });
}
