# core/agents/base.py - what every agent kind gets for free (tmp/agents-v2.md §3.4)
#
# The engine makes one Agent per spawn, bound to a chat, and starts it on its
# own thread with the spawning turn's context carried over and the chat's
# stream-brain override installed (so hooks and tools inside the agent read
# ITS chat's settings and privacy, never the operator's open chat). A kind
# subclasses this and writes the work in run(mission) - and say(text)/stop()
# when it is conversational. It talks back through three doors and never
# touches the chat itself:
#
#   self.ask(question)  -> the answer, or None. BLOCKS this agent until the
#                          director answers (agent_action answer) or ASK_TIMEOUT_S.
#   self.event(kind, d) -> a transcript line (text, tool_use, tool_result, note);
#                          a bounded ring the UI and agent_peek read, never the chat.
#   self.report(text)   -> the turn's result; the engine delivers it into the chat
#                          through the inbox, no browser needed.
import contextvars
import json
import logging
import threading
import time
import uuid
from collections import deque

logger = logging.getLogger(__name__)

ASK_TIMEOUT_S = 600            # a fork question waits this long for the director (Krem: 10 min)
TRANSCRIPT_RING = 400          # events kept per agent, in memory only
TERMINAL = ('done', 'failed', 'stopped', 'lost')
STATUSES = ('pending', 'running', 'waiting', 'idle', 'resting') + TERMINAL


class Agent:
    """Base class for agent kinds. Plugins subclass this (module named in the
    kind's manifest entry, class named Agent)."""

    def __init__(self, row, engine):
        self.id = row['id']
        self.name = row['name']
        self.kind = row['kind']
        self.chat = row.get('chat') or ''
        self.mission = row.get('mission') or ''
        self.options = dict(row.get('options') or {})
        self.privacy = bool(row.get('privacy'))       # the spawning chat was private: local only
        self.resume_token = row.get('resume_token')
        self._engine = engine
        self._status = 'pending'
        self._status_lock = threading.Lock()
        self.result = None
        self.error = None
        self.warning = None                            # a run that completed as a placeholder (amber pill)
        self._cancelled = threading.Event()
        self._thread = None
        self._start_time = None
        self._end_time = None
        self._events = deque(maxlen=TRANSCRIPT_RING)
        self.tool_count = 0
        self.last_event = ''
        self._question = None                          # {'id', 'text' | 'questions', 'event', 'answer', 'asked_at', 'ticket'}
        self._question_lock = threading.Lock()
        self._reported = False                         # report() ran: the chat heard from this agent

    # --- status ---------------------------------------------------------------

    @property
    def status(self):
        return self._status

    @status.setter
    def status(self, value):
        if value not in STATUSES:
            raise ValueError(f"unknown agent status {value!r}")
        with self._status_lock:
            # a stopped/failed/lost agent never comes back as running or done
            if self._status in ('failed', 'stopped', 'lost') and value not in ('failed', 'stopped', 'lost'):
                return
            self._status = value

    @property
    def cancelled(self):
        return self._cancelled.is_set()

    @property
    def elapsed(self):
        if self._start_time is None:
            return 0
        end = self._end_time or time.time()
        return round(end - self._start_time, 1)

    # --- the thread -----------------------------------------------------------

    def start(self):
        self.status = 'running'
        self._start_time = time.time()
        # Carry the spawning turn's ContextVars (event data, reply routing):
        # a bare thread saw an EMPTY event and the Discord reach rule read
        # that as "operator chat, full reach" (row 53).
        ctx = contextvars.copy_context()
        self._thread = threading.Thread(target=ctx.run, args=(self._run_wrapper,),
                                        daemon=True, name=f'agent-{self.name}')
        self._thread.start()

    def _run_wrapper(self):
        # The chat's stream-brain override, from the ROW (never from whoever
        # woke us): hooks fired on this thread resolve privacy and settings
        # through it (vault hunt H2; scout C M5, 2026-10-06).
        token = self._engine.install_carrier(self)
        try:
            self.run(self.mission)
            if self._cancelled.is_set():
                self.status = 'stopped'
                self.result = None             # the director said "I don't want this": honor that
            elif self.status in ('running', 'waiting'):
                self.status = 'done'
        except Exception as e:
            logger.error(f"Agent {self.name} ({self.kind}) failed: {e}", exc_info=True)
            self.status = 'failed'
            self.error = str(e)
        finally:
            self._end_time = time.time()
            self._engine.release_carrier(token)
            # Guarded: an exception here would kill the thread silently and the
            # engine would never learn the agent finished.
            try:
                self._engine.finished(self)
            except Exception as e:
                logger.error(f"Agent {self.name}: engine.finished raised: {e}", exc_info=True)

    # --- what a kind writes ---------------------------------------------------

    def run(self, mission):
        """The work. Set self.result via report(), or raise. Override."""
        raise NotImplementedError

    def say(self, text):
        """A follow-up turn (conversational kinds). Override; the base refuses."""
        return f"{self.name} ({self.kind}) does not take follow-up turns.", False

    def stop(self):
        """Interrupt and end. A kind that holds a process overrides and calls super()."""
        self._cancelled.set()
        self._end_question(None)               # a blocked ask() wakes with no answer
        if self.status not in TERMINAL:
            self.status = 'stopped'
            self._end_time = time.time()
        self.result = None

    cancel = stop                              # the old name, kept for the shutdown path

    # --- the three doors ------------------------------------------------------

    def ask(self, question, timeout=ASK_TIMEOUT_S):
        """Ask the director and wait. `question` is {'text': str} or the
        AskUserQuestion shape {'questions': [{question, header, options:
        [{label, description}], multiSelect}]}. Returns the answer - a str for
        a text question, {question_text: label | [labels] | str} for a
        questions list - or None when nobody answered in time."""
        if self._cancelled.is_set():
            return None
        q = dict(question or {})
        q['id'] = uuid.uuid4().hex[:8]
        q['event'] = threading.Event()
        q['answer'] = None
        q['asked_at'] = time.time()
        with self._question_lock:
            self._question = q
        self.status = 'waiting'
        self.event('ask', _question_summary(q))
        try:
            q['ticket'] = self._engine.question_out(self, q)
        except Exception as e:
            # the chat can't take it (sealed, gone): nobody will ever be asked -
            # don't hold the agent for the whole timeout (chaos scout, 2026-10-07)
            logger.warning(f"Agent {self.name}: could not deliver its question: {e}")
            with self._question_lock:
                if self._question is q:
                    self._question = None
            if self.status == 'waiting':
                self.status = 'running'
            self.event('note', 'the question could not reach the chat; going on with the default')
            return None
        got = q['event'].wait(timeout)
        with self._question_lock:
            answer = q['answer'] if got else None
            if self._question is q:
                self._question = None
        if self.status == 'waiting':
            self.status = 'running'
        if not got:
            self.event('note', 'no answer from the director in time')
            self._withdraw(q, 'no answer in time')
        return answer

    def _withdraw(self, q, why):
        """The queued 'Agent X asks' turn is moot once the question is settled."""
        ticket = (q or {}).get('ticket')
        if ticket:
            try:
                self._engine.question_withdrawn(self, ticket, why)
            except Exception as e:
                logger.debug(f"Agent {self.name}: withdraw failed: {e}")

    def answer(self, value):
        """The director's answer (agent_action answer). (text, ok)."""
        with self._question_lock:
            q = self._question
            if q is None:
                return f"{self.name} has no question pending.", False
            if q.get('questions'):
                q['answer'] = _map_answers(q['questions'], value)
            else:
                q['answer'] = value if isinstance(value, str) else str(value)
            self._question = None
            q['event'].set()
        self.event('answer', value if isinstance(value, str) else str(value))
        self._withdraw(q, 'answered')
        return f"Answered {self.name}.", True

    def _end_question(self, answer):
        with self._question_lock:
            q = self._question
            if q is not None:
                q['answer'] = answer
                self._question = None
                q['event'].set()

    @property
    def pending_question(self):
        """The question waiting on the director, as the UI and agent_peek
        show it ({id, text} or {id, questions}), or None."""
        with self._question_lock:
            q = self._question
        return _question_summary(q) if q else None

    def event(self, kind, data=None):
        """A transcript line. kinds: text · tool_use · tool_result · note · ask · answer."""
        if kind == 'tool_use':
            self.tool_count += 1
            name = (data or {}).get('name') if isinstance(data, dict) else str(data)
            self.last_event = f"{name} {_head((data or {}).get('input'))}".strip() if isinstance(data, dict) else str(data)
        elif kind in ('text', 'note'):
            self.last_event = _head(data)
        self._events.append({'t': time.time(), 'kind': kind, 'data': data})
        try:
            self._engine.event_out(self, kind)
        except Exception as e:
            logger.debug(f"Agent {self.name}: event_out failed: {e}")

    def remember(self, **fields):
        """Metadata a kind needs back on its next wake (a workspace name, a
        mode) - onto the ROW, which is metadata-only by contract: never a
        mission, a question or a report (those go through the engine's
        content store under the chat)."""
        try:
            self._engine._row_update(self.id, **fields)
        except Exception as e:
            logger.debug(f"Agent {self.name}: remember({list(fields)}) failed: {e}")

    def report(self, text):
        """The turn's result. Delivered into the chat by the engine."""
        self.result = text if text is None else str(text)
        self._reported = True
        try:
            self._engine.report_out(self, self.result)
        except Exception as e:
            logger.warning(f"Agent {self.name}: report not delivered: {e}")

    # --- reading --------------------------------------------------------------

    def transcript(self, last=40):
        items = list(self._events)
        return items[-last:] if last else items

    def progress(self):
        """One line: `running 4m · 12 tool calls · last: Edit core/x.py`."""
        bits = [f"{self.status} {_fmt_secs(self.elapsed)}"]
        if self.tool_count:
            bits.append(f"{self.tool_count} tool call{'s' if self.tool_count != 1 else ''}")
        if self.last_event:
            bits.append(f"last: {self.last_event}")
        return ' · '.join(bits)

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'kind': self.kind,
            'status': self.status,
            'mission': self.mission,
            'elapsed': self.elapsed,
            'has_result': self.result is not None,
            'error': self.error,
            'warning': self.warning,
            'tool_count': self.tool_count,
            'last_event': self.last_event,
            'pending_question': self.pending_question,
            'chat_name': self.chat,
            'privacy': self.privacy,
        }


# --- helpers ---------------------------------------------------------------------

def _head(x, n=80):
    if x is None:
        return ''
    if isinstance(x, dict):
        for k in ('file_path', 'command', 'path', 'pattern', 'query', 'url', 'text'):
            if x.get(k):
                return _head(str(x[k]), n)
        return ''
    s = ' '.join(str(x).split())
    return s if len(s) <= n else s[:n - 1] + '…'


def _fmt_secs(s):
    s = int(s or 0)
    return f"{s}s" if s < 60 else f"{s // 60}m{s % 60:02d}s"


def _question_summary(q):
    if not q:
        return None
    if q.get('questions'):
        return {'id': q['id'], 'questions': [
            {'question': str(x.get('question', '')), 'header': str(x.get('header', '')),
             'options': [{'label': str(o.get('label', '')), 'description': str(o.get('description', ''))}
                         for o in (x.get('options') or []) if isinstance(o, dict)],
             'multiSelect': bool(x.get('multiSelect'))}
            for x in q['questions'] if isinstance(x, dict)]}
    return {'id': q['id'], 'text': str(q.get('text') or '')}


def _map_answers(questions, value):
    """The director's answer to a questions list, as {question_text: answer}.
    A dict is taken as given (missing questions get ''). A str answers every
    question - a letter (a/b/c) or an option label picks that option, anything
    else is free text. A list answers the questions in order."""
    out = {}
    if isinstance(value, str) and len(questions) > 1:
        parsed = _parse_per_question(questions, value)
        if parsed is not None:
            value = parsed
    if isinstance(value, dict):
        for x in questions:
            qt = str(x.get('question', ''))
            v = value.get(qt, value.get('*', ''))
            out[qt] = _pick(x, v)
        return out
    if isinstance(value, list):
        for x, v in zip(questions, value):
            out[str(x.get('question', ''))] = _pick(x, v)
        for x in questions[len(value):]:
            out[str(x.get('question', ''))] = ''
        return out
    for x in questions:
        out[str(x.get('question', ''))] = _pick(x, value)
    return out


def _parse_per_question(questions, text):
    """A string that answers SEVERAL questions: the question card's own
    `Question? → Answer` lines (what the director relays from the user's
    card), or a JSON object {question: answer}. None when it is neither -
    then the one string answers every question, as before."""
    t = (text or '').strip()
    if t.startswith('{'):
        try:
            d = json.loads(t)
            return d if isinstance(d, dict) else None
        except ValueError:
            return None
    if '\u2192' not in t:
        return None
    marks = {str(q.get('question', '')): t.find(str(q.get('question', '')) + ' \u2192 ') for q in questions}
    found = {q: i for q, i in marks.items() if i >= 0}
    if not found:
        return None
    out = {}
    for qt, i in found.items():
        start = i + len(qt) + 3
        nxt = [j for j in found.values() if j > i]
        out[qt] = t[start:min(nxt) if nxt else len(t)].strip()
    return out


def _pick(question, value):
    """Resolve one answer against the question's options: a letter or a label
    (case-insensitive) becomes the option's label; several (comma / 'and')
    become a list when multiSelect; otherwise the text stands as said."""
    if isinstance(value, list):
        return [_pick(question, v) for v in value]
    v = ' '.join(str(value if value is not None else '').split())
    opts = [str(o.get('label', '')) for o in (question.get('options') or []) if isinstance(o, dict)]
    if not v or not opts:
        return v
    if question.get('multiSelect') and (',' in v or ' and ' in v.lower()):
        parts = [p.strip() for p in v.replace(' and ', ',').split(',') if p.strip()]
        picked = [_pick(dict(question, multiSelect=False), p) for p in parts]
        return picked
    low = v.lower().rstrip('.)')
    for i, label in enumerate(opts):
        letter = chr(ord('a') + i)
        if low in (letter, f"option {letter}", label.lower()):
            return label
    return v
