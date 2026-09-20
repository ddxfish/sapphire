# plugins/mcp_client/mcp_bridge.py — MCP protocol bridge
#
# Wraps the mcp SDK for Sapphire. All SDK usage contained here.
# Each connection runs as its own asyncio Task with proper `async with`
# context management to avoid anyio cancel scope leaks.

import asyncio
import logging
import shutil
from typing import Optional

logger = logging.getLogger(__name__)

try:
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
    MCP_AVAILABLE = True
except ImportError:
    MCP_AVAILABLE = False
    logger.warning("mcp SDK not installed. Run: pip install mcp")

try:
    from mcp.client.streamable_http import streamablehttp_client
    HTTP_AVAILABLE = True
except ImportError:
    HTTP_AVAILABLE = False


class MCPBridge:
    """Manages a single MCP server connection.

    Each connection runs as its own asyncio Task. The task holds the
    `async with` context managers open for the connection lifetime,
    keeping anyio's cancel scopes properly contained.
    """

    def __init__(self, config: dict, loop: asyncio.AbstractEventLoop):
        if not MCP_AVAILABLE:
            raise ImportError("mcp SDK not installed. Run: pip install mcp")
        self.config = config
        self.transport_type = config.get("type", "stdio")
        self._loop = loop
        self._session: Optional[ClientSession] = None
        self._connected = False
        self._task: Optional[asyncio.Task] = None
        self._ready = asyncio.Event()
        self._stop = asyncio.Event()
        self._error: Optional[str] = None
        self._tools: list = []

    @property
    def connected(self) -> bool:
        return self._connected

    async def connect(self):
        """Start the connection task and wait for it to be ready."""
        self._ready.clear()
        self._stop.clear()
        self._error = None
        self._task = asyncio.ensure_future(self._run())
        # Wait for connection to establish or fail
        await self._ready.wait()
        if self._error:
            raise RuntimeError(self._error)

    async def _run(self):
        """Long-running task that holds the connection open."""
        try:
            if self.transport_type == "stdio":
                await self._run_stdio()
            elif self.transport_type == "http":
                await self._run_http()
            else:
                self._error = f"Unknown transport: {self.transport_type}"
                self._ready.set()
        except Exception as e:
            if not self._connected:
                self._error = str(e) or repr(e)   # NotImplementedError stringifies to ""
                self._ready.set()
            else:
                logger.error(f"[MCP] Connection task error: {e}")
        finally:
            self._connected = False

    async def _run_stdio(self):
        """stdio connection — held open via async with."""
        command = self.config.get("command", "")
        args = self.config.get("args", [])
        env = self.config.get("env") or None

        if not shutil.which(command):
            raise FileNotFoundError(
                f"Command '{command}' not found. "
                + ("Install Node.js to use npx-based MCP servers." if command == "npx" else "")
            )

        params = StdioServerParameters(command=command, args=args, env=env)

        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                self._session = session
                self._connected = True
                logger.info(f"[MCP] stdio connected: {command} {' '.join(args[:3])}")
                self._ready.set()
                # Hold open until stop requested
                await self._stop.wait()

    async def _run_http(self):
        """HTTP connection — held open via async with."""
        if not HTTP_AVAILABLE:
            raise ImportError("HTTP transport not available — update mcp SDK: pip install -U mcp")

        url = self.config.get("url", "")
        if not url:
            raise ValueError("URL required for HTTP MCP server")

        headers = dict(self.config.get("headers", {}))

        async with streamablehttp_client(url, headers=headers) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                self._session = session
                self._connected = True
                logger.info(f"[MCP] HTTP connected: {url}")
                self._ready.set()
                await self._stop.wait()

    async def disconnect(self):
        """Signal the connection task to stop and wait for cleanup."""
        self._connected = False
        self._session = None
        self._stop.set()
        if self._task and not self._task.done():
            try:
                # Give the task time to exit its async with blocks cleanly
                await asyncio.wait_for(asyncio.shield(self._task), timeout=5)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()
                try:
                    await self._task
                except (asyncio.CancelledError, BaseException):
                    pass
            except BaseException:
                pass
        self._task = None

    async def list_tools(self) -> list:
        """Discover available tools from the MCP server."""
        if not self._session:
            raise RuntimeError("Not connected")
        result = await self._session.list_tools()
        tools = []
        for t in result.tools:
            schema = t.inputSchema if hasattr(t, 'inputSchema') else {}
            if hasattr(schema, 'model_dump'):
                schema = schema.model_dump()
            elif not isinstance(schema, dict):
                schema = {"type": "object", "properties": {}}
            tools.append({
                "name": t.name,
                "description": getattr(t, 'description', '') or '',
                "inputSchema": schema,
            })
        return tools

    async def call_tool(self, tool_name: str, arguments: dict) -> str:
        """Call a tool and return the text result."""
        if not self._session:
            raise RuntimeError("Not connected")
        result = await self._session.call_tool(tool_name, arguments)
        parts = []
        for block in (result.content or []):
            if hasattr(block, 'text'):
                parts.append(block.text)
            elif hasattr(block, 'data'):
                mime = getattr(block, 'mimeType', 'unknown')
                parts.append(f"[binary data: {mime}]")
            else:
                parts.append(str(block))
        return "\n".join(parts) if parts else "(empty result)"

    def call_tool_sync(self, tool_name: str, arguments: dict, timeout: float = 30) -> str:
        """Synchronous tool call — schedules on the daemon event loop."""
        future = asyncio.run_coroutine_threadsafe(
            self.call_tool(tool_name, arguments), self._loop
        )
        return future.result(timeout=timeout)
