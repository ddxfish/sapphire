#!/bin/bash
# plugin-toggle.sh — toggle a plugin on/off via API.
# Usage: bash tools/plugin-toggle.sh NAME [on|off]
#   Without second arg: reports current state.
#   With on/off: flips if needed; no-ops if already there.

PLUGIN="$1"
ACTION="$2"
if [ -z "$PLUGIN" ]; then
    echo "Usage: plugin-toggle.sh NAME [on|off]" >&2
    exit 3
fi

BASE="https://localhost:8073"
PASSWORD="${SAPPHIRE_PASSWORD:-changeme}"
COOKIE_JAR=$(mktemp)
trap 'rm -f "$COOKIE_JAR"' EXIT

CSRF=$(curl -sk -c "$COOKIE_JAR" "$BASE/login" | grep -oP 'name="csrf_token"\s+value="\K[^"]+')
if [ -z "$CSRF" ]; then
    echo "could not reach sapphire at $BASE" >&2
    exit 2
fi
curl -sk -b "$COOKIE_JAR" -c "$COOKIE_JAR" -X POST "$BASE/login" \
    -d "password=$PASSWORD&csrf_token=$CSRF" -o /dev/null
# The session rotates after login, so the form token is stale for the PUT:
# re-scrape the post-login token from / (same as ask-sapphire.sh). 2026-10-12.
CSRF=$(curl -sk -b "$COOKIE_JAR" -c "$COOKIE_JAR" "$BASE/" | grep -oP 'name="csrf-token"\s+content="\K[^"]+')
if [ -z "$CSRF" ]; then
    echo "could not fetch the post-login CSRF token (login failed?)" >&2
    exit 2
fi

CURRENT=$(curl -sk -b "$COOKIE_JAR" -H "X-CSRF-Token: $CSRF" "$BASE/api/webui/plugins" \
    | python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(1)
plugins = data if isinstance(data, list) else data.get('plugins', [])
for p in plugins:
    if p.get('name') == '$PLUGIN':
        state = 'on' if p.get('enabled') else 'off'
        loaded = 'loaded' if p.get('loaded') else 'not-loaded'
        print(f'{state} {loaded}')
        sys.exit(0)
print('NOTFOUND')
sys.exit(0)
")

if [ "$CURRENT" = "NOTFOUND" ]; then
    echo "plugin '$PLUGIN' not found" >&2
    exit 4
fi

STATE=$(echo "$CURRENT" | cut -d' ' -f1)
LOADED=$(echo "$CURRENT" | cut -d' ' -f2)

if [ -z "$ACTION" ]; then
    echo "$PLUGIN: $STATE ($LOADED)"
    exit 0
fi

if [ "$STATE" = "$ACTION" ]; then
    echo "$PLUGIN: already $ACTION ($LOADED)"
    exit 0
fi

RESULT=$(curl -sk -b "$COOKIE_JAR" -H "X-CSRF-Token: $CSRF" \
    -X PUT "$BASE/api/webui/plugins/toggle/$PLUGIN")
echo "$RESULT"
