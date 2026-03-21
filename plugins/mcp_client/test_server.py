"""Tiny MCP test server — run via stdio for testing the MCP client plugin.

Usage in Sapphire MCP settings:
  Command: python
  Args: tools/test_mcp_server.py
"""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("Test Server")


@mcp.tool()
def add(a: int, b: int) -> int:
    """Add two numbers together."""
    return a + b


@mcp.tool()
def mcp_clock() -> str:
    """Get the current date and time."""
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@mcp.tool()
def reverse_text(text: str) -> str:
    """Reverse a string of text."""
    return text[::-1]


if __name__ == "__main__":
    mcp.run(transport="stdio")
