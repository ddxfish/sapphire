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
import json
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
    1. credentials.json (managed by credentials_manager)
    2. Environment variables (SAPPHIRE_SOCKS_USERNAME, SAPPHIRE_SOCKS_PASSWORD)
    3. Legacy: CONFIG_DIR/socks_config
    4. Legacy: user/.socks_config
    
    Returns (username, password) or (None, None) if not found.
    """
    # 1. Try credentials_manager first (avoids circular import by lazy loading)
    try:
        from core.credentials_manager import credentials
        username, password = credentials.get_socks_credentials()
        if username and password:
            logger.debug("Using SOCKS credentials from credentials.json")
            return username, password
    except ImportError:
        pass  # credentials_manager not available yet
    
    # 2. Try env vars (production/deployment)
    username = os.environ.get('SAPPHIRE_SOCKS_USERNAME')
    password = os.environ.get('SAPPHIRE_SOCKS_PASSWORD')
    
    if username and password:
        logger.info("Using SOCKS credentials from environment variables")
        return username, password
    
    # 3. Legacy: platform config directory file
    if SOCKS_CONFIG_FILE.exists():
        try:
            lines = SOCKS_CONFIG_FILE.read_text().splitlines()
            if len(lines) >= 2:
                username = _parse_legacy_line(lines[0])
                password = _parse_legacy_line(lines[1])
                if username and password:
                    logger.info(f"Using SOCKS credentials from {SOCKS_CONFIG_FILE}")
                    return username, password
        except Exception as e:
            logger.debug(f"Failed to read {SOCKS_CONFIG_FILE}: {e}")
    
    # 4. Legacy: project-local file
    project_config = Path(__file__).parent.parent / 'user' / '.socks_config'
    if project_config.exists():
        try:
            lines = project_config.read_text().splitlines()
            if len(lines) >= 2:
                username = _parse_legacy_line(lines[0])
                password = _parse_legacy_line(lines[1])
                if username and password:
                    logger.info(f"Using SOCKS credentials from {project_config}")
                    return username, password
        except Exception as e:
            logger.debug(f"Failed to read {project_config}: {e}")
    
    return None, None


def _parse_legacy_line(line: str) -> str:
    """Strip key= prefix if present from legacy config files."""
    line = line.strip()
    if '=' in line:
        return line.split('=', 1)[1].strip()
    return line


def get_claude_api_key() -> str | None:
    """
    Load Claude API key with priority:
    1. credentials.json (managed by credentials_manager)
    2. Environment variable (ANTHROPIC_API_KEY)
    3. Legacy: CONFIG_DIR/claude_api_key
    
    Returns API key or None if not found.
    """
    # 1. Try credentials_manager first
    try:
        from core.credentials_manager import credentials
        api_key = credentials.get_llm_api_key('claude')
        if api_key:
            logger.debug("Using Claude API key from credentials.json")
            return api_key
    except ImportError:
        pass
    
    # 2. Try env var (standard Anthropic pattern)
    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if api_key:
        logger.info("Using Claude API key from ANTHROPIC_API_KEY environment variable")
        return api_key
    
    # 3. Legacy: config file
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


def reset_prompt_files() -> bool:
    """
    Force-copy all prompt files from core → user (overwrite).
    Used for recovery when user prompts are corrupted or botched.
    Returns True on success, False on error.
    """
    source_dir = Path(__file__).parent / "modules" / "system" / "prompts"
    target_dir = Path(__file__).parent.parent / "user" / "prompts"
    
    files = [
        "prompt_monoliths.json",
        "prompt_pieces.json",
        "prompt_spices.json"
    ]
    
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        
        for filename in files:
            source = source_dir / filename
            target = target_dir / filename
            
            if not source.exists():
                logger.warning(f"Source template missing: {source}")
                continue
            
            shutil.copy2(source, target)
            logger.info(f"Reset {filename} to factory defaults")
        
        return True
    except Exception as e:
        logger.error(f"Failed to reset prompt files: {e}")
        return False


def reset_chat_defaults() -> bool:
    """
    Force-copy chat_defaults.json from core → user/settings (overwrite).
    Returns True on success, False on error.
    """
    source = Path(__file__).parent / "modules" / "system" / "prompts" / "chat_defaults.json"
    target_dir = Path(__file__).parent.parent / "user" / "settings"
    target = target_dir / "chat_defaults.json"
    
    try:
        if not source.exists():
            logger.warning(f"Factory chat_defaults.json not found at {source}")
            return False
        
        target_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        logger.info("Reset chat_defaults.json to factory defaults")
        return True
    except Exception as e:
        logger.error(f"Failed to reset chat_defaults: {e}")
        return False


def _deep_merge(base: dict, override: dict) -> dict:
    """
    Deep merge two dicts. Override wins on conflicts.
    Used for merging core prompts into user prompts.
    """
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def merge_prompt_files() -> dict:
    """
    Deep merge core prompts into user prompts.
    Core values overwrite user values at same path.
    New user keys are preserved.
    Returns dict with merge results per file.
    """
    source_dir = Path(__file__).parent / "modules" / "system" / "prompts"
    target_dir = Path(__file__).parent.parent / "user" / "prompts"
    
    files = [
        "prompt_monoliths.json",
        "prompt_pieces.json",
        "prompt_spices.json"
    ]
    
    results = {}
    
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        
        for filename in files:
            source = source_dir / filename
            target = target_dir / filename
            
            if not source.exists():
                results[filename] = {"status": "skipped", "reason": "source missing"}
                continue
            
            # Load core defaults
            with open(source, 'r', encoding='utf-8') as f:
                core_data = json.load(f)
            
            # Load user data (or empty dict if missing)
            if target.exists():
                with open(target, 'r', encoding='utf-8') as f:
                    user_data = json.load(f)
            else:
                user_data = {}
            
            # Deep merge: core overwrites, user additions preserved
            merged = _deep_merge(user_data, core_data)
            
            # Save merged result
            with open(target, 'w', encoding='utf-8') as f:
                json.dump(merged, f, indent=2)
            
            results[filename] = {"status": "merged", "keys": len(merged)}
            logger.info(f"Merged {filename} - {len(merged)} top-level keys")
        
        return results
    except Exception as e:
        logger.error(f"Failed to merge prompt files: {e}")
        return {"error": str(e)}