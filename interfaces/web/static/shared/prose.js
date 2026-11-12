// shared/prose.js — THE paragraph and code-fence rules for her words.
//
// History (ui-parsing renderProse), the live stream (ui-streaming), Continue's
// seed (chat.js) and TTS (audio.js) all split prose HERE. Until 2026-11-12 the
// stream kept every paragraph in one <p> (a blank line became one <br>) while
// history gave each its own <p>: no paragraph gaps while she wrote, then the
// bubble jumped at the swap. Pure: no DOM, so node tests it
// (tests/js/prose.test.mjs).

const PARA_RE = /\n\s*\n/;

// A fence (``` or ~~~, closed by the same mark) opens only at the START of a
// line, and only when the rest of that line is its info string - "```ls -la```"
// on one line is inline code, as in CommonMark. Its language is the info's
// first word ("```c++", "``` python title=x"). It runs to its closer, or to the
// end when it never closes (a cut-off or cancelled reply still shows its code
// as code). The live stream asks the same questions (nextFence, fenceInfoOk,
// fenceLang) chunk by chunk.
const FENCE_RE = /(?<=^|\n)[ \t]*(`{3}|~{3})([^\n`~]*)(?:\n|$)([\s\S]*?)(?:\1|$)/g;
const FENCE_OPEN = /(?<=^|\n)[ \t]*(`{3}|~{3})/g;

export const fenceLang = (info) => (info || '').trim().split(/\s+/)[0] || '';
export const fenceInfoOk = (info) => !/[`~]/.test(info || '');

// The next line-start fence mark at or after `from`: {index, mark} or null.
export const nextFence = (text, from = 0) => {
    const re = new RegExp(FENCE_OPEN.source, 'g');
    re.lastIndex = from;
    const m = re.exec(text || '');
    return m ? { index: m.index + m[0].length - 3, mark: m[1] } : null;
};

// Carriage returns go (CRLF text from a model or an import read its "# head\r"
// as a word and its code as starting with a blank line).
const lf = (text) => (text || '').replace(/\r/g, '');

// A paragraph's lines lose only their COMMON indent: trimming the first line
// alone ("   - sub a\n   - sub b") nested sub b under sub a.
export const dedent = (text) => {
    const lines = lf(text).replace(/^(?:[ \t]*\n)+/, '').trimEnd().split('\n');
    const pad = Math.min(...lines.filter(l => l.trim()).map(l => l.match(/^[ \t]*/)[0].length));
    return Number.isFinite(pad) && pad ? lines.map(l => l.slice(pad)).join('\n') : lines.join('\n');
};

export const splitParagraphs = (text) =>
    lf(text).split(PARA_RE).filter(p => p.trim());

// Prose → ordered blocks: {type:'para', text} and {type:'code', lang, code}.
// Code is its own block, never inside a paragraph.
export const proseBlocks = (input) => {
    const text = lf(input);
    const out = [];
    const paras = s => splitParagraphs(s).forEach(t => out.push({ type: 'para', text: dedent(t) }));
    let last = 0;
    for (const m of text.matchAll(FENCE_RE)) {
        paras(text.slice(last, m.index));
        out.push({ type: 'code', lang: fenceLang(m[2]) || 'plaintext', code: m[3].trimEnd() });
        last = m.index + m[0].length;
    }
    paras(text.slice(last));
    return out;
};

// The stream's paragraph buffer → the paragraphs that are finished and the one
// still being written. A blank line closes a paragraph the moment it arrives.
export const rollParagraphs = (buf) => {
    const parts = (buf || '').split(PARA_RE);
    const open = parts.pop();
    return { done: parts.filter(p => p.trim()), open };
};
