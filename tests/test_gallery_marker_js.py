"""shared/gallery-marker.js corpus, run under node (same pattern as
test_dom_guard_js.py). The ONE tool-result gallery renderer: marker parse +
strip, legacy string vs v2 object entries, no-referrer/lazy tiles, lightbox
over the full urls, caption → source page. Image-tools rebuild, 2026-09-09.
Skips cleanly without node.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CORPUS = PROJECT_ROOT / "tests" / "js" / "gallery-marker.test.mjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_gallery_marker_corpus_passes():
    assert CORPUS.exists(), CORPUS
    result = subprocess.run(["node", str(CORPUS)], capture_output=True, text=True,
                            timeout=30, cwd=str(PROJECT_ROOT))
    if result.returncode != 0:
        pytest.fail(f"gallery-marker corpus failed (exit {result.returncode}):\n"
                    f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}")
    assert "passed" in result.stdout.lower(), result.stdout
