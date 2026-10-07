"""shared/llm-picker.js corpus, run under node (same pattern as
test_ask_marker_js.py). THE brain picker every provider+model surface renders
through: the provider list, the model rule, the read. 2026-10-07.
Skips cleanly without node.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CORPUS = PROJECT_ROOT / "tests" / "js" / "llm-picker.test.mjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_llm_picker_corpus_passes():
    assert CORPUS.exists(), CORPUS
    result = subprocess.run(["node", str(CORPUS)], capture_output=True, text=True,
                            timeout=30, cwd=str(PROJECT_ROOT))
    if result.returncode != 0:
        pytest.fail(f"llm-picker corpus failed (exit {result.returncode}):\n"
                    f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}")
    assert "passed" in result.stdout.lower(), result.stdout


def test_every_picker_surface_renders_through_the_shared_module():
    """Tripwire: the seven hand-rolled copies stay gone. Each surface imports
    the module and carries no private model_options loop."""
    static = PROJECT_ROOT / "interfaces" / "web" / "static"
    for rel in ("views/chat.js", "views/personas.js", "surface/sections/core-sections.js",
                "shared/trigger-editor/ai-config.js", "views/palace/self.js"):
        src = (static / rel).read_text(encoding="utf-8")
        assert "llm-picker.js" in src, f"{rel}: not on the shared picker"
        assert "Object.entries(meta.model_options)" not in src, f"{rel}: private model loop survives"
        assert "Object.entries(opts)" not in src, f"{rel}: private model loop survives"
