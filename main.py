#!/usr/bin/env python3
# main.py - Sapphire Runner
# Manages sapphire.py lifecycle with restart support
#
# Exit codes from sapphire.py:
#   0  = Clean shutdown
#   42 = Restart requested
#   *  = Crash/error
#
# Signal exit codes (treated as clean shutdown):
#   -2, 130  = SIGINT (Ctrl+C)
#   -15, 143 = SIGTERM
#   -1, 129  = SIGHUP

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

# Exit codes that mean "user requested stop" not "crash"
CLEAN_EXIT_CODES = {
    0,          # Normal exit
    -2, 130,    # SIGINT (Ctrl+C) - negative on Unix, 128+sig on some systems
    -15, 143,   # SIGTERM
    -1, 129,    # SIGHUP
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
            _child_process.send_signal(signum)
        except (ProcessLookupError, OSError):
            pass  # Child already dead
    

def run_sapphire():
    """Run sapphire.py and return its exit code."""
    global _child_process
    
    script_path = Path(__file__).parent / "sapphire.py"
    
    if not script_path.exists():
        log(f"ERROR: sapphire.py not found at {script_path}", RED)
        return 1
    
    try:
        _child_process = subprocess.Popen(
            [sys.executable, str(script_path)],
            stdin=sys.stdin,
            stdout=sys.stdout,
            stderr=sys.stderr
        )
        
        # Wait indefinitely - signals are forwarded via handle_signal(), so Ctrl+C
        # will reach the child. No timeout/watchdog needed; if child hangs, user
        # can still Ctrl+C which sets _runner_stopping and forwards SIGINT.
        _child_process.wait()
        exit_code = _child_process.returncode
        _child_process = None
        return exit_code
        
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
    
    log("Sapphire Runner starting", GREEN)
    
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
        consecutive_crashes += 1
        log(f"Crashed with exit code {exit_code} (crash {consecutive_crashes}/{args.max_crashes})", RED)
        
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