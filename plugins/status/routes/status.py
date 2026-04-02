"""Status data endpoint — gathers system state for both the app page and the AI tool."""

import sys
import time
import platform
import logging
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)

_boot_time = time.time()


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
