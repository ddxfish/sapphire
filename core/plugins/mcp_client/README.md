# MCP Client

Connect Sapphire to external tools via the [Model Context Protocol](https://modelcontextprotocol.io). MCP servers expose tools that Sapphire's AI can use — file systems, databases, APIs, and thousands of community-built integrations.

## Quick Start

1. Enable the MCP Client plugin in **Settings**
2. Go to **Settings > Plugins > MCP Client**
3. Click **+ Add Local** or **+ Add Remote**
4. Once connected, MCP tools appear in your **Toolsets** view
5. Enable the tools you want in your active toolset

## Test It First

A built-in test server is included so you can verify MCP works before connecting real servers. No Node.js or external dependencies needed.

1. In MCP Client settings, click **+ Add Local (stdio)**
2. Enter:
   - **Name**: `test`
   - **Command**: `python`
   - **Args**: `core/plugins/mcp_client/test_server.py`
3. Click **Connect**

You should see "3 tools" — `add`, `mcp_clock`, and `reverse_text`. Go to **Toolsets**, find the `mcp:test` module, enable the tools, then try:

- "What time is it?" (uses `mcp_clock`)
- "Add 42 and 58" (uses `add`)
- "Reverse the word sapphire" (uses `reverse_text`)

Remove the test server when you're done — it's just for verification.

## Local Servers (stdio)

Local MCP servers run as subprocesses on your machine. Sapphire spawns the process, talks to it over stdin/stdout — you don't run it yourself.

### Example: Filesystem Server

```
Command: npx
Args: -y @modelcontextprotocol/server-filesystem /home/user/documents
```

This gives the AI tools to read, write, and search files in the specified directory.

### Example: GitHub Server

The [official GitHub MCP server](https://github.com/github/github-mcp-server) gives the AI access to issues, PRs, repos, code search, and more. Requires Docker:

```
Command: docker
Args: run -i --rm -e GITHUB_PERSONAL_ACCESS_TOKEN ghcr.io/github/github-mcp-server
Env: GITHUB_PERSONAL_ACCESS_TOKEN=ghp_your_token_here
```

Get a token at [github.com/settings/tokens](https://github.com/settings/tokens) — enable the `repo` scope for full access, or `public_repo` for public repos only.

### Requirements

Local servers that use `npx` require [Node.js](https://nodejs.org) to be installed. Python-based MCP servers work with your existing Python environment.

## Remote Servers (HTTP)

Remote MCP servers are hosted by third parties (Canva, Slack, Notion, etc.). Enter the URL and optional API key.

```
URL: https://mcp.example.com
API Key: your-key (if required)
```

Many remote servers use OAuth for authentication — support for OAuth flows is planned for a future update.

## How It Works

MCP tools register alongside native Sapphire tools. The AI doesn't know or care whether a tool is local or from an MCP server — it just uses them.

```
MCP Server advertises tools
  > Sapphire discovers and registers them
  > AI sees them in its tool list
  > AI calls a tool
  > Sapphire proxies the call to the MCP server
  > Result returned to AI
```

## Managing Tools

Connected MCP servers appear as collapsible modules in the **Toolsets** view:

- Toggle individual tools on/off
- Module header shows server name and tool count
- Works with custom toolsets — include only the MCP tools you want
- Tools with names that conflict with existing Sapphire tools are skipped (warning logged)

## Server Management

Each server in the settings shows its status:

- Green dot — connected and working
- Red dot — error or disconnected

Use the **reconnect button** to re-discover tools after a server update, or if a connection drops. Use the **remove button** to disconnect and delete a server config.

## Troubleshooting

- **"Command not found"** — Install Node.js for npx-based servers, or check the command path
- **Server shows red/error** — Click the reconnect button to retry. Check the Sapphire logs for details
- **Tools not appearing in chat** — Go to Toolsets, find the `mcp:servername` module, and enable the tools
- **Fewer tools than expected** — A tool name may conflict with an existing Sapphire tool. Check logs for "conflicts with" warnings
- **Slow tool calls** — MCP calls have a 30-second timeout. Some servers need warmup time on first call
- **High CPU** — If a server subprocess misbehaves, remove it in settings. The subprocess will be cleaned up

## Popular MCP Servers

Browse available servers at [mcp.so](https://mcp.so) or [smithery.ai](https://smithery.ai). Thousands of community-built servers available for databases, APIs, cloud services, and more.

### Common Servers

| Server | Type | What it does |
|--------|------|-------------|
| Filesystem | stdio (npx) | Read, write, search files in a directory |
| GitHub | stdio (docker) | Issues, PRs, repos, code search |
| PostgreSQL | stdio (npx) | Query databases, inspect schemas |
| Brave Search | stdio (npx) | Web search |
| Fetch | stdio (npx) | HTTP requests, web scraping |
| Canva | HTTP (remote) | Design creation and management |
| Slack | HTTP (remote) | Channel messaging, search |
| Notion | Both | Page and database operations |
