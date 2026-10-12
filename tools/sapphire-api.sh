#!/bin/bash
# sapphire-api.sh — authenticated curl to the local Sapphire API.
# Usage:
#   tools/sapphire-api.sh GET  /api/chats
#   tools/sapphire-api.sh GET  /api/chats/lookout/settings
#   tools/sapphire-api.sh POST /api/chats '{"name":"lookout"}'
#   tools/sapphire-api.sh DELETE /api/memory/42
#
# Same login + CSRF ceremony as ask-sapphire.sh. Output is piped straight
# through — pipe into `jq` or `python -m json.tool` for pretty printing.
set -euo pipefail

BASE="${SAPPHIRE_BASE:-https://localhost:8073}"
PASSWORD="${SAPPHIRE_PASSWORD:-changeme}"
COOKIE_JAR="/tmp/sapphire-api-cookies.txt"

METHOD="${1:-}"
PATH_="${2:-}"
BODY="${3:-}"

if [ -z "$METHOD" ] || [ -z "$PATH_" ]; then
    echo "Usage: $(basename "$0") METHOD PATH [BODY]" >&2
    echo "Examples:" >&2
    echo "  $(basename "$0") GET /api/chats" >&2
    echo "  $(basename "$0") POST /api/chats '{\"name\":\"lookout\"}'" >&2
    exit 1
fi

# Fresh login every invocation — CSRF must come from the same session as the
# cookie jar. This is slow (two extra round-trips) but self-contained.
rm -f "$COOKIE_JAR"
CSRF=$(curl -sk -c "$COOKIE_JAR" "$BASE/login" | grep -oP 'name="csrf_token"\s+value="\K[^"]+')
if [ -z "$CSRF" ]; then
    echo "ERROR: could not obtain CSRF token from $BASE/login. Is Sapphire running?" >&2
    exit 2
fi
curl -sk -b "$COOKIE_JAR" -c "$COOKIE_JAR" -X POST "$BASE/login" \
    -d "password=$PASSWORD&csrf_token=$CSRF" -o /dev/null
# The session rotates after login (session-fixation fix 2026-04-22), so the
# form token no longer matches: re-scrape the post-login token from / as
# ask-sapphire.sh does. Without this every non-GET answered 403 (2026-10-12).
CSRF=$(curl -sk -b "$COOKIE_JAR" -c "$COOKIE_JAR" "$BASE/" | grep -oP 'name="csrf-token"\s+content="\K[^"]+')
if [ -z "$CSRF" ]; then
    echo "ERROR: could not fetch the post-login CSRF token from $BASE/ (login failed?)" >&2
    exit 2
fi

ARGS=(-sk -b "$COOKIE_JAR" -H "X-CSRF-Token: $CSRF" -X "$METHOD" "$BASE$PATH_")
if [ -n "$BODY" ]; then
    ARGS+=(-H "Content-Type: application/json" -d "$BODY")
fi

# Show HTTP status on stderr for visibility; body on stdout so it can be piped.
RESPONSE=$(curl "${ARGS[@]}" -w '\nHTTP_STATUS=%{http_code}' 2>/dev/null)
BODY_OUT=$(echo "$RESPONSE" | sed '$d')
STATUS=$(echo "$RESPONSE" | tail -1 | sed 's/HTTP_STATUS=//')
echo "HTTP $STATUS" >&2
echo "$BODY_OUT"
# Non-2xx exits nonzero so scripts can check $?
case "$STATUS" in
    2*) exit 0 ;;
    *)  exit 1 ;;
esac
