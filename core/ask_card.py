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

MARKER_RE = re.compile(r'<!--ASK:\{[^\n]*\}-->[ \t]*\n?')
MAX_QUESTIONS = 4
MAX_OPTIONS = 6
_LIMITS = {'question': 300, 'header': 16, 'label': 80, 'description': 200}


def _clean(value, key):
    # one line, and nothing that could close the HTML comment early
    text = re.sub(r'[\r\n]+', ' ', str(value or '')).replace('-->', '→').strip()
    return text[:_LIMITS[key]]


def normalize(questions):
    """The card's payload from a tool's `questions` argument.
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
        if q.get('multi_select'):
            item['multi_select'] = True
        out.append(item)
    return {'questions': out}


def marker(questions) -> str:
    return '<!--ASK:' + json.dumps(normalize(questions), ensure_ascii=False) + '-->'


def strip(text):
    """The model's copy: the text with every ASK marker removed."""
    if not text or '<!--ASK:' not in text:
        return text
    return MARKER_RE.sub('', text).strip()
