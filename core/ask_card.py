"""core/ask_card.py - a question card the AI shows the user (the ask_user tool).

ONE marker, a sibling of FILES (core/attachments.py) and GALLERY:
    <!--ASK:{"questions": [{"question": "...", "header": "...", "multi_select": false,
                            "options": [{"label": "...", "description": "..."}]}]}-->
The chat renders it as a card in her bubble: one tab per question, the options
one per line, a type-your-own box, an × to dismiss (shared/ask-marker.js). The
user's picks are sent as THEIR next message, led by the inbox header line
`[Question card (ask_user) — what the user clicked; not typed by the user]`
(inbox.header - the same line every not-typed door leads with) then one
`Question? → Answer` line per question. So the answer is an ordinary turn: it
queues in the inbox like anything typed, reads naturally in history, and works
on voice by just saying it. After Send the card folds to its title, reopenable
to see the picks; a reload reads them back out of that message. UI-only: kept
in history for the browser, stripped from every copy the model reads
(core/ui_markers.py, shared by strip_ui_markers and history).

Same shape as Claude Code's AskUserQuestion so an agent's fork can be relayed
as the same card one day: 1-4 questions, 2-6 options each.
"""
import json
import re
import uuid

# Non-greedy to the first `}-->`: two markers on one line used to be eaten as
# one (and the prose between them with it). Safe because _clean keeps `-->`
# out of every field (second scout wave, 2026-10-08).
MARKER_RE = re.compile(r'<!--ASK:\{.*?\}-->[ \t]*\n?')
MAX_QUESTIONS = 4
MAX_OPTIONS = 6
_LIMITS = {'question': 300, 'header': 16, 'label': 80, 'description': 200}


def _clean(value, key):
    # one line, and nothing that could close the HTML comment early
    text = re.sub(r'[\r\n]+', ' ', str(value or '')).replace('-->', '→').strip()
    return text[:_LIMITS[key]]


def _flag(v):
    # local models send "false" as a string; the truthy string drew a multi-select
    if isinstance(v, str):
        return v.strip().lower() in ('1', 'true', 'yes', 'on')
    return bool(v)


def normalize(questions, card_id=None):
    """The card's payload from a tool's `questions` argument: {id, questions}.
    `id` names THIS card - the answer message echoes it in its header line, and
    that is what locks the card (not "some user row followed it": an agent's
    report is a user row too, and locked cards nobody had answered).
    Raises ValueError with a message the model can act on."""
    if isinstance(questions, str):
        try:
            questions = json.loads(questions)
        except ValueError:
            pass
    if isinstance(questions, dict):
        questions = [questions]
    if not isinstance(questions, list) or not questions:
        raise ValueError("questions must be a list of 1-%d {question, options} objects" % MAX_QUESTIONS)
    if len(questions) > MAX_QUESTIONS:
        raise ValueError("at most %d questions per card" % MAX_QUESTIONS)
    out = []
    for i, q in enumerate(questions, 1):
        if not isinstance(q, dict):
            raise ValueError(f"question {i} must be an object")
        text = _clean(q.get('question'), 'question')
        if not text:
            raise ValueError(f"question {i} has no text")
        opts = []
        raw_opts = q.get('options') or []
        if isinstance(raw_opts, str):
            # local models stringify nested arrays; iterating the string drew one
            # option per CHARACTER and said success (chaos scout, 2026-10-07)
            try:
                raw_opts = json.loads(raw_opts)
            except ValueError:
                raw_opts = [p.strip() for p in raw_opts.split(',') if p.strip()]
            if not isinstance(raw_opts, list):
                raw_opts = []
        for o in list(raw_opts)[:MAX_OPTIONS]:
            if isinstance(o, str):
                o = {'label': o}
            if not isinstance(o, dict):
                continue
            label = _clean(o.get('label'), 'label')
            if not label or any(x['label'].lower() == label.lower() for x in opts):
                continue
            opt = {'label': label}
            desc = _clean(o.get('description'), 'description')
            if desc:
                opt['description'] = desc
            opts.append(opt)
        if len(opts) < 2:
            raise ValueError(f"question {i} needs 2-{MAX_OPTIONS} distinct options (the user can always type their own)")
        item = {'question': text, 'options': opts}
        header = _clean(q.get('header'), 'header')
        if header:
            item['header'] = header
        if _flag(q.get('multi_select')):
            item['multi_select'] = True
        out.append(item)
    cid = re.sub(r'[^A-Za-z0-9]', '', str(card_id or ''))[:16] or uuid.uuid4().hex[:8]
    return {'id': cid, 'questions': out}


def marker(questions, card_id=None) -> str:
    return '<!--ASK:' + json.dumps(normalize(questions, card_id), ensure_ascii=False) + '-->'


def answer_who(card_id) -> str:
    """The `who` of the answer's header line - `[Question card <id> (ask_user) —
    what the user clicked; not typed by the user]` - built with inbox.header
    so every not-typed door reads the same. The id is what the browser matches."""
    return f"Question card {card_id}"


def strip(text):
    """The model's copy: the text with every ASK marker removed."""
    if not text or '<!--ASK:' not in text:
        return text
    return MARKER_RE.sub('', text).strip()
