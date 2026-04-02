"""Self-awareness tool — lets Sapphire see her own state, time, and environment."""

import logging

logger = logging.getLogger(__name__)

ENABLED = True
EMOJI = "📡"

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_self_info",
            "description": "Get your current system status, configuration, date/time, active model, services, plugins, memory stats, and diagnostics. Use this when asked about yourself, your current state, what time it is, what model you're running, or to help troubleshoot issues.",
            "parameters": {
                "type": "object",
                "properties": {
                    "detail": {
                        "type": "string",
                        "enum": ["brief", "full"],
                        "description": "brief = key info + time/date, full = everything including plugins, providers, tools, mind stats"
                    },
                    "include_errors": {
                        "type": "boolean",
                        "description": "Include last 20 WARNING/ERROR log lines for troubleshooting (default: false)"
                    }
                },
                "required": []
            }
        }
    }
]


def execute(function_name, arguments, config=None):
    if function_name != "get_self_info":
        return f"Unknown function: {function_name}", False

    detail = arguments.get("detail", "brief")
    include_errors = arguments.get("include_errors", False)

    try:
        from plugins.status.routes.status import get_full_status_sync
        data = get_full_status_sync()

        if "error" in data:
            return f"Status unavailable: {data['error']}", False

        lines = []

        # Identity + time (always included)
        ident = data.get("identity", {})
        uptime_m = ident.get("uptime_seconds", 0) // 60
        uptime_h, uptime_m = divmod(uptime_m, 60)
        env = "Docker" if ident.get("docker") else ident.get("os", "Unknown")
        lines.append(f"Date/Time: {ident.get('datetime', '?')} {ident.get('timezone', '')}")
        lines.append(f"Sapphire v{ident.get('app_version', '?')} | Python {ident.get('python_version', '?')} | {env} | Uptime: {uptime_h}h {uptime_m}m")

        # Update
        update = data.get("update", {})
        if update.get("available"):
            lines.append(f"UPDATE AVAILABLE: v{update.get('latest_version', '?')}")

        # Active session
        s = data.get("session", {})
        lines.append(f"Chat: {s.get('chat', '?')} | Prompt: {s.get('prompt', '?')} | Persona: {s.get('persona') or 'none'}")
        lines.append(f"LLM: {s.get('llm_primary', '?')} ({s.get('llm_model', 'default')}) | Toolset: {s.get('toolset', '?')} ({s.get('function_count', 0)} tools)")
        lines.append(f"Parallel: {s.get('parallel_tool_calls', 1)} | Max iterations: {s.get('max_iterations', 10)} | Theme: {s.get('theme', 'default')}")
        if s.get('user_timezone'):
            lines.append(f"App timezone: {s['user_timezone']}")
        lines.append(f"Scopes: memory={s.get('memory_scope', '?')}, knowledge={s.get('knowledge_scope', '?')}")

        # Services
        svc = data.get("services", {})
        tts = svc.get("tts", {})
        stt = svc.get("stt", {})
        ww = svc.get("wakeword", {})
        emb = svc.get("embeddings", {})
        tts_str = f"{tts.get('provider', 'off')}" + (f" ({tts.get('voice', '')})" if tts.get('voice') else "")
        lines.append(f"TTS: {tts_str} | STT: {stt.get('provider', 'off')} | Wakeword: {'ON' if ww.get('enabled') else 'OFF'} | Embeddings: {emb.get('provider', 'off')}")

        # Audio
        audio = data.get("audio", {})
        if audio:
            lines.append(f"Audio: in={audio.get('input', 'default')}, out={audio.get('output', 'default')}")

        # Daemons
        daemons = data.get("daemons", {})
        if daemons:
            lines.append(f"Daemons: {', '.join(f'{k}: {v}' for k, v in daemons.items())}")

        # Tasks
        t = data.get("tasks", {})
        lines.append(f"Tasks: {t.get('total', 0)} total, {t.get('enabled', 0)} enabled, {t.get('running', 0)} running")

        # Backup
        backup = data.get("backup", {})
        if backup.get("count") is not None:
            lines.append(f"Backups: {backup['count']}{' (latest: ' + backup.get('latest_date', '?') + ')' if backup.get('latest') else ''}")

        # Mind
        mind = data.get("mind", {})
        if mind:
            scopes = mind.get("scopes", [])
            lines.append(f"Mind scopes: {', '.join(scopes) if scopes else 'none'}")
            lines.append(f"  Memories: {mind.get('memories', 0)} total")
            mem_scopes = mind.get("memory_scopes", {})
            if mem_scopes:
                lines.append(f"    by scope: {', '.join(f'{k}: {v}' for k, v in mem_scopes.items())}")
            lines.append(f"  People: {mind.get('people', 0)} total")
            lines.append(f"  Knowledge: {mind.get('knowledge_total', 0)} entries")

        # Metrics
        m = data.get("metrics", {})
        if m.get("total_tokens"):
            lines.append(f"Token usage (7d): {m['total_tokens']:,} total | {m.get('total_calls', 0)} calls")

        if detail == "full":
            # Providers
            provs = data.get("providers", [])
            if provs:
                lines.append("\nLLM Providers:")
                for p in provs:
                    status = "enabled" if p.get("enabled") else "disabled"
                    key_status = "key set" if p.get("has_key") else "no key"
                    local = " (local)" if p.get("is_local") else ""
                    lines.append(f"  {p.get('name', p.get('key', '?'))}: {status}, {key_status}{local}")

            # Plugins
            plugs = data.get("plugins", [])
            if plugs:
                loaded = [p for p in plugs if p.get("loaded")]
                lines.append(f"\nPlugins: {len(loaded)} loaded / {len(plugs)} total")
                for p in plugs:
                    status = "loaded" if p.get("loaded") else ("enabled" if p.get("enabled") else "disabled")
                    ver = f" v{p['version']}" if p.get("version") else ""
                    tier = p.get("verify_tier", "unsigned")
                    deps = f" [MISSING: {', '.join(p['missing_deps'])}]" if p.get("missing_deps") else ""
                    lines.append(f"  {p['name']}{ver}: {status} ({tier}){deps}")

            # Tools
            tool_names = s.get("tool_names", [])
            if tool_names:
                lines.append(f"\nEnabled tools ({len(tool_names)}): {', '.join(tool_names)}")

        # Recent errors
        if include_errors:
            try:
                from plugins.status.routes.status import get_logs_sync
                log_data = get_logs_sync()  # no request = defaults (200 lines, ALL)
                error_lines = [l for l in (log_data.get("lines", [])) if l["level"] in ("WARNING", "ERROR", "CRITICAL")]
                recent = error_lines[-20:]
                if recent:
                    lines.append(f"\nRecent warnings/errors ({len(recent)}):")
                    for l in recent:
                        lines.append(f"  [{l['level']}] {l['text']}")
                else:
                    lines.append("\nNo recent warnings or errors.")
            except Exception:
                lines.append("\nCould not read logs.")

        return "\n".join(lines), True

    except Exception as e:
        logger.error(f"get_self_info failed: {e}", exc_info=True)
        return f"Failed to gather status: {e}", False
