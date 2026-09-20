# plugins/mcp_client/daemon.py — MCP server lifecycle manager
#
# Connects to configured MCP servers, discovers tools, registers them
# with FunctionManager. Holds connections open for tool call proxying.

import asyncio
import logging
import sys
import threading

logger = logging.getLogger(__name__)

# Module state
_loop: asyncio.AbstractEventLoop = None
_thread: threading.Thread = None
_servers: dict = {}       # {name: {config, bridge, tools, status}}
_tool_map: dict = {}      # {tool_name: server_name} — for routing calls
_plugin_loader = None
_stop_event = threading.Event()


def start(plugin_loader, settings):
    """Called by plugin_loader. Connect to all configured MCP servers."""
    global _loop, _thread, _plugin_loader

    _plugin_loader = plugin_loader
    server_configs = settings.get("servers", {})
    if not server_configs:
        logger.info("[MCP] No servers configured — daemon idle")
        return

    _stop_event.clear()
    # Windows: sapphire.py pins the Selector policy process-wide (Proactor's
    # socket-cleanup noise), but Selector loops have NO asyncio subprocess
    # support on Windows — stdio_client raised NotImplementedError for every
    # npx/uvx server. A Proactor loop in this daemon's own thread needs no
    # signal handling or child watcher; the global policy stays untouched.
    _loop = asyncio.ProactorEventLoop() if sys.platform == 'win32' else asyncio.new_event_loop()
    _thread = threading.Thread(target=_run_loop, args=(server_configs,), daemon=True, name="mcp-daemon")
    _thread.start()
    logger.info("[MCP] Daemon thread started")


def stop():
    """Disconnect all MCP servers."""
    global _loop, _thread
    _stop_event.set()

    if _loop and _loop.is_running():
        _loop.call_soon_threadsafe(_loop.stop)

    if _thread and _thread.is_alive():
        _thread.join(timeout=5)

    _loop = None
    _thread = None
    logger.info("[MCP] Daemon stopped")


# -- Accessors for routes/tools --

def get_server(name):
    return _servers.get(name)

def get_all_servers():
    return dict(_servers)

def get_tool_server(tool_name):
    return _tool_map.get(tool_name)

def get_loop():
    return _loop


# -- Internal --

def _run_loop(server_configs):
    """Main daemon thread — single long-running coroutine."""
    asyncio.set_event_loop(_loop)

    async def _main():
        # Connect all configured servers
        for name, config in server_configs.items():
            if _stop_event.is_set():
                return
            if config.get("enabled", True):
                await _connect_server(name, config)

        # Hold the loop open — stop_event triggers exit
        while not _stop_event.is_set():
            await asyncio.sleep(1)

        # Clean up
        for name in list(_servers):
            await _disconnect_server(name)

    try:
        _loop.run_until_complete(_main())
    except BaseException as e:
        if not _stop_event.is_set():
            logger.error(f"[MCP] Daemon loop error: {e}")
    finally:
        # Final cleanup for any remaining async generators
        try:
            _loop.run_until_complete(_loop.shutdown_asyncgens())
        except BaseException:
            pass


async def _connect_server(name, config):
    """Connect to one MCP server, discover tools, register them."""
    from plugins.mcp_client.mcp_bridge import MCPBridge

    try:
        bridge = MCPBridge(config, _loop)
        await bridge.connect()
        tools = await bridge.list_tools()

        _servers[name] = {
            "config": config,
            "bridge": bridge,
            "tools": tools,
            "status": "connected",
        }
        _register_tools(name, tools)
        logger.info(f"[MCP] Connected: {name} ({len(tools)} tools)")

    except FileNotFoundError as e:
        _servers[name] = {"config": config, "bridge": None, "tools": [], "status": str(e)}
        logger.error(f"[MCP] {name}: {e}")
    except Exception as e:
        _servers[name] = {"config": config, "bridge": None, "tools": [], "status": f"error: {e}"}
        logger.error(f"[MCP] Failed to connect '{name}': {e}")


async def _disconnect_server(name):
    """Disconnect one server, unregister its tools."""
    server = _servers.pop(name, None)
    if server and server.get("bridge"):
        try:
            await server["bridge"].disconnect()
        except BaseException:
            pass
    _unregister_tools(name)


def _register_tools(name, mcp_tools):
    """Convert MCP tool defs to Sapphire format, register with FunctionManager."""
    fm = _plugin_loader._function_manager if _plugin_loader else None
    if not fm:
        logger.warning(f"[MCP] No FunctionManager — cannot register tools for {name}")
        return

    module_name = f"mcp:{name}"
    sapphire_tools = []
    for t in mcp_tools:
        sapphire_tools.append({
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("inputSchema", {"type": "object", "properties": {}}),
            }
        })
        _tool_map[t["name"]] = name

    from plugins.mcp_client.tools.mcp_tools import execute
    fm.register_dynamic_tools(module_name, sapphire_tools, execute, plugin_name="mcp_client", emoji="\U0001F50C")


def _unregister_tools(name):
    """Remove a server's tools from FunctionManager."""
    module_name = f"mcp:{name}"
    fm = _plugin_loader._function_manager if _plugin_loader else None
    if fm:
        fm.unregister_dynamic_tools(module_name)

    to_remove = [t for t, s in _tool_map.items() if s == name]
    for t in to_remove:
        _tool_map.pop(t, None)


async def reconnect_server_async(name):
    """Disconnect + reconnect + re-discover tools for one server."""
    old = _servers.get(name)
    config = old["config"] if old else None
    if not config:
        settings = _get_settings()
        config = settings.get("servers", {}).get(name)
    if not config:
        logger.error(f"[MCP] Cannot reconnect '{name}' — no config found")
        return
    await _disconnect_server(name)
    await _connect_server(name, config)


def reconnect_server(name):
    """Sync wrapper for reconnect."""
    if _loop and _loop.is_running():
        future = asyncio.run_coroutine_threadsafe(reconnect_server_async(name), _loop)
        future.result(timeout=30)


def add_and_connect(name, config):
    """Add a new server and connect immediately."""
    if _loop and _loop.is_running():
        future = asyncio.run_coroutine_threadsafe(_connect_server(name, config), _loop)
        future.result(timeout=30)
    else:
        start(_plugin_loader, {"servers": {name: config}})


def disconnect_and_remove(name):
    """Disconnect and remove a server."""
    if _loop and _loop.is_running():
        future = asyncio.run_coroutine_threadsafe(_disconnect_server(name), _loop)
        future.result(timeout=10)
    else:
        _servers.pop(name, None)
        _unregister_tools(name)


def _get_settings():
    if _plugin_loader:
        return _plugin_loader.get_plugin_settings("mcp_client") or {}
    return {}
