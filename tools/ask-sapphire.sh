#!/bin/bash
# ask-sapphire.sh — Claude Code talks to Sapphire
# Usage: tools/ask-sapphire.sh "Your message here" [chat_name]
# Default chat: trinity
set -euo pipefail

BASE="https://localhost:8073"
PASSWORD="${SAPPHIRE_PASSWORD:-changeme}"
MESSAGE="$1"
CHAT="${2:-trinity}"
COOKIE_JAR="/tmp/sapphire-claude-cookies.txt"

if [ -z "${MESSAGE:-}" ]; then
    echo "Usage: ask-sapphire.sh \"message\" [chat_name]"
    echo "  Default chat: trinity"
    exit 1
fi

# Prepend header so Sapphire knows this is Claude Code, not the user typing
FULL_MESSAGE="[Claude Code via terminal — not the user]
---
$MESSAGE"

# Always fresh login — CSRF token must come from the same session
rm -f "$COOKIE_JAR"
CSRF=$(curl -sk -c "$COOKIE_JAR" "$BASE/login" | grep -oP 'name="csrf_token"\s+value="\K[^"]+')
curl -sk -b "$COOKIE_JAR" -c "$COOKIE_JAR" -X POST "$BASE/login" \
    -d "password=$PASSWORD&csrf_token=$CSRF" -o /dev/null

# Escape message for JSON
ESCAPED_MSG=$(python3 -c "import json,sys; print(json.dumps(sys.argv[1]))" "$FULL_MESSAGE")

# Create one-shot task
TASK_RESULT=$(curl -sk -b "$COOKIE_JAR" -H "X-CSRF-Token: $CSRF" \
    -H "Content-Type: application/json" \
    -X POST "$BASE/api/continuity/tasks" \
    -d "{
        \"name\": \"claude-code-msg\",
        \"type\": \"task\",
        \"enabled\": true,
        \"schedule\": \"0 0 31 2 *\",
        \"toolset\": \"all\",
        \"prompt\": \"sapphire\",
        \"chat_target\": \"$CHAT\",
        \"initial_message\": $ESCAPED_MSG,
        \"tts_enabled\": false,
        \"memory_scope\": \"default\",
        \"knowledge_scope\": \"default\",
        \"people_scope\": \"default\",
        \"goal_scope\": \"default\"
    }")

TASK_ID=$(echo "$TASK_RESULT" | python3 -c "
import sys, json
d = json.load(sys.stdin)
tid = d.get('task_id', {})
print(tid.get('id', '') if isinstance(tid, dict) else tid)
")

if [ -z "$TASK_ID" ]; then
    echo "Failed to create task"
    exit 1
fi

# Run — blocks until Sapphire responds
RAW=$(curl -sk -b "$COOKIE_JAR" -H "X-CSRF-Token: $CSRF" \
    -X POST "$BASE/api/continuity/tasks/$TASK_ID/run" --max-time 180)

# Extract and display her response
python3 -c "
import sys, json, re

raw = sys.stdin.read()
try:
    d = json.loads(raw)
except json.JSONDecodeError:
    print('(could not parse response)')
    sys.exit(0)

for r in d.get('responses', []):
    text = r.get('output', r.get('response', ''))
    if text:
        text = re.sub(r'<think>.*?</think>\s*', '', text, flags=re.DOTALL)
        text = re.sub(r'<<[^>]+>>\s*', '', text)
        cleaned = text.strip()
        if cleaned:
            print(cleaned)
" <<< "$RAW"

# Cleanup task (conversation persists in the chat)
curl -sk -b "$COOKIE_JAR" -H "X-CSRF-Token: $CSRF" \
    -X DELETE "$BASE/api/continuity/tasks/$TASK_ID" -o /dev/null
rm -f "$COOKIE_JAR"
