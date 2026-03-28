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
        identity = {
            "app_version": APP_VERSION,
            "python_version": platform.python_version(),
            "os": f"{platform.system()} {platform.release()}",
            "docker": _is_docker(),
            "uptime_seconds": int(time.time() - _boot_time),
            "hostname": platform.node(),
            "branch": _get_git_branch(),
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
            "memory_scope": chat_settings.get("memory_scope", "default"),
            "knowledge_scope": chat_settings.get("knowledge_scope", "default"),
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

        # Plugins
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

        return {
            "identity": identity,
            "session": active_session,
            "services": services,
            "daemons": daemons,
            "providers": providers,
            "tasks": tasks_info,
            "plugins": plugins,
            "metrics": metrics,
        }

    except Exception as e:
        logger.error(f"Status gathering failed: {e}", exc_info=True)
        return {"error": str(e)}


def _check_provider_key(provider_key):
    """Check if a provider has an API key via credentials or env."""
    try:
        from core.credentials_manager import credentials
        return bool(credentials.get_llm_api_key(provider_key))
    except Exception:
        return False
