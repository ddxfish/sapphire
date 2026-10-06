#!/bin/bash
# mcp-persona.sh - call one tool at Sapphire's MCP door as a persona key.
# For testing a key, and for a client whose MCP connection is down.
#
#   tools/mcp-persona.sh                              list the tools this key is offered
#   tools/mcp-persona.sh ding
#   tools/mcp-persona.sh speak '{"text": "Hello."}'
#   tools/mcp-persona.sh memory_recent '{"count": 5}'
#
# The key: $SAPPHIRE_MCP_TOKEN, or the file $SAPPHIRE_MCP_TOKEN_FILE, or
# user/mcp-persona.token. Make one under Settings > System > API Keys.
set -euo pipefail

BASE="${SAPPHIRE_BASE:-https://localhost:8073}"
FILE="${SAPPHIRE_MCP_TOKEN_FILE:-$(dirname "$(dirname "$(realpath "$0")")")/user/mcp-persona.token}"
TOKEN="${SAPPHIRE_MCP_TOKEN:-$(cat "$FILE" 2>/dev/null || true)}"
if [ -z "$TOKEN" ]; then
    echo "ERROR: no API token. Save one to $FILE (Sapphire > Settings > System > API Keys)." >&2
    exit 2
fi

BODY=$(TOOL="${1:-}" ARGS="${2:-}" python3 -c '
import json, os, sys
tool, args = os.environ["TOOL"], os.environ["ARGS"] or "{}"
try:
    args = json.loads(args)
except ValueError as e:
    sys.exit(f"ERROR: the arguments are not JSON: {e}")
call = {"method": "tools/call", "params": {"name": tool, "arguments": args}} if tool else {"method": "tools/list"}
print(json.dumps({"jsonrpc": "2.0", "id": 1, **call}))')

curl -sk --max-time 150 -X POST "$BASE/mcp" -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" -d "$BODY" | python3 -c '
import json, sys
raw = sys.stdin.read()
try:
    d = json.loads(raw)
except ValueError:
    sys.exit("ERROR: no answer from Sapphire: " + (raw.strip()[:200] or "nothing came back"))
if "error" in d or "detail" in d:
    sys.exit("ERROR: " + str((d.get("error") or {}).get("message") or d.get("detail")))
result = d.get("result", {})
if "tools" in result:
    print("\n".join(t["name"] for t in result["tools"]))
    sys.exit(0)
for block in result.get("content", []):
    if block.get("type") == "text":
        print(block.get("text", ""))
sys.exit(1 if result.get("isError") else 0)'
