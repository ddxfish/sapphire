#!/bin/bash
# chat-dump.sh — dump last N messages from a chat as plain text.
# Usage: bash tools/chat-dump.sh [CHAT_NAME] [N]
#   Default chat: trinity
#   Default N: 10

CHAT="${1:-trinity}"
N="${2:-10}"
DB="$(cd "$(dirname "$0")/.." && pwd)/user/history/sapphire_history.db"

if [ ! -f "$DB" ]; then
    echo "chat DB not found: $DB" >&2
    exit 2
fi

MESSAGES_JSON=$(sqlite3 "$DB" "SELECT messages FROM chats WHERE name = '$CHAT'")
if [ -z "$MESSAGES_JSON" ]; then
    echo "chat '$CHAT' not found (or empty)" >&2
    exit 3
fi

echo "$MESSAGES_JSON" | python3 -c "
import json, sys
data = sys.stdin.read().strip()
try:
    messages = json.loads(data)
except Exception as e:
    print(f'parse failed: {e}', file=sys.stderr)
    sys.exit(4)

if not isinstance(messages, list):
    print('unexpected messages shape', file=sys.stderr)
    sys.exit(5)

last = messages[-$N:]
for m in last:
    role = m.get('role', '?')
    content = m.get('content', '') or ''
    ts = m.get('timestamp', '')
    if m.get('tool_calls'):
        tcs = [tc.get('function', {}).get('name', '?') for tc in m['tool_calls']]
        preview = f'[tool_calls: {\", \".join(tcs)}]'
        if content:
            preview += ' ' + content[:200]
        content = preview
    elif role == 'tool':
        content = f'[result for {m.get(\"name\", \"?\")}] ' + content[:300]
    else:
        content = content[:500]
    print(f'--- {role} @ {ts} ---')
    print(content)
    print()
"
