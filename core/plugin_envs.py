# core/plugin_envs.py — Per-plugin conda environments
#
# Plugins that declare an "environment" section in plugin.json get their own
# conda env named sapphire-plugin-<name>, built on explicit user consent and
# used to run the plugin's declared services (see capabilities.services).
# The env lives in conda's own envs dir — outside the project tree, so it
# never rides backups and `conda env list` shows it to the user.
#
# A receipt file (sapphire-env.json) inside the env prefix records the hash
# of the spec that built it; a manifest edit flips status to 'stale' so the
# UI can offer a rebuild instead of running services against a wrong env.

import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

IS_WINDOWS = sys.platform == 'win32'
PROJECT_ROOT = Path(__file__).parent.parent
LOG_DIR = PROJECT_ROOT / "user" / "logs"

ENV_PREFIX = "sapphire-plugin-"
RECEIPT_NAME = "sapphire-env.json"

# Generous: torch-class downloads are GBs. Per-step, not total.
CONDA_TIMEOUT = 1800
PIP_TIMEOUT = 3600

_conda_exe = None       # cached find_conda result ('' = searched, not found)
_envs_dir = None        # cached conda envs directory
_cache_lock = threading.Lock()

# plugin name -> {"state": "building"|"done"|"error", "step": str,
#                 "error": str|None, "started": float}
_builds = {}
_builds_lock = threading.Lock()


def find_conda():
    """Locate the conda executable. Returns str path or None. Cached."""
    global _conda_exe
    with _cache_lock:
        if _conda_exe is not None:
            return _conda_exe or None

        candidates = []
        env_exe = os.environ.get("CONDA_EXE")
        if env_exe:
            candidates.append(Path(env_exe))
        which = shutil.which("conda")
        if which:
            candidates.append(Path(which))
        home = Path.home()
        for root in ("miniconda3", "anaconda3", "miniforge3", "mambaforge"):
            if IS_WINDOWS:
                candidates.append(home / root / "Scripts" / "conda.exe")
            else:
                candidates.append(home / root / "condabin" / "conda")
                candidates.append(home / root / "bin" / "conda")

        for c in candidates:
            if c and c.exists():
                _conda_exe = str(c)
                return _conda_exe
        _conda_exe = ''
        return None


def envs_dir():
    """Conda's envs directory (where named envs live). None if no conda."""
    global _envs_dir
    conda = find_conda()
    if not conda:
        return None
    with _cache_lock:
        if _envs_dir is not None:
            return _envs_dir
    try:
        result = subprocess.run(
            [conda, "info", "--json"],
            capture_output=True, text=True, timeout=30,
            encoding='utf-8', errors='replace',
        )
        dirs = json.loads(result.stdout).get("envs_dirs") or []
        if dirs:
            with _cache_lock:
                _envs_dir = Path(dirs[0])
            return _envs_dir
    except Exception as e:
        logger.warning(f"[PLUGIN-ENVS] conda info failed: {e}")
    # Fallback: conda lives at <root>/bin/conda or <root>/condabin/conda
    fallback = Path(conda).parent.parent / "envs"
    with _cache_lock:
        _envs_dir = fallback
    return fallback


def env_name(plugin_name: str) -> str:
    """sapphire-plugin-<name>, sanitized to conda-safe chars."""
    safe = "".join(c if c.isalnum() or c == '-' else '-' for c in plugin_name.lower())
    return f"{ENV_PREFIX}{safe.strip('-')}"


def env_prefix(plugin_name: str):
    """Path to the env prefix, or None if conda unavailable."""
    d = envs_dir()
    return (d / env_name(plugin_name)) if d else None


def env_python(plugin_name: str):
    """Path to the env's python if the env exists, else None."""
    prefix = env_prefix(plugin_name)
    if not prefix:
        return None
    python = prefix / "python.exe" if IS_WINDOWS else prefix / "bin" / "python"
    return python if python.exists() else None


def spec_hash(env_spec: dict) -> str:
    return hashlib.sha256(
        json.dumps(env_spec or {}, sort_keys=True, separators=(',', ':')).encode('utf-8')
    ).hexdigest()


def _read_receipt(plugin_name: str) -> dict:
    prefix = env_prefix(plugin_name)
    if not prefix:
        return {}
    try:
        return json.loads((prefix / RECEIPT_NAME).read_text(encoding='utf-8'))
    except Exception:
        return {}


def _write_receipt(plugin_name: str, env_spec: dict):
    prefix = env_prefix(plugin_name)
    receipt = {
        "plugin": plugin_name,
        "spec_hash": spec_hash(env_spec),
        "spec": env_spec,
        "built": time.time(),
    }
    (prefix / RECEIPT_NAME).write_text(json.dumps(receipt, indent=2), encoding='utf-8')


def build_state(plugin_name: str) -> dict:
    """Current build registry entry for a plugin (empty dict if never built this boot)."""
    with _builds_lock:
        return dict(_builds.get(plugin_name, {}))


def env_status(plugin_name: str, env_spec: dict) -> str:
    """'ready' | 'stale' | 'missing' | 'building' | 'error' | 'no-conda'."""
    b = build_state(plugin_name)
    if b.get("state") == "building":
        return "building"
    if not find_conda():
        return "no-conda"
    if not env_python(plugin_name):
        return "error" if b.get("state") == "error" else "missing"
    if _read_receipt(plugin_name).get("spec_hash") != spec_hash(env_spec):
        return "stale"
    return "ready"


def _log_path(plugin_name: str) -> Path:
    return LOG_DIR / f"env-build-{plugin_name}.log"


def tail_log(plugin_name: str, lines: int = 20) -> list:
    try:
        text = _log_path(plugin_name).read_text(encoding='utf-8', errors='replace')
        return text.splitlines()[-lines:]
    except Exception:
        return []


def _set_build(plugin_name: str, **fields):
    with _builds_lock:
        entry = _builds.setdefault(plugin_name, {})
        entry.update(fields)


def start_build(plugin_name: str, env_spec: dict, on_done=None) -> bool:
    """Kick off a background env build. Returns False if one is already running.

    on_done(ok: bool) is called from the worker thread after the receipt is
    written (or the build failed) — the loader uses it to reload the plugin
    so services start against the fresh env.
    """
    conda = find_conda()
    if not conda:
        return False
    with _builds_lock:
        if _builds.get(plugin_name, {}).get("state") == "building":
            return False
        _builds[plugin_name] = {"state": "building", "step": "starting",
                                "error": None, "started": time.time()}

    thread = threading.Thread(
        target=_build_worker, args=(plugin_name, env_spec, conda, on_done),
        daemon=True, name=f"EnvBuild-{plugin_name}",
    )
    thread.start()
    return True


def _build_worker(plugin_name: str, env_spec: dict, conda: str, on_done):
    name = env_name(plugin_name)
    log_path = _log_path(plugin_name)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    ok = False

    def _run_step(step_label, cmd, timeout, log_fh):
        _set_build(plugin_name, step=step_label)
        log_fh.write(f"\n=== {step_label}: {' '.join(cmd)} ===\n")
        log_fh.flush()
        result = subprocess.run(
            cmd, stdout=log_fh, stderr=subprocess.STDOUT, timeout=timeout,
        )
        if result.returncode != 0:
            raise RuntimeError(f"{step_label} failed (exit {result.returncode})")

    try:
        with open(log_path, "w", encoding='utf-8', errors='replace') as log_fh:
            log_fh.write(f"Env build for plugin '{plugin_name}' -> {name}\n")

            # Stale rebuild: remove the old env first (conda create refuses
            # to overwrite an existing prefix).
            if env_prefix(plugin_name) and env_prefix(plugin_name).exists():
                _run_step("remove old env",
                          [conda, "env", "remove", "-y", "-n", name],
                          CONDA_TIMEOUT, log_fh)

            python_ver = str(env_spec.get("python", "3.11"))
            create_cmd = [conda, "create", "-y", "-n", name, f"python={python_ver}"]
            for pkg in env_spec.get("conda", []):
                create_cmd.append(str(pkg))
            for ch in env_spec.get("channels", []):
                create_cmd.extend(["-c", str(ch)])
            _run_step("conda create", create_cmd, CONDA_TIMEOUT, log_fh)

            pip_deps = [str(d) for d in env_spec.get("pip", [])]
            if pip_deps:
                python = env_python(plugin_name)
                if not python:
                    raise RuntimeError("env python missing after conda create")
                _run_step("pip install",
                          [str(python), "-m", "pip", "install", *pip_deps],
                          PIP_TIMEOUT, log_fh)

            _write_receipt(plugin_name, env_spec)
            log_fh.write("\n=== build complete ===\n")

        _set_build(plugin_name, state="done", step="complete")
        logger.info(f"[PLUGIN-ENVS] Built env {name} for {plugin_name}")
        ok = True
    except Exception as e:
        _set_build(plugin_name, state="error", step="failed", error=str(e))
        logger.error(f"[PLUGIN-ENVS] Build failed for {plugin_name}: {e}")
        try:
            with open(log_path, "a", encoding='utf-8', errors='replace') as log_fh:
                log_fh.write(f"\n=== BUILD FAILED: {e} ===\n")
        except Exception:
            pass

    if on_done:
        try:
            on_done(ok)
        except Exception as e:
            logger.error(f"[PLUGIN-ENVS] on_done callback failed for {plugin_name}: {e}")


def remove_env(plugin_name: str) -> bool:
    """Remove a plugin's env. Refuses while a build is running."""
    conda = find_conda()
    if not conda:
        return False
    if build_state(plugin_name).get("state") == "building":
        return False
    try:
        result = subprocess.run(
            [conda, "env", "remove", "-y", "-n", env_name(plugin_name)],
            capture_output=True, text=True, timeout=CONDA_TIMEOUT,
            encoding='utf-8', errors='replace',
        )
        with _builds_lock:
            _builds.pop(plugin_name, None)
        return result.returncode == 0
    except Exception as e:
        logger.error(f"[PLUGIN-ENVS] remove_env failed for {plugin_name}: {e}")
        return False
