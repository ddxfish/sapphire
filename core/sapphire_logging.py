import os
import sys
import logging
import shutil
from logging.handlers import TimedRotatingFileHandler

# Early stderr capture - ensures ANY errors get logged
_startup_log = None
try:
    os.makedirs('user/logs', exist_ok=True)
    _startup_log = open('user/logs/startup_errors.log', 'a')
    _startup_log.write(f"\n--- Startup attempt ---\n")
except:
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
    patterns = ['user.png', 'user.jpg', 'assistant.png', 'assistant.jpg']
    
    # Check if ANY avatar already exists
    has_avatar = any(os.path.exists(os.path.join(avatar_dir, p)) for p in patterns)
    if has_avatar:
        return
    
    # Copy defaults if static dir exists
    if not os.path.isdir(static_dir):
        return
    
    for f in os.listdir(static_dir):
        if f.lower().endswith(('.png', '.jpg', '.jpeg')):
            src = os.path.join(static_dir, f)
            dst = os.path.join(avatar_dir, f)
            try:
                shutil.copy2(src, dst)
            except Exception:
                pass

_init_avatars()  

# Configure file handler with daily rotation
file_handler = TimedRotatingFileHandler(
    'user/logs/sapphire.log',
    when='midnight',
    interval=1,
    backupCount=30
)
file_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))

# Console handler for terminal output
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))

# Configure root logger
root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)

# Remove any existing handlers
for handler in root_logger.handlers[:]:
    root_logger.removeHandler(handler)

# Add both handlers
root_logger.addHandler(file_handler)
root_logger.addHandler(console_handler)

# Quiet down Flask's werkzeug logger
logging.getLogger('werkzeug').setLevel(logging.WARNING)

# Only redirect stdout/stderr when running as systemd service
# if os.environ.get('SYSTEMD_EXEC_PID'):
#     sys.stdout = open(os.devnull, 'w')
#     sys.stderr = open(os.devnull, 'w')