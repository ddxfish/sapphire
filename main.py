#!/usr/bin/env python3
# main.py - Sapphire Runner
# Manages sapphire.py lifecycle with restart support
#
# Exit codes from sapphire.py:
#   0  = Clean shutdown
#   42 = Restart requested
#   43 = Another Sapphire is running (instance lock or web port held) - not restarted
#   44 = Configuration error (web host, port or certificate) - not restarted
#   *  = Crash/error
#
# Signal exit codes (treated as clean shutdown):
#   -2, 130  = SIGINT (Ctrl+C)
#   -15, 143 = SIGTERM
#   -1, 129  = SIGHUP

import os
import sys
import subprocess
import signal
import time
import argparse
from pathlib import Path

IS_WINDOWS = sys.platform == 'win32'

# ANSI colors for terminal output
GREEN = '\033[92m'
YELLOW = '\033[93m'
RED = '\033[91m'
RESET = '\033[0m'

# Signal state
_runner_stopping = False
_child_process = None
_child_started = 0.0   # monotonic, set at spawn: a crash after a long healthy run is not the next strike
HEALTHY_SECONDS = 60
STOP_GRACE = 90        # seconds after a stop signal before the runner kills a child that will not end

# Exit codes that mean "user requested stop" not "crash"
CLEAN_EXIT_CODES = {
    0,          # Normal exit
    -2, 130,    # SIGINT (Ctrl+C) - negative on Unix, 128+sig on some systems
    -15, 143,   # SIGTERM
    -1, 129,    # SIGHUP
    3221225786, # STATUS_CONTROL_C_EXIT (0xC000013A) - Windows Ctrl+C termination
}


def log(msg, color=None):
    """Print with optional color."""
    if IS_WINDOWS:
        # Windows doesn't handle ANSI well in all terminals
        print(f"[Runner] {msg}")
    else:
        prefix = f"{color}" if color else ""
        suffix = f"{RESET}" if color else ""
        print(f"{prefix}[Runner] {msg}{suffix}")


def handle_signal(signum, frame):
    """Handle termination signals - mark stopping and forward to child."""
    global _runner_stopping
    _runner_stopping = True

    if _child_process and _child_process.poll() is None:
        try:
            if IS_WINDOWS:
                # On Windows, Ctrl+C already sent CTRL_C_EVENT to entire console group.
                # Calling send_signal(SIGINT) would re-send CTRL_C_EVENT to the group
                # (including ourselves), causing double delivery. Skip it.
                pass
            else:
                _child_process.send_signal(signum)
        except (ProcessLookupError, OSError):
            pass  # Child already dead
    

def _wait_for_child(proc):
    """Wait for sapphire.py. Signals are forwarded by handle_signal(); after one,
    a child that does not end within STOP_GRACE seconds is killed, so a wedged
    shutdown cannot hang the runner (and the launcher) forever. sapphire.py's
    own teardown watchdog fires first, at 60 s, with the thread stacks."""
    deadline = None
    while True:
        try:
            return proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            if _runner_stopping and deadline is None:
                deadline = time.monotonic() + STOP_GRACE
            if deadline is not None and time.monotonic() > deadline:
                log(f"Sapphire did not stop within {STOP_GRACE}s, killing it", RED)
                proc.kill()
                return proc.wait()


def run_sapphire():
    """Run sapphire.py and return its exit code."""
    global _child_process, _child_started

    script_path = Path(__file__).parent / "sapphire.py"

    if not script_path.exists():
        log(f"ERROR: sapphire.py not found at {script_path}", RED)
        return 1

    # Apply a pending update BEFORE spawning sapphire.py. This is the only
    # point where no Python process holds files open, so git pull can
    # overwrite `.py` files safely on Windows. Pip sync runs here too, so
    # new dependencies are installed before the import cascade fires.
    # Never blocks boot: any failure is written to user/last_update_result.json
    # and surfaced to the UI on next load, then we boot the previous version.
    try:
        from core.updater import apply_pending_update
        apply_pending_update()
    except Exception as e:
        log(f"Pending-update apply raised: {e}", RED)

    # Apply a pending RESTORE (swap user/) in this same safe window — nothing
    # holds user/ open before sapphire.py spawns. Preserves the old user/ as
    # user.old for rollback; a broken restore never loops.
    try:
        from core.restore import apply_pending_restore
        apply_pending_restore(log=lambda m: log(m, YELLOW))
    except Exception as e:
        log(f"Pending-restore apply raised: {e}", RED)

    # A queued GPU torch install runs in this same window: nothing holds torch's
    # DLLs before sapphire.py spawns, the only time pip can swap them on Windows.
    # Never blocks boot; the result goes to user/last_gpu_install.json.
    try:
        from core.gpu_install import apply_pending as apply_pending_gpu_install
        apply_pending_gpu_install(log=lambda m: log(m, YELLOW))
    except Exception as e:
        log(f"GPU install apply raised: {e}", RED)

    # Core integrity check (~22ms) — runs after any pending update applies, so it
    # catches a partial/half-applied update at boot instead of at crash-time. Logs via
    # the logging module (visible in journalctl) rather than the buffered launcher print.
    try:
        from core.integrity import log_boot_status
        log_boot_status()
    except Exception as e:
        log(f"Integrity boot check raised: {e}", YELLOW)

    # A stop that arrived during the window above (Ctrl+C mid-pip) used to be
    # followed by a full boot anyway. Honour it here.
    if _runner_stopping:
        log("Stop requested during the pre-start window; not starting Sapphire", YELLOW)
        return 0

    try:
        _child_started = time.monotonic()
        _child_process = subprocess.Popen(
            [sys.executable, str(script_path)],
            stdin=sys.stdin,
            stdout=sys.stdout,
            stderr=sys.stderr
        )

        exit_code = _wait_for_child(_child_process)
        _child_process = None
        return exit_code

    except KeyboardInterrupt:
        # Windows: KeyboardInterrupt can bypass custom signal handler during wait()
        if _child_process and _child_process.poll() is None:
            try:
                _child_process.wait(timeout=15)
            except (subprocess.TimeoutExpired, KeyboardInterrupt):
                try:
                    _child_process.kill()
                except OSError:
                    pass
        _child_process = None
        return 0  # Treat Ctrl+C as clean exit

    except Exception as e:
        log(f"ERROR: Failed to run sapphire.py: {e}", RED)
        _child_process = None
        return 1


def main():
    global _runner_stopping
    
    parser = argparse.ArgumentParser(description='Sapphire Voice Assistant Runner')
    parser.add_argument('--once', action='store_true', 
                        help='Run once without restart loop (for debugging)')
    parser.add_argument('--max-crashes', type=int, default=5,
                        help='Max consecutive crashes before giving up (default: 5)')
    args = parser.parse_args()
    
    # Register signal handlers
    signal.signal(signal.SIGINT, handle_signal)
    if hasattr(signal, 'SIGTERM'):
        signal.signal(signal.SIGTERM, handle_signal)
    if hasattr(signal, 'SIGHUP'):
        signal.signal(signal.SIGHUP, handle_signal)
    if hasattr(signal, 'SIGBREAK'):
        # Windows: a graceful stop from the launcher is CTRL_BREAK to our process
        # group (the console group gets it, sapphire.py included), never taskkill
        signal.signal(signal.SIGBREAK, handle_signal)
    
    log("Sapphire Runner starting", GREEN)

    # One Sapphire per user/. The second runner stops HERE, before the update
    # window and before any core import touches settings; the lock dies with
    # this process and the child is told the runner holds it (2026-10-16).
    from core.single_instance import acquire_or_exit, ENV_HELD, EXIT_ANOTHER_INSTANCE, EXIT_CONFIG
    _instance_lock = acquire_or_exit(Path(__file__).parent, who='The runner', log=lambda m: log(m, RED))
    os.environ[ENV_HELD] = str(os.getpid())
    from core.single_instance import tie_children_to_me
    _job = tie_children_to_me(log=lambda m: log(m, YELLOW))   # Windows: no child outlives this runner
    
    if args.once:
        log("Running in single-run mode (--once)", YELLOW)
        exit_code = run_sapphire()
        log(f"Exited with code {exit_code}")
        sys.exit(0 if exit_code in CLEAN_EXIT_CODES or exit_code == 42 else exit_code)
    
    consecutive_crashes = 0
    backoff_seconds = 2
    
    while True:
        _runner_stopping = False
        exit_code = run_sapphire()
        
        # Check if runner itself was signaled to stop
        if _runner_stopping:
            log("Interrupted, exiting", YELLOW)
            sys.exit(0)
        
        # Check for clean exit codes (including signal-based exits)
        if exit_code in CLEAN_EXIT_CODES:
            log("Clean shutdown, exiting runner", GREEN)
            sys.exit(0)
        
        # Another instance, or a configuration that cannot work: restarting
        # would only repeat the same line five times and then give up anyway.
        if exit_code in (EXIT_ANOTHER_INSTANCE, EXIT_CONFIG):
            log("Not restarting: " + ("another Sapphire holds the instance lock or the web port"
                                      if exit_code == EXIT_ANOTHER_INSTANCE else
                                      "configuration error (web host, port or certificate), see the lines above"), RED)
            sys.exit(exit_code)

        # Restart requested
        if exit_code == 42:
            log("Restart requested, restarting in 1 second...", YELLOW)
            consecutive_crashes = 0
            time.sleep(1)
            
            # Double-check we weren't interrupted during sleep
            if _runner_stopping:
                log("Interrupted during restart delay, exiting", YELLOW)
                sys.exit(0)
            continue
        
        # Crash or error
        if _child_started and time.monotonic() - _child_started >= HEALTHY_SECONDS:
            consecutive_crashes = 0   # it ran healthy for a while: a new story, not strike N
        consecutive_crashes += 1
        hint = " - no traceback above means it was ended from outside (taskkill, launcher Stop, Task Manager)" if exit_code == 1 else ""
        log(f"Crashed with exit code {exit_code} (crash {consecutive_crashes}/{args.max_crashes}){hint}", RED)
        
        if consecutive_crashes >= args.max_crashes:
            log(f"Too many consecutive crashes, giving up", RED)
            sys.exit(1)
        
        # Exponential backoff: 2, 4, 8, 16, 32 seconds (capped)
        wait_time = min(backoff_seconds * (2 ** (consecutive_crashes - 1)), 32)
        log(f"Restarting in {wait_time} seconds... (Ctrl+C to exit)", YELLOW)
        
        # Sleep in small increments so Ctrl+C is responsive
        for _ in range(wait_time * 10):
            if _runner_stopping:
                log("Interrupted during backoff, exiting", YELLOW)
                sys.exit(0)
            time.sleep(0.1)


if __name__ == "__main__":
    main()