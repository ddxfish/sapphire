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

const KIND_ICON = { note: '\u{1F4DD}', article: '\u{1F4F0}', book: '\u{1F4DA}', reference: '\u{1F4C4}' };
const IMP_LABEL = { high: '\u{1F53A} high', med: '● med', low: '○ low' };

function jobFor(docId) {
    return (_catalog?.jobs || []).find(j => j.doc_id === docId);
}

function docRow(d) {
    if (_filter && !d.title.toLowerCase().includes(_filter) &&
        !(d.author || '').toLowerCase().includes(_filter)) return '';
    const job = jobFor(d.id);
    let status = '';
    if (job && job.state === 'error') {
        status = `<span class="plib-chip plib-chip-err" title="${escAttr(job.error || '')}">import failed</span>`;
    } else if (job) {
        const pct = job.total ? Math.round(100 * (job.cursor || 0) / job.total) : 0;
        status = `<span class="plib-chip plib-chip-busy">importing… ${pct}%</span>`;
    }
    return `
        <div class="plib-doc" data-id="${d.id}">
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
        </div>`;
}

async function renderShelf() {
    const el = content();
    if (!el) return;
    if (_reader !== null) return renderReader();
    try {
        _catalog = await palaceGet(`library/catalog?scope=${encodeURIComponent(scope)}`);
    } catch (e) {
        el.innerHTML = `<div class="mind-empty">Failed to load: ${escHtml(e.message)}</div>`;
        return;
    }
    const cats = _catalog.categories || [];
    const unfiled = (_catalog.unfiled || []).map(docRow).join('');
    el.innerHTML = `
        <div class="mind-toolbar plib-actions">
            <button class="mind-btn" id="plib-add-note">\u{1F4DD} Note</button>
            <button class="mind-btn" id="plib-add-file">\u{1F4C4} File</button>
            <button class="mind-btn" id="plib-add-bulk">\u{1F4DA} Bulk</button>
            <button class="mind-btn" id="plib-add-cat">+ Category</button>
            <span class="palace-count">${_catalog.total} documents</span>
        </div>
        <div class="pgoal-filters">
            <input type="search" id="plib-filter" class="palace-search" placeholder="Filter by title or author…" value="${escAttr(_filter)}">
        </div>
        <div class="plib-shelf">
            ${cats.map(c => collectionBlock(c)).join('')}
            ${unfiled ? `<div class="plib-coll plib-cat"><div class="plib-coll-head"><span class="plib-coll-name">▸ (unfiled)</span></div><div class="plib-docs">${unfiled}</div></div>` : ''}
            ${!_catalog.total && !cats.length ? `<div class="mind-empty">The library is empty — add a note, upload a file, or she shelves things herself with save_memory layer='knowledge'.</div>` : ''}
        </div>
        <input type="file" id="plib-file-input" hidden>
        <input type="file" id="plib-bulk-input" hidden multiple>
    `;
    el.querySelector('#plib-add-note')?.addEventListener('click', noteModal);
    el.querySelector('#plib-add-cat')?.addEventListener('click', () => collectionModal(null, null));
    const fileInput = el.querySelector('#plib-file-input');
    const bulkInput = el.querySelector('#plib-bulk-input');
    el.querySelector('#plib-add-file')?.addEventListener('click', () => { fileInput.value = ''; fileInput.click(); });
    el.querySelector('#plib-add-bulk')?.addEventListener('click', () => { bulkInput.value = ''; bulkInput.click(); });
    fileInput?.addEventListener('change', () => { if (fileInput.files[0]) fileModal(fileInput.files[0]); });
    bulkInput?.addEventListener('change', () => { if (bulkInput.files.length) bulkModal([...bulkInput.files]); });
    const filter = el.querySelector('#plib-filter');
    filter?.addEventListener('input', () => { _filter = filter.value.trim().toLowerCase(); renderShelf(); });
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
    showModal(`Edit — ${d.title}`, [
        { id: 'title', label: 'Title *', type: 'text', value: d.title },
        { id: 'author', label: 'Author', type: 'text', value: d.author || '' },
        { id: 'description', label: 'Short description — helps her FIND it', type: 'text', value: d.description || '' },
        KIND_FIELD(d.kind),
        collectionOptions(docCollectionId(d.id)),
        IMP_FIELD(d.importance),
    ], async data => {
        if (!data.title?.trim()) { ui.showToast('Title is required', 'error'); return; }
        try {
            await palaceSend(`library/documents/${d.id}`, 'PUT', {
                scope, title: data.title.trim(), author: data.author.trim(),
                description: data.description.trim(), kind: data.kind,
                importance: data.importance,
                collection_id: data.collection_id ? parseInt(data.collection_id) : null,
            });
            ui.showToast('Updated', 'success');
            renderShelf();
        } catch (e) { ui.showToast(e.message, 'error'); }
    }, { saveLabel: 'Save' });
}
