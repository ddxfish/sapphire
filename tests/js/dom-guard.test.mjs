// Corpus for shared/dom-guard.js — run under node by tests/test_dom_guard_js.py.
// A tiny fake DOM: enough activeElement / contains / scrollTop / focusout to
// prove the four moves (editable check, deferred soft refresh, scroll carry,
// focus carry) and the focus policy. DOM-refresh hunt, 2026-09-08.

const listeners = {};
const body = mk('BODY');
globalThis.document = {
    body,
    activeElement: body,
    addEventListener(type, fn) { (listeners[type] ||= []).push(fn); },
    querySelector(sel) { return findIn(root, sel); },
    contains(n) { return true; },
};
globalThis.CSS = { escape: s => s };

function mk(tagName, { id = '', cls = '', editable = false, parent = null } = {}) {
    const el = {
        tagName, id, isContentEditable: editable, scrollTop: 0, parentElement: parent,
        children: [], focused: 0, sel: null,
        getAttribute(a) { return a === 'class' ? cls : null; },
        contains(n) { for (let x = n; x; x = x.parentElement) if (x === el) return true; return false; },
        focus() { el.focused++; document.activeElement = el; },
        setSelectionRange(a, b) { el.sel = [a, b]; },
        querySelector(sel) { return findIn(el, sel); },
    };
    if (parent) parent.children.push(el);
    return el;
}
function findIn(node, sel) {
    const m = sel.match(/^#(.+)$/);
    const cm = sel.match(/^(\w+)\[class="(.*)"\]$/);
    const walk = n => {
        for (const c of n.children || []) {
            if (m && c.id === m[1]) return c;
            if (cm && c.tagName.toLowerCase() === cm[1] && c.getAttribute('class') === cm[2]) return c;
            const r = walk(c); if (r) return r;
        }
        return null;
    };
    return walk(node);
}
const focusout = () => (listeners.focusout || []).forEach(fn => fn());
const sleep = ms => new Promise(r => setTimeout(r, ms));

const root = mk('DIV', { id: 'root' });
const outer = mk('DIV', { id: 'outer', parent: root });
const ta = mk('TEXTAREA', { id: 'ta', parent: outer });
const inp = mk('INPUT', { cls: 'store-search', parent: outer });
const btn = mk('BUTTON', { id: 'btn', parent: outer });
const other = mk('TEXTAREA', { id: 'elsewhere', parent: root });

const g = await import('../../interfaces/web/static/shared/dom-guard.js');
let n = 0;
const ok = (cond, msg) => { if (!cond) throw new Error('FAIL: ' + msg); n++; };

// ── isEditable / editableFocused ──
ok(g.isEditable(ta) && g.isEditable(inp) && !g.isEditable(btn), 'textarea/input yes, button no');
ok(g.isEditable(mk('DIV', { editable: true })), 'contenteditable counts');
document.activeElement = body;
ok(!g.editableFocused(outer) && !g.editableFocused(), 'body focused = nothing focused');
document.activeElement = ta;
ok(g.editableFocused(outer) && g.editableFocused(document), 'textarea inside root');
ok(!g.editableFocused(mk('DIV')), 'textarea outside a foreign root does not count');
document.activeElement = btn;
ok(!g.editableFocused(outer), 'a focused button is not an edit');

// ── deferWhileEditing ──
let runs = 0;
const soft = g.deferWhileEditing(() => outer, () => runs++);
document.activeElement = body;
soft();
ok(runs === 1 && !soft.pending(), 'runs immediately when idle');
document.activeElement = ta;
soft(); soft(); soft();
ok(runs === 1 && soft.pending(), 'held while typing; three events coalesce');
focusout();                       // focus left but a new field may own it…
document.activeElement = other;   // …outside the root: not blocked by `outer`
await sleep(170);
ok(runs === 2 && !soft.pending(), 'catches up once after focusout + settle');
document.activeElement = ta;
soft();
focusout();
await sleep(170);                 // still in the field → stays queued
ok(runs === 2 && soft.pending(), 'stays held while the field still has focus');
document.activeElement = body;
focusout();
await sleep(170);
ok(runs === 3, 'runs when focus truly leaves');

// busy predicate + kick
let busy = true;
let runs2 = 0;
const soft2 = g.deferWhileEditing(outer, () => runs2++, { busy: () => busy });
soft2();
ok(runs2 === 0 && soft2.pending(), 'busy holds even with nothing focused');
soft2.kick(); await sleep(5);
ok(runs2 === 0, 'kick while still busy is a no-op');
busy = false;
soft2.kick(); await sleep(5);
ok(runs2 === 1 && !soft2.pending(), 'kick after busy clears runs it once');
soft2.kick(); await sleep(5);
ok(runs2 === 1, 'a spare kick with nothing pending does nothing');

// ── snapScroll ──
root.scrollTop = 0; outer.scrollTop = 120; ta.scrollTop = 7;
const restoreScroll = g.snapScroll(ta);
outer.scrollTop = 0; ta.scrollTop = 0;
restoreScroll();
ok(outer.scrollTop === 120 && ta.scrollTop === 7 && root.scrollTop === 0, 'every scrolled ancestor-or-self restored');
// inner scroller that the rebuild destroys: re-found by selector afterwards
const list = mk('DIV', { id: 'list', parent: outer }); list.scrollTop = 50;
const restoreInner = g.snapScroll(outer, ['#list', '#missing']);
outer.children.splice(outer.children.indexOf(list), 1);
const list2 = mk('DIV', { id: 'list', parent: outer });
restoreInner();
ok(list2.scrollTop === 50, 'inner scroller restored on its replacement');

// ── snapFocus ──
document.activeElement = ta; ta.sel = null;
ta.selectionStart = 3; ta.selectionEnd = 5;
let r = g.snapFocus(outer);
document.activeElement = body;
r();
ok(document.activeElement === ta && ta.sel?.[0] === 3 && ta.sel?.[1] === 5, 'refound by id, caret restored');
document.activeElement = inp; inp.selectionStart = 2; inp.selectionEnd = 2;
r = g.snapFocus(outer);
document.activeElement = body;
r();
ok(document.activeElement === inp && inp.sel?.[0] === 2, 'refound by tag + exact class when no id');
document.activeElement = btn;
r = g.snapFocus(outer);
document.activeElement = body;
r();
ok(document.activeElement === body, 'a focused button is not carried');
document.activeElement = other;
r = g.snapFocus(outer);
document.activeElement = body;
r();
ok(document.activeElement === body, 'focus outside root is not carried');

// ── focusUnlessEditing ──
document.activeElement = body; btn.focused = 0; ta.focused = 0;
g.focusUnlessEditing(ta);
ok(document.activeElement === ta && ta.focused === 1, 'focuses when nothing editable is active');
document.activeElement = other;
g.focusUnlessEditing(ta);
ok(document.activeElement === other && ta.focused === 1, 'never yanks the cursor out of another field');
document.activeElement = btn;
g.focusUnlessEditing(ta);
ok(document.activeElement === ta && ta.focused === 2, 'a button is not an edit — focus moves');
g.focusUnlessEditing(ta);
ok(ta.focused === 3, 'refocusing the already-active target is allowed');
g.focusUnlessEditing(null);
ok(true, 'null target is a no-op');

console.log(`dom-guard corpus: ${n} checks passed`);
