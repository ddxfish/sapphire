#!/bin/bash

# Sapphire Abilities API Test Script
# Tests all abilities endpoints with authentication

BASE_URL="https://localhost:8073"
COOKIE_FILE="/tmp/sapphire_test_cookies.txt"
PASSWORD="changeme"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Test results
TESTS_PASSED=0
TESTS_FAILED=0

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}Sapphire Abilities API Test Suite${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# Clean up old cookies
rm -f "$COOKIE_FILE"

# Step 1: Login to get session cookie
echo -e "${YELLOW}[1] Logging in...${NC}"
LOGIN_RESPONSE=$(curl -k -s -c "$COOKIE_FILE" "$BASE_URL/login" \
  -d "password=$PASSWORD" \
  -w "\n%{http_code}" \
  -L)

HTTP_CODE=$(echo "$LOGIN_RESPONSE" | tail -1)

if [ "$HTTP_CODE" = "200" ]; then
    echo -e "${GREEN}✓ Login successful${NC}"
    ((TESTS_PASSED++))
else
    echo -e "${RED}✗ Login failed (HTTP $HTTP_CODE)${NC}"
    ((TESTS_FAILED++))
    echo "Exiting - cannot proceed without authentication"
    exit 1
fi
echo ""

# Step 2: List all abilities
echo -e "${YELLOW}[2] Listing all abilities...${NC}"
ABILITIES=$(curl -k -s -b "$COOKIE_FILE" "$BASE_URL/api/abilities")
echo "$ABILITIES" | python3 -m json.tool 2>/dev/null

if echo "$ABILITIES" | grep -q '"abilities"'; then
    echo -e "${GREEN}✓ List abilities successful${NC}"
    ((TESTS_PASSED++))
else
    echo -e "${RED}✗ List abilities failed${NC}"
    echo "Response: $ABILITIES"
    ((TESTS_FAILED++))
fi
echo ""

# Step 3: Get current ability
echo -e "${YELLOW}[3] Getting current ability...${NC}"
CURRENT=$(curl -k -s -b "$COOKIE_FILE" "$BASE_URL/api/abilities/current")
echo "$CURRENT" | python3 -m json.tool 2>/dev/null

if echo "$CURRENT" | grep -q '"name"'; then
    echo -e "${GREEN}✓ Get current ability successful${NC}"
    ((TESTS_PASSED++))
else
    echo -e "${RED}✗ Get current ability failed${NC}"
    echo "Response: $CURRENT"
    ((TESTS_FAILED++))
fi
echo ""

# Step 4: List all functions
echo -e "${YELLOW}[4] Listing all functions...${NC}"
FUNCTIONS=$(curl -k -s -b "$COOKIE_FILE" "$BASE_URL/api/functions")
echo "$FUNCTIONS" | python3 -m json.tool 2>/dev/null

if echo "$FUNCTIONS" | grep -q '"modules"'; then
    echo -e "${GREEN}✓ List functions successful${NC}"
    ((TESTS_PASSED++))
else
    echo -e "${RED}✗ List functions failed${NC}"
    echo "Response: $FUNCTIONS"
    ((TESTS_FAILED++))
fi
echo ""

# Step 5: Activate "work" ability
echo -e "${YELLOW}[5] Activating 'work' ability...${NC}"
ACTIVATE=$(curl -k -s -b "$COOKIE_FILE" -X POST "$BASE_URL/api/abilities/work/activate")
echo "$ACTIVATE" | python3 -m json.tool 2>/dev/null

if echo "$ACTIVATE" | grep -q '"status".*"success"'; then
    echo -e "${GREEN}✓ Activate ability successful${NC}"
    ((TESTS_PASSED++))
else
    echo -e "${RED}✗ Activate ability failed${NC}"
    echo "Response: $ACTIVATE"
    ((TESTS_FAILED++))
fi
echo ""

# Step 6: Verify activation worked
echo -e "${YELLOW}[6] Verifying activation...${NC}"
VERIFY=$(curl -k -s -b "$COOKIE_FILE" "$BASE_URL/api/abilities/current")
echo "$VERIFY" | python3 -m json.tool 2>/dev/null

if echo "$VERIFY" | grep -q '"name".*"work"'; then
    echo -e "${GREEN}✓ Ability activation verified${NC}"
    ((TESTS_PASSED++))
else
    echo -e "${RED}✗ Ability activation not reflected${NC}"
    echo "Response: $VERIFY"
    ((TESTS_FAILED++))
fi
echo ""

# Step 7: Enable custom function set
echo -e "${YELLOW}[7] Enabling custom function set...${NC}"
CUSTOM=$(curl -k -s -b "$COOKIE_FILE" -X POST "$BASE_URL/api/functions/enable" \
  -H "Content-Type: application/json" \
  -d '{"functions": ["get_memories", "search_memory"]}')
echo "$CUSTOM" | python3 -m json.tool 2>/dev/null

if echo "$CUSTOM" | grep -q '"status".*"success"'; then
    echo -e "${GREEN}✓ Enable custom functions successful${NC}"
    ((TESTS_PASSED++))
else
    echo -e "${RED}✗ Enable custom functions failed${NC}"
    echo "Response: $CUSTOM"
    ((TESTS_FAILED++))
fi
echo ""

# Step 8: Save custom ability
echo -e "${YELLOW}[8] Saving custom ability 'test_research'...${NC}"
SAVE=$(curl -k -s -b "$COOKIE_FILE" -X POST "$BASE_URL/api/abilities/custom" \
  -H "Content-Type: application/json" \
  -d '{"name": "test_research", "functions": ["get_memories", "search_memory"]}')
echo "$SAVE" | python3 -m json.tool 2>/dev/null

if echo "$SAVE" | grep -q '"status".*"success"'; then
    echo -e "${GREEN}✓ Save custom ability successful${NC}"
    ((TESTS_PASSED++))
else
    echo -e "${RED}✗ Save custom ability failed${NC}"
    echo "Response: $SAVE"
    ((TESTS_FAILED++))
fi
echo ""

# Step 9: Verify custom ability exists
echo -e "${YELLOW}[9] Verifying custom ability exists...${NC}"
CHECK=$(curl -k -s -b "$COOKIE_FILE" "$BASE_URL/api/abilities")
echo "$CHECK" | python3 -m json.tool 2>/dev/null | grep -A 2 "test_research" || echo "(Not found in list)"

if echo "$CHECK" | grep -q '"name".*"test_research"'; then
    echo -e "${GREEN}✓ Custom ability verified${NC}"
    ((TESTS_PASSED++))
else
    echo -e "${RED}✗ Custom ability not found${NC}"
    ((TESTS_FAILED++))
fi
echo ""

# Step 10: Activate custom ability
echo -e "${YELLOW}[10] Activating custom ability...${NC}"
ACTIVATE_CUSTOM=$(curl -k -s -b "$COOKIE_FILE" -X POST "$BASE_URL/api/abilities/test_research/activate")
echo "$ACTIVATE_CUSTOM" | python3 -m json.tool 2>/dev/null

if echo "$ACTIVATE_CUSTOM" | grep -q '"status".*"success"'; then
    echo -e "${GREEN}✓ Activate custom ability successful${NC}"
    ((TESTS_PASSED++))
else
    echo -e "${RED}✗ Activate custom ability failed${NC}"
    echo "Response: $ACTIVATE_CUSTOM"
    ((TESTS_FAILED++))
fi
echo ""

# Step 11: Delete custom ability
echo -e "${YELLOW}[11] Deleting custom ability...${NC}"
DELETE=$(curl -k -s -b "$COOKIE_FILE" -X DELETE "$BASE_URL/api/abilities/test_research")
echo "$DELETE" | python3 -m json.tool 2>/dev/null

if echo "$DELETE" | grep -q '"status".*"success"'; then
    echo -e "${GREEN}✓ Delete custom ability successful${NC}"
    ((TESTS_PASSED++))
else
    echo -e "${RED}✗ Delete custom ability failed${NC}"
    echo "Response: $DELETE"
    ((TESTS_FAILED++))
fi
echo ""

# Step 12: Try to delete built-in ability (should fail)
echo -e "${YELLOW}[12] Attempting to delete built-in ability (should fail)...${NC}"
DELETE_BUILTIN=$(curl -k -s -b "$COOKIE_FILE" -X DELETE "$BASE_URL/api/abilities/all")
echo "$DELETE_BUILTIN" | python3 -m json.tool 2>/dev/null

if echo "$DELETE_BUILTIN" | grep -q '"error".*"Cannot delete built-in"'; then
    echo -e "${GREEN}✓ Built-in protection working${NC}"
    ((TESTS_PASSED++))
else
    echo -e "${RED}✗ Built-in protection failed${NC}"
    echo "Response: $DELETE_BUILTIN"
    ((TESTS_FAILED++))
fi
echo ""

# Step 13: Reset to default ability
echo -e "${YELLOW}[13] Resetting to default ability...${NC}"
RESET=$(curl -k -s -b "$COOKIE_FILE" -X POST "$BASE_URL/api/abilities/default/activate")
echo "$RESET" | python3 -m json.tool 2>/dev/null

if echo "$RESET" | grep -q '"status".*"success"'; then
    echo -e "${GREEN}✓ Reset to default successful${NC}"
    ((TESTS_PASSED++))
else
    echo -e "${RED}✗ Reset to default failed${NC}"
    echo "Response: $RESET"
    ((TESTS_FAILED++))
fi
echo ""

# Clean up
rm -f "$COOKIE_FILE"

# Summary
echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}Test Summary${NC}"
echo -e "${BLUE}========================================${NC}"
echo -e "Total Tests: $((TESTS_PASSED + TESTS_FAILED))"
echo -e "${GREEN}Passed: $TESTS_PASSED${NC}"
echo -e "${RED}Failed: $TESTS_FAILED${NC}"
echo ""

if [ $TESTS_FAILED -eq 0 ]; then
    echo -e "${GREEN}✓ All tests passed!${NC}"
    exit 0
else
    echo -e "${RED}✗ Some tests failed${NC}"
    exit 1
fi