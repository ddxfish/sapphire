import os
import subprocess
import signal
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


def kill_process_on_port(port: int) -> bool:
    """
    Kill any process listening on the specified port.
    Returns True if a process was killed, False otherwise.
    """
    if IS_WINDOWS:
        try:
            result = subprocess.run(
                ['netstat', '-ano', '-p', 'TCP'],
                capture_output=True, text=True, timeout=5,
                encoding='utf-8', errors='replace',
            )
            for line in result.stdout.splitlines():
                if f':{port}' in line and 'LISTENING' in line:
                    pid = int(line.strip().split()[-1])
                    if pid > 0:
                        subprocess.run(['taskkill', '/F', '/PID', str(pid)], timeout=5)
                        logger.info(f"Killed orphan process {pid} on port {port}")
                        return True
        except Exception:
            pass
        return False

    try:
        # Find PID using fuser
        result = subprocess.run(
            ['fuser', f'{port}/tcp'],
            capture_output=True,
            text=True,
            timeout=5,
            encoding='utf-8', errors='replace',
        )
        if result.returncode == 0 and result.stdout.strip():
            pids = result.stdout.strip().split()
            for pid in pids:
                try:
                    pid_int = int(pid.strip())
                    os.kill(pid_int, signal.SIGTERM)
                    logger.info(f"Killed orphan process {pid_int} on port {port}")
                except (ValueError, ProcessLookupError):
                    pass
            time.sleep(0.5)  # Let it die
            return True
    except FileNotFoundError:
        # fuser not available, try lsof
        try:
            result = subprocess.run(
                ['lsof', '-ti', f':{port}'],
                capture_output=True,
                text=True,
                timeout=5,
                encoding='utf-8', errors='replace',
            )
            if result.returncode == 0 and result.stdout.strip():
                for pid in result.stdout.strip().split('\n'):
                    try:
                        pid_int = int(pid.strip())
                        os.kill(pid_int, signal.SIGTERM)
                        logger.info(f"Killed orphan process {pid_int} on port {port}")
                    except (ValueError, ProcessLookupError):
                        pass
                time.sleep(0.5)
                return True
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
    except subprocess.TimeoutExpired:
        pass

    return False


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
                    self.process = subprocess.Popen(
                        self.command,
                        stdout=log,
                        stderr=log,
                        env=spawn_env
                    )
                else:
                    # Unix: new session + die-with-parent for clean orphan handling
                    self.process = subprocess.Popen(
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
        """Stops the external script process and monitoring."""
        self._monitor_running = False
        
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
                logger.info(f"Stopping process group for '{self.script_path.name}' (PGID: {os.getpgid(self.process.pid)})...")
                try:
                    os.killpg(os.getpgid(self.process.pid), signal.SIGTERM)
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
        
        def _monitor():
            logger.info(f"Monitor started for '{self.script_path.name}' (interval: {check_interval}s)")
            retry_warns = 0  # throttle for the never-started branch (wave-3 A2)

            while self._monitor_running:
                time.sleep(check_interval)
                
                if not self._monitor_running:
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

                    # Brief delay before restart
                    time.sleep(2)
                    
                    if self._monitor_running:
                        if self.start():
                            retry_warns = 0  # healthy again — reset throttle
            
            logger.info(f"Monitor stopped for '{self.script_path.name}'")
        
        self._monitor_thread = threading.Thread(
            target=_monitor,
            daemon=True,
            name=f"Monitor-{self.script_path.name}"
        )
        self._monitor_thread.start()