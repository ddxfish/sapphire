// shared/think.js - the web UI's reader of thinking blocks (2026-09-27).
// The twin of core/think.py: same tag names, same four rules. Change one,
// change the other; tests/test_think_js_twin.py runs both on the same cases.
//
//   1. A tag in backticks, or inside a fenced code block of the answer, is a
//      WORD. A model explaining `<think>` tags is not thinking.
//   2. An opener with a closer after it is a thinking block.
//   3. A closer with nothing open means what came before it was thinking
//      (a lost opener, or an early close). Greedy, but fenced: it reaches
//      back to the last block, never across one.
//   4. An opener that never closes is thinking to the end only where a block
//      can start (start of the reply, start of a line, right after a sentence
//      or a tag). Mid-sentence it is a word.

export const OPEN_NAMES = ['think', 'seed:think', 'thinking', 'redacted_thinking', 'reasoning'];
export const CLOSE_NAMES = [...OPEN_NAMES, 'seed:cot_budget_reflect'];

const ALT = [...CLOSE_NAMES].sort((a, b) => b.length - a.length)
    .map(n => n.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|');
const tagRe = () => new RegExp(`<(/?)(${ALT})(?:\\s[^<>]*)?\\s*>`, 'gi');
const FENCE_RE = /^[ \t]*(?:`{3,}|~{3,})/gm;
const BLOCK_START = '\n\r.!?>';
const WS = ' \t\r\n';

const backticked = (text, at) => at > 0 && text[at - 1] === '`';
const inFence = (chunk) => ((chunk.match(FENCE_RE) || []).length % 2) === 1;

const after = (text, at) => {
    while (at < text.length && WS.includes(text[at])) at++;
    return at;
};

const blockPosition = (text, at) => {
    const head = text.slice(0, at).replace(/[ \t]+$/, '');
    return !head || BLOCK_START.includes(head[head.length - 1]);
};

const hit = (m) => ({ index: m.index, text: m[0], name: m[2].toLowerCase(), closing: !!m[1] });

// The next real closer at or after `from` (a backticked one is a word).
export const nextThinkCloser = (text, from = 0) => {
    const re = tagRe();
    re.lastIndex = from;
    let m;
    while ((m = re.exec(text))) {
        if (m[1] && !backticked(text, m.index)) return hit(m);
    }
    return null;
};

// The next opener at or after `from` that can start a block. For a live
// stream, where the closer has not arrived yet: not backticked, and sitting
// where a block can start.
export const nextThinkOpener = (text, from = 0) => {
    const re = tagRe();
    re.lastIndex = from;
    let m;
    while ((m = re.exec(text))) {
        if (m[1] || !OPEN_NAMES.includes(m[2].toLowerCase())) continue;
        if (backticked(text, m.index) || !blockPosition(text, m.index)) continue;
        return hit(m);
    }
    return null;
};

// The reply in order: [{type: 'text' | 'think', text, name}].
export const thinkSegments = (input) => {
    const text = input == null ? '' : String(input);
    const out = [];
    const add = (type, chunk, name = '') => {
        if (chunk && (type === 'text' || chunk.trim())) out.push({ type, text: chunk, name });
    };
    const re = tagRe();
    let pos = 0, i = 0;
    for (;;) {
        re.lastIndex = i;
        const m = re.exec(text);
        if (!m) break;
        const closing = !!m[1], name = m[2].toLowerCase(), end = m.index + m[0].length;
        const chunk = text.slice(pos, m.index);
        if (backticked(text, m.index) || inFence(chunk) || (!closing && !OPEN_NAMES.includes(name))) {
            i = end;                                        // rule 1: a word
            continue;
        }
        if (closing) {                                      // rule 3: nothing open
            add('think', chunk, name);
            pos = i = after(text, end);
            continue;
        }
        const close = nextThinkCloser(text, end);
        if (!close) {                                       // rule 4: never closes
            if (blockPosition(text, m.index)) {
                add('text', chunk);
                add('think', text.slice(end), name);
                pos = text.length;
                break;
            }
            i = end;
            continue;
        }
        add('text', chunk);                                 // rule 2: a block
        add('think', text.slice(end, close.index), name);
        pos = i = after(text, close.index + close.text.length);
    }
    add('text', text.slice(pos));
    return out;
};

export const splitThink = (text) => {
    const segs = thinkSegments(text);
    return {
        answer: segs.filter(s => s.type === 'text').map(s => s.text).join('').trim(),
        thinking: segs.filter(s => s.type === 'think').map(s => s.text.trim()).join('\n\n').trim(),
    };
};

export const stripThink = (text) => splitThink(text).answer;
export const hasThink = (text) => thinkSegments(text).some(s => s.type === 'think');
export const isSeedThink = (name) => String(name || '').startsWith('seed:');
