"""Status data endpoint — gathers system state for both the app page and the AI tool."""

import os
import sys
import time
import shutil
import platform
import subprocess
import logging
from pathlib import Path
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

_boot_time = time.time()


# ── Hardware / disk / activity / custom-command helpers ─────────────────
# All wrapped in try/except so a single failure (e.g. torch missing on a
# stripped-down install, psutil quirk on Windows, etc.) just hides that
# section instead of breaking get_self_info wholesale.

def _get_hardware_info() -> dict:
    """Cross-platform hardware summary. Falls back gracefully — every
    field is optional. psutil + torch are listed deps, but defensive
    imports keep this safe on environments where they're not present.
    """
    info = {}
    try:
        info["arch"] = platform.machine()
        info["cores_logical"] = os.cpu_count() or 0
    except Exception:
        pass
    try:
        import psutil
        info["cores_physical"] = psutil.cpu_count(logical=False) or 0
        vm = psutil.virtual_memory()
        info["ram_total_gb"] = round(vm.total / (1024**3), 1)
        info["ram_used_pct"] = int(vm.percent)
    except Exception:
        pass
    # CPU model name — Linux /proc/cpuinfo gives the most useful string.
    # platform.processor() is empty/garbage on most Linuxes. Best-effort.
    try:
        if sys.platform.startswith('linux'):
            with open('/proc/cpuinfo') as f:
                for line in f:
                    if line.startswith('model name'):
                        info["cpu_model"] = line.split(':', 1)[1].strip()
                        break
        else:
            proc = platform.processor()
            if proc:
                info["cpu_model"] = proc
    except Exception:
        pass
    # GPU: torch covers NVIDIA (cuda) + Apple Silicon (mps) cross-platform.
    # Defensive: if torch isn't importable or cuda init fails on a CPU-only
    # box, just return empty list.
    gpus = []
    try:
        import torch
        if torch.cuda.is_available():
            for i in range(torch.cuda.device_count()):
                try:
                    gpus.append({
                        "index": i,
                        "name": torch.cuda.get_device_name(i),
                        "backend": "cuda",
                    })
                except Exception:
                    pass
        elif hasattr(torch.backends, 'mps') and torch.backends.mps.is_available():
            gpus.append({"index": 0, "name": "Apple Silicon GPU", "backend": "mps"})
    except Exception:
        pass
    info["gpus"] = gpus
    return info


def _get_disk_info(user_dir: Path) -> dict:
    """Free/total on the drive holding user_dir + size of the main DBs.
    Skips a full `user/` walk — that can be slow on large installs and
    isn't worth the cost for chat-start latency.
    """
    info = {}
    try:
        total, used, free = shutil.disk_usage(user_dir)
        info["disk_total_gb"] = round(total / (1024**3), 1)
        info["disk_free_gb"] = round(free / (1024**3), 1)
        info["disk_used_pct"] = int(used / total * 100) if total else 0
    except Exception:
        pass
    db_sizes = {}
    for name in ("chats.db", "memory.db", "knowledge.db"):
        p = user_dir / name
        try:
            if p.exists():
                db_sizes[name] = round(p.stat().st_size / (1024**2), 1)
        except Exception:
            pass
    if db_sizes:
        info["db_sizes_mb"] = db_sizes
    return info


def _get_recent_activity(user_dir: Path, active_chat: str | None) -> dict:
    """Messages sent today on the active chat + last activity timestamp.
    Reads from chats.db directly to keep this independent of the rest of
    the chat module (avoids load-order issues during early boot).
    """
    info = {}
    if not active_chat:
        return info
    try:
        import sqlite3
        chats_db = user_dir / "chats.db"
        if not chats_db.exists():
            return info
        conn = sqlite3.connect(str(chats_db))
        c = conn.cursor()
        # Discover the schema — chat tables vary across versions. Just look
        # for any table with a 'timestamp' or 'created_at' column tied to
        # the active chat. Best-effort, never raise.
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            c.execute(
                "SELECT COUNT(*) FROM messages WHERE chat_name = ? AND substr(timestamp, 1, 10) = ?",
                (active_chat, today)
            )
            row = c.fetchone()
            if row:
                info["messages_today"] = row[0]
        except Exception:
            pass
        try:
            c.execute(
                "SELECT timestamp FROM messages WHERE chat_name = ? ORDER BY id DESC LIMIT 1",
                (active_chat,)
            )
            row = c.fetchone()
            if row and row[0]:
                info["last_message"] = row[0]
                # Compute relative "X ago"
                try:
                    last = datetime.fromisoformat(row[0].replace('Z', '+00:00').split('.')[0])
                    delta = datetime.now() - last.replace(tzinfo=None)
                    secs = int(delta.total_seconds())
                    if secs < 60:
                        info["last_message_ago"] = f"{secs}s ago"
                    elif secs < 3600:
                        info["last_message_ago"] = f"{secs // 60}m ago"
                    elif secs < 86400:
                        info["last_message_ago"] = f"{secs // 3600}h ago"
                    else:
                        info["last_message_ago"] = f"{secs // 86400}d ago"
                except Exception:
                    pass
        except Exception:
            pass
        conn.close()
    except Exception:
        pass
    return info


def _get_upcoming_tasks(scheduler, hours: int = 4) -> list:
    """Continuity tasks due in the next `hours` hours. Cron tasks only —
    daemons/heartbeats run on intervals and aren't 'scheduled' in the
    same sense. croniter is a continuity dep, defensive import.
    """
    upcoming = []
    if not scheduler:
        return upcoming
    try:
        from croniter import croniter
    except Exception:
        return upcoming
    try:
        now = datetime.now()
        cutoff = now + timedelta(hours=hours)
        for t in scheduler.list_tasks():
            if not t.get("enabled"):
                continue
            cron_expr = t.get("schedule") or t.get("cron")
            if not cron_expr:
                continue
            try:
                it = croniter(cron_expr, now)
                nxt = it.get_next(datetime)
                if nxt <= cutoff:
                    upcoming.append({
                        "name": t.get("name", "Unknown"),
                        "when": nxt.strftime("%H:%M"),
                    })
            except Exception:
                continue
        upcoming.sort(key=lambda x: x["when"])
    except Exception:
        pass
    return upcoming


def _run_custom_commands(commands_text: str, max_per_output: int = 500, timeout_s: int = 5) -> list:
    """Run user-configured shell commands and return their output.
    Format: one per line, `LABEL ::: COMMAND`. Lines starting with #
    are skipped. Output capped at `max_per_output` chars (label + marker
    show truncation). Stderr merged into stdout. sudo prefix refused.
    Pipes/redirects work via shell=True — that's the whole point.
    """
    results = []
    if not commands_text or not commands_text.strip():
        return results
    for raw in commands_text.splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        if '::: ' in line:
            label, cmd = line.split('::: ', 1)
        elif ':::' in line:
            label, cmd = line.split(':::', 1)
        else:
            # No separator — use the line as both label and command
            label, cmd = line, line
        label = label.strip()[:40] or "(unnamed)"
        cmd = cmd.strip()
        if not cmd:
            continue
        # Refuse sudo — cheap footgun guard.
        if cmd.startswith('sudo ') or cmd == 'sudo':
            results.append({"label": label, "command": cmd, "output": "(sudo refused)", "ok": False})
            continue
        try:
            r = subprocess.run(
                cmd, shell=True, capture_output=True, text=True,
                timeout=timeout_s, errors='replace',
            )
            out = (r.stdout or "") + (r.stderr or "")
            out = out.strip()
            if not out:
                out = f"(exit {r.returncode}, no output)" if r.returncode else "(no output)"
            if len(out) > max_per_output:
                out = out[:max_per_output] + f"… [+{len(out) - max_per_output} chars truncated]"
            results.append({"label": label, "command": cmd, "output": out, "ok": r.returncode == 0})
        except subprocess.TimeoutExpired:
            results.append({"label": label, "command": cmd, "output": f"(timed out after {timeout_s}s)", "ok": False})
        except Exception as e:
            results.append({"label": label, "command": cmd, "output": f"(error: {e})", "ok": False})
    return results


def _is_docker():
    try:
        return Path('/.dockerenv').exists() or 'docker' in Path('/proc/1/cgroup').read_text()
    except Exception:
        return False


def _get_git_branch():
    try:
        head = Path(__file__).parent.parent.parent.parent / '.git' / 'HEAD'
        content = head.read_text().strip()
        if content.startswith('ref: refs/heads/'):
            return content.replace('ref: refs/heads/', '')
        return content[:8]  # detached HEAD
    except Exception:
        return ''


async def get_full_status(**kwargs):
    """GET /api/plugin/status/full — comprehensive system snapshot."""
    return get_full_status_sync()


def get_full_status_sync():
    """GET /api/plugin/status/full — comprehensive system snapshot."""
    try:
        import config
        from core.api_fastapi import get_system, APP_VERSION

        system = get_system()
        session = system.llm_chat.session_manager
        fm = system.llm_chat.function_manager

        # Identity
        import locale
        try:
            tz_name = datetime.now().astimezone().tzname()
            tz_offset = datetime.now().astimezone().strftime('%z')
        except Exception:
            tz_name, tz_offset = "UTC", "+0000"

        identity = {
            "app_version": APP_VERSION,
            "python_version": platform.python_version(),
            "os": f"{platform.system()} {platform.release()}",
            "docker": _is_docker(),
            "uptime_seconds": int(time.time() - _boot_time),
            "hostname": platform.node(),
            "branch": _get_git_branch(),
            "timezone": f"{tz_name} ({tz_offset})",
            "datetime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

        # Active session
        chat_settings = session.get_chat_settings()
        active_session = {
            "chat": session.get_active_chat_name(),
            "prompt": chat_settings.get("prompt", ""),
            "persona": chat_settings.get("persona", ""),
            "llm_primary": chat_settings.get("llm_primary", "auto"),
            "llm_model": chat_settings.get("llm_model", ""),
            "toolset": fm.current_toolset_name,
            "function_count": len(fm._enabled_tools),
            "tool_names": sorted(t['function']['name'] for t in fm._enabled_tools),
            "memory_scope": chat_settings.get("memory_scope", "default"),
            "knowledge_scope": chat_settings.get("knowledge_scope", "default"),
            "parallel_tool_calls": getattr(config, 'MAX_PARALLEL_TOOLS', 1),
            "max_iterations": getattr(config, 'MAX_TOOL_ITERATIONS', 10),
            "theme": getattr(config, 'THEME', 'default'),
            "user_timezone": getattr(config, 'USER_TIMEZONE', ''),
        }

        # Services
        tts_provider = getattr(config, 'TTS_PROVIDER', 'none')
        stt_provider = getattr(config, 'STT_PROVIDER', 'none')
        wakeword_on = getattr(config, 'WAKE_WORD_ENABLED', False)
        embedding_provider = getattr(config, 'EMBEDDING_PROVIDER', 'local')

        # SOCKS proxy
        socks_enabled = getattr(config, 'SOCKS_ENABLED', False)
        socks_has_creds = False
        try:
            from core.credentials_manager import credentials
            socks_has_creds = credentials.has_socks_credentials()
        except Exception:
            pass

        services = {
            "tts": {
                "provider": tts_provider,
                "enabled": bool(tts_provider and tts_provider != 'none'),
                "voice": getattr(system.tts, '_voice', '') if hasattr(system, 'tts') else '',
            },
            "stt": {
                "provider": stt_provider,
                "enabled": bool(stt_provider and stt_provider != 'none'),
            },
            "wakeword": {
                "enabled": wakeword_on,
                "model": getattr(config, 'WAKEWORD_MODEL', ''),
            },
            "embeddings": {
                "provider": embedding_provider,
                "enabled": bool(embedding_provider and embedding_provider != 'none'),
            },
            "socks": {
                "enabled": socks_enabled,
                "has_credentials": socks_has_creds,
            },
        }

        # Daemons
        daemons = {}
        try:
            from core.plugin_loader import plugin_loader
            for pname, info in plugin_loader._plugins.items():
                if info.get("daemon_started"):
                    daemons[pname] = "running"
                elif info.get("daemon_module"):
                    daemons[pname] = "loaded"
        except Exception:
            pass

        # LLM Providers
        providers = []
        try:
            all_pconfig = {**dict(getattr(config, 'LLM_PROVIDERS', {})), **dict(getattr(config, 'LLM_CUSTOM_PROVIDERS', {}))}
            from core.chat.llm_providers import provider_registry
            all_registry = {**provider_registry._core, **provider_registry._plugins}
            for key, pconfig in all_pconfig.items():
                reg = all_registry.get(key, {})
                providers.append({
                    "key": key,
                    "name": reg.get("display_name") or pconfig.get("display_name", key),
                    "enabled": pconfig.get("enabled", False),
                    "is_local": reg.get("is_local", pconfig.get("is_local", False)),
                    "has_key": bool(_check_provider_key(key)),
                })
        except Exception as e:
            logger.debug(f"Provider listing failed: {e}")

        # Tasks (with type breakdown)
        tasks_info = {"total": 0, "enabled": 0, "running": 0, "tasks": 0, "heartbeats": 0, "daemons": 0, "webhooks": 0}
        try:
            if hasattr(system, 'continuity_scheduler') and system.continuity_scheduler:
                sched = system.continuity_scheduler
                all_tasks = sched.list_tasks()
                tasks_info["total"] = len(all_tasks)
                tasks_info["enabled"] = sum(1 for t in all_tasks if t.get("enabled"))
                tasks_info["running"] = sum(1 for t in all_tasks if t.get("running"))
                for t in all_tasks:
                    tt = t.get("type", "task")
                    if tt == "heartbeat":
                        tasks_info["heartbeats"] += 1
                    elif tt == "daemon":
                        tasks_info["daemons"] += 1
                    elif tt == "webhook":
                        tasks_info["webhooks"] += 1
                    else:
                        tasks_info["tasks"] += 1
        except Exception:
            pass

        # Plugins (with verification status)
        plugins = []
        try:
            from core.plugin_loader import plugin_loader
            for name, info in plugin_loader._plugins.items():
                plugins.append({
                    "name": name,
                    "loaded": info.get("loaded", False),
                    "enabled": info.get("enabled", False),
                    "band": info.get("band", ""),
                    "version": info.get("manifest", {}).get("version", ""),
                    "verify_tier": info.get("verify_tier", "unsigned"),
                    "missing_deps": info.get("missing_deps", []),
                })
        except Exception:
            pass

        # Token metrics
        metrics = {}
        try:
            from core.metrics import token_metrics
            metrics = token_metrics.summary(days=7)
        except Exception:
            pass

        # Audio devices
        audio_info = {}
        try:
            audio_info["input"] = getattr(config, 'AUDIO_INPUT_DEVICE', 'default')
            audio_info["output"] = getattr(config, 'AUDIO_OUTPUT_DEVICE', 'default')
        except Exception:
            pass

        # Backup stats
        backup_info = {}
        try:
            from core.backup import backup_manager
            backups = backup_manager.list_backups()
            backup_info["count"] = len(backups)
            if backups:
                backup_info["latest"] = backups[0].get("filename", "")
                backup_info["latest_date"] = backups[0].get("date", "")
                backup_info["latest_size"] = backups[0].get("size", 0)
        except Exception:
            pass

        # Update check (use cached result if available)
        update_info = {}
        try:
            update_file = Path(__file__).parent.parent.parent.parent / "user" / "webui" / "update_check.json"
            if update_file.exists():
                import json as _json
                cached = _json.loads(update_file.read_text(encoding="utf-8"))
                update_info["available"] = cached.get("update_available", False)
                update_info["latest_version"] = cached.get("latest_version", "")
                update_info["current_version"] = APP_VERSION
        except Exception:
            pass

        # Mind / Knowledge / Memory stats
        mind_info = {"scopes": [], "memories": 0, "memory_scopes": {}, "people": 0, "people_by_scope": {},
                     "knowledge_total": 0, "knowledge_scopes": {}}
        user_dir = Path(__file__).parent.parent.parent.parent / "user"
        try:
            import sqlite3

            # Memories (user/memory.db)
            mem_path = user_dir / "memory.db"
            if mem_path.exists():
                conn = sqlite3.connect(str(mem_path))
                c = conn.cursor()
                try:
                    c.execute("SELECT scope, COUNT(*) FROM memories GROUP BY scope")
                    mind_info["memory_scopes"] = {r[0]: r[1] for r in c.fetchall()}
                    mind_info["memories"] = sum(mind_info["memory_scopes"].values())
                except Exception:
                    pass
                try:
                    c.execute("SELECT name FROM memory_scopes")
                    mind_info["scopes"] = sorted(set(mind_info.get("scopes", []) + [r[0] for r in c.fetchall()]))
                except Exception:
                    pass
                conn.close()

            # Knowledge + People (user/knowledge.db)
            kb_path = user_dir / "knowledge.db"
            if kb_path.exists():
                conn = sqlite3.connect(str(kb_path))
                c = conn.cursor()
                # People
                try:
                    c.execute("SELECT scope, COUNT(*) FROM people GROUP BY scope")
                    mind_info["people_by_scope"] = {r[0]: r[1] for r in c.fetchall()}
                    mind_info["people"] = sum(mind_info["people_by_scope"].values())
                except Exception:
                    pass
                # Knowledge entries by scope (via tabs)
                try:
                    c.execute("SELECT t.scope, COUNT(e.id) FROM knowledge_tabs t LEFT JOIN knowledge_entries e ON e.tab_id = t.id GROUP BY t.scope")
                    mind_info["knowledge_scopes"] = {r[0]: r[1] for r in c.fetchall()}
                    mind_info["knowledge_total"] = sum(mind_info["knowledge_scopes"].values())
                except Exception:
                    pass
                # Collect all scope names
                try:
                    c.execute("SELECT name FROM knowledge_scopes")
                    mind_info["scopes"] = sorted(set(mind_info.get("scopes", []) + [r[0] for r in c.fetchall()]))
                except Exception:
                    pass
                conn.close()
        except Exception:
            pass

        # Hardware / disk / activity / upcoming tasks / custom commands.
        # Each gatherer is independently try/except'd inside — failures
        # produce empty dicts/lists instead of breaking get_self_info.
        hardware_info = _get_hardware_info()
        disk_info = _get_disk_info(user_dir)
        recent_activity = _get_recent_activity(user_dir, active_session.get("chat"))
        upcoming = _get_upcoming_tasks(getattr(system, 'continuity_scheduler', None))

        custom_results = []
        try:
            from core.plugin_loader import plugin_loader
            settings_obj = plugin_loader.get_plugin_settings("status") or {}
            cmds_text = settings_obj.get("custom_status_commands", "")
            if cmds_text:
                custom_results = _run_custom_commands(cmds_text)
        except Exception as e:
            logger.debug(f"Custom commands skipped: {e}")

        return {
            "identity": identity,
            "session": active_session,
            "services": services,
            "daemons": daemons,
            "providers": providers,
            "tasks": tasks_info,
            "plugins": plugins,
            "metrics": metrics,
            "audio": audio_info,
            "backup": backup_info,
            "update": update_info,
            "mind": mind_info,
            "hardware": hardware_info,
            "disk": disk_info,
            "recent_activity": recent_activity,
            "upcoming_tasks": upcoming,
            "custom": custom_results,
        }

    except Exception as e:
        logger.error(f"Status gathering failed: {e}", exc_info=True)
        return {"error": str(e)}


LOG_PATH = Path(__file__).parent.parent.parent.parent / "user" / "logs" / "sapphire.log"
LOG_LEVELS = {'DEBUG': 10, 'INFO': 20, 'WARNING': 30, 'ERROR': 40, 'CRITICAL': 50}


async def get_logs(**kwargs):
    """GET /api/plugin/status/logs?lines=200&level=WARNING&search=telegram"""
    return get_logs_sync(kwargs.get('request'))


def get_logs_sync(request=None):
    lines_param = 200
    level_param = 'ALL'
    search_param = ''

    if request:
        lines_param = int(request.query_params.get('lines', 200))
        level_param = request.query_params.get('level', 'ALL').upper()
        search_param = request.query_params.get('search', '').strip()

    lines_param = min(lines_param, 2000)  # cap at 2000

    if not LOG_PATH.exists():
        return {"lines": [], "total": 0, "filtered": 0}

    # Read last N*3 lines to have enough after filtering
    try:
        with open(LOG_PATH, 'r', encoding='utf-8', errors='replace') as f:
            all_lines = f.readlines()
    except Exception as e:
        return {"lines": [], "total": 0, "error": str(e)}

    total = len(all_lines)

    # Parse and filter
    min_level = LOG_LEVELS.get(level_param, 0)
    search_lower = search_param.lower()
    result = []

    for raw in all_lines:
        raw = raw.rstrip('\n')
        if not raw:
            continue

        # Parse level from format: "2026-04-02 12:51:43,953 - name - LEVEL - message"
        level = 'INFO'
        parts = raw.split(' - ', 3)
        if len(parts) >= 3:
            level = parts[2].strip()

        level_num = LOG_LEVELS.get(level, 20)

        if level_param != 'ALL' and level_num < min_level:
            continue
        if search_lower and search_lower not in raw.lower():
            continue

        result.append({"text": raw, "level": level})

    # Return last N
    filtered = result[-lines_param:]
    return {"lines": filtered, "total": total, "filtered": len(result), "showing": len(filtered)}


def _check_provider_key(provider_key):
    """Check if a provider has an API key via credentials or env."""
    try:
        from core.credentials_manager import credentials
        return bool(credentials.get_llm_api_key(provider_key))
    except Exception:
        return False
