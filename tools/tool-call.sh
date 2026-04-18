#!/bin/bash
# tool-call.sh — directly invoke a plugin tool function in Sapphire's env.
# Bypasses the LLM — useful for testing tool CODE in isolation.
# Usage: bash tools/tool-call.sh MODULE.FUNCTION '{"arg1": "val"}'
# Example:
#   bash tools/tool-call.sh plugins.memory.tools.memory_tools._save_memory '{"content":"test"}'

FUNC="$1"
ARGS="${2:-\{\}}"

if [ -z "$FUNC" ]; then
    echo "Usage: tool-call.sh MODULE.FUNCTION '{json_args}'" >&2
    exit 3
fi

cd "$(dirname "$0")/.." || exit 2

conda run -n sapphire python3 -c "
import json, sys
from importlib import import_module

func_path = '$FUNC'
args_json = '''$ARGS'''

try:
    args = json.loads(args_json)
    if not isinstance(args, dict):
        print('args must be a JSON object', file=sys.stderr)
        sys.exit(4)
except Exception as e:
    print(f'bad JSON: {e}', file=sys.stderr)
    sys.exit(4)

parts = func_path.rsplit('.', 1)
if len(parts) != 2:
    print('FUNC must be MODULE.FUNCTION', file=sys.stderr)
    sys.exit(3)

try:
    mod = import_module(parts[0])
    fn = getattr(mod, parts[1])
except Exception as e:
    print(f'import failed: {e}', file=sys.stderr)
    sys.exit(5)

try:
    result = fn(**args)
except Exception as e:
    print(f'TOOL EXCEPTION: {type(e).__name__}: {e}', file=sys.stderr)
    sys.exit(6)

if isinstance(result, (dict, list, tuple)):
    print(json.dumps(result, default=str, indent=2))
else:
    print(result)
"
