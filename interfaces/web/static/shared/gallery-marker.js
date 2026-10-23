// shared/gallery-marker.js — the ONE gallery renderer for tool results (2026-09-09,
// image-tools rebuild; v3 row 2026-09-10, image upgrade). A tool that wants tiles
// under its result appends ONE marker line:
//   v3  <!--GALLERY:{"title":"…","items":[{"handle":"img:ID","full":"…","title":"…","page":"…"}]}-->
//   v2  <!--GALLERY:[{thumb, full, title, page}, …]-->     (2026-09-09 web hits, proxied)
//   v1  <!--GALLERY:["url", …]-->                           (legacy)
// v3 tiles are OURS: `handle` is the same img: handle the model holds (stashed in
// tool_images → /api/tool-image/<id>: vault-aware, offline, survives a VM move).
// Tiles are numbered 1..N — the same numbers as her contact sheet, so "#3" means
// the same picture to both of you. `full` is the lightbox target (a proxied
// original for web hits); if it won't load, the lightbox falls back to the tile.
// Both renderers — history (ui-parsing renderToolResult) and the live stream
// (ui-streaming doEndTool) — parse + build through here. Never hand-roll a
// gallery. The marker is UI-only: strip_ui_markers drops it from the LLM copy.

export const GALLERY_RE = /<!--GALLERY:([\[{][^\n]*[\]}])-->[ \t]*\n?/;
const TOOL_IMAGE = '/api/tool-image/';

export function normalizeEntries(raw) {
    const list = Array.isArray(raw) ? raw
        : (raw && typeof raw === 'object' && Array.isArray(raw.items)) ? raw.items : [];
    return list.map(e => {
        if (typeof e === 'string') return e ? { thumb: e, full: e, handle: '', title: '', page: '' } : null;
        if (!e || typeof e !== 'object') return null;
        const handle = typeof e.handle === 'string' && e.handle.startsWith('img:') ? e.handle.slice(4) : '';
        const thumb = handle ? TOOL_IMAGE + encodeURIComponent(handle) : (e.thumb || e.full || '');
        if (!thumb) return null;
        return { thumb, full: e.full || thumb, handle,
                 title: typeof e.title === 'string' ? e.title : '',
                 page: typeof e.page === 'string' ? e.page : '' };
    }).filter(Boolean);
}

// → { entries, title, text } — text has the marker removed (it's the UI's, not the reader's).
export function parseGalleryMarker(text) {
    const src = text || '';
    const m = src.match(GALLERY_RE);
    if (!m) return { entries: [], title: '', text: src };
    let entries = [], title = '';
    try {
        const parsed = JSON.parse(m[1]);
        entries = normalizeEntries(parsed);
        if (parsed && !Array.isArray(parsed) && typeof parsed.title === 'string') title = parsed.title;
    } catch (e) { console.warn('[Gallery] bad marker JSON:', e); }
    return { entries, title, text: src.replace(GALLERY_RE, '').trimEnd() };
}

function hostOf(u) {
    try { return new URL(u).hostname; } catch { return u; }
}

// entries → .gallery-row element (or null). openModal(fullSrc, fulls, idx, fallbacks):
// fallbacks = the tile sources, one per full, for when a full won't load.
export function buildGallery(entries, openModal, title = '') {
    if (!entries || !entries.length) return null;
    const fulls = entries.map(e => e.full);
    const thumbs = entries.map(e => e.thumb);
    const row = document.createElement('div');
    row.className = 'gallery-row';
    if (title) {
        const head = document.createElement('div');
        head.className = 'gallery-title';
        head.textContent = title;
        row.appendChild(head);
    }
    const track = document.createElement('div');
    track.className = 'gallery-track';
    entries.forEach((e, idx) => {
        const item = document.createElement('div');
        item.className = 'gallery-item';
        const num = document.createElement('span');
        num.className = 'gallery-num';
        num.textContent = String(idx + 1);
        item.appendChild(num);
        const img = document.createElement('img');
        img.className = 'chat-img';
        img.alt = e.title || `image ${idx + 1}`;
        img.loading = 'lazy';
        img.referrerPolicy = 'no-referrer';
        img.dataset.modalReady = 'true';          // wrapImageGalleries leaves it alone
        img.dataset.full = e.full;
        if (e.handle) img.dataset.handle = 'img:' + e.handle;
        img.src = e.thumb;
        img.addEventListener('click', ev => {
            ev.stopPropagation();
            if (openModal) openModal(e.full, fulls, idx, thumbs);
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
        track.appendChild(item);
    });
    row.appendChild(track);
    return row;
}
