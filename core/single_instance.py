"""One Sapphire per user/ directory, and one owner for the web port.

A second `python main.py` into the same user/ used to boot completely: run
the update window, kill the first instance's Kokoro, open the mic, arm the
schedulers, then idle forever as a headless twin, because uvicorn's bind
failure died inside a daemon thread with a swallowed SystemExit
(2026-10-16, tmp/single-instance-plan.md). Two guards, stdlib only so the
runner can take them before any core import touches settings:

  InstanceLock   a kernel lock on user/.instance.lock. It dies with the
                 process, so a stale file never blocks a boot.
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


class InstanceLock:
    """Byte 0 of the file carries the lock; the holder's note follows it, so
    a loser can read who holds it even on Windows, where a locked byte
    refuses reads from other processes."""

    def __init__(self, root):
        self.path = os.path.join(str(root), 'user', '.instance.lock')
        self._fh = None

    def acquire(self):
        """True when this process now holds the lock, False when another does."""
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        fh = open(self.path, 'a+b')          # never truncates another holder's note
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
