#!/bin/bash
# claude-memory.sh — friction-free memory access for Claude Code sessions.
# Wraps the MCP endpoint at /api/plugin/claude-code-persona/mcp. Reads the
# bearer key from disk. Designed for one-line use.
#
# Usage:
#   tools/claude-memory.sh save "memory content" [label]
#   tools/claude-memory.sh recent [count] [label]
#   tools/claude-memory.sh search "query" [limit] [label]
#   tools/claude-memory.sh delete <id>
set -euo pipefail

BASE="${SAPPHIRE_BASE:-https://localhost:8073}"
KEY_FILE="$(dirname "$(dirname "$(realpath "$0")")")/user/plugin_state/claude-code-persona_mcp_key.json"

if [ ! -f "$KEY_FILE" ]; then
    echo "ERROR: MCP key file not found at $KEY_FILE" >&2
    echo "       Enable the claude-code-persona plugin in Sapphire Settings." >&2
    exit 2
fi

KEY=$(python3 -c "import json,sys; print(json.load(open('$KEY_FILE'))['key'])")

CMD="${1:-}"
if [ -z "$CMD" ]; then
    echo "Usage: $(basename "$0") <save|recent|search|delete> [args...]" >&2
    echo "  save 'content' [label]    — save a memory" >&2
    echo "  recent [count] [label]    — list recent entries (default 10)" >&2
    echo "  search 'query' [limit] [label] — semantic+FTS search" >&2
    echo "  delete <id>               — remove an entry" >&2
    exit 1
fi

# Build JSON-RPC tools/call body in Python for clean escaping.
BODY=$(CMD="$CMD" ARG1="${2:-}" ARG2="${3:-}" ARG3="${4:-}" python3 <<'PY'
import json, os
cmd = os.environ['CMD']
a1, a2, a3 = os.environ['ARG1'], os.environ['ARG2'], os.environ['ARG3']
if cmd == 'save':
    args = {'content': a1}
    if a2: args['label'] = a2
    name = 'save_claude_memory'
elif cmd == 'recent':
    args = {}
    if a1: args['count'] = int(a1)
    if a2: args['label'] = a2
    name = 'get_recent_claude_memories'
elif cmd == 'search':
    args = {'query': a1}
    if a2: args['limit'] = int(a2)
    if a3: args['label'] = a3
    name = 'search_claude_memory'
elif cmd == 'delete':
    if not a1:
        raise SystemExit("ERROR: delete requires an id")
    args = {'memory_id': int(a1)}
    name = 'delete_claude_memory'
else:
    raise SystemExit(f"Unknown command: {cmd}")
print(json.dumps({
    'jsonrpc': '2.0', 'id': 1,
    'method': 'tools/call',
    'params': {'name': name, 'arguments': args}
}))
PY
)

# Fire the request, extract the text content (one memory per call returns
# a single text block; show it plainly for easy reading).
curl -sk -X POST "$BASE/api/plugin/claude-code-persona/mcp" \
    -H "Authorization: Bearer $KEY" \
    -H "Content-Type: application/json" \
    -d "$BODY" | python3 -c "
import sys, json
d = json.load(sys.stdin)
if 'error' in d:
    print('ERROR:', d['error'].get('message','?'), file=sys.stderr)
    sys.exit(1)
result = d.get('result', {})
for block in result.get('content', []):
    if block.get('type') == 'text':
        print(block.get('text',''))
sys.exit(1 if result.get('isError') else 0)
"
