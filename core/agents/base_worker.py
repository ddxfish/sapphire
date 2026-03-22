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

    def _run_wrapper(self):
        from core.event_bus import publish, Events
        try:
            self.run()
            if self._cancelled.is_set():
                self.status = 'cancelled'
            elif self.status == 'running':
                self.status = 'done'
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"Agent {self.name} failed: {e}", exc_info=True)
            self.status = 'failed'
            self.error = str(e)
        finally:
            self._end_time = time.time()
            publish(Events.AGENT_COMPLETED, {
                'id': self.id,
                'name': self.name,
                'status': self.status,
                'elapsed': self.elapsed,
            })
            if self._on_complete:
                try:
                    self._on_complete(self.id, self.chat_name)
                except Exception:
                    pass

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
            'tool_log': self.tool_log,
            'chat_name': self.chat_name,
        }
