"""Release-check wave, 2026-09-20 (record: tmp/release-check-20260920.md).

Windows scouts W1/W2 + the fresh-install dependency probe. Each test pins one
shipped fix so it can't quietly regress:
  - pip hint quoting (core/plugin_loader.py pip_hint) — the copy-paste line
    was unquoted; `davey>=0.1.4` redirected, `py-cord[voice] @ git+…` split.
  - conda detection without `conda activate` (core/routes/plugins.py).
  - netstat orphan kill compares the port exactly (core/process_manager.py).
  - MCP daemon takes a Proactor loop on Windows (plugins/mcp_client/daemon.py).
  - clock ping rides the core audio backend and warns at set-time when silent.
  - HF_HOME redirect is set in sapphire.py's env block, before any spawn.
"""
import asyncio
import os
import sys
import types
from pathlib import Path

ROOT = Path(__file__).parent.parent


# ---- pip hint --------------------------------------------------------------

def test_pip_hint_quotes_only_what_shells_would_mangle():
    from core.plugin_loader import PluginLoader
    hint = PluginLoader.pip_hint([
        "py-cord[voice] @ git+https://example.invalid/pycord.git@abc123",
        "davey>=0.1.4", "PyNaCl", "mss",
    ])
    assert hint == ('pip install "py-cord[voice] @ git+https://example.invalid/pycord.git@abc123" '
                    '"davey>=0.1.4" PyNaCl mss')


def test_every_hint_site_uses_pip_hint():
    """No site may rebuild the unquoted string by hand again."""
    for rel in ("core/plugin_loader.py", "core/routes/plugins.py"):
        src = (ROOT / rel).read_text(encoding="utf-8")
        assert "' '.join(missing)" not in src, f"{rel} still joins missing specs unquoted"


# ---- conda detection -------------------------------------------------------

def test_conda_env_detected_by_conda_meta_without_activation(tmp_path, monkeypatch):
    import core.api_fastapi  # noqa: F401 — routes import the app back; load it first
    from core.routes.plugins import _conda_env_name
    monkeypatch.delenv("CONDA_DEFAULT_ENV", raising=False)
    env = tmp_path / "envs" / "sapphire"
    (env / "conda-meta").mkdir(parents=True)
    monkeypatch.setattr(sys, "prefix", str(env))
    assert _conda_env_name() == "sapphire"
    # a plain system python has no conda-meta
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "usr"))
    assert _conda_env_name() is None
    monkeypatch.setenv("CONDA_DEFAULT_ENV", "other")
    assert _conda_env_name() == "other"


# ---- netstat exact port ----------------------------------------------------

NETSTAT = """
Active Connections

  Proto  Local Address          Foreign Address        State           PID
  TCP    0.0.0.0:50120          0.0.0.0:0              LISTENING       111
  TCP    127.0.0.1:5012         0.0.0.0:0              LISTENING       222
  TCP    [::]:5012              [::]:0                 LISTENING       333
  TCP    127.0.0.1:5012         127.0.0.1:60001        ESTABLISHED     444
"""


def test_windows_orphan_kill_matches_port_exactly(monkeypatch):
    import core.process_manager as pm
    calls = []

    def fake_run(argv, **kw):
        if argv[0] == "netstat":
            return types.SimpleNamespace(stdout=NETSTAT, returncode=0)
        calls.append(argv)
        return types.SimpleNamespace(stdout="", returncode=0)

    monkeypatch.setattr(pm, "IS_WINDOWS", True)
    monkeypatch.setattr(pm.subprocess, "run", fake_run)
    assert pm.kill_process_on_port(5012) is True
    killed = {a[-1] for a in calls}
    assert killed == {"222", "333"}, killed          # both listeners on 5012, never 50120


# ---- MCP daemon loop -------------------------------------------------------

def test_mcp_daemon_uses_proactor_loop_on_windows(monkeypatch):
    import plugins.mcp_client.daemon as d
    made = []

    class FakeProactor:
        def __init__(self):
            made.append("proactor")

    monkeypatch.setattr(d.sys, "platform", "win32")
    monkeypatch.setattr(asyncio, "ProactorEventLoop", FakeProactor, raising=False)
    started = []
    monkeypatch.setattr(d.threading, "Thread",
                        lambda **kw: types.SimpleNamespace(start=lambda: started.append(kw["name"])))
    d.start(plugin_loader=None, settings={"servers": {"x": {"enabled": True}}})
    try:
        assert made == ["proactor"] and started == ["mcp-daemon"]
    finally:
        d._loop = None; d._thread = None


def test_mcp_bridge_never_stores_an_empty_error():
    src = (ROOT / "plugins/mcp_client/mcp_bridge.py").read_text(encoding="utf-8")
    assert "self._error = str(e) or repr(e)" in src


# ---- clock ping ------------------------------------------------------------

def test_clock_timer_warns_when_ping_would_be_silent(monkeypatch):
    import plugins.clock.tools.clock_tools as ct
    from core.audio import backend
    monkeypatch.setattr(backend, "ensure", lambda *a, **k: False)
    monkeypatch.setattr(backend, "error", lambda: "PortAudio missing")
    msg, ok = ct._set_timer({"name": "t-silent", "time": "5m"})
    try:
        assert ok and "silent" in msg and "PortAudio missing" in msg
        assert ct._play_ping() is False
    finally:
        ct._set_timer({"name": "t-silent", "delete": True})


def test_clock_ping_plays_through_core_backend(monkeypatch, tmp_path):
    import numpy as np
    import soundfile as sf
    import plugins.clock.tools.clock_tools as ct
    from core.audio import backend
    wav = tmp_path / "ping.wav"
    sf.write(str(wav), np.zeros(800, dtype="float32"), 8000)
    monkeypatch.setattr(ct, "_PING_WAV", wav)
    monkeypatch.setattr(backend, "ensure", lambda *a, **k: True)
    played = []
    fake_sd = types.SimpleNamespace(play=lambda data, rate: played.append((len(data), rate)),
                                    wait=lambda: None)
    monkeypatch.setattr(backend, "sd", fake_sd)
    monkeypatch.setattr(ct.time, "sleep", lambda s: None)
    assert ct._play_ping() is True
    assert played == [(800, 8000), (800, 8000)]
    src = (ROOT / "plugins/clock/tools/clock_tools.py").read_text(encoding="utf-8")
    assert "import subprocess" not in src, "playback must not shell out again"


# ---- HF_HOME ---------------------------------------------------------------

def test_hf_home_redirect_lives_in_the_env_block_before_logging_import():
    src = (ROOT / "sapphire.py").read_text(encoding="utf-8")
    hf = src.index('os.environ.setdefault("HF_HOME"')
    assert hf < src.index("import core.sapphire_logging"), "HF_HOME must be set before any core import"
    emb = (ROOT / "core/embeddings/__init__.py").read_text(encoding="utf-8")
    assert "os.environ['HF_HOME']" not in emb, "the redirect moved to sapphire.py — one owner"
