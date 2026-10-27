# core/plugins/browser/chromium.py - Chromium itself, fetched on first use
#
# `pip install playwright` (the plugin's pip_dependencies) brings the driver
# but not a browser. The first launch that finds no Chromium starts one
# download (full Chromium only, no separate headless shell: ~370 MB on disk
# instead of ~630, in Playwright's own cache: ~/.cache/ms-playwright or
# %LOCALAPPDATA%\ms-playwright; engine launches channel='chromium') on a
# thread, with a notification-center row, and the call gets words to try
# again. Linux system libraries need root, so those get the command, never sudo.
import logging
import os
import subprocess
import sys
import threading

logger = logging.getLogger(__name__)

SOURCE = 'browser'
WAIT = 1800                # seconds the download may take
_lock = threading.Lock()
_job = None                # the running download thread
failed = ''                # why the last download failed, until one succeeds


def kind(err):
    """'missing' (no Chromium), 'libs' (Linux lacks its libraries) or None."""
    text = str(err)
    if 'install-deps' in text or 'missing dependencies' in text:
        return 'libs'
    if "Executable doesn't exist" in text or 'playwright install' in text:
        return 'missing'
    return None


def explain(err):
    """Words for a launch failure that is about Chromium being absent or
    unable to run here (and the fix set going), or None for anything else."""
    what = kind(err)
    if what == 'missing':
        if _managed():
            return "Chromium isn't installed, and this Sapphire is hosted: installs are up to its host."
        before = failed
        start()
        if before:
            return (f"Chromium's last download failed ({before}); trying again now. "
                    "Try the browser again in a minute or two.")
        return ("The browser's first run: Chromium (about 200 MB) is downloading now. "
                "Try again in a minute or two; the notification center shows how it's going.")
    if what == 'libs':
        cmd = f"sudo {sys.executable} -m playwright install-deps chromium"
        try:
            from core import notify
            notify.notify("The browser needs system libraries", 'warning', source=SOURCE, key='browser-deps', once=True,
                          body=f"Chromium is downloaded but this system lacks libraries it needs. Run once in a terminal:\n{cmd}")
        except Exception as e:
            logger.warning(f"[BROWSER] could not raise the libraries notice: {e}")
        return f"Chromium can't start: this system lacks libraries it needs. The fix is one terminal command for the user: {cmd}"
    return None


def start():
    """Begin the download unless one is running."""
    global _job
    with _lock:
        if _job and _job.is_alive():
            return
        _job = threading.Thread(target=_download, daemon=True, name='browser-chromium')
        _job.start()


def _download():
    global failed
    from core import notify
    rid = notify.begin("Downloading Chromium for the browser", source=SOURCE, key='browser-chromium',
                       body="About 200 MB to download, once.")
    cmd = [_python(), '-m', 'playwright', 'install', '--no-shell', 'chromium']
    logger.info(f"[BROWSER] downloading Chromium: {' '.join(cmd)}")
    last, proc, timer = '', None, None
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding='utf-8', errors='replace', bufsize=1,
                                env={**os.environ, 'PYTHONIOENCODING': 'utf-8'},
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                                start_new_session=sys.platform != 'win32')
        timer = threading.Timer(WAIT, _kill_tree, (proc,))   # a stalled download ends; reading stdout never times out
        timer.daemon = True
        timer.start()
        for line in proc.stdout:
            line = line.strip()
            if line:
                last = line
                notify.tick(rid, text=line[:200])
        code = proc.wait(30)
        if code and not timer.is_alive():
            last = f"the download stalled and was stopped after {WAIT // 60} minutes"
    except Exception as e:
        code, last = -1, str(e)
    finally:
        if timer:
            timer.cancel()
        if proc and proc.poll() is None:
            _kill_tree(proc)
            proc.wait()
    if code == 0:
        failed = ''
        logger.info("[BROWSER] Chromium downloaded")
        notify.finish(rid, title="Chromium is ready for the browser")
    else:
        failed = (last or f'exit {code}')[:200]
        logger.warning(f"[BROWSER] Chromium download failed: {failed}")
        notify.finish(rid, failed=True, body=failed)


def _kill_tree(proc):
    """The installer and its node child: node holds the output pipe, so
    killing python alone would leave the read waiting forever."""
    try:
        if sys.platform == 'win32':
            subprocess.run(['taskkill', '/F', '/T', '/PID', str(proc.pid)], capture_output=True,
                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), timeout=30)
        else:
            import signal
            os.killpg(proc.pid, signal.SIGKILL)        # its own session (start_new_session): the whole group
    except Exception as e:
        logger.warning(f"[BROWSER] stopping the Chromium download: {e}")
        proc.kill()


def _python():
    """The console python beside pythonw: run hidden (CREATE_NO_WINDOW), its
    children - Playwright's node - share that hidden console instead of each
    popping a black window of their own for the whole download."""
    exe = sys.executable
    if sys.platform == 'win32' and os.path.basename(exe).lower() == 'pythonw.exe':
        sibling = os.path.join(os.path.dirname(exe), 'python.exe')
        if os.path.exists(sibling):
            return sibling
    return exe


def _managed():
    try:
        from core.settings_manager import settings
        return bool(settings.is_managed())
    except Exception:
        return False
