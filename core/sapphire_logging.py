import os
import sys
import faulthandler
import logging
import shutil
from pathlib import Path
from logging.handlers import TimedRotatingFileHandler

# Dump Python traceback on SIGSEGV/SIGFPE/SIGABRT to stderr
faulthandler.enable()

# Resolve project root from this file's location (core/sapphire_logging.py -> project root)
_BASE_DIR = Path(__file__).parent.parent.resolve()

# Early stderr capture - ensures ANY errors get logged
_startup_log = None
try:
    os.makedirs(_BASE_DIR / 'user' / 'logs', exist_ok=True)
    _startup_log = open(_BASE_DIR / 'user' / 'logs' / 'startup_errors.log', 'a', encoding='utf-8')
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
    _BASE_DIR / 'user' / 'logs',
    _BASE_DIR / 'user' / 'history',
    _BASE_DIR / 'user' / 'public' / 'avatars',
    _BASE_DIR / 'user' / 'plugins',
    _BASE_DIR / 'user' / 'plugin_state',
    _BASE_DIR / 'user' / 'webui' / 'plugins',
    _BASE_DIR / 'user' / 'continuity',
    _BASE_DIR / 'user' / 'ssl',
    _BASE_DIR / 'user' / 'prompts',
    _BASE_DIR / 'user' / 'toolsets',
    _BASE_DIR / 'user' / 'personas',
    _BASE_DIR / 'user' / 'spice_sets',
    _BASE_DIR / 'user' / 'story_engine',
]
try:
    for d in _USER_DIRS:
        os.makedirs(d, exist_ok=True)
except Exception as e:
    _log_startup_error(f"Failed to create user dirs: {e}")

# Copy default avatars if none exist in user dir
def _init_avatars():
    avatar_dir = _BASE_DIR / 'user' / 'public' / 'avatars'
    static_dir = _BASE_DIR / 'interfaces' / 'web' / 'static' / 'users'

    # Check if ANY avatar already exists (any format)
    for role in ('user', 'assistant'):
        for ext in ('.webp', '.jpg', '.png'):
            if (avatar_dir / f'{role}{ext}').exists():
                return  # Already have avatars, don't overwrite

    # Copy defaults - prefer webp > jpg > png
    if not static_dir.is_dir():
        return

    for role in ('user', 'assistant'):
        for ext in ('.webp', '.jpg', '.png'):
            src = static_dir / f'{role}{ext}'
            if src.exists():
                dst = avatar_dir / f'{role}{ext}'
                try:
                    shutil.copy2(src, dst)
                except Exception:
                    pass
                break  # Only copy one format per role

_init_avatars()  

# Configure file handler with daily rotation
file_handler = TimedRotatingFileHandler(
    str(_BASE_DIR / 'user' / 'logs' / 'sapphire.log'),
    when='midnight',
    interval=1,
    backupCount=30
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

# Add both handlers
root_logger.addHandler(file_handler)
root_logger.addHandler(console_handler)

# Quiet down noisy loggers
logging.getLogger('uvicorn.access').setLevel(logging.WARNING)

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