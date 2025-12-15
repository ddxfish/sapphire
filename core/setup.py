# core/setup.py - Password, API keys, and platform config management
"""
Single source of truth for secrets and platform-appropriate config paths.

Config locations by platform:
- Windows: %APPDATA%/Sapphire/
- macOS: ~/Library/Application Support/Sapphire/
- Linux: ~/.config/sapphire/

Files stored:
- secret_key: bcrypt hash for auth
- socks_config: SOCKS5 proxy credentials
- claude_api_key: Anthropic API key
"""
import os
import sys
import logging
import shutil
from pathlib import Path

try:
    import bcrypt
except ImportError:
    bcrypt = None

logger = logging.getLogger(__name__)


def get_config_dir() -> Path:
    """
    Get platform-appropriate config directory for secrets.
    
    Returns:
        Path to config directory (created if needed on first access)
    """
    if sys.platform == 'win32':
        # Windows: %APPDATA%/Sapphire
        base = os.environ.get('APPDATA')
        if base:
            return Path(base) / 'Sapphire'
        return Path.home() / 'AppData' / 'Roaming' / 'Sapphire'
    elif sys.platform == 'darwin':
        # macOS: ~/Library/Application Support/Sapphire
        return Path.home() / 'Library' / 'Application Support' / 'Sapphire'
    else:
        # Linux/Unix: XDG Base Directory spec
        xdg_config = os.environ.get('XDG_CONFIG_HOME')
        if xdg_config:
            return Path(xdg_config) / 'sapphire'
        return Path.home() / '.config' / 'sapphire'


CONFIG_DIR = get_config_dir()
SECRET_KEY_FILE = CONFIG_DIR / 'secret_key'
SOCKS_CONFIG_FILE = CONFIG_DIR / 'socks_config'
CLAUDE_API_KEY_FILE = CONFIG_DIR / 'claude_api_key'


def ensure_config_directory() -> bool:
    """Create config directory if it doesn't exist."""
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        return True
    except Exception as e:
        logger.error(f"Failed to create config directory: {e}")
        return False


def get_password_hash() -> str | None:
    """
    Get stored bcrypt hash, or None if not set up.
    Returns None on any error (fail-secure).
    """
    try:
        if not SECRET_KEY_FILE.exists():
            return None
        
        hash_value = SECRET_KEY_FILE.read_text().strip()
        
        # Validate it looks like a bcrypt hash
        if not hash_value or len(hash_value) < 50:
            logger.error("Invalid hash format in secret_key file")
            return None
        
        if not hash_value.startswith('$2'):
            logger.error("Secret key file does not contain bcrypt hash")
            return None
        
        return hash_value
    except Exception as e:
        logger.error(f"Failed to read password hash: {e}")
        return None


def save_password_hash(password: str) -> str | None:
    """
    Hash password with bcrypt and save to file.
    Returns hash on success, None on failure.
    """
    if bcrypt is None:
        logger.error("bcrypt module not available")
        return None
    
    if not password or len(password) < 4:
        logger.error("Password too short")
        return None
    
    try:
        if not ensure_config_directory():
            return None
        
        # Generate bcrypt hash
        hash_bytes = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())
        hash_str = hash_bytes.decode('utf-8')
        
        # Write to file with restrictive permissions (Unix only)
        SECRET_KEY_FILE.write_text(hash_str)
        if sys.platform != 'win32':
            os.chmod(SECRET_KEY_FILE, 0o600)
        
        logger.info(f"Password hash saved to {SECRET_KEY_FILE}")
        return hash_str
    except Exception as e:
        logger.error(f"Failed to save password hash: {e}")
        return None


def verify_password(password: str, hash_str: str) -> bool:
    """
    Verify password against stored hash.
    Returns False on any error (fail-secure).
    """
    if bcrypt is None:
        logger.error("bcrypt module not available")
        return False
    
    if not password or not hash_str:
        return False
    
    try:
        return bcrypt.checkpw(password.encode('utf-8'), hash_str.encode('utf-8'))
    except Exception as e:
        logger.error(f"Password verification error: {e}")
        return False


def is_setup_complete() -> bool:
    """Check if initial setup has been completed."""
    return get_password_hash() is not None


def delete_password_hash() -> bool:
    """
    Delete the password hash file (for password reset scenarios).
    Returns True on success or if file doesn't exist.
    """
    try:
        if SECRET_KEY_FILE.exists():
            SECRET_KEY_FILE.unlink()
            logger.info("Password hash deleted")
        return True
    except Exception as e:
        logger.error(f"Failed to delete password hash: {e}")
        return False


def get_socks_credentials() -> tuple[str | None, str | None]:
    """
    Load SOCKS5 credentials with priority:
    1. Environment variables (SAPPHIRE_SOCKS_USERNAME, SAPPHIRE_SOCKS_PASSWORD)
    2. Config file: CONFIG_DIR/socks_config
    3. Project file: user/.socks_config (legacy/dev convenience)
    
    Returns (username, password) or (None, None) if not found.
    """
    # Try env vars first (production/deployment)
    username = os.environ.get('SAPPHIRE_SOCKS_USERNAME')
    password = os.environ.get('SAPPHIRE_SOCKS_PASSWORD')
    
    if username and password:
        logger.info("Using SOCKS credentials from environment variables")
        return username, password
    
    # Try platform config directory
    if SOCKS_CONFIG_FILE.exists():
        try:
            lines = SOCKS_CONFIG_FILE.read_text().splitlines()
            if len(lines) >= 2:
                username = lines[0].strip()
                password = lines[1].strip()
                if username and password:
                    logger.info(f"Using SOCKS credentials from {SOCKS_CONFIG_FILE}")
                    return username, password
        except Exception as e:
            logger.debug(f"Failed to read {SOCKS_CONFIG_FILE}: {e}")
    
    # Try project-local file (legacy/dev convenience)
    project_config = Path(__file__).parent.parent / 'user' / '.socks_config'
    if project_config.exists():
        try:
            lines = project_config.read_text().splitlines()
            if len(lines) >= 2:
                username = lines[0].strip()
                password = lines[1].strip()
                if username and password:
                    logger.info(f"Using SOCKS credentials from {project_config}")
                    return username, password
        except Exception as e:
            logger.debug(f"Failed to read {project_config}: {e}")
    
    return None, None


def get_claude_api_key() -> str | None:
    """
    Load Claude API key with priority:
    1. Environment variable (ANTHROPIC_API_KEY)
    2. Config file: CONFIG_DIR/claude_api_key
    
    Returns API key or None if not found.
    """
    # Try env var first (standard Anthropic pattern)
    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if api_key:
        logger.info("Using Claude API key from ANTHROPIC_API_KEY environment variable")
        return api_key
    
    # Try config file
    if CLAUDE_API_KEY_FILE.exists():
        try:
            api_key = CLAUDE_API_KEY_FILE.read_text().strip()
            if api_key:
                logger.info(f"Using Claude API key from {CLAUDE_API_KEY_FILE}")
                return api_key
        except Exception as e:
            logger.debug(f"Failed to read {CLAUDE_API_KEY_FILE}: {e}")
    
    return None


def ensure_wakeword_models() -> bool:
    """
    Ensure OpenWakeWord models are downloaded.
    OWW auto-downloads models on first use, but this pre-downloads them.
    Returns True if models are available, False on error.
    """
    try:
        import openwakeword
        from openwakeword.utils import download_models
        
        logger.info("Downloading OpenWakeWord models...")
        download_models()
        logger.info("OpenWakeWord models ready")
        return True
    except ImportError:
        logger.warning("OpenWakeWord not installed - skipping model download")
        return False
    except Exception as e:
        logger.error(f"Failed to download OpenWakeWord models: {e}")
        return False


def ensure_prompt_files() -> bool:
    """
    Bootstrap prompt templates from core to user/prompts/ if missing.
    Run once at startup. After this, only user/prompts/ is ever used.
    Returns True if all files available, False on error.
    """
    # Source: factory defaults shipped with app
    source_dir = Path(__file__).parent / "modules" / "system" / "prompts"
    # Target: user's working copies
    target_dir = Path(__file__).parent.parent / "user" / "prompts"
    
    files = [
        "prompt_monoliths.json",
        "prompt_pieces.json",
        "prompt_spices.json"
    ]
    
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        
        for filename in files:
            target = target_dir / filename
            if target.exists():
                continue
            
            source = source_dir / filename
            if not source.exists():
                logger.warning(f"Template missing: {source}")
                continue
            
            shutil.copy2(source, target)
            logger.info(f"Bootstrapped {filename} to user/prompts/")
        
        return True
    except Exception as e:
        logger.error(f"Failed to ensure prompt files: {e}")
        return False


def ensure_chat_defaults() -> bool:
    """
    Bootstrap chat_defaults.json from core to user/settings/ if missing.
    This sets the default prompt, voice, ability, etc. for new installs.
    Returns True if file available, False on error.
    """
    source = Path(__file__).parent / "modules" / "system" / "prompts" / "chat_defaults.json"
    target_dir = Path(__file__).parent.parent / "user" / "settings"
    target = target_dir / "chat_defaults.json"
    
    try:
        if target.exists():
            return True
        
        if not source.exists():
            logger.warning(f"Factory chat_defaults.json not found at {source}")
            return False
        
        target_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        logger.info(f"Bootstrapped chat_defaults.json to user/settings/")
        return True
    except Exception as e:
        logger.error(f"Failed to ensure chat_defaults: {e}")
        return False