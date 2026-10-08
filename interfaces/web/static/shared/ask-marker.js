// shared/ask-marker.js — the ONE question-card renderer (2026-10-07). The ask_user
// tool's result carries one marker line, built by core/ask_card.py:
//   <!--ASK:{"id":"a1b2c3d4","questions":[{"question":"…","header":"…","multi_select":false,
//                          "options":[{"label":"…","description":"…"}]}]}-->
// It draws as a card in her bubble: a title row (with an × to dismiss), a tab
// per question, the options one per line, a type-your-own box, Send. The picks
// go out as the USER'S next message through the normal send path
// (sapphire:ask_answer → triggerSendWithText), so they queue in the inbox like
// anything typed. Like every door that isn't the user typing, the message leads
// with the inbox header line (core/chat/inbox.py `header`), naming THIS card:
//   [Question card a1b2c3d4 (ask_user) — what the user clicked; not typed by the user]
//   Question? → Answer            (one line per question)
// The card LOCKS and folds to its title when its answer is sent, or when the
// person types something else after it; open it again to flip through the tabs
// and see the picks — Send never fires twice. On a history reload the picks are
// read back out of the message that carries the card's id. A machine row (an
// agent's report, a MIDI take - every door that leads with `not typed by the
// user`) never locks a card: the inbox runs those right after her turn, and
// "a user row followed" locked cards nobody had answered (race scout,
// 2026-10-07). Sibling of files-marker.js; the marker is UI-only (core strips
// the model's copy).

export const ASK_RE = /<!--ASK:(\{.*?\})-->[ \t]*\n?/;
const ASK_RE_ALL = new RegExp(ASK_RE.source, 'g');
const MAX_Q = 4, MAX_OPT = 6;
export const answerHeader = (id) => `[Question card ${id} (ask_user) — what the user clicked; not typed by the user]`;
const MACHINE_ROW_RE = /^\s*\[[^\]\n]*; not typed by the user\]/;   // the inbox header: a door, not a person
export const isMachineRow = (text) => MACHINE_ROW_RE.test(text || '');
const ARROW = ' → ';

function str(v, max) { return typeof v === 'string' ? v.trim().slice(0, max) : ''; }

export const askId = (raw) => String((raw && typeof raw === 'object' && raw.id) || '').replace(/[^A-Za-z0-9]/g, '').slice(0, 16);

export function normalizeAsk(raw) {
    const list = (raw && typeof raw === 'object' && Array.isArray(raw.questions)) ? raw.questions : [];
    return list.slice(0, MAX_Q).map(q => {
        if (!q || typeof q !== 'object') return null;
        const question = str(q.question, 300);
        const options = (Array.isArray(q.options) ? q.options : []).slice(0, MAX_OPT).map(o => {
            if (typeof o === 'string') o = { label: o };
            if (!o || typeof o !== 'object') return null;
            const label = str(o.label, 80);
            return label ? { label, description: str(o.description, 200) } : null;
        }).filter(Boolean);
        if (!question || options.length < 2) return null;
        return { question, header: str(q.header, 16), options, multi: !!q.multi_select };
    }).filter(Boolean);
}

// → { cards: [{id, questions}], text } — text has every marker removed.
export function parseAskMarker(text) {
    const src = text || '';
    if (typeof src !== 'string' || !src.includes('<!--ASK:')) return { cards: [], text: src };
    const cards = [];
    for (const m of src.matchAll(ASK_RE_ALL)) {
        try {
            const raw = JSON.parse(m[1]);
            const qs = normalizeAsk(raw);
            if (qs.length) cards.push({ id: askId(raw), questions: qs });
        } catch (e) { console.warn('[Ask] bad marker JSON:', e); }
    }
    return { cards, text: src.replace(ASK_RE_ALL, '').trimEnd() };
}

// The answers for `questions` found in a sent message's text (the header line and
// any line breaks the renderer dropped don't matter: each answer runs from its
// own `Question → ` to the next question's).
export function parseAnswers(questions, text) {
    const t = typeof text === 'string' ? text : '';
    const marks = questions.map(q => t.indexOf(q.question + ARROW));
    return questions.map((q, i) => {
        if (marks[i] < 0) return undefined;
        const from = marks[i] + q.question.length + ARROW.length;
        const to = Math.min(t.length, ...marks.filter(m => m >= from));
        const raw = t.slice(from, to).trim();
        if (!raw) return undefined;
        return q.multi ? raw.split(/,\s+/).map(s => s.trim()).filter(Boolean) : raw;
    });
}

function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
}

// One card. answers[i] = string | string[] | undefined. The controller is kept
// on the element (card._ask) for the lock pass. `onSubmit(text, id)`, when
// given, takes the `Question → Answer` lines instead of the chat send (the
// agent pill's card answers the agent through its route - one card renderer,
// two doors; `id` is the agent's question id there, so a stale answer is refused).
function buildCard(questions, onSubmit = null, id = '') {
    const card = el('div', 'ask-card');
    if (id) card.dataset.askId = id;
    const answers = questions.map(() => undefined);
    const needsSend = questions.length > 1 || questions.some(q => q.multi);
    let cur = 0, locked = false, state = 'open';          // open | answered | dismissed

    const head = el('div', 'ask-head');
    const title = el('button', 'ask-title');
    title.type = 'button';
    const x = el('button', 'ask-x', '×');
    x.type = 'button';
    x.title = 'Dismiss without answering';
    head.append(title, x);
    const tabs = questions.length > 1 ? el('div', 'ask-tabs') : null;
    const body = el('div', 'ask-body');
    const foot = el('div', 'ask-foot');
    const other = el('input', 'ask-other');
    other.type = 'text';
    const sendBtn = el('button', 'ask-send', needsSend ? 'Send' : '↵');
    sendBtn.type = 'button';
    sendBtn.title = needsSend ? 'Send all answers' : 'Send';
    foot.append(other, sendBtn);
    card.appendChild(head);
    if (tabs) card.appendChild(tabs);
    card.append(body, foot);

    const answered = i => answers[i] !== undefined && answers[i] !== '' && !(Array.isArray(answers[i]) && !answers[i].length);
    const allAnswered = () => answers.every((_, i) => answered(i));
    const line = i => `${questions[i].question}${ARROW}${Array.isArray(answers[i]) ? answers[i].join(', ') : answers[i]}`;
    const summary = () => {
        const base = questions.length > 1
            ? questions.map((q, i) => q.header || `Q${i + 1}`).join(' · ')
            : questions[0].question.slice(0, 80);
        return state === 'dismissed' ? `${base} — dismissed` : (state === 'answered' ? `${base} ✓` : base);
    };

    const renderHead = () => {
        title.textContent = summary();
        title.title = locked ? (card.classList.contains('collapsed') ? 'Open' : 'Fold') : '';
        x.hidden = locked;
    };

    const renderTabs = () => {
        sendBtn.disabled = locked || (needsSend && !allAnswered());
        if (!tabs) return;
        tabs.replaceChildren(...questions.map((q, i) => {
            const t = el('button', 'ask-tab' + (i === cur ? ' active' : '') + (answered(i) ? ' done' : ''),
                         (q.header || `Q${i + 1}`) + (answered(i) ? ' ✓' : ''));
            t.type = 'button';
            t.addEventListener('click', () => { cur = i; render(); });   // tabs stay live when locked
            return t;
        }));
    };

    const render = () => {
        const q = questions[cur];
        body.replaceChildren(el('div', 'ask-q', q.question));
        const opts = el('div', 'ask-opts');
        const picked = Array.isArray(answers[cur]) ? answers[cur] : (answers[cur] !== undefined ? [answers[cur]] : []);
        q.options.forEach(o => {
            const b = el('button', 'ask-opt' + (picked.includes(o.label) ? ' picked' : ''));
            b.type = 'button';
            b.disabled = locked;
            b.appendChild(el('span', 'ask-opt-label', o.label));
            if (o.description) b.appendChild(el('span', 'ask-opt-desc', o.description));
            b.addEventListener('click', () => {
                if (locked) return;
                if (q.multi) {
                    const set = new Set(Array.isArray(answers[cur]) ? answers[cur] : []);
                    set.has(o.label) ? set.delete(o.label) : set.add(o.label);
                    answers[cur] = [...set];
                    other.value = '';
                    render();
                    return;
                }
                answers[cur] = o.label;
                other.value = '';
                if (!needsSend) { render(); submit(); return; }
                const next = questions.findIndex((_, i) => i !== cur && !answered(i));   // on to the next open tab
                if (next >= 0) cur = next;
                render();
            });
            opts.appendChild(b);
        });
        body.appendChild(opts);
        // a typed answer (anything that isn't one of the options) shows in the box
        const typedOne = typeof answers[cur] === 'string' && !q.options.some(o => o.label === answers[cur]);
        const typedMany = Array.isArray(answers[cur]) ? answers[cur].filter(a => !q.options.some(o => o.label === a)) : [];
        other.value = typedOne ? answers[cur] : typedMany.join(', ');
        other.placeholder = q.multi ? 'Add your own…' : 'Type your own…';
        other.disabled = locked;
        renderTabs();
        renderHead();
    };

    const takeTyped = () => {
        const v = other.value.trim();
        if (!v) return false;
        if (questions[cur].multi) {
            const set = new Set((Array.isArray(answers[cur]) ? answers[cur] : []).filter(a => questions[cur].options.some(o => o.label === a)));
            v.split(/,\s*/).map(s => s.trim()).filter(Boolean).forEach(s => set.add(s));
            answers[cur] = [...set];
        } else {
            answers[cur] = v;
        }
        return true;
    };

    const lock = (newState) => {
        locked = true;
        if (newState) state = newState;
        card.classList.add('locked');
        if (state === 'dismissed') card.classList.add('dismissed');
        card.classList.add('collapsed');
        render();
    };

    const submit = () => {
        if (locked) return;
        if (!needsSend && !answered(cur)) return;
        if (needsSend && !allAnswered()) { renderTabs(); return; }
        const lines = questions.map((_, i) => line(i)).join('\n');
        lock('answered');
        if (onSubmit) { onSubmit(lines, id); return; }
        document.dispatchEvent(new CustomEvent('sapphire:ask_answer', { detail: { text: answerHeader(id) + '\n' + lines, id } }));
    };

    other.addEventListener('keydown', e => {
        if (e.key !== 'Enter') return;
        e.preventDefault();
        e.stopPropagation();
        if (locked) return;
        if (!takeTyped() && !answered(cur)) return;
        if (!needsSend) { submit(); return; }
        const next = questions.findIndex((_, i) => i !== cur && !answered(i));
        if (next >= 0) cur = next;
        render();
    });
    sendBtn.addEventListener('click', () => { if (locked) return; takeTyped(); render(); submit(); });
    x.addEventListener('click', () => { if (!locked) lock('dismissed'); });
    title.addEventListener('click', () => {
        if (!locked) return;
        card.classList.contains('collapsed') ? card.classList.remove('collapsed') : card.classList.add('collapsed');
        renderHead();
    });
    // a click inside the card must not reach the bubble's own handlers
    card.addEventListener('click', e => e.stopPropagation());

    card._ask = {
        id,
        questions,
        lock,
        // the picks a sent message holds, re-applied to a reloaded card
        applyAnswers(text) {
            const found = parseAnswers(questions, text);
            let any = false;
            found.forEach((a, i) => { if (a !== undefined) { answers[i] = a; any = true; } });
            lock(any ? 'answered' : undefined);
        },
    };
    render();
    return card;
}

// cards: [{id, questions}] (a parsed marker) or [[question…]] (no id).
export function buildAskCards(cards, onSubmit = null) {
    return (cards || []).map(c => {
        const qs = Array.isArray(c) ? c : (c && c.questions);
        const id = Array.isArray(c) ? '' : String((c && c.id) || '');
        return (qs && qs.length) ? buildCard(qs, onSubmit, id) : null;
    }).filter(Boolean);
}

export function lockCard(card) {
    if (card._ask) card._ask.lock();
    else card.classList.add('locked');
}

// A card locks when ITS answer was sent (a user row carrying the card's id:
// the picks are read back out of it) or when the person typed something else
// after it (they moved on). Machine rows - the inbox header's `not typed by
// the user` - are skipped: an agent's report right after her turn is not the
// user answering. Runs after a history render and after a user message is
// added (ui.js). A card nothing was said after stays live.
export function lockAnsweredAskCards(root) {
    const cards = (root || document).querySelectorAll('.ask-card:not(.locked)');
    for (const card of cards) {
        const msg = card.closest('.message');
        if (!msg) continue;
        const id = card.dataset.askId || '';
        for (let sib = msg.nextElementSibling; sib; sib = sib.nextElementSibling) {
            const user = sib.matches('.message.user') ? sib : sib.querySelector?.('.message.user');
            if (!user) continue;
            const text = (user.querySelector?.('.message-content') || user).textContent || '';
            if (id && text.includes(`Question card ${id} `)) {
                if (card._ask) card._ask.applyAnswers(text); else lockCard(card);
                break;
            }
            if (isMachineRow(text)) continue;          // a door spoke, not the person
            lockCard(card);                            // the person moved on
            break;
        }
    }
}
