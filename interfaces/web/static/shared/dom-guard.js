// shared/dom-guard.js — the four moves that keep a DOM refresh surgical.
//
// One bug class, three symptoms (DOM-refresh hunt, 2026-09-08): a container
// is rebuilt while the user is INSIDE it — typing (edit reverted / focus
// stolen) or scrolled (position reset) — because a bus echo, a poll, or an
// async completion fired a repaint. The guards existed once each in the
// wrong files (prompts.js focus guard, memories.js caret restore, daemons.js
// scroll carry); this is them, lifted, so every view reaches for the same
// four instead of growing a fifth.
//
//   editableFocused(root)          — is the user typing somewhere inside root?
//   deferWhileEditing(root, fn)    — a "soft refresh": fn now, or after the
//                                    user leaves the field (+ an optional
//                                    `busy` predicate with `.kick()` to catch
//                                    up when it clears, e.g. a pending save)
//   snapScroll(el) → restore()     — carry every scrolled ancestor across a
//                                    rebuild of el
//   snapFocus(root) → restore()    — carry focus + caret across a rebuild
//   focusUnlessEditing(el)         — the focus policy: never yank a cursor
//                                    out of another editable

const SETTLE_MS = 150;   // focusout fires before the next target owns focus

export function isEditable(el) {
    if (!el) return false;
    const t = el.tagName;
    return t === 'TEXTAREA' || t === 'INPUT' || t === 'SELECT' || !!el.isContentEditable;
}

export function editableFocused(root = document) {
    const ae = document.activeElement;
    if (!ae || ae === document.body) return false;
    if (root && root !== document && !root.contains(ae)) return false;
    return isEditable(ae);
}

// One document-level focusout drains every deferred refresh after the settle
// window; a wrapper that is still blocked (focus moved to another field, save
// still pending) simply stays queued.
const _queued = new Set();
let _docBound = false;
function _drain() { for (const run of [..._queued]) run(); }
function _bindDoc() {
    if (_docBound) return;
    _docBound = true;
    document.addEventListener('focusout', () => setTimeout(_drain, SETTLE_MS));
}

/**
 * Wrap a refresh so it never lands under the user's cursor.
 * @param {Element|Document|() => Element|null} root  where the user's editables live
 * @param {() => any} fn                              the refresh
 * @param {{busy?: () => boolean}} [opts]             extra hold (e.g. a pending save)
 * @returns {() => void} soft — call instead of fn. `soft.kick()` retries a held
 *          refresh once `busy` clears; `soft.pending()` reports the hold.
 */
export function deferWhileEditing(root, fn, { busy } = {}) {
    let pending = false;
    const resolve = () => (typeof root === 'function' ? root() : root);
    const blocked = () => !!(busy && busy()) || editableFocused(resolve() || document);
    const run = () => {
        if (!pending || blocked()) return;
        pending = false;
        _queued.delete(run);
        fn();
    };
    const soft = () => {
        if (!blocked()) { pending = false; _queued.delete(run); fn(); return; }
        pending = true;
        _queued.add(run);
        _bindDoc();
    };
    soft.kick = () => setTimeout(run, 0);
    soft.pending = () => pending;
    return soft;
}

/** Snapshot the scrollTop of el and every scrolled ancestor — plus any inner
 *  scrollers named by selector (they die with the rebuild and are re-found
 *  after it). Returns restore(). */
export function snapScroll(el, innerSelectors = []) {
    const saved = [];
    for (let n = el; n && n !== document.body && n !== document; n = n.parentElement) {
        if (n.scrollTop) saved.push([n, n.scrollTop]);
    }
    const inner = [];
    for (const sel of innerSelectors) {
        const c = el?.querySelector?.(sel);
        if (c && c.scrollTop) inner.push([sel, c.scrollTop]);
    }
    return () => {
        for (const [n, t] of saved) n.scrollTop = t;
        for (const [sel, t] of inner) { const c = el?.querySelector?.(sel); if (c) c.scrollTop = t; }
    };
}

/** Snapshot focus + caret inside root; returns restore() for after the rebuild.
 *  Re-finds the field by id, else by tag + exact class list. */
export function snapFocus(root) {
    const ae = document.activeElement;
    if (!ae || !isEditable(ae) || (root && root !== document && !root.contains(ae))) return () => {};
    const id = ae.id;
    const tag = ae.tagName.toLowerCase();
    const cls = ae.getAttribute('class') || '';
    const selStart = ae.selectionStart ?? null;
    const selEnd = ae.selectionEnd ?? null;
    return () => {
        const scope = root || document;
        let el = null;
        if (id) el = scope.querySelector(`#${CSS.escape(id)}`);
        if (!el && cls) {
            // Class fallback only when it names ONE field — a per-card input
            // (one note box per goal) would otherwise land in the first card.
            const all = scope.querySelectorAll(`${tag}[class="${cls.replace(/"/g, '\\"')}"]`);
            if (all.length === 1) el = all[0];
        }
        if (!el || el === document.activeElement) return;
        // preventScroll: restoreFocus runs AFTER restoreScroll at every site —
        // a default focus() would scroll the field into view over the
        // carried position (D1#10).
        el.focus({ preventScroll: true });
        if (selStart !== null && typeof el.setSelectionRange === 'function') {
            try { el.setSelectionRange(selStart, selEnd ?? selStart); } catch { /* not a text field */ }
        }
    };
}

/** Focus el only when the user's cursor isn't already in some other editable. */
export function focusUnlessEditing(el) {
    if (!el) return;
    const ae = document.activeElement;
    if (ae && ae !== el && isEditable(ae)) return;
    el.focus();
}
