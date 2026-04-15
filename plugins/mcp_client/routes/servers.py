# plugins/mcp_client/routes/servers.py — MCP server management endpoints

import json
import logging

logger = logging.getLogger(__name__)


def _get_settings():
    from core.plugin_loader import plugin_loader
    return plugin_loader.get_plugin_settings("mcp_client") or {}


import threading as _threading
_save_lock = _threading.Lock()


def _update_servers(mutator):
    """Atomic read-modify-write for the 'servers' key in mcp_client.json.

    mutator(current_servers) -> new_servers. Holds a per-file lock across the
    full span so concurrent add_server / remove_server calls can't clobber
    each other (sibling family of the MCP save-destroys-servers bug). Unique
    tmp suffix prevents cross-writer .tmp truncation.
    """
    from pathlib import Path
    import os as _os
    settings_path = Path(__file__).parent.parent.parent.parent / "user" / "webui" / "plugins" / "mcp_client.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    with _save_lock:
        try:
            existing = json.loads(settings_path.read_text(encoding='utf-8')) if settings_path.exists() else {}
        except Exception:
            existing = {}
        existing["servers"] = mutator(existing.get("servers", {}))
        tmp_path = settings_path.with_suffix(f'.tmp.{_os.getpid()}.{id(existing):x}')
        try:
            tmp_path.write_text(json.dumps(existing, indent=2), encoding='utf-8')
            tmp_path.replace(settings_path)
        finally:
            if tmp_path.exists():
                try: tmp_path.unlink()
                except Exception: pass


def _save_servers(servers):
    """Compatibility shim for callers that already have the full new dict.
    NEW callers should use _update_servers(mutator) for atomic RMW."""
    _update_servers(lambda _old: servers)


async def list_servers(**kwargs):
    """GET /api/plugin/mcp_client/servers — list all servers with status."""
    from plugins.mcp_client.daemon import get_all_servers

    settings = _get_settings()
    configured = settings.get("servers", {})
    live = get_all_servers()

    servers = []
    for name, config in configured.items():
        live_info = live.get(name, {})
        servers.append({
            "name": name,
            "type": config.get("type", "stdio"),
            "command": config.get("command", ""),
            "url": config.get("url", ""),
            "status": live_info.get("status", "disconnected"),
            "tool_count": len(live_info.get("tools", [])),
            "enabled": config.get("enabled", True),
        })

    return {"servers": servers}


async def add_server(**kwargs):
    """POST /api/plugin/mcp_client/servers — add and connect a new server."""
    body = kwargs.get("body", {})
    name = body.get("name", "").strip()
    server_type = body.get("type", "stdio")

    if not name:
        return {"error": "Server name required"}

    # Sanitize name
    name = "".join(c for c in name if c.isalnum() or c in "-_").lower()
    if not name:
        return {"error": "Invalid server name"}

    settings = _get_settings()
    servers = settings.get("servers", {})
    if name in servers:
        return {"error": f"Server '{name}' already exists"}

    if server_type == "stdio":
        command = body.get("command", "").strip()
        args = body.get("args", [])
        env_str = body.get("env", "").strip()

        if not command:
            return {"error": "Command required for stdio server"}

        # Parse args from string if needed
        if isinstance(args, str):
            args = args.split() if args.strip() else []

        # Parse env from "KEY=value,KEY2=value2" format
        env = None
        if env_str:
            env = {}
            for pair in env_str.split(","):
                pair = pair.strip()
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    env[k.strip()] = v.strip()

        config = {
            "type": "stdio",
            "command": command,
            "args": args,
            "env": env,
            "enabled": True,
        }
    elif server_type == "http":
        url = body.get("url", "").strip()
        api_key = body.get("api_key", "").strip()

        if not url:
            return {"error": "URL required for HTTP server"}

        headers = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        config = {
            "type": "http",
            "url": url,
            "headers": headers,
            "enabled": True,
        }
    else:
        return {"error": f"Unknown server type: {server_type}"}

    # Save config — atomic RMW so concurrent add_server calls don't clobber each other
    def _add(srvs):
        srvs = dict(srvs or {})
        srvs[name] = config
        return srvs
    _update_servers(_add)

    # Connect
    try:
        from plugins.mcp_client.daemon import add_and_connect
        add_and_connect(name, config)
        from plugins.mcp_client.daemon import get_server
        server = get_server(name)
        status = server.get("status", "unknown") if server else "unknown"
        tool_count = len(server.get("tools", [])) if server else 0
        return {"status": status, "name": name, "tool_count": tool_count}
    except Exception as e:
        return {"status": "error", "name": name, "error": str(e)}


async def remove_server(**kwargs):
    """DELETE /api/plugin/mcp_client/servers/{name} — disconnect and remove."""
    name = kwargs.get("name", "")
    if not name:
        return {"error": "Server name required"}

    # Disconnect
    try:
        from plugins.mcp_client.daemon import disconnect_and_remove
        disconnect_and_remove(name)
    except Exception as e:
        logger.warning(f"[MCP] Error disconnecting '{name}': {e}")

    # Remove from config — atomic RMW
    def _remove(srvs):
        srvs = dict(srvs or {})
        srvs.pop(name, None)
        return srvs
    _update_servers(_remove)

    return {"status": "removed", "name": name}


async def reconnect_server(**kwargs):
    """POST /api/plugin/mcp_client/servers/{name}/reconnect — re-discover tools."""
    name = kwargs.get("name", "")
    if not name:
        return {"error": "Server name required"}

    try:
        from plugins.mcp_client.daemon import reconnect_server as _reconnect
        _reconnect(name)
        from plugins.mcp_client.daemon import get_server
        server = get_server(name)
        status = server.get("status", "unknown") if server else "error"
        tool_count = len(server.get("tools", [])) if server else 0
        return {"status": status, "name": name, "tool_count": tool_count}
    except Exception as e:
        return {"status": "error", "name": name, "error": str(e)}


async def get_server_tools(**kwargs):
    """GET /api/plugin/mcp_client/servers/{name}/tools — cached tool list."""
    name = kwargs.get("name", "")
    if not name:
        return {"error": "Server name required"}

    from plugins.mcp_client.daemon import get_server
    server = get_server(name)
    if not server:
        return {"tools": [], "error": f"Server '{name}' not found"}

    tools = server.get("tools", [])
    return {"tools": [{"name": t["name"], "description": t.get("description", "")} for t in tools]}
