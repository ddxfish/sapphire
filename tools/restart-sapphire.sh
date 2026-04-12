#!/bin/bash
# restart-sapphire.sh — Restart Sapphire via systemd (user service)
# Usage: tools/restart-sapphire.sh [--status]
set -euo pipefail

if [ "${1:-}" = "--status" ]; then
    systemctl --user status sapphire 2>&1 | head -20
    exit 0
fi

echo "Restarting Sapphire..."
systemctl --user restart sapphire
sleep 3
STATUS=$(systemctl --user is-active sapphire 2>&1)
echo "Status: $STATUS"

if [ "$STATUS" = "active" ]; then
    echo "Sapphire is running."
else
    echo "WARNING: Sapphire may not have started. Check logs:"
    echo "  journalctl --user -u sapphire -n 20"
fi
