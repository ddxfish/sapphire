"""core/chat/turn.py — the server-owned turn (SS1 stream lifecycle, 2026-09-15).

ONE engine run (StreamingChat.chat_stream) driven by ONE Sapphire thread from
the moment a door accepts it. Everything else is a VIEWER: it attaches, reads
events, detaches. A viewer dying never touches the engine. The generator is
closed exactly once, on the runner thread, when the turn ends — its finally
(tool-cycle close, cleanup, end_streaming, AI_TYPING_END) runs there, never on
the event loop and never at GC time.

Why: Krem's phone. Brave drops the SSE socket on screen lock; Starlette
dropped the route generator, CPython closed the engine, and the engine's
finally wrote "[Cancelled by user]" / "[Cancelled during tool execution]"
with nobody pressing Stop. The web door was the only consumer whose turn
lifetime was its socket — voice, cadence and the wake door already run the
engine in their own thread. Record: tmp/server-owned-turns-plan.md.

Ring: text/tool events carry a seq so a viewer that lost its feed reattaches
`since` its last seq, exact-once. Adjacent content chunks coalesce into runs
(a reply is thousands of tiny chunks) with per-chunk offsets so a resume can
land inside a run. Audio (tts_*) is live-only: forwarded to audio viewers,
never buffered, never numbered. The ring dies with the turn.
"""
import contextvars
import logging
import queue
import threading
import time
from array import array
from collections import deque

logger = logging.getLogger(__name__)

RING_CHARS = 256_000      # text held for reattach; past it the head drops → resync
RUN_CHARS = 4096          # a content run closes past this so eviction has granularity
VIEWER_QUEUE = 4000       # a viewer this far behind is dropped; the turn never waits
_RING_TYPES = {'stream_started', 'iteration_start', 'content', 'tool_pending',
               'tool_start', 'tool_end', 'notice', 'reload', 'llm_done', 'turn_end'}
_AUDIO_TYPES = {'tts_stream_start', 'tts_chunk', 'tts_stream_end'}
_END = object()


class Viewer:
    """One reader of a turn. Iterate it: it ends when the turn ends, or when
    detach() is called, or when it fell too far behind (`dropped`)."""

    def __init__(self, turn, audio):
        self.turn = turn
        self.audio = audio
        self.dropped = False
        self._q = queue.Queue(maxsize=VIEWER_QUEUE)
        self._closed = False

    def _push(self, ev):
        if self._closed:
            return
        try:
            self._q.put_nowait(ev)
        except queue.Full:
            self.dropped = True
            self._closed = True
            logger.warning(f"[TURN] {self.turn.label}: a viewer fell {VIEWER_QUEUE} events behind — dropped")

    def _end(self):
        self._closed = True
        try:
            self._q.put_nowait(_END)
        except queue.Full:
            pass                      # a dropped viewer wakes on the poll below

    def detach(self):
        self.turn.detach(self)

    def __iter__(self):
        while True:
            try:
                ev = self._q.get(timeout=0.5)
            except queue.Empty:
                if self._closed:
                    return
                continue
            if ev is _END:
                return
            yield ev


class Turn:
    def __init__(self, stream, gen, on_end=None, label='turn'):
        self.stream = stream
        self._gen = gen
        self._on_end = on_end
        self.label = label
        self.seq = 0
        self._ring = deque()          # ['run', first, last, parts, ends] | ['ev', seq, event, cost]
        self._ring_chars = 0
        self.ring_truncated = False
        self._viewers = []
        self._lock = threading.RLock()
        self.done = threading.Event()
        self.terminal = None
        self.started_at = time.time()
        self.thread = None
        # ONE Context for the whole turn: the A1 stream-brain override and
        # session_origin live in ContextVars; set and reset must see the same
        # Context. Copied HERE (the accepting door's context), run on the thread.
        self._ctx = contextvars.copy_context()
        try:
            stream.turn = self
        except Exception:
            pass

    # ── run ─────────────────────────────────────────────────────────────
    def start(self):
        self.thread = threading.Thread(target=self._ctx.run, args=(self._run,),
                                       daemon=True, name=f"turn-{self.label}")
        self.thread.start()
        return self

    def _run(self):
        stream = self.stream
        terminal = None
        n = 0
        try:
            for ev in self._gen:
                if isinstance(ev, str):        # legacy module replies
                    ev = {'type': 'reload'} if '<<RELOAD_PAGE>>' in ev else {'type': 'content', 'text': ev}
                elif not isinstance(ev, dict):
                    continue
                # A cancel in the gap before llm_done ends the turn here — the
                # rule the web route always applied (E1#7). Past llm_done the row
                # is written; what follows is the audio tail, forwarded as-is.
                if getattr(stream, 'cancel_flag', False) and not getattr(stream, 'llm_done', False):
                    terminal = {'type': 'turn_end', 'cancelled': True}
                    logger.info(f"[TURN] {self.label}: cancelled at event {n}")
                    break
                n += 1
                self._fanout(ev)
        except ConnectionError as e:
            logger.warning(f"[TURN] {self.label}: {e}")
            terminal = {'type': 'turn_end', 'error': self._friendly(e)}
        except Exception as e:
            logger.error(f"[TURN] {self.label} failed: {e}", exc_info=True)
            terminal = {'type': 'turn_end', 'error': self._friendly(e)}
        finally:
            # Close the engine exactly once, HERE, in the Context that ran it —
            # a cancel `break` leaves it suspended with its finally pending.
            try:
                self._gen.close()
            except Exception as e:
                logger.warning(f"[TURN] {self.label}: engine close failed: {e}")
            if terminal is None:
                if getattr(stream, 'cancel_flag', False) and not getattr(stream, 'llm_done', False):
                    terminal = {'type': 'turn_end', 'cancelled': True}
                else:
                    terminal = {'type': 'turn_end', 'done': True,
                                'ephemeral': bool(getattr(stream, 'ephemeral', False))}
            self.terminal = terminal
            # Registration goes BEFORE the terminal reaches any viewer: by the
            # time a body closes on `done`, the chat is free (the gate, the
            # counters) — the order the route always had.
            if self._on_end:
                try:
                    self._on_end()
                except Exception as e:
                    logger.error(f"[TURN] {self.label}: on_end failed: {e}")
            self._fanout(terminal)
            with self._lock:
                self.done.set()
                for v in self._viewers:
                    v._end()
                self._viewers = []
            logger.info(f"[TURN] {self.label} ended: {self._describe(terminal)}, {n} events, "
                        f"{self._ring_chars} ring chars{' (truncated)' if self.ring_truncated else ''}, "
                        f"{time.time() - self.started_at:.1f}s")

    @staticmethod
    def _describe(t):
        if t.get('error'):
            return 'error'
        return 'cancelled' if t.get('cancelled') else 'done'

    @staticmethod
    def _friendly(e):
        try:
            from core.chat.chat import friendly_llm_error
            return friendly_llm_error(e) or str(e)
        except Exception:
            return str(e)

    # ── fan-out + ring ──────────────────────────────────────────────────
    def _fanout(self, ev):
        with self._lock:
            t = ev.get('type')
            if t in _AUDIO_TYPES:
                ev['seq'] = self.seq            # unnumbered: audio is never replayed
                for v in self._viewers:
                    if v.audio:
                        v._push(ev)
            else:
                self.seq += 1
                ev['seq'] = self.seq
                if t in _RING_TYPES:
                    self._ring_add(ev)
                for v in self._viewers:
                    v._push(ev)
            dead = [v for v in self._viewers if v.dropped]
        for v in dead:
            self.detach(v)

    def _ring_add(self, ev):
        t = ev.get('type')
        seq = ev['seq']
        if t == 'content':
            text = ev.get('text') or ''
            last = self._ring[-1] if self._ring else None
            run_cap = max(1, min(RUN_CHARS, RING_CHARS // 4))
            if last is not None and last[0] == 'run' and last[2] == seq - 1 and last[4][-1] < run_cap:
                last[2] = seq
                last[3].append(text)
                last[4].append(last[4][-1] + len(text))
            else:
                self._ring.append(['run', seq, seq, [text], array('L', [len(text)])])
            self._ring_chars += len(text)
        else:
            cost = 64 + len(str(ev.get('result') or '')) + len(str(ev.get('text') or ''))
            self._ring.append(['ev', seq, ev, cost])
            self._ring_chars += cost
        while self._ring_chars > RING_CHARS and len(self._ring) > 1:
            old = self._ring.popleft()
            self._ring_chars -= old[4][-1] if old[0] == 'run' else old[3]
            self.ring_truncated = True

    def _replay(self, since):
        """Events a viewer that saw up to `since` still needs, exact-once.
        Returns (events, gap) — gap means the ring no longer reaches back."""
        out = []
        if not self._ring:
            return out, False
        oldest = self._ring[0][1]
        gap = self.ring_truncated and since < oldest - 1
        for entry in self._ring:
            if entry[0] == 'run':
                first, last = entry[1], entry[2]
                if last <= since:
                    continue
                text = ''.join(entry[3])
                if since >= first:
                    text = text[entry[4][since - first]:]
                out.append({'type': 'content', 'text': text, 'seq': last})
            elif entry[1] > since:
                out.append(entry[2])
        return out, gap

    # ── viewers ─────────────────────────────────────────────────────────
    def attach(self, since=None, audio=False):
        """A new reader. since=None: from now. since=N: replay everything
        after seq N (a `resync` first if the ring can't reach that far)."""
        v = Viewer(self, audio)
        with self._lock:
            if since is not None:
                events, gap = self._replay(int(since))
                if gap:
                    v._push({'type': 'resync', 'seq': self.seq})
                for ev in events:
                    v._push(ev)
            if self.done.is_set():
                if since is None and self.terminal is not None:
                    v._push(self.terminal)
                v._end()
            else:
                self._viewers.append(v)
        return v

    def detach(self, viewer):
        with self._lock:
            if viewer not in self._viewers:
                return
            self._viewers.remove(viewer)
            viewer._end()
            mute = (viewer.audio and not any(v.audio for v in self._viewers)
                    and not self.done.is_set() and not getattr(self.stream, 'llm_done', False))
        if mute:
            # Nobody can hear her: stop synthesizing, keep writing. The row
            # lands either way; a reattached tab speaks the finished reply once.
            try:
                self.stream.stop_tts()
                logger.info(f"[TURN] {self.label}: last listener left — voice muted, she keeps writing")
            except Exception:
                pass

    @property
    def viewers(self):
        with self._lock:
            return len(self._viewers)
