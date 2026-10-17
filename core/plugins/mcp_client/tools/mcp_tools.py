# core/plugins/mcp_client/tools/mcp_tools.py — MCP tool call proxy
#
# execute() routes tool calls to the correct MCP server.
# TOOLS list is empty — tools are registered dynamically by the daemon.

import logging

logger = logging.getLogger(__name__)

ENABLED = True
EMOJI = '\U0001F50C'
TOOLS = []  # Dynamic — daemon registers tools via FunctionManager
AVAILABLE_FUNCTIONS = []  # Dynamic


def execute(function_name, arguments, config):
    """Proxy a tool call to the MCP server that owns it."""
    from plugins.mcp_client.daemon import get_tool_server, get_server

    server_name = get_tool_server(function_name)
    if not server_name:
        return f"MCP tool '{function_name}' not found — server may have disconnected", False

    server = get_server(server_name)
    if not server or not server.get("bridge"):
        return f"MCP server '{server_name}' is not connected", False

    bridge = server["bridge"]
    if not bridge.connected:
        return f"MCP server '{server_name}' is disconnected", False

    try:
        result = bridge.call_tool_sync(function_name, arguments or {})
        return result, True
    except TimeoutError:
        return f"MCP tool '{function_name}' timed out after 30s", False
    except Exception as e:
        logger.error(f"[MCP] Tool call failed: {function_name} on {server_name}: {e}")
        return f"MCP tool error: {e}", False
