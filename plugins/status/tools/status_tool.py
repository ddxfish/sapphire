"""System status tool — lets Sapphire see her own state."""

import logging

logger = logging.getLogger(__name__)

ENABLED = True
EMOJI = "📡"

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "system_status",
            "description": "Get Sapphire's current system status — active model, provider, services, plugins, and diagnostics. Use this when the user asks about your current configuration, what model you're running, or to help troubleshoot issues.",
            "parameters": {
                "type": "object",
                "properties": {
                    "include_plugins": {
                        "type": "boolean",
                        "description": "Include full plugin list in output (default: false)"
                    },
                    "include_providers": {
                        "type": "boolean",
                        "description": "Include LLM provider details (default: false)"
                    }
                },
                "required": []
            }
        }
    }
]


def execute(function_name, arguments, config=None):
    if function_name != "system_status":
        return f"Unknown function: {function_name}", False

    include_plugins = arguments.get("include_plugins", False)
    include_providers = arguments.get("include_providers", False)

    try:
        from plugins.status.routes.status import get_full_status_sync
        data = get_full_status_sync()

        if "error" in data:
            return f"Status unavailable: {data['error']}", False

        lines = []

        # Identity
        ident = data.get("identity", {})
        uptime_m = ident.get("uptime_seconds", 0) // 60
        uptime_h, uptime_m = divmod(uptime_m, 60)
        env = "Docker" if ident.get("docker") else ident.get("os", "Unknown")
        lines.append(f"Sapphire v{ident.get('app_version', '?')} | Python {ident.get('python_version', '?')} | {env} | Uptime: {uptime_h}h {uptime_m}m")

        # Active session
        s = data.get("session", {})
        lines.append(f"Chat: {s.get('chat', '?')} | Prompt: {s.get('prompt', '?')} | Persona: {s.get('persona') or 'none'}")
        lines.append(f"LLM: {s.get('llm_primary', '?')} ({s.get('llm_model', 'default')}) | Toolset: {s.get('toolset', '?')} ({s.get('function_count', 0)} tools)")
        lines.append(f"Scopes: memory={s.get('memory_scope', '?')}, knowledge={s.get('knowledge_scope', '?')}")

        # Services
        svc = data.get("services", {})
        tts = svc.get("tts", {})
        stt = svc.get("stt", {})
        ww = svc.get("wakeword", {})
        emb = svc.get("embeddings", {})
        tts_str = f"{tts.get('provider', 'off')}" + (f" ({tts.get('voice', '')})" if tts.get('voice') else "")
        lines.append(f"TTS: {tts_str} | STT: {stt.get('provider', 'off')} | Wakeword: {'ON' if ww.get('enabled') else 'OFF'} | Embeddings: {emb.get('provider', 'off')}")

        # Daemons
        daemons = data.get("daemons", {})
        if daemons:
            daemon_str = ", ".join(f"{k}: {v}" for k, v in daemons.items())
            lines.append(f"Daemons: {daemon_str}")

        # Tasks
        t = data.get("tasks", {})
        lines.append(f"Tasks: {t.get('total', 0)} total, {t.get('enabled', 0)} enabled, {t.get('running', 0)} running now")

        # Metrics
        m = data.get("metrics", {})
        if m.get("total_tokens"):
            lines.append(f"Token usage (7d): {m['total_tokens']:,} total | {m.get('total_calls', 0)} calls")

        # Optional: providers
        if include_providers:
            provs = data.get("providers", [])
            if provs:
                lines.append("\nLLM Providers:")
                for p in provs:
                    status = "enabled" if p.get("enabled") else "disabled"
                    key_status = "key set" if p.get("has_key") else "no key"
                    local = " (local)" if p.get("is_local") else ""
                    lines.append(f"  {p.get('name', p.get('key', '?'))}: {status}, {key_status}{local}")

        # Optional: plugins
        if include_plugins:
            plugs = data.get("plugins", [])
            if plugs:
                loaded = [p for p in plugs if p.get("loaded")]
                disabled = [p for p in plugs if not p.get("enabled")]
                lines.append(f"\nPlugins: {len(loaded)} loaded, {len(disabled)} disabled")
                for p in plugs:
                    status = "loaded" if p.get("loaded") else ("enabled" if p.get("enabled") else "disabled")
                    ver = f" v{p['version']}" if p.get("version") else ""
                    lines.append(f"  {p['name']}{ver}: {status}")

        return "\n".join(lines), True

    except Exception as e:
        logger.error(f"system_status failed: {e}", exc_info=True)
        return f"Failed to gather status: {e}", False
