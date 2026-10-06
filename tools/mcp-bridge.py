#!/usr/bin/env python3
"""stdio MCP bridge to Sapphire's MCP door (POST /mcp).

Claude Code spawns this as a stdio MCP server and it carries each call to the
local Sapphire over HTTPS (her self-signed cert is fine on loopback). Who the
client is comes from the API token: one that speaks as a persona gets that
persona's voice (speak, ding, listen) and memory beside ask and tell.

The connection outlives her restarts. The handshake is answered here when she
is away, the last good tool list is kept on disk and served meanwhile, and a
call she cannot take comes back as a tool error to retry, not a dead server.

Setup:
  1. Sapphire: Settings > MCP Server on. Settings > System > API Keys: add a
     key and pick the persona it speaks as. Save the token to
     user/mcp-persona.token.
  2. The client's MCP config (Claude Code shown; any stdio MCP client works):
       "mcpServers": {"sapphire": {"command": "python3",
                                   "args": ["/path/to/sapphire/tools/mcp-bridge.py"]}}

Two clients on one machine each need a key of their own: give each its token
in its config, as SAPPHIRE_MCP_TOKEN or as a file named by SAPPHIRE_MCP_TOKEN_FILE.
"""
import json
import os
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TOKEN_FILE = Path(os.environ.get('SAPPHIRE_MCP_TOKEN_FILE') or PROJECT_ROOT / 'user' / 'mcp-persona.token')
TOOLS_FILE = TOKEN_FILE.with_suffix('.tools.json')                # the last tool list she gave this key
BASE = os.environ.get('SAPPHIRE_BASE', 'https://localhost:8073').rstrip('/')
ENDPOINT = f'{BASE}/mcp'
PROTOCOL = '2025-06-18'
QUICK, LONG = 5, 150           # seconds: the handshake and lists; a call (speak plays to its end, listen waits on a person)
AWAY = "Sapphire is not answering right now (down or restarting). Try again in a few seconds."

# Loopback: her default cert is self-signed, so it is not verified. Any other
# host is verified normally and a self-signed cert there fails loudly, which
# is right: skipping it would hand the token to whoever sits on the path.
_ctx = ssl.create_default_context()
if (urlparse(BASE).hostname or '').lower() in ('localhost', '127.0.0.1', '::1'):
    _ctx.check_hostname = False
    _ctx.verify_mode = ssl.CERT_NONE


def _token():
    given = os.environ.get('SAPPHIRE_MCP_TOKEN', '').strip()
    if given:
        return given
    try:
        return TOKEN_FILE.read_text(encoding='utf-8').strip()
    except OSError:
        return ''


def _post(message, timeout):
    """One JSON-RPC message to her door. Returns (answer, ''), or (None, why)
    when she could not be reached or refused the token."""
    token = _token()
    if not token:
        return None, (f"No API token: save one to {TOKEN_FILE} (Sapphire > Settings > System > API Keys, "
                      "with the persona it speaks as).")
    request = urllib.request.Request(
        ENDPOINT, data=json.dumps(message).encode('utf-8'), method='POST',
        headers={'Content-Type': 'application/json', 'Authorization': f'Bearer {token}'})
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=_ctx) as response:
            raw = response.read()
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return None, "Sapphire refused the API token (revoked or mistyped)."
        if e.code == 404:
            return None, "Sapphire's MCP server is off. Turn it on under Settings > MCP Server."
        return None, f"Sapphire answered HTTP {e.code}."
    except Exception:
        return None, AWAY
    try:
        return (json.loads(raw) if raw else None), ''
    except ValueError:
        return None, "Sapphire sent something that is not JSON."


def _result(ident, value):
    return {'jsonrpc': '2.0', 'id': ident, 'result': value}


def _kept_tools():
    try:
        tools = json.loads(TOOLS_FILE.read_text(encoding='utf-8'))
        return tools if isinstance(tools, list) else []
    except (OSError, ValueError):
        return []


def _keep_tools(tools):
    try:
        tmp = TOOLS_FILE.with_suffix('.tmp')
        tmp.write_text(json.dumps(tools), encoding='utf-8')
        os.replace(tmp, TOOLS_FILE)
    except OSError:
        pass


def handle(message):
    """One message from Claude Code in, one answer out, or None when nothing is owed."""
    method, ident = message.get('method', ''), message.get('id')
    if ident is None or method.startswith('notifications/'):
        return None                                     # her door keeps no session: nothing to pass on
    if method == 'ping':
        return _result(ident, {})
    if method == 'initialize':
        answer, why = _post(message, QUICK)
        if answer and 'result' in answer:
            return answer
        return _result(ident, {
            'protocolVersion': PROTOCOL,
            'capabilities': {'tools': {'listChanged': False}},
            'serverInfo': {'name': 'Sapphire', 'version': 'away'},
            'instructions': f"Sapphire's MCP door. {why} Her tools work again once she answers.",
        })
    if method == 'tools/list':
        answer, _why = _post(message, QUICK)
        tools = (answer or {}).get('result', {}).get('tools')
        if isinstance(tools, list):
            _keep_tools(tools)
            return answer
        return _result(ident, {'tools': _kept_tools()})
    if method == 'tools/call':
        answer, why = _post(message, LONG)
        if answer is not None:
            return answer
        return _result(ident, {'content': [{'type': 'text', 'text': why}], 'isError': True})
    return {'jsonrpc': '2.0', 'id': ident, 'error': {'code': -32601, 'message': f'Method not found: {method}'}}


def main():
    """stdio MCP framing: one JSON message per line, each way."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
            answer = handle(message) if isinstance(message, dict) else None
        except ValueError as e:
            answer = {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32700, 'message': f'Parse error: {e}'}}
        if answer is not None:
            sys.stdout.write(json.dumps(answer) + '\n')
            sys.stdout.flush()


if __name__ == '__main__':
    main()
