// Corpus for shared/files-marker.js — run under node by tests/test_files_marker_js.py.
// A tiny fake DOM proves: marker parse + strip, several markers, plugin-route urls
// only (nothing off-origin ever reaches an element), audio → a player that fetches
// nothing until played, every file → a download button. 2026-09-26.

function mk(tag) {
    const el = { tagName: tag.toUpperCase(), children: [], dataset: {}, listeners: {},
                 appendChild(c) { this.children.push(c); return c; },
                 addEventListener(t, fn) { (this.listeners[t] ||= []).push(fn); } };
    return el;
}
globalThis.document = { createElement: mk };
globalThis.console = { ...console, warn: () => {} };

const { FILES_RE, safeUrl, normalizeFiles, parseFilesMarker, buildFilesRows } =
    await import('../../interfaces/web/static/shared/files-marker.js');

let passed = 0;
function ok(cond, msg) { if (!cond) throw new Error('FAIL: ' + msg); passed++; }

const MP3 = '/api/plugin/fm1/song/4a6d591cf2.mp3';
const MID = '/api/plugin/fm1/song/4a6d591cf2.mid';
const mark = (title, items) => '<!--FILES:' + JSON.stringify({ title, items }) + '-->';

// ── parse ────────────────────────────────────────────────────────────────────
{
    const text = 'Saved Copper Light.\n' + mark('Copper Light',
        [{ url: MP3, name: 'copper-light.mp3' }, { url: MID, name: 'copper-light.mid' }]);
    const r = parseFilesMarker(text);
    ok(r.text === 'Saved Copper Light.', 'marker stripped from shown text');
    ok(r.groups.length === 1 && r.groups[0].title === 'Copper Light', 'one titled group');
    ok(r.groups[0].files.length === 2, 'both files kept');
    ok(r.groups[0].files[0].audio === true && r.groups[0].files[1].audio === false, 'mp3 is audio, mid is not');
}
{
    const r = parseFilesMarker('plain text, no marker');
    ok(r.groups.length === 0 && r.text === 'plain text, no marker', 'no marker = untouched');
    ok(parseFilesMarker('').groups.length === 0 && parseFilesMarker(null).text === '', 'empty / null safe');
    const list = [{ type: 'text', text: 'x' }];
    ok(parseFilesMarker(list).text === list, 'non-string content passes through');
    const bad = parseFilesMarker('x\n<!--FILES:{not json}-->\ny');
    ok(bad.groups.length === 0 && !bad.text.includes('FILES'), 'bad JSON: no row, marker still stripped');
}
{
    const two = 'a\n' + mark('One', [{ url: MP3 }]) + '\nb\n' + mark('Two', [{ url: MID }]) + '\n';
    const r = parseFilesMarker(two);
    ok(r.groups.length === 2 && r.groups[1].title === 'Two', 'two markers, two groups, in order');
    ok(r.text === 'a\nb', 'both markers stripped');
    const tricky = parseFilesMarker(mark('a}-->b', [{ url: MP3 }]));
    ok(tricky.groups.length === 1 && tricky.groups[0].title === 'a}-->b', 'a title holding }--> still parses');
    ok(FILES_RE.test('<!--FILES:{}-->') && !FILES_RE.test('<!--FILE:{}-->') && !FILES_RE.test('<!--FILES:[]-->'),
       'only FILES with an object body');
}

// ── urls: plugin routes only ─────────────────────────────────────────────────
{
    for (const good of [MP3, MID, MP3 + '?dl=1', '/api/plugin/some-plugin/a/b/c.wav'])
        ok(safeUrl(good), 'accepted: ' + good);
    for (const bad of ['https://evil.example/x.mp3', '//evil.example/x.mp3', 'javascript:alert(1)',
                       '/api/plugin/fm1/../../settings', '/api/settings', '/api/plugin/fm1//x.mp3',
                       '/api/plugin/fm1/x.mp3?a=<b>', 'data:audio/mp3;base64,AAAA', '', null, 42, {}])
        ok(!safeUrl(bad), 'refused: ' + String(bad));
    const n = normalizeFiles({ items: [{ url: 'https://evil.example/x.mp3', name: 'x' }, { url: MP3 }, null, 'str', {}] });
    ok(n.length === 1 && n[0].url === MP3 && n[0].name === '4a6d591cf2.mp3', 'bad items dropped; name falls back to the file');
    ok(normalizeFiles([{ url: MP3 }]).length === 0 && normalizeFiles('nope').length === 0, 'only the {items} shape');
    ok(normalizeFiles({ items: Array(40).fill({ url: MP3 }) }).length === 12, 'capped at 12 files');
}

// ── build ────────────────────────────────────────────────────────────────────
{
    const { groups } = parseFilesMarker(mark('Copper Light',
        [{ url: MP3, name: 'copper-light.mp3' }, { url: MID, name: 'copper-light.mid' }]));
    const rows = buildFilesRows(groups);
    ok(rows.length === 1 && rows[0].className === 'files-row', 'one row');
    const [head, player, chips] = rows[0].children;
    ok(head.className === 'files-title' && head.textContent === 'Copper Light', 'title first');
    ok(player.tagName === 'AUDIO' && player.src === MP3 && player.controls === true, 'a player for the mp3');
    ok(player.preload === 'none', 'nothing is fetched until play is pressed');
    ok(chips.children.length === 2, 'a download button per file');
    ok(chips.children[0].tagName === 'A' && chips.children[0].href === MP3
       && chips.children[0].download === 'copper-light.mp3', 'mp3 button downloads under its name');
    ok(chips.children[1].href === MID && chips.children[1].download === 'copper-light.mid', 'mid button too');
}
{
    const rows = buildFilesRows(parseFilesMarker(mark('', [{ url: MID, name: 'a.mid' }])).groups);
    ok(rows.length === 1 && rows[0].children.length === 1 && rows[0].children[0].className === 'files-chips',
       'no title, no audio: just the button');
    ok(buildFilesRows([]).length === 0 && buildFilesRows(null).length === 0
       && buildFilesRows([{ title: 't', files: [] }]).length === 0, 'nothing to show = no row');
}

console.log(`files-marker corpus: ${passed} checks passed`);
