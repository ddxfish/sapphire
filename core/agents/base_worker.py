# core/agents/base_worker.py — Abstract base for all agent workers
import threading
import time


class BaseWorker:
    """Base class for agent workers. Plugins subclass this to define agent types."""

    def __init__(self, agent_id, name, mission, chat_name='', on_complete=None):
        self.id = agent_id
        self.name = name
        self.mission = mission
        self.chat_name = chat_name
        self._status = 'pending'  # pending | running | done | failed | cancelled
        self._status_lock = threading.Lock()
        self.result = None
        self.error = None
        self.tool_log = []
        # Non-fatal degradation signal. Subclasses set this when the run
        # technically completed but the output is a placeholder — tool-loop
        # exhaustion, context overflow, empty LLM. Surfaces to the frontend
        # via AGENT_COMPLETED so the pill can render amber-with-tooltip
        # instead of green-success. None = clean run. Scout #15 — 2026-04-20.
        self.warning = None
        self._start_time = None
        self._end_time = None
        self._cancelled = threading.Event()
        self._thread = None
        self._on_complete = on_complete

    @property
    def status(self):
        return self._status

    @status.setter
    def status(self, value):
        with self._status_lock:
            # Once terminal, don't allow regression (cancelled can't become done)
            if self._status in ('cancelled', 'failed') and value == 'done':
                return
            self._status = value

    @property
    def elapsed(self):
        if self._start_time is None:
            return 0
        end = self._end_time or time.time()
        return round(end - self._start_time, 1)

    def start(self):
        self.status = 'running'
        self._start_time = time.time()
        self._thread = threading.Thread(
            target=self._run_wrapper, daemon=True, name=f'agent-{self.name}'
        )
        self._thread.start()

    def cancel(self):
        self._cancelled.set()
        if self.status == 'running':
            self.status = 'cancelled'
            self._end_time = time.time()
        # Clear result so recall() doesn't return partial output for a
        # dismissed agent. User said "I don't want this" — honor that.
        self.result = None

    def _run_wrapper(self):
        from core.event_bus import publish, Events
        try:
            self.run()
            if self._cancelled.is_set():
                self.status = 'cancelled'
                # Cancellation voids the result — run() may have finished
                # between cancel() firing and its check, leaving stale output
                self.result = None
            elif self.status == 'running':
                self.status = 'done'
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"Agent {self.name} failed: {e}", exc_info=True)
            self.status = 'failed'
            self.error = str(e)
        finally:
            self._end_time = time.time()
            # Guard every step of post-run cleanup — any exception here kills
            # the thread silently and prevents _on_complete (batch fire) from
            # ever running, which hides the completion from everything
            # downstream. This was a real bug: `getattr(self, 'tool_log',
            # ['unknown'])[0]` blew up with IndexError because tool_log is
            # initialized to [], making `getattr` return [] (the attribute
            # exists) and `[0]` then throwing.
            try:
                publish(Events.AGENT_COMPLETED, {
                    'id': self.id,
                    'name': self.name,
                    'status': self.status,
                    'elapsed': self.elapsed,
                    'result': self.result,
                    'error': self.error,
                    'warning': self.warning,
                    'agent_type': getattr(self, '_agent_type', 'unknown'),
                })
            except Exception as e:
                import logging
                logging.getLogger(__name__).error(
                    f"Agent {self.name}: publish AGENT_COMPLETED raised: {e}",
                    exc_info=True,
                )
            if self._on_complete:
                try:
                    self._on_complete(self.id, self.chat_name)
                except Exception as e:
                    import logging
                    logging.getLogger(__name__).error(
                        f"Agent {self.name}: on_complete raised: {e}",
                        exc_info=True,
                    )

    def run(self):
        """Override this in subclasses. Set self.result on success, self.error on failure."""
        raise NotImplementedError

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'status': self.status,
            'mission': self.mission,
            'elapsed': self.elapsed,
            'has_result': self.result is not None,
            'error': self.error,
            'warning': self.warning,
            'tool_log': self.tool_log,
            'chat_name': self.chat_name,
        }
