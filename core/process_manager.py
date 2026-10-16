import os
import subprocess
import signal
import concurrent.futures
import logging
import threading
import time
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

IS_WINDOWS = sys.platform == 'win32'


def _make_child_die_with_parent():
    """
    Preexec function for subprocess: sets up child to die when parent dies.
    On Linux, uses prctl(PR_SET_PDEATHSIG) to receive SIGTERM on parent death.
    Also creates new session for process group management.
    """
    if IS_WINDOWS:
        return
    os.setsid()
    if not IS_WINDOWS:
        try:
            import ctypes
            libc = ctypes.CDLL("libc.so.6", use_errno=True)
            PR_SET_PDEATHSIG = 1
            SIGTERM = 15
            libc.prctl(PR_SET_PDEATHSIG, SIGTERM)
        except Exception:
            pass  # Fall back to setsid only


# PR_SET_PDEATHSIG fires when the THREAD that forked exits, not the process.
# A service started from a web worker thread (anyio prunes idle workers after
# 10 s), an env-build callback or the extras pip thread got a spurious SIGTERM
# minutes later. Every spawn goes through this one thread, which lives as long
# as the process does (2026-10-16 scouts, tmp/single-instance-plan.md row 8).
_spawner = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix='spawner')


def _spawn(*args, **kwargs):
    """subprocess.Popen, from the long-lived spawner thread on Unix."""
    if IS_WINDOWS:
        return subprocess.Popen(*args, **kwargs)
    return _spawner.submit(subprocess.Popen, *args, **kwargs).result()


def kill_process_on_port(port: int, owner: str = None) -> bool:
    """Kill the process LISTENING on `port` when it is ours, never otherwise.

    Ours = its command line contains `owner` (the service script, or the
    Sapphire root for plugin services) and it is neither this process nor its
    parent. A stranger on the port is logged and left alone: the service then
    fails to bind and says so, instead of us shooting a neighbour. Needs
    psutil (always present with the voice stack); without it nothing is
    killed. The old netstat/fuser/lsof walk matched by port alone, read a
    localized Windows netstat, listed CLIENTS with lsof and could SIGTERM
    Sapphire herself (2026-10-16 scouts).
    """
    try:
        import psutil
    except ImportError:
        logger.warning(f"psutil missing: cannot tell who holds port {port}, leaving it alone")
        return False
    owner = owner or str(Path(__file__).absolute().parent.parent)
    me, parent = os.getpid(), os.getppid()
    try:
        conns = psutil.net_connections(kind='tcp')
    except Exception as e:
        logger.debug(f"net_connections failed: {e}")
        return False
    killed = False
    for c in conns:
        if c.status != psutil.CONN_LISTEN or not c.laddr or c.laddr.port != port or not c.pid:
            continue
        if c.pid in (me, parent):
            continue
        try:
            proc = psutil.Process(c.pid)
            cmd = ' '.join(proc.cmdline())
            name = proc.name()
        except Exception:
            continue                        # cannot identify it: cannot kill it
        if owner not in cmd:
            logger.warning(f"Port {port} is held by pid {c.pid} ({name}), not one of ours; leaving it alone")
            continue
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except psutil.TimeoutExpired:
            try:
                proc.kill()
            except Exception:
                pass
        except Exception:
            pass
        logger.info(f"Killed orphan process {c.pid} ({name}) on port {port}")
        killed = True
    return killed


class ProcessManager:
    """A generic class to manage the lifecycle of an external script process."""
    
    def __init__(self, script_path: Path, log_name: str, base_dir: Path, command_args: list = None,
                 env_callback=None, python_exe=None):
        """
        Initializes the ProcessManager.
        Args:
            script_path (Path): The path to the script to execute.
            log_name (str): The name for the log file.
            base_dir (Path): The project's base directory, for log file placement.
            command_args (list, optional): A list of command and arguments. If None, script_path is used.
            env_callback (callable, optional): Zero-arg callable returning a dict to
                pass as subprocess.Popen(env=...). Called fresh on every start()
                including monitor_and_restart-driven restarts, so plugins can
                inject settings-derived env vars that may have changed since the
                last spawn. If None, Popen inherits the parent process env
                (current default — unchanged behavior). Added 2026-04-21 to
                replace the ProcessManager.start() monkey-patch pattern used by
                qwen3-tts / f5-tts plugins.
            python_exe (str|Path, optional): Interpreter used for .py scripts.
                Defaults to sys.executable (the main Sapphire env). Plugin
                services with their own conda env pass that env's python here.
        """
        self.process = None
        self.script_path = script_path
        self.log_file = base_dir / "user" / "logs" / f"{log_name}.log"
        self.command = command_args or [str(self.script_path)]
        self._explicit_command = command_args is not None
        self._python_exe = str(python_exe) if python_exe else None
        self._monitor_thread = None
        self._monitor_running = False
        self._stop_event = threading.Event()    # wakes the monitor at once on stop()
        self._lock = threading.RLock()          # start/stop and the monitor's restart decision
        self._env_callback = env_callback

    def start(self):
        """Starts the external script."""
        # Prepend python interpreter for .py files (unless the caller gave an
        # explicit command — historically this branch silently clobbered
        # command_args for .py scripts).
        if self.script_path.suffix == '.py' and not self._explicit_command:
            self.command = [self._python_exe or sys.executable, str(self.script_path)]
        elif not self.script_path.exists():
            logger.error(f"Manager Error: Script not found at {self.script_path}")
            return False
        else:
            # Make shell scripts executable (Unix only, no-op on Windows)
            if not IS_WINDOWS:
                try:
                    os.chmod(self.script_path, 0o755)
                except OSError as e:
                    logger.warning(f"Could not set executable bit on {self.script_path}: {e}")

        logger.info(f"Starting Process: {' '.join(self.command)}")
        logger.info(f"Logs will be written to: {self.log_file}")

        self.log_file.parent.mkdir(parents=True, exist_ok=True)

        try:
            # Fresh env each spawn if a callback is registered. Lets plugins
            # (qwen3-tts, f5-tts) pass current settings-derived env vars on
            # every restart without monkey-patching start().
            spawn_env = None
            if self._env_callback is not None:
                try:
                    spawn_env = self._env_callback()
                except Exception as e:
                    logger.warning(f"env_callback raised for {self.script_path.name}: {e} — spawning with parent env")
                    spawn_env = None

            # Truncate on restart rather than append forever. The process gets
            # restarted at each Sapphire boot — there's no long-lived log needed,
            # and the append path was growing the file unboundedly (Scout 1
            # finding 2026-04-19, measured 2.1MB already, linear per-utterance).
            # BUT keep ONE previous generation: truncate-per-spawn erased the
            # crash traceback the respawn was reacting to — the evidence
            # self-destructed on a 12-second timer during crash loops
            # (X1 F6 / negspace N21, 2026-08-31).
            try:
                if self.log_file.exists() and self.log_file.stat().st_size > 0:
                    self.log_file.replace(self.log_file.with_name(self.log_file.name + ".prev"))
            except OSError:
                pass
            with open(self.log_file, "w") as log:
                if IS_WINDOWS:
                    # Windows: no process groups, just start the process
                    self.process = _spawn(
                        self.command,
                        stdout=log,
                        stderr=log,
                        env=spawn_env
                    )
                else:
                    # Unix: new session + die-with-parent, from the spawner thread
                    self.process = _spawn(
                        self.command,
                        stdout=log,
                        stderr=log,
                        preexec_fn=_make_child_die_with_parent,
                        env=spawn_env
                    )
            
            logger.info(f"Process for '{self.script_path.name}' started with PID: {self.process.pid}")
            return True
        except FileNotFoundError:
            logger.error(f"Manager Error: Command not found for '{self.script_path.name}'.")
            return False
        except Exception as e:
            logger.error(f"Manager Error: Unexpected error starting '{self.script_path.name}': {e}", exc_info=True)
            return False

    def stop(self):
        """Stops the external script process and monitoring.

        The monitor is told first and joined, so it cannot respawn the child we
        are about to kill: that race was live, stop() returned and a fresh child
        appeared two seconds later (2026-10-16 scouts, row 10)."""
        with self._lock:
            self._monitor_running = False
            self._stop_event.set()
            monitor = self._monitor_thread
        if monitor is not None and monitor.is_alive() and monitor is not threading.current_thread():
            monitor.join(timeout=5)
        with self._lock:
            self._stop_process()

    def _stop_process(self):
        if self.process and self.process.poll() is None:
            if IS_WINDOWS:
                # Windows: terminate then kill if needed
                logger.info(f"Stopping process '{self.script_path.name}' (PID: {self.process.pid})...")
                try:
                    self.process.terminate()
                    self.process.wait(timeout=10)
                    logger.info(f"Process '{self.script_path.name}' stopped successfully.")
                except subprocess.TimeoutExpired:
                    logger.warning(f"Process '{self.script_path.name}' did not terminate gracefully, forcing kill.")
                    self.process.kill()
                    try:
                        self.process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        logger.error(f"Process '{self.script_path.name}' could not be killed.")
            else:
                # Unix: kill entire process group
                try:
                    pgid = os.getpgid(self.process.pid)
                except ProcessLookupError:
                    logger.info(f"Process '{self.script_path.name}' ended on its own.")
                    return
                logger.info(f"Stopping process group for '{self.script_path.name}' (PGID: {pgid})...")
                try:
                    os.killpg(pgid, signal.SIGTERM)
                    self.process.wait(timeout=10)
                    logger.info(f"Process '{self.script_path.name}' stopped successfully.")
                except (subprocess.TimeoutExpired, ProcessLookupError):
                    logger.warning(f"Process '{self.script_path.name}' did not terminate gracefully, sending SIGKILL.")
                    try:
                        os.killpg(os.getpgid(self.process.pid), signal.SIGKILL)
                    except ProcessLookupError:
                        logger.warning(f"Process group for '{self.script_path.name}' not found for SIGKILL.")
        else:
            logger.info(f"Process for '{self.script_path.name}' not running or already stopped.")

    def is_running(self) -> bool:
        """Check if process is currently running."""
        return self.process is not None and self.process.poll() is None

    def monitor_and_restart(self, check_interval: int = 10):
        """
        Start background thread that restarts process if it dies.
        
        Args:
            check_interval: Seconds between health checks
        """
        if self._monitor_thread is not None and self._monitor_thread.is_alive():
            logger.warning(f"Monitor already running for '{self.script_path.name}'")
            return
        
        self._monitor_running = True
        self._stop_event.clear()
        
        def _monitor():
            logger.info(f"Monitor started for '{self.script_path.name}' (interval: {check_interval}s)")
            retry_warns = 0  # throttle for the never-started branch (wave-3 A2)

            while self._monitor_running:
                if self._stop_event.wait(check_interval):
                    break
                
                if self.process is None or self.process.poll() is not None:
                    if self.process is not None:
                        exit_code = self.process.returncode
                        logger.info(f"Process '{self.script_path.name}' died (exit code {exit_code}), restarting...")
                    else:
                        # Initial start() failed -> self.process stayed None and
                        # the old `if self.process and ...` condition was False
                        # FOREVER: monitor alive, child never retried, TTS just
                        # silently off (X1 F5 / negspace N21, 2026-08-31).
                        # Throttled: a permanently-failing service would
                        # otherwise WARN every interval (~5,700/day). First 3,
                        # then every 20th — still visible, no longer a firehose
                        # (wave-3 A2).
                        retry_warns += 1
                        if retry_warns <= 3 or retry_warns % 20 == 0:
                            logger.warning(f"Process '{self.script_path.name}' has no live process "
                                           f"(initial start failed?) — retrying start "
                                           f"(attempt {retry_warns})...")

                    # Brief delay before restart; stop() wakes us instead of waiting it out
                    if self._stop_event.wait(2):
                        break

                    # Under the lock: stop() cannot slip between this check and the spawn
                    with self._lock:
                        if self._monitor_running and self.start():
                            retry_warns = 0  # healthy again — reset throttle
            
            logger.info(f"Monitor stopped for '{self.script_path.name}'")
        
        self._monitor_thread = threading.Thread(
            target=_monitor,
            daemon=True,
            name=f"Monitor-{self.script_path.name}"
        )
        self._monitor_thread.start()