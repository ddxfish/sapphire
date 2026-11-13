"""shared/inline-md.js corpus, run under node (same pattern as
test_mic_words_js.py): what inline markdown draws, and that no chat text can
put an attribute on a tag the renderer builds. Skips cleanly without node.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CORPUS = PROJECT_ROOT / "tests" / "js" / "inline-md.test.mjs"
STATIC = PROJECT_ROOT / "interfaces" / "web" / "static"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_inline_md_corpus_passes():
    result = subprocess.run(["node", str(CORPUS)], capture_output=True, text=True,
                            timeout=30, cwd=str(PROJECT_ROOT))
    if result.returncode != 0:
        pytest.fail(f"inline-md corpus failed (exit {result.returncode}):\n"
                    f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}")
    assert "passed" in result.stdout, result.stdout


def test_the_chat_renderer_builds_no_tag_attributes_itself():
    # ui-parsing.js draws inline markdown through shared/inline-md.js only: a
    # second image or link pass beside it is how the hole came back
    s = (STATIC / "ui-parsing.js").read_text(encoding="utf-8")
    assert "from './shared/inline-md.js'" in s
    assert '<img src="${' not in s and '<a href="${' not in s
