"""shared/checklist-modal.js pure-function corpus, run under node
(same pattern as test_markdown_sanitizer.py). Covers the filter table
(All/None/Main/Custom) every prompts modal reads, the row markup the harvest
and the filters depend on, and the flat-vs-sectioned list markup. Skips
cleanly without node.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CORPUS = PROJECT_ROOT / "tests" / "js" / "checklist-modal.test.mjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_checklist_modal_corpus_passes():
    assert CORPUS.exists(), CORPUS
    result = subprocess.run(["node", str(CORPUS)], capture_output=True, text=True,
                            timeout=30, cwd=str(PROJECT_ROOT))
    if result.returncode != 0:
        pytest.fail(f"checklist-modal corpus failed (exit {result.returncode}):\n"
                    f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}")
    assert "passed" in result.stdout.lower(), result.stdout
