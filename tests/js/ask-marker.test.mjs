// Corpus for shared/ask-marker.js — run under node by tests/test_ask_marker_js.py.
// A small fake DOM proves: marker parse + strip + normalize, and the card's
// behaviour — one question/single-select sends on click; several questions are
// tabs, a pick moves to the next open tab, Send waits for all; multi-select
// toggles; a typed answer counts; the answer text leads with the inbox header
// line then `Question → Answer` per line, dispatched as sapphire:ask_answer; ×
// dismisses; after Send the card locks and folds to its title, opens again with
// the tabs browsable and Send dead; a reloaded card reads its picks back out of
// the user message that answered it. 2026-10-07.

class El {
    constructor(tag) {
        this.tagName = tag.toUpperCase(); this.children = []; this.parent = null;
        this.listeners = {}; this.dataset = {}; this._cls = new Set(); this.disabled = false;
        this.textContent = ''; this.value = ''; this.type = ''; this.placeholder = ''; this.title = '';
        this.hidden = false;
        this.classList = {
            add: (...c) => c.forEach(x => this._cls.add(x)),
            remove: (...c) => c.forEach(x => this._cls.delete(x)),
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

const { ASK_RE, answerHeader, isMachineRow, normalizeAsk, parseAskMarker, parseAnswers, buildAskCards, lockCard, lockAnsweredAskCards } =
    await import('../../interfaces/web/static/shared/ask-marker.js');

let passed = 0;
function ok(cond, msg) { if (!cond) throw new Error('FAIL: ' + msg); passed++; }
const mark = (qs, id = 'c0ffee01') => '<!--ASK:' + JSON.stringify({ id, questions: qs }) + '-->';
const byCls = (root, c) => root.querySelectorAll('.' + c);
const opts = card => byCls(card, 'ask-opt');
const tabs = card => byCls(card, 'ask-tab');
const other = card => byCls(card, 'ask-other')[0];
const sendBtn = card => byCls(card, 'ask-send')[0];
const title = card => byCls(card, 'ask-title')[0];
const xBtn = card => byCls(card, 'ask-x')[0];
const HEAD = answerHeader('c0ffee01') + '\n';
ok(/^\[Question card c0ffee01 \(ask_user\) \u2014 .*; not typed by the user\]$/.test(answerHeader('c0ffee01')), 'header follows the inbox header grammar and names the card');
ok(isMachineRow(HEAD + 'x') && isMachineRow('  [Agent Spark (claude_code) \u2014 reports; not typed by the user]\nhi') && !isMachineRow('[not a header] hi') && !isMachineRow('hello'), 'machine rows are the inbox header');

// ── parse ────────────────────────────────────────────────────────────────────
{
    const text = 'Card shown.\n' + mark([{ question: 'Fav color?', header: 'Color',
        options: [{ label: 'Blue', description: 'calm' }, 'Red', { label: 'Red' }, { nope: 1 }] }]);
    const r = parseAskMarker(text);
    ok(r.text === 'Card shown.', 'marker stripped from shown text');
    ok(r.cards.length === 1 && r.cards[0].id === 'c0ffee01' && r.cards[0].questions[0].question === 'Fav color?', 'one card with its id, one question');
    ok(r.cards[0].questions[0].options.map(o => o.label).join() === 'Blue,Red,Red', 'string options accepted, junk dropped');
    ok(r.cards[0].questions[0].options[0].description === 'calm' && r.cards[0].questions[0].header === 'Color', 'description + header kept');
    const twoOnOneLine = parseAskMarker('a ' + mark([{ question: 'Q1?', options: ['x', 'y'] }], 'aaaa') + ' between ' + mark([{ question: 'Q2?', options: ['x', 'y'] }], 'bbbb') + ' z');
    ok(twoOnOneLine.cards.length === 2 && twoOnOneLine.text === 'a between z', 'two markers on one line: two cards, the prose between them kept');
    ok(parseAskMarker('plain').cards.length === 0 && parseAskMarker(null).text === '', 'no marker / null safe');
    const list = [{ type: 'text', text: 'x' }];
    ok(parseAskMarker(list).text === list, 'non-string content passes through');
    const bad = parseAskMarker('x\n<!--ASK:{not json}-->\ny');
    ok(bad.cards.length === 0 && !bad.text.includes('ASK'), 'bad JSON: no card, marker still stripped');
    ok(normalizeAsk({ questions: [{ question: 'q', options: ['a'] }] }).length === 0, 'one option is no question');
    ok(normalizeAsk({ questions: Array(6).fill({ question: 'q', options: ['a', 'b'] }) }).length === 4, 'four questions max');
    ok(ASK_RE.test('<!--ASK:{}-->') && !ASK_RE.test('<!--ASK:[]-->'), 'only ASK with an object body');
    ok(parseAskMarker(mark([{ question: 'q', options: ['a', 'b'] }], 'bad id!')).cards[0].id === 'badid', 'ids are alphanumeric');
}

// ── one question, single-select: a click sends ───────────────────────────────
{
    sent.length = 0;
    const [card] = buildAskCards(parseAskMarker(mark([{ question: 'Fav color?', options: ['Blue', 'Red'] }])).cards);
    ok(tabs(card).length === 0, 'no tabs for one question');
    ok(sendBtn(card).textContent === '↵', 'single question shows a return key, not Send');
    opts(card)[1].fire('click');
    ok(sent.length === 1 && sent[0].type === 'sapphire:ask_answer', 'a click dispatches the answer');
    ok(sent[0].detail.text === HEAD + 'Fav color? → Red', 'header line, then `Question → Answer`');
    ok(card.classList.contains('locked') && opts(card).every(b => b.disabled), 'card locks after sending');
    ok(card.classList.contains('collapsed') && title(card).textContent === 'Fav color? ✓', 'folds to its title with a tick');
    ok(xBtn(card).hidden, 'the × goes once locked');
    title(card).fire('click');
    ok(!card.classList.contains('collapsed'), 'the title opens it again');
    ok(opts(card)[1].classList.contains('picked') && sendBtn(card).disabled, 'the pick is shown; Send is dead');
    opts(card)[0].fire('click'); sendBtn(card).fire('click');
    ok(sent.length === 1, 'a locked card sends nothing more');
    title(card).fire('click');
    ok(card.classList.contains('collapsed'), 'and folds again');
}

// ── typed answer ─────────────────────────────────────────────────────────────
{
    sent.length = 0;
    const [card] = buildAskCards(parseAskMarker(mark([{ question: 'Fav color?', options: ['Blue', 'Red'] }])).cards);
    other(card).value = '  teal-ish ';
    other(card).fire('keydown', { key: 'Enter' });
    ok(sent.length === 1 && sent[0].detail.text === HEAD + 'Fav color? → teal-ish', 'Enter sends the typed answer, trimmed');
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
    ok(sent.length === 1 && sent[0].detail.text === HEAD + 'Color? → Blue\nPet? → a cat named Steve\nSnack? → Fruit',
       'one line per question, in question order');
    ok(card.classList.contains('locked') && card.classList.contains('collapsed'), 'locked and folded after Send');
    ok(title(card).textContent === 'Color · Q2 · Snack ✓', 'title is the question group');
    title(card).fire('click');
    tabs(card)[2].fire('click');
    ok(byCls(card, 'ask-q')[0].textContent === 'Snack?' && opts(card)[1].classList.contains('picked'), 'tabs still flip when locked, picks shown');
    tabs(card)[1].fire('click');
    ok(other(card).value === 'a cat named Steve' && other(card).disabled, 'the typed answer shows, read-only');
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
    ok(sent.length === 1 && sent[0].detail.text === HEAD + 'Toppings? → Corn, pineapple', 'picks and a typed one, comma-joined');
}

// ── × dismisses ──────────────────────────────────────────────────────────────
{
    sent.length = 0;
    const [card] = buildAskCards(parseAskMarker(mark([{ question: 'Fav color?', options: ['Blue', 'Red'] }])).cards);
    ok(!xBtn(card).hidden, 'a live card has its ×');
    xBtn(card).fire('click');
    ok(sent.length === 0, 'dismissing sends nothing');
    ok(card.classList.contains('locked') && card.classList.contains('dismissed') && card.classList.contains('collapsed'), 'locked, dismissed, folded');
    ok(title(card).textContent === 'Fav color? \u2014 dismissed', 'title says so');
    title(card).fire('click');
    ok(!card.classList.contains('collapsed') && opts(card).every(b => b.disabled), 'opens read-only');
}

// ── parseAnswers: the sent message read back ─────────────────────────────────
{
    const qs = parseAskMarker(mark([{ question: 'Color?', options: ['Blue', 'Red'] },
        { question: 'Toppings?', options: ['Ham', 'Corn'], multi_select: true }, { question: 'Snack?', options: ['Chips', 'Fruit'] }])).cards[0].questions;
    const a = parseAnswers(qs, HEAD + 'Color? \u2192 Blue\nToppings? \u2192 Corn, pineapple\nSnack? \u2192 Fruit');
    ok(a[0] === 'Blue' && a[2] === 'Fruit', 'single answers read back');
    ok(Array.isArray(a[1]) && a[1].join('|') === 'Corn|pineapple', 'multi answers split');
    const flat = parseAnswers(qs, 'Color? \u2192 Blue Toppings? \u2192 Ham Snack? \u2192 my own thing');
    ok(flat[0] === 'Blue' && flat[1][0] === 'Ham' && flat[2] === 'my own thing', 'line breaks the renderer dropped do not matter');
    const partial = parseAnswers(qs, 'Snack? \u2192 Chips');
    ok(partial[0] === undefined && partial[2] === 'Chips', 'missing questions stay unanswered');
    ok(parseAnswers(qs, 'I changed my mind, pizza').every(x => x === undefined), 'a plain reply answers nothing');
}

// ── lock when a user message follows; picks read back on reload ─────────────
{
    const chat = new El('div');
    const mk = (role, card, text) => {
        const m = new El('div'); m.className = 'message ' + role;
        const c = new El('div'); c.className = 'message-content'; if (text) c.textContent = text; m.appendChild(c);
        if (card) c.appendChild(card); chat.appendChild(m); return m;
    };
    const two = () => buildAskCards(parseAskMarker(mark([{ question: 'Color?', header: 'Color', options: ['Blue', 'Red'] },
                                                        { question: 'Pet?', header: 'Pet', options: ['Dog', 'Cat'] }])).cards)[0];
    const card1 = two(), card2 = two(), card3 = two();
    mk('assistant', card1); mk('user', null, HEAD + 'Color? \u2192 Red\nPet? \u2192 a goat');
    mk('assistant', card2); mk('user', null, 'actually never mind');
    mk('assistant', card3);
    lockAnsweredAskCards(chat);
    ok(card1.classList.contains('locked') && card1.classList.contains('collapsed'), 'an answered card is locked and folded');
    ok(title(card1).textContent === 'Color \u00b7 Pet \u2713', 'and ticked: its picks were read back');
    title(card1).fire('click');
    ok(opts(card1)[1].classList.contains('picked'), 'Red is shown picked');
    tabs(card1)[1].fire('click');
    ok(other(card1).value === 'a goat', 'the typed answer is shown');
    ok(card2.classList.contains('locked') && title(card2).textContent === 'Color \u00b7 Pet', 'a card answered in free text locks without a tick');
    ok(!card3.classList.contains('locked'), 'the card in the last message stays live');
    mk('assistant');                                  // her next turn does not lock it
    lockAnsweredAskCards(chat);
    ok(!card3.classList.contains('locked'), 'another assistant message does not lock it');
    // an agent's report lands as a user row right after her turn (the inbox runs
    // it the moment she ends) - a door spoke, not the person: the card stays live
    mk('user', null, '[Agent Spark (claude_code) \u2014 reports (2m in); not typed by the user]\nAll tests pass.');
    mk('assistant');
    lockAnsweredAskCards(chat);
    ok(!card3.classList.contains('locked'), 'a machine row (agent report) does not lock it');
    // another card's answer is a machine row too - and not THIS card's id
    mk('user', null, answerHeader('other001') + '\nColor? \u2192 Blue\nPet? \u2192 Dog');
    lockAnsweredAskCards(chat);
    ok(!card3.classList.contains('locked'), "another card's answer does not lock it");
    mk('user', null, 'hi');
    lockAnsweredAskCards(chat);
    ok(card3.classList.contains('locked') && title(card3).textContent === 'Color \u00b7 Pet', 'the person typing something else locks it, no tick');
    lockCard(card3);
    ok(card3.classList.contains('locked'), 'lockCard is idempotent');
    // the answer carries the card's id: a reload matches by it even with rows between
    const card4 = two();
    mk('assistant', card4);
    mk('user', null, '[Agent Spark (claude_code) \u2014 asks; not typed by the user]\nWhich DB?');
    mk('assistant');
    mk('user', null, HEAD + 'Color? \u2192 Blue\nPet? \u2192 Cat');
    lockAnsweredAskCards(chat);
    ok(card4.classList.contains('locked') && title(card4).textContent === 'Color \u00b7 Pet \u2713', 'the answer is found past a machine row, by id');
    ok(card4.dataset.askId === 'c0ffee01', 'the card knows its id');
}

// ── onSubmit: the agent pill's card answers through its route, not the chat ──
{
    sent.length = 0;
    const got = [];
    const qs = parseAskMarker(mark([{ question: 'Framework?', header: 'Framework', options: ['FastAPI', 'Flask'] },
                                    { question: 'Tests?', options: ['pytest', 'unittest'] }])).cards[0].questions;
    const ids = [];
    const [card] = buildAskCards([{ id: 'q-77', questions: qs }], (text, id) => { got.push(text); ids.push(id); });
    opts(card)[1].fire('click');                      // Flask → next tab
    opts(card)[0].fire('click');                      // pytest
    sendBtn(card).fire('click');
    ok(got.length === 1 && got[0] === 'Framework? \u2192 Flask\nTests? \u2192 pytest', 'per-question lines go to onSubmit');
    ok(ids[0] === 'q-77', 'with the question id (a stale answer is refused server-side)');
    ok(buildAskCards([qs]).length === 1, 'a bare question list still builds (no id)');
    ok(sent.length === 0, 'and nothing is sent as a chat message');
    ok(card.classList.contains('locked'), 'the card locks all the same');
}

console.log(`ask-marker corpus: ${passed} checks passed`);
