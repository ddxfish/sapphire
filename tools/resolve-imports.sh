#!/bin/bash
# resolve-imports.sh — walk static ES import graph from a JS entry file, flag broken paths.
# Catches "deleted file still imported" class of regressions that would silently break
# the frontend at browser-load time and never show up in pytest.
#
# Usage: bash tools/resolve-imports.sh [ENTRY]
#   ENTRY defaults to interfaces/web/static/main.js
# Exit: 0 if all imports resolve, 1 if any broken

set -u
ENTRY="${1:-$(cd "$(dirname "$0")/.." && pwd)/interfaces/web/static/main.js}"

if [ ! -f "$ENTRY" ]; then
    echo "entry not found: $ENTRY" >&2
    exit 2
fi

node -e '
const fs = require("fs");
const path = require("path");

const visited = new Set();
const broken = [];

function resolve(fromFile, importPath) {
    if (!importPath.startsWith(".")) return null;
    let r = path.resolve(path.dirname(fromFile), importPath);
    if (!r.endsWith(".js")) r += ".js";
    return r;
}

function scan(file) {
    if (visited.has(file)) return;
    visited.add(file);
    let content;
    try { content = fs.readFileSync(file, "utf8"); }
    catch { broken.push(`MISSING: ${file}`); return; }
    // Match both `import x from "./y"` and `import "./y"` and dynamic `import("./y")`
    const staticRe = /(?:^|\n)\s*import\s+(?:[^"\x27]+\s+from\s+)?["\x27]([^"\x27]+)["\x27]\s*;?/g;
    const dynamicRe = /\bimport\s*\(\s*["\x27]([^"\x27]+)["\x27]\s*\)/g;
    for (const re of [staticRe, dynamicRe]) {
        let m;
        while ((m = re.exec(content))) {
            const target = resolve(file, m[1]);
            if (target) {
                if (!fs.existsSync(target)) {
                    broken.push(`${file.replace(process.cwd() + "/", "")} -> ${m[1]}`);
                } else if (re === staticRe) {
                    scan(target);  // only recurse through static imports
                }
            }
        }
    }
}

scan("'"$ENTRY"'");
if (broken.length) {
    console.log("BROKEN IMPORTS:");
    broken.forEach(b => console.log("  " + b));
    process.exit(1);
} else {
    console.log(`ok: ${visited.size} modules scanned, all imports resolve`);
}
'
