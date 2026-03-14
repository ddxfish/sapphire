# core/scouts/worker.py — Individual scout worker thread
import logging
import threading
import time

from core.event_bus import publish, Events

logger = logging.getLogger(__name__)

# NATO phonetic names for scout slots
SCOUT_NAMES = ['Alpha', 'Bravo', 'Charlie', 'Delta', 'Echo']


class ScoutWorker:
    """Runs an isolated LLM + tool loop in a background thread."""

    def __init__(self, scout_id, name, mission, task_settings, function_manager, tool_engine, chat_name=''):
        self.id = scout_id
        self.name = name
        self.mission = mission
        self.chat_name = chat_name
        self.status = 'pending'  # pending | running | done | failed | cancelled
        self.result = None
        self.error = None
        self.tool_log = []  # Names of tools the scout called
        self._start_time = None
        self._end_time = None
        self._cancelled = threading.Event()
        self._thread = None
        self._task_settings = task_settings
        self._fm = function_manager
        self._tool_engine = tool_engine

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
            target=self._run, daemon=True, name=f'scout-{self.name}'
        )
        self._thread.start()

    def cancel(self):
        self._cancelled.set()
        if self.status == 'running':
            self.status = 'cancelled'
            self._end_time = time.time()

    def _run(self):
        from core.continuity.execution_context import ExecutionContext

        try:
            ctx = ExecutionContext(self._fm, self._tool_engine, self._task_settings)
            # Run ephemeral (no history)
            self.result = ctx.run(self.mission)
            self.tool_log = ctx.tool_log
            if self._cancelled.is_set():
                self.status = 'cancelled'
            else:
                self.status = 'done'
        except Exception as e:
            logger.error(f"Scout {self.name} failed: {e}", exc_info=True)
            self.status = 'failed'
            self.error = str(e)
        finally:
            self._end_time = time.time()
            publish(Events.SCOUT_COMPLETED, {
                'id': self.id,
                'name': self.name,
                'status': self.status,
                'elapsed': self.elapsed,
            })
            logger.info(f"Scout {self.name} finished: {self.status} ({self.elapsed}s)")

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
