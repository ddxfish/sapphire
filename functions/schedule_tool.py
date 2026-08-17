"""Schedule tool — lets Sapphire create scheduled tasks for herself.

Simple time (5pm, 17:00, 1700) → one-shot task, auto-deletes after running.
Cron expression (0 17 * * *) → recurring task, runs indefinitely.
"""

import re
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

ENABLED = True
EMOJI = "\u23F0"

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "schedule_task",
            "description": (
                "Schedule a future task for yourself.\n"
                "  time='5pm' or '17:00' — one-shot (auto-deletes after firing)\n"
                "  time='0 17 * * *' — cron (recurring)\n"
                "description becomes the prompt you'll receive when it fires."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "description": {
                        "type": "string",
                        "description": "Prompt future-you will receive. Be specific."
                    },
                    "time": {
                        "type": "string",
                        "description": "One-shot ('5pm', '17:00', '1700', '2:30pm') or cron ('0 17 * * *', '30 9 * * 1-5')"
                    }
                },
                "required": ["description", "time"]
            }
        }
    }
]

# Max AI-scheduled tasks to prevent abuse
MAX_AI_TASKS = 10


def _parse_simple_time(time_str):
    """Parse simple time formats into (hour, minute).

    Accepts: 5pm, 5 pm, 5:30pm, 17:00, 1700, 2:30 PM
    Returns: (hour, minute) or None if not a simple time.
    """
    s = time_str.strip().lower()

    # 12-hour with am/pm: 5pm, 5:30pm, 5 pm, 2:30 PM
    m = re.match(r'^(\d{1,2})(?::(\d{2}))?\s*(am|pm)$', s)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2) or 0)
        if m.group(3) == 'pm' and hour != 12:
            hour += 12
        elif m.group(3) == 'am' and hour == 12:
            hour = 0
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return hour, minute
        return None

    # 24-hour with colon: 17:00, 9:30
    m = re.match(r'^(\d{1,2}):(\d{2})$', s)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2))
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return hour, minute
        return None

    # 24-hour no colon: 1700, 0930
    m = re.match(r'^(\d{4})$', s)
    if m:
        hour, minute = int(s[:2]), int(s[2:])
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return hour, minute
        return None

    return None


def _is_cron(time_str):
    """Check if the string looks like a cron expression (has spaces and 5 fields)."""
    parts = time_str.strip().split()
    return len(parts) == 5


def _get_user_now():
    """Get current time in user's timezone."""
    try:
        import config
        tz_name = getattr(config, 'USER_TIMEZONE', '')
        if tz_name:
            from zoneinfo import ZoneInfo
            return datetime.now(ZoneInfo(tz_name))
    except Exception:
        pass
    return datetime.now().astimezone()


def execute(function_name, arguments, config_obj=None):
    if function_name != "schedule_task":
        return f"Unknown function: {function_name}", False

    description = arguments.get("description", "").strip()
    time_str = arguments.get("time", "").strip()

    if not description:
        return "Description is required — what should I do when this task fires?", False
    if not time_str:
        return "Time is required — when should this run? (e.g. '5pm' or '0 17 * * *')", False

    # Detect mode
    simple_time = _parse_simple_time(time_str)
    is_cron = _is_cron(time_str)

    if not simple_time and not is_cron:
        return (
            f"Could not parse time '{time_str}'. "
            "Use simple time (5pm, 17:00, 1700) for one-shot, "
            "or cron (0 17 * * *) for recurring."
        ), False

    try:
        from core.api_fastapi import get_system
        system = get_system()
        if not system or not hasattr(system, 'continuity_scheduler') or not system.continuity_scheduler:
            return "Scheduler not available.", False

        scheduler = system.continuity_scheduler

        # Rate limit: count existing AI-scheduled tasks
        ai_tasks = [t for t in scheduler.list_tasks() if t.get("source") == "ai_scheduled"]
        if len(ai_tasks) >= MAX_AI_TASKS:
            return f"Maximum AI-scheduled tasks reached ({MAX_AI_TASKS}). Delete some in Schedule before creating more.", False

        now = _get_user_now()

        if simple_time:
            # One-shot: build a cron for the specific time
            hour, minute = simple_time
            cron = f"{minute} {hour} * * *"

            # Figure out if it's today or tomorrow
            target_today = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if target_today <= now:
                when_label = "tomorrow"
                target = target_today + timedelta(days=1)
            else:
                when_label = "today"
                target = target_today

            # %I (not %-I): the glibc-only %- modifier raises ValueError on
            # Windows; lstrip already removes the leading zero.
            time_label = target.strftime("%I:%M %p").lstrip("0")
            one_shot = True
        else:
            # Recurring: use the cron as-is
            cron = time_str.strip()
            time_label = cron
            when_label = "recurring"
            one_shot = False

        # Validate cron
        try:
            from croniter import croniter
            croniter(cron, now)
        except Exception as e:
            return f"Invalid cron expression '{cron}': {e}", False

        # Interval floor for recurring tasks: each fire is a full LLM+TTS
        # turn, and nothing else clamps frequency ('* * * * *' would talk
        # every minute forever, surviving restarts).
        if not one_shot:
            it = croniter(cron, now)
            fires = [it.get_next(datetime) for _ in range(4)]
            gap = min((b - a).total_seconds() for a, b in zip(fires, fires[1:]))
            if gap < 300:
                return (f"That schedule fires every {int(gap)}s — too frequent for a "
                        "recurring task. Minimum interval is 5 minutes (*/5 or slower)."), False

        # Build a short name from description
        name = description[:50] + ("..." if len(description) > 50 else "")

        task_data = {
            "name": name,
            "type": "task",
            "schedule": cron,
            "initial_message": description,
            "enabled": True,
            "source": "ai_scheduled",
            "emoji": "\u23F0",
            "tts_enabled": True,
            "inject_datetime": True,
            "max_runs": 1 if one_shot else 0,
            "delete_after_run": one_shot,
        }
        # Privacy rolls downhill: a task authored inside a private chat is
        # private-derived text, and fire-time otherwise derives privacy from
        # the PROMPT alone \u2014 the deferred-execution hole (hunt 2026-08-17).
        # The scheduler + ExecutionContext already honor this flag end to end.
        try:
            from core.api_fastapi import get_system
            if bool((get_system().llm_chat.session_manager
                     .get_chat_settings() or {}).get('private_chat')):
                task_data["privacy_required"] = True
        except Exception:
            pass

        task = scheduler.create_task(task_data)

        if one_shot:
            return f"Scheduled one-shot task for {time_label} {when_label}: \"{name}\"\nTask ID: {task['id'][:8]}", True
        else:
            return f"Scheduled recurring task ({cron}): \"{name}\"\nTask ID: {task['id'][:8]}", True

    except ValueError as e:
        return f"Failed to create task: {e}", False
    except Exception as e:
        logger.error(f"schedule_task failed: {e}", exc_info=True)
        return f"Failed to create task: {e}", False
