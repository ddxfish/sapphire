#!/bin/bash
# Curl test examples for Plugin API
# Run against a logged-in session (get cookie from browser dev tools)
#
# Usage: 
#   export SAPPHIRE_COOKIE="session=your_session_cookie_here"
#   ./tests/test_plugins_curl.sh

BASE_URL="${SAPPHIRE_URL:-https://localhost:8073}"

if [ -z "$SAPPHIRE_COOKIE" ]; then
    echo "Set SAPPHIRE_COOKIE first (grab from browser after login)"
    echo "export SAPPHIRE_COOKIE='session=...'"
    exit 1
fi

echo "=== Plugin API Curl Tests ==="
echo "Base: $BASE_URL"
echo ""

# List all plugins
echo "--- GET /api/webui/plugins ---"
curl -sk -H "Cookie: $SAPPHIRE_COOKIE" "$BASE_URL/api/webui/plugins" | python3 -m json.tool
echo ""

# Get merged config
echo "--- GET /api/webui/plugins/config ---"
curl -sk -H "Cookie: $SAPPHIRE_COOKIE" "$BASE_URL/api/webui/plugins/config" | python3 -m json.tool
echo ""

# Get settings for a plugin (empty if not set)
echo "--- GET /api/webui/plugins/image-gen/settings ---"
curl -sk -H "Cookie: $SAPPHIRE_COOKIE" "$BASE_URL/api/webui/plugins/image-gen/settings" | python3 -m json.tool
echo ""

# Save settings for image-gen
echo "--- PUT /api/webui/plugins/image-gen/settings ---"
curl -sk -X PUT \
    -H "Cookie: $SAPPHIRE_COOKIE" \
    -H "Content-Type: application/json" \
    -d '{"api_url":"http://localhost:5153","negative_prompt":"ugly, blurry"}' \
    "$BASE_URL/api/webui/plugins/image-gen/settings" | python3 -m json.tool
echo ""

# Verify it saved
echo "--- GET /api/webui/plugins/image-gen/settings (after save) ---"
curl -sk -H "Cookie: $SAPPHIRE_COOKIE" "$BASE_URL/api/webui/plugins/image-gen/settings" | python3 -m json.tool
echo ""

# Cat the actual file
echo "--- cat user/webui/plugins/image-gen.json ---"
cat user/webui/plugins/image-gen.json 2>/dev/null || echo "(file not found - test from sapphire root)"
echo ""

# Toggle a plugin (example: disable spice-manager)
echo "--- PUT /api/webui/plugins/toggle/spice-manager ---"
curl -sk -X PUT \
    -H "Cookie: $SAPPHIRE_COOKIE" \
    "$BASE_URL/api/webui/plugins/toggle/spice-manager" | python3 -m json.tool
echo ""

# Try to toggle locked plugin (should fail)
echo "--- PUT /api/webui/plugins/toggle/settings-modal (should fail) ---"
curl -sk -X PUT \
    -H "Cookie: $SAPPHIRE_COOKIE" \
    "$BASE_URL/api/webui/plugins/toggle/settings-modal" | python3 -m json.tool
echo ""

echo "=== Done ==="