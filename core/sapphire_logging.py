import os
import sys
import faulthandler
import logging
import shutil
from pathlib import Path
from logging.handlers import TimedRotatingFileHandler

# Anchor everything to the repo root — a launch from another CWD (Windows
# shortcut, service unit without WorkingDirectory) used to scatter a fresh
# user/ tree wherever the shell happened to be. Same anchor pattern as
# prompt_manager (no .resolve(): keep symlinked checkouts on their own path).
_ROOT = Path(__file__).parent.parent

# Reconfigure stdout/stderr to utf-8 with errors='replace' BEFORE any handler
# is attached. Without this, Windows cmd.exe defaults to cp1252 — a single
# em-dash / smart quote / emoji in any log line raises UnicodeEncodeError
# inside logging.emit() and crashes the chat turn or worker thread. With
# errors='replace', unmappable chars become '?' instead of crashing. Modern
# Win10/11 cmd (chcp 65001) renders the utf-8 correctly; older cmd shows
# mojibake but stays alive. Confirmed Win-cp1252 crash 2026-05-18 (Krem).
# Defensive: a captured / redirected stdout may not be a TextIOWrapper.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, ValueError):
        pass

# Dump Python traceback on SIGSEGV/SIGFPE/SIGABRT to stderr
faulthandler.enable()

# Pytest imports this module transitively (and test_log_level_hot_reload
# directly). Without a guard, the TEST process attaches the SAME
# user/logs/sapphire.log handler as the live service — test records
# interleave with production lines (2026-07-19: mock ExecCtx retries and a
# test traceback landed in the live log mid-drain-autopsy, and a stray
# traceback there can false-trip the health script's "zero errors since
# boot" check). Tests keep the console handler (pytest captures stdout);
# only the production log files are off-limits.
_UNDER_PYTEST = 'pytest' in sys.modules or 'PYTEST_CURRENT_TEST' in os.environ

# Early stderr capture - ensures ANY errors get logged
_startup_log = None
if not _UNDER_PYTEST:
    try:
        os.makedirs(_ROOT / 'user/logs', exist_ok=True)
        _startup_log = open(_ROOT / 'user/logs/startup_errors.log', 'a', encoding='utf-8')
        _startup_log.write(f"\n--- Startup attempt ---\n")
    except Exception:
        pass

def _log_startup_error(msg):
    """Log critical startup errors before main logging is ready."""
    if _startup_log:
        _startup_log.write(f"{msg}\n")
        _startup_log.flush()
    print(msg, file=sys.stderr)

# Ensure all user directories exist (covers both local and Docker first-boot)
_USER_DIRS = [
    'user/logs',
    'user/history',
    'user/public/avatars',
    'user/plugins',
    'user/plugin_state',
    'user/webui/plugins',
    'user/continuity',
    'user/ssl',
    'user/prompts',
    'user/toolsets',
    'user/personas',
    'user/spice_sets',
]
try:
    for d in _USER_DIRS:
        os.makedirs(_ROOT / d, exist_ok=True)
except Exception as e:
    _log_startup_error(f"Failed to create user dirs: {e}")

# Copy default avatars if none exist in user dir
def _init_avatars():
    avatar_dir = str(_ROOT / 'user/public/avatars')
    static_dir = str(_ROOT / 'interfaces/web/static/users')

    # Check if ANY avatar already exists (any format)
    for role in ('user', 'assistant'):
        for ext in ('.webp', '.jpg', '.png'):
            if os.path.exists(os.path.join(avatar_dir, f'{role}{ext}')):
                return  # Already have avatars, don't overwrite

    # Copy defaults - prefer webp > jpg > png
    if not os.path.isdir(static_dir):
        return

    for role in ('user', 'assistant'):
        for ext in ('.webp', '.jpg', '.png'):
            src = os.path.join(static_dir, f'{role}{ext}')
            if os.path.exists(src):
                dst = os.path.join(avatar_dir, f'{role}{ext}')
                try:
                    shutil.copy2(src, dst)
                except Exception:
                    pass
                break  # Only copy one format per role

_init_avatars()  

# Configure file handler with daily rotation.
# encoding='utf-8' is mandatory — without it, TimedRotatingFileHandler opens
# the logfile in the system default (cp1252 on Windows). Any unicode char
# (em-dash, smart quote, emoji) in a log record raises UnicodeEncodeError
# inside emit(), which logging.handleError() swallows but the message is
# lost. Companion fix to the stdout reconfigure above.
file_handler = None
if not _UNDER_PYTEST:
    file_handler = TimedRotatingFileHandler(
        _ROOT / 'user/logs/sapphire.log',
        when='midnight',
        interval=1,
        backupCount=30,
        encoding='utf-8',
    )
    file_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))

# Colored console formatter
class ColoredFormatter(logging.Formatter):
    """ANSI color formatter for terminal output."""
    COLORS = {
        logging.DEBUG:    '\033[90m',   # Light grey
        logging.INFO:     '\033[97m',   # White
        logging.WARNING:  '\033[93m',   # Yellow
        logging.ERROR:    '\033[91m',   # Red
        logging.CRITICAL: '\033[1;91m', # Bold red
    }
    RESET = '\033[0m'

    def format(self, record):
        color = self.COLORS.get(record.levelno, '')
        msg = super().format(record)
        return f"{color}{msg}{self.RESET}" if color else msg

# Console handler for terminal output
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(ColoredFormatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))

# Configure root logger
root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)

# Remove any existing handlers
for handler in root_logger.handlers[:]:
    root_logger.removeHandler(handler)

# Add both handlers (file only outside pytest — see _UNDER_PYTEST above)
if file_handler is not None:
    root_logger.addHandler(file_handler)
root_logger.addHandler(console_handler)

# Quiet down noisy third-party loggers. These flood with wire-level chatter —
# httpx/httpcore request bytes, telethon MTProto keepalive PINGs (~every 20-30s),
# discord gateway heartbeats — that drowns Sapphire's own diagnostics and, at
# volume on slow disks, turns logging into a syscall storm (every record is
# flushed to disk + stdout). WARNING still surfaces real errors (disconnects,
# auth failures). Pinned independently of the root level so they stay quiet even
# when the user flips LOG_LEVEL=DEBUG to debug Sapphire itself.
_NOISY_LOGGERS = ('uvicorn.access', 'telethon', 'httpx', 'httpcore', 'discord')

def _quiet_noisy_loggers():
    for _name in _NOISY_LOGGERS:
        logging.getLogger(_name).setLevel(logging.WARNING)

_quiet_noisy_loggers()


def set_log_level(level_name):
    """Hot-apply root logger level. Called on boot with config.LOG_LEVEL and
    via settings reload callback when the user changes the setting.

    The noisy third-party loggers (_NOISY_LOGGERS) stay pinned at WARNING
    regardless — their wire-level chatter bleeding back at DEBUG would drown the
    diagnostic content the user flipped to DEBUG to see."""
    name = (level_name or "INFO").upper()
    level = getattr(logging, name, None)
    if not isinstance(level, int):
        root_logger.warning(f"Unknown log level {level_name!r}; keeping current")
        return
    root_logger.setLevel(level)
    _quiet_noisy_loggers()
    root_logger.info(f"Log level set to {name}")

# Windows: asyncio ProactorEventLoop logs harmless ConnectionResetError on socket cleanup
# These are cosmetic — the response already completed successfully.
# The error appears in exc_info (not msg), so check the full formatted record.
if sys.platform == 'win32':
    class _WinAsyncioFilter(logging.Filter):
        _suppress = ('ConnectionResetError', '_ProactorBasePipeTransport', 'WinError 10054')
        def filter(self, record):
            msg = str(getattr(record, 'msg', ''))
            if any(s in msg for s in self._suppress):
                return False
            if record.exc_info:
                exc_text = str(record.exc_info[1]) if record.exc_info[1] else ''
                if any(s in exc_text for s in self._suppress):
                    return False
                exc_type = record.exc_info[0].__name__ if record.exc_info[0] else ''
                if any(s in exc_type for s in self._suppress):
                    return False
            return True
    logging.getLogger('asyncio').addFilter(_WinAsyncioFilter())

# Only redirect stdout/stderr when running as systemd service
# if os.environ.get('SYSTEMD_EXEC_PID'):
#     sys.stdout = open(os.devnull, 'w')
#     sys.stderr = open(os.devnull, 'w')