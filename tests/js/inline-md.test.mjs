// Corpus for shared/inline-md.js - run under node by tests/test_inline_md_js.py.
// The renderer builds <img> and <a> from chat text (her reply, a room guest,
// a pasted line). Whatever the text, a tag it builds may carry ONLY the
// attributes the renderer wrote: one extra attribute is a live event handler.
// 2026-11-13: a link typed inside an image url closed src="…" and left an
// onerror behind.

const { inlineMarkdown } = await import('../../interfaces/web/static/shared/inline-md.js');

let failed = 0, passed = 0;
const ok = (cond, name, detail = '') => {
    if (cond) { passed++; return; }
    failed++;
    console.log(`FAIL ${name}${detail ? '\n  ' + detail : ''}`);
};
const eq = (name, got, want) => ok(got === want, name, `got:  ${got}\n  want: ${want}`);

// What the page hands the renderer: textContent -> innerHTML escapes & < > only.
const esc = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
const md = (s) => inlineMarkdown(esc(s));

// The attribute names of every tag in `html`, read the way a browser's
// tokenizer reads them (quoted and unquoted values, "/" as a separator).
function attrNames(html) {
    const names = [];
    let i = 0;
    while ((i = html.indexOf('<', i)) !== -1) {
        i++;
        if (!/[a-zA-Z]/.test(html[i] || '')) continue;
        while (i < html.length && !/[\s/>]/.test(html[i])) i++;         // tag name
        while (i < html.length && html[i] !== '>') {
            if (/[\s/]/.test(html[i])) { i++; continue; }
            let n = '';
            while (i < html.length && !/[\s/>=]/.test(html[i])) n += html[i++];
            if (n) names.push(n.toLowerCase());
            while (/\s/.test(html[i] || '')) i++;
            if (html[i] !== '=') continue;
            i++;
            while (/\s/.test(html[i] || '')) i++;
            const q = html[i];
            if (q === '"' || q === "'") {
                i++;
                while (i < html.length && html[i] !== q) i++;
                i++;
            } else {
                while (i < html.length && !/[\s>]/.test(html[i])) i++;
            }
        }
    }
    return names;
}
const ALLOWED = new Set(['src', 'alt', 'class', 'referrerpolicy', 'loading',
                         'href', 'target', 'rel', 'type', 'checked', 'disabled']);
const clean = (name, text) => {
    const out = md(text);
    const extra = attrNames(out).filter(n => !ALLOWED.has(n));
    ok(extra.length === 0, name, `attributes the renderer never wrote: ${extra.join(', ')}\n  html: ${out}`);
    ok(!out.includes('\u0000'), `${name} (no token left behind)`, out);
};

// ── what it draws ──
eq('image', md('![cat](https://x.test/c.png)'),
   '<img src="https://x.test/c.png" alt="cat" class="chat-img" referrerpolicy="no-referrer" loading="lazy">');
eq('link', md('[site](https://x.test/a)'),
   '<a href="https://x.test/a" target="_blank" rel="noopener">site</a>');
eq('local link', md('[file](/api/files/a.txt)'),
   '<a href="/api/files/a.txt" target="_blank" rel="noopener">file</a>');
eq('mailto', md('[mail](mailto:a@x.test)'),
   '<a href="mailto:a@x.test" target="_blank" rel="noopener">mail</a>');
eq('parens in a url', md('[w](https://en.wikipedia.org/wiki/Mars_(planet))'),
   '<a href="https://en.wikipedia.org/wiki/Mars_(planet)" target="_blank" rel="noopener">w</a>');
eq('a linked image', md('[![b](https://x.test/b.png)](https://x.test/)'),
   '<a href="https://x.test/" target="_blank" rel="noopener">'
   + '<img src="https://x.test/b.png" alt="b" class="chat-img" referrerpolicy="no-referrer" loading="lazy"></a>');
eq('two images and a link on one line', md('![a](https://x.test/a.png) [l](https://x.test/) ![b](https://x.test/b.png)'),
   '<img src="https://x.test/a.png" alt="a" class="chat-img" referrerpolicy="no-referrer" loading="lazy"> '
   + '<a href="https://x.test/" target="_blank" rel="noopener">l</a> '
   + '<img src="https://x.test/b.png" alt="b" class="chat-img" referrerpolicy="no-referrer" loading="lazy">');
eq('an image that is not http stays text', md('![a](javascript:alert(1))'), '![a](javascript:alert(1))');
eq('a link that is not allowed stays text', md('[a](javascript:alert(1))'), '[a](javascript:alert(1))');
eq('bold, italic, code', md('**b** *i* `c`'), '<strong>b</strong> <em>i</em> <code>c</code>');
eq('a ticked box is not a link', md('[x] done'), '<input type="checkbox" checked disabled> done');
eq('quotes in alt and url', md('![a"b](https://x.test/c"d\'e)'),
   '<img src="https://x.test/c&quot;d&#39;e" alt="a&quot;b" class="chat-img" referrerpolicy="no-referrer" loading="lazy">');

// ── the 2026-11-13 hole and its relatives ──
const hole = "![a](https://x.invalid/[t](/b/onerror=location='javascript:alert%281%29'//))";
eq('a link inside an image url stays inside src', md(hole),
   '<img src="https://x.invalid/[t](/b/onerror=location=&#39;javascript:alert%281%29&#39;//)" alt="a" '
   + 'class="chat-img" referrerpolicy="no-referrer" loading="lazy">');
ok(!md('[t](https://x.test/![a](https://y.test/onerror=alert%281%29//))').includes('<a '),
   'an image inside a link url: the link is not built');

const nasty = [
    hole,
    '[t](https://x.test/![a](https://y.test/onerror=alert%281%29//))',
    '![a](https://x.test/[t](https://y.test/onerror=alert%281%29//))',
    '![a" onerror="alert(1)](https://x.test/a.png)',
    "![a' onerror='alert(1)](https://x.test/a.png)",
    '[t" onmouseover="alert(1)](https://x.test/)',
    '[t](https://x.test/" onmouseover="alert(1))',
    '![a](https://x.test/a.png "onerror=alert(1)")',
    '![a](https://x.test/`onerror=alert(1)`)',
    '![a](https://x.test/*onerror=alert(1)*)',
    '![a](https://x.test/_onerror=alert(1)_/)',
    '![[t](/b/onerror=alert%281%29//)](https://x.test/a.png)',
    '[![a](https://x.test/[t](/b/onerror=alert%281%29//))](https://x.test/)',
    '![a](https://x.test/\u00000\u0000) [t](/b/onerror=alert%281%29//)',
    '\u00000\u0000 ![a](https://x.test/a.png) [t](https://x.test/\u00000\u0000)',
    '[x](/a/[x]/onerror=alert%281%29//)',
];
nasty.forEach((t, n) => clean(`nasty ${n}`, t));

// Every pair of them on one line, both orders.
for (let a = 0; a < nasty.length; a++) {
    for (let b = 0; b < nasty.length; b++) clean(`nasty ${a}+${b}`, `${nasty[a]} ${nasty[b]}`);
}

console.log(`${passed} passed, ${failed} failed`);
if (failed) process.exit(1);
