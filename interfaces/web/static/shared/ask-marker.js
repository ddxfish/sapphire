// shared/ask-marker.js — the ONE question-card renderer (2026-10-07). The ask_user
// tool's result carries one marker line, built by core/ask_card.py:
//   <!--ASK:{"questions":[{"question":"…","header":"…","multi_select":false,
//                          "options":[{"label":"…","description":"…"}]}]}-->
// It draws as a card in her bubble: a tab per question, the options as buttons,
// a type-your-own box. The picks are sent as the USER'S next message —
//   `Question? → Answer`, one line per question —
// through the normal send path (sapphire:ask_answer → triggerSendWithText), so
// the answer queues in the inbox like anything typed. Sibling of
// files-marker.js; like it the marker is UI-only (core strips the model's copy).
// A card locks once a user message follows it (lockAnsweredAskCards), and the
// tab's own answer stays highlighted in the session it was given.

export const ASK_RE = /<!--ASK:(\{[^\n]*\})-->[ \t]*\n?/;
const ASK_RE_ALL = new RegExp(ASK_RE.source, 'g');
const MAX_Q = 4, MAX_OPT = 6;

function str(v, max) { return typeof v === 'string' ? v.trim().slice(0, max) : ''; }

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

// → { cards: [[question…]], text } — text has every marker removed.
export function parseAskMarker(text) {
    const src = text || '';
    if (typeof src !== 'string' || !src.includes('<!--ASK:')) return { cards: [], text: src };
    const cards = [];
    for (const m of src.matchAll(ASK_RE_ALL)) {
        try {
            const qs = normalizeAsk(JSON.parse(m[1]));
            if (qs.length) cards.push(qs);
        } catch (e) { console.warn('[Ask] bad marker JSON:', e); }
    }
    return { cards, text: src.replace(ASK_RE_ALL, '').trimEnd() };
}

function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
}

// One card. answers[i] = string | string[] | undefined.
function buildCard(questions) {
    const card = el('div', 'ask-card');
    const answers = questions.map(() => undefined);
    const needsSend = questions.length > 1 || questions.some(q => q.multi);
    let cur = 0;

    const tabs = questions.length > 1 ? el('div', 'ask-tabs') : null;
    const body = el('div', 'ask-body');
    const foot = el('div', 'ask-foot');
    const other = el('input', 'ask-other');
    other.type = 'text';
    other.placeholder = 'Type your own…';
    const sendBtn = el('button', 'ask-send', needsSend ? 'Send' : '↵');
    sendBtn.type = 'button';
    sendBtn.title = needsSend ? 'Send all answers' : 'Send';
    foot.append(other, sendBtn);
    if (tabs) card.appendChild(tabs);
    card.append(body, foot);

    const answered = i => answers[i] !== undefined && answers[i] !== '' && !(Array.isArray(answers[i]) && !answers[i].length);
    const allAnswered = () => answers.every((_, i) => answered(i));
    const line = i => {
        const a = answers[i];
        return `${questions[i].question} → ${Array.isArray(a) ? a.join(', ') : a}`;
    };

    const submit = () => {
        if (card.classList.contains('locked')) return;
        if (!needsSend && !answered(cur)) return;
        if (needsSend && !allAnswered()) { renderTabs(); return; }
        const text = questions.map((_, i) => line(i)).join('\n');
        lockCard(card);
        document.dispatchEvent(new CustomEvent('sapphire:ask_answer', { detail: { text } }));
    };

    const renderTabs = () => {
        sendBtn.disabled = needsSend && !allAnswered();
        if (!tabs) return;
        tabs.replaceChildren(...questions.map((q, i) => {
            const t = el('button', 'ask-tab' + (i === cur ? ' active' : '') + (answered(i) ? ' done' : ''),
                         (q.header || `Q${i + 1}`) + (answered(i) ? ' ✓' : ''));
            t.type = 'button';
            t.addEventListener('click', () => { cur = i; render(); });
            return t;
        }));
    };

    const render = () => {
        const q = questions[cur];
        body.replaceChildren();
        body.appendChild(el('div', 'ask-q', q.question));
        const opts = el('div', 'ask-opts');
        const picked = Array.isArray(answers[cur]) ? answers[cur] : (answers[cur] !== undefined ? [answers[cur]] : []);
        q.options.forEach(o => {
            const b = el('button', 'ask-opt' + (picked.includes(o.label) ? ' picked' : ''));
            b.type = 'button';
            b.appendChild(el('span', 'ask-opt-label', o.label));
            if (o.description) b.appendChild(el('span', 'ask-opt-desc', o.description));
            b.addEventListener('click', () => {
                if (card.classList.contains('locked')) return;
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
                // single-select on a multi-question card: on to the next unanswered tab
                const next = questions.findIndex((_, i) => i !== cur && !answered(i));
                if (next >= 0) cur = next;
                render();
            });
            opts.appendChild(b);
        });
        body.appendChild(opts);
        const typed = typeof answers[cur] === 'string' && !q.options.some(o => o.label === answers[cur]);
        other.value = typed ? answers[cur] : '';
        other.placeholder = q.multi ? 'Add your own…' : 'Type your own…';
        renderTabs();
    };

    const takeTyped = () => {
        const v = other.value.trim();
        if (!v) return false;
        const q = questions[cur];
        if (q.multi) {
            const set = new Set(Array.isArray(answers[cur]) ? answers[cur] : []);
            set.add(v);
            answers[cur] = [...set];
        } else {
            answers[cur] = v;
        }
        other.value = '';
        return true;
    };
    other.addEventListener('keydown', e => {
        if (e.key !== 'Enter') return;
        e.preventDefault();
        e.stopPropagation();
        if (!takeTyped() && !answered(cur)) return;
        if (!needsSend) { submit(); return; }
        const next = questions.findIndex((_, i) => i !== cur && !answered(i));
        if (next >= 0) cur = next;
        render();
    });
    sendBtn.addEventListener('click', () => { takeTyped(); render(); submit(); });
    // a click inside the card must not reach the bubble's own handlers
    card.addEventListener('click', e => e.stopPropagation());

    render();
    return card;
}

export function buildAskCards(cards) {
    return (cards || []).map(qs => (qs && qs.length) ? buildCard(qs) : null).filter(Boolean);
}

export function lockCard(card) {
    card.classList.add('locked');
    card.querySelectorAll('button, input').forEach(b => { b.disabled = true; });
}

// A card is answerable only until the user speaks again: lock every card that
// has a user message after it. Runs after a history render and after a user
// message is added (ui.js). Cards in the final assistant message stay live.
export function lockAnsweredAskCards(root) {
    const cards = (root || document).querySelectorAll('.ask-card:not(.locked)');
    for (const card of cards) {
        let msg = card.closest('.message');
        if (!msg) continue;
        for (let sib = msg.nextElementSibling; sib; sib = sib.nextElementSibling) {
            if (sib.matches('.message.user') || sib.querySelector?.('.message.user')) { lockCard(card); break; }
        }
    }
}
