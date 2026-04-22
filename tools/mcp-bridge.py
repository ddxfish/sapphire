#!/usr/bin/env python3
"""stdio-to-HTTP MCP bridge for claude-code-persona.

Claude Code spawns this as a stdio MCP server. It translates JSON-RPC from
stdin/stdout to HTTP POST against the local Sapphire (self-signed TLS OK on
loopback). Bearer key is read from the plugin state file — no config plumbing.

Claude Code MCP config:
    {
      "mcpServers": {
        "sapphire-memory": {
          "command": "python3",
          "args": ["/path/to/sapphire/tools/mcp-bridge.py"]
        }
      }
    }
"""
import json
import os
import ssl
import sys
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).resolve().parent.parent
KEY_FILE = PROJECT_ROOT / 'user' / 'plugin_state' / 'claude-code-persona_mcp_key.json'
BASE = os.environ.get('SAPPHIRE_BASE', 'https://localhost:8073')
ENDPOINT = f'{BASE}/api/plugin/claude-code-persona/mcp'

# TLS verification policy: we ship with verify-off because Sapphire's default
# cert is self-signed and the expected target is loopback. If SAPPHIRE_BASE
# points at a remote host, refuse to bypass cert verification — that'd be a
# silent MITM window for anyone who can intercept the path. Force the user to
# either use a cert a browser would trust, or keep it on localhost.
_host = (urlparse(BASE).hostname or '').lower()
_LOOPBACK = {'localhost', '127.0.0.1', '::1'}
if _host in _LOOPBACK:
    _ssl_ctx = ssl.create_default_context()
    _ssl_ctx.check_hostname = False
    _ssl_ctx.verify_mode = ssl.CERT_NONE
else:
    # Remote target → verify normally. Self-signed cert on a remote host will
    # fail loudly, which is the correct behavior.
    _ssl_ctx = ssl.create_default_context()


def _read_key() -> str:
    if not KEY_FILE.exists():
        return ''
    try:
        return json.loads(KEY_FILE.read_text()).get('key', '') or ''
    except Exception:
        return ''


def _forward(payload: dict) -> dict | None:
    """POST a JSON-RPC message to Sapphire. Return parsed response dict, or
    None if it was a notification (server returned 202 / no body)."""
    key = _read_key()
    if not key:
        return {
            'jsonrpc': '2.0',
            'id': payload.get('id'),
            'error': {
                'code': -32001,
                'message': (
                    f'MCP key not found at {KEY_FILE}. '
                    'Enable the claude-code-persona plugin in Sapphire to generate one.'
                ),
            },
        }

    body = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(
        ENDPOINT,
        data=body,
        method='POST',
        headers={
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {key}',
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30, context=_ssl_ctx) as resp:
            status = resp.status
            raw = resp.read()
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            err_body = json.loads(raw)
        except Exception:
            err_body = {'message': raw.decode('utf-8', errors='replace')}
        return {
            'jsonrpc': '2.0',
            'id': payload.get('id'),
            'error': {
                'code': -32000,
                'message': f'HTTP {e.code}: {err_body}',
            },
        }
    except Exception as e:
        return {
            'jsonrpc': '2.0',
            'id': payload.get('id'),
            'error': {'code': -32000, 'message': f'Sapphire unreachable: {e}'},
        }

    if status == 202 or not raw:
        # Notification — no response body expected
        return None
    try:
        return json.loads(raw)
    except Exception as e:
        return {
            'jsonrpc': '2.0',
            'id': payload.get('id'),
            'error': {'code': -32700, 'message': f'Bad JSON from Sapphire: {e}'},
        }


def main():
    """Read JSON-RPC messages line-by-line from stdin, forward, write responses
    to stdout. stdio MCP framing is newline-delimited JSON (one message per line)."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception as e:
            sys.stdout.write(json.dumps({
                'jsonrpc': '2.0',
                'id': None,
                'error': {'code': -32700, 'message': f'Parse error: {e}'},
            }) + '\n')
            sys.stdout.flush()
            continue

        result = _forward(msg)
        if result is not None:
            sys.stdout.write(json.dumps(result) + '\n')
            sys.stdout.flush()


if __name__ == '__main__':
    main()
