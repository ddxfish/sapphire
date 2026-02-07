# core/modules/continuity/scheduler.py
"""
Continuity Scheduler - Background thread that checks cron schedules and fires tasks.
"""

import os
import json
import uuid
import random
import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Any, Optional

logger = logging.getLogger(__name__)

# Lazy import croniter to avoid startup crash if not installed
croniter = None

def _get_croniter():
    global croniter
    if croniter is None:
        try:
            from croniter import croniter as _croniter
            croniter = _croniter
        except ImportError:
            logger.error("croniter not installed. Run: pip install croniter")
            raise ImportError("croniter required for Continuity. Install with: pip install croniter")
    return croniter


class ContinuityScheduler:
    """
    Background scheduler for continuity tasks.
    Checks every 30 seconds, matches cron expressions, respects cooldowns.
    """
    
    CHECK_INTERVAL = 30  # seconds between schedule checks
    
    def __init__(self, system, executor):
        """
        Args:
            system: VoiceChatSystem instance
            executor: ContinuityExecutor instance
        """
        self.system = system
        self.executor = executor
        
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        
        # Paths
        self._base_dir = Path(__file__).parent.parent.parent.parent / "user" / "continuity"
        self._tasks_path = self._base_dir / "tasks.json"
        self._activity_path = self._base_dir / "activity.json"
        
        # In-memory caches
        self._tasks: Dict[str, Dict] = {}
        self._activity: List[Dict] = []
        
        self._ensure_dirs()
        self._load_tasks()
        self._load_activity()
    
    def _ensure_dirs(self):
        """Create user/continuity directory if missing."""
        self._base_dir.mkdir(parents=True, exist_ok=True)
    
    # =========================================================================
    # TASK PERSISTENCE
    # =========================================================================
    
    def _load_tasks(self):
        """Load tasks from JSON file."""
        if not self._tasks_path.exists():
            self._tasks = {}
            return
        
        try:
            with open(self._tasks_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            self._tasks = {t["id"]: t for t in data.get("tasks", [])}
            logger.info(f"[Continuity] Loaded {len(self._tasks)} tasks")
        except Exception as e:
            logger.error(f"[Continuity] Failed to load tasks: {e}")
            self._tasks = {}
    
    def _save_tasks(self):
        """Save tasks to JSON file."""
        try:
            data = {"tasks": list(self._tasks.values())}
            with open(self._tasks_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"[Continuity] Failed to save tasks: {e}")
    
    def _load_activity(self):
        """Load activity log from JSON file."""
        if not self._activity_path.exists():
            self._activity = []
            return
        
        try:
            with open(self._activity_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            self._activity = data.get("activity", [])[-50:]  # Keep last 50
        except Exception as e:
            logger.error(f"[Continuity] Failed to load activity: {e}")
            self._activity = []
    
    def _save_activity(self):
        """Save activity log to JSON file."""
        try:
            data = {"activity": self._activity[-50:]}  # Keep last 50
            with open(self._activity_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"[Continuity] Failed to save activity: {e}")
    
    def _log_activity(self, task_id: str, task_name: str, status: str, details: Optional[Dict] = None):
        """Add entry to activity log."""
        entry = {
            "timestamp": datetime.now().isoformat(),
            "task_id": task_id,
            "task_name": task_name,
            "status": status,
            "details": details or {}
        }
        self._activity.append(entry)
        self._activity = self._activity[-50:]  # Trim
        self._save_activity()
        
        # Publish event
        from core.event_bus import publish, Events
        event_map = {
            "started": Events.CONTINUITY_TASK_STARTING,
            "complete": Events.CONTINUITY_TASK_COMPLETE,
            "skipped": Events.CONTINUITY_TASK_SKIPPED,
            "error": Events.CONTINUITY_TASK_ERROR,
        }
        event_type = event_map.get(status, Events.CONTINUITY_TASK_COMPLETE)
        publish(event_type, {"task_id": task_id, "task_name": task_name, **entry})
    
    # =========================================================================
    # TASK CRUD
    # =========================================================================
    
    def list_tasks(self) -> List[Dict]:
        """Get all tasks."""
        with self._lock:
            return list(self._tasks.values())
    
    def get_task(self, task_id: str) -> Optional[Dict]:
        """Get single task by ID."""
        with self._lock:
            return self._tasks.get(task_id)
    
    def create_task(self, data: Dict) -> Dict:
        """Create new task, returns the created task."""
        task = {
            "id": str(uuid.uuid4()),
            "name": data.get("name", "Unnamed Task"),
            "enabled": data.get("enabled", True),
            "schedule": data.get("schedule", "0 9 * * *"),
            "chance": data.get("chance", 100),
            "iterations": data.get("iterations", 1),
            "provider": data.get("provider", "auto"),
            "model": data.get("model", ""),
            "prompt": data.get("prompt", "default"),
            "toolset": data.get("toolset", "none"),
            "chat_target": data.get("chat_target", ""),  # blank = ephemeral (no chat, no UI)
            "initial_message": data.get("initial_message", "Hello."),
            "tts_enabled": data.get("tts_enabled", True),
            "inject_datetime": data.get("inject_datetime", False),
            "memory_scope": data.get("memory_scope", "default"),
            "cooldown_minutes": data.get("cooldown_minutes", 1),
            "last_run": None,
            "created": datetime.now().isoformat()
        }
        
        # Validate cron
        try:
            _get_croniter()(task["schedule"], datetime.now())
        except Exception as e:
            raise ValueError(f"Invalid cron schedule: {e}")
        
        with self._lock:
            self._tasks[task["id"]] = task
            self._save_tasks()
        
        logger.info(f"[Continuity] Created task: {task['name']} ({task['id']})")
        return task
    
    def update_task(self, task_id: str, data: Dict) -> Optional[Dict]:
        """Update existing task."""
        with self._lock:
            if task_id not in self._tasks:
                return None
            
            task = self._tasks[task_id]
            
            # Validate cron if provided
            if "schedule" in data:
                try:
                    _get_croniter()(data["schedule"], datetime.now())
                except Exception as e:
                    raise ValueError(f"Invalid cron schedule: {e}")
            
            # Update allowed fields
            allowed = {
                "name", "enabled", "schedule", "chance", "iterations",
                "provider", "model", "prompt", "toolset", "chat_target",
                "initial_message", "tts_enabled", "inject_datetime", "memory_scope", "cooldown_minutes"
            }
            for key in allowed:
                if key in data:
                    task[key] = data[key]
            
            self._save_tasks()
            logger.info(f"[Continuity] Updated task: {task['name']} ({task_id})")
            return task
    
    def delete_task(self, task_id: str) -> bool:
        """Delete task by ID."""
        with self._lock:
            if task_id not in self._tasks:
                return False
            
            name = self._tasks[task_id].get("name", task_id)
            del self._tasks[task_id]
            self._save_tasks()
            logger.info(f"[Continuity] Deleted task: {name} ({task_id})")
            return True
    
    # =========================================================================
    # SCHEDULE CHECKING
    # =========================================================================
    
    def _cron_matches_now(self, cron_expr: str, last_check: datetime) -> bool:
        """
        Check if cron expression matches current minute.
        Uses croniter to get next scheduled time and compares.
        """
        try:
            cron = _get_croniter()(cron_expr, last_check - timedelta(minutes=1))
            next_time = cron.get_next(datetime)
            now = datetime.now()
            
            matched = (next_time.year == now.year and
                       next_time.month == now.month and
                       next_time.day == now.day and
                       next_time.hour == now.hour and
                       next_time.minute == now.minute)
            
            if not matched:
                logger.debug(f"[Continuity] Cron '{cron_expr}': next={next_time.strftime('%H:%M')}, now={now.strftime('%H:%M')}")
            
            return matched
        except Exception as e:
            logger.error(f"[Continuity] Cron check failed for '{cron_expr}': {e}")
            return False
    
    def _cooldown_passed(self, task: Dict) -> bool:
        """Check if enough time has passed since last run. 0 = no cooldown."""
        cooldown = task.get("cooldown_minutes", 60)
        if cooldown == 0:
            return True  # 0 means no cooldown
        
        last_run = task.get("last_run")
        if not last_run:
            return True
        
        try:
            last_dt = datetime.fromisoformat(last_run)
            elapsed = (datetime.now() - last_dt).total_seconds() / 60
            return elapsed >= cooldown
        except Exception:
            return True
    
    def _check_and_run(self):
        """Single check cycle - evaluate all tasks, run eligible ones."""
        now = datetime.now()
        
        with self._lock:
            tasks_snapshot = list(self._tasks.values())
        
        if not tasks_snapshot:
            return
        
        logger.debug(f"[Continuity] Checking {len(tasks_snapshot)} tasks at {now.strftime('%H:%M:%S')}")
        
        for task in tasks_snapshot:
            if not task.get("enabled", True):
                continue
            
            task_id = task["id"]
            task_name = task.get("name", "Unnamed")
            schedule = task.get("schedule", "")
            
            # Check cron match
            cron_matched = self._cron_matches_now(schedule, now - timedelta(seconds=self.CHECK_INTERVAL))
            if not cron_matched:
                logger.debug(f"[Continuity] '{task_name}' schedule '{schedule}' - no match at {now.strftime('%H:%M')}")
                continue
            
            logger.info(f"[Continuity] '{task_name}' schedule '{schedule}' - MATCHED at {now.strftime('%H:%M')}")
            
            # Check cooldown
            if not self._cooldown_passed(task):
                cooldown = task.get("cooldown_minutes", 60)
                last_run = task.get("last_run", "never")
                logger.info(f"[Continuity] Task '{task_name}' skipped - in cooldown ({cooldown}min, last run: {last_run})")
                continue
            
            # Probability gate
            chance = task.get("chance", 100)
            if chance < 100:
                roll = random.randint(1, 100)
                if roll > chance:
                    self._log_activity(task_id, task_name, "skipped", {"reason": "chance", "roll": roll, "threshold": chance})
                    logger.info(f"[Continuity] Task '{task_name}' skipped (roll {roll} > {chance}%)")
                    continue
            
            # Execute task
            logger.info(f"[Continuity] Triggering task: {task_name}")
            self._log_activity(task_id, task_name, "started")
            
            try:
                result = self.executor.run(task)
                
                # Update last_run
                with self._lock:
                    if task_id in self._tasks:
                        self._tasks[task_id]["last_run"] = datetime.now().isoformat()
                        self._save_tasks()
                
                status = "complete" if result.get("success") else "error"
                self._log_activity(task_id, task_name, status, {
                    "responses": len(result.get("responses", [])),
                    "errors": result.get("errors", [])
                })
                
            except Exception as e:
                logger.error(f"[Continuity] Task '{task_name}' execution failed: {e}", exc_info=True)
                self._log_activity(task_id, task_name, "error", {"exception": str(e)})
    
    # =========================================================================
    # MANUAL RUN
    # =========================================================================
    
    def run_task_now(self, task_id: str) -> Dict[str, Any]:
        """Manually trigger a task immediately (for testing)."""
        with self._lock:
            task = self._tasks.get(task_id)
        
        if not task:
            return {"success": False, "error": "Task not found"}
        
        task_name = task.get("name", "Unnamed")
        logger.info(f"[Continuity] Manual run: {task_name}")
        self._log_activity(task_id, task_name, "started", {"manual": True})
        
        try:
            result = self.executor.run(task)
            
            # Update last_run
            with self._lock:
                if task_id in self._tasks:
                    self._tasks[task_id]["last_run"] = datetime.now().isoformat()
                    self._save_tasks()
            
            status = "complete" if result.get("success") else "error"
            self._log_activity(task_id, task_name, status, {
                "manual": True,
                "responses": len(result.get("responses", [])),
                "errors": result.get("errors", [])
            })
            
            return result
            
        except Exception as e:
            logger.error(f"[Continuity] Manual run failed: {e}", exc_info=True)
            self._log_activity(task_id, task_name, "error", {"manual": True, "exception": str(e)})
            return {"success": False, "error": str(e)}
    
    # =========================================================================
    # THREAD CONTROL
    # =========================================================================
    
    def start(self):
        """Start the scheduler background thread."""
        if self._running:
            logger.warning("[Continuity] Scheduler already running")
            return
        
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="ContinuityScheduler")
        self._thread.start()
        logger.info("[Continuity] Scheduler started")
    
    def stop(self):
        """Stop the scheduler."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
        logger.info("[Continuity] Scheduler stopped")
    
    def _run_loop(self):
        """Main scheduler loop."""
        import time
        
        logger.info("[Continuity] Scheduler loop running")
        check_count = 0
        
        while self._running:
            try:
                self._check_and_run()
                check_count += 1
                
                # Heartbeat every 120 checks (~1 hour)
                if check_count % 120 == 0:
                    with self._lock:
                        enabled = sum(1 for t in self._tasks.values() if t.get("enabled"))
                    logger.info(f"[Continuity] Heartbeat: {enabled} enabled tasks, {check_count} checks since start")
                    
            except Exception as e:
                logger.error(f"[Continuity] Scheduler loop error: {e}", exc_info=True)
            
            # Sleep in small increments for responsive shutdown
            for _ in range(self.CHECK_INTERVAL):
                if not self._running:
                    break
                time.sleep(1)
    
    def is_running(self) -> bool:
        """Check if scheduler is running."""
        return self._running
    
    # =========================================================================
    # STATUS / TIMELINE
    # =========================================================================
    
    def get_status(self) -> Dict[str, Any]:
        """Get scheduler status."""
        with self._lock:
            enabled_count = sum(1 for t in self._tasks.values() if t.get("enabled"))
            
        next_task = self._get_next_scheduled()
        
        return {
            "running": self._running,
            "total_tasks": len(self._tasks),
            "enabled_tasks": enabled_count,
            "next_task": next_task
        }
    
    def _get_next_scheduled(self) -> Optional[Dict]:
        """Get the next task that will run."""
        now = datetime.now()
        next_task = None
        next_time = None
        
        with self._lock:
            for task in self._tasks.values():
                if not task.get("enabled"):
                    continue
                
                try:
                    cron = _get_croniter()(task.get("schedule", ""), now)
                    task_next = cron.get_next(datetime)
                    
                    if next_time is None or task_next < next_time:
                        next_time = task_next
                        next_task = {
                            "id": task["id"],
                            "name": task.get("name"),
                            "scheduled_for": task_next.isoformat()
                        }
                except Exception:
                    continue
        
        return next_task
    
    def get_activity(self, limit: int = 50) -> List[Dict]:
        """Get recent activity log."""
        return self._activity[-limit:]
    
    def get_timeline(self, hours: int = 24) -> List[Dict]:
        """Get timeline of scheduled tasks for next N hours."""
        now = datetime.now()
        end = now + timedelta(hours=hours)
        timeline = []
        
        with self._lock:
            for task in self._tasks.values():
                if not task.get("enabled"):
                    continue
                
                try:
                    cron = _get_croniter()(task.get("schedule", ""), now)
                    
                    # Get next occurrences within window
                    for _ in range(10):  # Max 10 per task
                        next_time = cron.get_next(datetime)
                        if next_time > end:
                            break
                        
                        timeline.append({
                            "task_id": task["id"],
                            "task_name": task.get("name"),
                            "scheduled_for": next_time.isoformat(),
                            "chance": task.get("chance", 100)
                        })
                except Exception:
                    continue
        
        # Sort by time
        timeline.sort(key=lambda x: x["scheduled_for"])
        return timeline