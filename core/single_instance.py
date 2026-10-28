"""One Sapphire per user/ directory, and one owner for the web port.

A second `python main.py` into the same user/ used to boot completely: run
the update window, kill the first instance's Kokoro, open the mic, arm the
schedulers, then idle forever as a headless twin, because uvicorn's bind
failure died inside a daemon thread with a swallowed SystemExit
(2026-10-16, tmp/single-instance-plan.md). Two guards, stdlib only (plus
core.setup's config-dir path) so the runner can take them before any core
import touches settings:

  InstanceLock   a kernel lock on a file in the config dir, one per user/
                 folder. It dies with the process, so a stale file never
                 blocks a boot. Never inside user/: on Windows the locked
                 byte refuses reads, which failed every backup (Errno 13),
                 and an open file there refuses the restore's folder swap.
  bind_listener  the web socket, bound in the main thread before the banner
                 and handed to uvicorn. Exclusive on Windows.

Exit codes the runner understands and never restarts:
  43  another Sapphire is running (lock or web port held)
  44  configuration (web host, port or certificate)
"""
import os
import socket
import sys
import time

EXIT_ANOTHER_INSTANCE = 43
EXIT_CONFIG = 44
ENV_HELD = 'SAPPHIRE_INSTANCE_LOCK'   # the runner sets it for its child: "I hold the lock"
HELD_V = 'v2:'      # ... as "v2:<pid>": a runner that holds the config-dir lock (2026-10-27)


def runner_holds_lock():
    """Whether this process's runner holds the instance lock for it. A
    runner started before the lock left user/ says a bare pid and holds only
    the old file, so its child must take the new lock itself; otherwise an
    in-app update left nothing guarding against a second launch (hunt 20)."""
    return os.environ.get(ENV_HELD, '').startswith(HELD_V)


def legacy_lock_held(root):
    """A runner from before 2026-10-27 is still up and holds user/.instance.lock
    (byte-locked on Windows): a restore cannot move user/ aside until it stops."""
    return bool(os.environ.get(ENV_HELD)) and not runner_holds_lock() and \
        os.path.exists(os.path.join(str(root), 'user', '.instance.lock'))


def lock_path(root):
    """The lock file for the user/ folder under `root`: in the config dir,
    named by that folder's real path, so two installs never share a lock and
    two names for one folder always do."""
    import hashlib
    from core.setup import get_config_dir
    user = os.path.normcase(os.path.realpath(os.path.join(str(root), 'user')))
    key = hashlib.sha256(user.encode('utf-8')).hexdigest()[:16]
    return os.path.join(str(get_config_dir()), 'locks', f'instance-{key}.lock')


class InstanceLock:
    """Byte 0 of the file carries the lock; the holder's note follows it, so
    a loser can read who holds it even on Windows, where a locked byte
    refuses reads from other processes."""

    def __init__(self, root):
        self._root = root
        self.path = lock_path(root)
        self._fh = None
        self.error = ''         # why there is no lock file at all (then nothing guards, but she boots)

    def acquire(self):
        """True when this process now holds the lock, False when another does.
        A lock file that cannot be made at all (a read-only config dir) is True
        with `error` set: the guard is lost, never the boot (hunt 20)."""
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            fh = open(self.path, 'a+b')          # never truncates another holder's note
        except OSError as e:
            self.error = f"{self.path}: {e}"
            return True
        try:
            if sys.platform == 'win32':
                import msvcrt
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            return False
        self._fh = fh
        try:
            os.remove(os.path.join(str(self._root), 'user', '.instance.lock'))   # where it lived before 2026-10-27
        except OSError:
            pass
        try:
            fh.seek(0)
            fh.truncate()
            fh.write(b'\n' + f"pid={os.getpid()} since={time.strftime('%Y-%m-%d %H:%M:%S')}\n".encode())
            fh.flush()
        except OSError:
            pass
        return True

    def holder(self):
        """The note the holder left, or '' when there is none to read."""
        try:
            with open(self.path, 'rb') as f:
                f.seek(1)
                return ' '.join(f.read().decode('utf-8', 'replace').split())
        except OSError:
            return ''

    def release(self):
        if not self._fh:
            return
        try:
            if sys.platform == 'win32':
                import msvcrt
                self._fh.seek(0)
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        self._fh.close()
        self._fh = None


def acquire_or_exit(root, who, log=print):
    """Take the instance lock, or exit 43 with one line that names the holder."""
    lock = InstanceLock(root)
    if lock.acquire():
        if lock.error:
            log(f"Warning: the instance lock could not be made ({lock.error}). {who} starts without "
                f"the guard against a second Sapphire on this user/ folder. Check that the config "
                f"folder can be written.")
        return lock
    log(f"Another Sapphire is already running on this user/ ({lock.holder() or 'holder unknown'}). "
        f"{who} will not start a second one. Stop the other first (launcher Stop, Task Manager, systemctl).")
    sys.exit(EXIT_ANOTHER_INSTANCE)


def bind_listener(host, port):
    """The web socket, bound now so a taken port is a loud exit in the main
    thread instead of a daemon-thread death nobody sees. Exclusive on Windows,
    where SO_REUSEADDR would let two servers share the port; reusable on posix
    so a restart does not trip over TIME_WAIT. Raises OSError, errno set."""
    family = socket.AF_INET6 if ':' in (host or '') else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    try:
        if sys.platform == 'win32':
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))
        sock.listen(2048)       # bound alone excludes nobody on Linux; listening does
    except OSError:
        sock.close()
        raise
    return sock


def is_port_taken(err):
    """Whether a bind OSError means 'someone listens there' (not a bad host or a privileged port)."""
    import errno
    return err.errno in (errno.EADDRINUSE, 10048) or getattr(err, 'winerror', None) == 10048


def tie_children_to_me(log=print):
    """Windows: put this process in a Job Object that kills every member when
    its last handle closes. Children (sapphire.py, Kokoro, plugin services)
    join the job as they are created, so nothing outlives the runner: Windows
    has no die-with-parent otherwise, and a launcher close or a Task Manager
    kill of the runner used to leave a headless Sapphire behind (row 9).
    Returns the job handle (keep it alive) or None. Never raises."""
    if sys.platform != 'win32':
        return None
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.windll.kernel32
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        k32.GetCurrentProcess.restype = wintypes.HANDLE

        class Basic(ctypes.Structure):
            _fields_ = [('PerProcessUserTimeLimit', ctypes.c_int64), ('PerJobUserTimeLimit', ctypes.c_int64),
                        ('LimitFlags', wintypes.DWORD), ('MinimumWorkingSetSize', ctypes.c_size_t),
                        ('MaximumWorkingSetSize', ctypes.c_size_t), ('ActiveProcessLimit', wintypes.DWORD),
                        ('Affinity', ctypes.c_size_t), ('PriorityClass', wintypes.DWORD),
                        ('SchedulingClass', wintypes.DWORD)]

        class Io(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint64) for n in ('ReadOperationCount', 'WriteOperationCount',
                                                       'OtherOperationCount', 'ReadTransferCount',
                                                       'WriteTransferCount', 'OtherTransferCount')]

        class Extended(ctypes.Structure):
            _fields_ = [('BasicLimitInformation', Basic), ('IoInfo', Io), ('ProcessMemoryLimit', ctypes.c_size_t),
                        ('JobMemoryLimit', ctypes.c_size_t), ('PeakProcessMemoryUsed', ctypes.c_size_t),
                        ('PeakJobMemoryUsed', ctypes.c_size_t)]

        job = k32.CreateJobObjectW(None, None)
        if not job:
            raise ctypes.WinError()
        info = Extended()
        info.BasicLimitInformation.LimitFlags = 0x2000              # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):   # ExtendedLimitInformation
            raise ctypes.WinError()
        if not k32.AssignProcessToJobObject(job, k32.GetCurrentProcess()):
            raise ctypes.WinError()
        return job
    except Exception as e:
        log(f"Could not tie child processes to this runner (Job Object): {e}")
        return None


def bind_when_free(host, port, wait=90, log=print):
    """bind_listener, waiting while the port is taken but nobody listens on
    it: Windows' exclusive bind refuses the port until the last connection of
    the previous run is fully closed (a phone or satellite that dropped off
    WiFi), so a restart used to exit 43 and stay down, blaming a second
    Sapphire that did not exist (hunt 20). A real listener raises at once."""
    deadline = time.monotonic() + wait
    told = False
    while True:
        try:
            return bind_listener(host, port)
        except OSError as e:
            if not is_port_taken(e) or port_holder(port) or time.monotonic() > deadline:
                raise
            if not told:
                log(f"Port {port} is still held by the last run's connections; waiting up to {wait} s for it.")
                told = True
            time.sleep(2)


def port_holder(port):
    """'pid N (name)' for the listener on `port`, or '' when unknown. Best effort."""
    try:
        import psutil
        for c in psutil.net_connections(kind='tcp'):
            if c.status == psutil.CONN_LISTEN and c.laddr and c.laddr.port == port and c.pid:
                try:
                    name = psutil.Process(c.pid).name()
                except Exception:
                    name = '?'
                return f"pid {c.pid} ({name})"
    except Exception:
        pass
    return ''
