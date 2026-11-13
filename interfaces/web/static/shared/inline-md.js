// shared/inline-md.js - inline markdown for ONE line of chat text that is
// already HTML-escaped (& < >). Pure string work, no DOM: ui-parsing.js draws
// every line, cell, header and list item through it, and the corpus in
// tests/js/inline-md.test.mjs runs it under node.

// A value going inside "…" in an attribute. The caller's escape covers & < >
// but NOT quotes: `![x" onerror="…"](https://…)` closed the attribute and ran
// a handler in the user's tab - any text reaching the renderer (her reply
// after a prompt injection, a room guest, a pasted line) could do it (UI
// scout, 2026-11-12).
const attr = (s) => s.replace(/"/g, '&quot;').replace(/'/g, '&#39;');

// A built <img> or <a> leaves the string as a token until every pass is done.
// The link pass used to run over the HTML the image pass had just written, and
// a link typed INSIDE an image url - `![a](https://x/[t](/b/onerror=…//))` -
// was matched inside src="…": its own <a href=" closed the attribute and the
// rest became a live onerror (scout hunt 2026-11-13). No pass reads inside a
// tag another pass built, and a url holding a token is not a url.
const T = '\u0000';

export const inlineMarkdown = (html) => {
    html = html.replace(/\u0000/g, '');
    const held = [];
    const hold = (tag) => `${T}${held.push(tag) - 1}${T}`;
    const back = (s) => s.replace(/\u0000(\d+)\u0000/g, (_, i) => back(held[+i] ?? ''));

    // Checkboxes: [x] or [X] = checked, [ ] = unchecked - never the start of a
    // link: "[X](https://x.com/…)" drew a ticked box and a raw url
    html = html.replace(/\[x\](?!\s*\()/gi, '<input type="checkbox" checked disabled>');
    html = html.replace(/\[ \](?!\s*\()/g, '<input type="checkbox" disabled>');

    // Bold+italic: ***text*** or ___text___
    html = html.replace(/\*\*\*([^*]+)\*\*\*/g, '<strong><em>$1</em></strong>');
    html = html.replace(/___([^_]+)___/g, '<strong><em>$1</em></strong>');

    // Bold: **text** or __text__
    html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    html = html.replace(/__([^_]+)__/g, '<strong>$1</strong>');

    // Italic: *text* or _text_
    html = html.replace(/\*([^*]+)\*/g, '<em>$1</em>');
    html = html.replace(/(?<![a-zA-Z0-9])_([^_]+)_(?![a-zA-Z0-9])/g, '<em>$1</em>');

    // Strikethrough: ~~text~~
    html = html.replace(/~~([^~]+)~~/g, '<del>$1</del>');

    // Inline code: `code`, or ```code``` on one line (never a block: shared/prose.js)
    html = html.replace(/(`{1,3})([^`\n]+)\1/g, '<code>$2</code>');

    // Images: ![alt](url) - before links, so a linked image [![alt](img)](url) works
    // Balanced parens in URL for Wikipedia etc, tolerates space between ] and (
    html = html.replace(/!\[([^\]]*)\]\s*\(([^()]*(?:\([^)]*\)[^()]*)*)\)/g, (m, alt, url) => {
        url = url.trim().replace(/ /g, '%20');
        if (/^https?:\/\//i.test(url)) return hold(`<img src="${attr(url)}" alt="${attr(alt)}" class="chat-img" referrerpolicy="no-referrer" loading="lazy">`);
        return m;
    });

    // Links: [text](url) - allow http(s), mailto, and local paths starting with /
    html = html.replace(/\[([^\]]+)\]\s*\(([^()]*(?:\([^)]*\)[^()]*)*)\)/g, (m, text, url) => {
        url = url.trim().replace(/ /g, '%20');
        if (url.includes(T)) return m;
        if (/^(https?:\/\/|mailto:)/i.test(url) || /^\/[a-zA-Z]/.test(url)) {
            return hold(`<a href="${attr(url)}" target="_blank" rel="noopener">${text}</a>`);
        }
        return m;
    });

    return back(html);
};
