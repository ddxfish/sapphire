# tests/test_plugin_envs.py — per-plugin conda envs (core/plugin_envs.py)
#
# Unit tests always run (no conda subprocess). The integration test builds a
# REAL conda env (sapphire-plugin-envtest) and is gated behind
# SAPPHIRE_ENV_TESTS=1 so the default suite — and sapphire-health --long's
# 90s pytest budget — stays fast. The env is reused across runs: matching
# spec hash means no rebuild and no downloads.
#
#   SAPPHIRE_ENV_TESTS=1 python -m pytest tests/test_plugin_envs.py -v

import os
import subprocess
import time

import pytest

from core import plugin_envs


# ── Unit ──

def test_env_name_sanitize():
    assert plugin_envs.env_name("qwen3-tts") == "sapphire-plugin-qwen3-tts"
    assert plugin_envs.env_name("My Plugin!") == "sapphire-plugin-my-plugin"
    assert plugin_envs.env_name("UPPER_case") == "sapphire-plugin-upper-case"


def test_spec_hash_stable_and_order_independent():
    a = {"python": "3.11", "pip": ["torch"]}
    b = {"pip": ["torch"], "python": "3.11"}
    assert plugin_envs.spec_hash(a) == plugin_envs.spec_hash(b)
    assert plugin_envs.spec_hash(a) != plugin_envs.spec_hash({"python": "3.12", "pip": ["torch"]})
    assert plugin_envs.spec_hash({}) == plugin_envs.spec_hash(None)


def test_env_status_no_conda(monkeypatch):
    monkeypatch.setattr(plugin_envs, "find_conda", lambda: None)
    assert plugin_envs.env_status("ghost-plugin", {"python": "3.11"}) == "no-conda"


def test_env_status_missing(monkeypatch):
    monkeypatch.setattr(plugin_envs, "find_conda", lambda: "/fake/conda")
    monkeypatch.setattr(plugin_envs, "env_python", lambda name: None)
    assert plugin_envs.env_status("ghost-plugin", {"python": "3.11"}) == "missing"


def test_env_status_building_wins():
    with plugin_envs._builds_lock:
        plugin_envs._builds["ghost-plugin"] = {"state": "building"}
    try:
        assert plugin_envs.env_status("ghost-plugin", {}) == "building"
    finally:
        with plugin_envs._builds_lock:
            plugin_envs._builds.pop("ghost-plugin", None)


def test_env_status_failed_build_is_error_not_stale(monkeypatch):
    """[REGRESSION_GUARD] conda create ok + pip failed leaves python present
    with no receipt. That's a failed BUILD — reporting 'stale' rendered
    'Environment outdated / Rebuild' and hid the error forever (bug hunt
    2026-07-15 F3#10). Without a this-boot build error it's still 'stale'."""
    monkeypatch.setattr(plugin_envs, "find_conda", lambda: "/fake/conda")
    monkeypatch.setattr(plugin_envs, "env_python", lambda name: "/fake/python")
    monkeypatch.setattr(plugin_envs, "_read_receipt", lambda name: {})
    with plugin_envs._builds_lock:
        plugin_envs._builds["ghost-plugin"] = {"state": "error", "error": "pip boom"}
    try:
        assert plugin_envs.env_status("ghost-plugin", {"python": "3.11"}) == "error"
    finally:
        with plugin_envs._builds_lock:
            plugin_envs._builds.pop("ghost-plugin", None)
    # No build record this boot → genuinely outdated, not an error
    assert plugin_envs.env_status("ghost-plugin", {"python": "3.11"}) == "stale"


def test_remove_refused_while_building(monkeypatch):
    monkeypatch.setattr(plugin_envs, "find_conda", lambda: "/fake/conda")
    with plugin_envs._builds_lock:
        plugin_envs._builds["ghost-plugin"] = {"state": "building"}
    try:
        assert plugin_envs.remove_env("ghost-plugin") is False
    finally:
        with plugin_envs._builds_lock:
            plugin_envs._builds.pop("ghost-plugin", None)


def test_start_build_refused_without_conda(monkeypatch):
    monkeypatch.setattr(plugin_envs, "find_conda", lambda: None)
    assert plugin_envs.start_build("ghost-plugin", {"python": "3.11"}) is False


# ── Integration (real conda env; opt-in; reused across runs) ──

ENV_TESTS = os.environ.get("SAPPHIRE_ENV_TESTS") == "1"
TEST_PLUGIN = "envtest"
# Tiny pure-python wheel exercises the pip step; downloaded once ever.
TEST_SPEC = {"python": "3.11", "pip": ["six"]}


@pytest.mark.skipif(not ENV_TESTS, reason="set SAPPHIRE_ENV_TESTS=1 to run real env builds")
@pytest.mark.skipif(plugin_envs.find_conda() is None, reason="conda not found")
def test_real_env_build_and_reuse():
    """Build (or reuse) a real conda env, run its python, verify staleness.

    Reuse contract: if sapphire-plugin-envtest already exists with a matching
    spec hash, nothing is rebuilt or downloaded — repeated runs are ~instant.
    The env is intentionally never removed.
    """
    if plugin_envs.env_status(TEST_PLUGIN, TEST_SPEC) != "ready":
        assert plugin_envs.start_build(TEST_PLUGIN, TEST_SPEC), "build did not start"
        deadline = time.time() + 900
        while time.time() < deadline:
            if plugin_envs.build_state(TEST_PLUGIN).get("state") in ("done", "error"):
                break
            time.sleep(2)
        state = plugin_envs.build_state(TEST_PLUGIN)
        assert state.get("state") == "done", (
            f"build failed: {state} / log tail: {plugin_envs.tail_log(TEST_PLUGIN)}")

    assert plugin_envs.env_status(TEST_PLUGIN, TEST_SPEC) == "ready"

    python = plugin_envs.env_python(TEST_PLUGIN)
    assert python is not None
    out = subprocess.run(
        [str(python), "-c", "import sys, six; print(sys.version.split()[0])"],
        capture_output=True, text=True, timeout=60,
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip().startswith("3.11")

    # A changed spec flips status to 'stale' (UI offers rebuild) without
    # touching the env on disk.
    assert plugin_envs.env_status(TEST_PLUGIN, {"python": "3.12", "pip": ["six"]}) == "stale"
    assert plugin_envs.env_status(TEST_PLUGIN, TEST_SPEC) == "ready"
