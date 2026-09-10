// Corpus for shared/gallery-marker.js — run under node by tests/test_gallery_marker_js.py.
// A tiny fake DOM proves: marker parse + strip, v1 string / v2 object / v3 {title,items}
// entries, handle → /api/tool-image tiles, numbered row, no-referrer + lazy on every
// tile, lightbox over the FULL urls with the tiles as fallbacks, caption → page.
// Image-tools rebuild 2026-09-09; v3 row 2026-09-10.

function mk(tag) {
    const el = { tagName: tag.toUpperCase(), children: [], dataset: {}, listeners: {},
                 appendChild(c) { this.children.push(c); return c; },
                 addEventListener(t, fn) { (this.listeners[t] ||= []).push(fn); } };
    return el;
}
globalThis.document = { createElement: mk };
globalThis.console = { ...console, warn: () => {} };

const { GALLERY_RE, normalizeEntries, parseGalleryMarker, buildGallery } =
    await import('../../interfaces/web/static/shared/gallery-marker.js');

let passed = 0;
function ok(cond, msg) { if (!cond) throw new Error('FAIL: ' + msg); passed++; }

// ── parse ────────────────────────────────────────────────────────────────────
{
    const v2 = [{ thumb: 'https://p/t1', full: 'https://p/f1', title: 'One', page: 'https://site.example/a' },
                { thumb: 'https://p/t2', full: 'https://p/f2', title: '', page: '' }];
    const text = 'Top 2 images:\n1. One\n<!--GALLERY:' + JSON.stringify(v2) + '-->';
    const r = parseGalleryMarker(text);
    ok(r.entries.length === 2, 'v2 entries parsed');
    ok(r.text === 'Top 2 images:\n1. One', 'marker stripped from shown text');
    ok(r.entries[0].page === 'https://site.example/a', 'page kept');
}
{
    const r = parseGalleryMarker('found\n<!--GALLERY:["https://a/1.jpg","https://a/2.jpg"]-->\n');
    ok(r.entries.length === 2 && r.entries[1].full === 'https://a/2.jpg' && r.entries[1].thumb === 'https://a/2.jpg',
       'legacy string entries: thumb == full');
    ok(r.text === 'found', 'trailing newline after marker trimmed');
}
{
    const r = parseGalleryMarker('plain result, no marker');
    ok(r.entries.length === 0 && r.text === 'plain result, no marker', 'no marker = untouched');
    ok(parseGalleryMarker('').entries.length === 0 && parseGalleryMarker(null).text === '', 'empty / null safe');
    const bad = parseGalleryMarker('x <!--GALLERY:[not json]--> y');
    ok(bad.entries.length === 0 && !bad.text.includes('GALLERY'), 'bad JSON: no tiles, marker still stripped');
}
{
    ok(normalizeEntries('nope').length === 0, 'non-array → empty');
    const n = normalizeEntries([{ full: 'https://f' }, { thumb: 'https://t' }, {}, '', 42, null]);
    ok(n.length === 2 && n[0].thumb === 'https://f' && n[1].full === 'https://t', 'thumb/full fill each other; junk dropped');
    ok(GALLERY_RE.test('<!--GALLERY:[]-->') && !GALLERY_RE.test('<!--GALLERIES:[]-->'), 'only GALLERY, not the dead GALLERIES');
}

// ── build ────────────────────────────────────────────────────────────────────
{
    const entries = normalizeEntries([
        { thumb: 'https://p/t1', full: 'https://p/f1', title: 'One', page: 'https://site.example/a' },
        { thumb: 'https://p/t2', full: 'https://p/f2', title: '', page: 'https://other.example/b' },
        'https://legacy/3.jpg',
    ]);
    const opened = [];
    const g = buildGallery(entries, (src, fulls, idx, fallbacks) => opened.push({ src, fulls, idx, fallbacks }), 'Cats');
    ok(g.className === 'gallery-row' && g.children.length === 2, 'one .gallery-row: title + track');
    ok(g.children[0].className === 'gallery-title' && g.children[0].textContent === 'Cats', 'title row shows the title');
    const track = g.children[1];
    ok(track.className === 'gallery-track' && track.children.length === 3, 'one item per entry in the track');
    const items = track.children;
    ok(items.every((it, i) => it.children[0].className === 'gallery-num' && it.children[0].textContent === String(i + 1)),
       'tiles numbered 1..N — the same numbers as her contact sheet');
    const tiles = items.map(item => item.children[1]);
    ok(tiles.every(t => t.tagName === 'IMG' && t.referrerPolicy === 'no-referrer' && t.loading === 'lazy'),
       'every tile no-referrer + lazy');
    ok(tiles.every(t => t.className === 'chat-img' && t.dataset.modalReady === 'true'),
       'tiles are chat-imgs already wired for the modal');
    ok(tiles[0].src === 'https://p/t1' && tiles[0].dataset.full === 'https://p/f1', 'src = thumb, data-full = full');
    tiles[1].listeners.click[0]({ stopPropagation() {} });
    ok(opened.length === 1 && opened[0].src === 'https://p/f2' && opened[0].idx === 1
       && opened[0].fulls.join() === 'https://p/f1,https://p/f2,https://legacy/3.jpg',
       'click opens the lightbox on the FULL url with the full list');
    ok(opened[0].fallbacks.join() === 'https://p/t1,https://p/t2,https://legacy/3.jpg',
       'the tiles ride along as lightbox fallbacks');
    const cap0 = items[0].children[2];
    ok(cap0.tagName === 'A' && cap0.href === 'https://site.example/a' && cap0.textContent === 'One'
       && cap0.target === '_blank' && cap0.rel === 'noopener noreferrer', 'caption links to the source page');
    const cap1 = items[1].children[2];
    ok(cap1.tagName === 'A' && cap1.textContent === 'other.example', 'no title → host as caption');
    ok(items[2].children.length === 2, 'legacy entry: number + tile, no caption');
    ok(buildGallery(entries, () => {}).children.length === 1, 'no title → track only');
    ok(buildGallery([], () => {}) === null && buildGallery(null, () => {}) === null, 'nothing to show → null');
}

// ── v3: {title, items} with img: handles ─────────────────────────────────────
{
    const v3 = { title: 'Top 3 for "red apple"', items: [
        { handle: 'img:ab12.jpg', full: 'https://proxy/f1', title: 'Apple', page: 'https://site.example/p' },
        { handle: 'img:cd34.jpg' },
        { handle: 'nope', thumb: 'https://t/3' },
    ] };
    const r = parseGalleryMarker('list\n<!--GALLERY:' + JSON.stringify(v3) + '-->\n');
    ok(r.title === v3.title && r.text === 'list', 'v3 title parsed, marker stripped');
    ok(r.entries.length === 3, 'v3 items parsed');
    ok(r.entries[0].thumb === '/api/tool-image/ab12.jpg' && r.entries[0].full === 'https://proxy/f1'
       && r.entries[0].handle === 'ab12.jpg', 'handle → OUR tool-image route; full kept for the lightbox');
    ok(r.entries[1].full === '/api/tool-image/cd34.jpg', 'no full → the tile is the full');
    ok(r.entries[2].thumb === 'https://t/3' && r.entries[2].handle === '', 'a non-img: handle is ignored, thumb used');
    const g = buildGallery(r.entries, () => {}, r.title);
    const tile = g.children[1].children[0].children[1];
    ok(tile.dataset.handle === 'img:ab12.jpg', 'tile carries the handle she holds');
    ok(parseGalleryMarker('<!--GALLERY:{"title":"t"}-->').entries.length === 0, 'v3 without items → no tiles');
    ok(parseGalleryMarker('x <!--GALLERY:["u"]--> y').title === '', 'v1/v2 have no title');
    ok(GALLERY_RE.test('<!--GALLERY:{"items":[]}-->'), 'object marker matches the regex');
}

console.log(`gallery-marker corpus: ${passed} checks passed`);
