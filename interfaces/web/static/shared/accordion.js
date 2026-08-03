// shared/accordion.js — the accordion primitive.
// Extracted 2026-08-02 from chat.js's sidebar pattern (the house's most-worn
// accordion); ten other files hand-roll their own — they migrate here as
// they're touched, Game Room consumes it first.
//
//   accordionHtml({id, title, icon, desc, content, open}) -> HTML string
//   initAccordions(root, ns)                              -> toggle + restore
//
// Emits the same classes as the chat sidebar (.sidebar-accordion / -header /
// -content / .accordion-arrow) so the look inherits wherever that CSS is in
// scope; new surfaces restyle the same names. Open-state persists per
// namespace in localStorage: an entry is a user preference (true/false),
// absence means "use the accordion's default".

import { escapeHtml } from './modal.js';

const _key = (ns) => `accordion-state:${ns}`;

function _load(ns) {
    try { return JSON.parse(localStorage.getItem(_key(ns)) || '{}') || {}; }
    catch { return {}; }
}

function _save(ns, state) {
    try { localStorage.setItem(_key(ns), JSON.stringify(state)); }
    catch { /* localStorage full/disabled — persistence is a nicety */ }
}

export function accordionHtml({ id, title, icon = '', desc = '', content = '', open = false }) {
    return `
      <div class="sidebar-accordion" data-acc="${escapeHtml(id)}"${open ? ' data-acc-open="1"' : ''}>
        <div class="sidebar-accordion-header${open ? ' open' : ''}">
          <span class="accordion-arrow">▶</span>
          <span>${icon ? escapeHtml(icon) + ' ' : ''}${escapeHtml(title)}</span>
          ${desc ? `<span class="accordion-desc">${escapeHtml(desc)}</span>` : ''}
        </div>
        <div class="sidebar-accordion-content" style="display:${open ? 'block' : 'none'}">${content}</div>
      </div>`;
}

export function initAccordions(root, ns) {
    // Restore: only touch sections with a stored preference or an explicit
    // default — sections opened transiently by code are left as they are
    // (matches the old chat-sidebar restore, which only ever OPENED).
    const state = _load(ns);
    root.querySelectorAll('.sidebar-accordion[data-acc]').forEach(sec => {
        const header = sec.querySelector('.sidebar-accordion-header');
        const content = sec.querySelector('.sidebar-accordion-content');
        if (!header || !content) return;
        const id = sec.dataset.acc;
        if (!(id in state) && sec.dataset.accOpen !== '1') return;
        const open = (id in state) ? !!state[id] : true;
        header.classList.toggle('open', open);
        content.style.display = open ? 'block' : 'none';
    });
    if (root.dataset.accBound) return;   // bind the delegate once per root
    root.dataset.accBound = '1';
    root.addEventListener('click', (e) => {
        // Interactive elements inside a header (links, buttons) never toggle —
        // callers shouldn't need stopPropagation like the old hand-rolls did.
        if (e.target.closest('a, button, input, select, label')) return;
        const header = e.target.closest('.sidebar-accordion-header');
        if (!header || !root.contains(header)) return;
        const sec = header.closest('.sidebar-accordion');
        if (!sec) return;
        const open = !header.classList.contains('open');
        header.classList.toggle('open', open);
        const content = sec.querySelector('.sidebar-accordion-content');
        if (content) content.style.display = open ? 'block' : 'none';
        if (!sec.dataset.acc) return;    // toggles without an id don't persist
        const st = _load(ns);
        st[sec.dataset.acc] = open;
        _save(ns, st);
    });
}
