"""shared/plugin-settings-renderer.js: the rows widget, show_if, and the
secret textarea (Devices page, tmp/device-manager-plan.md C5). Run under node,
same pattern as test_files_marker_js.py. Skips cleanly without node.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CORPUS = PROJECT_ROOT / "tests" / "js" / "settings-renderer-devices.test.mjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_settings_renderer_devices_corpus_passes():
    assert CORPUS.exists(), CORPUS
    result = subprocess.run(["node", str(CORPUS)], capture_output=True, text=True,
                            timeout=30, cwd=str(PROJECT_ROOT))
    if result.returncode != 0:
        pytest.fail(f"renderer corpus failed (exit {result.returncode}):\n"
                    f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}")
    assert "passed" in result.stdout.lower(), result.stdout
