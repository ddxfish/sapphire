// Corpus for shared/ask-marker.js — run under node by tests/test_ask_marker_js.py.
// A small fake DOM proves: marker parse + strip + normalize, and the card's
// behaviour — one question/single-select sends on click; several questions are
// tabs, a pick moves to the next open tab, Send waits for all; multi-select
// toggles; a typed answer counts; the answer text is `Question → Answer` per
// line, dispatched as sapphire:ask_answer; a card locks once sent or once a
// user message follows it. 2026-10-07.

class El {
    constructor(tag) {
        this.tagName = tag.toUpperCase(); this.children = []; this.parent = null;
        this.listeners = {}; this.dataset = {}; this._cls = new Set(); this.disabled = false;
        this.textContent = ''; this.value = ''; this.type = ''; this.placeholder = ''; this.title = '';
        this.classList = {
            add: (...c) => c.forEach(x => this._cls.add(x)),
            contains: c => this._cls.has(c),
        };
    }
    get className() { return [...this._cls].join(' '); }
    set className(v) { this._cls = new Set(String(v).split(/\s+/).filter(Boolean)); }
    appendChild(c) { c.parent = this; this.children.push(c); return c; }
    append(...cs) { cs.forEach(c => this.appendChild(c)); }
    replaceChildren(...cs) { this.children = []; this.append(...cs); }
    after(n) { const i = this.parent.children.indexOf(this); n.parent = this.parent; this.parent.children.splice(i + 1, 0, n); }
    addEventListener(t, fn) { (this.listeners[t] ||= []).push(fn); }
    fire(t, ev = {}) { (this.listeners[t] || []).forEach(fn => fn({ preventDefault() {}, stopPropagation() {}, ...ev })); }
    matches(sel) { return sel.split('.').filter(Boolean).every(c => this._cls.has(c)); }
    get nextElementSibling() { const s = this.parent?.children || []; return s[s.indexOf(this) + 1] || null; }
    closest(sel) { let n = this; while (n && !n.matches(sel)) n = n.parent; return n; }
    *walk() { for (const c of this.children) { yield c; yield* c.walk(); } }
    querySelectorAll(sel) {
        const [base, not] = sel.split(':not(');
        const want = base.split('.').filter(Boolean), skip = not ? not.replace(')', '').split('.').filter(Boolean) : [];
        return [...this.walk()].filter(e => (want.length ? want.every(c => e._cls.has(c)) : true)
            && (sel.includes(',') ? true : true) && !skip.some(c => e._cls.has(c)));
    }
    querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
}
// the lock helper asks for 'button, input' — serve both
const _qsa = El.prototype.querySelectorAll;
El.prototype.querySelectorAll = function (sel) {
    if (sel === 'button, input') return [...this.walk()].filter(e => e.tagName === 'BUTTON' || e.tagName === 'INPUT');
    return _qsa.call(this, sel);
};

const sent = [];
globalThis.CustomEvent = class { constructor(type, init) { this.type = type; this.detail = init?.detail; } };
globalThis.document = { createElement: t => new El(t), dispatchEvent: ev => { sent.push(ev); return true; } };
globalThis.console = { ...console, warn: () => {} };

const { ASK_RE, normalizeAsk, parseAskMarker, buildAskCards, lockCard, lockAnsweredAskCards } =
    await import('../../interfaces/web/static/shared/ask-marker.js');

let passed = 0;
function ok(cond, msg) { if (!cond) throw new Error('FAIL: ' + msg); passed++; }
const mark = qs => '<!--ASK:' + JSON.stringify({ questions: qs }) + '-->';
const byCls = (root, c) => root.querySelectorAll('.' + c);
const opts = card => byCls(card, 'ask-opt');
const tabs = card => byCls(card, 'ask-tab');
const other = card => byCls(card, 'ask-other')[0];
const sendBtn = card => byCls(card, 'ask-send')[0];

// ── parse ────────────────────────────────────────────────────────────────────
{
    const text = 'Card shown.\n' + mark([{ question: 'Fav color?', header: 'Color',
        options: [{ label: 'Blue', description: 'calm' }, 'Red', { label: 'Red' }, { nope: 1 }] }]);
    const r = parseAskMarker(text);
    ok(r.text === 'Card shown.', 'marker stripped from shown text');
    ok(r.cards.length === 1 && r.cards[0][0].question === 'Fav color?', 'one card, one question');
    ok(r.cards[0][0].options.map(o => o.label).join() === 'Blue,Red,Red', 'string options accepted, junk dropped');
    ok(r.cards[0][0].options[0].description === 'calm' && r.cards[0][0].header === 'Color', 'description + header kept');
    ok(parseAskMarker('plain').cards.length === 0 && parseAskMarker(null).text === '', 'no marker / null safe');
    const list = [{ type: 'text', text: 'x' }];
    ok(parseAskMarker(list).text === list, 'non-string content passes through');
    const bad = parseAskMarker('x\n<!--ASK:{not json}-->\ny');
    ok(bad.cards.length === 0 && !bad.text.includes('ASK'), 'bad JSON: no card, marker still stripped');
    ok(normalizeAsk({ questions: [{ question: 'q', options: ['a'] }] }).length === 0, 'one option is no question');
    ok(normalizeAsk({ questions: Array(6).fill({ question: 'q', options: ['a', 'b'] }) }).length === 4, 'four questions max');
    ok(ASK_RE.test('<!--ASK:{}-->') && !ASK_RE.test('<!--ASK:[]-->'), 'only ASK with an object body');
}

// ── one question, single-select: a click sends ───────────────────────────────
{
    sent.length = 0;
    const [card] = buildAskCards(parseAskMarker(mark([{ question: 'Fav color?', options: ['Blue', 'Red'] }])).cards);
    ok(tabs(card).length === 0, 'no tabs for one question');
    ok(sendBtn(card).textContent === '↵', 'single question shows a return key, not Send');
    opts(card)[1].fire('click');
    ok(sent.length === 1 && sent[0].type === 'sapphire:ask_answer', 'a click dispatches the answer');
    ok(sent[0].detail.text === 'Fav color? → Red', 'answer line is `Question → Answer`');
    ok(card.classList.contains('locked') && opts(card).every(b => b.disabled), 'card locks after sending');
    ok(opts(card)[1].classList.contains('picked'), 'the pick stays highlighted');
    opts(card)[0].fire('click');
    ok(sent.length === 1, 'a locked card sends nothing more');
}

// ── typed answer ─────────────────────────────────────────────────────────────
{
    sent.length = 0;
    const [card] = buildAskCards(parseAskMarker(mark([{ question: 'Fav color?', options: ['Blue', 'Red'] }])).cards);
    other(card).value = '  teal-ish ';
    other(card).fire('keydown', { key: 'Enter' });
    ok(sent.length === 1 && sent[0].detail.text === 'Fav color? → teal-ish', 'Enter sends the typed answer, trimmed');
    const [card2] = buildAskCards(parseAskMarker(mark([{ question: 'Fav color?', options: ['Blue', 'Red'] }])).cards);
    other(card2).fire('keydown', { key: 'Enter' });
    sendBtn(card2).fire('click');
    ok(sent.length === 1, 'empty Enter / Send sends nothing');
}

// ── three questions: tabs, advance, Send waits for all ───────────────────────
{
    sent.length = 0;
    const qs = [{ question: 'Color?', header: 'Color', options: ['Blue', 'Red'] },
                { question: 'Pet?', options: ['Dog', 'Cat'] },
                { question: 'Snack?', header: 'Snack', options: ['Chips', 'Fruit'] }];
    const [card] = buildAskCards(parseAskMarker(mark(qs)).cards);
    ok(tabs(card).map(t => t.textContent).join('|') === 'Color|Q2|Snack', 'a tab per question, header or Qn');
    ok(tabs(card)[0].classList.contains('active') && sendBtn(card).disabled, 'first tab active, Send waits');
    ok(byCls(card, 'ask-q')[0].textContent === 'Color?', 'first question shown');
    opts(card)[0].fire('click');                                   // Blue
    ok(sent.length === 0, 'a pick on a multi-question card does not send');
    ok(tabs(card)[0].textContent === 'Color ✓' && tabs(card)[1].classList.contains('active'), 'answered tab ticks, next tab opens');
    ok(byCls(card, 'ask-q')[0].textContent === 'Pet?', 'second question shown');
    tabs(card)[2].fire('click');                                   // jump to Snack
    opts(card)[1].fire('click');                                   // Fruit
    ok(tabs(card)[1].classList.contains('active') && sendBtn(card).disabled, 'back to the open tab; Send still waits');
    sendBtn(card).fire('click');
    ok(sent.length === 0 && !card.classList.contains('locked'), 'Send with a question open does nothing');
    other(card).value = 'a cat named Steve';
    other(card).fire('keydown', { key: 'Enter' });
    ok(!sendBtn(card).disabled, 'all answered: Send enabled');
    ok(sent.length === 0, 'Enter on the last open tab does not send by itself');
    sendBtn(card).fire('click');
    ok(sent.length === 1 && sent[0].detail.text === 'Color? → Blue\nPet? → a cat named Steve\nSnack? → Fruit',
       'one line per question, in question order');
    ok(card.classList.contains('locked'), 'locked after Send');
}

// ── multi-select ─────────────────────────────────────────────────────────────
{
    sent.length = 0;
    const [card] = buildAskCards(parseAskMarker(mark([{ question: 'Toppings?', options: ['Ham', 'Olives', 'Corn'], multi_select: true }])).cards);
    ok(sendBtn(card).textContent === 'Send' && sendBtn(card).disabled, 'multi-select needs Send, disabled until a pick');
    opts(card)[0].fire('click'); opts(card)[2].fire('click');
    ok(opts(card)[0].classList.contains('picked') && opts(card)[2].classList.contains('picked'), 'two picked');
    opts(card)[0].fire('click');
    ok(!opts(card)[0].classList.contains('picked'), 'a second click unpicks');
    other(card).value = 'pineapple';
    other(card).fire('keydown', { key: 'Enter' });
    sendBtn(card).fire('click');
    ok(sent.length === 1 && sent[0].detail.text === 'Toppings? → Corn, pineapple', 'picks and a typed one, comma-joined');
}

// ── lock when a user message follows ─────────────────────────────────────────
{
    const chat = new El('div');
    const mk = (role, card) => { const m = new El('div'); m.className = 'message ' + role; const c = new El('div'); m.appendChild(c); if (card) c.appendChild(card); chat.appendChild(m); return m; };
    const card1 = buildAskCards(parseAskMarker(mark([{ question: 'A?', options: ['x', 'y'] }])).cards)[0];
    const card2 = buildAskCards(parseAskMarker(mark([{ question: 'B?', options: ['x', 'y'] }])).cards)[0];
    mk('assistant', card1); mk('user'); mk('assistant', card2);
    lockAnsweredAskCards(chat);
    ok(card1.classList.contains('locked') && opts(card1).every(b => b.disabled), 'a card the user spoke after is locked');
    ok(!card2.classList.contains('locked'), 'the card in the last message stays live');
    mk('assistant');                                  // her next turn (an agent report, say) does not lock it
    lockAnsweredAskCards(chat);
    ok(!card2.classList.contains('locked'), 'another assistant message does not lock it');
    mk('user');
    lockAnsweredAskCards(chat);
    ok(card2.classList.contains('locked'), 'the user speaking locks it');
    lockCard(card2);
    ok(card2.classList.contains('locked'), 'lockCard is idempotent');
}

console.log(`ask-marker corpus: ${passed} checks passed`);
