"""Every shipped JS file must parse as an ES module.

The whole frontend is ES modules (index.html import-map). A single parse-time
SyntaxError in one module — duplicate declaration, bad import syntax, stray
brace — kills the entire import graph and blanks the UI with nothing but a
console error. That class shipped on 2026-08-17 (an imported `escapeHtml`
colliding with a local `function escapeHtml` in chat.js) and nothing caught
it before a human loaded the page.

`node --input-type=module --check` is a pure parse (no execution, no DOM
needed) and catches exactly that class, at the exact line. MODULE parse is
load-bearing: classic-script parse treats function redeclaration as legal,
so downgrading this to a plain syntax check would miss the shipped bug.

Does NOT catch: bad import *paths*, missing exports, or runtime errors —
those need the import graph actually loaded (browser/playwright territory).
"""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "interfaces" / "web" / "static"
PLUGIN_WEB = sorted((ROOT / "plugins").glob("*/web/**/*.js")) if (ROOT / "plugins").exists() else []

NODE = shutil.which("node")

def _js_files():
    files = sorted(STATIC.rglob("*.js")) + PLUGIN_WEB
    return [f for f in files if "node_modules" not in f.parts]


@pytest.mark.skipif(NODE is None, reason="node not installed")
@pytest.mark.parametrize("js_file", _js_files(), ids=lambda p: str(p.relative_to(ROOT)))
def test_js_parses_as_module(js_file):
    proc = subprocess.run(
        [NODE, "--input-type=module", "--check"],
        stdin=js_file.open("rb"),
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, (
        f"{js_file.relative_to(ROOT)} failed ES-module parse "
        f"(this blanks the whole UI):\n{proc.stderr}"
    )
