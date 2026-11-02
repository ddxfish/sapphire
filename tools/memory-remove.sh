#!/bin/bash
# memory-remove.sh - prune Memory (legacy) rows that mention a phrase; one
# yes/no per match. Mind Palace is not touched.
#   bash tools/memory-remove.sh PHRASE [PHRASE ...] [--count] [--yes]
# Windows: python tools\memory_remove.py PHRASE
cd "$(dirname "$0")/.." || exit 1
exec python3 tools/memory_remove.py "$@"
