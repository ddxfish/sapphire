"""A plugin file runs as a module registered in sys.modules (hunt 2026-11-12)."""
import sys
import pytest
from core.plugin_loader import _exec_as_module

SRC = '''from __future__ import annotations
from dataclasses import dataclass

@dataclass
class Row:
    name: str
    n: int = 0

def handle(event):
    return Row("x")
'''


def test_future_annotations_dataclass_loads(tmp_path):
    f = tmp_path / "h.py"
    f.write_text(SRC, encoding="utf-8")
    ns = _exec_as_module(f, "plugin_test_h")
    try:
        assert ns["handle"](None).name == "x"
        assert sys.modules["plugin_test_h"].__dict__ is ns
    finally:
        sys.modules.pop("plugin_test_h", None)


def test_a_failed_file_does_not_stay_importable(tmp_path):
    f = tmp_path / "bad.py"
    f.write_text("x = 1\nraise RuntimeError('boom')\n", encoding="utf-8")
    with pytest.raises(RuntimeError):
        _exec_as_module(f, "plugin_test_bad")
    assert "plugin_test_bad" not in sys.modules
