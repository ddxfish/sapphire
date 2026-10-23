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

# Always fresh login — CSRF token must come from the same session.
# Two-stage CSRF: the login form has its own token; after login the session
# rotates (session-fixation fix 2026-04-22 M5), so we re-scrape a fresh
# CSRF from the main page's <meta name="csrf-token"> for subsequent requests.
rm -f "$COOKIE_JAR"
LOGIN_CSRF=$(curl -sk -c "$COOKIE_JAR" "$BASE/login" | grep -oP 'name="csrf_token"\s+value="\K[^"]+')
curl -sk -b "$COOKIE_JAR" -c "$COOKIE_JAR" -X POST "$BASE/login" \
    -d "password=$PASSWORD&csrf_token=$LOGIN_CSRF" -o /dev/null
CSRF=$(curl -sk -b "$COOKIE_JAR" -c "$COOKIE_JAR" "$BASE/" | grep -oP 'name="csrf-token"\s+content="\K[^"]+')
if [ -z "$CSRF" ]; then
    echo "ERROR: could not fetch post-login CSRF token from / — login may have failed" >&2
    exit 4
fi

# Fetch the target chat's configured settings so the continuity task inherits
# the chat's persona + scopes + toolset. We REFUSE to run if settings can't
# be fetched — silent fallback to 'sapphire'/'default' means the user thinks
# they're talking to one persona in one scope but lands somewhere else. A
# chat whose settings can't be read is an error state to surface, not mask.
CHAT_SETTINGS_TMP=$(mktemp)
HTTP_STATUS=$(curl -sk -b "$COOKIE_JAR" -H "X-CSRF-Token: $CSRF" \
    -w "%{http_code}" -o "$CHAT_SETTINGS_TMP" \
    "$BASE/api/chats/$CHAT/settings" 2>/dev/null || echo "000")
CHAT_SETTINGS_RAW=$(cat "$CHAT_SETTINGS_TMP")
rm -f "$CHAT_SETTINGS_TMP"

if [ "$HTTP_STATUS" != "200" ]; then
    echo "ERROR: Cannot fetch settings for chat '$CHAT' (HTTP $HTTP_STATUS)." >&2
    echo "       Response: $CHAT_SETTINGS_RAW" >&2
    echo "       Refusing to send — silent fallback to 'sapphire'/'default'" >&2
    echo "       would route this to the wrong persona and scope." >&2
    echo "       Fix: create the chat in the Web UI first, or pick an existing one." >&2
    exit 2
fi

# Build the task body in Python. The chat's settings are authoritative —
# we only fall back to 'all' for toolset (UI convention, not a scope/persona
# hard-default).
TASK_BODY=$(CHAT_RAW="$CHAT_SETTINGS_RAW" CHAT_NAME="$CHAT" MSG="$FULL_MESSAGE" python3 <<'PY'
import json, os, sys
try:
    raw = json.loads(os.environ.get('CHAT_RAW') or '{}')
except json.JSONDecodeError as e:
    print(f"ERROR: chat settings response is not valid JSON: {e}", file=sys.stderr)
    sys.exit(3)
s = raw.get('settings', {}) if isinstance(raw, dict) else {}
if not s:
    print("ERROR: chat settings response has no 'settings' key. Response:",
          json.dumps(raw)[:200], file=sys.stderr)
    sys.exit(3)
body = {
    "name": "claude-code-msg",
    "type": "task",
    "enabled": True,
    "schedule": "0 0 31 2 *",
    # Auto-delete on completion + cap runs. Without this, a SIGKILL / timeout
    # / Ctrl-C between POST /tasks and DELETE leaves the task stranded in
    # tasks.json. After 25 strands the scheduler's MAX_TASKS cap blocks new
    # task creation — trinity bridge dies with 400s. Scout longevity #2
    # (2026-04-20).
    "delete_after_run": True,
    "max_runs": 1,
    # ASK_TOOLSET=<name> overrides the chat's toolset for ONE message (dev:
    # proving a new tool before the user toolsets learn its name). 2026-09-10.
    "toolset": os.environ.get('ASK_TOOLSET') or s.get('toolset') or s.get('ability') or 'all',
    "prompt": s.get('persona') or s.get('prompt') or 'sapphire',
    # Forward chat's bound LLM provider/model. Without this, provider defaults
    # to 'auto' → first available → wrong model from global fallback order
    # (same silent-default class as the 2026-04-19 fix). 2026-05-15.
    "provider": s.get('llm_primary') or 'auto',
    "model": s.get('llm_model') or '',
    "chat_target": os.environ['CHAT_NAME'],
    "initial_message": os.environ['MSG'],
    "tts_enabled": False,
    "memory_scope": s.get('memory_scope') or 'default',
    "knowledge_scope": s.get('knowledge_scope') or 'default',
    "people_scope": s.get('people_scope') or 'default',
    "goal_scope": s.get('goal_scope') or 'default',
}
print(json.dumps(body))
PY
) || exit 3

# Create one-shot task
TASK_RESULT=$(curl -sk -b "$COOKIE_JAR" -H "X-CSRF-Token: $CSRF" \
    -H "Content-Type: application/json" \
    -X POST "$BASE/api/continuity/tasks" \
    -d "$TASK_BODY")

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
