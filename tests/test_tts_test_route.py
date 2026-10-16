"""POST /api/tts/test proves a real synthesis, not just a health ping
(user report 2026-10-15: Test said OK while every sentence failed)."""
import asyncio
from types import SimpleNamespace

import core.api_fastapi  # noqa: F401 — the routes module imports from it; this order avoids the cycle
from core.routes.tts import test_tts as tts_test_route  # alias: pytest would collect the route by name


class _Provider:
    def __init__(self, available=True):
        self.available = available
        self._last_error = None

    def is_available(self):
        return self.available


def _system(audio, available=True, calls=None):
    prov = _Provider(available)
    tts = SimpleNamespace(provider=prov,
                          generate_audio_data=lambda text, **kw: (calls.append(text) if calls is not None else None) or audio)
    return SimpleNamespace(tts=tts)


def test_reachable_and_speaks():
    r = asyncio.run(tts_test_route(None, None, _system(b"OGG" * 100)))
    assert r["success"] and r["bytes"] == 300 and "synth_ms" in r and "ms" in r


def test_reachable_but_no_audio_is_a_failure_with_the_reason():
    system = _system(None)
    system.tts.provider._last_error = "HTTP 404 Voice 'zz' unavailable"
    r = asyncio.run(tts_test_route(None, None, system))
    assert not r["success"] and "synthesis failed" in r["error"].lower() and "zz" in r["error"]


def test_unreachable_never_tries_to_speak():
    calls = []
    r = asyncio.run(tts_test_route(None, None, _system(b"x", available=False, calls=calls)))
    assert not r["success"] and calls == []
