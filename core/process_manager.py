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
        return False  # TODO: implement for Windows if needed

    try:
        # Find PID using fuser
        result = subprocess.run(
            ['fuser', f'{port}/tcp'],
            capture_output=True,
            text=True,
            timeout=5
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
                timeout=5
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
    
    def __init__(self, script_path: Path, log_name: str, base_dir: Path, command_args: list = None):
        """
        Initializes the ProcessManager.
        Args:
            script_path (Path): The path to the script to execute.
            log_name (str): The name for the log file.
            base_dir (Path): The project's base directory, for log file placement.
            command_args (list, optional): A list of command and arguments. If None, script_path is used.
        """
        self.process = None
        self.script_path = script_path
        self.log_file = base_dir / "user" / "logs" / f"{log_name}.log"
        self.command = command_args or [str(self.script_path)]
        self._monitor_thread = None
        self._monitor_running = False

    def start(self):
        """Starts the external script."""
        # Prepend python interpreter for .py files
        if self.script_path.suffix == '.py':
            self.command = [sys.executable, str(self.script_path)]
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
            with open(self.log_file, "a") as log:
                if IS_WINDOWS:
                    # Windows: no process groups, just start the process
                    self.process = subprocess.Popen(
                        self.command,
                        stdout=log,
                        stderr=log
                    )
                else:
                    # Unix: new session + die-with-parent for clean orphan handling
                    self.process = subprocess.Popen(
                        self.command,
                        stdout=log,
                        stderr=log,
                        preexec_fn=_make_child_die_with_parent
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
            
            while self._monitor_running:
                time.sleep(check_interval)
                
                if not self._monitor_running:
                    break
                
                if self.process and self.process.poll() is not None:
                    exit_code = self.process.returncode
                    logger.info(f"Process '{self.script_path.name}' died (exit code {exit_code}), restarting...")
                    
                    # Brief delay before restart
                    time.sleep(2)
                    
                    if self._monitor_running:
                        self.start()
            
            logger.info(f"Monitor stopped for '{self.script_path.name}'")
        
        self._monitor_thread = threading.Thread(
            target=_monitor,
            daemon=True,
            name=f"Monitor-{self.script_path.name}"
        )
        self._monitor_thread.start()