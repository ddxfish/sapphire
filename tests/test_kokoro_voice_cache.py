"""Kokoro resolves model/voice files from the local HF cache, never via a
process-wide offline flag, and a handler failure is an HTTP answer.

User report 2026-10-15 (Windows, fresh install): every synth died with
LocalEntryNotFoundError for voices/af_heart.pt. The server armed
HF_HUB_OFFLINE once the model was cached, and its lift-and-retry wrapped
kokoro's generator *creation*, which never raises (the voice fetch runs on
the first next()), so the lift never fired and no uncached voice could
ever download. The escaping exception made the HTTP server drop the socket,
which the main app could only report as "Remote end closed connection".
The server is imported through tests/_kokoro_stub.py (no model, no disk,
no network)."""
import io
import json
from pathlib import Path

import torch

from _kokoro_stub import import_tts_server

SRC = (Path(__file__).resolve().parent.parent / "core" / "tts" / "tts_server.py").read_text(encoding="utf-8")


def test_no_process_wide_offline_flag():
    assert "HF_HUB_OFFLINE" not in SRC
    assert "try_to_load_from_cache" in SRC


def test_stream_resolves_voice_before_committing_headers():
    stream = SRC[SRC.index("def _handle_tts_stream"):]
    assert stream.index("_voice_pack(voice") < stream.index("self.send_response(200)")


def test_voice_pack_uses_cache_then_downloads_then_blends(monkeypatch):
    ts = import_tts_server()
    downloaded, loaded = [], []
    monkeypatch.setattr(ts, "try_to_load_from_cache",
                        lambda repo, fn: f"/cache/{fn}" if "af_heart" in fn else None)
    monkeypatch.setattr(ts, "hf_hub_download", lambda repo, fn: downloaded.append(fn) or f"/dl/{fn}")
    monkeypatch.setattr(ts._torch, "load",
                        lambda path, **kw: loaded.append(path) or torch.full((2,), 1.0 if "heart" in path else 3.0))
    monkeypatch.setattr(ts, "_packs", {})
    assert ts._voice_pack("af_heart").tolist() == [1.0, 1.0]
    assert downloaded == [] and loaded == ["/cache/voices/af_heart.pt"]
    assert ts._voice_pack("af_sky").tolist() == [3.0, 3.0]
    assert downloaded == ["voices/af_sky.pt"]
    # second use of a voice: served from the pack cache, no disk, no hub
    ts._voice_pack("af_sky")
    assert len(loaded) == 2 and len(downloaded) == 1
    # comma blends are the mean of the packs (what kokoro does for "a,b")
    assert ts._voice_pack("af_heart, af_sky").tolist() == [2.0, 2.0]


def test_unknown_voice_is_404_so_the_provider_stops_retrying():
    ts = import_tts_server()

    class Resp:
        status_code = 404

    class HubMiss(Exception):
        response = Resp()

    assert ts._unavailable_status(HubMiss()) == 404
    assert ts._unavailable_status(ConnectionError("hub unreachable")) == 503


def _fake_handler(ts, path, body: bytes):
    h = ts.TTSHandler.__new__(ts.TTSHandler)
    h.rfile, h.wfile = io.BytesIO(body), io.BytesIO()
    h.headers = {"Content-Length": str(len(body))}
    h.request_version, h.command, h.path = "HTTP/1.0", "POST", path
    h.requestline, h.client_address = f"POST {path} HTTP/1.0", ("127.0.0.1", 1)
    return h


def _status_and_json(h):
    raw = h.wfile.getvalue()
    head, _, body = raw.partition(b"\r\n\r\n")
    return int(head.split()[1]), json.loads(body)


def test_generator_raising_on_next_becomes_a_json_500(monkeypatch):
    """The exact failure class of the report: nothing runs when kokoro's
    pipeline is *called*; the error surfaces on the first next()."""
    ts = import_tts_server()
    monkeypatch.setattr(ts, "_voice_pack", lambda v, **k: torch.zeros(1))

    def boom(text, voice, speed):
        raise RuntimeError("CUDA error: device-side assert")
        yield  # noqa: a generator function on purpose

    monkeypatch.setattr(ts, "pipeline", boom)
    h = _fake_handler(ts, "/tts", json.dumps({"text": "hello there", "voice": "af_heart"}).encode())
    h._handle_tts()
    status, body = _status_and_json(h)
    assert status == 500 and "Synthesis failed" in body["error"] and "device-side assert" in body["error"]


def test_stream_voice_failure_is_json_before_any_200(monkeypatch):
    ts = import_tts_server()

    def missing(v):
        raise ConnectionError("hub unreachable")

    monkeypatch.setattr(ts, "_voice_pack", missing)
    h = _fake_handler(ts, "/tts/stream", json.dumps({"text": "hello there", "voice": "af_sky"}).encode())
    h._handle_tts_stream()
    status, body = _status_and_json(h)
    assert status == 503 and "unavailable" in body["error"]
    assert b"HTTP/1.0 200" not in h.wfile.getvalue()


def test_another_computer_names_a_voice_never_a_file():
    import pytest
    ts = import_tts_server()
    for voice in ("/etc/some.pt", "some.pt", "../voices/x"):
        with pytest.raises(ValueError):
            ts._voice_pack(voice, remote=True)
