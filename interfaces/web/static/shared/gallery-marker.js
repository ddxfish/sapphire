// shared/gallery-marker.js — the ONE gallery renderer for tool results (2026-09-09,
// image-tools rebuild). A tool that wants tiles under its result appends
//   <!--GALLERY:[ ... ]-->
// where each entry is a URL string (legacy) or {thumb, full, title, page} (v2,
// web_search_images). Both renderers — history (ui-parsing renderToolResult) and
// the live stream (ui-streaming doEndTool) — parse + build through here. Never
// hand-roll a gallery. Every tile: no-referrer, lazy, click → lightbox over the
// FULL urls; caption links to the source page.

export const GALLERY_RE = /<!--GALLERY:(\[[^\n]*\])-->[ \t]*\n?/;

export function normalizeEntries(raw) {
    if (!Array.isArray(raw)) return [];
    return raw.map(e => {
        if (typeof e === 'string') return e ? { thumb: e, full: e, title: '', page: '' } : null;
        if (e && typeof e === 'object' && (e.thumb || e.full)) {
            return { thumb: e.thumb || e.full, full: e.full || e.thumb,
                     title: typeof e.title === 'string' ? e.title : '',
                     page: typeof e.page === 'string' ? e.page : '' };
        }
        return null;
    }).filter(Boolean);
}

// → { entries, text } — text has the marker removed (it's the UI's, not the reader's).
export function parseGalleryMarker(text) {
    const src = text || '';
    const m = src.match(GALLERY_RE);
    if (!m) return { entries: [], text: src };
    let entries = [];
    try { entries = normalizeEntries(JSON.parse(m[1])); }
    catch (e) { console.warn('[Gallery] bad marker JSON:', e); }
    return { entries, text: src.replace(GALLERY_RE, '').trimEnd() };
}

function hostOf(u) {
    try { return new URL(u).hostname; } catch { return u; }
}

// entries → .image-gallery element (or null). openModal(fullSrc, fulls, idx).
export function buildGallery(entries, openModal) {
    if (!entries || !entries.length) return null;
    const fulls = entries.map(e => e.full);
    const gallery = document.createElement('div');
    gallery.className = 'image-gallery';
    entries.forEach((e, idx) => {
        const item = document.createElement('div');
        item.className = 'gallery-item';
        const img = document.createElement('img');
        img.className = 'chat-img';
        img.alt = e.title || '';
        img.loading = 'lazy';
        img.referrerPolicy = 'no-referrer';
        img.dataset.modalReady = 'true';          // wrapImageGalleries leaves it alone
        img.dataset.full = e.full;
        img.src = e.thumb;
        img.addEventListener('click', ev => {
            ev.stopPropagation();
            if (openModal) openModal(e.full, fulls, idx);
        });
        item.appendChild(img);
        if (e.title || e.page) {
            const cap = document.createElement(e.page ? 'a' : 'div');
            cap.className = 'gallery-caption';
            cap.textContent = e.title || hostOf(e.page);
            if (e.page) {
                cap.href = e.page;
                cap.target = '_blank';
                cap.rel = 'noopener noreferrer';
                cap.title = hostOf(e.page);
            }
            item.appendChild(cap);
        }
        gallery.appendChild(item);
    });
    return gallery;
}
