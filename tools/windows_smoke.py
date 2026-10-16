#!/usr/bin/env python
"""Smoke test for the single-instance work of 2026-10-16, for a Windows box or
VM (no GPU needed). It starts Sapphire itself, the way the launcher does, and
checks what only a real boot can show. Linux runs the same steps minus the two
that are Windows-only.

    tools\\windows_smoke.bat                 double-click: finds miniconda3\\envs\\sapphire
    python tools\\windows_smoke.py           from an activated sapphire env
    python tools\\windows_smoke.py --install also runs the GPU torch installer with a
                                            forced index (a 2-3 GB download, asks for
                                            the web password to press the button)
    python tools\\windows_smoke.py --pythonw also boots once under pythonw.exe

Stop Sapphire first (launcher Stop, or taskkill): the script starts its own,
several times, into the same user/. Logs: user/logs/windows_smoke/. Exit code
is the number of failed checks. Every line printed here is plain ASCII.
"""
import argparse
import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).absolute().parent.parent
WIN = sys.platform == 'win32'
PY = sys.executable
LOGDIR = ROOT / 'user' / 'logs' / 'windows_smoke'
MARKER = ROOT / 'user' / 'logs' / 'startup_errors.log'
RESULT_FILE = ROOT / 'user' / 'last_gpu_install.json'
RESULTS = []


def say(msg):
    print(msg, flush=True)


def record(name, ok, detail=''):
    RESULTS.append((name, ok, detail))
    tag = 'PASS' if ok is True else ('FAIL' if ok is False else 'SKIP')
    say(f"  [{tag}] {name}" + (f": {detail}" if detail else ''))


def settings():
    try:
        s = json.loads((ROOT / 'user' / 'settings.json').read_text(encoding='utf-8'))
    except Exception:
        s = {}
    port = int(s.get('WEB_UI_PORT', 8073) or 8073)
    tts = int(s.get('TTS_SERVER_PORT', 5012) or 5012)
    scheme = 'https' if s.get('WEB_UI_SSL_ADHOC', True) else 'http'
    return port, tts, scheme


def http_ok(url, timeout=3):
    import requests
    try:
        r = requests.get(url, verify=False, timeout=timeout)
        return r.ok and 'ok' in r.text
    except Exception:
        return False


def wait_health(base, timeout):
    end = time.time() + timeout
    while time.time() < end:
        if http_ok(f"{base}/api/health"):
            return True
        time.sleep(1)
    return False


def start_runner(tag, extra_env=None, exe=None):
    """python main.py in its own process group, output to user/logs/windows_smoke/<tag>.log."""
    LOGDIR.mkdir(parents=True, exist_ok=True)
    log = open(LOGDIR / f"{tag}.log", 'w', encoding='utf-8', errors='replace')
    env = dict(os.environ, **(extra_env or {}))
    kw = {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP} if WIN else {'preexec_fn': os.setsid}
    proc = subprocess.Popen([exe or PY, 'main.py'], cwd=str(ROOT), stdout=log, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, env=env, **kw)
    return proc, log


def child_pids(pid):
    import psutil
    try:
        return [c.pid for c in psutil.Process(pid).children(recursive=True)]
    except Exception:
        return []


def wait_gone(pids, timeout):
    import psutil
    end = time.time() + timeout
    while time.time() < end:
        left = [p for p in pids if psutil.pid_exists(p)]
        if not left:
            return True
        time.sleep(0.5)
    return False


def kill_tree(proc):
    import psutil
    for pid in child_pids(proc.pid):
        try:
            psutil.Process(pid).kill()
        except Exception:
            pass
    try:
        proc.kill()
    except Exception:
        pass


def graceful_stop(proc, timeout=120):
    """The launcher contract: CTRL_BREAK to the runner's group on Windows, SIGTERM on posix.
    Returns the exit code, or None when it did not end in time."""
    if WIN:
        os.kill(proc.pid, signal.CTRL_BREAK_EVENT)
    else:
        proc.send_signal(signal.SIGTERM)
    try:
        return proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        return None


def count_markers():
    try:
        return MARKER.read_text(encoding='utf-8', errors='replace').count('--- Startup attempt')
    except OSError:
        return 0


def port_refuses_us(port):
    """True when a bind with SO_REUSEADDR fails: the holder has it exclusively.
    The old Kokoro server let a second bind through (two Kokoros on one port)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(('0.0.0.0', port))
        s.listen(1)
        return False
    except OSError:
        return True
    finally:
        s.close()


def second_launch(script):
    r = subprocess.run([PY, script], cwd=str(ROOT), capture_output=True, text=True,
                       encoding='utf-8', errors='replace', timeout=120)
    return r.returncode, (r.stdout or '') + (r.stderr or '')


def login_session(base, password):
    """The two-stage login the UI does. Returns (session, csrf) or (None, '')."""
    import requests
    s = requests.Session()
    s.verify = False
    page = s.get(f"{base}/login", timeout=10).text
    m = re.search(r'name="csrf_token"\s+value="([^"]+)"', page)
    s.post(f"{base}/login", data={'password': password, 'csrf_token': m.group(1) if m else ''}, timeout=10)
    home = s.get(f"{base}/", timeout=10).text
    m = re.search(r'name="csrf-token"\s+content="([^"]+)"', home)
    if not m:
        return None, ''
    return s, m.group(1)


def install_test(args, base):
    import getpass
    pw = ''
    if not args.token_file:
        pw = args.password or getpass.getpass('Sapphire web password (the script presses the install button): ')
    runner, log = start_runner('install', extra_env={'SAPPHIRE_GPU_INDEX': args.index})
    if not wait_health(base, 300):
        record('installer: boot with the forced index', False, f"no answer in 300 s; see {LOGDIR / 'install.log'}")
        kill_tree(runner)
        log.close()
        return
    if args.token_file:
        import requests
        s = requests.Session()
        s.verify = False
        s.headers['Authorization'] = 'Bearer ' + Path(args.token_file).read_text(encoding='utf-8').strip()
        csrf = ''
    else:
        s, csrf = login_session(base, pw)
    if s is None:
        record('installer: login', False, 'wrong password, or the login page changed')
        graceful_stop(runner)
        log.close()
        return
    g = s.get(f"{base}/api/system/gpu", timeout=10).json()
    record(f"installer: offer forced to {args.index}", (g.get('install') or {}).get('index') == args.index,
           json.dumps(g.get('install')))
    mtime0 = RESULT_FILE.stat().st_mtime if RESULT_FILE.exists() else 0
    r = s.post(f"{base}/api/system/gpu/install", headers={'X-CSRF-Token': csrf}, timeout=10)
    record('installer: button accepted (queued + restart)', r.status_code == 200, f"http {r.status_code} {r.text[:80]}")
    if r.status_code != 200:
        graceful_stop(runner)
        log.close()
        return
    say('  waiting for the runner window to install (2-3 GB download; up to 45 min)...')
    end = time.time() + 2700
    res = None
    while time.time() < end:
        if RESULT_FILE.exists() and RESULT_FILE.stat().st_mtime > mtime0:
            try:
                res = json.loads(RESULT_FILE.read_text(encoding='utf-8'))
            except Exception:
                res = {'ok': False, 'message': 'result file unreadable'}
            break
        if runner.poll() is not None:
            break
        time.sleep(5)
    record('installer: pip finished and wrote its result', bool(res and res.get('ok')),
           json.dumps(res) if res else f"no result; runner exit {runner.poll()}; see {LOGDIR / 'install.log'}")
    record('installer: Sapphire booted after the install', wait_health(base, 300))
    chk = subprocess.run([PY, '-c', 'import torch; print(torch.__version__, torch.version.cuda)'],
                         capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=300)
    want = {'cu126': '12.6', 'cu128': '12.8'}[args.index]
    record(f"installer: the env's torch now has CUDA {want}", want in (chk.stdout or ''),
           (chk.stdout or '').strip() or (chk.stderr or '').strip()[-120:])
    code = graceful_stop(runner)
    record('installer: graceful stop afterwards exits 0', code == 0, f"exit {code}")
    if code is None:
        kill_tree(runner)
    log.close()


def summary():
    fails = [n for n, ok, _ in RESULTS if ok is False]
    say('')
    say('=' * 64)
    for name, ok, detail in RESULTS:
        tag = 'PASS' if ok is True else ('FAIL' if ok is False else 'SKIP')
        say(f"  {tag:4}  {name}")
    say('=' * 64)
    say(f"  {len(RESULTS) - len(fails)} of {len(RESULTS)} passed; logs in {LOGDIR}")
    return len(fails)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--install', action='store_true', help='also run the GPU torch installer (2-3 GB download)')
    ap.add_argument('--index', default='cu126', choices=('cu126', 'cu128'),
                    help='index to force for --install (cu126 installs fine on a box without a card)')
    ap.add_argument('--pythonw', action='store_true', help='also boot once under pythonw.exe (Windows)')
    ap.add_argument('--password', help='web password for --install (asked for when missing)')
    ap.add_argument('--token-file', help='an API bearer token file instead of the password (user/sapphire-health.token)')
    args = ap.parse_args()

    try:
        import urllib3
        urllib3.disable_warnings()
    except Exception:
        pass
    try:
        import psutil, requests  # noqa: F401
    except ImportError as e:
        say(f"Run this from the sapphire env (python {PY} lacks {e.name}).")
        return 2

    port, tts, scheme = settings()
    base = f"{scheme}://localhost:{port}"
    say(f"Sapphire smoke test on {sys.platform}; root {ROOT}; {base}; python {PY}")
    if http_ok(f"{base}/api/health"):
        say(f"Sapphire is already answering at {base}. Stop it first (launcher Stop, or taskkill), then run this again.")
        return 2

    # 1. boot
    markers0 = count_markers()
    t0 = time.time()
    runner, log = start_runner('boot1')
    up = wait_health(base, 300)
    record('boots and answers /api/health', up,
           f"{time.time() - t0:.0f} s" if up else f"no answer in 300 s, runner exit {runner.poll()}; see {LOGDIR / 'boot1.log'}")
    if not up:
        kill_tree(runner)
        log.close()
        return summary()
    time.sleep(3)
    record('startup_errors.log gained one flushed marker', count_markers() == markers0 + 1, f"{markers0} -> {count_markers()}")

    # 2. the twin, both doors
    code, out = second_launch('main.py')
    record('second python main.py exits 43 naming the holder', code == 43 and 'Another Sapphire' in out, f"exit {code}")
    code, out = second_launch('sapphire.py')
    record('second python sapphire.py exits 43', code == 43 and 'Another Sapphire' in out, f"exit {code}")

    # 3. ports held exclusively
    record(f"web port {port} refuses a second bind", port_refuses_us(port))
    if http_ok(f"http://127.0.0.1:{tts}/health", timeout=5):
        record(f"Kokoro port {tts} refuses a second bind (no co-bind)", port_refuses_us(tts))
    else:
        record(f"Kokoro port {tts} refuses a second bind", None, 'Kokoro not answering (TTS off, or still loading)')

    # 4. graceful stop, the launcher contract
    kids = child_pids(runner.pid)
    t0 = time.time()
    code = graceful_stop(runner)
    record('graceful stop (CTRL_BREAK on Windows, SIGTERM elsewhere) exits 0', code == 0, f"exit {code} in {time.time() - t0:.0f} s")
    record('sapphire.py and Kokoro are gone after the graceful stop', wait_gone(kids, 20), f"{len(kids)} child pids")
    if code is None:
        kill_tree(runner)
    log.close()

    # 5. a hard kill of the runner takes the children with it (Job Object)
    if WIN:
        runner, log = start_runner('boot2')
        if wait_health(base, 300):
            time.sleep(3)
            kids = child_pids(runner.pid)
            subprocess.run(['taskkill', '/F', '/PID', str(runner.pid)], capture_output=True)
            try:
                runner.wait(timeout=30)
            except subprocess.TimeoutExpired:
                pass
            record('hard kill of the runner alone takes sapphire.py and Kokoro with it (Job Object)',
                   wait_gone(kids, 15), f"{len(kids)} child pids")
            if not wait_gone(kids, 1):
                import psutil
                for pid in kids:
                    try:
                        psutil.Process(pid).kill()
                    except Exception:
                        pass
        else:
            record('Job Object', None, f"second boot did not come up; see {LOGDIR / 'boot2.log'}")
            kill_tree(runner)
        log.close()
    else:
        record('Job Object (Windows only)', None, 'not Windows')

    # 6. pythonw
    if args.pythonw and WIN:
        pw = Path(PY).with_name('pythonw.exe')
        if pw.exists():
            runner, log = start_runner('pythonw', exe=str(pw))
            up = wait_health(base, 300)
            record('boots under pythonw.exe (no stderr: the faulthandler guard)', up,
                   '' if up else f"runner exit {runner.poll()}; see {LOGDIR / 'pythonw.log'}")
            kids = child_pids(runner.pid)
            subprocess.run(['taskkill', '/F', '/T', '/PID', str(runner.pid)], capture_output=True)
            try:
                runner.wait(timeout=30)
            except subprocess.TimeoutExpired:
                pass
            wait_gone(kids, 15)
            log.close()
        else:
            record('pythonw boot', None, f"{pw} not found")
    elif args.pythonw:
        record('pythonw boot (Windows only)', None, 'not Windows')

    # 7. the GPU torch installer, forced index
    if args.install:
        install_test(args, base)

    return summary()


if __name__ == '__main__':
    sys.exit(main())
