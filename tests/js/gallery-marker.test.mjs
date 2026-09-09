// Corpus for shared/gallery-marker.js — run under node by tests/test_gallery_marker_js.py.
// A tiny fake DOM proves: marker parse + strip, legacy string vs v2 object entries,
// no-referrer + lazy on every tile, lightbox over the FULL urls, caption → page.
// Image-tools rebuild, 2026-09-09.

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
    const g = buildGallery(entries, (src, fulls, idx) => opened.push({ src, fulls, idx }));
    ok(g.className === 'image-gallery' && g.children.length === 3, 'one .image-gallery, one item per entry');
    const tiles = g.children.map(item => item.children[0]);
    ok(tiles.every(t => t.tagName === 'IMG' && t.referrerPolicy === 'no-referrer' && t.loading === 'lazy'),
       'every tile no-referrer + lazy');
    ok(tiles.every(t => t.className === 'chat-img' && t.dataset.modalReady === 'true'),
       'tiles are chat-imgs already wired for the modal');
    ok(tiles[0].src === 'https://p/t1' && tiles[0].dataset.full === 'https://p/f1', 'src = thumb, data-full = full');
    tiles[1].listeners.click[0]({ stopPropagation() {} });
    ok(opened.length === 1 && opened[0].src === 'https://p/f2' && opened[0].idx === 1
       && opened[0].fulls.join() === 'https://p/f1,https://p/f2,https://legacy/3.jpg',
       'click opens the lightbox on the FULL url with the full list');
    const cap0 = g.children[0].children[1];
    ok(cap0.tagName === 'A' && cap0.href === 'https://site.example/a' && cap0.textContent === 'One'
       && cap0.target === '_blank' && cap0.rel === 'noopener noreferrer', 'caption links to the source page');
    const cap1 = g.children[1].children[1];
    ok(cap1.tagName === 'A' && cap1.textContent === 'other.example', 'no title → host as caption');
    ok(g.children[2].children.length === 1, 'legacy entry: tile only, no caption');
    ok(buildGallery([], () => {}) === null && buildGallery(null, () => {}) === null, 'nothing to show → null');
}

console.log(`gallery-marker corpus: ${passed} checks passed`);
