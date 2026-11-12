// Corpus for shared/prose.js — run under node by tests/test_prose_js.py.
// The live stream and history must build the SAME paragraphs: the stream rolls
// its buffer chunk by chunk (rollParagraphs), history splits the whole text
// (splitParagraphs). Every corpus text is streamed in many chunkings and must
// land on history's paragraphs exactly. 2026-11-12.

const { splitParagraphs, proseBlocks, rollParagraphs } =
    await import('../../interfaces/web/static/shared/prose.js');

let failed = 0, passed = 0;
const eq = (name, got, want) => {
    const g = JSON.stringify(got), w = JSON.stringify(want);
    if (g === w) { passed++; return; }
    failed++;
    console.log(`FAIL ${name}\n  got:  ${g}\n  want: ${w}`);
};

// The streamer's prose handling (ui-streaming appendStream + updatePara),
// minus the DOM: leading space dropped on an empty buffer, rolled each chunk.
function stream(chunks) {
    const paras = [];
    let buf = '';
    for (const c of chunks) {
        buf += buf === '' ? c.replace(/^\s+/, '') : c;
        if (!buf) continue;
        const { done, open } = rollParagraphs(buf);
        paras.push(...done.map(t => t.trim()));
        buf = done.length ? open.trimStart() : open;
    }
    if (buf.trim()) paras.push(buf.trim());
    return paras;
}

function chunkings(text) {
    const out = [[text], [...text]];                     // whole, char by char
    for (const n of [2, 3, 5, 7, 13]) {
        const cs = [];
        for (let i = 0; i < text.length; i += n) cs.push(text.slice(i, i + n));
        out.push(cs);
    }
    let seed = 7;                                         // deterministic "random"
    const cs = [];
    for (let i = 0; i < text.length;) {
        seed = (seed * 1103515245 + 12345) & 0x7fffffff;
        const n = 1 + (seed % 9);
        cs.push(text.slice(i, i + n));
        i += n;
    }
    out.push(cs);
    return out;
}

const CORPUS = [
    'One paragraph only.',
    'First paragraph.\n\nSecond paragraph.',
    'Line one\nline two\n\nNew para.',
    'A\n\n\n\nB',
    'A\n  \n\t\nB',
    '  leading space\n\nB  ',
    'Ends with a break\n\n',
    '\n\nStarts with a break',
    '1. one\n\n2. two\n\n3. three',
    '- a\n- b\n\nAfter the list.',
    '# Heading\n\nBody text with **bold** and `code`.',
    '| a | b |\n|---|---|\n| 1 | 2 |\n\nAfter the table.',
    'Windows\r\n\r\nline endings',
];

for (const text of CORPUS) {
    const want = splitParagraphs(text).map(t => t.trim());
    chunkings(text).forEach((cs, i) => eq(`stream==history [${JSON.stringify(text)}] chunking ${i}`, stream(cs), want));
}

// splitParagraphs
eq('split: blank-only lines are one break', splitParagraphs('A\n \n\nB'), ['A', 'B']);
eq('split: empty', splitParagraphs(''), []);
eq('split: null-safe', splitParagraphs(null), []);

// rollParagraphs
eq('roll: open only', rollParagraphs('A\nB'), { done: [], open: 'A\nB' });
eq('roll: closes at the blank line', rollParagraphs('A\n\nB'), { done: ['A'], open: 'B' });
eq('roll: trailing break closes, open empty', rollParagraphs('A\n\n'), { done: ['A'], open: '' });

// proseBlocks: code is its own block, never inside a paragraph
eq('blocks: code between paragraphs', proseBlocks('Before.\n\n```py\nx = 1\n```\nAfter.'), [
    { type: 'para', text: 'Before.' },
    { type: 'code', lang: 'py', code: 'x = 1' },
    { type: 'para', text: 'After.' },
]);
// fence rules (scouts 1+2, 2026-11-12): line start only, language = first word.
// Mid-sentence ```x``` is inline code now, never a block that split the paragraph.
eq('fence: mid-sentence is prose', proseBlocks('Run ```ls``` now'), [{ type: 'para', text: 'Run ```ls``` now' }]);
eq('fence: c++ keeps its language', proseBlocks('```c++\nint x;\n```'), [{ type: 'code', lang: 'c++', code: 'int x;' }]);
eq('fence: info after the language', proseBlocks('``` python title=x\nprint(1)\n```'), [{ type: 'code', lang: 'python', code: 'print(1)' }]);
eq('fence: mentioned in prose does not swallow the rest', proseBlocks('Use (```) to fence.\n\nProse.').length, 2);
eq('fence: one-line ```x``` is inline', proseBlocks('```ls -la```'), [{ type: 'para', text: '```ls -la```' }]);
eq('fence: tildes', proseBlocks('~~~py\n# c\n~~~\nafter'), [{ type: 'code', lang: 'py', code: '# c' }, { type: 'para', text: 'after' }]);
eq('fence: CRLF', proseBlocks('```python\r\nx\r\n```'), [{ type: 'code', lang: 'python', code: 'x' }]);
eq('dedent: sub-bullets keep their own nesting', proseBlocks('1. Step\n\n   - sub a\n   - sub b')[1], { type: 'para', text: '- sub a\n- sub b' });
eq('blocks: no lang is plaintext', proseBlocks('```\nraw\n```'), [{ type: 'code', lang: 'plaintext', code: 'raw' }]);
eq('blocks: blank lines inside code stay code', proseBlocks('```\na\n\nb\n```'), [{ type: 'code', lang: 'plaintext', code: 'a\n\nb' }]);
eq('blocks: unclosed fence runs to the end', proseBlocks('Text.\n\n```js\nlet a;\n\nlet b;'), [
    { type: 'para', text: 'Text.' },
    { type: 'code', lang: 'js', code: 'let a;\n\nlet b;' },
]);
eq('blocks: two fences', proseBlocks('```a\n1\n```\n\n```b\n2\n```'), [
    { type: 'code', lang: 'a', code: '1' },
    { type: 'code', lang: 'b', code: '2' },
]);
// Continue's seed (chat.js) is the last BLOCK: after a fence it's the line after
// the fence, never the fence itself (HDF test run 2026-11-12 drew the code twice)
eq('seed: last block after a fence', proseBlocks('Intro.\n\n```py\nx = 1\n```\nAfter code cut').at(-1), { type: 'para', text: 'After code cut' });
eq('seed: reply ending in code has no paragraph', proseBlocks('Intro.\n\n```py\nx = 1\n```').at(-1).type, 'code');
eq('blocks: plain prose',proseBlocks('A\n\nB'), [{ type: 'para', text: 'A' }, { type: 'para', text: 'B' }]);

console.log(`${passed} passed, ${failed} failed`);
if (failed) process.exit(1);
