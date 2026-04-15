# plugins/clock/tools/clock_tools.py
# Clock plugin — get_time, timer, stopwatch, alarm.
# Tight tool surface: 4 tools, terse descriptions, schema carries the contract.

import logging
import os
import re
import subprocess
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

ENABLED = True
EMOJI = '⏰'

_PLUGIN_DIR = Path(__file__).resolve().parent.parent
_PING_WAV = _PLUGIN_DIR / 'ping.wav'

# In-memory state — timers + stopwatches. Lost on restart by design;
# alarms persist via continuity scheduler.
_timers = {}        # name -> {'thread', 'expires_at', 'cancel_event'}
_stopwatches = {}   # name -> {'started_at'}
_state_lock = threading.Lock()

# Alarm task naming convention: "(alarm) <name>" so it reads naturally in the
# Schedule page UI and is trivially filterable.
_ALARM_PREFIX = '(alarm) '


AVAILABLE_FUNCTIONS = [
    'get_time',
    'set_timer',
    'set_stopwatch',
    'set_alarm',
]

TOOLS = [
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "get_time",
            "description": "Current date/time plus all active timers, stopwatches, and alarms by name.",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "set_timer",
            "description": "Create or cancel a countdown timer by name. Pings on expiry.",
            "parameters": {
                "type": "object",
                "properties": {
                    "time": {"type": "string", "description": "Duration like '5m', '30s', '1h 10m'. Required unless delete=true."},
                    "name": {"type": "string", "description": "Timer name. Reusing replaces."},
                    "delete": {"type": "boolean", "description": "If true, cancel by name instead. Default false."}
                },
                "required": ["name"]
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "set_stopwatch",
            "description": "Start a named stopwatch. Subsequent calls show elapsed. delete=true removes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Stopwatch name. Reusing returns elapsed."},
                    "delete": {"type": "boolean", "description": "If true, remove by name. Default false."}
                },
                "required": ["name"]
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "set_alarm",
            "description": "Create or cancel a one-shot alarm at a time-of-day. Survives reboot.",
            "parameters": {
                "type": "object",
                "properties": {
                    "time": {"type": "string", "description": "Time of day like '07:00', '2:30pm'. Required unless delete=true."},
                    "name": {"type": "string", "description": "Alarm name. Reusing replaces."},
                    "delete": {"type": "boolean", "description": "If true, cancel by name. Default false."}
                },
                "required": ["name"]
            }
        }
    },
]


# =============================================================================
# PARSING / FORMATTING
# =============================================================================

_DURATION_RE = re.compile(r'(\d+(?:\.\d+)?)\s*(h|hr|hour|hours|m|min|minute|minutes|s|sec|second|seconds)?')


def _parse_duration(s):
    """'5m', '30s', '1h 10m', '5' (bare = minutes) → total seconds. Returns None if unparseable."""
    if not s:
        return None
    s = str(s).strip().lower()
    total = 0.0
    matched_any = False
    for num, unit in _DURATION_RE.findall(s):
        try:
            n = float(num)
        except ValueError:
            continue
        unit = unit or 'm'  # bare number = minutes
        if unit.startswith('h'):
            total += n * 3600
        elif unit.startswith('m') and not unit.startswith('mi'):
            total += n * 60
        elif unit.startswith('mi'):  # min, minute, minutes
            total += n * 60
        elif unit.startswith('s'):
            total += n
        matched_any = True
    return total if matched_any and total > 0 else None


def _parse_time_of_day(s):
    """'07:00', '2:30pm', '14:00' → datetime today (or tomorrow if past). None if bad."""
    if not s:
        return None
    s = str(s).strip().lower().replace(' ', '')
    m = re.match(r'^(\d{1,2}):(\d{2})\s*(am|pm)?$', s) or re.match(r'^(\d{1,2})\s*(am|pm)$', s)
    if not m:
        return None
    parts = m.groups()
    try:
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) >= 3 and parts[1] and parts[1].isdigit() else 0
        ampm = parts[-1] if parts[-1] in ('am', 'pm') else None
        if ampm == 'pm' and hour < 12:
            hour += 12
        elif ampm == 'am' and hour == 12:
            hour = 0
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            return None
    except (ValueError, IndexError):
        return None
    now = datetime.now()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target


def _format_remaining(seconds):
    """Seconds → '3m 12s' / '1h 4m' / '20s'."""
    seconds = int(max(0, seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        m, s = divmod(seconds, 60)
        return f"{m}m {s}s" if s else f"{m}m"
    h, rem = divmod(seconds, 3600)
    m = rem // 60
    return f"{h}h {m}m" if m else f"{h}h"


# =============================================================================
# PING — sound playback with volume + TTS mute
# =============================================================================

def _play_ping():
    """Play ping.wav. Mutes Sapphire's TTS during, restores volume after."""
    if not _PING_WAV.exists():
        logger.warning(f"[clock] ping.wav not found at {_PING_WAV}")
        return False

    aplay = '/usr/bin/aplay'
    amixer = '/usr/bin/amixer'
    if not os.path.isfile(aplay):
        logger.warning("[clock] aplay not installed; ping will be silent")
        return False

    try:
        # Snapshot current volume
        prev_vol = None
        if os.path.isfile(amixer):
            r = subprocess.run([amixer, 'get', 'Master'], capture_output=True, text=True, timeout=2)
            m = re.search(r'(\d+)%', r.stdout or '')
            prev_vol = int(m.group(1)) if m else None
            subprocess.run([amixer, '-q', 'set', 'Master', '40%'], timeout=2,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # Mute Sapphire's TTS so the ping isn't talked over
        try:
            from core.api_fastapi import get_system
            sys_obj = get_system()
            if sys_obj and hasattr(sys_obj, 'tts'):
                sys_obj.tts.stop()
        except Exception:
            pass
        # Play twice with a short gap — distinguishable from random noise
        for _ in range(2):
            subprocess.run([aplay, '-q', str(_PING_WAV)], timeout=5,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(0.6)
        if prev_vol is not None and os.path.isfile(amixer):
            subprocess.run([amixer, '-q', 'set', 'Master', f'{prev_vol}%'], timeout=2,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception as e:
        logger.error(f"[clock] ping failed: {e}")
        return False


# =============================================================================
# TOOL: get_time
# =============================================================================

def _list_alarm_tasks():
    """Query the continuity scheduler for our alarm tasks."""
    try:
        from core.api_fastapi import get_system
        sys_obj = get_system()
        if not sys_obj or not hasattr(sys_obj, 'continuity_scheduler'):
            return []
        scheduler = sys_obj.continuity_scheduler
        tasks = scheduler.list_tasks() if hasattr(scheduler, 'list_tasks') else []
        out = []
        for t in tasks:
            name = t.get('name', '')
            if not name.startswith(_ALARM_PREFIX):
                continue
            user_name = name[len(_ALARM_PREFIX):]
            sched = t.get('schedule', '')
            # cron "M H * * *" → "HH:MM"
            cron_parts = sched.split()
            time_str = ''
            if len(cron_parts) >= 2 and cron_parts[0].isdigit() and cron_parts[1].isdigit():
                time_str = f"{int(cron_parts[1]):02d}:{int(cron_parts[0]):02d}"
            out.append({'name': user_name, 'fires_at': time_str, 'enabled': t.get('enabled', True)})
        return out
    except Exception as e:
        logger.warning(f"[clock] could not list alarms: {e}")
        return []


def _get_time(arguments):
    now = datetime.now()
    timers_out = []
    with _state_lock:
        for name, t in list(_timers.items()):
            remaining = (t['expires_at'] - now).total_seconds()
            if remaining > 0:
                timers_out.append({'name': name, 'remaining': _format_remaining(remaining)})
        stopwatches_out = [
            {'name': name, 'elapsed': _format_remaining((now - sw['started_at']).total_seconds())}
            for name, sw in _stopwatches.items()
        ]
    alarms_out = _list_alarm_tasks()

    lines = [f"**Now:** {now.strftime('%Y-%m-%d %H:%M:%S')}"]
    if timers_out:
        lines.append("\n**Timers:**")
        lines += [f"- `{t['name']}` — {t['remaining']} remaining" for t in timers_out]
    if stopwatches_out:
        lines.append("\n**Stopwatches:**")
        lines += [f"- `{sw['name']}` — {sw['elapsed']} elapsed" for sw in stopwatches_out]
    if alarms_out:
        lines.append("\n**Alarms:**")
        lines += [f"- `{a['name']}` — fires at {a['fires_at']}" for a in alarms_out]
    if not (timers_out or stopwatches_out or alarms_out):
        lines.append("\n_No active timers, stopwatches, or alarms._")
    lines.append("\n_To delete any: call set_timer/set_stopwatch/set_alarm with delete=true._")
    return "\n".join(lines), True


# =============================================================================
# TOOL: set_timer
# =============================================================================

def _timer_thread(name, seconds, cancel_event):
    """Wait until expiry or cancellation, then ping."""
    if cancel_event.wait(timeout=seconds):
        return  # cancelled
    with _state_lock:
        # Verify this timer is still the active one for this name
        t = _timers.get(name)
        if not t or t['cancel_event'] is not cancel_event:
            return
        _timers.pop(name, None)
    logger.info(f"[clock] timer '{name}' expired")
    _play_ping()


def _set_timer(arguments):
    name = (arguments.get('name') or '').strip()
    if not name:
        return "name is required.", False
    delete = bool(arguments.get('delete', False))

    if delete:
        with _state_lock:
            t = _timers.pop(name, None)
        if t:
            t['cancel_event'].set()
            return f"Timer '{name}' cancelled.", True
        return f"No timer named '{name}'.", False

    seconds = _parse_duration(arguments.get('time'))
    if seconds is None:
        return "Bad time. Use '5m', '30s', '1h 10m', etc.", False

    # Replace existing timer with same name
    with _state_lock:
        existing = _timers.pop(name, None)
        if existing:
            existing['cancel_event'].set()
        cancel_event = threading.Event()
        thread = threading.Thread(
            target=_timer_thread, args=(name, seconds, cancel_event), daemon=True,
            name=f'clock-timer-{name}',
        )
        _timers[name] = {
            'thread': thread,
            'expires_at': datetime.now() + timedelta(seconds=seconds),
            'cancel_event': cancel_event,
        }
        thread.start()
    return f"Timer '{name}' set for {_format_remaining(seconds)}.", True


# =============================================================================
# TOOL: set_stopwatch
# =============================================================================

def _set_stopwatch(arguments):
    name = (arguments.get('name') or '').strip()
    if not name:
        return "name is required.", False
    delete = bool(arguments.get('delete', False))

    with _state_lock:
        if delete:
            sw = _stopwatches.pop(name, None)
            if sw:
                elapsed = (datetime.now() - sw['started_at']).total_seconds()
                return f"Stopwatch '{name}' removed at {_format_remaining(elapsed)}.", True
            return f"No stopwatch named '{name}'.", False
        existing = _stopwatches.get(name)
        if existing:
            elapsed = (datetime.now() - existing['started_at']).total_seconds()
            return f"Stopwatch '{name}' already running — {_format_remaining(elapsed)} elapsed.", True
        _stopwatches[name] = {'started_at': datetime.now()}
    return f"Stopwatch '{name}' started.", True


# =============================================================================
# TOOL: set_alarm  (continuity-scheduler-backed, survives reboot)
# =============================================================================

def _set_alarm(arguments):
    name = (arguments.get('name') or '').strip()
    if not name:
        return "name is required.", False
    delete = bool(arguments.get('delete', False))
    task_name = f"{_ALARM_PREFIX}{name}"

    try:
        from core.api_fastapi import get_system
        sys_obj = get_system()
        if not sys_obj or not hasattr(sys_obj, 'continuity_scheduler'):
            return "Continuity scheduler not available.", False
        scheduler = sys_obj.continuity_scheduler
    except Exception as e:
        return f"Scheduler unreachable: {e}", False

    # Find existing alarm task by name
    existing_id = None
    try:
        for t in scheduler.list_tasks():
            if t.get('name') == task_name:
                existing_id = t.get('id')
                break
    except Exception as e:
        logger.warning(f"[clock] couldn't list tasks: {e}")

    if delete:
        if existing_id:
            try:
                scheduler.delete_task(existing_id)
                return f"Alarm '{name}' cancelled.", True
            except Exception as e:
                return f"Couldn't cancel alarm: {e}", False
        return f"No alarm named '{name}'.", False

    target = _parse_time_of_day(arguments.get('time'))
    if not target:
        return "Bad time. Use '07:00', '2:30pm', '14:00'.", False

    cron = f"{target.minute} {target.hour} * * *"

    # Replace existing
    if existing_id:
        try:
            scheduler.delete_task(existing_id)
        except Exception:
            pass

    try:
        scheduler.create_task({
            "name": task_name,
            "schedule": cron,
            "enabled": True,
            "source": "plugin:clock",
            "handler": "alarm_handler.py",
            "plugin_dir": str(_PLUGIN_DIR),
            "delete_after_run": True,
            "emoji": "⏰",
            "initial_message": f"Alarm: {name}",
        })
    except Exception as e:
        return f"Couldn't create alarm: {e}", False

    in_str = _format_remaining((target - datetime.now()).total_seconds())
    return f"Alarm '{name}' set for {target.strftime('%H:%M')} (in {in_str}).", True


# =============================================================================
# DISPATCH
# =============================================================================

def execute(function_name, arguments, config):
    try:
        if function_name == 'get_time':
            return _get_time(arguments)
        elif function_name == 'set_timer':
            return _set_timer(arguments)
        elif function_name == 'set_stopwatch':
            return _set_stopwatch(arguments)
        elif function_name == 'set_alarm':
            return _set_alarm(arguments)
        else:
            return f"Unknown function: {function_name}", False
    except Exception as e:
        logger.error(f"[clock] {function_name} failed: {e}", exc_info=True)
        return f"Clock error: {e}", False
