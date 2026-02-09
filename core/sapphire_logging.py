import os
import sys
import faulthandler
import logging
import shutil
from logging.handlers import TimedRotatingFileHandler

# Dump Python traceback on SIGSEGV/SIGFPE/SIGABRT to stderr
faulthandler.enable()

# Early stderr capture - ensures ANY errors get logged
_startup_log = None
try:
    os.makedirs('user/logs', exist_ok=True)
    _startup_log = open('user/logs/startup_errors.log', 'a')
    _startup_log.write(f"\n--- Startup attempt ---\n")
except Exception:
    pass

def _log_startup_error(msg):
    """Log critical startup errors before main logging is ready."""
    if _startup_log:
        _startup_log.write(f"{msg}\n")
        _startup_log.flush()
    print(msg, file=sys.stderr)

# Ensure user directories exist
try:
    os.makedirs('user/logs', exist_ok=True)
    os.makedirs('user/history', exist_ok=True)
    os.makedirs('user/public/avatars', exist_ok=True)
except Exception as e:
    _log_startup_error(f"Failed to create user dirs: {e}")

# Copy default avatars if none exist in user dir
def _init_avatars():
    avatar_dir = 'user/public/avatars'
    static_dir = 'interfaces/web/static/users'

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

# Configure file handler with daily rotation
file_handler = TimedRotatingFileHandler(
    'user/logs/sapphire.log',
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
# These are cosmetic — the response already completed successfully
import sys
if sys.platform == 'win32':
    class _WinAsyncioFilter(logging.Filter):
        def filter(self, record):
            return 'ConnectionResetError' not in str(getattr(record, 'msg', ''))
    logging.getLogger('asyncio').addFilter(_WinAsyncioFilter())

# Only redirect stdout/stderr when running as systemd service
# if os.environ.get('SYSTEMD_EXEC_PID'):
#     sys.stdout = open(os.devnull, 'w')
#     sys.stderr = open(os.devnull, 'w')