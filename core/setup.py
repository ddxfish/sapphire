# core/setup.py - Password and API key management
"""
Single source of truth: ~/.config/sapphire/secret_key contains bcrypt hash.
This hash serves as:
1. Password verification (bcrypt.checkpw)
2. API key for internal requests
3. Flask session secret
"""
import os
import logging
import shutil
from pathlib import Path

try:
    import bcrypt
except ImportError:
    bcrypt = None

logger = logging.getLogger(__name__)

CONFIG_DIR = Path.home() / '.config' / 'sapphire'
SECRET_KEY_FILE = CONFIG_DIR / 'secret_key'


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
        
        # Write to file
        SECRET_KEY_FILE.write_text(hash_str)
        os.chmod(SECRET_KEY_FILE, 0o600)
        
        logger.info("Password hash saved successfully")
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
        "prompt_spices.json",
        "prompt_presets.json"
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