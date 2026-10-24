"""Loop stall watch: a blocked event loop is reported, with the culprit's stack."""
import asyncio
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import loop_watch


def _hog_the_loop(seconds):
    time.sleep(seconds)            # sync work on the loop — the bug class we watch for


def test_blocked_loop_logs_stall_and_stack(monkeypatch, caplog):
    monkeypatch.setattr(loop_watch, "TICK_S", 0.05)
    monkeypatch.setattr(loop_watch, "STALL_S", 0.3)
    monkeypatch.setattr(loop_watch, "DUMP_S", 0.4)
    monkeypatch.setattr(loop_watch, "_watching", False)

    async def main():
        watch = asyncio.create_task(loop_watch.run())
        await asyncio.sleep(0.2)
        _hog_the_loop(1.0)
        await asyncio.sleep(0.2)
        watch.cancel()

    with caplog.at_level(logging.WARNING, logger="core.loop_watch"):
        asyncio.run(main())
    text = caplog.text
    assert "event loop stalled" in text                     # the ticker, after recovery
    assert "event loop blocked" in text and "_hog_the_loop" in text   # the watcher named the culprit
    assert "whole process was held" not in text             # only the loop was stuck
